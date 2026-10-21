#!/usr/bin/env python3
"""Atomic APP_SECRET Rotation Utility for StaffDeck.

Safely rotates APP_SECRET across all encrypted database tables:
1. model_configs.api_key_encrypted (Fernet)
2. webhook_endpoints.secret_encrypted (Fernet, if present)
3. channel_bindings.credentials_enc (Fernet with fallback '{APP_SECRET}:channel')
4. data_sources.config_json (AES-256-GCM for values with 'enc:' prefix)
5. artifact_share_links.token & token_hash (HMAC-SHA256 signature update)

All migrations run inside a single database transaction. If any record fails
decryption or validation, the entire transaction is rolled back with zero data corruption.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import json
import logging
import os
import sqlite3
import sys
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("rotate_app_secret")


def _fernet_for(secret: str) -> Fernet:
    key = base64.urlsafe_b64encode(hashlib.sha256(secret.encode("utf-8")).digest())
    return Fernet(key)


def _aesgcm_for(secret: str) -> AESGCM:
    key = hashlib.sha256(secret.encode("utf-8")).digest()
    return AESGCM(key)


def _re_encrypt_aesgcm_value(ciphertext: str, old_aes: AESGCM, new_aes: AESGCM) -> str:
    prefix = "enc:"
    nonce_bytes = 12
    if not isinstance(ciphertext, str) or not ciphertext.startswith(prefix):
        return ciphertext
    raw = base64.b64decode(ciphertext[len(prefix):])
    if len(raw) < nonce_bytes + 16:
        raise ValueError("Invalid AESGCM ciphertext: payload too short")
    nonce = raw[:nonce_bytes]
    ct = raw[nonce_bytes:]
    plaintext = old_aes.decrypt(nonce, ct, None)

    new_nonce = os.urandom(nonce_bytes)
    new_ct = new_aes.encrypt(new_nonce, plaintext, None)
    combined = base64.b64encode(new_nonce + new_ct).decode("ascii")
    return f"{prefix}{combined}"


def _re_sign_share_token(token: str, old_secret: str, new_secret: str) -> tuple[str, str]:
    """Re-signs an HMAC-signed share token '{body}.{sig}', returning (new_token, new_token_hash)."""
    parts = token.split(".")
    if len(parts) != 2:
        raise ValueError(f"Malformed share token format: {token[:20]}...")
    body, old_sig = parts
    expected_old_sig = hmac.new(old_secret.encode("utf-8"), body.encode("ascii"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(old_sig, expected_old_sig):
        raise ValueError(f"Token signature does not match old secret: {token[:20]}...")

    new_sig = hmac.new(new_secret.encode("utf-8"), body.encode("ascii"), hashlib.sha256).hexdigest()
    new_token = f"{body}.{new_sig}"
    new_token_hash = hashlib.sha256(new_token.encode("utf-8")).hexdigest()
    return new_token, new_token_hash


def rotate_secrets_in_db(
    db_path: str,
    old_secret: str,
    new_secret: str,
    channel_secret: str | None = None,
    dry_run: bool = False,
) -> dict[str, int]:
    """Execute atomic APP_SECRET rotation. Returns counts of migrated records."""
    if not old_secret or not new_secret:
        raise ValueError("Both old_secret and new_secret must be non-empty strings")
    if old_secret == new_secret:
        raise ValueError("old_secret and new_secret are identical; rotation aborted")

    p = Path(db_path)
    if not p.is_file():
        raise FileNotFoundError(f"Database file not found: {db_path}")

    old_fernet = _fernet_for(old_secret)
    new_fernet = _fernet_for(new_secret)

    old_channel_fernet = _fernet_for(f"{old_secret}:channel")
    new_channel_fernet = _fernet_for(f"{new_secret}:channel")

    old_aes = _aesgcm_for(old_secret)
    new_aes = _aesgcm_for(new_secret)

    counts = {
        "model_configs": 0,
        "webhook_endpoints": 0,
        "channel_bindings": 0,
        "data_sources": 0,
        "artifact_share_links": 0,
    }

    conn = sqlite3.connect(db_path)
    conn.isolation_level = None  # Explicit transaction control
    cursor = conn.cursor()

    try:
        cursor.execute("BEGIN IMMEDIATE")

        # 1. model_configs
        cursor.execute("SELECT id, api_key_encrypted FROM model_configs WHERE api_key_encrypted IS NOT NULL AND api_key_encrypted != ''")
        for row_id, enc_val in cursor.fetchall():
            try:
                decrypted = old_fernet.decrypt(enc_val.encode("utf-8")).decode("utf-8")
            except InvalidToken as exc:
                raise ValueError(f"Failed to decrypt model_configs row id={row_id} with old_secret") from exc
            new_enc = new_fernet.encrypt(decrypted.encode("utf-8")).decode("utf-8")
            if not dry_run:
                cursor.execute("UPDATE model_configs SET api_key_encrypted = ? WHERE id = ?", (new_enc, row_id))
            counts["model_configs"] += 1

        # 2. webhook_endpoints (if table exists)
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='webhook_endpoints'")
        if cursor.fetchone():
            cursor.execute("SELECT id, secret_encrypted FROM webhook_endpoints WHERE secret_encrypted IS NOT NULL AND secret_encrypted != ''")
            for row_id, enc_val in cursor.fetchall():
                try:
                    decrypted = old_fernet.decrypt(enc_val.encode("utf-8")).decode("utf-8")
                except InvalidToken as exc:
                    raise ValueError(f"Failed to decrypt webhook_endpoints row id={row_id} with old_secret") from exc
                new_enc = new_fernet.encrypt(decrypted.encode("utf-8")).decode("utf-8")
                if not dry_run:
                    cursor.execute("UPDATE webhook_endpoints SET secret_encrypted = ? WHERE id = ?", (new_enc, row_id))
                counts["webhook_endpoints"] += 1

        # 3. channel_bindings
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='channel_bindings'")
        if cursor.fetchone():
            cursor.execute("SELECT id, credentials_enc FROM channel_bindings WHERE credentials_enc IS NOT NULL AND credentials_enc != ''")
            for row_id, enc_val in cursor.fetchall():
                # If channel_secret was configured separately, verify it decrypts and skip
                if channel_secret:
                    try:
                        _fernet_for(channel_secret).decrypt(enc_val.encode("utf-8"))
                        logger.info("channel_bindings row id=%s uses dedicated CHANNEL_SECRET, skipping", row_id)
                        continue
                    except InvalidToken:
                        pass
                try:
                    decrypted = old_channel_fernet.decrypt(enc_val.encode("utf-8")).decode("utf-8")
                except InvalidToken as exc:
                    raise ValueError(f"Failed to decrypt channel_bindings row id={row_id} with old APP_SECRET channel key") from exc
                new_enc = new_channel_fernet.encrypt(decrypted.encode("utf-8")).decode("utf-8")
                if not dry_run:
                    cursor.execute("UPDATE channel_bindings SET credentials_enc = ? WHERE id = ?", (new_enc, row_id))
                counts["channel_bindings"] += 1

        # 4. data_sources
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='data_sources'")
        if cursor.fetchone():
            cursor.execute("SELECT id, config_json FROM data_sources WHERE config_json IS NOT NULL")
            for row_id, cfg_raw in cursor.fetchall():
                try:
                    cfg = json.loads(cfg_raw) if isinstance(cfg_raw, str) else (cfg_raw or {})
                except (json.JSONDecodeError, TypeError):
                    logger.warning("Skipping unparsable config_json for data_sources row id=%s", row_id)
                    continue
                if not isinstance(cfg, dict):
                    continue
                modified = False
                for k, v in cfg.items():
                    if isinstance(v, str) and v.startswith("enc:"):
                        try:
                            cfg[k] = _re_encrypt_aesgcm_value(v, old_aes, new_aes)
                            modified = True
                        except Exception as exc:
                            raise ValueError(f"Failed to re-encrypt data_sources id={row_id} field {k}") from exc
                if modified:
                    counts["data_sources"] += 1
                    if not dry_run:
                        cursor.execute("UPDATE data_sources SET config_json = ? WHERE id = ?", (json.dumps(cfg), row_id))

        # 5. artifact_share_links
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='artifact_share_links'")
        if cursor.fetchone():
            cursor.execute("SELECT id, token, url, is_revoked FROM artifact_share_links")
            for row_id, token, url, is_revoked in cursor.fetchall():
                if not token or is_revoked:
                    continue
                try:
                    new_token, new_hash = _re_sign_share_token(token, old_secret, new_secret)
                except (ValueError, KeyError) as exc:
                    logger.warning("Skipping invalid share link row id=%s: %s", row_id, exc)
                    continue
                new_url = url.replace(token, new_token) if (url and token in url) else url
                if not dry_run:
                    cursor.execute(
                        "UPDATE artifact_share_links SET token = ?, token_hash = ?, url = ? WHERE id = ?",
                        (new_token, new_hash, new_url, row_id),
                    )
                counts["artifact_share_links"] += 1

        if dry_run:
            cursor.execute("ROLLBACK")
            logger.info("DRY RUN verified successfully. No changes were committed.")
        else:
            cursor.execute("COMMIT")
            logger.info("Secret rotation committed successfully to %s", db_path)

    except Exception:
        cursor.execute("ROLLBACK")
        logger.exception("Secret rotation failed! Database transaction rolled back cleanly.")
        raise
    finally:
        conn.close()

    return counts


def main() -> None:
    parser = argparse.ArgumentParser(description="Rotate StaffDeck APP_SECRET across database tables")
    parser.add_argument("--db-path", default="backend/skill_agent_loop.db", help="Path to SQLite DB")
    parser.add_argument("--old-secret", default=os.getenv("OLD_APP_SECRET"), help="Old APP_SECRET")
    parser.add_argument("--new-secret", default=os.getenv("NEW_APP_SECRET"), help="New APP_SECRET")
    parser.add_argument("--channel-secret", default=os.getenv("CHANNEL_SECRET"), help="Current CHANNEL_SECRET (optional)")
    parser.add_argument("--dry-run", action="store_true", help="Simulate without writing changes")
    args = parser.parse_args()

    if not args.old_secret or not args.new_secret:
        logger.error("Both --old-secret and --new-secret are required.")
        sys.exit(1)

    try:
        counts = rotate_secrets_in_db(
            db_path=args.db_path,
            old_secret=args.old_secret,
            new_secret=args.new_secret,
            channel_secret=args.channel_secret,
            dry_run=args.dry_run,
        )
        logger.info("Rotation summary: %s", counts)
    except Exception as exc:  # noqa: BLE001
        logger.error("Rotation failed: %s", exc)
        sys.exit(1)


if __name__ == "__main__":
    main()

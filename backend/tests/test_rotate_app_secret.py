from __future__ import annotations

import base64
import hashlib
import json
import os
import sqlite3
import sys
from pathlib import Path

import pytest
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

# Ensure scripts module is accessible
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from scripts.rotate_app_secret import rotate_secrets_in_db


@pytest.fixture
def temp_db(tmp_path: Path) -> str:
    db_file = tmp_path / "test_rotate.db"
    conn = sqlite3.connect(db_file)
    cur = conn.cursor()

    cur.execute("""
        CREATE TABLE model_configs (
            id TEXT PRIMARY KEY,
            api_key_encrypted TEXT
        )
    """)
    cur.execute("""
        CREATE TABLE channel_bindings (
            id TEXT PRIMARY KEY,
            credentials_enc TEXT
        )
    """)
    cur.execute("""
        CREATE TABLE data_sources (
            id TEXT PRIMARY KEY,
            config_json TEXT
        )
    """)
    cur.execute("""
        CREATE TABLE artifact_share_links (
            id TEXT PRIMARY KEY,
            token TEXT,
            token_hash TEXT,
            url TEXT,
            is_revoked BOOLEAN DEFAULT 0
        )
    """)
    conn.commit()
    conn.close()
    return str(db_file)


def _fernet(secret: str) -> Fernet:
    key = base64.urlsafe_b64encode(hashlib.sha256(secret.encode("utf-8")).digest())
    return Fernet(key)


def _aesgcm_encrypt(plaintext: str, secret: str) -> str:
    key = hashlib.sha256(secret.encode("utf-8")).digest()
    aesgcm = AESGCM(key)
    nonce = os.urandom(12)
    ct = aesgcm.encrypt(nonce, plaintext.encode("utf-8"), None)
    return "enc:" + base64.b64encode(nonce + ct).decode("ascii")


def test_rotate_app_secret_end_to_end(temp_db: str):
    old_secret = "test-old-secret-12345"
    new_secret = "test-new-secret-67890"

    old_f = _fernet(old_secret)
    old_chan_f = _fernet(f"{old_secret}:channel")

    conn = sqlite3.connect(temp_db)
    cur = conn.cursor()

    # Seed model config
    cur.execute("INSERT INTO model_configs VALUES (?, ?)", ("mc_1", old_f.encrypt(b"sk-test-model-key").decode()))

    # Seed channel binding
    cur.execute("INSERT INTO channel_bindings VALUES (?, ?)", ("cb_1", old_chan_f.encrypt(b"feishu-bot-secret").decode()))

    # Seed data source with AES-256-GCM password
    ds_config = {
        "host": "127.0.0.1",
        "port": 3306,
        "password": _aesgcm_encrypt("my-db-pass", old_secret),
    }
    cur.execute("INSERT INTO data_sources VALUES (?, ?)", ("ds_1", json.dumps(ds_config)))

    # Seed share link
    import hmac
    body = "eyJ0eXBlIjoiYXJ0aWZhY3Rfc2hhcmUiLCJ0ZW5hbnRfaWQiOiJ0MV90ZXN0In0"
    old_sig = hmac.new(old_secret.encode("utf-8"), body.encode("ascii"), hashlib.sha256).hexdigest()
    old_token = f"{body}.{old_sig}"
    old_url = f"http://127.0.0.1:5173/api/chat/artifacts/view/{old_token}"
    cur.execute(
        "INSERT INTO artifact_share_links VALUES (?, ?, ?, ?, ?)",
        ("link_1", old_token, hashlib.sha256(old_token.encode()).hexdigest(), old_url, False),
    )
    conn.commit()
    conn.close()

    # 1. Test dry-run: should report 1 for all, but DB should remain unchanged
    counts = rotate_secrets_in_db(temp_db, old_secret, new_secret, dry_run=True)
    assert counts["model_configs"] == 1
    assert counts["channel_bindings"] == 1
    assert counts["data_sources"] == 1
    assert counts["artifact_share_links"] == 1

    # Verify dry-run didn't change DB
    conn = sqlite3.connect(temp_db)
    cur = conn.cursor()
    val = cur.execute("SELECT api_key_encrypted FROM model_configs WHERE id = 'mc_1'").fetchone()[0]
    assert old_f.decrypt(val.encode()).decode() == "sk-test-model-key"
    conn.close()

    # 2. Test live rotation
    counts_live = rotate_secrets_in_db(temp_db, old_secret, new_secret, dry_run=False)
    assert counts_live["model_configs"] == 1
    assert counts_live["channel_bindings"] == 1
    assert counts_live["data_sources"] == 1
    assert counts_live["artifact_share_links"] == 1

    # 3. Verify new secret decrypts, old secret fails
    conn = sqlite3.connect(temp_db)
    cur = conn.cursor()

    # Check model config
    new_f = _fernet(new_secret)
    new_mc_val = cur.execute("SELECT api_key_encrypted FROM model_configs WHERE id = 'mc_1'").fetchone()[0]
    assert new_f.decrypt(new_mc_val.encode()).decode() == "sk-test-model-key"
    from cryptography.fernet import InvalidToken

    with pytest.raises(InvalidToken):
        old_f.decrypt(new_mc_val.encode())

    # Check channel binding
    new_chan_f = _fernet(f"{new_secret}:channel")
    new_cb_val = cur.execute("SELECT credentials_enc FROM channel_bindings WHERE id = 'cb_1'").fetchone()[0]
    assert new_chan_f.decrypt(new_cb_val.encode()).decode() == "feishu-bot-secret"

    # Check data source
    new_ds_raw = cur.execute("SELECT config_json FROM data_sources WHERE id = 'ds_1'").fetchone()[0]
    new_ds = json.loads(new_ds_raw)
    raw_enc = new_ds["password"]
    assert raw_enc.startswith("enc:")
    raw_b64 = base64.b64decode(raw_enc[4:])
    new_aes = AESGCM(hashlib.sha256(new_secret.encode()).digest())
    dec_pass = new_aes.decrypt(raw_b64[:12], raw_b64[12:], None).decode()
    assert dec_pass == "my-db-pass"

    # Check artifact share link
    new_link_row = cur.execute("SELECT token, url FROM artifact_share_links WHERE id = 'link_1'").fetchone()
    new_token = new_link_row[0]
    new_sig = hmac.new(new_secret.encode("utf-8"), body.encode("ascii"), hashlib.sha256).hexdigest()
    assert new_token == f"{body}.{new_sig}"
    assert new_token in new_link_row[1]
    conn.close()


def test_rotate_app_secret_rollback_on_failure(temp_db: str):
    old_secret = "correct-old-secret"
    wrong_old_secret = "wrong-old-secret"
    new_secret = "brand-new-secret"

    conn = sqlite3.connect(temp_db)
    cur = conn.cursor()
    # Insert a record encrypted with `correct-old-secret`
    old_f = _fernet(old_secret)
    cur.execute("INSERT INTO model_configs VALUES (?, ?)", ("mc_fail", old_f.encrypt(b"secret-value").decode()))
    conn.commit()
    conn.close()

    # Attempt rotation using `wrong_old_secret` -> Must raise and rollback cleanly
    with pytest.raises(ValueError, match="Failed to decrypt model_configs"):
        rotate_secrets_in_db(temp_db, wrong_old_secret, new_secret, dry_run=False)

    # Verify data was NOT corrupted
    conn = sqlite3.connect(temp_db)
    cur = conn.cursor()
    val = cur.execute("SELECT api_key_encrypted FROM model_configs WHERE id = 'mc_fail'").fetchone()[0]
    assert old_f.decrypt(val.encode()).decode() == "secret-value"
    conn.close()

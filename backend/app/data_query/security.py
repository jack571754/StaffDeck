"""AES-256-GCM encryption utilities for data query center.

Uses APP_SECRET-derived keys to encrypt sensitive configuration values.
Ciphertext format: ``enc:`` + base64(nonce + ciphertext + tag), with a 12-byte nonce.
"""

from __future__ import annotations

import base64
import hashlib
import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.config import get_settings

_PREFIX = "enc:"
_NONCE_BYTES = 12


def _derive_key(key: bytes | None = None) -> bytes:
    """Derive a 32-byte AES-256 key from APP_SECRET (SHA-256) or use the provided key."""
    if key is not None:
        if len(key) != 32:
            raise ValueError("Encryption key must be exactly 32 bytes")
        return key
    secret = get_settings().app_secret.encode("utf-8")
    return hashlib.sha256(secret).digest()


def encrypt_value(plaintext: str, key: bytes | None = None) -> str:
    """Encrypt a string and return it with the ``enc:`` prefix.

    Args:
        plaintext: The string to encrypt.
        key: Optional 32-byte key. If None, derived from APP_SECRET.

    Returns:
        ``enc:`` + base64(nonce + ciphertext+tag).
    """
    aesgcm = AESGCM(_derive_key(key))
    nonce = os.urandom(_NONCE_BYTES)
    ciphertext = aesgcm.encrypt(nonce, plaintext.encode("utf-8"), None)
    combined = base64.b64encode(nonce + ciphertext).decode("ascii")
    return f"{_PREFIX}{combined}"


def decrypt_value(ciphertext: str, key: bytes | None = None) -> str:
    """Decrypt a value produced by :func:`encrypt_value`.

    If *ciphertext* does not start with ``enc:`` it is returned as-is.

    Args:
        ciphertext: The encrypted string (or plaintext to pass through).
        key: Optional 32-byte key. If None, derived from APP_SECRET.

    Raises:
        ValueError: If the ciphertext has the ``enc:`` prefix but cannot be decrypted.
    """
    if not ciphertext.startswith(_PREFIX):
        return ciphertext
    raw = base64.b64decode(ciphertext[len(_PREFIX):])
    if len(raw) < _NONCE_BYTES + 16:  # nonce + minimum GCM tag
        raise ValueError("Invalid ciphertext: too short")
    nonce = raw[:_NONCE_BYTES]
    ct = raw[_NONCE_BYTES:]
    aesgcm = AESGCM(_derive_key(key))
    try:
        return aesgcm.decrypt(nonce, ct, None).decode("utf-8")
    except Exception as exc:
        raise ValueError("Decryption failed: invalid key or corrupted data") from exc


def encrypt_config(
    config: dict,
    sensitive_keys: list[str],
    key: bytes | None = None,
) -> dict:
    """Encrypt specified keys in a configuration dict in-place.

    Args:
        config: The configuration dictionary (shallow-copied before mutation).
        sensitive_keys: List of top-level keys whose string values should be encrypted.
        key: Optional 32-byte key. If None, derived from APP_SECRET.

    Returns:
        A new dict with sensitive values replaced by ``enc:`` ciphertexts.
        Non-sensitive keys and keys not present are left untouched.
    """
    result = dict(config)
    for k in sensitive_keys:
        if k in result and isinstance(result[k], str) and result[k]:
            result[k] = encrypt_value(result[k], key=key)
    return result


def decrypt_config(
    config: dict,
    sensitive_keys: list[str],
    key: bytes | None = None,
) -> dict:
    """Decrypt specified keys in a configuration dict.

    Keys whose values are not encrypted (no ``enc:`` prefix) are left as-is.

    Args:
        config: The configuration dictionary.
        sensitive_keys: List of top-level keys to decrypt.
        key: Optional 32-byte key. If None, derived from APP_SECRET.

    Returns:
        A new dict with sensitive values decrypted.
    """
    result = dict(config)
    for k in sensitive_keys:
        if k in result and isinstance(result[k], str):
            result[k] = decrypt_value(result[k], key=key)
    return result

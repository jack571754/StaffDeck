"""Tests for data_query.security encryption utilities."""

from __future__ import annotations

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.data_query.security import (
    decrypt_config,
    decrypt_value,
    encrypt_config,
    encrypt_value,
)

# A fixed 32-byte test key so tests are deterministic and don't depend on APP_SECRET
_TEST_KEY = b"\x00" * 32
_OTHER_KEY = b"\x01" * 32


# ---------------------------------------------------------------------------
# encrypt_value / decrypt_value round-trip
# ---------------------------------------------------------------------------


def test_encrypt_decrypt_roundtrip():
    plaintext = "hello-world-123"
    ciphertext = encrypt_value(plaintext, key=_TEST_KEY)
    assert ciphertext.startswith("enc:")
    assert ciphertext != plaintext
    decrypted = decrypt_value(ciphertext, key=_TEST_KEY)
    assert decrypted == plaintext


def test_different_nonce_produces_different_ciphertext():
    """Encrypting the same value twice must yield different ciphertexts (random nonce)."""
    plaintext = "same-secret"
    ct1 = encrypt_value(plaintext, key=_TEST_KEY)
    ct2 = encrypt_value(plaintext, key=_TEST_KEY)
    assert ct1 != ct2
    # Both decrypt to the same plaintext
    assert decrypt_value(ct1, key=_TEST_KEY) == plaintext
    assert decrypt_value(ct2, key=_TEST_KEY) == plaintext


def test_wrong_key_decrypt_fails():
    """Decrypting with a different key must raise ValueError."""
    ciphertext = encrypt_value("secret-data", key=_TEST_KEY)
    with pytest.raises(ValueError):
        decrypt_value(ciphertext, key=_OTHER_KEY)


def test_non_encrypted_value_passthrough():
    """Values without the enc: prefix are returned unchanged."""
    assert decrypt_value("plain-text-value", key=_TEST_KEY) == "plain-text-value"
    assert decrypt_value("", key=_TEST_KEY) == ""
    assert decrypt_value("some-other-prefix:abc", key=_TEST_KEY) == "some-other-prefix:abc"


# ---------------------------------------------------------------------------
# encrypt_config / decrypt_config
# ---------------------------------------------------------------------------


def test_config_encrypt_decrypt_sensitive_keys():
    """Only sensitive keys are encrypted; others pass through unchanged."""
    config = {
        "host": "db.example.com",
        "port": 5432,
        "username": "admin",
        "password": "supersecret",
        "api_key": "sk-12345",
        "ssl": True,
    }
    sensitive = ["password", "api_key"]
    encrypted = encrypt_config(config, sensitive, key=_TEST_KEY)

    # Non-sensitive values unchanged
    assert encrypted["host"] == "db.example.com"
    assert encrypted["port"] == 5432
    assert encrypted["username"] == "admin"
    assert encrypted["ssl"] is True

    # Sensitive values are encrypted with enc: prefix
    assert encrypted["password"].startswith("enc:")
    assert encrypted["api_key"].startswith("enc:")
    assert encrypted["password"] != "supersecret"
    assert encrypted["api_key"] != "sk-12345"

    # Decrypt restores original
    decrypted = decrypt_config(encrypted, sensitive, key=_TEST_KEY)
    assert decrypted["password"] == "supersecret"
    assert decrypted["api_key"] == "sk-12345"
    assert decrypted["host"] == "db.example.com"
    assert decrypted["port"] == 5432


def test_config_missing_sensitive_keys_ignored():
    """Keys listed in sensitive but absent from config are silently skipped."""
    config = {"host": "localhost"}
    encrypted = encrypt_config(config, ["password", "api_key"], key=_TEST_KEY)
    assert encrypted == {"host": "localhost"}
    decrypted = decrypt_config(encrypted, ["password"], key=_TEST_KEY)
    assert decrypted == {"host": "localhost"}


# ---------------------------------------------------------------------------
# Model registration / table creation smoke test
# ---------------------------------------------------------------------------


def test_data_query_models_register_and_create():
    """DataSource and QueryTemplate tables can be created via SQLModel.metadata."""
    from app.data_query.models import DataSource, QueryTemplate

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)

    with Session(engine) as session:

        ds = DataSource(
            tenant_id="t_test",
            name="test-db",
            type="postgres",
            config_json={"host": "localhost", "password": "enc:test"},
        )
        session.add(ds)
        session.commit()
        session.refresh(ds)
        assert ds.id.startswith("ds_")
        assert ds.name == "test-db"
        assert ds.config_json["host"] == "localhost"
        assert ds.read_only is True

        qt = QueryTemplate(
            tenant_id="t_test",
            data_source_id=ds.id,
            name="list-users",
            query_type="sql",
            query_content="SELECT * FROM users WHERE status = :status",
            params_json=[{"name": "status", "type": "string", "default": "active"}],
        )
        session.add(qt)
        session.commit()
        session.refresh(qt)
        assert qt.id.startswith("qt_")
        assert qt.data_source_id == ds.id
        assert qt.query_content.startswith("SELECT")
        assert qt.status == "draft"
        assert qt.cache_ttl == 300
        assert qt.timeout_seconds == 30
        assert qt.max_rows == 1000


# ---------------------------------------------------------------------------
# Read schema tests
# ---------------------------------------------------------------------------


def test_datasource_read_excludes_config_json():
    """DataSourceRead schema must not contain config_json to avoid secret leaks."""
    from app.data_query.models import DataSourceRead

    field_names = set(DataSourceRead.model_fields.keys())
    assert "config_json" not in field_names
    assert "id" in field_names
    assert "name" in field_names
    assert "type" in field_names
    assert "read_only" in field_names


# ---------------------------------------------------------------------------
# Key derivation edge cases
# ---------------------------------------------------------------------------


def test_invalid_key_length_raises():
    """Passing a key that is not exactly 32 bytes raises ValueError."""
    with pytest.raises(ValueError, match="32 bytes"):
        encrypt_value("test", key=b"too-short")


def test_encrypted_empty_string():
    """Empty string encryption round-trips."""
    ct = encrypt_value("", key=_TEST_KEY)
    assert ct.startswith("enc:")
    assert decrypt_value(ct, key=_TEST_KEY) == ""


def test_unicode_values():
    """Unicode values round-trip correctly."""
    plaintext = "密码测试 🔐 数据查询中心"
    ct = encrypt_value(plaintext, key=_TEST_KEY)
    assert decrypt_value(ct, key=_TEST_KEY) == plaintext

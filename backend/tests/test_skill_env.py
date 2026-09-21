from __future__ import annotations

from pathlib import Path

import pytest

from app import paths
from app.config import get_settings
from app.security import skill_env


@pytest.fixture(autouse=True)
def _reset_settings_cache(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("GENERAL_SKILL_ENV_PASSTHROUGH", raising=False)
    monkeypatch.delenv("ULTRARAG_DOTENV", raising=False)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_skill_secret_environment_reads_allowlisted_os_environ(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GENERAL_SKILL_ENV_PASSTHROUGH", "A_KEY,B_KEY")
    get_settings.cache_clear()
    monkeypatch.setenv("A_KEY", "v")

    assert skill_env.skill_secret_environment() == {"A_KEY": "v"}


def test_skill_secret_environment_fills_missing_keys_from_dotenv(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GENERAL_SKILL_ENV_PASSTHROUGH", "TEST_WEBHOOK")
    get_settings.cache_clear()
    (tmp_path / ".env").write_text(
        'TEST_WEBHOOK="https://example.com/hook"\nOTHER=ignored\n', encoding="utf-8"
    )
    monkeypatch.chdir(tmp_path)

    assert skill_env.skill_secret_environment() == {
        "TEST_WEBHOOK": "https://example.com/hook"
    }


def test_skill_secret_environment_os_environ_wins_over_dotenv(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GENERAL_SKILL_ENV_PASSTHROUGH", "TEST_WEBHOOK")
    get_settings.cache_clear()
    (tmp_path / ".env").write_text('TEST_WEBHOOK="from-dotenv"\n', encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("TEST_WEBHOOK", "from-environ")

    assert skill_env.skill_secret_environment() == {"TEST_WEBHOOK": "from-environ"}


def test_skill_secret_environment_ignores_non_allowlisted_keys(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GENERAL_SKILL_ENV_PASSTHROUGH", "TEST_WEBHOOK")
    get_settings.cache_clear()
    (tmp_path / ".env").write_text(
        'TEST_WEBHOOK="https://example.com/hook"\nAPP_SECRET="leak-me"\n', encoding="utf-8"
    )
    monkeypatch.chdir(tmp_path)

    resolved = skill_env.skill_secret_environment()
    assert resolved == {"TEST_WEBHOOK": "https://example.com/hook"}
    assert "APP_SECRET" not in resolved


def test_skill_secret_environment_empty_allowlist_returns_empty(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    get_settings.cache_clear()
    (tmp_path / ".env").write_text('TEST_WEBHOOK="https://example.com/hook"\n', encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    assert skill_env.skill_secret_environment() == {}


def test_dotenv_path_falls_back_to_backend_dir(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)

    assert skill_env._dotenv_path() == paths.app_root() / ".env"

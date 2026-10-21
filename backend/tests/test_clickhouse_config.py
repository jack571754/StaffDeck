"""Unit tests for the sales dashboard ClickHouse endpoint configuration."""

from types import SimpleNamespace

from app.reporting import clickhouse_config


def _settings(url: str = "", user: str = "", password: str = "") -> SimpleNamespace:
    return SimpleNamespace(
        sales_dashboard_clickhouse_url=url,
        sales_dashboard_clickhouse_user=user,
        sales_dashboard_clickhouse_password=password,
    )


def test_unconfigured_url_returns_none(monkeypatch):
    monkeypatch.setattr(clickhouse_config, "get_settings", lambda: _settings())
    assert clickhouse_config.clickhouse_query_url() is None


def test_url_without_credentials_is_returned_as_is(monkeypatch):
    monkeypatch.setattr(
        clickhouse_config,
        "get_settings",
        lambda: _settings(url="http://127.0.0.1:8123/"),
    )
    assert clickhouse_config.clickhouse_query_url() == "http://127.0.0.1:8123/"


def test_credentials_are_appended_and_url_encoded(monkeypatch):
    monkeypatch.setattr(
        clickhouse_config,
        "get_settings",
        lambda: _settings(
            url="http://127.0.0.1:8123/",
            user="dash board",
            password="p@ss&word",
        ),
    )
    assert clickhouse_config.clickhouse_query_url() == (
        "http://127.0.0.1:8123/?user=dash%20board&password=p%40ss%26word"
    )


def test_existing_query_string_uses_ampersand(monkeypatch):
    monkeypatch.setattr(
        clickhouse_config,
        "get_settings",
        lambda: _settings(url="http://127.0.0.1:8123/?database=sales", user="dash"),
    )
    assert clickhouse_config.clickhouse_query_url() == (
        "http://127.0.0.1:8123/?database=sales&user=dash"
    )

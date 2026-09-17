"""Tests for data query connectors (MySQL + HTTP API)."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from app.data_query.connectors import (
    BaseConnector,
    HttpApiConnector,
    MySQLConnector,
    QueryResult,
    SQLNotAllowedError,
    get_connector,
)
from app.data_query.connectors.mysql_connector import (
    _convert_params,
    _is_sql_allowed,
)

# -- fixtures ---------------------------------------------------------------


def _make_data_source(
    type: str = "mysql",
    config_json: dict | None = None,
    read_only: bool = True,
):
    """Build a lightweight mock data source object."""
    return SimpleNamespace(
        type=type,
        config_json=config_json or {},
        read_only=read_only,
    )


# -- QueryResult -----------------------------------------------------------


class TestQueryResult:
    def test_to_list_of_dicts_returns_rows(self):
        result = QueryResult(
            columns=["a", "b"],
            rows=[{"a": 1, "b": 2}, {"a": 3, "b": 4}],
        )
        assert result.to_list_of_dicts() == [{"a": 1, "b": 2}, {"a": 3, "b": 4}]

    def test_empty_result(self):
        result = QueryResult()
        assert result.columns == []
        assert result.rows == []
        assert result.row_count == 0
        assert result.to_list_of_dicts() == []

    def test_post_init_sets_row_count_from_rows(self):
        result = QueryResult(rows=[{"x": 1}, {"x": 2}])
        assert result.row_count == 2

    def test_explicit_row_count_preserved(self):
        result = QueryResult(rows=[{"x": 1}], row_count=100)
        assert result.row_count == 100


# -- SQL whitelist ---------------------------------------------------------


class TestSQLWhitelist:
    @pytest.mark.parametrize(
        "sql",
        [
            "SELECT * FROM users",
            "  SELECT id, name FROM users WHERE id = 1",
            "select * from users",
            "WITH cte AS (SELECT 1) SELECT * FROM cte",
            "with cte AS (SELECT 1) SELECT * FROM cte",
            "WITH ranked AS (SELECT ROW_NUMBER() OVER () AS rn FROM t) SELECT * FROM ranked",
        ],
    )
    def test_allowed_sql(self, sql):
        assert _is_sql_allowed(sql) is True

    @pytest.mark.parametrize(
        "sql",
        [
            "INSERT INTO users (name) VALUES ('bob')",
            "UPDATE users SET name = 'bob' WHERE id = 1",
            "DELETE FROM users WHERE id = 1",
            "DROP TABLE users",
            "ALTER TABLE users ADD COLUMN email VARCHAR(255)",
            "CREATE TABLE users (id INT)",
            "TRUNCATE TABLE users",
            "REPLACE INTO users (id, name) VALUES (1, 'bob')",
        ],
    )
    def test_rejected_sql(self, sql):
        assert _is_sql_allowed(sql) is False

    def test_block_comment_bypass_rejected(self):
        sql = "/* SELECT */ DROP TABLE users"
        assert _is_sql_allowed(sql) is False

    def test_line_comment_bypass_rejected(self):
        sql = "-- SELECT\nDROP TABLE users"
        assert _is_sql_allowed(sql) is False


# -- param conversion ------------------------------------------------------


class TestConvertParams:
    def test_single_param(self):
        query, names = _convert_params("SELECT * FROM users WHERE id = :id")
        assert query == "SELECT * FROM users WHERE id = %(id)s"
        assert names == ["id"]

    def test_multiple_params(self):
        query, names = _convert_params(
            "SELECT * FROM users WHERE name = :name AND age > :age"
        )
        assert query == (
            "SELECT * FROM users WHERE name = %(name)s AND age > %(age)s"
        )
        assert names == ["name", "age"]

    def test_no_params(self):
        query, names = _convert_params("SELECT 1")
        assert query == "SELECT 1"
        assert names == []

    def test_repeated_param(self):
        query, names = _convert_params(
            "SELECT :x AS a, :x AS b"
        )
        assert query == "SELECT %(x)s AS a, %(x)s AS b"
        assert names == ["x", "x"]


# -- MySQL connector -------------------------------------------------------


class TestMySQLConnector:
    def _make_conn(self, **cfg_overrides):
        cfg = {
            "host": "127.0.0.1",
            "port": 3306,
            "database": "testdb",
            "username": "user",
            "password": "secret",
        }
        cfg.update(cfg_overrides)
        ds = _make_data_source(type="mysql", config_json=cfg)
        return MySQLConnector(ds)

    def test_execute_with_params_mocked(self):
        """Execute a parameterized SELECT via a fully mocked pymysql."""
        connector = self._make_conn()

        mock_cursor = MagicMock()
        mock_cursor.description = [("id",), ("name",)]
        mock_cursor.fetchmany.return_value = [
            {"id": 1, "name": "alice"},
            {"id": 2, "name": "bob"},
        ]

        mock_conn = MagicMock()
        mock_conn.cursor.return_value.__enter__.return_value = mock_cursor

        mock_pymysql = MagicMock()
        mock_pymysql.connect.return_value = mock_conn
        mock_pymysql.cursors.DictCursor = MagicMock()

        with patch.dict("sys.modules", {"pymysql": mock_pymysql}):
            result = connector.execute(
                "SELECT id, name FROM users WHERE age > :min_age",
                {"min_age": 18},
                max_rows=100,
            )

        assert result.columns == ["id", "name"]
        assert result.row_count == 2
        assert result.rows[0]["name"] == "alice"

        # Verify that the :param was converted to pymysql %(name)s style
        called_query = mock_cursor.execute.call_args[0][0]
        called_params = mock_cursor.execute.call_args[0][1]
        assert "%(min_age)s" in called_query
        assert called_params == {"min_age": 18}

    def test_execute_rejects_drop_in_read_only(self):
        connector = self._make_conn()
        with pytest.raises(SQLNotAllowedError):
            connector.execute("DROP TABLE users", {})

    def test_execute_rejects_insert_in_read_only(self):
        connector = self._make_conn()
        with pytest.raises(SQLNotAllowedError):
            connector.execute(
                "INSERT INTO users (name) VALUES (:name)", {"name": "x"}
            )

    def test_execute_allows_cte(self):
        """WITH...SELECT (CTE) must pass the whitelist."""
        connector = self._make_conn()

        mock_cursor = MagicMock()
        mock_cursor.description = [("n",)]
        mock_cursor.fetchmany.return_value = [{"n": 1}]

        mock_conn = MagicMock()
        mock_conn.cursor.return_value.__enter__.return_value = mock_cursor

        mock_pymysql = MagicMock()
        mock_pymysql.connect.return_value = mock_conn
        mock_pymysql.cursors.DictCursor = MagicMock()

        with patch.dict("sys.modules", {"pymysql": mock_pymysql}):
            result = connector.execute(
                "WITH cte AS (SELECT 1 AS n) SELECT * FROM cte",
                {},
            )

        assert result.row_count == 1
        assert result.columns == ["n"]

    def test_close_is_idempotent(self):
        connector = self._make_conn()
        # Calling close on an unopened connector should not raise.
        connector.close()
        connector.close()

    def test_test_connection_mocked(self):
        connector = self._make_conn()
        mock_conn = MagicMock()
        mock_pymysql = MagicMock()
        mock_pymysql.connect.return_value = mock_conn
        with patch.dict("sys.modules", {"pymysql": mock_pymysql}):
            assert connector.test_connection() is True
            # test_connection closes after ping
            mock_conn.ping.assert_called_once()
            mock_conn.close.assert_called_once()


# -- HTTP API connector ----------------------------------------------------


class TestHttpApiConnector:
    def _make_conn(self, **cfg_overrides):
        cfg = {
            "base_url": "https://api.example.com",
            "headers": {"X-API-Key": "test-key"},
            "timeout": 10,
        }
        cfg.update(cfg_overrides)
        ds = _make_data_source(type="http_api", config_json=cfg)
        return HttpApiConnector(ds)

    def test_execute_list_response(self):
        connector = self._make_conn()
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = [
            {"id": 1, "name": "alice"},
            {"id": 2, "name": "bob"},
        ]
        mock_response.raise_for_status.return_value = None

        mock_client = MagicMock()
        mock_client.get.return_value = mock_response

        with patch("httpx.Client", return_value=mock_client):
            result = connector.execute("/api/users?status=:status", {"status": "active"})

        assert result.row_count == 2
        assert result.columns == ["id", "name"]
        assert result.rows[0]["name"] == "alice"

        # Verify the URL and query params
        call_args = mock_client.get.call_args
        url = call_args[0][0]
        assert "/api/users" in url
        assert "status=active" in url

    def test_execute_dict_response_single_row(self):
        connector = self._make_conn()
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"id": 1, "name": "alice"}
        mock_response.raise_for_status.return_value = None

        mock_client = MagicMock()
        mock_client.get.return_value = mock_response

        with patch("httpx.Client", return_value=mock_client):
            result = connector.execute("/api/users/1", {})

        assert result.row_count == 1
        assert result.rows[0]["id"] == 1

    def test_execute_scalar_response_wrapped(self):
        connector = self._make_conn()
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = 42
        mock_response.raise_for_status.return_value = None

        mock_client = MagicMock()
        mock_client.get.return_value = mock_response

        with patch("httpx.Client", return_value=mock_client):
            result = connector.execute("/api/count", {})

        assert result.row_count == 1
        assert result.columns == ["value"]
        assert result.rows[0]["value"] == 42

    def test_execute_wrapped_data_key(self):
        """Responses with {"data": [...]} should unwrap the list."""
        connector = self._make_conn()
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "code": 0,
            "data": [{"id": 1, "name": "alice"}],
        }
        mock_response.raise_for_status.return_value = None

        mock_client = MagicMock()
        mock_client.get.return_value = mock_response

        with patch("httpx.Client", return_value=mock_client):
            result = connector.execute("/api/list", {})

        assert result.row_count == 1
        assert result.rows[0]["name"] == "alice"

    def test_execute_path_param_substitution(self):
        connector = self._make_conn()
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"id": 42}
        mock_response.raise_for_status.return_value = None

        mock_client = MagicMock()
        mock_client.get.return_value = mock_response

        with patch("httpx.Client", return_value=mock_client):
            connector.execute("/api/users/:user_id/orders", {"user_id": "U123"})

        call_args = mock_client.get.call_args
        url = call_args[0][0]
        assert "/api/users/U123/orders" in url

    def test_test_connection_success(self):
        connector = self._make_conn(health_url="/health")
        mock_response = MagicMock()
        mock_response.status_code = 200

        mock_client = MagicMock()
        mock_client.get.return_value = mock_response

        with patch("httpx.Client", return_value=mock_client):
            assert connector.test_connection() is True

    def test_test_connection_failure(self):
        connector = self._make_conn()
        mock_client = MagicMock()
        mock_client.get.side_effect = ConnectionError("nope")

        with patch("httpx.Client", return_value=mock_client):
            assert connector.test_connection() is False

    def test_max_rows_truncates_list(self):
        connector = self._make_conn()
        items = [{"id": i} for i in range(50)]
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = items
        mock_response.raise_for_status.return_value = None

        mock_client = MagicMock()
        mock_client.get.return_value = mock_response

        with patch("httpx.Client", return_value=mock_client):
            result = connector.execute("/api/items", {}, max_rows=10)

        assert result.row_count == 10


# -- factory ----------------------------------------------------------------


class TestGetConnector:
    def test_mysql_returns_mysql_connector(self):
        ds = _make_data_source(type="mysql")
        connector = get_connector(ds)
        assert isinstance(connector, MySQLConnector)

    def test_http_api_returns_http_connector(self):
        ds = _make_data_source(type="http_api")
        connector = get_connector(ds)
        assert isinstance(connector, HttpApiConnector)

    def test_unknown_type_raises_value_error(self):
        ds = _make_data_source(type="postgres")
        with pytest.raises(ValueError, match="Unsupported data source type"):
            get_connector(ds)

    def test_missing_type_raises_value_error(self):
        ds = SimpleNamespace()  # no type
        with pytest.raises(ValueError, match="no 'type'"):
            get_connector(ds)

    def test_all_connectors_inherit_base(self):
        for stype in ("mysql", "http_api"):
            ds = _make_data_source(type=stype)
            connector = get_connector(ds)
            assert isinstance(connector, BaseConnector)

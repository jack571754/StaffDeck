"""Tests for the query execution engine (data_query.executor)."""

from __future__ import annotations

import time
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from sqlmodel import Session

from app.data_query.connectors import QueryResult
from app.data_query.executor import (
    QueryExecutor,
    _cache,
    _make_cache_key,
    _QueryCache,
    _validate_params,
)
from app.data_query.models import DataSource, QueryTemplate

# ---------------------------------------------------------------------------
# _validate_params tests
# ---------------------------------------------------------------------------


class TestValidateParams:
    """Tests for :func:`_validate_params`."""

    def test_required_missing_raises(self) -> None:
        """Missing required parameter raises ValueError."""
        params_def = [{"name": "id", "type": "int", "required": True}]
        with pytest.raises(ValueError, match="Missing required parameter: 'id'"):
            _validate_params({}, params_def)

    def test_default_value_applied(self) -> None:
        """Parameter with default gets filled in when missing."""
        params_def = [
            {"name": "limit", "type": "int", "required": False, "default": 10}
        ]
        result = _validate_params({}, params_def)
        assert result["limit"] == 10

    def test_enum_validation_fails(self) -> None:
        """Value not in enum list raises ValueError."""
        params_def = [
            {
                "name": "status",
                "type": "string",
                "required": True,
                "enum": ["active", "inactive"],
            }
        ]
        with pytest.raises(ValueError, match="is not in allowed values"):
            _validate_params({"status": "pending"}, params_def)

    def test_type_conversion_int(self) -> None:
        """Integer type coercion works for string input."""
        params_def = [{"name": "age", "type": "int", "required": True}]
        result = _validate_params({"age": "42"}, params_def)
        assert result["age"] == 42
        assert isinstance(result["age"], int)

    def test_type_conversion_float(self) -> None:
        """Float type coercion works for string input."""
        params_def = [{"name": "price", "type": "float", "required": True}]
        result = _validate_params({"price": "12.5"}, params_def)
        assert result["price"] == 12.5
        assert isinstance(result["price"], float)

    def test_type_conversion_bool_true_strings(self) -> None:
        """Boolean coercion for various truthy string values."""
        params_def = [{"name": "flag", "type": "bool", "required": True}]
        for val in ["true", "True", "1", "yes", "y", "on"]:
            result = _validate_params({"flag": val}, params_def)
            assert result["flag"] is True, f"expected True for {val!r}"

    def test_type_conversion_bool_false_strings(self) -> None:
        """Boolean coercion for various falsy string values."""
        params_def = [{"name": "flag", "type": "bool", "required": True}]
        for val in ["false", "False", "0", "no", "n", "off"]:
            result = _validate_params({"flag": val}, params_def)
            assert result["flag"] is False, f"expected False for {val!r}"

    def test_type_conversion_bool_invalid_string(self) -> None:
        """Invalid boolean string raises ValueError."""
        params_def = [{"name": "flag", "type": "bool", "required": True}]
        with pytest.raises(ValueError, match="must be a boolean"):
            _validate_params({"flag": "maybe"}, params_def)

    def test_type_conversion_date_valid(self) -> None:
        """Valid YYYY-MM-DD date passes validation."""
        params_def = [{"name": "date", "type": "date", "required": True}]
        result = _validate_params({"date": "2025-01-15"}, params_def)
        assert result["date"] == "2025-01-15"

    def test_type_conversion_date_invalid_format(self) -> None:
        """Invalid date format raises ValueError."""
        params_def = [{"name": "date", "type": "date", "required": True}]
        with pytest.raises(ValueError, match="YYYY-MM-DD"):
            _validate_params({"date": "01/15/2025"}, params_def)

    def test_type_conversion_date_invalid_value(self) -> None:
        """Invalid date value (e.g. month 13) raises ValueError."""
        params_def = [{"name": "date", "type": "date", "required": True}]
        with pytest.raises(ValueError, match="invalid date value"):
            _validate_params({"date": "2025-13-01"}, params_def)

    def test_string_passthrough(self) -> None:
        """String parameters are passed through unchanged."""
        params_def = [{"name": "name", "type": "string", "required": True}]
        result = _validate_params({"name": "hello"}, params_def)
        assert result["name"] == "hello"

    def test_int_coercion_fails_for_non_numeric(self) -> None:
        """Non-numeric string for int parameter raises ValueError."""
        params_def = [{"name": "count", "type": "int", "required": True}]
        with pytest.raises(ValueError, match="must be an integer"):
            _validate_params({"count": "abc"}, params_def)

    def test_optional_no_default_skipped(self) -> None:
        """Optional param without default is omitted from result when missing."""
        params_def = [
            {"name": "name", "type": "string", "required": False},
            {"name": "required_one", "type": "string", "required": True},
        ]
        result = _validate_params({"required_one": "yes"}, params_def)
        assert "name" not in result
        assert result["required_one"] == "yes"


# ---------------------------------------------------------------------------
# _QueryCache tests
# ---------------------------------------------------------------------------


class TestQueryCache:
    """Tests for :class:`_QueryCache`."""

    def test_set_and_get(self) -> None:
        """Set then get returns the same value."""
        cache = _QueryCache()
        value = QueryResult(columns=["a"], rows=[{"a": 1}], row_count=1)
        cache.set("key1", value, ttl_seconds=60)
        got = cache.get("key1")
        assert got is not None
        assert got.columns == ["a"]
        assert got.rows == [{"a": 1}]

    def test_get_missing_returns_none(self) -> None:
        """Getting a missing key returns None."""
        cache = _QueryCache()
        assert cache.get("nope") is None

    def test_expired_entry_returns_none(self) -> None:
        """Expired entries are not returned (and evicted)."""
        cache = _QueryCache()
        value = QueryResult(columns=["a"], rows=[{"a": 1}])
        cache.set("key1", value, ttl_seconds=0)  # expires immediately
        # Small sleep to ensure time has advanced
        time.sleep(0.01)
        assert cache.get("key1") is None
        # Verify lazy eviction
        assert "key1" not in cache._store

    def test_clear(self) -> None:
        """Clear removes all entries."""
        cache = _QueryCache()
        cache.set("k1", QueryResult(), ttl_seconds=60)
        cache.set("k2", QueryResult(), ttl_seconds=60)
        cache.clear()
        assert cache.get("k1") is None
        assert cache.get("k2") is None


class TestMakeCacheKey:
    """Tests for :func:`_make_cache_key`."""

    def test_deterministic(self) -> None:
        """Same inputs always produce the same key."""
        k1 = _make_cache_key("tpl-1", {"a": 1, "b": 2})
        k2 = _make_cache_key("tpl-1", {"b": 2, "a": 1})
        assert k1 == k2

    def test_different_params_different_keys(self) -> None:
        """Different parameter values produce different keys."""
        k1 = _make_cache_key("tpl-1", {"a": 1})
        k2 = _make_cache_key("tpl-1", {"a": 2})
        assert k1 != k2

    def test_different_template_different_keys(self) -> None:
        """Different template IDs produce different keys."""
        k1 = _make_cache_key("tpl-1", {"a": 1})
        k2 = _make_cache_key("tpl-2", {"a": 1})
        assert k1 != k2

    def test_md5_format(self) -> None:
        """Cache key is a 32-char hex string (MD5)."""
        key = _make_cache_key("t1", {})
        assert len(key) == 32
        assert all(c in "0123456789abcdef" for c in key)


# ---------------------------------------------------------------------------
# QueryExecutor tests
# ---------------------------------------------------------------------------


class TestQueryExecutor:
    """Tests for :class:`QueryExecutor` with mocked connectors."""

    @pytest.fixture(autouse=True)
    def clear_global_cache(self) -> None:
        """Clear the global cache before each test."""
        _cache.clear()
        yield
        _cache.clear()

    def _make_template(
        self,
        *,
        tpl_id: str = "qt-test",
        data_source_id: str = "ds-test",
        query_content: str = "SELECT 1",
        params_json: list[dict[str, Any]] | None = None,
        cache_ttl: int = 0,
        timeout_seconds: int = 30,
        max_rows: int = 1000,
    ) -> QueryTemplate:
        return QueryTemplate(
            id=tpl_id,
            tenant_id="tenant-1",
            name="test",
            data_source_id=data_source_id,
            query_type="sql",
            query_content=query_content,
            params_json=params_json or [],
            cache_ttl=cache_ttl,
            timeout_seconds=timeout_seconds,
            max_rows=max_rows,
        )

    def _make_data_source(self, *, ds_id: str = "ds-test", ds_type: str = "mysql") -> MagicMock:
        ds = MagicMock(spec=DataSource)
        ds.id = ds_id
        ds.type = ds_type
        ds.config_json = {"host": "localhost", "port": 3306}
        ds.read_only = True
        return ds

    def test_execute_returns_correct_result(self) -> None:
        """Execution returns expected columns, rows, and timing."""
        template = self._make_template()
        ds = self._make_data_source()

        mock_session = MagicMock(spec=Session)
        mock_session.get.return_value = ds

        mock_connector = MagicMock()
        mock_connector.execute.return_value = QueryResult(
            columns=["id", "name"],
            rows=[{"id": 1, "name": "Alice"}, {"id": 2, "name": "Bob"}],
            row_count=2,
        )
        mock_connector.close = MagicMock()

        with patch("app.data_query.executor.get_connector", return_value=mock_connector):
            executor = QueryExecutor(db_session=mock_session)
            result = executor.execute(template, {})

        assert result.template_id == "qt-test"
        assert result.columns == ["id", "name"]
        assert result.row_count == 2
        assert len(result.rows) == 2
        assert result.rows[0]["name"] == "Alice"
        assert result.cached is False
        assert result.execution_time_ms >= 0.0
        mock_connector.close.assert_called_once()

    def test_validation_error_propagates(self) -> None:
        """Parameter validation errors are raised as ValueError."""
        template = self._make_template(
            params_json=[{"name": "id", "type": "int", "required": True}]
        )
        mock_session = MagicMock(spec=Session)
        executor = QueryExecutor(db_session=mock_session)

        with pytest.raises(ValueError, match="Missing required parameter"):
            executor.execute(template, {})

    def test_cache_hit_returns_cached(self) -> None:
        """Second call with same params returns cached result, no re-execution."""
        template = self._make_template(
            cache_ttl=300,
            params_json=[{"name": "x", "type": "int", "required": True}],
        )
        ds = self._make_data_source()

        mock_session = MagicMock(spec=Session)
        mock_session.get.return_value = ds

        mock_connector = MagicMock()
        mock_connector.execute.return_value = QueryResult(
            columns=["val"], rows=[{"val": 42}], row_count=1
        )

        with patch("app.data_query.executor.get_connector", return_value=mock_connector):
            executor = QueryExecutor(db_session=mock_session)
            # First call — executes
            r1 = executor.execute(template, {"x": 1})
            assert r1.cached is False
            assert mock_connector.execute.call_count == 1

            # Second call — cached
            r2 = executor.execute(template, {"x": 1})
            assert r2.cached is True
            assert mock_connector.execute.call_count == 1  # no extra call
            assert r2.rows == [{"val": 42}]
            assert r2.execution_time_ms == 0.0

    def test_different_params_not_cached(self) -> None:
        """Different parameter values trigger separate executions."""
        template = self._make_template(
            cache_ttl=300,
            params_json=[{"name": "x", "type": "int", "required": True}],
        )
        ds = self._make_data_source()

        mock_session = MagicMock(spec=Session)
        mock_session.get.return_value = ds

        mock_connector = MagicMock()
        mock_connector.execute.side_effect = [
            QueryResult(columns=["val"], rows=[{"val": 10}], row_count=1),
            QueryResult(columns=["val"], rows=[{"val": 20}], row_count=1),
        ]

        with patch("app.data_query.executor.get_connector", return_value=mock_connector):
            executor = QueryExecutor(db_session=mock_session)
            r1 = executor.execute(template, {"x": 1})
            r2 = executor.execute(template, {"x": 2})

        assert r1.cached is False
        assert r2.cached is False
        assert mock_connector.execute.call_count == 2
        assert r1.rows == [{"val": 10}]
        assert r2.rows == [{"val": 20}]

    def test_cache_expiry_triggers_reexecute(self) -> None:
        """After TTL expires, the query is re-executed."""
        template = self._make_template(
            cache_ttl=1,  # 1 second TTL
        )
        ds = self._make_data_source()

        mock_session = MagicMock(spec=Session)
        mock_session.get.return_value = ds

        mock_connector = MagicMock()
        mock_connector.execute.side_effect = [
            QueryResult(columns=["v"], rows=[{"v": 1}], row_count=1),
            QueryResult(columns=["v"], rows=[{"v": 2}], row_count=1),
        ]

        with patch("app.data_query.executor.get_connector", return_value=mock_connector):
            executor = QueryExecutor(db_session=mock_session)
            # First call
            r1 = executor.execute(template, {})
            assert r1.cached is False
            assert r1.rows == [{"v": 1}]
            assert mock_connector.execute.call_count == 1

            # Manually expire the cache entry by patching time.time
            with patch("app.data_query.executor.time.time", return_value=time.time() + 10):
                r2 = executor.execute(template, {})

            assert r2.cached is False
            assert r2.rows == [{"v": 2}]
            assert mock_connector.execute.call_count == 2

    def test_max_rows_passed_to_connector(self) -> None:
        """The max_rows from template is passed through to the connector."""
        template = self._make_template(max_rows=50)
        ds = self._make_data_source()

        mock_session = MagicMock(spec=Session)
        mock_session.get.return_value = ds

        mock_connector = MagicMock()
        mock_connector.execute.return_value = QueryResult(columns=["a"], rows=[], row_count=0)

        with patch("app.data_query.executor.get_connector", return_value=mock_connector):
            executor = QueryExecutor(db_session=mock_session)
            executor.execute(template, {})

        call_kwargs = mock_connector.execute.call_args
        assert call_kwargs.kwargs["max_rows"] == 50

    def test_timeout_passed_to_connector(self) -> None:
        """The timeout_seconds from template is passed through to the connector."""
        template = self._make_template(timeout_seconds=15)
        ds = self._make_data_source()

        mock_session = MagicMock(spec=Session)
        mock_session.get.return_value = ds

        mock_connector = MagicMock()
        mock_connector.execute.return_value = QueryResult(columns=["a"], rows=[], row_count=0)

        with patch("app.data_query.executor.get_connector", return_value=mock_connector):
            executor = QueryExecutor(db_session=mock_session)
            executor.execute(template, {})

        call_kwargs = mock_connector.execute.call_args
        assert call_kwargs.kwargs["timeout"] == 15

    def test_no_cache_when_ttl_zero(self) -> None:
        """When cache_ttl is 0, no caching occurs and each call executes."""
        template = self._make_template(cache_ttl=0)
        ds = self._make_data_source()

        mock_session = MagicMock(spec=Session)
        mock_session.get.return_value = ds

        mock_connector = MagicMock()
        mock_connector.execute.return_value = QueryResult(columns=["a"], rows=[{"a": 1}])

        with patch("app.data_query.executor.get_connector", return_value=mock_connector):
            executor = QueryExecutor(db_session=mock_session)
            r1 = executor.execute(template, {})
            r2 = executor.execute(template, {})

        assert r1.cached is False
        assert r2.cached is False
        assert mock_connector.execute.call_count == 2

    def test_connector_closed_even_on_error(self) -> None:
        """Connector.close() is called even when execute raises."""
        template = self._make_template()
        ds = self._make_data_source()

        mock_session = MagicMock(spec=Session)
        mock_session.get.return_value = ds

        mock_connector = MagicMock()
        mock_connector.execute.side_effect = RuntimeError("DB boom")

        with patch("app.data_query.executor.get_connector", return_value=mock_connector):
            executor = QueryExecutor(db_session=mock_session)
            with pytest.raises(RuntimeError, match="DB boom"):
                executor.execute(template, {})

        mock_connector.close.assert_called_once()

    def test_data_source_not_found_raises(self) -> None:
        """Missing data source raises ValueError."""
        template = self._make_template(data_source_id="ds-missing")
        mock_session = MagicMock(spec=Session)
        mock_session.get.return_value = None

        executor = QueryExecutor(db_session=mock_session)
        with pytest.raises(ValueError, match="Data source not found"):
            executor.execute(template, {})

    def test_no_db_session_raises(self) -> None:
        """Executor without a db_session raises RuntimeError on execute."""
        template = self._make_template()
        executor = QueryExecutor(db_session=None)
        with pytest.raises(RuntimeError, match="requires a db_session"):
            executor.execute(template, {})

    def test_validated_params_passed_to_connector(self) -> None:
        """Validated/coerced params are what the connector receives."""
        template = self._make_template(
            query_content="SELECT * FROM t WHERE id = :id",
            params_json=[{"name": "id", "type": "int", "required": True}],
        )
        ds = self._make_data_source()

        mock_session = MagicMock(spec=Session)
        mock_session.get.return_value = ds

        mock_connector = MagicMock()
        mock_connector.execute.return_value = QueryResult(columns=["id"], rows=[{"id": 42}])

        with patch("app.data_query.executor.get_connector", return_value=mock_connector):
            executor = QueryExecutor(db_session=mock_session)
            executor.execute(template, {"id": "42"})

        # Check that the connector received the int-coerced value
        call_args = mock_connector.execute.call_args
        params_arg = call_args.args[1]
        assert params_arg == {"id": 42}
        assert isinstance(params_arg["id"], int)

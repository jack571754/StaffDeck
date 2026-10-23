"""Query execution engine with parameter validation and result caching.

Provides :class:`QueryExecutor` which validates parameters, looks up
cached results, delegates to the appropriate connector, and records
execution timing.
"""

from __future__ import annotations

import hashlib
import time
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from sqlmodel import Session

from app.data_query.connectors import QueryResult, get_connector
from app.data_query.models import DataSource, QueryExecuteResult, QueryTemplate

try:
    import sqlparse
    import sqlparse.engine.grouping as sqlparse_grouping
    if hasattr(sqlparse_grouping, "MAX_GROUPING_TOKENS"):
        sqlparse_grouping.MAX_GROUPING_TOKENS = max(sqlparse_grouping.MAX_GROUPING_TOKENS, 500000)
except ImportError:
    sqlparse = None  # type: ignore[assignment]
import re


def _clean_value(val: Any) -> Any:
    if isinstance(val, Decimal):
        return float(val) if val % 1 else int(val)
    if isinstance(val, (datetime, date)):
        return val.isoformat()
    return val


def _clean_row(row: dict[str, Any]) -> dict[str, Any]:
    return {k: _clean_value(v) for k, v in row.items()}

# ---------------------------------------------------------------------------
# Parameter validation
# ---------------------------------------------------------------------------


def _validate_params(params: dict[str, Any], params_def: list[dict[str, Any]]) -> dict[str, Any]:
    """Validate and coerce user-supplied parameters against *params_def*.

    Rules:
        - Required parameters that are missing raise ``ValueError``.
        - Optional parameters with a default are filled in when absent.
        - Enum values must be in the allowed list (if ``enum`` is present).
        - Types ``int``, ``float``, ``bool``, and ``date`` are coerced;
          ``string`` is passed through unchanged.
        - ``date`` values must match ``YYYY-MM-DD`` format.

    Args:
        params: Raw user-supplied parameter mapping.
        params_def: Parameter definition list (see brief for shape).

    Returns:
        A new dict with validated / coerced / default-filled values.

    Raises:
        ValueError: If a required parameter is missing, an enum value is
            invalid, or a type coercion fails.
    """
    result: dict[str, Any] = {}
    params = params or {}

    for pdef in params_def:
        name = pdef["name"]
        ptype = pdef.get("type", "string")
        required = pdef.get("required", False)
        default = pdef.get("default", None)
        enum_values = pdef.get("enum", None)

        if name not in params or (params[name] in (None, "") and "default" in pdef and default not in (None, "")):
            if "default" in pdef and default not in (None, ""):
                value = default
            elif required:
                raise ValueError(f"Missing required parameter: {name!r}")
            else:
                if "default" in pdef:
                    result[name] = default
                continue
        else:
            value = params[name]

        # Type coercion
        if ptype == "int":
            try:
                value = int(value)
            except (TypeError, ValueError) as exc:
                raise ValueError(
                    f"Parameter {name!r} must be an integer, got {value!r}"
                ) from exc
        elif ptype == "float":
            try:
                value = float(value)
            except (TypeError, ValueError) as exc:
                raise ValueError(
                    f"Parameter {name!r} must be a float, got {value!r}"
                ) from exc
        elif ptype == "bool":
            if isinstance(value, bool):
                pass
            elif isinstance(value, str):
                if value.lower() in ("true", "1", "yes", "y", "on"):
                    value = True
                elif value.lower() in ("false", "0", "no", "n", "off"):
                    value = False
                else:
                    raise ValueError(
                        f"Parameter {name!r} must be a boolean, got {value!r}"
                    )
            elif isinstance(value, (int, float)):
                value = bool(value)
            else:
                raise ValueError(
                    f"Parameter {name!r} must be a boolean, got {type(value).__name__}"
                )
        elif ptype == "date":
            # Dynamic date macro translation (in local/CST timezone UTC+8)
            if isinstance(value, str):
                v_lower = value.strip().lower()
                cst = timezone(timedelta(hours=8))
                now_local = datetime.now(cst)
                if v_lower in ("今天", "today", "@today", "now", "当前", "当天", "当日", "执行日期"):
                    value = now_local.strftime("%Y-%m-%d")
                elif v_lower in ("昨天", "yesterday", "@yesterday", "昨", "前一日"):
                    value = (now_local - timedelta(days=1)).strftime("%Y-%m-%d")
                elif v_lower in ("前天", "before_yesterday"):
                    value = (now_local - timedelta(days=2)).strftime("%Y-%m-%d")

            # Validate YYYY-MM-DD format
            if not isinstance(value, str) or len(value) != 10 or value[4] != "-" or value[7] != "-":
                raise ValueError(
                    f"Parameter {name!r} must be a date in YYYY-MM-DD format, got {value!r}"
                )
            try:
                year = int(value[:4])
                month = int(value[5:7])
                day = int(value[8:10])
            except ValueError as exc:
                raise ValueError(
                    f"Parameter {name!r} must be a date in YYYY-MM-DD format, got {value!r}"
                ) from exc
            if not (1 <= month <= 12 and 1 <= day <= 31 and year > 0):
                raise ValueError(
                    f"Parameter {name!r} has invalid date value: {value!r}"
                )
            # string is fine; keep it as-is after validation

        # Enum check
        if enum_values is not None and value not in enum_values:
            raise ValueError(
                f"Parameter {name!r} value {value!r} is not in allowed values: {enum_values}"
            )

        result[name] = value

    return result


# ---------------------------------------------------------------------------
# In-memory cache with TTL
# ---------------------------------------------------------------------------


class _QueryCache:
    """Simple in-memory cache keyed by string with per-entry TTL.

    Internal storage maps keys to ``(expiry_timestamp, value)`` tuples.
    Expired entries are evicted lazily on :meth:`get`.
    """

    def __init__(self) -> None:
        self._store: dict[str, tuple[float, QueryResult]] = {}

    def get(self, key: str) -> QueryResult | None:
        """Return the cached value for *key*, or ``None`` if missing/expired."""
        entry = self._store.get(key)
        if entry is None:
            return None
        expiry, value = entry
        if time.time() >= expiry:
            # Lazy eviction
            del self._store[key]
            return None
        return value

    def set(self, key: str, value: QueryResult, ttl_seconds: int) -> None:
        """Store *value* under *key* with a TTL of *ttl_seconds* seconds."""
        expiry = time.time() + ttl_seconds
        self._store[key] = (expiry, value)

    def clear(self) -> None:
        """Remove all entries from the cache."""
        self._store.clear()

    def invalidate(self, template_id: str) -> int:
        """Remove every entry belonging to *template_id*.

        Returns:
            The number of entries removed.
        """
        prefix = f"{template_id}::"
        stale = [key for key in self._store if key.startswith(prefix)]
        for key in stale:
            del self._store[key]
        return len(stale)


# Global singleton cache
_cache = _QueryCache()


def invalidate_template_cache(template_id: str) -> int:
    """Drop all cached results for *template_id* (e.g. after an update).

    Returns:
        The number of cache entries removed.
    """
    return _cache.invalidate(template_id)


def _make_cache_key(template: QueryTemplate, params: dict[str, Any]) -> str:
    """Build a deterministic, content-aware cache key.

    Besides the template id, the key covers a hash of the template's
    ``query_content`` and ``params_json`` so that editing a template's SQL
    yields a fresh key even before explicit invalidation, plus the sorted
    parameter values. The template id is kept as a literal prefix so
    :meth:`_QueryCache.invalidate` can drop all of a template's entries.
    """
    content_hash = hashlib.md5(
        f"{template.query_content}\x1f{template.params_json}".encode()
    ).hexdigest()
    # Serialize params in a stable order
    param_str = ",".join(f"{k}={params[k]!r}" for k in sorted(params.keys()))
    raw = f"{template.id}|{content_hash}|{param_str}"
    return f"{template.id}::{hashlib.md5(raw.encode('utf-8')).hexdigest()}"


_BLOCK_COMMENT_RE = re.compile(r"/\*.*?\*/", re.DOTALL)
_LINE_COMMENT_RE = re.compile(r"--[^\n]*")


def _strip_comments(sql: str) -> str:
    """Remove block and line comments from SQL."""
    sql = _BLOCK_COMMENT_RE.sub("", sql)
    sql = _LINE_COMMENT_RE.sub("", sql)
    return sql


def _extract_cte_names(sql: str) -> set[str]:
    """Extract Common Table Expression (CTE) aliases defined in SQL."""
    cleaned = _strip_comments(sql)
    pattern = re.compile(
        r"(?:(?:WITH\s+(?:RECURSIVE\s+)?)|,)\s*([`'\"a-zA-Z0-9_\u4e00-\u9fff\-]+)(?:\s*\([^)]*\))?\s+AS\s*\(",
        re.IGNORECASE,
    )
    return {m.group(1).strip("`'\"").lower() for m in pattern.finditer(cleaned)}


def _contains_subquery(token: Any) -> bool:
    """Check if token or its children represent a subquery (parenthesis)."""
    if isinstance(token, getattr(sqlparse.sql, "Parenthesis", ())) or getattr(token, "value", "").strip().startswith("("):
        return True
    if hasattr(token, "tokens"):
        return any(_contains_subquery(sub) for sub in token.tokens)
    return False


def _extract_query_tables(sql: str) -> set[str]:
    """Extract referenced physical table names from a SQL query using sqlparse."""
    if not sql or not sql.strip():
        return set()

    cte_names = _extract_cte_names(sql)
    raw_tables: set[str] = set()

    if sqlparse is not None:
        try:
            statements = sqlparse.parse(sql)

            def _walk(tokens):
                from_seen = False
                for token in tokens:
                    if token.is_whitespace:
                        continue
                    if hasattr(token, "tokens"):
                        _walk(token.tokens)
                    val = token.value.upper()
                    if token.ttype is sqlparse.tokens.Keyword and (
                        val in ("FROM", "JOIN", "INNER JOIN", "LEFT JOIN", "RIGHT JOIN", "CROSS JOIN", "FULL JOIN")
                        or "JOIN" in val
                    ):
                        from_seen = True
                        continue
                    if from_seen:
                        if isinstance(token, sqlparse.sql.Parenthesis) or token.value.strip().startswith("("):
                            from_seen = False
                            continue
                        elif isinstance(token, sqlparse.sql.IdentifierList):
                            for iden in token.get_identifiers():
                                if not _contains_subquery(iden):
                                    name = iden.get_real_name() or iden.value
                                    raw_tables.add(name.strip("`'\""))
                            from_seen = False
                        elif isinstance(token, sqlparse.sql.Identifier):
                            if not _contains_subquery(token):
                                name = token.get_real_name() or token.value
                                raw_tables.add(name.strip("`'\""))
                            from_seen = False
                        elif token.ttype is sqlparse.tokens.Keyword:
                            from_seen = False
                        else:
                            val_str = token.value.strip("`'\"")
                            if not val_str.startswith("(") and "\n" not in val_str and " " not in val_str:
                                raw_tables.add(val_str)
                            from_seen = False

            for stmt in statements:
                _walk(stmt.tokens)
        except Exception:  # noqa: BLE001, S110
            pass

    if not raw_tables:
        raw_tables = {
            m.strip("`'\"")
            for m in re.findall(
                r"(?:FROM|JOIN)\s+([`'\"a-zA-Z0-9_\u4e00-\u9fff\.\-]+)",
                sql,
                re.IGNORECASE,
            )
        }

    clean_tables: set[str] = set()
    for t in raw_tables:
        base_name = t.split(".")[-1].strip("`'\"").lower()
        if (
            base_name
            and base_name not in cte_names
            and not base_name.startswith("(")
            and base_name not in ("dual", "select", "where", "on", "values")
        ):
            clean_tables.add(base_name)

    return clean_tables

# ---------------------------------------------------------------------------
# Query executor
# ---------------------------------------------------------------------------


class QueryExecutor:
    """Execute query templates with validation, caching, and timing.

    Args:
        db_session: SQLAlchemy / SQLModel session used to look up the
            data source.  Required for execution.
    """

    def __init__(self, db_session: Session | None = None) -> None:
        self._db = db_session

    def execute(
        self,
        template: QueryTemplate,
        params: dict[str, Any],
    ) -> QueryExecuteResult:
        """Execute *template* with the given *params*.

        Execution flow:
            1. Validate and coerce parameters.
            2. Check the cache (if ``cache_ttl > 0``); hit returns immediately.
            3. Load the data source from DB.
            4. Obtain a connector via :func:`get_connector`.
            5. Execute the query via the connector.
            6. Close the connector.
            7. Populate the cache (if TTL > 0).
            8. Return a :class:`QueryExecuteResult` with timing.

        Args:
            template: The query template to execute.
            params: User-supplied parameter values.

        Returns:
            A :class:`QueryExecuteResult` populated with columns, rows,
            execution time, and cache status.

        Raises:
            ValueError: On parameter validation errors (propagated from
                :func:`_validate_params`).
            Exception: Any exception raised by the connector is propagated
                unchanged.
        """
        # 1. Parameter validation
        validated = _validate_params(params, template.params_json or [])

        # 2. Load and validate data source & permissions BEFORE cache lookup
        if self._db is None:
            raise RuntimeError("QueryExecutor requires a db_session to load data sources")
        data_source = self._db.get(DataSource, template.data_source_id)
        if data_source is None:
            raise ValueError(f"Data source not found: {template.data_source_id!r}")

        ds_status = getattr(data_source, "status", "active")
        if isinstance(ds_status, str) and ds_status.lower() not in ("active", "enabled"):
            raise ValueError(f"Data source is inactive: {template.data_source_id!r}")

        raw_allowed = getattr(data_source, "allowed_tables_json", None)
        if isinstance(raw_allowed, list) and len(raw_allowed) > 0:
            allowed_set = {t.lower().strip() for t in raw_allowed if isinstance(t, str) and t.strip()}
            if allowed_set:
                query_tables = _extract_query_tables(template.query_content)
                for tbl in query_tables:
                    if tbl.lower() not in allowed_set:
                        raise PermissionError(
                            f"Access to table '{tbl}' is forbidden by data source whitelist"
                        )

        # 3. Cache lookup (only after data source status and table permissions are verified)
        cache_ttl = template.cache_ttl or 0
        if cache_ttl > 0:
            cache_key = _make_cache_key(template, validated)
            cached = _cache.get(cache_key)
            if cached is not None:
                return QueryExecuteResult(
                    template_id=template.id,
                    columns=cached.columns,
                    rows=[_clean_row(r) for r in cached.rows],
                    row_count=cached.row_count,
                    execution_time_ms=0.0,
                    cached=True,
                )

        # 4-5. Get connector and execute (timed)
        connector = get_connector(data_source)
        start = time.perf_counter()
        try:
            result = connector.execute(
                template.query_content,
                validated,
                timeout=template.timeout_seconds or 30,
                max_rows=template.max_rows or 1000,
            )
        finally:
            # 6. Always close
            connector.close()
        elapsed_ms = (time.perf_counter() - start) * 1000.0

        # 7. Write cache
        if cache_ttl > 0:
            _cache.set(cache_key, result, cache_ttl)

        # 8. Return
        return QueryExecuteResult(
            template_id=template.id,
            columns=result.columns,
            rows=[_clean_row(r) for r in result.rows],
            row_count=result.row_count,
            execution_time_ms=round(elapsed_ms, 3),
            cached=False,
        )

"""Query execution engine with parameter validation and result caching.

Provides :class:`QueryExecutor` which validates parameters, looks up
cached results, delegates to the appropriate connector, and records
execution timing.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
import hashlib
import time
from typing import Any

from sqlmodel import Session

from app.data_query.connectors import QueryResult, get_connector
from app.data_query.models import DataSource, QueryExecuteResult, QueryTemplate


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

        if name not in params:
            if required:
                raise ValueError(f"Missing required parameter: {name!r}")
            # Use default if provided; otherwise skip
            if "default" in pdef:
                result[name] = default
            continue

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

        # 2. Cache lookup
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

        # 3. Load data source
        if self._db is None:
            raise RuntimeError("QueryExecutor requires a db_session to load data sources")
        data_source = self._db.get(DataSource, template.data_source_id)
        if data_source is None:
            raise ValueError(f"Data source not found: {template.data_source_id!r}")

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

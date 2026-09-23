"""HTTP API data source connector.

Calls remote HTTP/JSON endpoints and normalizes the JSON response into a
:class:`QueryResult`.  ``:param_name`` style tokens in the URL path and
query string are substituted from the params dict.
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from app.data_query.connectors.base import BaseConnector, QueryResult
from app.data_query.security import decrypt_config

# Match :param_name tokens in URL path and query portions.
_PARAM_PATTERN = re.compile(r":(\w+)")

_HTTP_SENSITIVE_KEYS: list[str] = []


def _substitute_path_and_query(
    template: str,
    params: dict[str, Any],
) -> tuple[str, dict[str, Any]]:
    """Substitute ``:param_name`` tokens in a URL path+query template.

    * Tokens found in the path or query values are replaced with the
      corresponding value from *params* (stringified).
    * Params that appear in the template are consumed; remaining params
      are returned as extra query parameters.

    Returns ``(path_and_query, remaining_params)``.
    """
    used: set[str] = set()

    def _replace(match: re.Match[str]) -> str:
        name = match.group(1)
        used.add(name)
        value = params.get(name, "")
        return str(value)

    # Split off the query string so we can handle both path params and
    # query value params uniformly.
    parts = urlsplit(template)
    path = _PARAM_PATTERN.sub(_replace, parts.path)
    query_pairs = parse_qsl(parts.query, keep_blank_values=True)
    new_query_pairs: list[tuple[str, str]] = []
    for key, value in query_pairs:
        substituted = _PARAM_PATTERN.sub(_replace, value)
        new_query_pairs.append((key, substituted))

    remaining = {k: v for k, v in params.items() if k not in used}
    new_query = urlencode(new_query_pairs)
    result = urlunsplit((parts.scheme, parts.netloc, path, new_query, ""))
    return result, remaining


class HttpApiConnector(BaseConnector):
    """Connector for JSON-over-HTTP data sources.

    The *query* argument to :meth:`execute` is interpreted as a URL path
    (with optional query string) relative to the configured ``base_url``.
    Named parameters of the form ``:param_name`` are substituted into the
    path and query values; any remaining parameters are appended as
    additional query parameters.

    JSON responses are normalized into :class:`QueryResult`:

    * A list (of objects) becomes ``rows`` with keys as columns.
    * A single object becomes a single row.
    * A scalar value becomes a single row with column ``"value"``.
    """

    def __init__(self, data_source: Any) -> None:
        super().__init__(data_source)
        self._client: Any = None  # httpx.Client | None
        self._decrypted_config: dict[str, Any] | None = None

    # -- helpers -------------------------------------------------------------

    def _get_decrypted_config(self) -> dict[str, Any]:
        """Return config_json with sensitive fields decrypted."""
        if self._decrypted_config is None:
            self._decrypted_config = decrypt_config(
                self.config,
                _HTTP_SENSITIVE_KEYS,
            )
        return self._decrypted_config

    def _get_client(self) -> Any:
        """Return (and lazily create) the httpx client."""
        if self._client is not None:
            return self._client
        try:
            import httpx  # noqa: WPS433
        except ImportError as exc:
            raise RuntimeError(
                "httpx is required for HTTP API connectors"
            ) from exc

        cfg = self._get_decrypted_config()
        base_url = cfg.get("base_url", "")
        if not base_url:
            raise RuntimeError("HTTP API data source config missing 'base_url'")
        headers = dict(cfg.get("headers", {}) or {})
        timeout = float(cfg.get("timeout", 30.0))

        self._client = httpx.Client(
            base_url=base_url,
            headers=headers,
            timeout=timeout,
        )
        self._connected = True
        return self._client

    # -- public API ----------------------------------------------------------

    def test_connection(self) -> bool:
        """Hit the configured health endpoint (or base URL) and check for a 2xx."""
        cfg = self._get_decrypted_config()
        health_url = cfg.get("health_url") or "/"
        client = self._get_client()
        try:
            response = client.get(health_url)
            return 200 <= response.status_code < 300
        except Exception:  # noqa: BLE001 - any failure means the endpoint is unreachable.
            return False

    def execute(
        self,
        query: str,
        params: dict[str, Any],
        *,
        timeout: int = 30,
        max_rows: int = 1000,
    ) -> QueryResult:
        """Execute an HTTP GET against *query* and normalize JSON to QueryResult.

        Args:
            query: URL path (and optional query string) relative to
                ``base_url``, may contain ``:param_name`` tokens.
            params: Parameter values for substitution; extras become
                additional query parameters.
            timeout: Per-request timeout in seconds (overrides config).
            max_rows: Cap on rows returned (applies after normalization).

        Returns:
            A :class:`QueryResult` built from the JSON response.
        """
        client = self._get_client()
        url, remaining = _substitute_path_and_query(query, params)

        try:
            response = client.get(
                url,
                params=remaining if remaining else None,
                timeout=timeout,
            )
            response.raise_for_status()
            data = response.json()
        except Exception as exc:
            raise RuntimeError(f"HTTP API request failed: {exc}") from exc

        return _normalize_json_response(data, max_rows=max_rows)

    def list_tables(self) -> list[dict[str, Any]]:
        """HTTP data sources do not have tables; return an empty list."""
        return []

    def describe_table(self, table: str) -> list[dict[str, Any]]:
        """HTTP data sources do not have schema tables; return an empty list."""
        return []

    def preview_table(self, table: str, limit: int = 20) -> QueryResult:
        """HTTP data sources do not support table previews; return empty result."""
        return QueryResult(columns=[], rows=[], row_count=0)

    def explain(self, sql: str, params: dict[str, Any]) -> QueryResult:
        """Probe the HTTP endpoint with max_rows=1 to discover schema."""
        return self.execute(sql, params, timeout=10, max_rows=1)

    def close(self) -> None:
        """Release the underlying httpx client."""
        if self._client is not None:
            try:
                self._client.close()
            except Exception:  # noqa: BLE001 S110 - best-effort cleanup on close.
                pass
            self._client = None
        super().close()



# -- JSON normalization ------------------------------------------------------


def _normalize_json_response(data: Any, *, max_rows: int = 1000) -> QueryResult:
    """Convert an arbitrary JSON value into a :class:`QueryResult`."""
    if isinstance(data, list):
        rows = [_ensure_dict(item) for item in data]
        if max_rows and max_rows > 0:
            rows = rows[:max_rows]
        columns = _infer_columns(rows)
        return QueryResult(columns=columns, rows=rows, row_count=len(rows))

    if isinstance(data, dict):
        # Wrapped responses: look for a common "list at key" hint.
        for list_key in ("data", "items", "results", "rows", "list"):
            if list_key in data and isinstance(data[list_key], list):
                return _normalize_json_response(data[list_key], max_rows=max_rows)
        # Otherwise treat the dict as a single row.
        row = dict(data)
        columns = list(row.keys())
        return QueryResult(columns=columns, rows=[row], row_count=1)

    # Scalar / null / other: wrap in {"value": ...}
    row = {"value": data}
    return QueryResult(columns=["value"], rows=[row], row_count=1)


def _ensure_dict(item: Any) -> dict[str, Any]:
    """Coerce a list element to a dict (scalars become ``{"value": item}``)."""
    if isinstance(item, dict):
        return item
    return {"value": item}


def _infer_columns(rows: list[dict[str, Any]]) -> list[str]:
    """Infer column order from the union of keys across rows.

    The first row's key order is preserved; subsequent rows contribute
    any new keys at the end (in order of first appearance).
    """
    columns: list[str] = []
    seen: set[str] = set()
    for row in rows:
        for key in row:
            if key not in seen:
                seen.add(key)
                columns.append(key)
    return columns

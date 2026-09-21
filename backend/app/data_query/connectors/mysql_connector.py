"""MySQL data source connector.

Uses ``pymysql`` with ``DictCursor`` for dictionary row results. SQL is
validated against a read-only whitelist (SELECT / WITH...SELECT only).
"""

from __future__ import annotations

import re
from typing import Any

from app.data_query.connectors.base import BaseConnector, QueryResult
from app.data_query.security import decrypt_config

# -- SQL whitelist -----------------------------------------------------------
# Allow SELECT statements and WITH-CTE statements.  Comments (/* ... */ and
# -- style) are stripped before matching to prevent comment-based bypasses.

_SQL_ALLOW_PATTERN = re.compile(
    r"^\s*(SELECT|WITH\s+\w+\s+AS\s*\()",
    re.IGNORECASE,
)

# Comments used to bypass whitelist detection.
_BLOCK_COMMENT_RE = re.compile(r"/\*.*?\*/", re.DOTALL)
_LINE_COMMENT_RE = re.compile(r"--[^\n]*")

# Named parameters in :param_name style. The lookbehinds prevent false
# positives:
#   * ``(?<!:`` — keeps PostgreSQL-style casts like ``v::date`` intact;
#   * ``(?<!\w)`` — keeps string literals like ``'%10:30%'`` (colon preceded
#     by a digit/word char) from being turned into bind parameters.
# Known limitation: a ``:name`` occurrence preceded by whitespace *inside* a
# string literal would still be converted (regex cannot see string quoting).
_PARAM_PATTERN = re.compile(r"(?<!:)(?<!\w):([a-zA-Z_][a-zA-Z0-9_]*)")

_MYSQL_SENSITIVE_KEYS = ["password"]


class MySQLError(Exception):
    """Base error for MySQL connector failures."""


class SQLNotAllowedError(MySQLError):
    """Raised when a SQL statement does not match the read-only whitelist."""


def _strip_comments(sql: str) -> str:
    """Remove block and line comments from SQL before whitelist checking."""
    sql = _BLOCK_COMMENT_RE.sub("", sql)
    sql = _LINE_COMMENT_RE.sub("", sql)
    return sql


def _is_sql_allowed(sql: str) -> bool:
    """Return True if *sql* passes the read-only whitelist check."""
    cleaned = _strip_comments(sql)
    return bool(_SQL_ALLOW_PATTERN.match(cleaned))


def _convert_params(query: str) -> tuple[str, list[str]]:
    """Convert ``:param_name`` style parameters to pymysql ``%(name)s`` style.

    Returns a tuple of ``(new_query, param_names)`` where *param_names* is
    the ordered list of parameter names discovered in the query.
    """
    param_names: list[str] = []

    def _replace(match: re.Match[str]) -> str:
        name = match.group(1)
        param_names.append(name)
        return f"%({name})s"

    new_query = _PARAM_PATTERN.sub(_replace, query)
    return new_query, param_names


class MySQLConnector(BaseConnector):
    """MySQL / MariaDB connector using pymysql with DictCursor.

    Features:

    * Lazy connection — the DB connection is opened on first execute.
    * Read-only SQL whitelist — only SELECT and WITH...SELECT are allowed.
    * Named parameters — ``:param_name`` is converted to pymysql's format.
    * Sensitive config values (``password``) are decrypted on connect.
    * ``read_only`` mode enforced via the whitelist (default on).
    """

    def __init__(self, data_source: Any) -> None:
        super().__init__(data_source)
        self._conn: Any = None  # pymysql.Connection or None
        self._decrypted_config: dict[str, Any] | None = None

    # -- helpers -------------------------------------------------------------

    def _get_decrypted_config(self) -> dict[str, Any]:
        """Return config_json with sensitive fields decrypted."""
        if self._decrypted_config is None:
            self._decrypted_config = decrypt_config(
                self.config,
                _MYSQL_SENSITIVE_KEYS,
            )
        return self._decrypted_config

    def _connect(self, timeout: int = 30) -> None:
        """Open a pymysql connection (lazy, idempotent).

        Args:
            timeout: Nominal query budget in seconds. The connect phase is
                capped at 10s; the socket read timeout gets 2x headroom so a
                legitimately slow query near the nominal budget is not killed
                by pymysql's client-side read timer (MySQL 2013).
        """
        if self._conn is not None:
            return
        try:
            import pymysql  # noqa: WPS433
        except ImportError as exc:
            raise MySQLError(
                "pymysql is required for MySQL connectors; "
                "install with `pip install pymysql`"
            ) from exc

        cfg = self._get_decrypted_config()
        host = cfg.get("host", "127.0.0.1")
        port = int(cfg.get("port", 3306))
        database = cfg.get("database", "")
        user = cfg.get("username", "")
        password = cfg.get("password", "")
        charset = cfg.get("charset", "utf8mb4")

        if not database:
            raise MySQLError("MySQL data source config is missing 'database'")

        self._conn = pymysql.connect(
            host=host,
            port=port,
            database=database,
            user=user,
            password=password,
            charset=charset,
            cursorclass=pymysql.cursors.DictCursor,
            connect_timeout=min(timeout, 10),
            read_timeout=timeout * 2,
            write_timeout=timeout,
        )
        self._connected = True

    # -- public API ----------------------------------------------------------

    def test_connection(self) -> bool:
        """Open a connection, ping, and close; return True on success."""
        try:
            self._connect()
            if self._conn is not None:
                self._conn.ping(reconnect=False)
            return True
        except Exception:  # noqa: BLE001 - any failure means the connection is bad.
            return False
        finally:
            self.close()

    def execute(
        self,
        query: str,
        params: dict[str, Any],
        *,
        timeout: int = 30,
        max_rows: int = 1000,
    ) -> QueryResult:
        """Execute a read-only SQL query and return a :class:`QueryResult`.

        Args:
            query: SQL statement with ``:param_name`` named parameters.
            params: Parameter values keyed by name.
            timeout: Query timeout in seconds (passed to pymysql connect;
                note: per-query timeout requires server-side support).
            max_rows: Maximum rows to fetch (0 = unlimited).

        Returns:
            A QueryResult populated from the DictCursor output.

        Raises:
            SQLNotAllowedError: If the SQL does not match the whitelist.
            MySQLError: On connection or execution errors.
        """
        if self.read_only and not _is_sql_allowed(query):
            raise SQLNotAllowedError(
                "SQL statement rejected: only SELECT and WITH...SELECT "
                "queries are allowed in read-only mode"
            )

        self._connect(timeout=timeout)
        assert self._conn is not None, "connection should be open after _connect"

        converted_query, _ = _convert_params(query)

        try:
            with self._conn.cursor() as cursor:
                cursor.execute(converted_query, params)
                if max_rows and max_rows > 0:
                    raw_rows = cursor.fetchmany(max_rows)
                else:
                    raw_rows = cursor.fetchall()
                columns = [desc[0] for desc in cursor.description] if cursor.description else []
        except Exception as exc:
            raise MySQLError(f"MySQL query execution failed: {exc}") from exc

        rows = [dict(row) for row in raw_rows]
        return QueryResult(
            columns=columns,
            rows=rows,
            row_count=len(rows),
        )

    def close(self) -> None:
        """Close the underlying pymysql connection if open."""
        if self._conn is not None:
            try:
                self._conn.close()
            except Exception:  # noqa: BLE001 S110 - best-effort cleanup on close.
                pass
            self._conn = None
        super().close()

"""Base connector interface and shared result types."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class QueryResult:
    """Standardized query result across all connector types.

    Attributes:
        columns: Ordered list of column names.
        rows: Result rows as dictionaries keyed by column name.
        row_count: Number of rows actually returned (equal to ``len(rows)``
            unless the result was truncated or is a non-row count).
    """

    columns: list[str] = field(default_factory=list)
    rows: list[dict[str, Any]] = field(default_factory=list)
    row_count: int = 0

    def to_list_of_dicts(self) -> list[dict[str, Any]]:
        """Return the rows as a list of dictionaries.

        Provided for callers that expect a plain list-of-dicts shape
        without the surrounding QueryResult metadata.
        """
        return list(self.rows)

    def __post_init__(self) -> None:
        """Ensure row_count matches rows if not explicitly provided."""
        if self.row_count == 0 and self.rows:
            self.row_count = len(self.rows)


class BaseConnector:
    """Abstract base class for data source connectors.

    Subclasses must implement :meth:`test_connection`, :meth:`execute`,
    and :meth:`close`.

    Args:
        data_source: A data source configuration object (e.g.
            :class:`app.data_query.models.DataSource`) with at least
            ``type``, ``config_json``, and ``read_only`` attributes.
    """

    def __init__(self, data_source: Any) -> None:
        self._data_source = data_source
        self._connected = False

    @property
    def type(self) -> str:
        """Return the data source type string."""
        return getattr(self._data_source, "type", "")

    @property
    def config(self) -> dict[str, Any]:
        """Return the data source config dict."""
        return getattr(self._data_source, "config_json", {}) or {}

    @property
    def read_only(self) -> bool:
        """Return whether the data source is configured as read-only."""
        return bool(getattr(self._data_source, "read_only", True))

    def test_connection(self) -> bool:
        """Test whether the connector can reach the data source.

        Returns:
            True if the connection test succeeded.

        Raises:
            NotImplementedError: Subclasses must override.
        """
        raise NotImplementedError

    def execute(
        self,
        query: str,
        params: dict[str, Any],
        *,
        timeout: int = 30,
        max_rows: int = 1000,
    ) -> QueryResult:
        """Execute a query against the data source.

        Args:
            query: The query text (SQL, API path template, etc.).
            params: Named parameter values to substitute into the query.
            timeout: Per-execution timeout in seconds.
            max_rows: Maximum number of rows to return.

        Returns:
            A :class:`QueryResult` with columns, rows, and row count.

        Raises:
            NotImplementedError: Subclasses must override.
        """
        raise NotImplementedError

    def close(self) -> None:
        """Release any resources held by the connector (connections, etc.).

        Subclasses should override if they hold open resources.
        """
        self._connected = False

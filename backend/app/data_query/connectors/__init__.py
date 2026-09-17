"""Data source connector registry and factory.

Use :func:`get_connector` to obtain the appropriate connector subclass
for a given data source object.
"""

from __future__ import annotations

from typing import Any

from app.data_query.connectors.base import BaseConnector, QueryResult
from app.data_query.connectors.http_connector import HttpApiConnector
from app.data_query.connectors.mysql_connector import (
    MySQLConnector,
    MySQLError,
    SQLNotAllowedError,
)

__all__ = [
    "BaseConnector",
    "HttpApiConnector",
    "MySQLConnector",
    "MySQLError",
    "QueryResult",
    "SQLNotAllowedError",
    "get_connector",
]


_CONNECTOR_TYPES: dict[str, type[BaseConnector]] = {
    "mysql": MySQLConnector,
    "http_api": HttpApiConnector,
}


def get_connector(data_source: Any) -> BaseConnector:
    """Return a connector instance appropriate for *data_source*.

    Args:
        data_source: A data source config object (e.g.
            :class:`app.data_query.models.DataSource`) with a
            ``type`` attribute.

    Returns:
        A :class:`BaseConnector` subclass instance.

    Raises:
        ValueError: If ``data_source.type`` is not supported.
    """
    ds_type = getattr(data_source, "type", None)
    if not ds_type:
        raise ValueError("Data source has no 'type' attribute")
    if ds_type not in _CONNECTOR_TYPES:
        raise ValueError(
            f"Unsupported data source type: {ds_type!r}. "
            f"Supported types: {sorted(_CONNECTOR_TYPES)}"
        )
    return _CONNECTOR_TYPES[ds_type](data_source)

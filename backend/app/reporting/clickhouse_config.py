"""Internal ClickHouse endpoint configuration for the sales dashboards.

The endpoint, user and password are read **only** from environment variables or
``backend/.env``:

- ``SALES_DASHBOARD_CLICKHOUSE_URL`` (for example ``http://127.0.0.1:8123/``)
- ``SALES_DASHBOARD_CLICKHOUSE_USER``
- ``SALES_DASHBOARD_CLICKHOUSE_PASSWORD``

Deployment credentials must never be hard-coded in this repository: the tree is
AGPL-3.0 open source. When the URL is unset the dashboards degrade to "no
ClickHouse data" instead of failing, so local/dev runs keep working.
"""

from __future__ import annotations

import logging
import urllib.parse

from app.config import get_settings

logger = logging.getLogger(__name__)


def clickhouse_query_url() -> str | None:
    """Build the authenticated ClickHouse HTTP URL, or ``None`` when unconfigured."""

    settings = get_settings()
    base = (settings.sales_dashboard_clickhouse_url or "").strip()
    if not base:
        logger.warning(
            "SALES_DASHBOARD_CLICKHOUSE_URL is not configured; "
            "sales dashboard ClickHouse queries are skipped"
        )
        return None

    params: list[str] = []
    user = (settings.sales_dashboard_clickhouse_user or "").strip()
    if user:
        params.append("user=" + urllib.parse.quote(user))
    password = settings.sales_dashboard_clickhouse_password or ""
    if password:
        params.append("password=" + urllib.parse.quote(password))
    if not params:
        return base
    if base.endswith("?"):
        return base + "&".join(params)
    separator = "&" if "?" in base else "?"
    return base + separator + "&".join(params)


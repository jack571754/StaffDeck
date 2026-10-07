"""Report renderer registry.

Mirrors ``scheduled_tasks.renderers.CARD_RENDERERS``, with one deliberate
difference: an unspecified name falls back to the *neutral* ``generic_table``
renderer. ``get_card_renderer(None)`` returns the sales card, which is recorded as
a P1 debt in ``scheduled_tasks/CLAUDE.md`` — this registry does not repeat it.
"""

from __future__ import annotations

from app.reporting.renderers.base import (
    ReportRenderer,
    coerce_rows,
    default_generated_at,
    rows_to_dataset,
)
from app.reporting.renderers.generic_table import build_generic_table_report
from app.reporting.renderers.sales_report import build_sales_report

REPORT_RENDERERS: dict[str, ReportRenderer] = {
    "generic_table": build_generic_table_report,
    "table": build_generic_table_report,
    "sales_report": build_sales_report,
    "sales_card": build_sales_report,
    "sales": build_sales_report,
}

DEFAULT_RENDERER_NAME = "generic_table"


def get_report_renderer(name: str | None = None) -> ReportRenderer:
    """Resolve a renderer by name, falling back to the neutral generic table."""
    if not name:
        return REPORT_RENDERERS[DEFAULT_RENDERER_NAME]
    return REPORT_RENDERERS.get(str(name).strip().lower(), build_generic_table_report)


def report_renderer_names() -> list[str]:
    """Registered renderer names, for error messages and capability descriptions."""
    return sorted(REPORT_RENDERERS)


__all__ = [
    "DEFAULT_RENDERER_NAME",
    "REPORT_RENDERERS",
    "ReportRenderer",
    "build_generic_table_report",
    "build_sales_report",
    "coerce_rows",
    "default_generated_at",
    "get_report_renderer",
    "report_renderer_names",
    "rows_to_dataset",
]

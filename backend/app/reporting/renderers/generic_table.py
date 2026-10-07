"""Generic table report renderer.

Semantic counterpart of the Feishu ``generic_table`` card: any query result rows,
rendered as one table. This is also the neutral default of ``REPORT_RENDERERS`` —
unlike ``get_card_renderer(None)``, an unspecified renderer must not silently
produce a business-specific (sales) report.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from app.reporting.document import (
    MAX_DATASET_ROWS,
    ReportBlock,
    ReportDocument,
    ReportMeta,
)
from app.reporting.renderers.base import default_generated_at, rows_to_dataset

DEFAULT_TITLE = "数据报告"


def build_generic_table_report(
    rows: list[dict[str, Any]] | None = None,
    *,
    title: str = "",
    subtitle: str = "",
    columns: list[str] | None = None,
    column_labels: dict[str, str] | None = None,
    max_rows: int | None = MAX_DATASET_ROWS,
    source: dict[str, Any] | None = None,
    generated_at: datetime | None = None,
    tenant_id: str = "",
    **_kwargs: Any,
) -> ReportDocument:
    """Render arbitrary rows as a single sortable/filterable table."""
    dataset = rows_to_dataset(
        rows,
        columns=columns,
        column_labels=column_labels,
        max_rows=max_rows,
        source=source,
    )
    if dataset.rows:
        blocks = [
            ReportBlock(
                kind="table",
                dataset_id=dataset.id,
                columns=list(dataset.columns),
                max_rows=max_rows,
            )
        ]
    else:
        blocks = [ReportBlock(kind="note", text="没有查询到数据。")]

    return ReportDocument(
        meta=ReportMeta(
            title=title.strip() or DEFAULT_TITLE,
            generated_at=generated_at or default_generated_at(),
            tenant_id=tenant_id,
            subtitle=subtitle,
            source=dict(source or {}),
        ),
        datasets=[dataset],
        blocks=blocks,
    )

"""Shared types and row shaping for report renderers.

A ``ReportRenderer`` turns a query result (rows) into a ``ReportDocument``. It is a
pure data-shaping function: no IO, no DB, no HTML. That mirrors how
``scheduled_tasks.renderers`` separates card *payload builders* from the Feishu
serialization step — here, ``render_report_html`` is the single serializer and is
deliberately not registered.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from app.reporting.document import (
    MAX_COLUMNS,
    MAX_DATASET_ROWS,
    ReportDataset,
    ReportDocument,
    json_safe,
)

ReportRenderer = Callable[..., ReportDocument]


def default_generated_at() -> datetime:
    return datetime.now(UTC)


def coerce_rows(
    rows: list[dict[str, Any]] | None,
    *,
    columns: list[str] | None = None,
) -> tuple[list[str], list[dict[str, Any]]]:
    """Normalize rows into JSON-safe dicts and settle a deterministic column order.

    Column order is: explicit ``columns`` that actually exist, then any remaining
    keys in first-seen order. Non-dict entries are dropped.
    """
    normalized: list[dict[str, Any]] = []
    order: list[str] = []
    seen: set[str] = set()
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        clean: dict[str, Any] = {}
        for key, value in row.items():
            name = str(key)
            if name not in seen:
                seen.add(name)
                order.append(name)
            clean[name] = json_safe(value)
        normalized.append(clean)

    if columns:
        # An explicit ``columns`` argument is a declared schema, not a filter: it is
        # kept verbatim even when no row carries that key (e.g. an empty result set
        # must still render its headers), and any extra keys follow in first-seen
        # order. Callers that want a subset use ReportBlock.columns instead.
        explicit = [str(column) for column in columns]
        explicit_set = set(explicit)
        ordered = list(explicit)
        ordered.extend(column for column in order if column not in explicit_set)
    else:
        ordered = order

    return ordered[:MAX_COLUMNS], normalized


def rows_to_dataset(
    rows: list[dict[str, Any]] | None,
    *,
    dataset_id: str = "main",
    columns: list[str] | None = None,
    column_labels: dict[str, str] | None = None,
    max_rows: int | None = MAX_DATASET_ROWS,
    source: dict[str, Any] | None = None,
) -> ReportDataset:
    """Build a truncated, JSON-safe dataset and record the pre-truncation row count."""
    ordered, normalized = coerce_rows(rows, columns=columns)
    total = len(normalized)
    if max_rows is not None and max_rows > 0:
        trimmed = normalized[:max_rows]
    else:
        trimmed = normalized

    merged_source: dict[str, Any] = dict(source or {})
    merged_source.setdefault("row_count", total)

    return ReportDataset(
        id=dataset_id,
        columns=ordered,
        rows=trimmed,
        column_labels=dict(column_labels or {}),
        source=merged_source,
    )

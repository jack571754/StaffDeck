"""Spec -> ``ReportDocument``: the flat, caller-facing way to build a report.

``write_report_html`` takes a fully-formed ``ReportDocument``. That contract is
deliberately dumb — pre-formatted cell strings, dataset ids, block wiring — which is
the wrong shape to ask a model, a pipeline step, or an HTTP caller for. This module is
the single small translation layer between the two: a flat spec (title + rows +
optional summary/KPIs/renderer) becomes a document through the registered renderer, so
no caller ever hand-builds datasets and blocks.

Design rules:

* **One renderer call.** Row shaping, truncation, column order, and JSON safety stay in
  ``renderers.base`` — this module adds blocks, it does not re-shape rows.
* **Fail loudly.** Unknown keys, wrong types, and an empty title raise
  ``ReportDocumentError`` instead of silently producing a report that ignores half the
  request. A caller that passes ``{"titel": ...}`` deserves an error, not a blank page.
* **Pure.** No IO, no database, no clock beyond the renderer's own default.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import replace
from typing import Any

from app.reporting.document import (
    MAX_DATASET_ROWS,
    DeltaTone,
    KpiItem,
    ReportBlock,
    ReportDocument,
)
from app.reporting.errors import ReportDocumentError
from app.reporting.renderers import get_report_renderer

# Every accepted spec key. Anything else is a caller bug (see the module docstring).
SPEC_KEYS: frozenset[str] = frozenset(
    {
        "title",
        "subtitle",
        "renderer",
        "rows",
        "columns",
        "column_labels",
        "summary",
        "kpis",
        "source",
        "max_rows",
    }
)

# Input guard, deliberately larger than MAX_DATASET_ROWS: the renderer truncates what it
# renders, but a 100k-row payload is a modelling mistake worth rejecting at the door
# rather than normalizing into memory first.
MAX_SPEC_ROWS = 5000

_TONE_BY_NAME: dict[str, DeltaTone] = {"up": "up", "down": "down", "flat": "flat"}


def build_report_from_spec(
    spec: Mapping[str, Any],
    *,
    tenant_id: str = "",
) -> ReportDocument:
    """Build one ``ReportDocument`` from a flat report spec.

    Raises ``ReportDocumentError`` for a malformed spec. The returned document is
    renderable as-is: ``render_report_html`` re-validates it anyway.
    """

    if not isinstance(spec, Mapping):
        raise ReportDocumentError("报告配置必须是对象。")

    unknown = sorted({str(key) for key in spec} - SPEC_KEYS)
    if unknown:
        raise ReportDocumentError(f"报告配置包含未知字段：{', '.join(unknown)}")

    title = _text(spec.get("title"))
    if not title:
        raise ReportDocumentError("报告标题不能为空。")

    renderer = get_report_renderer(_text(spec.get("renderer")))
    document = renderer(
        _rows(spec.get("rows")),
        title=title,
        subtitle=_text(spec.get("subtitle")),
        columns=_columns(spec.get("columns")),
        column_labels=_labels(spec.get("column_labels")),
        max_rows=_max_rows(spec.get("max_rows")),
        source=_mapping(spec.get("source"), field="source"),
        tenant_id=tenant_id,
    )

    leading = _leading_blocks(spec)
    if not leading:
        return document
    return replace(document, blocks=[*leading, *document.blocks])


def _leading_blocks(spec: Mapping[str, Any]) -> list[ReportBlock]:
    """Blocks that render above the renderer's own content: summary, then KPIs."""

    blocks: list[ReportBlock] = []
    summary = _text(spec.get("summary"))
    if summary:
        blocks.append(ReportBlock(kind="markdown", text=summary))
    kpis = _kpis(spec.get("kpis"))
    if kpis:
        blocks.append(ReportBlock(kind="kpi_row", kpis=kpis))
    return blocks


def _text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _rows(value: Any) -> list[dict[str, Any]]:
    if value is None:
        return []
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise ReportDocumentError("rows 必须是对象数组。")
    if len(value) > MAX_SPEC_ROWS:
        raise ReportDocumentError(
            f"rows 数量 {len(value)} 超出上限 {MAX_SPEC_ROWS}；请先聚合或在取数侧裁剪。"
        )
    return [dict(item) for item in value if isinstance(item, Mapping)]


def _columns(value: Any) -> list[str] | None:
    if value is None:
        return None
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise ReportDocumentError("columns 必须是字符串数组。")
    columns = [_text(item) for item in value]
    return [column for column in columns if column]


def _labels(value: Any) -> dict[str, str] | None:
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise ReportDocumentError("column_labels 必须是对象。")
    return {str(key): str(label) for key, label in value.items()}


def _max_rows(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ReportDocumentError("max_rows 必须是正整数。")
    return min(value, MAX_DATASET_ROWS)


def _mapping(value: Any, *, field: str) -> dict[str, Any] | None:
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise ReportDocumentError(f"{field} 必须是对象。")
    return {str(key): item for key, item in value.items()}


def _kpis(value: Any) -> list[KpiItem]:
    if value is None:
        return []
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise ReportDocumentError("kpis 必须是对象数组。")

    items: list[KpiItem] = []
    for raw in value:
        if not isinstance(raw, Mapping):
            continue
        label = _text(raw.get("label"))
        if not label:
            continue
        raw_tone = _text(raw.get("delta_tone"))
        tone = _TONE_BY_NAME.get(raw_tone)
        if raw_tone and tone is None:
            raise ReportDocumentError(
                f"KPI {label!r} 的 delta_tone 非法：{raw_tone!r}（可选 up/down/flat）"
            )
        items.append(
            KpiItem(
                label=label,
                value=_text(raw.get("value")),
                delta=_text(raw.get("delta")) or None,
                delta_tone=tone,
                hint=_text(raw.get("hint")) or None,
            )
        )
    return items


__all__ = [
    "MAX_SPEC_ROWS",
    "SPEC_KEYS",
    "build_report_from_spec",
]

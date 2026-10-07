"""ReportDocument -> self-contained single-file HTML.

Design notes
------------
* **One escaping exit.** Every value that reaches the markup goes through ``_esc``.
  There is no raw-HTML passthrough anywhere in this module, which is what makes the
  "inline this under ``sandbox allow-scripts``" decision auditable.
* **The data island is metadata only.** Sorting/filtering run against the
  server-rendered ``<table>`` DOM using ``data-v`` attributes, so embedding the rows
  a second time would double the payload for no functional gain. Rows are what
  dominate report size, so we keep only column metadata in the island.
* **Zero external resources.** No CDN fonts/scripts/styles. This satisfies the
  offline-archive requirement and needs no CSP relaxation: ``_HTML_INLINE_CSP`` in
  ``api/chat.py`` already allows ``script-src 'unsafe-inline'`` / ``style-src
  'unsafe-inline'``.
* **Charts are server-rendered inline SVG** (bar/line), with ``<title>`` elements for
  native hover tooltips, so no charting library is involved.
"""

from __future__ import annotations

import html
import json
import re
from datetime import date, datetime
from typing import Any

from app.reporting.assets import REPORT_CSS, REPORT_JS
from app.reporting.document import (
    ReportBlock,
    ReportDataset,
    ReportDocument,
    json_safe,
    validate_document,
)
from app.reporting.errors import ReportDocumentError
from app.reporting.format import format_cell

ISLAND_ID = "staffdeck-report-data"

# Series palette. Distinguishable in both light and dark mode.
SERIES_COLORS = ("#2563eb", "#f59e0b", "#10b981", "#8b5cf6", "#ef4444", "#0ea5e9")

_BOLD_RE = re.compile(r"\*\*(.+?)\*\*")
_NUMERIC_TYPES = (int, float)

# SVG canvas geometry (user units; the element scales to the container width).
_SVG_W = 760.0
_SVG_H = 280.0
_SVG_PAD_L = 56.0
_SVG_PAD_R = 16.0
_SVG_PAD_T = 18.0
_SVG_PAD_B = 46.0
_MAX_CATEGORY_LABELS = 16


def _esc(value: Any) -> str:
    """The single escaping exit for this module."""
    if value is None:
        return ""
    return html.escape(str(value), quote=True)


def _json_island(payload: dict[str, Any]) -> str:
    """Serialize the island so it cannot terminate the surrounding <script>.

    ``<`` and ``>`` are unicode-escaped rather than just ``</``. That is stricter
    than needed for ``</script>`` alone and also rules out the ``<!--`` parser state
    that can swallow the element's real closing tag. ``json.dumps`` never emits a
    bare ``<`` in its own syntax, so this only ever touches string content.
    """
    raw = json.dumps(json_safe(payload), ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    return raw.replace("<", "\\u003c").replace(">", "\\u003e")


def _as_number(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, _NUMERIC_TYPES):
        return float(value)
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return None


def _column_is_numeric(dataset: ReportDataset, column: str) -> bool:
    seen = False
    for row in dataset.rows:
        value = row.get(column)
        if value is None or value == "":
            continue
        seen = True
        if isinstance(value, bool) or not isinstance(value, _NUMERIC_TYPES):
            return False
    return seen


def _block_columns(block: ReportBlock, dataset: ReportDataset) -> list[str]:
    if block.columns:
        return [c for c in block.columns if c in dataset.columns]
    return list(dataset.columns)


def _block_rows(block: ReportBlock, dataset: ReportDataset) -> list[dict[str, Any]]:
    if block.max_rows is not None and block.max_rows >= 0:
        return dataset.rows[: block.max_rows]
    return dataset.rows


def _svg_num(value: float) -> str:
    if abs(value) >= 1000:
        return f"{value:,.0f}"
    if abs(value) >= 1:
        return f"{value:.1f}"
    return f"{value:.2f}"


def _truncation_note(dataset: ReportDataset, shown: int) -> str:
    total = dataset.source.get("row_count")
    if isinstance(total, int) and total > shown:
        return f"仅展示前 {shown} 行（共 {total} 行）。"
    return ""


# --------------------------------------------------------------------------- blocks


def _emit_heading(block: ReportBlock) -> str:
    level = block.level if block.level in (1, 2, 3) else 2
    tag = f"h{level}"
    return f"<{tag}>{_esc(block.text)}</{tag}>"


def _emit_markdown(block: ReportBlock) -> str:
    lines = [_BOLD_RE.sub(r"<strong>\1</strong>", _esc(line)) for line in (block.text or "").splitlines()]
    body = "<br>".join(lines) if lines else ""
    return f"<p>{body}</p>"


def _emit_note(block: ReportBlock) -> str:
    return f'<p class="note">{_esc(block.text)}</p>'


def _emit_divider(_block: ReportBlock) -> str:
    return "<hr>"


def _emit_kpi_row(block: ReportBlock) -> str:
    tiles: list[str] = []
    for kpi in block.kpis or []:
        parts = [
            '<div class="kpi">',
            f'<div class="kpi-label">{_esc(kpi.label)}</div>',
            f'<div class="kpi-value">{_esc(kpi.value)}</div>',
        ]
        if kpi.delta:
            tone = kpi.delta_tone or "flat"
            parts.append(f'<div class="kpi-delta tone-{_esc(tone)}">{_esc(kpi.delta)}</div>')
        if kpi.hint:
            parts.append(f'<div class="kpi-hint">{_esc(kpi.hint)}</div>')
        parts.append("</div>")
        tiles.append("".join(parts))
    return f'<div class="kpi-grid">{"".join(tiles)}</div>'


def _emit_table(block: ReportBlock, dataset: ReportDataset, table_id: str) -> str:
    columns = _block_columns(block, dataset)
    rows = _block_rows(block, dataset)
    numeric = {column: _column_is_numeric(dataset, column) for column in columns}

    head_cells = []
    for index, column in enumerate(columns):
        cls = ' class="num"' if numeric[column] else ""
        head_cells.append(f'<th{cls} data-col="{index}">{_esc(dataset.label_for(column))}</th>')

    body_rows = []
    for row in rows:
        cells = []
        for column in columns:
            raw = row.get(column)
            sort_value = "" if raw is None else str(raw)
            cls = ' class="num"' if numeric[column] else ""
            cells.append(f'<td{cls} data-v="{_esc(sort_value)}">{_esc(format_cell(raw))}</td>')
        body_rows.append(f"<tr>{''.join(cells)}</tr>")

    tools = ""
    if block.filterable:
        tools = (
            '<div class="table-tools">'
            f'<input type="search" placeholder="筛选…" aria-label="筛选表格" '
            f'data-report-filter="{_esc(table_id)}">'
            "</div>"
        )

    note = _truncation_note(dataset, len(rows))
    note_html = f'<p class="note">{_esc(note)}</p>' if note else ""
    empty = '<p class="note">没有可展示的数据行。</p>' if not rows else ""

    return (
        f'<div class="card">{tools}<div class="table-wrap">'
        f'<table data-report-table="{_esc(table_id)}">'
        f"<thead><tr>{''.join(head_cells)}</tr></thead>"
        f"<tbody>{''.join(body_rows)}</tbody>"
        "</table></div></div>"
        f"{empty}{note_html}"
    )


def _series_values(
    block: ReportBlock, dataset: ReportDataset, rows: list[dict[str, Any]]
) -> tuple[list[str], list[str], list[list[float]]]:
    categories = [format_cell(row.get(block.x)) for row in rows]
    series = list(block.y or [])
    values: list[list[float]] = []
    for column in series:
        values.append([_as_number(row.get(column)) or 0.0 for row in rows])
    return categories, series, values


def _chart_scale(values: list[list[float]]) -> float:
    flat = [v for group in values for v in group]
    peak = max(flat) if flat else 0.0
    return peak if peak > 0 else 1.0


def _category_labels(categories: list[str]) -> dict[int, str]:
    count = len(categories)
    if count <= _MAX_CATEGORY_LABELS:
        step = 1
    else:
        step = (count + _MAX_CATEGORY_LABELS - 1) // _MAX_CATEGORY_LABELS
    return {i: categories[i] for i in range(count) if i % step == 0}


def _emit_bar_chart(block: ReportBlock, dataset: ReportDataset) -> str:
    rows = _block_rows(block, dataset)
    categories, series, values = _series_values(block, dataset, rows)
    if not rows or not series:
        return '<p class="note">没有可绘制的数据。</p>'

    scale = _chart_scale(values)
    plot_w = _SVG_W - _SVG_PAD_L - _SVG_PAD_R
    plot_h = _SVG_H - _SVG_PAD_T - _SVG_PAD_B
    group_w = plot_w / max(1, len(categories))
    bar_w = max(1.0, min(26.0, group_w / max(1, len(series)) * 0.72))

    parts: list[str] = []
    for cat_index in range(len(categories)):
        group_x = _SVG_PAD_L + cat_index * group_w
        for s_index in range(len(series)):
            value = values[s_index][cat_index]
            height = max(0.0, (value / scale) * plot_h)
            bar_x = group_x + (group_w - bar_w * len(series)) / 2.0 + s_index * bar_w
            bar_y = _SVG_PAD_T + plot_h - height
            parts.append(
                f'<rect x="{bar_x:.1f}" y="{bar_y:.1f}" width="{bar_w:.1f}" '
                f'height="{height:.1f}" rx="2" fill="{SERIES_COLORS[s_index % len(SERIES_COLORS)]}">'
                f"<title>{_esc(series[s_index])} · {_esc(categories[cat_index])}: "
                f"{_esc(_svg_num(value))}</title></rect>"
            )

    labels = _category_labels(categories)
    for cat_index, text in labels.items():
        cx = _SVG_PAD_L + cat_index * group_w + group_w / 2.0
        parts.append(
            f'<text class="axis-label" x="{cx:.1f}" y="{_SVG_H - _SVG_PAD_B + 16:.1f}" '
            f'text-anchor="middle">{_esc(text)}</text>'
        )

    parts.append(
        f'<line class="axis-line" x1="{_SVG_PAD_L:.1f}" y1="{_SVG_PAD_T + plot_h:.1f}" '
        f'x2="{_SVG_W - _SVG_PAD_R:.1f}" y2="{_SVG_PAD_T + plot_h:.1f}"></line>'
    )
    parts.append(
        f'<text class="axis-label" x="{_SVG_PAD_L - 8:.1f}" y="{_SVG_PAD_T + 10:.1f}" '
        f'text-anchor="end">{_esc(_svg_num(scale))}</text>'
    )

    return (
        f'<div class="card chart-card"><svg viewBox="0 0 {_SVG_W:.0f} {_SVG_H:.0f}" '
        f'role="img" aria-label="{_esc(block.text or "柱状图")}">{"".join(parts)}</svg>'
        f"{_chart_legend(series)}</div>"
    )


def _emit_line_chart(block: ReportBlock, dataset: ReportDataset) -> str:
    rows = _block_rows(block, dataset)
    categories, series, values = _series_values(block, dataset, rows)
    if not rows or not series:
        return '<p class="note">没有可绘制的数据。</p>'

    scale = _chart_scale(values)
    plot_w = _SVG_W - _SVG_PAD_L - _SVG_PAD_R
    plot_h = _SVG_H - _SVG_PAD_T - _SVG_PAD_B
    count = len(categories)
    step = plot_w / (count - 1) if count > 1 else 0.0

    parts: list[str] = []
    for s_index in range(len(series)):
        color = SERIES_COLORS[s_index % len(SERIES_COLORS)]
        points: list[str] = []
        dots: list[str] = []
        for cat_index in range(count):
            value = values[s_index][cat_index]
            x = _SVG_PAD_L + cat_index * step if count > 1 else _SVG_PAD_L + plot_w / 2.0
            y = _SVG_PAD_T + plot_h - max(0.0, (value / scale) * plot_h)
            points.append(f"{x:.1f},{y:.1f}")
            dots.append(
                f'<circle cx="{x:.1f}" cy="{y:.1f}" r="3" fill="{color}">'
                f"<title>{_esc(series[s_index])} · {_esc(categories[cat_index])}: "
                f"{_esc(_svg_num(value))}</title></circle>"
            )
        parts.append(
            f'<polyline points="{" ".join(points)}" fill="none" stroke="{color}" '
            'stroke-width="2" stroke-linejoin="round"></polyline>'
        )
        parts.extend(dots)

    for cat_index, text in _category_labels(categories).items():
        x = _SVG_PAD_L + cat_index * step if count > 1 else _SVG_PAD_L + plot_w / 2.0
        parts.append(
            f'<text class="axis-label" x="{x:.1f}" y="{_SVG_H - _SVG_PAD_B + 16:.1f}" '
            f'text-anchor="middle">{_esc(text)}</text>'
        )

    parts.append(
        f'<line class="axis-line" x1="{_SVG_PAD_L:.1f}" y1="{_SVG_PAD_T + plot_h:.1f}" '
        f'x2="{_SVG_W - _SVG_PAD_R:.1f}" y2="{_SVG_PAD_T + plot_h:.1f}"></line>'
    )
    parts.append(
        f'<text class="axis-label" x="{_SVG_PAD_L - 8:.1f}" y="{_SVG_PAD_T + 10:.1f}" '
        f'text-anchor="end">{_esc(_svg_num(scale))}</text>'
    )

    return (
        f'<div class="card chart-card"><svg viewBox="0 0 {_SVG_W:.0f} {_SVG_H:.0f}" '
        f'role="img" aria-label="{_esc(block.text or "折线图")}">{"".join(parts)}</svg>'
        f"{_chart_legend(series)}</div>"
    )


def _chart_legend(series: list[str]) -> str:
    items = []
    for index, name in enumerate(series):
        color = SERIES_COLORS[index % len(SERIES_COLORS)]
        items.append(
            f'<span><span class="swatch" style="background:{color}"></span>{_esc(name)}</span>'
        )
    return f'<div class="chart-legend">{"".join(items)}</div>'


_EMITTERS: dict[str, Any] = {
    "heading": lambda block, _dataset, _tid: _emit_heading(block),
    "markdown": lambda block, _dataset, _tid: _emit_markdown(block),
    "note": lambda block, _dataset, _tid: _emit_note(block),
    "divider": lambda block, _dataset, _tid: _emit_divider(block),
    "kpi_row": lambda block, _dataset, _tid: _emit_kpi_row(block),
    "table": _emit_table,
    "bar_chart": lambda block, _dataset, _tid: _emit_bar_chart(block, _dataset),
    "line_chart": lambda block, _dataset, _tid: _emit_line_chart(block, _dataset),
}


# --------------------------------------------------------------------------- shell


def _render_header(document: ReportDocument) -> str:
    meta = document.meta
    bits: list[str] = []
    generated = meta.generated_at
    if isinstance(generated, datetime):
        bits.append(f"生成时间：{generated.isoformat(sep=' ', timespec='seconds')}")
    elif isinstance(generated, date):
        bits.append(f"生成时间：{generated.isoformat()}")
    for key, value in (meta.source or {}).items():
        bits.append(f"{key}：{format_cell(value)}")
    subtitle = f'<p class="subtitle">{_esc(meta.subtitle)}</p>' if meta.subtitle else ""
    meta_html = "".join(f"<span>{_esc(bit)}</span>" for bit in bits)
    return (
        '<header class="report-head"><div class="wrap">'
        f"<h1>{_esc(meta.title)}</h1>{subtitle}"
        f'<div class="meta">{meta_html}</div>'
        "</div></header>"
    )


def _render_footer(document: ReportDocument) -> str:
    return (
        '<footer class="report-foot"><div class="wrap">'
        f"{_esc(document.meta.generator)}"
        "</div></footer>"
    )


def build_island(document: ReportDocument) -> dict[str, Any]:
    """Metadata-only payload consumed by REPORT_JS."""
    tables: dict[str, Any] = {}
    for index, block in enumerate(document.blocks):
        if block.kind != "table" or not block.dataset_id:
            continue
        dataset = next((d for d in document.datasets if d.id == block.dataset_id), None)
        if dataset is None:
            continue
        columns = _block_columns(block, dataset)
        tables[f"t{index}"] = {
            "sortable": bool(block.sortable),
            "filterable": bool(block.filterable),
            "columns": [
                {
                    "key": column,
                    "label": dataset.label_for(column),
                    "numeric": _column_is_numeric(dataset, column),
                }
                for column in columns
            ],
        }
    return {
        "version": 1,
        "title": document.meta.title,
        "generatedAt": json_safe(document.meta.generated_at),
        "tables": tables,
    }


def render_report_html(document: ReportDocument) -> str:
    """Serialize a ReportDocument into a self-contained single-file HTML document."""
    validate_document(document)
    datasets = {dataset.id: dataset for dataset in document.datasets}

    body: list[str] = []
    for index, block in enumerate(document.blocks):
        emitter = _EMITTERS.get(block.kind)
        if emitter is None:  # pragma: no cover - validate_document already rejects these
            raise ReportDocumentError(f"未知内容块类型：{block.kind!r}")
        dataset = datasets.get(block.dataset_id) if block.dataset_id else None
        if block.kind in {"table", "bar_chart", "line_chart"} and dataset is None:
            raise ReportDocumentError(f"内容块 {index + 1}（{block.kind}）缺少可用数据集。")
        inner = emitter(block, dataset, f"t{index}")
        heading = ""
        if block.kind != "heading" and block.text and block.kind != "markdown":
            heading = f"<h2>{_esc(block.text)}</h2>"
        body.append(f'<section class="block">{heading}{inner}</section>')

    island = _json_island(build_island(document))
    return (
        "<!doctype html>\n"
        '<html lang="zh-CN">\n<head>\n'
        '<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width,initial-scale=1">\n'
        '<meta name="referrer" content="no-referrer">\n'
        '<meta name="robots" content="noindex,nofollow">\n'
        f"<title>{_esc(document.meta.title)}</title>\n"
        f"<style>{REPORT_CSS}</style>\n"
        "</head>\n<body>\n"
        f"{_render_header(document)}\n"
        f'<main class="wrap">{"".join(body)}</main>\n'
        f"{_render_footer(document)}\n"
        f'<script type="application/json" id="{ISLAND_ID}">{island}</script>\n'
        f"<script>{REPORT_JS}</script>\n"
        "</body>\n</html>\n"
    )


def block_kinds() -> frozenset[str]:
    """Block kinds the serializer can emit."""
    return frozenset(_EMITTERS)


__all__ = ["ISLAND_ID", "SERIES_COLORS", "block_kinds", "build_island", "render_report_html"]

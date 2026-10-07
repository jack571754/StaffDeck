"""Sales report renderer.

Semantic counterpart of the Feishu ``sales_card`` card (``renderers/sales_card.py``):
top KPI row, overall summary sentence, positive/negative increment shop rankings,
brand & channel breakdown table, and the 24-hour trend chart.

The row classification below is copied from ``build_sales_feishu_card`` on purpose —
the same dataset must read the same way whether it lands in a Feishu card or in a
shared HTML report. If the card's business definitions change, change them here too.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from app.reporting.document import (
    MAX_DATASET_ROWS,
    KpiItem,
    ReportBlock,
    ReportDocument,
    ReportMeta,
)
from app.reporting.format import format_metric_delta, format_metric_value, to_float
from app.reporting.renderers.base import default_generated_at, rows_to_dataset
from app.reporting.renderers.generic_table import build_generic_table_report

DEFAULT_TITLE = "实时销售播报"

BRAND_DATASET_ID = "brand_detail"
HOURLY_DATASET_ID = "hourly_trend"

BRAND_COLUMNS = (
    "brand",
    "net_sales",
    "net_diff",
    "ops_net_sales",
    "ops_diff",
    "live_net_sales",
    "live_diff",
)
BRAND_COLUMN_LABELS = {
    "brand": "品牌",
    "net_sales": "净销",
    "net_diff": "环比净销",
    "ops_net_sales": "运营端净销",
    "ops_diff": "环比净销",
    "live_net_sales": "达播端净销",
    "live_diff": "环比净销",
}
_HOURLY_CATEGORIES = ("24小时环比", "hourly_trend")


def _classify_rows(
    rows: list[dict[str, Any]],
) -> tuple[
    list[tuple[str, dict[str, Any]]],
    list[dict[str, Any]],
    dict[str, Any] | None,
    list[dict[str, Any]],
    list[dict[str, Any]],
]:
    """Mirror of the classification in ``build_sales_feishu_card``."""
    brand_rows: list[tuple[str, dict[str, Any]]] = []
    hourly_rows: list[dict[str, Any]] = []
    summary_row: dict[str, Any] | None = None
    pos_increment_shops: list[dict[str, Any]] = []
    neg_increment_shops: list[dict[str, Any]] = []

    for row in rows:
        category = str(row.get("category") or row.get("_channel") or "")
        item = str(row.get("item") or row.get("name") or "")
        if category in _HOURLY_CATEGORIES:
            hourly_rows.append(row)
        elif category == "大盘" and item == "电商整体":
            summary_row = row
            brand_rows.append(("电商整体", row))
        elif category in ("店铺正增量Top5", "正增量Top5"):
            pos_increment_shops.append(row)
        elif category in ("店铺负增量Top5", "负增量Top5"):
            neg_increment_shops.append(row)
        elif category == "可复美" and item == "整体":
            brand_rows.append(("可复美整体", row))
        elif category == "可复美":
            brand_rows.append((f"{item}可复美", row))
        elif category == "可丽金" and item == "整体":
            brand_rows.append(("可丽金整体", row))
        elif category == "可丽金":
            brand_rows.append((f"{item}可丽金", row))
        else:
            brand_rows.append((item or category, row))

    return brand_rows, hourly_rows, summary_row, pos_increment_shops, neg_increment_shops


def _resolve_update_time(rows: list[dict[str, Any]], summary_row: dict[str, Any] | None) -> str:
    raw: Any = None
    if summary_row:
        raw = (
            summary_row.get("数据更新时间")
            or summary_row.get("更新时间")
            or summary_row.get("data_time")
        )
    if not raw:
        for row in rows:
            candidate = row.get("数据更新时间") or row.get("更新时间") or row.get("data_time")
            if candidate:
                raw = candidate
                break
    if not raw:
        return datetime.now(UTC).strftime("%m-%d %H:%M")
    text = str(raw).strip()
    if len(text) >= 16 and text[4] == "-" and text[7] == "-":
        return text[5:16]
    return text


def _shop_name(row: dict[str, Any]) -> str:
    return str(
        row.get("item")
        or row.get("平台店铺")
        or row.get("name")
        or row.get("_identifier")
        or "未知店铺"
    )


def _shop_line(index: int, row: dict[str, Any]) -> str:
    name = _shop_name(row)
    net = format_metric_value(row.get("净销_万") or row.get("price"))
    delta = format_metric_delta(row.get("环比增量_万") or row.get("diff_amount"))[0]
    return f"{index}. **{name}**\n   增量: {delta} | 净销: {net}"


def _build_kpis(
    summary_row: dict[str, Any] | None, *, is_first_push: bool
) -> ReportBlock:
    def magnitude(*keys: str) -> float:
        if not summary_row:
            return 0.0
        for key in keys:
            if summary_row.get(key) is not None:
                return to_float(summary_row.get(key))
        return 0.0

    def delta(*keys: str) -> float:
        return 0.0 if is_first_push else magnitude(*keys)

    net_delta_text, net_tone = format_metric_delta(delta("环比增量_万", "diff_amount"))
    ops_delta_text, ops_tone = format_metric_delta(delta("运营增量_万"))
    live_delta_text, live_tone = format_metric_delta(delta("达播增量_万"))

    hint = "当日首次播报，增量计为 0" if is_first_push else None
    return ReportBlock(
        kind="kpi_row",
        kpis=[
            KpiItem(
                label="今日净销",
                value=format_metric_value(magnitude("净销_万", "price")),
                delta=net_delta_text,
                delta_tone=net_tone,
                hint=hint,
            ),
            KpiItem(
                label="达播净销",
                value=format_metric_value(magnitude("达播净销_万")),
                delta=live_delta_text,
                delta_tone=live_tone,
            ),
            KpiItem(
                label="运营端净销",
                value=format_metric_value(magnitude("运营净销_万")),
                delta=ops_delta_text,
                delta_tone=ops_tone,
            ),
        ],
    )


def _build_summary_text(
    summary_row: dict[str, Any] | None, *, update_time: str, is_first_push: bool
) -> str:
    def magnitude(*keys: str) -> float:
        if not summary_row:
            return 0.0
        for key in keys:
            if summary_row.get(key) is not None:
                return to_float(summary_row.get(key))
        return 0.0

    net = format_metric_value(magnitude("净销_万", "price"))
    ops = format_metric_value(magnitude("运营净销_万"))
    live = format_metric_value(magnitude("达播净销_万"))

    if is_first_push:
        return (
            f"📢 **整体销售播报**：截止 {update_time}，电商整体净销 **{net}**"
            f"（当日首次播报，增量计为 0.00万，不对比前一日数据），"
            f"其中运营端 **{ops}**；达播端 **{live}**，各渠道运行平稳。"
        )

    net_delta = format_metric_delta(magnitude("环比增量_万", "diff_amount"))[0]
    ops_delta = format_metric_delta(magnitude("运营增量_万"))[0]
    live_delta = format_metric_delta(magnitude("达播增量_万"))[0]
    return (
        f"📢 **整体销售播报**：截止 {update_time}，电商整体净销 **{net}**"
        f"（较上一时刻增量 {net_delta}，其中运营端 **{ops}**，较上一时刻增量 {ops_delta}；"
        f"达播端 **{live}**，较上一时刻增量 {live_delta}），各渠道运行平稳。"
    )


def _build_rank_blocks(
    pos_rows: list[dict[str, Any]],
    neg_rows: list[dict[str, Any]],
    *,
    is_first_push: bool,
) -> list[ReportBlock]:
    if is_first_push:
        return [
            ReportBlock(
                kind="note",
                text=(
                    "当日首次播报，增量统一计为 0.00万，不展示上一时刻店铺增量排行。"
                ),
            )
        ]
    if not pos_rows and not neg_rows:
        return []

    pos_lines = [_shop_line(i, row) for i, row in enumerate(pos_rows[:5], 1)]
    neg_lines = [_shop_line(i, row) for i, row in enumerate(neg_rows[:5], 1)]
    if not neg_lines:
        neg_lines = ["*（其余活跃店铺增量均为正或持平）*"]

    return [
        ReportBlock(kind="heading", level=2, text="店铺正负增量动态排行（较上一时刻 Top 5）"),
        ReportBlock(
            kind="markdown",
            text="📈 **正增量领跑 Top 5**\n" + ("\n".join(pos_lines) if pos_lines else "暂无"),
        ),
        ReportBlock(kind="markdown", text="📉 **负增量预警 Top 5**\n" + "\n".join(neg_lines)),
    ]


def _build_brand_dataset(
    brand_rows: list[tuple[str, dict[str, Any]]], *, is_first_push: bool
):
    detail_rows: list[dict[str, Any]] = []
    for label, row in brand_rows:
        detail_rows.append(
            {
                "brand": label,
                "net_sales": format_metric_value(row.get("净销_万") or row.get("price")),
                "net_diff": format_metric_delta(
                    0.0 if is_first_push else (row.get("环比增量_万") or row.get("diff_amount"))
                )[0],
                "ops_net_sales": format_metric_value(row.get("运营净销_万")),
                "ops_diff": format_metric_delta(
                    0.0 if is_first_push else row.get("运营增量_万")
                )[0],
                "live_net_sales": format_metric_value(row.get("达播净销_万")),
                "live_diff": format_metric_delta(
                    0.0 if is_first_push else row.get("达播增量_万")
                )[0],
            }
        )
    return rows_to_dataset(
        detail_rows,
        dataset_id=BRAND_DATASET_ID,
        columns=list(BRAND_COLUMNS),
        column_labels=BRAND_COLUMN_LABELS,
        max_rows=MAX_DATASET_ROWS,
    )


def _build_hourly_dataset(hourly_rows: list[dict[str, Any]], *, is_first_push: bool):
    series = ["今日"] if is_first_push else ["今日", "昨日"]
    chart_rows: list[dict[str, Any]] = []
    for row in hourly_rows:
        entry: dict[str, Any] = {
            "hour": str(row.get("item", "")),
            "今日": to_float(row.get("运营净销_万")),
        }
        if not is_first_push:
            entry["昨日"] = to_float(row.get("运营增量_万"))
        chart_rows.append(entry)
    dataset = rows_to_dataset(
        chart_rows,
        dataset_id=HOURLY_DATASET_ID,
        columns=["hour", *series],
        max_rows=24,
    )
    chart_title = (
        "24小时时段走势（今日累计，万元）"
        if is_first_push
        else "24小时时段走势环比（今日 vs 昨日，万元）"
    )
    return dataset, series, chart_title


def build_sales_report(
    rows: list[dict[str, Any]] | None = None,
    *,
    title: str = "",
    subtitle: str = "",
    is_first_push: bool = False,
    max_rows: int | None = MAX_DATASET_ROWS,
    source: dict[str, Any] | None = None,
    generated_at: datetime | None = None,
    tenant_id: str = "",
    **_kwargs: Any,
) -> ReportDocument:
    """Render a sales dataset as a KPI + rankings + breakdown + trend report.

    Falls back to the generic table report when the rows carry no sales markers, so a
    misconfigured renderer degrades to something readable instead of an empty shell.
    """
    all_rows = [row for row in (rows or []) if isinstance(row, dict)]
    brand_rows, hourly_rows, summary_row, pos_rows, neg_rows = _classify_rows(all_rows)

    if not summary_row and not pos_rows and not hourly_rows:
        return build_generic_table_report(
            all_rows,
            title=title,
            subtitle=subtitle,
            max_rows=max_rows,
            source=source,
            generated_at=generated_at,
            tenant_id=tenant_id,
        )

    update_time = _resolve_update_time(all_rows, summary_row)
    blocks: list[ReportBlock] = [
        _build_kpis(summary_row, is_first_push=is_first_push),
        ReportBlock(
            kind="markdown",
            text=_build_summary_text(summary_row, update_time=update_time, is_first_push=is_first_push),
        ),
    ]
    blocks.extend(_build_rank_blocks(pos_rows, neg_rows, is_first_push=is_first_push))

    datasets = [_build_brand_dataset(brand_rows, is_first_push=is_first_push)]
    blocks.append(
        ReportBlock(
            kind="table",
            text="品牌与渠道销售明细（万元）",
            dataset_id=BRAND_DATASET_ID,
            columns=list(BRAND_COLUMNS),
            max_rows=max_rows,
        )
    )

    if hourly_rows:
        hourly_dataset, series, chart_title = _build_hourly_dataset(
            hourly_rows, is_first_push=is_first_push
        )
        datasets.append(hourly_dataset)
        blocks.append(
            ReportBlock(
                kind="bar_chart",
                text=chart_title,
                dataset_id=HOURLY_DATASET_ID,
                x="hour",
                y=series,
            )
        )

    return ReportDocument(
        meta=ReportMeta(
            title=title.strip() or f"📊 {DEFAULT_TITLE} · {update_time}（当日累计）",
            generated_at=generated_at or default_generated_at(),
            tenant_id=tenant_id,
            subtitle=subtitle,
            source=dict(source or {}),
        ),
        datasets=datasets,
        blocks=blocks,
    )

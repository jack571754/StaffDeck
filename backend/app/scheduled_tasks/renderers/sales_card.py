"""Sales Feishu interactive card 2.0 renderer.

Renders real-time e-commerce sales broadcasts with:
1. Top KPI column set (Total net sales, live streaming, operations).
2. Overall sales summary text (with daily first-push zero-increment detection).
3. Dynamic Top 5 positive/negative increment shop rankings.
4. Brand & channel sales breakdown table (7-column adaptive layout).
5. 24-hour hourly trend chart (bar chart, today vs yesterday or cumulative).
6. Mentions and footer notes.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from app.scheduled_tasks.renderers.base import fmt_diff_styled, fmt_val_styled
from app.scheduled_tasks.renderers.generic_table import build_generic_feishu_card


def build_sales_feishu_card(
    rows: list[dict[str, Any]],
    title: str = "",
    template: str = "blue",
    *,
    is_first_push: bool = False,
    at_users: list[dict[str, Any]] | None = None,
    should_at_all: bool = False,
    **_kwargs: Any,
) -> dict[str, Any]:
    """Assemble native Feishu interactive card 2.0 based on real-time sales query rows."""
    brand_rows: list[tuple[str, dict[str, Any]]] = []
    hourly_rows: list[dict[str, Any]] = []
    summary_row: dict[str, Any] | None = None
    pos_increment_shops: list[dict[str, Any]] = []
    neg_increment_shops: list[dict[str, Any]] = []

    for r in rows:
        cat = str(r.get("category") or r.get("_channel") or "")
        item = str(r.get("item") or r.get("name") or "")
        if cat in ("24小时环比", "hourly_trend"):
            hourly_rows.append(r)
        elif cat == "大盘" and item == "电商整体":
            summary_row = r
            brand_rows.append(("电商整体", r))
        elif cat in ("店铺正增量Top5", "正增量Top5"):
            pos_increment_shops.append(r)
        elif cat in ("店铺负增量Top5", "负增量Top5"):
            neg_increment_shops.append(r)
        elif cat == "可复美" and item == "整体":
            brand_rows.append(("可复美整体", r))
        elif cat == "可复美":
            brand_rows.append((f"{item}可复美", r))
        elif cat == "可丽金" and item == "整体":
            brand_rows.append(("可丽金整体", r))
        elif cat == "可丽金":
            brand_rows.append((f"{item}可丽金", r))
        else:
            brand_rows.append((item or cat, r))

    # If this is not a sales dataset (no summary row, no shops, no hourly trend), fall back to generic card
    if not summary_row and not pos_increment_shops and not hourly_rows:
        return build_generic_feishu_card(
            rows,
            title=title,
            template=template,
            at_users=at_users,
            should_at_all=should_at_all,
        )

    tot_net = float(summary_row.get("净销_万") or summary_row.get("price") or 0) if summary_row else 0.0
    tot_ops = float(summary_row.get("运营净销_万") or 0) if summary_row else 0.0
    tot_live = float(summary_row.get("达播净销_万") or 0) if summary_row else 0.0
    tot_diff = 0.0 if is_first_push else (float(summary_row.get("环比增量_万") or summary_row.get("diff_amount") or 0) if summary_row else 0.0)
    tot_ops_diff = 0.0 if is_first_push else (float(summary_row.get("运营增量_万") or 0) if summary_row else 0.0)
    tot_live_diff = 0.0 if is_first_push else (float(summary_row.get("达播增量_万") or 0) if summary_row else 0.0)

    update_time = None
    if summary_row:
        update_time = (
            summary_row.get("数据更新时间")
            or summary_row.get("更新时间")
            or summary_row.get("data_time")
        )
    if not update_time and rows:
        for r in rows:
            t_val = r.get("数据更新时间") or r.get("更新时间") or r.get("data_time")
            if t_val:
                update_time = str(t_val).strip()
                break

    if not update_time:
        update_time = datetime.now(UTC).strftime("%m-%d %H:%M")
    elif len(update_time) >= 16 and update_time[4] == "-" and update_time[7] == "-":
        update_time = update_time[5:16]

    card_title = title or f"📊 实时销售播报 · {update_time}（当日累计）"

    # Assemble elements
    elements: list[dict[str, Any]] = [
        {
            "tag": "markdown",
            "content": f"**实时汇总** <font color='grey'>（数据更新至 {update_time}）</font>",
            "text_align": "left",
            "text_size": "normal",
        },
        {
            "tag": "column_set",
            "flex_mode": "bisect",
            "background_style": "grey",
            "horizontal_spacing": "8px",
            "horizontal_align": "left",
            "columns": [
                {
                    "tag": "column",
                    "width": "weighted",
                    "weight": 1,
                    "vertical_align": "top",
                    "vertical_spacing": "8px",
                    "elements": [
                        {
                            "tag": "markdown",
                            "content": (
                                f"<font color='grey'>今日净销</font>\n"
                                f"**{tot_net:.2f}万**\n"
                                f"{fmt_diff_styled(tot_diff)}"
                            ),
                            "text_align": "center",
                            "text_size": "normal",
                        }
                    ],
                },
                {
                    "tag": "column",
                    "width": "weighted",
                    "weight": 1,
                    "vertical_align": "top",
                    "vertical_spacing": "8px",
                    "elements": [
                        {
                            "tag": "markdown",
                            "content": (
                                f"<font color='grey'>达播净销</font>\n"
                                f"**{tot_live:.2f}万**\n"
                                f"{fmt_diff_styled(tot_live_diff)}"
                            ),
                            "text_align": "center",
                            "text_size": "normal",
                        }
                    ],
                },
                {
                    "tag": "column",
                    "width": "weighted",
                    "weight": 1,
                    "vertical_align": "top",
                    "vertical_spacing": "8px",
                    "elements": [
                        {
                            "tag": "markdown",
                            "content": (
                                f"<font color='grey'>运营端净销</font>\n"
                                f"**{tot_ops:.2f}万**\n"
                                f"{fmt_diff_styled(tot_ops_diff)}"
                            ),
                            "text_align": "center",
                            "text_size": "normal",
                        }
                    ],
                },
            ],
        },
        {
            "tag": "markdown",
            "content": (
                (
                    f"📢 **整体销售播报**：截止 {update_time}，电商整体净销 **{tot_net:.2f}万**"
                    f"（当日首次播报，增量计为 <font color='grey'>0.00万</font>，不对比前一日数据），"
                    f"其中运营端 **{tot_ops:.2f}万**（增量 <font color='grey'>0.00万</font>）；"
                    f"达播端 **{tot_live:.2f}万**（增量 <font color='grey'>0.00万</font>），各渠道运行平稳。"
                )
                if is_first_push
                else (
                    f"📢 **整体销售播报**：截止 {update_time}，电商整体净销 **{tot_net:.2f}万**"
                    f"（较上一时刻增量 {fmt_diff_styled(tot_diff, is_core=True)}，"
                    f"其中运营端 **{tot_ops:.2f}万**，较上一时刻增量 {fmt_diff_styled(tot_ops_diff, is_core=True)}；"
                    f"达播端 **{tot_live:.2f}万**，较上一时刻增量 {fmt_diff_styled(tot_live_diff, is_core=True)}），各渠道运行平稳。"
                )
            ),
            "text_align": "left",
            "text_size": "normal",
        },
    ]

    # Dynamic Top 5 shops
    if not is_first_push and (pos_increment_shops or neg_increment_shops):
        pos_lines = []
        for i, s in enumerate(pos_increment_shops[:5], 1):
            name = s.get("item") or s.get("平台店铺") or s.get("name") or s.get("_identifier") or "未知店铺"
            net = float(s.get("净销_万") or s.get("price") or 0)
            diff = float(s.get("环比增量_万") or s.get("diff_amount") or 0)
            pos_lines.append(f"{i}. **{name}**\n   增量: <font color='green'>**+{diff:.2f}万**</font> | 净销: {net:.2f}万")

        neg_lines = []
        for i, s in enumerate(neg_increment_shops[:5], 1):
            name = s.get("item") or s.get("平台店铺") or s.get("name") or s.get("_identifier") or "未知店铺"
            net = float(s.get("净销_万") or s.get("price") or 0)
            diff = float(s.get("环比增量_万") or s.get("diff_amount") or 0)
            diff_str = f"{diff:.2f}万" if diff < 0 else f"-{abs(diff):.2f}万"
            neg_lines.append(f"{i}. **{name}**\n   增量: <font color='red'>**{diff_str}**</font> | 净销: {net:.2f}万")

        if not neg_lines:
            neg_lines = ["*(其余活跃店铺增量均为正或持平)*"]

        elements.extend([
            {
                "tag": "markdown",
                "content": "**🏪 实时销售数据汇报：店铺正负增量动态排行（较上一时刻 Top 5）**",
                "text_align": "left",
                "text_size": "normal",
            },
            {
                "tag": "column_set",
                "flex_mode": "bisect",
                "background_style": "grey",
                "horizontal_spacing": "12px",
                "columns": [
                    {
                        "tag": "column",
                        "width": "weighted",
                        "weight": 1,
                        "vertical_align": "top",
                        "vertical_spacing": "4px",
                        "elements": [
                            {
                                "tag": "markdown",
                                "content": "📈 <font color='green'>**正增量领跑 Top 5**</font>\n" + ("\n".join(pos_lines) if pos_lines else "暂无"),
                                "text_size": "normal",
                            }
                        ],
                    },
                    {
                        "tag": "column",
                        "width": "weighted",
                        "weight": 1,
                        "vertical_align": "top",
                        "vertical_spacing": "4px",
                        "elements": [
                            {
                                "tag": "markdown",
                                "content": "📉 <font color='red'>**负增量预警 Top 5**</font>\n" + "\n".join(neg_lines),
                                "text_size": "normal",
                            }
                        ],
                    },
                ],
            },
        ])
    elif is_first_push:
        elements.extend([
            {
                "tag": "markdown",
                "content": "**🏪 实时销售数据汇报：店铺正负增量动态排行**\n<font color='grey'>*(当日首次播报，增量统一计为 0.00万，不展示上一时刻店铺增量排行)*</font>",
                "text_align": "left",
                "text_size": "normal",
            },
            {"tag": "hr"},
        ])

    # Table rows
    table_rows: list[dict[str, Any]] = []
    for display_name, r in brand_rows:
        is_core_row = display_name in ("电商整体", "可复美整体", "可丽金整体")
        brand_label = f"**{display_name}**" if is_core_row else display_name
        net_diff_val = 0.0 if is_first_push else (r.get("环比增量_万") or r.get("diff_amount"))
        ops_diff_val = 0.0 if is_first_push else r.get("运营增量_万")
        live_diff_val = 0.0 if is_first_push else r.get("达播增量_万")
        table_rows.append({
            "brand": brand_label,
            "net_sales": fmt_val_styled(r.get("净销_万") or r.get("price"), is_core=is_core_row),
            "net_diff": fmt_diff_styled(net_diff_val, is_core=is_core_row),
            "ops_net_sales": fmt_val_styled(r.get("运营净销_万"), is_core=is_core_row),
            "ops_diff": fmt_diff_styled(ops_diff_val, is_core=is_core_row),
            "live_net_sales": fmt_val_styled(r.get("达播净销_万"), is_core=is_core_row),
            "live_diff": fmt_diff_styled(live_diff_val, is_core=is_core_row),
        })

    elements.extend([
        {
            "tag": "markdown",
            "content": "**品牌与渠道销售明细（万元）**",
            "text_align": "left",
        },
        {
            "tag": "table",
            "columns": [
                {"data_type": "lark_md", "name": "brand", "display_name": "品牌", "horizontal_align": "left", "width": "auto"},
                {"data_type": "lark_md", "name": "net_sales", "display_name": "净销", "horizontal_align": "right", "width": "auto"},
                {"data_type": "lark_md", "name": "net_diff", "display_name": "环比净销", "horizontal_align": "right", "width": "auto"},
                {"data_type": "lark_md", "name": "ops_net_sales", "display_name": "运营端净销", "horizontal_align": "right", "width": "auto"},
                {"data_type": "lark_md", "name": "ops_diff", "display_name": "环比净销", "horizontal_align": "right", "width": "auto"},
                {"data_type": "lark_md", "name": "live_net_sales", "display_name": "达播端净销", "horizontal_align": "right", "width": "auto"},
                {"data_type": "lark_md", "name": "live_diff", "display_name": "环比净销", "horizontal_align": "right", "width": "auto"},
            ],
            "rows": table_rows,
            "row_height": "low",
            "freeze_first_column": False,
            "header_style": {
                "background_style": "grey",
                "bold": True,
                "lines": 1,
            },
            "page_size": 10,
        },
    ])

    # 24h Hourly chart
    if hourly_rows:
        chart_values = []
        if is_first_push:
            for hr in hourly_rows:
                h_str = str(hr.get("item", ""))
                v_today = float(hr.get("运营净销_万") or 0)
                chart_values.append({"hour": h_str, "type": "今日", "value": v_today})
            chart_title = "24小时时段走势（今日累计，万元）"
        else:
            for hr in hourly_rows:
                h_str = str(hr.get("item", ""))
                v_today = float(hr.get("运营净销_万") or 0)
                v_yest = float(hr.get("运营增量_万") or 0)
                chart_values.append({"hour": h_str, "type": "今日", "value": v_today})
                chart_values.append({"hour": h_str, "type": "昨日", "value": v_yest})
            chart_title = "24小时时段走势环比（今日 vs 昨日，万元）"

        elements.append({
            "tag": "chart",
            "chart_spec": {
                "type": "bar",
                "title": {
                    "text": chart_title,
                },
                "data": {
                    "values": chart_values,
                },
                "xField": [
                    "hour",
                    "type",
                ],
                "yField": "value",
                "seriesField": "type",
                "legends": {
                    "visible": True,
                    "orient": "bottom",
                },
            },
            "preview": True,
            "color_theme": "converse",
            "height": "300px",
        })

    # Mention / Notification section
    at_tags: list[str] = []
    if should_at_all:
        at_tags.append('<at id="all">所有人</at>')
    for u in at_users or []:
        uid = str(u.get("id") or u.get("open_id") or "").strip()
        uname = str(u.get("name") or "").strip()
        if not uid:
            continue
        if "@" in uid and not uid.startswith("ou_"):
            at_tags.append(f'<at email="{uid}">{uname or uid}</at>')
        else:
            at_tags.append(f'<at id="{uid}">{uname or uid}</at>')

    if at_tags:
        elements.append({
            "tag": "markdown",
            "content": f"🔔 **通知负责人**：{' '.join(at_tags)}",
            "text_align": "left",
            "text_size": "normal",
        })

    return {
        "msg_type": "interactive",
        "card": {
            "schema": "2.0",
            "config": {
                "wide_screen_mode": True,
                "update_multi": True,
            },
            "header": {
                "title": {
                    "tag": "plain_text",
                    "content": card_title,
                },
                "template": template or "blue",
            },
            "body": {
                "elements": elements,
            },
        },
    }

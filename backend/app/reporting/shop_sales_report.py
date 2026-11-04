"""全店销售快照的独立 HTML 展示，不访问数据源。"""

from datetime import datetime
from html import escape
from pathlib import Path
from string import Template
from typing import Any

from app.reporting.dashboard_shops import BEIJING, shop_sales_timestamp


def _number(value: Any) -> str:
    return "—" if value is None else f"{float(value):,.2f}"


def _platform_class(platform: str) -> str:
    p = platform.lower()
    if any(k in p for k in ("天猫", "淘宝", "tmall", "taobao")):
        return "platform-tmall"
    if any(k in p for k in ("抖音", "douyin")):
        return "platform-douyin"
    if any(k in p for k in ("京东", "jd")):
        return "platform-jd"
    if any(k in p for k in ("快手", "kuaishou")):
        return "platform-kuaishou"
    if any(k in p for k in ("唯品会", "vip")):
        return "platform-vip"
    if any(k in p for k in ("微信", "视频号", "wechat")):
        return "platform-wechat"
    if any(k in p for k in ("小红书", "red")):
        return "platform-xhs"
    if any(k in p for k in ("拼多多", "pdd")):
        return "platform-pdd"
    return "platform-other"


def render_shop_sales_report(
    shops: list[dict[str, Any]], *, generated_at: datetime, refresh_seconds: int = 60,
) -> str:
    """用真实快照时间展示全部店铺，缺失金额不参与伪造合计。"""
    generated = datetime.fromisoformat(shop_sales_timestamp(generated_at))
    ordered = sorted(
        shops, key=lambda row: (row.get("net") is not None, row.get("net") or 0),
        reverse=True,
    )
    rows = []
    stamps = []
    pos_count = 0
    zero_count = 0
    neg_count = 0

    for row in ordered:
        platform = str(row.get("platform") or "未提供")
        name = str(row.get("shop_name") or row.get("name") or "未命名店铺")
        stamp = shop_sales_timestamp(row.get("updated_at"))
        if stamp:
            stamp_dt = datetime.fromisoformat(stamp)
            stamps.append(stamp_dt)
            time_text = stamp_dt.strftime("%Y-%m-%d %H:%M:%S")
            age_min = max(0, int((generated - stamp_dt).total_seconds() // 60))
            if age_min < 1:
                delay_text = "延迟 <1 分钟"
                delay_class = "delay-good"
            elif age_min <= 15:
                delay_text = f"延迟 {age_min} 分钟"
                delay_class = "delay-good"
            elif age_min <= 30:
                delay_text = f"延迟 {age_min} 分钟"
                delay_class = "delay-warn"
            else:
                delay_text = f"延迟 {age_min} 分钟"
                delay_class = "delay-danger"
        else:
            time_text = "未提供"
            delay_text = "时间未提供"
            delay_class = "delay-missing"

        net_val = row.get("net")
        if net_val is not None:
            if float(net_val) > 0:
                pos_count += 1
                status_type = "pos"
            elif float(net_val) < 0:
                neg_count += 1
                status_type = "neg"
            else:
                zero_count += 1
                status_type = "zero"
        else:
            status_type = "unknown"

        cells = []
        for key, label in (("ops", "运营净销"), ("live", "达播净销"), ("net", "合计净销")):
            val = row.get(key)
            negative = " negative" if val is not None and float(val) < 0 else ""
            zero = " zero" if val is not None and float(val) == 0 else ""
            display = _number(val)
            if negative:
                display = f'<span class="negative-value">{display}</span>'
            cells.append(f'<td class="amount {key}{negative}{zero}" data-label="{label}">'
                         f'{display}</td>')

        net_attr = escape(str(net_val if net_val is not None else ""), quote=True)
        ops_attr = escape(str(row.get("ops") if row.get("ops") is not None else ""), quote=True)
        live_attr = escape(str(row.get("live") if row.get("live") is not None else ""), quote=True)
        platform_attr = escape(platform, quote=True)
        name_attr = escape(name, quote=True)
        search_attr = escape(platform + " " + name, quote=True)
        p_class = _platform_class(platform)

        rows.append(
            f'<tr data-shop-row="true" data-net="{net_attr}" data-ops="{ops_attr}" '
            f'data-live="{live_attr}" data-platform="{platform_attr}" '
            f'data-shop-name="{name_attr}" data-status="{status_type}" '
            f'data-search="{search_attr}">'
            f'<td class="platform" data-label="平台"><span class="platform-tag {p_class}">{escape(platform)}</span></td>'
            f'<td class="shop-name" data-label="店铺名称">{escape(name)}</td>'
            + "".join(cells)
            + f'<td class="updated" data-label="数据更新时间" data-updated-at="{stamp or ""}">'
              f'<time>{time_text}</time><span class="row-delay {delay_class}">{delay_text}</span></td></tr>'
        )

    totals = {}
    numeric_totals = {}
    for key in ("net", "ops", "live"):
        values = [row.get(key) for row in ordered]
        if values and all(v is not None for v in values):
            numeric_totals[key] = sum(values)
            totals[key] = _number(numeric_totals[key])
        else:
            numeric_totals[key] = 0.0
            totals[key] = "—"

    # 计算运营与达播占比
    total_net_num = numeric_totals.get("net", 0.0)
    if total_net_num > 0:
        ops_ratio = f"{round((numeric_totals.get('ops', 0.0) / total_net_num) * 100, 1)}%"
        live_ratio = f"{round((numeric_totals.get('live', 0.0) / total_net_num) * 100, 1)}%"
    else:
        ops_ratio = "—"
        live_ratio = "—"

    oldest = min(stamps) if stamps else None
    newest = max(stamps) if stamps else None
    delayed = len(stamps) < len(shops) or (
        oldest is not None and (generated - oldest).total_seconds() > 1800
    )
    if stamps and oldest and newest:
        time_range = (
            f"{oldest:%Y-%m-%d %H:%M:%S} — {newest:%H:%M:%S}"
            if oldest.date() == newest.date()
            else f"{oldest:%Y-%m-%d %H:%M:%S} — {newest:%Y-%m-%d %H:%M:%S}"
        )
    else:
        time_range = "源数据时间未提供"

    template = Template(
        (Path(__file__).parent / "templates" / "shop_sales_report.html").read_text(encoding="utf-8")
    )
    return template.substitute(
        rows="\n".join(rows), shop_count=len(shops),
        net=totals["net"], ops=totals["ops"], live=totals["live"],
        ops_ratio=ops_ratio, live_ratio=live_ratio,
        pos_count=pos_count, zero_count=zero_count, neg_count=neg_count,
        generated=generated.astimezone(BEIJING).strftime("%Y-%m-%d %H:%M:%S"),
        source_range=time_range, refresh_seconds=max(15, int(refresh_seconds)),
        status="数据可能延迟" if delayed else ("暂无数据" if not shops else "快照已更新"),
        status_class="delayed" if delayed else "",
        empty_hidden="hidden" if shops else "",
    )

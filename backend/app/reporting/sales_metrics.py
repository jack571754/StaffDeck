"""Sales metrics pure-function module (Single Source of Truth).

Consolidates calculation and formatting for sales reports and interactive cards:
- Influencer (达播/达人) row detection, metric extraction (净销, GMV in 万元), and aggregation.
- Top-N ranking and card line formatting.
"""

from __future__ import annotations

import json
import logging
import urllib.request
from typing import Any

from app.reporting.clickhouse_config import clickhouse_query_url

logger = logging.getLogger(__name__)


def is_dabo_row(row: dict[str, Any]) -> bool:
    """Return True if row represents an influencer / live-streaming sales record."""
    stat_dim = str(row.get("统计维度") or "").strip()
    cat = str(row.get("category") or row.get("_channel") or "").strip()

    if stat_dim.startswith(("3_达人", "3_达播")):
        return True
    return cat in (
        "达人销售Top5",
        "达人销售TOP5",
        "达人销售TOP 5",
        "达人Top5",
        "达人TOP5",
        "达人",
        "达播",
    )


def _to_float(val: Any, default: float = 0.0) -> float:
    if val is None or val == "":
        return default
    try:
        return float(val)
    except (ValueError, TypeError):
        return default


def extract_influencer_name(row: dict[str, Any]) -> str:
    """Extract and normalize influencer name from varied dataset schemas."""
    name = str(
        row.get("达人")
        or row.get("item")
        or row.get("name")
        or row.get("_identifier")
        or ""
    ).strip()
    if name in ("", "-"):
        return ""
    return name


def extract_influencer_amounts(row: dict[str, Any]) -> tuple[float, float, bool]:
    """Extract (net_sales_wan, gmv_wan, has_gmv) normalized to 万元."""
    if row.get("净销_万") is not None or row.get("price") is not None:
        net_wan = _to_float(row.get("净销_万") or row.get("price"))
    elif row.get("净销") is not None:
        net_wan = _to_float(row.get("净销")) / 10000.0
    else:
        net_wan = 0.0

    has_gmv = False
    gmv_wan = 0.0
    if row.get("成交金额_万") is not None:
        gmv_wan = _to_float(row.get("成交金额_万"))
        has_gmv = True
    elif row.get("成交金额") is not None:
        gmv_wan = _to_float(row.get("成交金额")) / 10000.0
        has_gmv = True

    return net_wan, gmv_wan, has_gmv


def aggregate_influencer_metrics(
    rows: list[dict[str, Any]],
) -> dict[str, dict[str, float | bool]]:
    """Aggregate influencer metrics (net sales & GMV in 万元) by influencer name."""
    aggregated: dict[str, dict[str, float | bool]] = {}
    for r in rows:
        name = extract_influencer_name(r)
        if not name:
            continue
        net_wan, gmv_wan, has_gmv = extract_influencer_amounts(r)

        entry = aggregated.get(name)
        if entry is None:
            aggregated[name] = {
                "net_wan": net_wan,
                "gmv_wan": gmv_wan,
                "has_gmv": has_gmv,
            }
        else:
            entry["net_wan"] = float(entry["net_wan"]) + net_wan
            entry["gmv_wan"] = float(entry["gmv_wan"]) + gmv_wan
            entry["has_gmv"] = bool(entry["has_gmv"] or has_gmv)

    return aggregated


def get_top_influencers(
    rows: list[dict[str, Any]],
    limit: int = 5,
) -> list[dict[str, Any]]:
    """Return top N influencers sorted by net sales desc, then GMV desc."""
    aggregated = aggregate_influencer_metrics(rows)
    sorted_items = sorted(
        aggregated.items(),
        key=lambda kv: (float(kv[1]["net_wan"]), float(kv[1]["gmv_wan"])),
        reverse=True,
    )
    result = []
    for name, metrics in sorted_items[:limit]:
        result.append({
            "name": name,
            "net_wan": round(float(metrics["net_wan"]), 2),
            "gmv_wan": round(float(metrics["gmv_wan"]), 2),
            "has_gmv": bool(metrics["has_gmv"]),
        })
    return result


def format_influencer_top_lines(
    rows: list[dict[str, Any]],
    limit: int = 5,
    empty_fallback: str = "*(暂无有效达人数据)*",
) -> list[str]:
    """Format top N influencers as markdown lines for Feishu cards and reports."""
    top_items = get_top_influencers(rows, limit=limit)
    if not top_items:
        return [empty_fallback] if empty_fallback else []

    lines: list[str] = []
    for i, item in enumerate(top_items, 1):
        name = item["name"]
        net_wan = item["net_wan"]
        gmv_wan = item["gmv_wan"]
        if item["has_gmv"] and gmv_wan > 0:
            lines.append(
                f"{i}. **{name}**\n   净销: <font color='blue'>**{net_wan:.2f}万**</font> | GMV: {gmv_wan:.2f}万"
            )
        else:
            lines.append(
                f"{i}. **{name}**\n   净销: <font color='blue'>**{net_wan:.2f}万**</font>"
            )
    return lines


def fetch_influencer_sales_rows() -> list[dict[str, Any]]:
    """Fetch untruncated channel/brand rows using the matrix's live-sales scope."""
    url = clickhouse_query_url()
    if url is None:
        return []
    queries = [
        """
        SELECT trim(author_name) AS author, '抖音' AS platform,
            if(trim(doudian_shop_name) like '%可丽金%', '可丽金', '可复美') AS brand,
            sum(pay_amount+promotion_platform_amount+promotion_pay_amount+post_amount)/10000 AS gmv_wan,
            (sum(pay_amount+promotion_platform_amount+promotion_pay_amount+post_amount) - sum(IF(order_status_desc LIKE '%已关闭%',
                pay_amount+promotion_platform_amount+promotion_pay_amount+post_amount, 0)))/10000 AS net_wan
        FROM smartbimpp.dy_order
        WHERE toDate(pay_time) = today() AND `端口` = '达播'
        GROUP BY author, brand
        FORMAT JSON
        """,
        """
        SELECT trim(`达人`) AS author, '天猫' AS platform,
            if(`平台店铺` like '%可丽金%', '可丽金', '可复美') AS brand,
            sum(`支付金额`)/10000 AS gmv_wan,
            (sum(`支付金额`) - sum(`成功退款金额`))/10000 AS net_wan
        FROM smartbimpp.tm_product_realtime_jieguobiao
        WHERE toDate(`日期`) = today() AND `归属` = '达播'
        GROUP BY author, brand
        FORMAT JSON
        """,
        """
        SELECT trim(coalesce(nullIf(trim(`达人`), ''), `昵称`, '')) AS author,
            trim(`平台`) AS platform,
            if(`平台店铺` like '%可丽金%', '可丽金',
                if(`平台店铺` like '%可复美%', '可复美', '')) AS brand,
            sum(`成交金额`)/10000 AS gmv_wan, sum(`净销`)/10000 AS net_wan
        FROM smartbimpp.`达人销售表汇总_商品维度_勿删`
        WHERE `日期` = today() AND trim(`平台`) NOT IN ('天猫', '抖音', '')
        GROUP BY author, platform, brand
        FORMAT JSON
        """,
    ]
    result: list[dict[str, Any]] = []
    for sql in queries:
        request = urllib.request.Request(url, data=sql.encode("utf-8"))
        # Do not publish a partial ranking if any channel is unavailable.
        with urllib.request.urlopen(request, timeout=8) as response:
            result.extend(json.loads(response.read().decode("utf-8")).get("data", []))
    return result


def fetch_dabo_matrix_raw(
    influencer_rows: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """从 ClickHouse 高速缓存库获取达播实时矩阵核心数据（天猫、抖音、小红书、快手）。

    100% 真实数据驱动，严禁造假，无虚构数据。
    """
    # Reuse the exact Tmall rows used by the ranking (including unattributed totals).
    if influencer_rows is None:
        influencer_rows = fetch_influencer_sales_rows()
    tm_groups: dict[tuple[str, str], dict[str, Any]] = {}
    for row in influencer_rows:
        if row.get("platform") != "天猫":
            continue
        brand = str(row.get("brand") or "")
        group = "李佳琦" if str(row.get("author") or "").strip() == "李佳琦" else "二梯队 (除李佳琦)"
        entry = tm_groups.setdefault((brand, group), {
            "brand": brand, "daren_group": group,
            "gmv_wan": 0.0, "net_wan": 0.0, "refund_wan": 0.0,
        })
        gmv, net = float(row.get("gmv_wan") or 0), float(row.get("net_wan") or 0)
        entry["gmv_wan"] += gmv
        entry["net_wan"] += net
        entry["refund_wan"] += gmv - net
    tm_data = list(tm_groups.values())

    # Derive all remaining matrix levels from the same untruncated snapshot.
    dy_groups: dict[tuple[str, str], dict[str, Any]] = {}
    other_groups: dict[str, dict[str, Any]] = {}
    for row in influencer_rows:
        platform = str(row.get("platform") or "")
        brand = str(row.get("brand") or "")
        author = str(row.get("author") or "").strip()
        if platform == "抖音":
            entry = dy_groups.setdefault((brand, author), {
                "brand": brand, "author": author,
                "gmv_wan": 0.0, "net_wan": 0.0, "refund_wan": 0.0,
            })
        elif platform in ("小红书", "快手"):
            entry = other_groups.setdefault(platform, {
                "平台": platform, "gmv_wan": 0.0, "net_wan": 0.0, "refund_wan": 0.0,
            })
        else:
            continue
        gmv, net = float(row.get("gmv_wan") or 0), float(row.get("net_wan") or 0)
        entry["gmv_wan"] += gmv
        entry["net_wan"] += net
        entry["refund_wan"] += gmv - net
    dy_author_data = sorted(
        (r for r in dy_groups.values() if r["author"] not in ("", "-")),
        key=lambda r: (-r["net_wan"], r["author"]),
    )
    dy_brands: dict[str, dict[str, Any]] = {}
    for row in dy_groups.values():
        entry = dy_brands.setdefault(row["brand"], {
            "brand": row["brand"], "gmv_wan": 0.0, "net_wan": 0.0, "refund_wan": 0.0,
        })
        for metric in ("gmv_wan", "net_wan", "refund_wan"):
            entry[metric] += row[metric]
    dy_brand_data = list(dy_brands.values())
    other_data = list(other_groups.values())

    if not tm_data and not dy_brand_data and not other_data:
        return []

    # 组装天猫数据
    tm_map = {(r["brand"], r["daren_group"]): r for r in tm_data}
    kfm_ljq = tm_map.get(("可复美", "李佳琦"), {"gmv_wan": 0.0, "net_wan": 0.0, "refund_wan": 0.0})
    kfm_sec = tm_map.get(("可复美", "二梯队 (除李佳琦)"), {"gmv_wan": 0.0, "net_wan": 0.0, "refund_wan": 0.0})
    clg_ljq = tm_map.get(("可丽金", "李佳琦"), {"gmv_wan": 0.0, "net_wan": 0.0, "refund_wan": 0.0})
    clg_sec = tm_map.get(("可丽金", "二梯队 (除李佳琦)"), {"gmv_wan": 0.0, "net_wan": 0.0, "refund_wan": 0.0})

    kfm_tm_gmv = round(float(kfm_ljq["gmv_wan"]) + float(kfm_sec["gmv_wan"]), 2)
    kfm_tm_net = round(float(kfm_ljq["net_wan"]) + float(kfm_sec["net_wan"]), 2)
    kfm_tm_ref = round(float(kfm_ljq["refund_wan"]) + float(kfm_sec["refund_wan"]), 2)

    clg_tm_gmv = round(float(clg_ljq["gmv_wan"]) + float(clg_sec["gmv_wan"]), 2)
    clg_tm_net = round(float(clg_ljq["net_wan"]) + float(clg_sec["net_wan"]), 2)
    clg_tm_ref = round(float(clg_ljq["refund_wan"]) + float(clg_sec["refund_wan"]), 2)

    tm_total_gmv = round(kfm_tm_gmv + clg_tm_gmv, 2)
    tm_total_net = round(kfm_tm_net + clg_tm_net, 2)
    tm_total_ref = round(kfm_tm_ref + clg_tm_ref, 2)

    # 组装抖音数据
    dy_brand_map = {r["brand"]: r for r in dy_brand_data}
    dy_kfm = dy_brand_map.get("可复美", {"gmv_wan": 0.0, "net_wan": 0.0, "refund_wan": 0.0})
    dy_clg = dy_brand_map.get("可丽金", {"gmv_wan": 0.0, "net_wan": 0.0, "refund_wan": 0.0})

    dy_total_gmv = round(float(dy_kfm["gmv_wan"]) + float(dy_clg["gmv_wan"]), 2)
    dy_total_net = round(float(dy_kfm["net_wan"]) + float(dy_clg["net_wan"]), 2)
    dy_total_ref = round(float(dy_kfm["refund_wan"]) + float(dy_clg["refund_wan"]), 2)

    dy_kfm_top3 = [r for r in dy_author_data if r["brand"] == "可复美"][:3]
    dy_clg_top3 = [r for r in dy_author_data if r["brand"] == "可丽金"][:3]

    # 组装小红书与快手
    other_map = {r["平台"]: r for r in other_data}
    xhs = other_map.get("小红书", {"gmv_wan": 0.0, "net_wan": 0.0, "refund_wan": 0.0})
    ks = other_map.get("快手", {"gmv_wan": 0.0, "net_wan": 0.0, "refund_wan": 0.0})

    items: list[dict[str, Any]] = [
        {"item": "天猫", "label": "**天猫**", "gmv": tm_total_gmv, "net": tm_total_net, "refund": tm_total_ref, "is_core": True},
        {"item": "天猫:可复美", "label": "**可复美**", "gmv": kfm_tm_gmv, "net": kfm_tm_net, "refund": kfm_tm_ref, "is_core": True},
        {"item": "天猫:可复美:李佳琦", "label": "　　李佳琦", "gmv": float(kfm_ljq["gmv_wan"]), "net": float(kfm_ljq["net_wan"]), "refund": float(kfm_ljq["refund_wan"]), "is_core": False},
        {"item": "天猫:可复美:二梯队", "label": "　　二梯队（除李佳琦）", "gmv": float(kfm_sec["gmv_wan"]), "net": float(kfm_sec["net_wan"]), "refund": float(kfm_sec["refund_wan"]), "is_core": False},
        {"item": "天猫:可丽金", "label": "**可丽金**", "gmv": clg_tm_gmv, "net": clg_tm_net, "refund": clg_tm_ref, "is_core": True},
        {"item": "天猫:可丽金:李佳琦", "label": "　　李佳琦", "gmv": float(clg_ljq["gmv_wan"]), "net": float(clg_ljq["net_wan"]), "refund": float(clg_ljq["refund_wan"]), "is_core": False},
        {"item": "天猫:可丽金:二梯队", "label": "　　二梯队（除李佳琦）", "gmv": float(clg_sec["gmv_wan"]), "net": float(clg_sec["net_wan"]), "refund": float(clg_sec["refund_wan"]), "is_core": False},
        {"item": "抖音", "label": "**抖音**", "gmv": dy_total_gmv, "net": dy_total_net, "refund": dy_total_ref, "is_core": True},
        {"item": "抖音:可复美", "label": "**可复美**", "gmv": float(dy_kfm["gmv_wan"]), "net": float(dy_kfm["net_wan"]), "refund": float(dy_kfm["refund_wan"]), "is_core": True},
    ]

    # 可复美：展示实际销售前三达人明细
    if dy_kfm_top3:
        for r in dy_kfm_top3:
            author = str(r.get("author") or "").strip()
            items.append({
                "item": f"抖音:可复美:{author}",
                "label": f"　　{author}",
                "gmv": float(r.get("gmv_wan") or 0.0),
                "net": float(r.get("net_wan") or 0.0),
                "refund": float(r.get("refund_wan") or 0.0),
                "is_core": False,
            })
    else:
        items.append({
            "item": "抖音:可复美:暂无",
            "label": "　　*(暂无达人明细)*",
            "gmv": 0.0,
            "net": 0.0,
            "refund": 0.0,
            "is_core": False,
        })

    items.append({
        "item": "抖音:可丽金",
        "label": "**可丽金**",
        "gmv": float(dy_clg["gmv_wan"]),
        "net": float(dy_clg["net_wan"]),
        "refund": float(dy_clg["refund_wan"]),
        "is_core": True,
    })

    # 可丽金：展示实际销售前三达人明细
    if dy_clg_top3:
        for r in dy_clg_top3:
            author = str(r.get("author") or "").strip()
            items.append({
                "item": f"抖音:可丽金:{author}",
                "label": f"　　{author}",
                "gmv": float(r.get("gmv_wan") or 0.0),
                "net": float(r.get("net_wan") or 0.0),
                "refund": float(r.get("refund_wan") or 0.0),
                "is_core": False,
            })
    else:
        items.append({
            "item": "抖音:可丽金:暂无",
            "label": "　　*(暂无达人明细)*",
            "gmv": 0.0,
            "net": 0.0,
            "refund": 0.0,
            "is_core": False,
        })

    items.extend([
        {"item": "小红书", "label": "**小红书**", "gmv": float(xhs["gmv_wan"]), "net": float(xhs["net_wan"]), "refund": float(xhs["refund_wan"]), "is_core": True},
        {"item": "快手", "label": "**快手**", "gmv": float(ks["gmv_wan"]), "net": float(ks["net_wan"]), "refund": float(ks["refund_wan"]), "is_core": True},
    ])

    return items


def inject_dabo_matrix_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """注入达播矩阵行至流水线查询行，以参与统一增量快照计算。"""
    if any(str(r.get("category") or "") == "达播矩阵" for r in rows):
        return rows

    influencer_rows = fetch_influencer_sales_rows()
    raw_items = fetch_dabo_matrix_raw(influencer_rows)
    if not raw_items:
        return rows

    # Discard the legacy, pre-truncated template ranking before adding live rows.
    res = [r for r in rows if not is_dabo_row(r)]
    for row in influencer_rows:
        name = str(row.get("author") or "").strip()
        if name in ("", "-", "通用", "未知", "其他", "二梯队", "二梯队 (除李佳琦)"):
            continue
        res.append({
            "category": "达人", "item": name, "达人": name,
            "平台": row.get("platform"), "品牌": row.get("brand"),
            "成交金额_万": float(row.get("gmv_wan") or 0),
            "净销_万": float(row.get("net_wan") or 0),
        })
    for it in raw_items:
        res.append({
            "category": "达播矩阵",
            "item": it["item"],
            "label": it["label"],
            "is_core": it.get("is_core", False),
            "成交金额_万": it["gmv"],
            "净销_万": it["net"],
            "退款金额_万": it["refund"],
            "price": it["net"],
        })
    return res


def build_dabo_matrix_table_elements(
    rows: list[dict[str, Any]],
    *,
    is_first_push: bool = False,
) -> list[dict[str, Any]]:
    """生成飞书卡片 Schema 2.0 规范的达播实时销售明细表格组件。"""
    matrix_rows = [r for r in rows if str(r.get("category") or "") == "达播矩阵"]

    if not matrix_rows:
        raw_items = fetch_dabo_matrix_raw()
        if not raw_items:
            return []
        matrix_rows = [
            {
                "category": "达播矩阵",
                "item": it["item"],
                "label": it["label"],
                "is_core": it.get("is_core", False),
                "成交金额_万": it["gmv"],
                "净销_万": it["net"],
                "退款金额_万": it["refund"],
                "环比增量_万": 0.0,
            }
            for it in raw_items
        ]

    def _fmt_amount(val: float, is_core: bool) -> str:
        if abs(val) < 0.0001:
            return "<font color='grey'>0.00万</font>"
        txt = f"{val:.2f}万"
        return f"**{txt}**" if is_core else txt

    def _fmt_net(val: float, is_core: bool) -> str:
        if abs(val) < 0.0001:
            return "<font color='grey'>0.00万</font>"
        txt = f"{val:.2f}万"
        if val < 0:
            return f"<font color='red'>**{txt}**</font>" if is_core else f"<font color='red'>{txt}</font>"
        return f"**{txt}**" if is_core else txt

    def _fmt_refund_rate(gmv: float, refund: float, is_core: bool) -> str:
        if refund <= 0.0001:
            return "<font color='grey'>0.0%</font>"
        if gmv <= 0.0001:
            return f"<font color='red'>{'**' if is_core else ''}100.0%{'**' if is_core else ''}</font>"
        rate = (refund / gmv) * 100
        txt = f"{rate:.1f}%"
        if rate >= 50.0:
            return f"<font color='red'>**{txt}**</font>" if is_core else f"<font color='red'>{txt}</font>"
        return f"**{txt}**" if is_core else txt

    def _fmt_diff(diff: float, is_first: bool, is_core: bool) -> str:
        if is_first or abs(diff) < 0.0001:
            return "<font color='grey'>0.00万</font>"
        sign = "+" if diff > 0 else ""
        txt = f"{sign}{diff:.2f}万"
        color = "green" if diff > 0 else "red"
        body = f"**{txt}**" if is_core else txt
        return f"<font color='{color}'>{body}</font>"

    table_rows = []
    for r in matrix_rows:
        label = str(r.get("label") or r.get("item") or "")
        is_core = bool(r.get("is_core", False))
        gmv = float(r.get("成交金额_万") or 0.0)
        net = float(r.get("净销_万") or r.get("price") or 0.0)
        refund = float(r.get("退款金额_万") or 0.0)
        diff = 0.0 if is_first_push else float(r.get("环比增量_万") or r.get("diff_amount") or 0.0)

        # Visibility depends only on daily cumulative sales/refunds, never the increment.
        if all(abs(value) < 0.0001 for value in (gmv, net, refund)):
            continue

        table_rows.append({
            "platform": label,
            "gmv": _fmt_amount(gmv, is_core),
            "net_sales": _fmt_net(net, is_core),
            "refund_rate": _fmt_refund_rate(gmv, refund, is_core),
            "diff_sales": _fmt_diff(diff, is_first_push, is_core),
        })

    if not table_rows:
        return []

    return [
        {
            "tag": "markdown",
            "content": "**🎯 达播数据表（分平台 / 品牌 / 重点达人，万元）**",
            "text_align": "left",
        },
        {
            "tag": "table",
            "columns": [
                {"data_type": "lark_md", "name": "platform", "display_name": "平台", "horizontal_align": "left", "width": "auto"},
                {"data_type": "lark_md", "name": "gmv", "display_name": "成交金额", "horizontal_align": "right", "width": "auto"},
                {"data_type": "lark_md", "name": "net_sales", "display_name": "净销", "horizontal_align": "right", "width": "auto"},
                {"data_type": "lark_md", "name": "refund_rate", "display_name": "退款率", "horizontal_align": "right", "width": "auto"},
                {"data_type": "lark_md", "name": "diff_sales", "display_name": "环比上一时刻净销", "horizontal_align": "right", "width": "auto"},
            ],
            "rows": table_rows,
            "row_height": "low",
            "freeze_first_column": False,
            "header_style": {
                "background_style": "grey",
                "bold": True,
                "lines": 1,
            },
            "page_size": 20,
        },
    ]

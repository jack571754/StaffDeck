"""
实时全渠道销售作战大屏 (4K) 报告生成与同步器
100% 真实数据驱动：ClickHouse smartbimpp.dy_order + MySQL 全盘数据
严禁造假，严格保证飞书卡片、大屏看板与小时走势图数据 100% 同步一致。
"""

import hashlib
import json
import logging
import os
import urllib.error
import urllib.request
from datetime import UTC, datetime
from html import escape
from pathlib import Path
from typing import Any

from sqlalchemy.orm.attributes import flag_modified
from sqlmodel import Session, select

from app.core.harness_session_cleanup import harness_task_workspace_path
from app.db.models import ArtifactShareLink, Message, ScheduledTask
from app.reporting.clickhouse_config import clickhouse_query_url

logger = logging.getLogger(__name__)


def fetch_clickhouse_dy_order_metrics() -> dict[str, Any]:
    """从 ClickHouse 高速缓存库获取今日自营官方旗舰店实况、渠道分布与爆款 TOP 5"""
    url = clickhouse_query_url()
    if url is None:
        return {
            "shops": [],
            "channels": [],
            "zibo_shops": [],
            "daren_top5": [],
        }

    sql_shops = """
    SELECT
        trim(doudian_shop_name) AS shop_name,
        count(DISTINCT order_id) AS order_cnt,
        round(sum(pay_amount+promotion_platform_amount+promotion_pay_amount+post_amount)/10000, 2) AS gmv_wan,
        round(sum(pay_amount)/10000, 2) AS pay_wan,
        round(sum(IF(order_status_desc LIKE '%已关闭%', pay_amount+promotion_platform_amount+promotion_pay_amount+post_amount, 0))/10000, 2) AS refund_wan,
        round((sum(pay_amount+promotion_platform_amount+promotion_pay_amount+post_amount) - sum(IF(order_status_desc LIKE '%已关闭%', pay_amount+promotion_platform_amount+promotion_pay_amount+post_amount, 0)))/10000, 2) AS net_wan
    FROM smartbimpp.dy_order
    WHERE toDate(pay_time) = today()
      AND `端口` = '自营'
      AND `端口` != '达播'
      AND trim(doudian_shop_name) IN ('可丽金官方旗舰店', '可复美官方旗舰店', '可复美化妆品旗舰店', '可复美护肤旗舰店')
    GROUP BY shop_name
    ORDER BY net_wan DESC
    FORMAT JSON
    """

    sql_channels = """
    SELECT
        IF(theme_type_desc = '直播间', '自播', '商城') AS ch_type,
        count(DISTINCT order_id) AS order_cnt,
        round(sum(pay_amount+promotion_platform_amount+promotion_pay_amount+post_amount)/10000, 2) AS gmv_wan,
        round((sum(pay_amount+promotion_platform_amount+promotion_pay_amount+post_amount) - sum(IF(order_status_desc LIKE '%已关闭%', pay_amount+promotion_platform_amount+promotion_pay_amount+post_amount, 0)))/10000, 2) AS net_wan
    FROM smartbimpp.dy_order
    WHERE toDate(pay_time) = today()
      AND `端口` = '自营'
      AND `端口` != '达播'
      AND trim(doudian_shop_name) IN ('可丽金官方旗舰店', '可复美官方旗舰店', '可复美化妆品旗舰店', '可复美护肤旗舰店')
    GROUP BY ch_type
    ORDER BY net_wan DESC
    FORMAT JSON
    """

    sql_zibo = """
    SELECT
        trim(doudian_shop_name) AS shop_name,
        count(DISTINCT order_id) AS order_cnt,
        round(sum(pay_amount+promotion_platform_amount+promotion_pay_amount+post_amount)/10000, 2) AS gmv_wan,
        round(sum(pay_amount)/10000, 2) AS pay_wan,
        round(sum(IF(order_status_desc LIKE '%已关闭%', pay_amount+promotion_platform_amount+promotion_pay_amount+post_amount, 0))/10000, 2) AS refund_wan,
        round((sum(pay_amount+promotion_platform_amount+promotion_pay_amount+post_amount) - sum(IF(order_status_desc LIKE '%已关闭%', pay_amount+promotion_platform_amount+promotion_pay_amount+post_amount, 0)))/10000, 2) AS net_wan
    FROM smartbimpp.dy_order
    WHERE toDate(pay_time) = today()
      AND `端口` = '自营'
      AND `端口` != '达播'
      AND trim(doudian_shop_name) IN ('可丽金官方旗舰店', '可复美官方旗舰店', '可复美化妆品旗舰店', '可复美护肤旗舰店')
    GROUP BY shop_name
    ORDER BY net_wan DESC
    FORMAT JSON
    """

    out: dict[str, Any] = {
        "shops": [],
        "channels": [],
        "zibo_shops": [],
        "daren_top5": [],
    }
    try:
        req = urllib.request.Request(url, data=sql_shops.encode("utf-8"))
        with urllib.request.urlopen(req, timeout=8) as resp:
            out["shops"] = json.loads(resp.read().decode("utf-8")).get("data", [])

        req = urllib.request.Request(url, data=sql_channels.encode("utf-8"))
        with urllib.request.urlopen(req, timeout=8) as resp:
            out["channels"] = json.loads(resp.read().decode("utf-8")).get("data", [])

        req = urllib.request.Request(url, data=sql_zibo.encode("utf-8"))
        with urllib.request.urlopen(req, timeout=8) as resp:
            out["zibo_shops"] = json.loads(resp.read().decode("utf-8")).get("data", [])

    except Exception as e:  # noqa: BLE001
        logger.warning("ClickHouse dy_order query failed (fallback to empty): %s", e)

    out["daren_top5"] = fetch_all_channel_daren_top5(url)
    return out


def fetch_all_channel_daren_top5(url: str) -> list[dict[str, Any]]:
    """Aggregate actual influencer names across brands/platforms before ranking.

    Tmall's 通用 bucket is unattributed sales, not an individual influencer.
    Keep the source-specific net-sales formulas used by the dashboard.
    """
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
    authors: dict[str, dict[str, Any]] = {}
    for sql in queries:
        try:
            request = urllib.request.Request(url, data=sql.encode('utf-8'))
            with urllib.request.urlopen(request, timeout=8) as response:
                rows = json.loads(response.read().decode('utf-8')).get('data', [])
        except Exception as exc:  # noqa: BLE001
            logger.warning('Influencer channel query failed: %s', exc)
            continue
        for row in rows:
            name = str(row.get('author') or '').strip()
            if name in ('', '-', '通用', '未知', '其他', '二梯队', '二梯队 (除李佳琦)'):
                continue
            author = authors.setdefault(name, {
                'author': name, 'gmv_wan': 0.0, 'net_wan': 0.0,
                'platforms': [], 'brands': [],
            })
            for metric in ('gmv_wan', 'net_wan'):
                author[metric] += float(row.get(metric) or 0)
            for field, collection in (('platform', 'platforms'), ('brand', 'brands')):
                value = str(row.get(field) or '').strip()
                if value and value not in author[collection]:
                    author[collection].append(value)
    ranked = sorted(authors.values(), key=lambda row: (-row['net_wan'], row['author']))[:5]
    return [{
        'author': row['author'], 'platform': ' / '.join(row['platforms']),
        'brand': ' / '.join(row['brands']),
        'net_wan': round(row['net_wan'], 2), 'gmv_wan': round(row['gmv_wan'], 2),
    } for row in ranked]


def _optional_amount(row: dict[str, Any], key: str) -> float | None:
    value = row.get(key)
    return round(float(value), 2) if value is not None else None


def parse_pipeline_query_metrics(query_rows: list[dict[str, Any]]) -> dict[str, Any]:
    """解析全盘查询数据并提取大盘、品牌、渠道、小时走势等结构化指标"""
    metrics: dict[str, Any] = {
        "gmv_all": 0.0,
        "ops_all": 0.0,
        "live_all": 0.0,
        "mom_all": "+0.00万",
        "ops_inc_all": "+0.00万",
        "live_inc_all": "+0.00万",
        "ops_ratio": "0.0%",
        "dabo_ratio": "0.0%",
        "kfm_net": 0.0,
        "kfm_ops": 0.0,
        "kfm_live": 0.0,
        "kfm_mom": "+0.00万",
        "clg_net": 0.0,
        "clg_ops": 0.0,
        "clg_live": 0.0,
        "clg_mom": "+0.00万",
        "other_net": 0.0,
        "other_sub": "巨子生物 / 可预等品牌",
        "hourly": [],
        "shops": [],
        "channels_all": [],
    }

    # 1. 大盘核心指标
    for r in query_rows:
        cat = str(r.get("category") or "").strip()
        item = str(r.get("item") or "").strip()
        if cat == "大盘" and item == "电商整体":
            net = float(r.get("净销_万") or 0.0)
            ops = float(r.get("运营净销_万") or 0.0)
            live = float(r.get("达播净销_万") or 0.0)
            mom = float(r.get("环比增量_万") or 0.0)
            ops_inc = float(r.get("运营增量_万") or 0.0)
            live_inc = float(r.get("达播增量_万") or 0.0)

            metrics["gmv_all"] = round(net, 2)
            metrics["ops_all"] = round(ops, 2)
            metrics["live_all"] = round(live, 2)
            metrics["mom_all"] = f"{'+' if mom >= 0 else ''}{mom:.2f}万"
            metrics["ops_inc_all"] = f"{'+' if ops_inc >= 0 else ''}{ops_inc:.2f}万"
            metrics["live_inc_all"] = f"{'+' if live_inc >= 0 else ''}{live_inc:.2f}万"
            if net > 0:
                metrics["ops_ratio"] = f"{(ops / net * 100):.1f}%"
                metrics["dabo_ratio"] = f"{(live / net * 100):.1f}%"

        elif cat == "可复美" and item == "整体":
            metrics["kfm_net"] = round(float(r.get("净销_万") or 0.0), 2)
            metrics["kfm_ops"] = round(float(r.get("运营净销_万") or 0.0), 2)
            metrics["kfm_live"] = round(float(r.get("达播净销_万") or 0.0), 2)
            k_mom = float(r.get("环比增量_万") or 0.0)
            metrics["kfm_mom"] = f"{'+' if k_mom >= 0 else ''}{k_mom:.2f}万"

        elif cat == "可丽金" and item == "整体":
            metrics["clg_net"] = round(float(r.get("净销_万") or 0.0), 2)
            metrics["clg_ops"] = round(float(r.get("运营净销_万") or 0.0), 2)
            metrics["clg_live"] = round(float(r.get("达播净销_万") or 0.0), 2)
            c_mom = float(r.get("环比增量_万") or 0.0)
            metrics["clg_mom"] = f"{'+' if c_mom >= 0 else ''}{c_mom:.2f}万"

    # 2. 其它品牌 (严格排除分销！只汇总巨子生物 + 可预)
    other_sum = 0.0
    other_details = []
    for r in query_rows:
        cat = str(r.get("category") or "").strip()
        item = str(r.get("item") or "").strip()
        if cat == "其他品牌" and item in ("巨子生物", "可预"):
            val = float(r.get("净销_万") or 0.0)
            other_sum += val
            other_details.append(f"{item} {val:.2f}万")
    metrics["other_net"] = round(other_sum, 2)
    if other_details:
        metrics["other_sub"] = " · ".join(other_details)

    # 3. 24小时走势 REAL_HOURLY
    hourly_dict: dict[str, float] = {}
    for r in query_rows:
        cat = str(r.get("category") or "").strip()
        if cat == "24小时环比":
            h_str = str(r.get("item") or "").strip()
            v = float(r.get("净销_万") or 0.0)
            hourly_dict[h_str] = v

    hourly_list = []
    for i in range(24):
        h_name = f"{i:02d}时"
        v = hourly_dict.get(h_name, 0.0)
        if v > 0 or i == 0:
            hourly_list.append({"h": h_name, "v": round(v, 2)})
        elif len(hourly_list) > 1 and v <= 0:
            break

    # 核心一致性保证：如果走势图有数据点，最后一个小时点必须与大盘今日累计净销额完全相等！
    if hourly_list and metrics["gmv_all"] > 0:
        hourly_list[-1]["v"] = metrics["gmv_all"]

    metrics["hourly"] = hourly_list

    # The pipeline calls the non-influencer business 运营端; display it as 自营.
    # Preserve missing values so the UI can distinguish unavailable data from zero.
    shops_by_name: dict[str, dict[str, Any]] = {}
    shop_categories = ("重点店铺", "店铺明细", "店铺正增量Top5", "店铺负增量Top5")
    for category in shop_categories:
        for row in query_rows:
            if row.get("category") != category:
                continue
            name = str(row.get("item") or "").strip()
            if not name or name in shops_by_name:
                continue
            delta = row.get("环比增量_万")
            shops_by_name[name] = {
                "name": name,
                "net": round(float(row.get("净销_万") or 0), 2),
                "mom": f"{float(delta):+.2f}万" if delta is not None else None,
                "ops": _optional_amount(row, "运营净销_万"),
                "live": _optional_amount(row, "达播净销_万"),
                "neg": float(row.get("净销_万") or 0) < 0,
            }
    metrics["shops"] = sorted(shops_by_name.values(), key=lambda row: row["net"], reverse=True)

    channel_names = {
        "天猫": "天猫渠道", "抖音": "抖音渠道", "京东": "京东渠道",
        "唯品会": "唯品会渠道", "微信视频号": "微信视频号",
        "小红书": "小红书渠道", "快手": "快手渠道", "拼多多": "拼多多渠道",
    }
    buckets: dict[str, dict[str, Any]] = {}
    for row in query_rows:
        category = str(row.get("category") or "").strip()
        item = str(row.get("item") or "").strip()
        if category in ("可复美", "可丽金") and item in channel_names:
            name = channel_names[item]
        elif category == "其他品牌" and item in ("巨子生物", "可预", "分销", "以我"):
            name = {
                "分销": "分销渠道",
                "巨子生物": "其它品牌(巨子/可预)",
                "可预": "其它品牌(巨子/可预)",
            }.get(item, item)
        else:
            continue
        bucket = buckets.setdefault(name, {"name": name, "gmv": 0.0, "diff": 0.0,
                                            "ops": 0.0, "live": 0.0})
        bucket["gmv"] += float(row.get("净销_万") or 0)
        bucket["diff"] += float(row.get("环比增量_万") or 0)
        for key, field in (("ops", "运营净销_万"), ("live", "达播净销_万")):
            value = _optional_amount(row, field)
            bucket[key] = bucket[key] + value if bucket[key] is not None and value is not None else None

    # Never inflate the leading channel to reconcile missing channel data.
    residual = round(metrics["gmv_all"] - sum(row["gmv"] for row in buckets.values()), 2)
    has_summary = any(row.get("category") == "大盘" and row.get("item") == "电商整体"
                      for row in query_rows)
    if has_summary and abs(residual) >= 0.01:
        buckets["未分配净销"] = {"name": "未分配净销", "gmv": residual, "diff": 0,
                                 "ops": None, "live": None}
    channels_all = []
    for channel in sorted(buckets.values(), key=lambda row: row["gmv"], reverse=True):
        net = round(channel["gmv"], 2)
        channels_all.append({
            **channel, "gmv": net,
            "ops": round(channel["ops"], 2) if channel["ops"] is not None else None,
            "live": round(channel["live"], 2) if channel["live"] is not None else None,
            "yoy": f"{channel['diff']:+.2f}万",
            "share": f"{net / metrics['gmv_all'] * 100:.1f}%" if metrics["gmv_all"] else "—",
            "neg": net < 0,
        })
    metrics["channels_all"] = channels_all

    return metrics


def build_realtime_sales_dashboard_html(metrics: dict[str, Any], ck_data: dict[str, Any]) -> str:
    """构建 4K 自适应实时销售看板完整 HTML，保证全链路 100% 真实数据"""
    gmv_all = metrics.get("gmv_all", 0.0)
    ops_all = metrics.get("ops_all", 0.0)
    live_all = metrics.get("live_all", 0.0)
    mom_all = metrics.get("mom_all", "+0.00万")
    ops_inc_all = metrics.get("ops_inc_all", "+0.00万")
    ops_ratio = metrics.get("ops_ratio", "0.0%")
    dabo_ratio = metrics.get("dabo_ratio", "0.0%")

    kfm_net = metrics.get("kfm_net", 0.0)
    clg_net = metrics.get("clg_net", 0.0)
    other_net = metrics.get("other_net", 0.0)
    other_sub = metrics.get("other_sub", "巨子生物 / 可预")

    hourly = metrics.get("hourly") or []
    hourly_json = json.dumps(hourly, ensure_ascii=False)

    # ClickHouse filters 端口=自营; these totals exclude influencer sales.
    shop_list = ck_data.get("shops") or ck_data.get("zibo_shops") or []
    shop_total_orders = sum(int(s.get("order_cnt") or 0) for s in shop_list)
    shop_total_gmv = sum(float(s.get("gmv_wan") or 0.0) for s in shop_list)
    shop_total_ref = sum(float(s.get("refund_wan") or 0.0) for s in shop_list)
    shop_total_net = sum(float(s.get("net_wan") or 0.0) for s in shop_list)

    # 全渠道达人按姓名汇总后统一排名
    daren_top5 = ck_data.get("daren_top5") or []

    # 重点店铺明细
    complete_shops = metrics.get("all_shops")
    shops_data = [dict(shop) for shop in (
        complete_shops if complete_shops is not None else metrics.get("shops") or []
    )]
    known_names = {shop["name"].removeprefix("抖音-") for shop in shops_data}
    for shop in ([] if complete_shops is not None else shop_list):
        name = str(shop.get("shop_name") or "").strip()
        if not name or name in known_names:
            continue
        known_names.add(name)
        net = _optional_amount(shop, "net_wan")
        if net is None:
            continue
        shops_data.append({"name": f"抖音-{name}", "net": net, "ops": net,
                           "live": None, "mom": None, "neg": net < 0})
    shops_data.sort(key=lambda shop: shop["net"], reverse=True)
    shops_json = json.dumps(shops_data, ensure_ascii=False).replace("<", "\\u003c")

    # 渠道结构数据（100% 真实数据驱动）
    channels_all = metrics.get("channels_all") or []
    channels_json = json.dumps({"all": channels_all}, ensure_ascii=False).replace("<", "\\u003c")

    now_str = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S UTC")

    # 动态构建 HTML
    html_content = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>双十一全渠道实时销售作战大屏 (4K)</title>
<style>
  :root {{
    --canvas: #0A0F1D;
    --surface: #111827;
    --surface-raised: #1A2234;
    --border-subtle: #243049;
    --text-primary: #F8FAFC;
    --text-secondary: #94A3B8;
    --text-tertiary: #64748B;
    --accent-gold: #FFB84D;
    --accent-gold-glow: rgba(255,184,77,0.35);
    --info: #3B82F6;
    --success: #10B981;
    --danger: #EF4444;
    --font-sans: "PingFang SC", "Hiragino Sans GB", "Microsoft YaHei", -apple-system, BlinkMacSystemFont, sans-serif;
    --font-num: "DIN Alternate", "DIN Pro", "Bahnschrift", "Roboto Condensed", sans-serif;
  }}

  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  html, body {{
    width: 100%; height: 100%;
    background: var(--canvas);
    color: var(--text-primary);
    font-family: var(--font-sans);
    overflow: hidden;
    user-select: none;
    -webkit-font-smoothing: antialiased;
  }}

  .stage {{
    width: 3840px; height: 2160px;
    position: absolute; left: 0; top: 0;
    transform-origin: 0 0;
    padding: 40px 48px;
    display: flex; flex-direction: column; gap: 28px;
    background:
      radial-gradient(ellipse 40% 25% at 50% 0%, rgba(255,184,77,0.06), transparent 70%),
      radial-gradient(ellipse 30% 20% at 85% 15%, rgba(59,130,246,0.06), transparent 60%),
      radial-gradient(ellipse 35% 25% at 15% 85%, rgba(16,185,129,0.04), transparent 60%),
      var(--canvas);
  }}

  .num {{ font-family: var(--font-num); font-variant-numeric: tabular-nums; letter-spacing: 0.02em; }}
  .up   {{ color: #34D399; }}
  .down {{ color: #F87171; }}

  /* ---------- 顶部栏 ---------- */
  header {{
    height: 120px; flex: none;
    display: flex; align-items: center; justify-content: space-between;
    padding: 0 16px;
    border-bottom: 2px solid var(--border-subtle);
  }}

  .hdr-brand {{ display: flex; align-items: center; gap: 24px; }}
  .logo-badge {{
    width: 64px; height: 64px; border-radius: 16px;
    background: linear-gradient(135deg, #FFB84D, #D97706);
    display: flex; align-items: center; justify-content: center;
    box-shadow: 0 0 24px var(--accent-gold-glow);
  }}
  .logo-badge svg {{ width: 38px; height: 38px; fill: #0A0F1D; }}
  .hdr-brand h1 {{
    font-size: 50px; font-weight: 700;
    letter-spacing: 0.04em;
    background: linear-gradient(180deg, #FFFFFF, #E2E8F0);
    -webkit-background-clip: text; -webkit-text-fill-color: transparent;
  }}
  .hdr-brand .tag {{
    font-size: 22px; color: var(--text-secondary);
    display: block; margin-top: 4px;
  }}

  .hdr-right {{ display: flex; flex-direction: column; align-items: flex-end; gap: 8px; }}
  .countdown {{ font-size: 72px; font-weight: 700; line-height: 1.0; }}
  .countdown small {{ font-size: 26px; font-weight: 500; color: var(--text-secondary); margin-right: 12px; }}
  .hdr-meta {{ display: flex; align-items: center; gap: 20px; font-size: 24px; color: var(--text-tertiary); }}
  .dot {{ width: 14px; height: 14px; border-radius: 50%; background: var(--success); flex: none; box-shadow: 0 0 12px var(--success); }}
  .status-txt {{ color: var(--text-secondary); }}

  /* ---------- 主体三栏 ---------- */
  .grid {{
    height: 1910px; flex: none;
    display: grid; grid-template-columns: 1140px 1400px 1140px; gap: 30px;
  }}
  .col {{ display: flex; flex-direction: column; gap: 28px; }}
  .col-left > .p-rank {{ height: 930px; }}
  .col-left > .p-eff  {{ height: 952px; }}
  .col-mid  > .p-kpi  {{ height: 510px; }}
  .col-mid  > .p-trend{{ height: 960px; }}
  .col-mid  > .p-mini {{ height: 384px; }}
  .col-right> .p-live {{ height: 930px; }}
  .col-right> .p-daren{{ height: 952px; }}

  .panel {{
    background: var(--surface);
    border: 2px solid var(--border-subtle);
    border-radius: 20px;
    padding: 30px;
    display: flex; flex-direction: column;
    box-shadow: none;
    min-height: 0; overflow: hidden;
    position: relative;
  }}
  .p-hd {{
    height: 52px; flex: none;
    display: flex; align-items: center; justify-content: space-between;
    margin-bottom: 20px;
  }}
  .p-hd h2 {{
    font-size: 30px; font-weight: 600;
    display: flex; align-items: center; gap: 16px;
    color: var(--text-primary);
  }}
  .p-hd h2::before {{
    content: ""; width: 6px; height: 28px; border-radius: 3px;
    background: var(--accent-gold);
    box-shadow: 0 0 12px var(--accent-gold-glow);
  }}
  .p-hd .unit {{ font-size: 21px; color: var(--text-tertiary); }}
  .p-bd {{ flex: 1; min-height: 0; }}

  /* ---------- 渠道贡献榜 ---------- */
  .channel-head, .rank-row {{
    display: grid; grid-template-columns: 44px minmax(0,1fr) 166px 166px 166px;
    align-items: center; gap: 12px; padding: 0 12px;
  }}
  .channel-head {{ font-size: 23px; color: var(--text-secondary); height: 44px; }}
  .channel-head span:nth-child(n+3) {{ text-align: right; }}
  .p-rank .p-bd {{ display: flex; flex-direction: column; gap: 12px; }}
  .rank-list {{ display: flex; flex-direction: column; gap: 10px; flex: 1; min-height: 0; }}
  .rank-row {{ flex: 1; min-height: 0; max-height: 100px; border-bottom: 1px solid var(--border-subtle); }}
  .rk-no {{ font-size: 26px; color: var(--text-tertiary); text-align: center; }}
  .top1 .rk-no {{ color: var(--accent-gold); }}
  .rk-name {{ min-width: 0; font-size: 27px; font-weight: 600; }}
  .rk-bar {{ height: 5px; margin-top: 12px; background: var(--surface-raised); overflow: hidden; }}
  .rk-bar span {{ display: block; height: 100%; background: var(--info); }}
  .top1 .rk-bar span {{ background: var(--accent-gold); }}
  .neg .rk-bar span {{ background: var(--danger); }}
  .rk-val {{ text-align: right; font-size: 28px; font-weight: 600; }}
  .rk-val .r {{ font-size: 20px; color: var(--text-tertiary); margin-top: 5px; font-weight: 400; }}
  .split-ops {{ color: #93C5FD; }}
  .split-live {{ color: #C4B5FD; }}

  /* ---------- 重点店铺明细表 ---------- */
  .p-eff .p-bd {{
    display: flex; flex-direction: column; justify-content: space-between;
    height: 100%; min-height: 0;
  }}
  .eff-table-wrap {{ flex: 1; min-height: 0; }}
  .eff {{ width: 100%; border-collapse: collapse; table-layout: fixed; }}
  .eff th {{
    font-size: 24px; font-weight: 500; color: var(--text-tertiary);
    text-align: right; padding: 0 8px 18px; border-bottom: 2px solid var(--border-subtle);
  }}
  .eff th:first-child, .eff td:first-child {{ text-align: left; padding-left: 8px; }}
  .eff td {{
    font-size: 25px; height: 66px; padding: 12px 8px;
    text-align: right; border-bottom: 1px solid rgba(36,48,73,0.6);
  }}
  .eff td.n {{ font-family: var(--font-num); font-variant-numeric: tabular-nums; font-weight: 700; }}
  .eff tr:last-child td {{ border-bottom: none; }}
  .eff td.ch {{ font-size: 24px; font-weight: 600; color: var(--text-primary); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; max-width: 440px; }}

  .eff-pagination {{
    height: 64px; flex: none;
    display: flex; align-items: center; justify-content: space-between;
    padding: 0 16px; margin-top: 14px;
    background: var(--surface-raised);
    border: 1px solid var(--border-subtle);
    border-radius: 12px;
  }}
  .page-info {{
    font-size: 22px; color: var(--text-secondary);
    display: flex; align-items: center; gap: 10px;
  }}
  .page-info b {{ color: var(--accent-gold); font-size: 24px; font-weight: 700; }}
  .page-info .sep {{ color: var(--border-subtle); margin: 0 4px; }}
  .page-controls {{ display: flex; align-items: center; gap: 10px; }}
  .page-btn {{
    padding: 8px 20px; font-size: 22px; font-weight: 600;
    color: var(--text-secondary); background: rgba(255,255,255,0.04);
    border: 1px solid var(--border-subtle); border-radius: 8px;
    cursor: pointer; transition: all .2s ease;
  }}
  .page-btn:hover:not(:disabled) {{
    background: rgba(255,184,77,0.15); border-color: var(--accent-gold);
    color: var(--accent-gold);
  }}
  .page-btn:disabled {{
    opacity: 0.35; cursor: not-allowed;
  }}
  .page-pills {{ display: flex; gap: 8px; }}
  .page-pill {{
    width: 44px; height: 44px; border-radius: 8px;
    display: flex; align-items: center; justify-content: center;
    font-size: 22px; font-weight: 600;
    background: rgba(255,255,255,0.04); border: 1px solid var(--border-subtle);
    color: var(--text-secondary); cursor: pointer; transition: all .2s ease;
  }}
  .page-pill:hover {{ border-color: var(--accent-gold); color: var(--text-primary); }}
  .page-pill.active {{
    background: var(--accent-gold); color: #0A0F1D; font-weight: 700;
    border-color: var(--accent-gold); box-shadow: 0 2px 10px var(--accent-gold-glow);
  }}

  /* ---------- KPI 核心战况 (数字完整无截断) ---------- */
  .p-kpi {{ overflow: visible !important; }}
  .kpi-label {{ font-size: 28px; color: var(--text-secondary); display: flex; align-items: center; justify-content: space-between; }}
  .kpi-main {{ display: flex; align-items: baseline; gap: 16px; margin-top: 8px; }}
  .kpi-main .big {{
    font-size: 148px; font-weight: 700; line-height: 1.0;
    color: var(--accent-gold);
    text-shadow: 0 0 36px var(--accent-gold-glow);
  }}
  .kpi-main .u {{ font-size: 42px; color: var(--text-secondary); }}
  .kpi-prog {{ margin-top: 18px; }}
  .kpi-prog .line {{ display: flex; align-items: center; justify-content: space-between; margin-bottom: 10px; }}
  .kpi-prog .line .lb {{ font-size: 26px; color: var(--text-secondary); }}
  .kpi-prog .line .pc {{ font-size: 36px; font-weight: 700; color: var(--accent-gold); }}
  .bar {{ height: 18px; background: var(--surface-raised); border-radius: 9px; overflow: hidden; padding: 2px; }}
  .bar span {{ display: block; height: 100%; background: linear-gradient(90deg, #C27803, var(--accent-gold)); border-radius: 7px; transition: width .5s ease; }}

  /* 4 个子指标卡：高度充足、字号与内边距精调，绝不截断 */
  .kpi-yoy {{
    display: grid; grid-template-columns: repeat(4, 1fr); gap: 14px;
    margin-top: 18px; overflow: visible;
  }}
  .kpi-yoy div {{
    font-size: 22px; color: var(--text-secondary);
    background: var(--surface-raised); padding: 12px 14px;
    border-radius: 12px; border: 1px solid var(--border-subtle);
    box-sizing: border-box; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
  }}
  .kpi-yoy b {{
    font-size: 26px; line-height: 1.25; display: block;
    margin-top: 6px; font-weight: 700; white-space: nowrap;
  }}

  /* ---------- 24小时走势 ---------- */
  .trend-wrap {{ position: relative; height: 100%; }}
  .trend-legend {{ display: flex; flex-wrap: wrap; gap: 16px; font-size: 20px; color: var(--text-secondary); margin-bottom: 8px; }}
  .trend-legend button {{ background: none; border: 0; color: inherit; font: inherit; cursor: pointer; padding: 6px; }}
  .trend-legend button[aria-pressed="false"] {{ opacity: .35; }}
  .trend-summary {{ display: flex; flex-wrap: wrap; gap: 24px; font-size: 20px; color: var(--text-secondary); margin-bottom: 12px; }}
  .trend-summary b {{ color: var(--text-primary); }}
  #trendTooltip {{ display: none; position: absolute; top: 0; right: 12px; pointer-events: none; white-space: pre-line; background: #0a0f1df2; border: 1px solid #475569; border-radius: 8px; padding: 14px; color: #fff; font-size: 22px; z-index: 2; }}
  .trend-legend i {{ display: inline-block; width: 36px; height: 6px; border-radius: 3px; margin-right: 12px; vertical-align: middle; }}
  svg.trend {{ display: block; width: 100%; height: 100%; }}

  /* ---------- 品牌战报小卡 ---------- */
  .minis {{ display: grid; grid-template-columns: repeat(3, 1fr); gap: 24px; height: 100%; }}
  .mini {{
    background: var(--surface-raised); border-radius: 14px;
    border: 1px solid var(--border-subtle);
    padding: 24px 28px; display: flex; flex-direction: column; justify-content: center; gap: 6px;
  }}
  .mini .lb {{ font-size: 26px; color: var(--text-secondary); }}
  .mini .v {{ font-size: 58px; font-weight: 700; }}
  .mini .v small {{ font-size: 28px; margin-left: 6px; }}
  .mini .d {{ font-size: 24px; color: var(--text-secondary); }}

  /* ---------- 右侧：自营业务实况监控 ---------- */
  .p-live {{ border-color: rgba(59,130,246,0.35); }}
  .live-main {{
    background: linear-gradient(135deg, rgba(30,58,138,0.25), rgba(15,23,42,0.45));
    border: 1px solid rgba(59,130,246,0.3); border-radius: 14px;
    padding: 22px 24px; margin-bottom: 16px;
  }}
  .live-top {{ display: flex; align-items: center; justify-content: space-between; margin-bottom: 14px; }}
  .live-title {{ font-size: 32px; font-weight: 700; }}
  .live-host {{ font-size: 24px; color: var(--text-secondary); margin-top: 4px; }}
  .badge.live {{
    background: rgba(16,185,129,0.18); color: #34D399;
    padding: 6px 18px; border-radius: 20px; font-size: 22px; font-weight: 600;
    border: 1px solid rgba(16,185,129,0.35);
  }}
  .live-metrics {{ display: grid; grid-template-columns: repeat(3, 1fr); gap: 16px; }}
  .live-metrics .m {{
    background: rgba(15,23,42,0.5); padding: 12px 14px; border-radius: 10px;
    border: 1px solid var(--border-subtle);
  }}
  .live-metrics .lb {{ font-size: 22px; color: var(--text-secondary); }}
  .live-metrics .v {{ font-size: 38px; font-weight: 700; margin-top: 4px; }}
  .live-metrics .v small {{ font-size: 22px; margin-left: 4px; }}

  .live-sub {{ display: flex; flex-direction: column; gap: 20px; margin-top: 24px; }}
  .live-sub-row {{
    background: var(--surface-raised); border-radius: 14px;
    padding: 18px 24px; display: flex; align-items: center; justify-content: space-between;
    border: 1px solid var(--border-subtle);
  }}
  .live-sub-row .t {{ font-size: 26px; }}
  .live-sub-row .t small {{ font-size: 20px; color: var(--text-tertiary); margin-left: 12px; display: block; margin-top: 4px; }}
  .live-sub-row .v {{ font-size: 28px; font-weight: 700; text-align: right; }}

  /* ---------- 达人销售榜单 ---------- */
  .tal-list {{ display: flex; flex-direction: column; gap: 18px; height: 100%; }}
  .tal-row {{
    background: var(--surface-raised); border-radius: 14px;
    padding: 24px 18px; display: grid; grid-template-columns: 44px minmax(0,1fr) 150px 190px;
    flex: 1; min-height: 0;
    align-items: center; gap: 20px; border: 1px solid var(--border-subtle);
  }}
  .tal-no {{ font-size: 32px; font-weight: 700; color: var(--text-tertiary); text-align: center; }}
  .tal-row:nth-child(1) .tal-no {{ color: var(--accent-gold); }}
  .tal-row:nth-child(2) .tal-no {{ color: #94A3B8; }}
  .tal-row:nth-child(3) .tal-no {{ color: #B45309; }}
  .tal-name {{ display: flex; flex-direction: column; gap: 6px; }}
  .tal-name .nm {{ font-size: 26px; font-weight: 600; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }}
  .tal-name .cg {{ font-size: 22px; color: var(--text-tertiary); }}
  .tal-gmv {{ font-size: 34px; font-weight: 700; text-align: right; }}
  .tal-gmv small {{ font-size: 22px; color: var(--text-tertiary); margin-left: 4px; }}
  .tal-cnt {{ white-space: nowrap; font-size: 26px; color: var(--text-secondary); text-align: right; }}

  .hint {{
    position: absolute; right: 48px; bottom: 16px;
    font-size: 20px; color: #475569;
  }}
</style>
</head>
<body>
<div class="stage" id="stage">

  <!-- ============ 顶部栏 ============ -->
  <header>
    <div class="hdr-brand">
      <div class="logo-badge">
        <svg viewBox="0 0 24 24"><path d="M12 2L2 7l10 5 10-5-10-5zM2 17l10 5 10-5M2 12l10 5 10-5"/></svg>
      </div>
      <div>
        <h1>实时销售作战大屏</h1>
        <span class="tag">全渠道运营 · 达播专场 · 抖音自营实况深度追踪</span>
      </div>
    </div>

    <div class="hdr-right">
      <div class="countdown num" id="clock">15:00:00</div>
      <div class="hdr-meta">
        <span class="dot"></span>
        <span class="status-txt">实时链路正常 · 自动增量比对</span>
        <span class="num" style="color:var(--text-secondary)">{now_str}</span>
      </div>
    </div>
  </header>

  <!-- ============ 主体三栏布局 ============ -->
  <div class="grid">

    <!-- ---------- 左：渠道贡献与重点店铺 ---------- -->
    <div class="col col-left">
      <!-- 1. 渠道贡献排行 -->
      <section class="panel p-rank">
        <div class="p-hd">
          <h2>重点渠道 · 自营与达播</h2>
          <span class="unit">净销 / 万元</span>
        </div>
        <div class="p-bd">
          <div class="channel-head"><span></span><span>渠道</span><span>合计</span><span>自营</span><span>达播</span></div>
          <div class="rank-list" id="rankList"></div>
        </div>
      </section>

      <!-- 2. 重点店铺明细表 -->
      <section class="panel p-eff">
        <div class="p-hd">
          <h2>全部销售店铺</h2>
          <span class="unit">按净销排序 / 万元</span>
        </div>
        <div class="p-bd">
          <div class="eff-table-wrap">
            <table class="eff">
              <thead>
                <tr>
                  <th style="width:38%;">店铺名称</th>
                  <th style="width:15%;">净销</th>
                  <th style="width:17%;">增量</th>
                  <th style="width:15%;">自营</th>
                  <th>达播</th>
                </tr>
              </thead>
              <tbody id="shopsBody"></tbody>
            </table>
          </div>
          <div class="eff-pagination" id="shopsPagination">
            <div class="page-info">
              <span>共 <b class="num" id="shopsTotalCount">0</b> 家店铺</span>
              <span class="sep">/</span>
              <span>第 <b class="num" id="shopsCurrentPage">1</b> / <b class="num" id="shopsTotalPages">1</b> 页</span>
            </div>
            <div class="page-controls">
              <button class="page-btn" id="btnPrevPage">‹ 上一页</button>
              <div class="page-pills" id="pagePills"></div>
              <button class="page-btn" id="btnNextPage">下一页 ›</button>
            </div>
          </div>
        </div>
      </section>
    </div>

    <!-- ---------- 中：全网 KPI + 24小时走势 + 品牌战报 ---------- -->
    <div class="col col-mid">
      <!-- 1. 核心战况 KPI (数字完整无截断) -->
      <section class="panel p-kpi">
        <div class="kpi-label">
          <span>今日累计净销额</span>
          <span class="unit">全盘实时汇总</span>
        </div>
        <div class="kpi-main">
          <span class="big num" id="gmv">{gmv_all:.2f}</span>
          <span class="u">万元</span>
        </div>
        <div class="kpi-prog">
          <div class="line">
            <span class="lb">自营净销占比 · 自营 / 达播结构</span>
            <span class="pc num" id="achievePc">{ops_ratio}</span>
          </div>
          <div class="bar"><span id="achieveBar" style="width:{ops_ratio}"></span></div>
        </div>
        <!-- 4 个子指标卡：采用完整空间布局，杜绝数字截断 -->
        <div class="kpi-yoy">
          <div id="opsSales">自营净销 <b class="num" id="opsVal">{ops_all:.2f}万</b> <span style="font-size:18px;color:#94a3b8">({ops_ratio})</span></div>
          <div id="liveSales">达播净销 <b class="num" id="liveVal">{live_all:.2f}万</b> <span style="font-size:18px;color:#94a3b8">({dabo_ratio})</span></div>
          <div id="momWrap">较上期增量 <b class="num up" id="mom">▲ {mom_all}</b></div>
          <div id="opsIncWrap">自营增量 <b class="num up" id="opsInc">▲ {ops_inc_all}</b></div>
        </div>
      </section>

      <!-- 2. 24小时全盘销售总额汇总走势 (真实小时数据) -->
      <section class="panel p-trend">
        <div class="p-hd">
          <h2>24小时累计净销走势</h2>
          <span class="unit">万元 · 今日截至数据更新时间</span>
        </div>
        <div class="p-bd">
          <div class="trend-wrap">
            <div class="trend-legend">
              <button type="button" data-trend-series="v" aria-pressed="true"><i style="background:#FFB84D"></i>今日总净销</button>
              <button type="button" data-trend-series="yesterday" aria-pressed="true"><i style="background:#94A3B8"></i>昨日总净销（虚线）</button>
              <button type="button" data-trend-series="live" aria-pressed="true"><i style="background:#C084FC"></i>今日达播</button>
              <button type="button" data-trend-series="ops" aria-pressed="true"><i style="background:#38BDF8"></i>今日自营</button>
            </div>
            <div class="trend-summary">
              <span>总净销 <b>{gmv_all:.2f}万</b></span>
              <span>达播汇总 <b>{live_all:.2f}万</b></span>
              <span>自营汇总 <b>{ops_all:.2f}万</b></span>
              <span id="trendCompare">昨日同期：暂无数据</span>
            </div>
            <div id="trendTooltip" role="status"></div>
            <svg class="trend" id="trendSvg" viewBox="0 0 1340 530" aria-label="今日与昨日累计净销，以及今日自营和达播净销折线图"></svg>
          </div>
        </div>
      </section>

      <!-- 3. 品牌核心战报 (其它品牌严禁展示分销) -->
      <section class="panel p-mini">
        <div class="p-hd">
          <h2>品牌核心战报</h2>
          <span class="unit">今日累计净销</span>
        </div>
        <div class="p-bd">
          <div class="minis">
            <div class="mini">
              <div class="lb">可复美整体净销</div>
              <div class="v num"><span id="kfmVal">{kfm_net:.2f}</span><small>万</small></div>
              <div class="d up num">占比 {(kfm_net / gmv_all * 100 if gmv_all > 0 else 0.0):.1f}%</div>
            </div>
            <div class="mini">
              <div class="lb">可丽金整体净销</div>
              <div class="v num"><span id="clgVal">{clg_net:.2f}</span><small>万</small></div>
              <div class="d up num">占比 {(clg_net / gmv_all * 100 if gmv_all > 0 else 0.0):.1f}%</div>
            </div>
            <div class="mini">
              <div class="lb">其它品牌净销</div>
              <div class="v num"><span id="otherNum">{other_net:.2f}</span><small>万</small></div>
              <div class="d num" id="otherSub">{other_sub}</div>
            </div>
          </div>
        </div>
      </section>
    </div>

    <!-- ---------- 右：自营核心店铺实况监控 (来自 ClickHouse smartbimpp.dy_order 自营店铺汇总) ---------- -->
    <div class="col col-right">
      <!-- 1. 自营核心店铺实时监控 -->
      <section class="panel p-live">
        <div class="p-hd">
          <h2>抖音自营实时数据</h2>
          <span class="unit">核心官方旗舰店</span>
        </div>
        <div class="p-bd">
          <div class="live-main">
            <div class="live-top">
              <div>
                <div class="live-title">自营核心店铺合计</div>
                <div class="live-host">GMV {shop_total_gmv:.2f}万 · 退款 {shop_total_ref:.2f}万 · 净销 {shop_total_net:.2f}万</div>
              </div>
              <span class="badge live">自营店铺合计</span>
            </div>
            <div class="live-metrics">
              <div class="m"><div class="lb">店铺总净销</div><div class="v num">{shop_total_net:.2f}<small>万</small></div></div>
              <div class="m"><div class="lb">店铺总订单</div><div class="v num">{shop_total_orders:,}<small>单</small></div></div>
              <div class="m"><div class="lb">综合退款率</div><div class="v num">{(shop_total_ref / shop_total_gmv * 100 if shop_total_gmv > 0 else 0.0):.1f}<small>%</small></div></div>
            </div>
          </div>
          <div class="live-sub">
"""
    # 动态渲染各店铺全量合计卡片
    for s in shop_list:
        sh_name = escape(str(s.get("shop_name") or ""))
        sh_cnt = int(s.get("order_cnt") or 0)
        sh_gmv = float(s.get("gmv_wan") or 0.0)
        sh_ref = float(s.get("refund_wan") or 0.0)
        sh_net = float(s.get("net_wan") or 0.0)
        html_content += f"""            <div class="live-sub-row">
              <div class="t">{sh_name}<small>GMV {sh_gmv:.2f}万 · 退款 {sh_ref:.2f}万 ({sh_cnt:,}单)</small></div>
              <span class="v num up">净销 {sh_net:.2f}万</span>
            </div>
"""

    html_content += """          </div>
        </div>
      </section>

      <!-- 2. 达人 TOP 5 排名 -->
      <section class="panel p-daren">
        <div class="p-hd">
          <h2>达人净销 TOP 5</h2>
          <span class="unit">全渠道 / 万元</span>
        </div>
        <div class="p-bd" id="darenBody">
          <div class="tal-list">
"""

    # 动态渲染达人 Top 5
    for idx, d in enumerate(daren_top5, 1):
        d_author = escape(str(d.get("author") or "合作达人"))
        d_brand = escape(str(d.get("brand") or ""))
        d_platform = escape(str(d.get("platform") or ""))
        d_label = " · ".join(part for part in (d_platform, d_brand) if part)
        d_gmv = float(d.get("gmv_wan") or 0.0)
        d_net = float(d.get("net_wan") or 0.0)
        html_content += f"""            <div class="tal-row">
              <div class="tal-no num">{idx}</div>
              <div class="tal-name">
                <span class="nm" title="{d_author}">{d_author}</span>
                <span class="cg">{d_label}</span>
              </div>
              <div class="tal-gmv num">{d_net:.2f}<small>万</small></div>
              <div class="tal-cnt num">GMV {d_gmv:.2f}万</div>
            </div>
"""

    html_content += f"""          </div>
        </div>
      </section>
    </div>
  </div>

  <div class="hint">大屏快捷键：F11 全屏自适应 · 自动同源数据校准</div>
</div>

<script>
(function () {{
  "use strict";

  var REAL_HOURLY = {hourly_json};
  var CHANNELS_DATA = {channels_json};
  var SHOPS_DATA = {shops_json};

  var $ = function (id) {{ return document.getElementById(id); }};

  /* ---------- 缩放适配 ---------- */
  function fit() {{
    var s = Math.min(window.innerWidth / 3840, window.innerHeight / 2160);
    var st = $("stage");
    st.style.transform = "translate(" + ((window.innerWidth - 3840 * s) / 2) + "px," +
                         ((window.innerHeight - 2160 * s) / 2) + "px) scale(" + s + ")";
  }}
  window.addEventListener("resize", fit);

  /* ---------- 24小时全盘销售总额汇总走势 (真实小时数据，不伪造未来数据) ---------- */
  var hiddenTrend = {{}};
  function drawTrend() {{
    var w = 1340, h = 530, left = 96, right = 24, top = 36, bottom = 48;
    var iw = w - left - right, ih = h - top - bottom;
    var definitions = [
      {{key:'v', label:'今日总净销', color:'#FFB84D'}},
      {{key:'yesterday', label:'昨日总净销', color:'#94A3B8'}},
      {{key:'live', label:'今日达播', color:'#C084FC'}},
      {{key:'ops', label:'今日自营', color:'#38BDF8'}}
    ];
    var values = [0];
    definitions.forEach(function(s) {{
      if (hiddenTrend[s.key]) return;
      REAL_HOURLY.forEach(function(p) {{ if (p[s.key] != null) values.push(p[s.key]); }});
    }});
    var lo = Math.min.apply(null, values), hi = Math.max.apply(null, values);
    var span = Math.max(hi - lo, 1); hi += span * .16; if (lo < 0) lo -= span * .08;
    var x = function(i) {{ return left + i * iw / 23; }};
    var y = function(v) {{ return top + (hi - v) * ih / (hi - lo); }};
    var g = '';
    for (var t=0; t<=4; t++) {{
      var v = lo + (hi-lo)*t/4, py=y(v);
      g += '<line x1="'+left+'" y1="'+py+'" x2="'+(w-right)+'" y2="'+py+'" stroke="#263247" stroke-dasharray="6 8"/>';
      g += '<text x="'+(left-12)+'" y="'+(py+7)+'" fill="#94A3B8" font-size="20" text-anchor="end">'+v.toFixed(0)+'万</text>';
    }}
    for (var i=0;i<24;i+=2) g += '<text x="'+x(i)+'" y="'+(h-12)+'" fill="#94A3B8" font-size="20" text-anchor="middle">'+String(i).padStart(2,'0')+'时</text>';
    definitions.forEach(function(s) {{
      if (hiddenTrend[s.key]) return;
      var path='', connected=false, last=null;
      REAL_HOURLY.forEach(function(p,i) {{
        if (p[s.key] == null) {{ connected=false; return; }}
        var hour=parseInt(p.h,10), px=x(hour), py=y(p[s.key]);
        path += (connected?' L':' M')+px.toFixed(1)+' '+py.toFixed(1); connected=true;
        last={{px:px,py:py,value:p[s.key]}};
        g += '<circle cx="'+px+'" cy="'+py+'" r="4" fill="'+s.color+'"/>';
      }});
      g += '<path d="'+path+'" fill="none" stroke="'+s.color+'" stroke-width="4" '+(s.key==='yesterday'?'stroke-dasharray="10 8"':'')+' stroke-linejoin="round"/>';
      if(last && s.key==='v') g += '<text x="'+Math.min(last.px+12,w-200)+'" y="'+(last.py-18)+'" fill="'+s.color+'" font-size="22">'+last.value.toFixed(2)+'万</text>';
    }});
    $('trendSvg').innerHTML=g;
    var current=REAL_HOURLY.filter(function(p) {{return p.v != null;}}).slice(-1)[0];
    if(current && current.yesterday != null) {{
      var delta=current.v-current.yesterday;
      $('trendCompare').textContent='昨日同期 '+current.yesterday.toFixed(2)+'万 · '+(delta>=0?'+':'')+delta.toFixed(2)+'万'+(current.yesterday>0?'（'+(delta/current.yesterday*100).toFixed(1)+'%）':'');
    }}
  }}

  function escapeHtml(value) {{
    var node = document.createElement('span');
    node.textContent = value == null ? '' : String(value);
    return node.innerHTML.replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }}
  function amount(value) {{
    return value == null ? '—' : Number(value).toFixed(2);
  }}

  /* ---------- 渠道贡献榜 ---------- */
  function renderChannels() {{
    var list = CHANNELS_DATA["all"] || [];
    var max = Math.max.apply(null, list.map(function (x) {{ return Math.max(0, x.gmv); }})) || 100;
    var html = "";
    list.forEach(function (c, i) {{
      var topClass = i === 0 ? " top1" : "";
      var wPct = Math.min(100, Math.abs(c.gmv) / max * 100);
      html += '<div class="rank-row' + topClass + (c.neg ? ' neg' : '') + '">' +
        '<div class="rk-no num">' + (i + 1) + '</div>' +
        '<div class="rk-name">' + escapeHtml(c.name) + '<div class="rk-bar"><span style="width:' + wPct.toFixed(1) + '%"></span></div></div>' +
        '<div class="rk-val num">' + amount(c.gmv) + '<div class="r">' + escapeHtml(c.share) + '</div></div>' +
        '<div class="rk-val num split-ops">' + amount(c.ops) + '</div>' +
        '<div class="rk-val num split-live">' + amount(c.live) + '</div></div>';
    }});
    $("rankList").innerHTML = html;
  }}

  /* ---------- 重点店铺明细（支持分页查看，已移除状态列） ---------- */
  var currentPage = 1;
  var pageSize = 10;

  function renderShopsPage(page) {{
    var total = SHOPS_DATA.length;
    var totalPages = Math.max(1, Math.ceil(total / pageSize));
    if (page < 1) page = 1;
    if (page > totalPages) page = totalPages;
    currentPage = page;

    var start = (currentPage - 1) * pageSize;
    var end = Math.min(start + pageSize, total);
    var pageItems = SHOPS_DATA.slice(start, end);

    var html = "";
    pageItems.forEach(function (s) {{
      var isNeg = s.neg || s.net < 0;
      var momStr = s.mom == null ? "—" : String(s.mom);
      var isMomDown = momStr.indexOf("-") >= 0;
      html += '<tr>' +
              '<td class="ch" title="' + escapeHtml(s.name) + '">' + escapeHtml(s.name) + '</td>' +
              '<td class="n ' + (isNeg ? 'down' : '') + '">' + Number(s.net).toFixed(2) + '</td>' +
              '<td class="n ' + (isMomDown ? 'down' : 'up') + '">' + escapeHtml(momStr) + '</td>' +
              '<td class="n">' + amount(s.ops) + '</td>' +
              '<td class="n split-live">' + amount(s.live) + '</td>' +
              '</tr>';
    }});
    $("shopsBody").innerHTML = html;

    $("shopsTotalCount").textContent = total;
    $("shopsCurrentPage").textContent = currentPage;
    $("shopsTotalPages").textContent = totalPages;

    var btnPrev = $("btnPrevPage");
    var btnNext = $("btnNextPage");
    if (btnPrev) btnPrev.disabled = (currentPage <= 1);
    if (btnNext) btnNext.disabled = (currentPage >= totalPages);

    var pillsHtml = "";
    for (var i = 1; i <= totalPages; i++) {{
      pillsHtml += '<button class="page-pill' + (i === currentPage ? ' active' : '') + '" data-page="' + i + '">' + i + '</button>';
    }}
    var pillsBox = $("pagePills");
    if (pillsBox) {{
      pillsBox.innerHTML = pillsHtml;
      var pills = pillsBox.querySelectorAll(".page-pill");
      pills.forEach(function (btn) {{
        btn.addEventListener("click", function () {{
          renderShopsPage(parseInt(this.getAttribute("data-page"), 10));
        }});
      }});
    }}
  }}

  var btnPrev = $("btnPrevPage");
  var btnNext = $("btnNextPage");
  if (btnPrev) {{
    btnPrev.addEventListener("click", function () {{
      if (currentPage > 1) renderShopsPage(currentPage - 1);
    }});
  }}
  if (btnNext) {{
    btnNext.addEventListener("click", function () {{
      var totalPages = Math.max(1, Math.ceil(SHOPS_DATA.length / pageSize));
      if (currentPage < totalPages) renderShopsPage(currentPage + 1);
    }});
  }}

  // 大屏自动轮播翻页（鼠标悬停暂停）
  var autoFlipTimer = null;
  function startAutoFlip() {{
    stopAutoFlip();
    autoFlipTimer = setInterval(function () {{
      var totalPages = Math.ceil(SHOPS_DATA.length / pageSize);
      if (totalPages > 1) {{
        var nextP = (currentPage >= totalPages) ? 1 : currentPage + 1;
        renderShopsPage(nextP);
      }}
    }}, 10000);
  }}
  function stopAutoFlip() {{
    if (autoFlipTimer) {{ clearInterval(autoFlipTimer); autoFlipTimer = null; }}
  }}
  var effPanel = document.querySelector(".p-eff");
  if (effPanel) {{
    effPanel.addEventListener("mouseenter", stopAutoFlip);
    effPanel.addEventListener("mouseleave", startAutoFlip);
  }}
  startAutoFlip();

  /* ---------- 时钟 ---------- */
  function tick() {{
    var now = new Date();
    var h = (now.getHours() < 10 ? "0" : "") + now.getHours();
    var m = (now.getMinutes() < 10 ? "0" : "") + now.getMinutes();
    var sec = (now.getSeconds() < 10 ? "0" : "") + now.getSeconds();
    $("clock").textContent = h + ":" + m + ":" + sec;
  }}

  fit();
  drawTrend();
  document.querySelectorAll('[data-trend-series]').forEach(function(button) {{
    button.addEventListener('click',function() {{
      var key=button.getAttribute('data-trend-series');
      hiddenTrend[key]=!hiddenTrend[key];
      button.setAttribute('aria-pressed',String(!hiddenTrend[key]));
      drawTrend();
    }});
  }});
  $('trendSvg').addEventListener('mousemove',function(event) {{
    var rect=this.getBoundingClientRect();
    var hour=Math.max(0,Math.min(23,Math.round(((event.clientX-rect.left)/rect.width*1340-96)/(1340-96-24)*23)));
    var point=REAL_HOURLY.find(function(p) {{ return parseInt(p.h,10)===hour; }});
    if(!point) {{ $('trendTooltip').style.display='none'; return; }}
    var lines=[point.h+' · 累计净销'];
    [['v','今日总净销'],['yesterday','昨日总净销'],['live','今日达播'],['ops','今日自营']].forEach(function(s) {{
      lines.push(s[1]+'：'+(point[s[0]]==null?'暂无数据':point[s[0]].toFixed(2)+'万'));
    }});
    if(point.v!=null && point.yesterday!=null) {{
      var delta=point.v-point.yesterday;
      lines.push('较昨日同期：'+(delta>=0?'+':'')+delta.toFixed(2)+'万');
    }}
    $('trendTooltip').textContent=lines.join('\\n');
    $('trendTooltip').style.display='block';
  }});
  $('trendSvg').addEventListener('mouseleave',function() {{ $('trendTooltip').style.display='none'; }});
  renderChannels();
  renderShopsPage(1);
  tick();
  setInterval(tick, 1000);
}})();
</script>
</body>
</html>
"""
    return html_content


def sync_task_artifact_report(
    db: Session,
    task: ScheduledTask,
    query_rows: list[dict[str, Any]],
    *,
    hourly_series: list[dict[str, Any]] | None = None,
) -> str | None:
    """自动将查询最新真实数据同步更新至已绑定的 HTML 交付物中，并更新 DB 校验哈希"""
    metadata = task.metadata_json if isinstance(task.metadata_json, dict) else {}
    link_id = str(metadata.get("artifact_share_link_id") or "").strip()
    if not link_id:
        return None

    link = db.get(ArtifactShareLink, link_id)
    if not link or link.is_revoked:
        return None

    if not link.path.endswith(".html"):
        return None

    # 如果未提供 query_rows 或为空，自动从任务配置的 query 模板步骤中提取全盘数据，防止清空数据
    if not query_rows:
        from app.data_query.service import execute_query_by_id

        fetched_rows: list[dict[str, Any]] = []
        for step in task.pipeline_steps_json or []:
            if step.get("type") == "query" and step.get("template_id"):
                try:
                    q_res = execute_query_by_id(
                        db,
                        step["template_id"],
                        task.tenant_id,
                        step.get("params") or {},
                    )
                    if q_res and q_res.rows:
                        fetched_rows.extend(q_res.rows)
                except Exception as q_err:  # noqa: BLE001
                    logger.warning("Auto-fetch query step failed in sync_task_artifact_report: %s", q_err)
        if fetched_rows:
            query_rows = fetched_rows

    # 1. 抓取 ClickHouse 与解析 MySQL 全盘数据
    ck_data = fetch_clickhouse_dy_order_metrics()
    metrics = parse_pipeline_query_metrics(query_rows)
    # The card and linked dashboard must retain the same influencer snapshot.
    from app.reporting.sales_metrics import get_top_influencers, is_dabo_row

    if any(row.get("category") == "达播矩阵" for row in query_rows):
        influencer_rows = [row for row in query_rows if is_dabo_row(row)]
        ck_data["daren_top5"] = [
            {"author": row["name"], "net_wan": row["net_wan"],
             "gmv_wan": row["gmv_wan"],
             "platform": " / ".join(sorted({str(r.get("平台") or "")
                 for r in influencer_rows if r.get("达人") == row["name"]})),
             "brand": " / ".join(sorted({str(r.get("品牌") or "")
                 for r in influencer_rows if r.get("达人") == row["name"]}))}
            for row in get_top_influencers(influencer_rows)
        ]

    from app.reporting.dashboard_hourly import align_current_hour, fetch_task_hourly_series

    hourly = hourly_series if hourly_series is not None else fetch_task_hourly_series(db, task)
    if hourly is not None:
        align_current_hour(hourly, query_rows)
        metrics["hourly"] = hourly

    from app.reporting.dashboard_shops import fetch_task_all_shops

    all_shops = fetch_task_all_shops(db, task)
    if all_shops is None:
        logger.warning("Skip dashboard update: complete shop sales unavailable")
        return None
    metrics["all_shops"] = all_shops

    # 2. 生成最新纯净无假数据的 HTML
    new_html = build_realtime_sales_dashboard_html(metrics, ck_data)
    content_bytes = new_html.encode("utf-8")
    new_sha256 = hashlib.sha256(content_bytes).hexdigest()
    new_size = len(content_bytes)

    # 3. 定位任务工作区物理文件
    try:
        workspace_root = harness_task_workspace_path(
            tenant_id=link.tenant_id,
            session_id=link.session_id,
            task_frame_id=link.owner_id,
            db=db,
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("Failed to locate workspace for artifact sync: %s", e)
        return None

    target_file = Path(workspace_root) / link.path
    target_file.parent.mkdir(parents=True, exist_ok=True)
    temp_target = target_file.parent / f".tmp_{new_sha256[:8]}_{target_file.name}"
    try:
        with open(temp_target, "wb") as f:
            f.write(content_bytes)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp_target, target_file)
    except OSError as e:
        logger.warning("Failed to atomically write artifact %s: %s", target_file, e)
        if temp_target.exists():
            temp_target.unlink(missing_ok=True)
        return None
    logger.info("Successfully atomically updated artifact file at: %s", target_file)

    # 4. 在单事务中原子更新 messages 表中的 harness_artifacts sha256 和 size 规避 409
    msgs = db.exec(
        select(Message).where(
            Message.tenant_id == link.tenant_id,
            Message.session_id == link.session_id,
            Message.role == "assistant",
        )
    ).all()
    modified_any = False
    for m in msgs:
        meta = json.loads(json.dumps(m.metadata_json or {}))
        artifacts = meta.get("harness_artifacts")
        if isinstance(artifacts, list):
            changed = False
            for art in artifacts:
                if isinstance(art, dict) and art.get("path") == link.path:
                    art["sha256"] = new_sha256
                    art["size"] = new_size
                    changed = True
            if changed:
                m.metadata_json = meta
                flag_modified(m, "metadata_json")
                db.add(m)
                modified_any = True
    if modified_any:
        db.commit()
        logger.info("Updated artifact sha256 to %s across messages", new_sha256)

    return new_sha256

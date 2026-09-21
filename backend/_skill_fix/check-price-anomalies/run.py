# -*- coding: utf-8 -*-
"""
多店铺价格合规监控 - 全网巡检 (check-price-anomalies/run.py)

职责：巡检京东/抖音/拼多多/唯品会近两天页面普惠价，对比飞书维护基准价与日内波动，输出异动清单。
零处置权：仅预警，绝不改价。使用内置 tool 运行，避免自行编写脚本。

优化点：token/价盘缓存、平台查询循环化、鉴权凭据可经环境变量注入、去掉无实际发送的 webhook 副作用。
"""

import os
import sys

if not os.environ.get("USERNAME"):
    os.environ["USERNAME"] = os.environ.get("USER", "staffdeck")
if not os.environ.get("LOGNAME"):
    os.environ["LOGNAME"] = os.environ["USERNAME"]

if sys.platform.startswith("win"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

import argparse
import json
import re
import time
from datetime import datetime, date
from decimal import Decimal
from pathlib import Path

# 依赖懒加载：pymysql / requests 在各自用到时才 import，
# 使纯逻辑（阈值判定、去重、签名）在无依赖环境下也可被单测直接覆盖。
def _db_config() -> dict:
    """业务库连接配置，延迟构造以便依赖按需加载。"""
    import pymysql
    return {
        "host": os.getenv("PRICE_DB_HOST", "172.16.200.39"),
        "port": int(os.getenv("PRICE_DB_PORT", "3306")),
        "user": os.getenv("PRICE_DB_USER", "root"),
        "password": os.getenv("PRICE_DB_PASSWORD", ""),
        "charset": "utf8mb4",
        "cursorclass": pymysql.cursors.DictCursor,
    }


# ================= 配置（优先平台环境变量注入，避免明文硬编码） =================
# 状态/缓存持久化目录：优先读取平台注入的 SKILL_STATE_DIR，其次兼容旧配置 PRICE_STATE_DIR，
# 最后回退到用户根目录下的 .staffdeck_price_audit。
_raw_state_dir = (
    os.getenv("SKILL_STATE_DIR", "").strip()
    or os.getenv("PRICE_STATE_DIR", "").strip()
)
if _raw_state_dir:
    STATE_DIR = Path(_raw_state_dir).expanduser().resolve()
else:
    STATE_DIR = (Path.home() / ".staffdeck_price_audit").resolve()
APP_TOKEN = os.getenv("FEISHU_APP_TOKEN", "JspJbuCiRawJ4IsVlz5ceX4enrc")
PRODUCT_TABLE_ID = os.getenv("FEISHU_TABLE_ID", "tbl0v7eI79pY8zxj")
SCHEDULE_TABLE_ID = "tblE43CKC009P90S"
APP_ID = os.getenv("FEISHU_APP_ID", "cli_a40af3f077f8d00d")
APP_SECRET = os.getenv("FEISHU_APP_SECRET", "")

# 飞书群自定义机器人 webhook（目标群 -> 设置 -> 群机器人 -> 添加自定义机器人）。
# 留空则跳过推送、不影响巡检本身。优先由平台环境变量注入，其次替换下方默认值。
FEISHU_ALERT_WEBHOOK = os.getenv("FEISHU_ALERT_WEBHOOK", "")

CACHE_FILE = STATE_DIR / ".feishu_cache.json"
CACHE_TTL = 300
# 已告警状态：记录每条异动的最后推送价，用于「变化才推送、不重复推送」。
ALERT_STATE_FILE = STATE_DIR / ".feishu_alert_state.json"

# 「同一天普惠价波动」触发阈值（元）。默认 0.5，避免 0.01 级微跳变在无基准表
# 平台（如拼多多）上产生大量孤立波动告警；由 PRICE_FLUCT_TOLERANCE 覆盖。
FLUCT_TOLERANCE = float(os.getenv("PRICE_FLUCT_TOLERANCE", "0.5"))

# 各平台价格查询：返回 (记录列表)。每条含 platform / record_date / full_time / product_id / actual_puhui_price
PLATFORM_QUERIES = [
    ("京东", "SELECT DATE(`date`) AS record_date, `date` AS full_time, "
            "CAST(`商品id` AS CHAR) AS product_id, "
            "CAST(COALESCE(`店铺名称`, '') AS CHAR) AS shop_name, "
            "CAST(COALESCE(`商品名称`, '') AS CHAR) AS item_name, "
            "CAST(COALESCE(`url`, '') AS CHAR) AS product_url, "
            "(CAST(COALESCE(`京东价`,0) AS DECIMAL(10,2)) - CAST(COALESCE(`官方直降`,0) AS DECIMAL(10,2))) AS actual_puhui_price "
            "FROM `temporary`.`2026-京东核心单品-价格监控` WHERE `date` >= DATE_SUB(CURDATE(), INTERVAL 2 DAY)"),
    ("抖音", "SELECT DATE(`date`) AS record_date, `date` AS full_time, "
            "CAST(COALESCE(NULLIF(TRIM(`sku_id`), ''), `product_id`) AS CHAR) AS product_id, "
            "CAST(`product_id` AS CHAR) AS dy_pid, "
            "CAST(COALESCE(`店铺名`, '') AS CHAR) AS shop_name, "
            "CAST(COALESCE(`product_name`, '') AS CHAR) AS item_name, "
            "TRIM(`spec`) AS spec, "
            "(CAST(COALESCE(`origin_price`,0) AS DECIMAL(10,2)) - CAST(COALESCE(`money_off_campaign`,0) AS DECIMAL(10,2))) AS actual_puhui_price "
            "FROM `ds_dy`.`dy_product_price_mechanism` WHERE `date` >= DATE_SUB(CURDATE(), INTERVAL 2 DAY)"),
    ("拼多多", "SELECT DATE(`fetch_time`) AS record_date, `fetch_time` AS full_time, "
             "CAST(`goods_id` AS CHAR) AS product_id, "
             "CAST(COALESCE(`shop_name`, '') AS CHAR) AS shop_name, "
             "CAST(COALESCE(`product_name`, '') AS CHAR) AS item_name, "
             "CAST(COALESCE(`url`, '') AS CHAR) AS product_url, "
             "CAST(COALESCE(`page_price`,0) AS DECIMAL(10,2)) AS actual_puhui_price "
             "FROM `ds_pdd`.`pdd_product_price_mechanism_test` WHERE `fetch_time` >= DATE_SUB(CURDATE(), INTERVAL 2 DAY)"),
    ("唯品会", "SELECT DATE(`获取日期`) AS record_date, `获取日期` AS full_time, "
             "CAST(`mid` AS CHAR) AS product_id, "
             "CAST(COALESCE(`店铺名称`, '') AS CHAR) AS shop_name, "
             "CAST(COALESCE(`产品标题`, '') AS CHAR) AS item_name, "
             "CAST(COALESCE(`单品链接`, '') AS CHAR) AS product_url, "
             "CAST(COALESCE(`页面价`,0) AS DECIMAL(10,2)) AS actual_puhui_price "
             "FROM `ds_wph`.`wph_product_price_mechanism` WHERE `获取日期` >= DATE_SUB(CURDATE(), INTERVAL 2 DAY)"),
]

# 各平台在飞书商品表里的匹配字段（拼多多暂无业务，不参与飞书基准匹配）
PLATFORM_MATCH_FIELDS = {
    "京东": "京东商品链接",
    "抖音": "抖音SKUID",
    "唯品会": "唯品mid",
}


def _extract_platform_key(platform: str, feishu_value) -> str | None:
    """把飞书各平台字段值转成可与 DB product_id 直接相等的匹配键。"""
    if feishu_value is None:
        return None
    s = str(feishu_value).strip()
    if not s:
        return None
    if platform == "京东":
        # 京东商品链接可能存完整 URL（如 https://item.jd.com/1234567890.html），
        # 提取其中的数字商品ID；若本身是纯数字则直接使用。
        m = re.search(r"\b\d{6,}\b", s)
        return m.group(0) if m else s
    return s


def _reconstruct_flat(nested_benchmarks: dict, nested_nicknames: dict) -> tuple[dict, dict]:
    """把缓存的「平台->匹配键->值」嵌套结构还原为扁平 (平台, 匹配键) 键。"""
    benchmarks: dict = {}
    nicknames: dict = {}
    for plat, m in nested_benchmarks.items():
        for k, v in m.items():
            benchmarks[(plat, k)] = float(v)
    for plat, m in nested_nicknames.items():
        for k, v in m.items():
            if v:
                nicknames[(plat, k)] = str(v)
    return benchmarks, nicknames


def _log(*args) -> None:
    sys.stderr.write("[%s] " % time.strftime("%H:%M:%S") + " ".join(str(a) for a in args) + "\n")


def get_feishu_token() -> str:
    if not APP_ID or not APP_SECRET:
        return ""
    import requests
    try:
        r = requests.post(
            "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
            json={"app_id": APP_ID, "app_secret": APP_SECRET},
            timeout=5,
        )
        return r.json().get("tenant_access_token", "")
    except Exception as exc:
        _log("获取飞书 token 失败:", exc)
        return ""


def current_stage(token: str) -> tuple[str, str]:
    default = ("S促", "S促普惠价")
    if not token:
        return default
    import requests
    try:
        url = f"https://open.feishu.cn/open-apis/bitable/v1/apps/{APP_TOKEN}/tables/{SCHEDULE_TABLE_ID}/records"
        r = requests.get(url, headers={"Authorization": f"Bearer {token}"}, params={"page_size": 100}, timeout=4)
        now_ms = time.time() * 1000
        for item in r.json().get("data", {}).get("items", []):
            f = item.get("fields", {})
            st = str(f.get("大促阶段") or f.get("阶段") or "").upper()
            tn = f.get("时间节点") or f.get("时间范围")
            s, e = None, None
            if isinstance(tn, list) and len(tn) >= 2:
                s, e = tn[0], tn[1]
            if isinstance(s, (int, float)) and isinstance(e, (int, float)) and s <= now_ms <= e:
                if "618" in st:
                    return ("618", "618价格")
                if any(k in st for k in ("D11", "双11", "11.11")):
                    return ("D11", "D11价格")
    except Exception as exc:
        _log("判断大促阶段失败:", exc)
    return default


def load_benchmarks(refresh: bool = False) -> tuple[dict, dict, str, str]:
    """从飞书加载维护基准价与商品昵称（按平台匹配键 (平台,匹配键) -> 基准价/昵称），带缓存。"""
    if not refresh and CACHE_FILE.exists():
        try:
            data = json.loads(CACHE_FILE.read_text(encoding="utf-8"))
            if time.time() - data.get("ts", 0) < CACHE_TTL:
                benches, nick = _reconstruct_flat(data.get("benchmarks", {}), data.get("nicknames", {}))
                return (benches, nick, data.get("stage", "S促"), data.get("col", "S促普惠价"))
        except Exception:
            pass

    token = get_feishu_token()
    import requests
    stage, col = current_stage(token)
    benchmarks: dict = {}
    nicknames: dict = {}
    if token:
        url = f"https://open.feishu.cn/open-apis/bitable/v1/apps/{APP_TOKEN}/tables/{PRODUCT_TABLE_ID}/records"
        page_token = ""
        while True:
            params: dict = {"page_size": 500}
            if page_token:
                params["page_token"] = page_token
            try:
                res = requests.get(url, headers={"Authorization": f"Bearer {token}"}, params=params, timeout=8)
            except Exception as exc:
                _log("拉取飞书基准价失败:", exc)
                break
            data = res.json()
            if data.get("code") != 0:
                _log("飞书接口返回错误:", data.get("code"))
                break
            for item in data.get("data", {}).get("items", []):
                f = item.get("fields", {})
                nick = str(f.get("商品昵称") or f.get("商品名称") or f.get("商品名") or "").strip()
                price = f.get(col) or f.get("S促普惠价")
                if not price:
                    continue
                # 按平台取对应飞书字段做匹配键（拼多多暂无业务，不参与基准匹配）
                for platform, field in PLATFORM_MATCH_FIELDS.items():
                    key = _extract_platform_key(platform, f.get(field))
                    if not key:
                        continue
                    tup = (platform, key)
                    try:
                        benchmarks[tup] = float(price)
                    except (TypeError, ValueError):
                        continue
                    if nick:
                        nicknames.setdefault(tup, nick)
            if not data.get("data", {}).get("has_more"):
                break
            page_token = data.get("data", {}).get("page_token", "")
    def _flatten(m):
        out: dict = {}
        for (plat, k), v in m.items():
            out.setdefault(plat, {})[k] = v
        return out

    try:
        _ensure_state_dir()
        CACHE_FILE.write_text(
            json.dumps({"ts": time.time(), "stage": stage, "col": col,
                        "benchmarks": _flatten(benchmarks), "nicknames": _flatten(nicknames)},
                       ensure_ascii=False),
            encoding="utf-8",
        )
    except Exception:
        pass
    return benchmarks, nicknames, stage, col


def fetch_platform_records(queries: list[tuple[str, str]]):
    """连接业务库，按平台查询近两天价格记录。"""
    import pymysql
    conn = pymysql.connect(**_db_config(), connect_timeout=10)
    records = []
    try:
        with conn.cursor() as cur:
            for platform, sql in queries:
                try:
                    cur.execute(sql)
                    batch = cur.fetchall()
                    for row in batch:
                        row["platform"] = platform
                    records.extend(batch)
                    _log(f"{platform}: {len(batch)} 条")
                except Exception as exc:
                    _log(f"{platform} 查询失败:", exc)
    finally:
        conn.close()
    return records


def run_audit() -> None:
    benchmarks, nicknames, stage, col = load_benchmarks()
    records = fetch_platform_records(PLATFORM_QUERIES)

    platform_db_times: dict[str, str] = {}
    product_history: dict[tuple, list] = {}
    for r in records:
        plat = str(r.get("platform") or "")
        t = str(r.get("full_time") or "").strip()
        if plat and t:
            if plat not in platform_db_times or t > platform_db_times[plat]:
                platform_db_times[plat] = t
        pid = str(r.get("product_id") or "").strip()
        if pid:
            product_history.setdefault((r["platform"], pid), []).append(r)

    anomalies = []
    for (platform, pid), p_records in product_history.items():
        sorted_records = sorted(p_records, key=lambda x: str(x["full_time"]), reverse=True)
        latest_rec = sorted_records[0]
        latest_price = float(latest_rec["actual_puhui_price"])
        product_name = nicknames.get((platform, pid), "") or str(latest_rec.get("item_name") or "")
        bench = benchmarks.get((platform, pid))
        shop_name = str(latest_rec.get("shop_name") or "")
        product_url = str(latest_rec.get("product_url") or "").strip()
        db_time = str(latest_rec.get("full_time") or latest_rec.get("record_date") or "").strip()
        if not product_url:
            if platform == "京东" and pid:
                product_url = f"https://item.jd.com/{pid}.html"
            elif platform == "抖音":
                dy_id = latest_rec.get("dy_pid") or pid
                product_url = f"https://haohuo.jinritemai.com/views/product/item2?id={dy_id}"
            elif platform == "唯品会" and pid:
                product_url = f"https://detail.vip.com/detail-0-{pid}.html"

        # 规则 1：跌破维护普惠价（按最新采集价格判定，单商品单次巡检最多 1 条，避免多日重复）
        if bench is not None:
            diff = round(latest_price - bench, 2)
            if diff < 0:
                anomalies.append({
                    "platform": platform,
                    "product_id": pid,
                    "product_code": pid,
                    "check_date": str(latest_rec.get("record_date")),
                    "db_update_time": db_time,
                    "anomaly_type": "跌破维护普惠价",
                    "risk_level": "严重",
                    "product_name": product_name,
                    "shop_name": shop_name,
                    "product_url": product_url,
                    "current_price": latest_price,
                    "benchmark_price": bench,
                    "diff_amount": diff,
                    "detail": f"当前最新价 ¥{latest_price} 已跌破飞书基准({stage}) ¥{bench}，跌破 ¥{abs(diff)}",
                })

        # 规则 2：同一天普惠价波动（按日分析极差；若多日有波动，取最近发生波动的日期，单商品最多 1 条）
        day_groups: dict[str, list] = {}
        for r in p_records:
            day_groups.setdefault(str(r.get("record_date")), []).append(r)

        fluct_days = []
        for r_date in sorted(day_groups.keys(), reverse=True):
            grp = day_groups[r_date]
            prices = [float(x["actual_puhui_price"]) for x in grp if x["actual_puhui_price"] is not None]
            if not prices:
                continue
            day_min, day_max = min(prices), max(prices)
            if day_max - day_min > FLUCT_TOLERANCE:
                day_latest = float(sorted(grp, key=lambda x: str(x["full_time"]), reverse=True)[0]["actual_puhui_price"])
                fluct_days.append({
                    "date": r_date,
                    "min": day_min,
                    "max": day_max,
                    "swing": round(day_max - day_min, 2),
                    "latest": day_latest,
                })

        if fluct_days:
            latest_fluct = fluct_days[0]
            anomalies.append({
                "platform": platform,
                "product_id": pid,
                "product_code": pid,
                "check_date": latest_fluct["date"],
                "db_update_time": db_time,
                "anomaly_type": "同一天普惠价波动",
                "risk_level": "高",
                "product_name": product_name,
                "shop_name": shop_name,
                "product_url": product_url,
                "current_price": latest_fluct["latest"],
                "day_min": latest_fluct["min"],
                "day_max": latest_fluct["max"],
                "fluctuation_amount": latest_fluct["swing"],
                "detail": f"{latest_fluct['date']} 价格跳变，最低 ¥{latest_fluct['min']}，最高 ¥{latest_fluct['max']}，日内极差 ¥{latest_fluct['swing']}",
            })

    anomalies.sort(key=lambda x: abs(x.get("diff_amount") or x.get("fluctuation_amount") or 0), reverse=True)

    report = {
        "status": "success",
        "check_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "platform_db_times": platform_db_times,
        "total_records_checked": len(records),
        "total_anomalies_found": len(anomalies),
        "feishu_benchmark_active": len(benchmarks) > 0,
        "current_promotion_stage": stage,
        "benchmark_price_column": col,
        "anomalies": anomalies[:500],  # 限制返回上限，避免输出过大
        "boundary_notice": "【合规红线】：本数据仅做异常预警，禁止自动改价，全部异动须人工二次复核确认。",
    }
    print(json.dumps(report, ensure_ascii=False, indent=2, cls=DecimalEncoder))

    # 批次内签名去重，获取本轮唯一的未恢复异常全集
    deduped_anomalies = []
    seen_in_batch: set[tuple] = set()
    for a in anomalies:
        sig = _alert_signature(a)
        if sig in seen_in_batch:
            continue
        seen_in_batch.add(sig)
        deduped_anomalies.append(a)

    # 检查是否有新增异常或现价变动
    alert_state = _load_alert_state()
    changed = [a for a in deduped_anomalies if _should_push(a, alert_state)]
    if changed:
        # 一旦出现新增或变动异常，累计推送当前所有未恢复异常
        pushed = _notify_feishu(report, deduped_anomalies)
        if pushed is None:
            pushed = deduped_anomalies
    else:
        pushed = []
    _persist_alert_state(alert_state, deduped_anomalies, pushed_items=pushed)


def _ensure_state_dir() -> Path:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    return STATE_DIR


def _alert_signature(item: dict) -> tuple:
    """一条异动的唯一签名：平台 + 商品 + 异常类型。"""
    return (str(item.get("platform")), str(item.get("product_id")), str(item.get("anomaly_type")))


META_KEY = "_meta"


def _today_str() -> str:
    return date.today().isoformat()


def _load_alert_state() -> dict:
    """读取上次已告警状态（签名 -> 最后推送价）。日期变更或损坏/缺失则重置。"""
    if not ALERT_STATE_FILE.exists():
        return {META_KEY: {"last_push_date": _today_str()}}
    try:
        state = json.loads(ALERT_STATE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {META_KEY: {"last_push_date": _today_str()}}
    meta = state.get(META_KEY, {})
    if meta.get("last_push_date") != _today_str():
        return {META_KEY: {"last_push_date": _today_str()}}
    return state


def _should_push(item: dict, state: dict) -> bool:
    """仅当该异动「新增」或「价格有变化」时才推送，避免对同一异动重复推送。"""
    key = "|".join(_alert_signature(item))
    prev = state.get(key)
    if prev is None:
        return True  # 新异动，首次推送
    try:
        now = float(item.get("current_price"))
        last = float(prev.get("current_price"))
        return abs(now - last) > 0.005  # 现价变化才再次推送
    except (TypeError, ValueError):
        return True


def _persist_alert_state(state: dict, items: list, pushed_items: list | None = None) -> None:
    """保存告警状态。成功推送的更新现价，未变化或推送失败的保留原状态，已恢复的自动清除。"""
    if pushed_items is None:
        pushed_items = items
    pushed_keys = {"|".join(_alert_signature(it)): it for it in pushed_items}
    kept: dict = {META_KEY: {"last_push_date": _today_str()}}
    for it in items:
        key = "|".join(_alert_signature(it))
        if key in pushed_keys:
            try:
                kept[key] = {"current_price": float(it.get("current_price"))}
            except (TypeError, ValueError):
                kept[key] = {"current_price": None}
        elif key in state:
            kept[key] = state[key]
    try:
        _ensure_state_dir()
        tmp_file = ALERT_STATE_FILE.with_suffix(".json.tmp")
        tmp_file.write_text(json.dumps(kept, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp_file, ALERT_STATE_FILE)
    except OSError as exc:
        _log("保存告警状态失败:", exc)


def _notify_feishu(report: dict, items: list) -> list[dict]:
    """对异常按平台分类各推送一张独立卡片；出现新变化时累计推送全部未恢复异常。"""
    url = (FEISHU_ALERT_WEBHOOK or "").strip()
    if not url or "xxx" in url or "请替换" in url:
        _log("未配置有效的 FEISHU_ALERT_WEBHOOK，跳过飞书群推送。")
        return []

    # 按平台分组（京东/抖音/拼多多/唯品会），只给有异动的平台推送
    by_platform: dict[str, list] = {}
    for item in items:
        by_platform.setdefault(str(item.get("platform") or "未知"), []).append(item)

    successful_items = []
    for platform, platform_items in by_platform.items():
        pushed = _push_platform_card(url, platform, platform_items, report)
        if pushed:
            successful_items.extend(pushed)
    return successful_items


def _push_platform_card(url: str, platform: str, items: list, report: dict) -> list[dict]:
    """推送单个平台的一张飞书 CardKit 2.0 表格告警卡片。"""
    seen_card_sigs: set[tuple] = set()
    deduped_items = []
    for it in items:
        sig = _alert_signature(it)
        if sig not in seen_card_sigs:
            seen_card_sigs.add(sig)
            deduped_items.append(it)

    # 优先使用数据库中该平台的最新更新时间，其次取该批次异动的最新更新时间，兜底巡检运行时间
    plat_db_time = (report.get("platform_db_times") or {}).get(platform)
    if not plat_db_time:
        item_times = [
            str(it.get("db_update_time") or it.get("full_time") or "").strip()
            for it in deduped_items
            if it.get("db_update_time") or it.get("full_time")
        ]
        if item_times:
            plat_db_time = max(item_times)

    if plat_db_time:
        time_text = str(plat_db_time)[:19]
    else:
        time_text = report.get("check_time") or datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    title_text = f"{platform}-普惠价破价预警-{time_text}"

    rows = []
    for it in deduped_items[:50]:
        shop = str(it.get("shop_name") or "-")
        pname = str(it.get("product_name") or it.get("product_id") or "-")
        pcode = str(it.get("product_code") or it.get("product_id") or "-")
        link_val = (it.get("product_url") or "").strip()
        link_cell = f"[查看商品]({link_val})" if link_val.startswith("http") else "-"

        bench = it.get("benchmark_price")
        bench_cell = f"¥{float(bench):.2f}" if bench is not None else "-"

        cur_p = it.get("current_price")
        cur_cell = f"¥{float(cur_p):.2f}" if cur_p is not None else "-"

        diff = it.get("diff_amount")
        if diff is not None:
            diff_cell = f"{float(diff):+.2f}"
        elif it.get("fluctuation_amount") is not None:
            diff_cell = f"波动¥{it['fluctuation_amount']}"
        else:
            diff_cell = "-"

        rows.append({
            "shop_name": shop,
            "product_name": pname,
            "product_code": pcode,
            "product_url": link_cell,
            "benchmark_price": bench_cell,
            "actual_price": cur_cell,
            "diff_amount": diff_cell,
        })

    import requests

    payload = {
        "msg_type": "interactive",
        "card": {
            "schema": "2.0",
            "config": {
                "wide_screen_mode": True,
                "update_multi": True,
            },
            "header": {
                "template": "red",
                "title": {
                    "tag": "plain_text",
                    "content": title_text,
                },
            },
            "body": {
                "elements": [
                    {
                        "tag": "table",
                        "page_size": 10,
                        "row_height": "low",
                        "header_style": {
                            "text_align": "left",
                            "bold": True,
                        },
                        "columns": [
                            {"name": "shop_name", "display_name": "店铺名", "data_type": "text", "width": "auto"},
                            {"name": "product_name", "display_name": "产品昵称", "data_type": "text", "width": "auto"},
                            {"name": "product_code", "display_name": "产品编码", "data_type": "text", "width": "auto"},
                            {"name": "product_url", "display_name": "产品链接", "data_type": "lark_md", "width": "auto"},
                            {"name": "benchmark_price", "display_name": "普惠锚定价", "data_type": "text", "width": "auto"},
                            {"name": "actual_price", "display_name": "实际执行价", "data_type": "text", "width": "auto"},
                            {"name": "diff_amount", "display_name": "异动差值", "data_type": "text", "width": "auto"},
                        ],
                        "rows": rows,
                    },
                    {
                        "tag": "markdown",
                        "content": "<font color='grey'>【合规红线】本数据仅做异常预警，禁止自动改价，全部异动须人工二次复核确认。</font>",
                    },
                ]
            },
        },
    }
    try:
        resp = requests.post(url, json=payload, timeout=8)
        code = resp.json().get("code") if resp.headers.get("content-type", "").startswith("application/json") else None
        if resp.status_code != 200 or code not in (0, None):
            _log(f"推送 [ {platform} ] 卡片失败:", resp.status_code, resp.text[:300])
            return []
        else:
            _log(f"已推送飞书群 [ {platform} ] 告警卡片（{len(deduped_items)} 条）。")
            return deduped_items
    except Exception as exc:
        _log(f"推送 [ {platform} ] 飞书群异常:", exc)
        return []


class DecimalEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, Decimal):
            return float(obj)
        if isinstance(obj, (datetime, date)):
            return obj.strftime("%Y-%m-%d %H:%M:%S") if isinstance(obj, datetime) else obj.strftime("%Y-%m-%d")
        return super().default(obj)


def main() -> None:
    parser = argparse.ArgumentParser(description="多店铺价格异动全网巡检")
    parser.add_argument("--mode", choices=["audit"], default="audit")
    parser.add_argument("--refresh", action="store_true", help="强制刷新飞书基准价缓存")
    args = parser.parse_args()

    try:
        if args.refresh:
            load_benchmarks(refresh=True)
        run_audit()
    except Exception as exc:
        print(json.dumps({"status": "error", "message": f"巡检异常: {exc}"}, ensure_ascii=False))


if __name__ == "__main__":
    main()
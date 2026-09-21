"""
价格检查 · 判断层 (price_analyst.py)

把确定性巡检（run.py）从「报警器」变成「会分析的分析员」的上层。MVP 提供两种模式：

  --mode analyze
      复用 run.py 的确定性取数（subprocess 调用 run.py --mode audit，解析其 stdout JSON），
      对原始异动清单做**确定性判断**：
        · 跨平台根因归类：按飞书商品昵称把同一商品在不同平台（京东/抖音/唯品会）的联动
          异动聚为一条，识别「全链条同受影响」而非 N 条孤立报警；
        · 去噪/置信标记：区分「跌破维护基准」（严重/需处置）与仅「日内波动」，
          并给每条挂 confidence + 建议；
        · 产出 summary / ai_material：给 Agent 的浓缩文本，让 LLM 只做最终汇报与话术。

  --mode down  --platform <平台> --sku <id> [--limit N]
      单商品下钻：查该 SKU 近 N 条价格时序，算 min/max/均值/当前价/是否跌破基准，
      供 Agent 深度问答「这个价为什么这么改」。

设计约束：
  · 所有判断逻辑确定性、无 LLM 依赖，纯函数可被单测直接覆盖；
  · analyze 不重复取数 —— 一律 subprocess 调用 run.py，避免两处 SQL/基准逻辑漂移；
  · 铁律：只分析、只预警、绝不改价。输出仅作人工复核依据。
"""

import argparse
import json
import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

DIR = Path(__file__).resolve().parent
RUN_PY = DIR / "run.py"

# 各平台通过飞书匹配字段做跨平台关联；这里用「商品昵称」作为跨平台归类键来源。
NICKNAME_FIELDS = ("商品昵称", "商品名称", "商品名")

# 跌破「维护基准」的判定：跌破幅度小于该绝对额时标记为低置信、并入摘要而非逐条告警
LOW_CONF_WINDOW = float(os.getenv("PRICE_LOW_CONF_WINDOW", "0.5"))
# 下钻默认取最近记录条数
DOWN_DEFAULT_LIMIT = 50

# 记忆学习的持久化目录：与 run.py 的去重状态同源（PRICE_STATE_DIR），保证跨 run 稳定。
# label 取值：常态调价 / 属实跌破（运营人工复核后回写，仅用于降噪标注，不自动改价）。
_MEM_STATE_DIR = Path(os.getenv("PRICE_STATE_DIR", "") or "").expanduser()
if not _MEM_STATE_DIR.is_absolute() or not _MEM_STATE_DIR.name:
    _MEM_STATE_DIR = Path.home() / ".staffdeck_price_audit"
MEMORY_FILE = _MEM_STATE_DIR / "price_memory.json"


# ---------------------------------------------------------------------------
# 纯逻辑：跨平台归类
# ---------------------------------------------------------------------------


def _group_key(item: dict) -> tuple[str, str, bool]:
    """跨平台归类键。有商品昵称 -> (昵称, 'by_brand')；否则退化为 (平台+SKU, 'by_sku')。"""
    name = str(item.get("product_name") or "").strip()
    if name:
        return (name, "by_brand")
    return (f"{item.get('platform')}:{item.get('product_id')}", "by_sku")


def group_by_merchandise(anomalies: list[dict]) -> dict:
    """按商品维度聚合异动，识别同商品跨平台联动。

    返回 {
      "groups": [ {name, kind, platforms, severities, items, impact, top} ... ],
      "cross_platform_groups": int,   # 涉及 >=2 个平台的组合
    }
    """
    buckets: dict[tuple[str, str], list[dict]] = {}
    for it in anomalies:
        buckets.setdefault(_group_key(it), []).append(it)

    groups = []
    cross = 0
    for (key, kind), items in buckets.items():
        items_sorted = sorted(
            items,
            key=lambda x: abs(float(x.get("diff_amount") or x.get("fluctuation_amount") or 0)),
            reverse=True,
        )
        platforms = sorted({str(it.get("platform")) for it in items})
        severities = sorted({str(it.get("risk_level") or "低") for it in items})
        impact = max(
            (abs(float(it.get("diff_amount") or 0)) for it in items),
            default=0.0,
        ) or max(
            (float(it.get("fluctuation_amount") or 0) for it in items),
            default=0.0,
        )
        if len(platforms) >= 2:
            cross += 1
        groups.append({
            "name": key,
            "kind": kind,
            "platforms": platforms,
            "severities": severities,
            "cross_platform": len(platforms) >= 2,
            "impact": round(impact, 2),
            "item_count": len(items),
            "items": items_sorted,
        })
    groups.sort(key=lambda g: g["impact"], reverse=True)
    return {"groups": groups, "cross_platform_groups": cross}


# ---------------------------------------------------------------------------
# 纯逻辑：去噪与置信
# ---------------------------------------------------------------------------


def _confidence(item: dict) -> tuple[str, str]:
    """返回 (confidence, 归类)。跌破基准=严重/高置信；纯日内波动视幅度。"""
    raw_diff = item.get("diff_amount")
    diff = None
    if raw_diff is not None:
        try:
            diff = float(raw_diff)
        except (TypeError, ValueError):
            diff = None
    if diff is not None and diff < 0:
        if abs(float(diff)) >= LOW_CONF_WINDOW:
            return ("高", "跌破维护普惠价")
        return ("中", "跌破维护普惠价(小幅)")
    amount = float(item.get("fluctuation_amount") or 0)
    if amount <= 0:
        return ("低", "无基准/无价格差，存疑")
    if amount >= 1.0:
        return ("高", "显著日内波动")
    if amount >= LOW_CONF_WINDOW:
        return ("中", "日内波动")
    return ("低", "微幅波动(可能为正常调价)")


def denoise(anomalies: list[dict]) -> dict:
    """给每条打 confidence / 归类，并把低置信项独立拆分出来（不入逐条告警）。"""
    high, low = [], []
    for it in anomalies:
        conf, category = _confidence(it)
        it = dict(it)
        it["confidence"] = conf
        it["category"] = category
        if conf == "低":
            low.append(it)
        else:
            high.append(it)
    return {"high": high, "low": low}


# ---------------------------------------------------------------------------
# 纯逻辑：建议与摘要
# ---------------------------------------------------------------------------


def recommendation_for(item: dict) -> str:
    if item.get("diff_amount") is not None and float(item.get("diff_amount") or 0) < 0:
        return (
            "疑似跌破维护普惠价，请责任运营核对后台是否叠加了不合理的优惠/改价，"
            "按档期保价规则复核；系统无自动改价权限，处置须人工确认。"
        )
    return (
        "疑似日内跳变/正常调价，请核对是否在档期优惠或临时改价范畴；"
        "若属常态可记录为「周期性平价」以便后续降低告警优先级。"
    )


def build_summary(anomalies: list[dict], grouped: list[dict]) -> dict:
    severe = sum(
        1 for it in anomalies
        if (it.get("diff_amount") is not None and float(it.get("diff_amount") or 0) < 0)
    )
    platforms = sorted({str(it.get("platform")) for it in anomalies})
    top = grouped[0] if grouped else {}
    return {
        "total_anomalies": len(anomalies),
        "severe_breach_count": severe,
        "involved_platforms": platforms,
        "cross_platform_groups": sum(1 for g in grouped if g.get("cross_platform")),
        "top_impact_group": (top.get("name") if top else None),
        "top_impact": (top.get("impact") if top else 0),
    }


def build_ai_material(report: dict) -> str:
    """给 Agent 的浓缩判断材料：让 LLM 只做最终汇报，不做可复现分析。"""
    lines = [
        (f"巡检时间 {report.get('check_time')}｜大促阶段 {report.get('stage')}｜"
         f"共 {report['summary']['total_anomalies']} 条异动")
    ]
    if report["summary"]["severe_breach_count"]:
        lines.append(f"其中跌破维护基准 {report['summary']['severe_breach_count']} 条（需优先处置）。")
    if report["summary"]["cross_platform_groups"]:
        lines.append(f"跨平台联动商品 {report['summary']['cross_platform_groups']} 个。")
    for g in report["grouped"][:10]:
        sev = "/".join(g["severities"])
        mem_hits = [it.get("memory_note") for it in g["items"] if it.get("memory_note")]
        suffix = f"｜{'；'.join(dict.fromkeys(mem_hits))}" if mem_hits else ""
        lines.append(f"- [{sev}] {g['name']}：平台 {'/'.join(g['platforms'])}，"
                     f"极差 ¥{g['impact']}，{g['item_count']} 条{suffix}")
    lines.append("【合规红线】仅预警，禁止自动改价，全部异动须人工二次复核。")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 记忆学习：经验库（运营历史处置结论，用于降噪标注，不自动改价）
# ---------------------------------------------------------------------------

# 可选记忆标签：只有运营确认过的结论才允许写入，避免噪声污染。
MEM_LABELS = ("常态调价", "属实跌破")


def _mem_key(platform: str, sku: str) -> str:
    """经验库键：平台 + SKU。sku 为 None/空时不可入记忆（不跨行匹配错）。"""
    sku = str(sku or "").strip()
    return f"{str(platform or '').strip()}|{sku}" if sku else ""


def _now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def load_memory() -> dict:
    """读经验库；文件缺失/损坏时返回空 dict（不抛致命异常）。"""
    try:
        with MEMORY_FILE.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def save_memory(data: dict) -> None:
    """写经验库；父目录不存在时自动创建。"""
    MEMORY_FILE.parent.mkdir(parents=True, exist_ok=True)
    with MEMORY_FILE.open("w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2, sort_keys=True)


def learn(platform: str, sku: str, label: str, note: str = "") -> dict:
    """运营人工复核后回写一条处置结论；label 必须属于 MEM_LABELS，否则拒绝。"""
    label = str(label or "").strip()
    if label not in MEM_LABELS:
        raise ValueError(f"非法记忆标签:{label}；可选 {MEM_LABELS}")
    key = _mem_key(platform, sku)
    if not key:
        raise ValueError("记忆写入需要非空 platform 与 sku。")
    memory = load_memory()
    prev = memory.get(key, {})
    memory[key] = {
        "label": label,
        "note": str(note or "").strip(),
        "count": int(prev.get("count") or 0) + 1,
        "first_seen": prev.get("first_seen") or _now_iso(),
        "updated": _now_iso(),
    }
    save_memory(memory)
    return {"key": key, "label": label, "count": memory[key]["count"]}


def memory_note_for(platform: str, sku: str, memory: dict | None = None) -> str:
    """hit 时返回经验结论的浓缩一句；miss 返回空串。供 analyze/down 标注用。"""
    memory = memory if memory is not None else load_memory()
    key = _mem_key(platform, sku)
    entry = memory.get(key) if key else None
    if not entry:
        return ""
    note = str(entry.get("note") or "").strip()
    return f"历史判定为「{entry.get('label')}」" + (f"：{note}" if note else "")


def annotate_memory(items: list[dict], memory: dict | None = None) -> list[dict]:
    """给已公开的 items 追加 memory_label/memory_note；不改动置信/分类。"""
    memory = memory if memory is not None else load_memory()
    out = []
    for it in items:
        note = memory_note_for(it.get("platform"), it.get("product_id"), memory)
        if note:
            it = dict(it)
            it["memory_label"] = str(memory.get(_mem_key(it["platform"], it["product_id"]) or {}).get("label"))
            it["memory_note"] = note
        out.append(it)
    return out


# ---------------------------------------------------------------------------
# 取数：analyze 复用 run.py；down 单商品查询
# ---------------------------------------------------------------------------


def run_raw_audit() -> dict:
    """以子进程调用 run.py --mode audit，解析其 stdout JSON。不重复取数。"""
    proc = subprocess.run(
        [sys.executable, str(RUN_PY), "--mode", "audit"],
        capture_output=True, text=True, timeout=600, check=False,
    )
    try:
        data = json.loads(proc.stdout or "")
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"run.py 输出非合法 JSON：{exc}；stderr={proc.stderr[:300]}") from exc
    if isinstance(data, dict) and data.get("status") != "success":
        raise RuntimeError(f"run.py 未成功：{data.get('message') or data.get('status')}")
    return data if isinstance(data, dict) else {}


# 平台下单商品历史查询配置：(表, 时间列, 价格表达式 SQL 片段, 商品匹配列表达式)
# 时间列为裸列名（build_down_sql 统一包反引号，避免双重转义）；价格表达式与商品
# 匹配列均为完整 SQL 片段、与 run.py 保持一致，避免口径漂移。
_DOWN_SQL = {
    "京东": (
        "`temporary`.`2026-京东核心单品-价格监控`",
        "date",
        "(CAST(COALESCE(`京东价`,0) AS DECIMAL(10,2)) - CAST(COALESCE(`官方直降`,0) AS DECIMAL(10,2)))",
        "CAST(`商品id` AS CHAR)",
    ),
    "抖音": (
        "`ds_dy`.`dy_product_price_mechanism`",
        "date",
        "(CAST(COALESCE(`origin_price`,0) AS DECIMAL(10,2)) - CAST(COALESCE(`money_off_campaign`,0) AS DECIMAL(10,2)))",
        "CAST(COALESCE(NULLIF(TRIM(`sku_id`), ''), `product_id`) AS CHAR)",
    ),
    "拼多多": (
        "`ds_pdd`.`pdd_product_price_mechanism_test`",
        "fetch_time",
        "CAST(COALESCE(`page_price`,0) AS DECIMAL(10,2))",
        "CAST(`goods_id` AS CHAR)",
    ),
    "唯品会": (
        "`ds_wph`.`wph_product_price_mechanism`",
        "获取日期",
        "CAST(COALESCE(`页面价`,0) AS DECIMAL(10,2))",
        "CAST(`mid` AS CHAR)",
    ),
}


def build_down_sql(platform: str, limit: int) -> str:
    """构造单商品历史价格查询 SQL（参数化占位符由调用方绑定 %s = sku）。纯函数可测。"""
    if platform not in _DOWN_SQL:
        raise ValueError(f"未知平台：{platform}")
    table, time_col, price_expr, pid_expr = _DOWN_SQL[platform]
    return (
        f"SELECT DATE(`{time_col}`) AS record_date, `{time_col}` AS full_time, "
        f"{price_expr} AS actual_puhui_price "
        f"FROM {table} WHERE {pid_expr}=%s ORDER BY `{time_col}` DESC LIMIT {int(limit)}"
    )


def fetch_sku_history(platform: str, sku: str, limit: int = DOWN_DEFAULT_LIMIT) -> list[dict]:
    """连接业务库查询单个商品近 N 条价格，返回按时间升序的时序。"""
    import pymysql

    sql = build_down_sql(platform, limit)
    conn = pymysql.connect(
        host=os.getenv("PRICE_DB_HOST", "172.16.200.39"),
        port=int(os.getenv("PRICE_DB_PORT", "3306")),
        user=os.getenv("PRICE_DB_USER", "root"),
        password=os.getenv("PRICE_DB_PASSWORD", ""),
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
        connect_timeout=10,
    )
    rows = []
    try:
        with conn.cursor() as cur:
            cur.execute(sql, (sku,))
            rows = list(cur.fetchall())
    finally:
        conn.close()
    rows.sort(key=lambda r: str(r.get("full_time") or ""))
    return rows


def analyze_down(platform: str, sku: str, limit: int, memory: dict | None = None) -> dict:
    """单商品下钻：拉时序并算统计；命中经验库时附带 memory_label/memory_note。"""
    rows = fetch_sku_history(platform, sku, limit)
    prices = [
        float(r["actual_puhui_price"])
        for r in rows
        if r.get("actual_puhui_price") is not None
    ]
    out = {
        "platform": platform,
        "sku": sku,
        "records": rows[-limit:],
        "count": len(rows),
    }
    if prices:
        current = prices[-1]
        out.update({
            "current_price": current,
            "min_price": min(prices),
            "max_price": max(prices),
            "avg_price": round(sum(prices) / len(prices), 2),
            "min_max_gap": round(max(prices) - min(prices), 2),
        })
    note = memory_note_for(platform, sku, memory)
    if note:
        out["memory_note"] = note
    return out


# ---------------------------------------------------------------------------
# 组装
# ---------------------------------------------------------------------------


def build_analysis(raw: dict, memory: dict | None = None) -> dict:
    """analyze 主流程（确定性）。raw = run.py 的 stdout JSON。

    memory：可注入经验库（测试用），缺省自读 price_memory.json。
    """
    memory = memory if memory is not None else load_memory()
    anomalies = list(raw.get("anomalies") or [])
    # 附上昵称（run.py 已在每条 anomaly 带 product_name）
    grouped_res = group_by_merchandise(anomalies)
    denoised = denoise(anomalies)
    public = _public_groups(grouped_res["groups"], denoised)
    for g in public:
        g["items"] = annotate_memory(g["items"], memory)
    summary = build_summary(denoised["high"], public)
    report = {
        "status": "success",
        "check_time": raw.get("check_time"),
        "stage": raw.get("current_promotion_stage"),
        "benchmark_price_column": raw.get("benchmark_price_column"),
        "summary": summary,
        "denoised_count": len(denoised["low"]),
        "memory_hits": sum(1 for g in public for it in g["items"] if it.get("memory_note")),
        "grouped": public,
        "ai_material": "",
        "boundary_notice": "【合规红线】本数据仅做异常预警，禁止自动改价，全部异动须人工二次复核确认。",
    }
    report["ai_material"] = build_ai_material(report)
    return report


def _item_sig(it: dict) -> str:
    """确定性标识一条异动：平台 + SKU + 异常类型。用于去噪结果与原清单对齐。"""
    return f"{it.get('platform')}|{it.get('product_id')}|{it.get('anomaly_type')}"


def _public_groups(groups: list[dict], denoised: dict) -> list[dict]:
    """把分组里的 items 收敛为公开字段，避免输出过大；confidence 用高/中口径。"""
    conf_by_sig: dict[str, str] = {
        _item_sig(it): str(it.get("confidence") or "中") for it in denoised["high"]
    }
    out = []
    for g in groups:
        items = []
        for it in g["items"]:
            sig = _item_sig(it)
            if sig not in conf_by_sig:
                continue  # 只保留高/中置信项
            items.append({
                "platform": it.get("platform"),
                "product_id": it.get("product_id"),
                "anomaly_type": it.get("anomaly_type"),
                "category": it.get("category"),
                "confidence": conf_by_sig[sig],
                "current_price": it.get("current_price"),
                "benchmark_price": it.get("benchmark_price"),
                "diff_amount": it.get("diff_amount"),
                "fluctuation_amount": it.get("fluctuation_amount"),
                "detail": it.get("detail"),
                "product_name": it.get("product_name", ""),
                "suggestion": recommendation_for(it),
            })
        if not items:
            continue
        out.append({
            "name": g["name"],
            "kinds": g["kind"],
            "cross_platform": g["cross_platform"],
            "platforms": g["platforms"],
            "severities": g["severities"],
            "impact": g["impact"],
            "item_count": len(items),
            "items": items,
        })
    return out


# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(description="价格巡检判断层")
    parser.add_argument("--mode", choices=["analyze", "down", "learn", "memory"],
                        default="analyze")
    parser.add_argument("--platform", default="")
    parser.add_argument("--sku", default="")
    parser.add_argument("--limit", type=int, default=DOWN_DEFAULT_LIMIT)
    parser.add_argument("--label", default="")
    parser.add_argument("--note", default="")
    args = parser.parse_args()

    try:
        if args.mode == "learn":
            if not args.platform or not args.sku or not args.label:
                raise ValueError("--mode learn 需要 --platform --sku --label。")
            report = {"status": "success", **learn(args.platform, args.sku, args.label, args.note)}
        elif args.mode == "memory":
            report = {"status": "success", "memory": load_memory()}
        elif args.mode == "down":
            if not args.platform or not args.sku:
                raise ValueError("--mode down 需要 --platform 与 --sku。")
            report = analyze_down(args.platform, args.sku, args.limit)
        else:
            report = build_analysis(run_raw_audit())
    except Exception as exc:  # noqa: BLE001
        report = {"status": "error", "message": f"判断层执行失败: {exc}"}
    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
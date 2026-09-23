"""固定流程协调执行器 (Fixed ETL & Feishu Notify Executor)

核心职责：
  1. 取数：通过 StaffDeck 内部数据查询中心模板获取当日数据；
  2. 处理：在内存中完成指标计算或异常比对（0 Token 消耗）；
  3. 去重：基于本地 SQLite 轻量指纹库过滤已推送记录，防止同一天重复报警；
  4. 推送：构造飞书交互式卡片 2.0 并发送至飞书机器人 Webhook；
  5. 状态反馈：输出标准 JSON 结果并返回退出码，由调度器捕获真实业务成败。
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Windows 控制台编码保护
if sys.platform.startswith("win"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except (AttributeError, OSError):
        pass

# 路径与配置解析
CURRENT_DIR = Path(__file__).resolve().parent
CONFIG_PATH = CURRENT_DIR / "config.json"
DEDUP_DB_PATH = CURRENT_DIR / "dedup_history.sqlite"

CONFIG: dict[str, Any] = {}
if CONFIG_PATH.exists():
    try:
        CONFIG = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        pass

STAFFDECK_BASE_URL = os.getenv("STAFFDECK_BASE_URL", "http://127.0.0.1:5173").rstrip("/")
DEFAULT_TEMPLATE_ID = os.getenv(
    "DATA_QUERY_TEMPLATE_ID",
    str(CONFIG.get("default_template_id") or "qt_50f463a815af4801"),
)
FEISHU_WEBHOOK_URL = (
    os.getenv("FEISHU_ALERT_WEBHOOK")
    or os.getenv("FEISHU_SALES_REPORT_WEBHOOK")
    or str(CONFIG.get("webhook_url") or "")
)


def init_dedup_db() -> None:
    """初始化轻量指纹去重表。"""
    with sqlite3.connect(DEDUP_DB_PATH) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS pushed_records (
                fingerprint TEXT PRIMARY KEY,
                category TEXT,
                identifier TEXT,
                value_summary TEXT,
                pushed_at DATETIME
            )
            """
        )
        conn.commit()


def is_already_pushed(fingerprint: str) -> bool:
    """检查指纹是否已记录（防重）。"""
    with sqlite3.connect(DEDUP_DB_PATH) as conn:
        cur = conn.cursor()
        cur.execute("SELECT 1 FROM pushed_records WHERE fingerprint = ?", (fingerprint,))
        return cur.fetchone() is not None


def record_pushed(fingerprint: str, category: str, identifier: str, value_summary: str) -> None:
    """记录成功推送的指纹。"""
    with sqlite3.connect(DEDUP_DB_PATH) as conn:
        conn.execute(
            "INSERT OR REPLACE INTO pushed_records VALUES (?, ?, ?, ?, ?)",
            (fingerprint, category, identifier, value_summary, datetime.now(timezone.utc).isoformat()),
        )
        conn.commit()


def get_internal_service_token() -> str:
    """计算或读取 StaffDeck 内部服务免密认证 Token。"""
    token = os.getenv("STAFFDECK_INTERNAL_TOKEN")
    if token:
        return token
    secret = os.getenv("APP_SECRET", "change-me-in-development").encode("utf-8")
    return hmac.new(secret, b"ultrarag-internal-mock-api-v1", hashlib.sha256).hexdigest()


def fetch_from_query_template(template_id: str, query_date: str | None = None, dry_run: bool = False) -> list[dict[str, Any]]:
    """步骤 1：通过 StaffDeck 内部查询端点取数。"""
    import requests

    target_date = query_date or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    url = f"{STAFFDECK_BASE_URL}/api/mock/data-query/{template_id}"
    headers = {
        "X-UltraRAG-Internal-Token": get_internal_service_token(),
    }
    payload = {
        "params": {
            "end_date": target_date,
            "query_date": target_date,
        }
    }
    try:
        resp = requests.post(url, headers=headers, json=payload, timeout=15)
        if resp.status_code == 200:
            data = resp.json()
            rows = data.get("rows")
            if isinstance(rows, list):
                return rows
        elif not dry_run:
            raise RuntimeError(f"数据查询接口返回 HTTP {resp.status_code}: {resp.text[:300]}")
    except Exception as exc:
        if not dry_run:
            raise RuntimeError(f"连接数据查询中心失败：{exc}") from exc

    # Dry-run 演练时的兜底演示数据
    if dry_run:
        return [
            {
                "channel": "京东",
                "sku_id": "SKU-001",
                "product_name": "示例商品 A",
                "current_price": 89.0,
                "baseline_price": 100.0,
            },
            {
                "channel": "抖音",
                "sku_id": "SKU-002",
                "product_name": "示例商品 B",
                "current_price": 150.0,
                "baseline_price": 140.0,
            },
        ]
    return []


def process_records(
    rows: list[dict[str, Any]],
    mode: str = "audit",
) -> tuple[list[dict[str, Any]], int]:
    """步骤 2 & 3：内存清洗、异常判定或播报摘要，并执行指纹去重。"""
    init_dedup_db()
    qualified: list[dict[str, Any]] = []
    skipped_count = 0
    today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    for item in rows:
        # 支持两种业务形态：价格破价异常比对 或 通用业务阈值监控
        curr_price = float(item.get("current_price") or item.get("price") or 0)
        base_price = float(item.get("baseline_price") or item.get("base_price") or 0)
        identifier = str(item.get("sku_id") or item.get("item") or item.get("id") or "")
        channel = str(item.get("channel") or item.get("category") or "通用")

        is_anomaly = False
        if base_price > 0 and curr_price < base_price:
            is_anomaly = True
            diff_amount = round(base_price - curr_price, 2)
            diff_rate = round((diff_amount / base_price) * 100, 1)
            item["diff_amount"] = diff_amount
            item["diff_rate"] = diff_rate
        elif mode == "digest":
            # 播报模式：全量汇总
            is_anomaly = True

        if is_anomaly:
            # 构造防重指纹：日期 + 渠道/分类 + 唯一识别码 + 当前数值
            raw_fingerprint = f"{today_str}_{channel}_{identifier}_{curr_price}"
            fp = hashlib.md5(raw_fingerprint.encode("utf-8")).hexdigest()
            item["_fingerprint"] = fp
            item["_channel"] = channel
            item["_identifier"] = identifier

            if is_already_pushed(fp):
                skipped_count += 1
            else:
                qualified.append(item)

    return qualified, skipped_count


def push_feishu_card(
    items: list[dict[str, Any]],
    total_checked: int,
    mode: str = "audit",
    dry_run: bool = False,
) -> dict[str, Any]:
    """步骤 4：装配飞书交互式卡片 2.0 并发送至 Webhook。"""
    import requests

    if not items:
        return {"push_status": "skipped", "message": "无新纪录或已全部去重，无需推送"}

    if dry_run:
        return {"push_status": "success", "message": f"[DryRun] 演练成功，匹配 {len(items)} 条数据"}

    if not FEISHU_WEBHOOK_URL:
        return {"push_status": "failed", "message": "未配置飞书 Webhook 地址 (FEISHU_ALERT_WEBHOOK)"}

    # 格式化卡片明细
    max_items = int(CONFIG.get("max_display_items") or 15)
    lines: list[str] = []
    for item in items[:max_items]:
        name = str(item.get("product_name") or item.get("name") or item.get("_identifier") or "明细项")
        channel = item.get("_channel", "全渠道")
        curr = item.get("current_price") or item.get("price") or item.get("净销_万")
        base = item.get("baseline_price") or item.get("base_price")

        if base:
            diff_rate = item.get("diff_rate", 0)
            lines.append(
                f"• **[{channel}]** {name} | 现价: <font color='red'>¥{curr}</font> "
                f"(基准价: ¥{base}，低 {diff_rate}%)"
            )
        else:
            lines.append(f"• **[{channel}]** {name}: **{curr}**")

    content_text = "\n".join(lines)
    card_title = "🚨 业务合规异动巡检预警" if mode == "audit" else "📊 实时业务播报战报"
    header_color = "red" if mode == "audit" else "blue"

    payload = {
        "msg_type": "interactive",
        "card": {
            "header": {
                "title": {"tag": "plain_text", "content": card_title},
                "template": header_color,
            },
            "elements": [
                {
                    "tag": "div",
                    "fields": [
                        {
                            "is_short": True,
                            "text": {
                                "tag": "lark_md",
                                "content": f"**执行时间**\n{datetime.now(timezone.utc).strftime('%H:%M:%S')} (UTC)",
                            },
                        },
                        {
                            "is_short": True,
                            "text": {
                                "tag": "lark_md",
                                "content": f"**巡检记录数**\n{total_checked} 条",
                            },
                        },
                        {
                            "is_short": True,
                            "text": {
                                "tag": "lark_md",
                                "content": f"**本次推送**\n<font color='{header_color}'>**{len(items)}** 条</font>",
                            },
                        },
                    ],
                },
                {"tag": "hr"},
                {
                    "tag": "div",
                    "text": {"tag": "lark_md", "content": f"**明细清单：**\n{content_text}"},
                },
                {
                    "tag": "note",
                    "elements": [
                        {
                            "tag": "plain_text",
                            "content": "已开启状态指纹去重，已通知的相同记录今日不会重复推送。",
                        }
                    ],
                },
            ],
        },
    }

    try:
        resp = requests.post(FEISHU_WEBHOOK_URL, json=payload, timeout=15)
        if resp.status_code == 200:
            # 推送成功后落库指纹
            for item in items:
                record_pushed(
                    item.get("_fingerprint", ""),
                    item.get("_channel", ""),
                    item.get("_identifier", ""),
                    str(item.get("current_price") or item.get("price") or ""),
                )
            return {"push_status": "success", "pushed_count": len(items)}
        return {"push_status": "failed", "message": f"飞书返回 HTTP {resp.status_code}: {resp.text[:200]}"}
    except Exception as exc:  # noqa: BLE001
        return {"push_status": "failed", "message": f"Webhook 请求异常: {exc}"}


def main() -> None:
    parser = argparse.ArgumentParser(description="Fixed ETL & Notify Runner")
    parser.add_argument("--mode", default="audit", choices=["audit", "digest"])
    parser.add_argument("--template-id", default=DEFAULT_TEMPLATE_ID)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    try:
        # 1. 取数
        rows = fetch_from_query_template(args.template_id, dry_run=args.dry_run)
        # 2. 处理 & 去重
        items, skipped_count = process_records(rows, mode=args.mode)
        # 3. 推送
        push_res = push_feishu_card(
            items,
            total_checked=len(rows),
            mode=args.mode,
            dry_run=args.dry_run,
        )

        push_status = push_res.get("push_status", "failed")
        is_success = push_status in ("success", "skipped")
        result = {
            "status": "success" if is_success else "failed",
            "push_status": push_status,
            "total_checked": len(rows),
            "new_items": len(items),
            "skipped_duplicates": skipped_count,
            "message": push_res.get("message", ""),
        }
        print(json.dumps(result, ensure_ascii=False))
        if not is_success:
            sys.exit(1)

    except Exception as exc:  # noqa: BLE001
        err_res = {
            "status": "failed",
            "push_status": "failed",
            "error": str(exc),
        }
        print(json.dumps(err_res, ensure_ascii=False))
        sys.exit(1)


if __name__ == "__main__":
    main()

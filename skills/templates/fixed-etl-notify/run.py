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

try:
    from dotenv import load_dotenv

    for candidate in [
        Path(r"d:\project\02-开源项目\StaffDeck\backend\.env"),
        Path.cwd() / "backend" / ".env",
        Path.cwd() / ".env",
        CURRENT_DIR.parents[2] / "backend" / ".env" if len(CURRENT_DIR.parents) >= 3 else None,
    ]:
        if candidate and candidate.exists():
            load_dotenv(candidate)
            break
except Exception:  # noqa: BLE001, S110
    pass

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


def resolve_notification_target(
    agent_id: str | None = None,
    task_id: str | None = None,
    task_title: str | None = None,
    explicit_webhook: str | None = None,
    explicit_users: str | list[dict[str, str]] | None = None,
    mention_all: bool = False,
) -> tuple[str, list[dict[str, str]], bool]:
    """按优先级多级解析目标群 Webhook 与通知人列表：

    【优先级层次】
    1. 任务运行时 CLI 参数显式传入 (--webhook-url / --notify-users) [最高优先级]
    2. 任务专属路由 (config.json 中的 task_routes[task_id 或 task_title 关键字])
    3. 数字员工专属路由 (config.json 中的 agent_routes[agent_id])
    4. 环境变量 STAFFDECK_TASK_{TASK_ID}_WEBHOOK 或 STAFFDECK_AGENT_{AGENT_ID}_WEBHOOK
    5. 技能根级 default_webhook_url
    6. 全局环境变量 FEISHU_SALES_REPORT_WEBHOOK 或 FEISHU_ALERT_WEBHOOK
    """
    target_agent_id = (agent_id or os.getenv("STAFFDECK_AGENT_ID") or "").strip()
    target_task_id = (task_id or os.getenv("STAFFDECK_TASK_ID") or "").strip()
    target_task_title = (task_title or os.getenv("STAFFDECK_TASK_TITLE") or "").strip()

    task_routes = CONFIG.get("task_routes", {}) if isinstance(CONFIG.get("task_routes"), dict) else {}
    agent_routes = CONFIG.get("agent_routes", {}) if isinstance(CONFIG.get("agent_routes"), dict) else {}

    # 1. 优先查找任务专属路由配置 (task_routes)
    task_cfg: dict[str, Any] = {}
    if target_task_id and target_task_id in task_routes:
        task_cfg = task_routes[target_task_id]
    elif target_task_title:
        if target_task_title in task_routes:
            task_cfg = task_routes[target_task_title]
        else:
            for k, v in task_routes.items():
                if isinstance(v, dict) and (k in target_task_title or target_task_title in k):
                    task_cfg = v
                    break

    # 2. 次级查找员工专属路由配置 (agent_routes)
    agent_cfg: dict[str, Any] = {}
    if target_agent_id:
        agent_cfg = agent_routes.get(target_agent_id) or {}
        if not agent_cfg:
            for _, r_val in agent_routes.items():
                if isinstance(r_val, dict) and str(r_val.get("agent_name") or "") == target_agent_id:
                    agent_cfg = r_val
                    break

    # 3. 解析 Webhook URL：CLI显式 > task_cfg > agent_cfg > 环境变量 > 全局兜底
    webhook_url = ""
    if explicit_webhook and explicit_webhook.strip():
        webhook_url = explicit_webhook.strip()
    elif task_cfg.get("webhook_url") and str(task_cfg["webhook_url"]).strip():
        webhook_url = str(task_cfg["webhook_url"]).strip()
    elif agent_cfg.get("webhook_url") and str(agent_cfg["webhook_url"]).strip():
        webhook_url = str(agent_cfg["webhook_url"]).strip()
    elif target_task_id and os.getenv(f"STAFFDECK_TASK_{target_task_id.upper()}_WEBHOOK"):
        webhook_url = os.getenv(f"STAFFDECK_TASK_{target_task_id.upper()}_WEBHOOK", "")
    elif target_agent_id and os.getenv(f"STAFFDECK_AGENT_{target_agent_id.upper()}_WEBHOOK"):
        webhook_url = os.getenv(f"STAFFDECK_AGENT_{target_agent_id.upper()}_WEBHOOK", "")
    elif not target_task_id:
        root_url = str(CONFIG.get("webhook_url") or CONFIG.get("default_webhook_url") or "").strip()
        if root_url.startswith(("http://", "https://")):
            webhook_url = root_url
        else:
            env_map = CONFIG.get("env_mapping", {}) if isinstance(CONFIG.get("env_mapping"), dict) else {}
            env_name = str(env_map.get("webhook_url") or "FEISHU_ALERT_WEBHOOK")
            fallback_name = str(env_map.get("fallback_webhook_url") or "FEISHU_SALES_REPORT_WEBHOOK")
            webhook_url = (
                os.getenv("FEISHU_SALES_REPORT_WEBHOOK")
                or os.getenv("FEISHU_ALERT_WEBHOOK")
                or os.getenv(env_name)
                or os.getenv(fallback_name)
                or ""
            )

    # 4. 解析通知人员：CLI显式 > task_cfg > agent_cfg
    users: list[dict[str, str]] = []
    source_users = explicit_users or task_cfg.get("notify_users") or agent_cfg.get("notify_users")
    if isinstance(source_users, list):
        for item in source_users:
            if isinstance(item, dict):
                users.append({
                    "id": str(item.get("id") or item.get("open_id") or ""),
                    "name": str(item.get("name") or ""),
                })
            elif isinstance(item, str) and item.strip():
                users.append({"id": item.strip(), "name": ""})
    elif isinstance(source_users, str) and source_users.strip():
        for part in source_users.split(","):
            part = part.strip()
            if not part:
                continue
            if ":" in part:
                uid, uname = part.split(":", 1)
                users.append({"id": uid.strip(), "name": uname.strip()})
            else:
                users.append({"id": part, "name": ""})

    # 5. 解析是否 @所有人：CLI显式 > task_cfg > agent_cfg
    should_mention_all = bool(
        mention_all
        or task_cfg.get("mention_all", False)
        or agent_cfg.get("mention_all", False)
    )

    return webhook_url, users, should_mention_all


def resolve_webhook_url(explicit_url: str | None = None) -> str:
    url, _, _ = resolve_notification_target(explicit_webhook=explicit_url)
    return url


FEISHU_WEBHOOK_URL = resolve_webhook_url()


def init_dedup_db() -> None:
    """初始化轻量指纹去重表与每日首次推送状态表。"""
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
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS pushed_daily_sessions (
                push_date TEXT PRIMARY KEY,
                task_id TEXT,
                pushed_at DATETIME
            )
            """
        )
        conn.commit()


def check_is_first_push_today(task_id: str = "") -> bool:
    """检查今天本地日期是否尚未有推送记录。"""
    init_dedup_db()
    today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    with sqlite3.connect(DEDUP_DB_PATH) as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT 1 FROM pushed_daily_sessions WHERE push_date = ? AND (task_id = ? OR task_id = '' OR ? = '')",
            (today_str, task_id, task_id),
        )
        return cur.fetchone() is None


def record_daily_pushed(task_id: str = "") -> None:
    """记录本日已完成首次推送。"""
    init_dedup_db()
    today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    with sqlite3.connect(DEDUP_DB_PATH) as conn:
        conn.execute(
            "INSERT OR REPLACE INTO pushed_daily_sessions VALUES (?, ?, ?)",
            (today_str, task_id, datetime.now(timezone.utc).isoformat()),
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
    is_first_push: bool = False,
    agent_id: str | None = None,
    task_id: str | None = None,
    task_title: str | None = None,
    webhook_url: str | None = None,
    webhooks: list[str] | str | None = None,
    notify_users: list[dict[str, str]] | str | None = None,
    mention_all: bool = False,
    notify_mode: str = "auto",
    chat_id: str | None = None,
    chat_ids: list[str] | str | None = None,
    mobiles: list[str] | str | None = None,
    emails: list[str] | str | None = None,
    open_ids: list[str] | str | None = None,
    binding_id: str | None = None,
) -> dict[str, Any]:
    """步骤 4：装配飞书交互式卡片 2.0 并聚合发送至企业应用或 Webhook 目标。"""
    import requests

    if not items:
        return {"push_status": "skipped", "message": "无新纪录或已全部去重，无需推送"}

    target_webhook, target_users, should_at_all = resolve_notification_target(
        agent_id=agent_id,
        task_id=task_id,
        task_title=task_title,
        explicit_webhook=webhook_url,
        explicit_users=notify_users,
        mention_all=mention_all,
    )

    # 规范化群聊列表
    chat_id_list: list[str] = []
    if chat_id:
        chat_id_list.extend([c.strip() for c in chat_id.split(",") if c.strip()])
    if chat_ids:
        if isinstance(chat_ids, str):
            chat_id_list.extend([c.strip() for c in chat_ids.split(",") if c.strip()])
        else:
            chat_id_list.extend([str(c).strip() for c in chat_ids if str(c).strip()])
    chat_id_list = list(dict.fromkeys(chat_id_list))

    # 规范化 Webhook 列表
    webhook_list: list[str] = []
    if webhook_url:
        webhook_list.extend([w.strip() for w in webhook_url.split(",") if w.strip()])
    if webhooks:
        if isinstance(webhooks, str):
            webhook_list.extend([w.strip() for w in webhooks.split(",") if w.strip()])
        else:
            webhook_list.extend([str(w).strip() for w in webhooks if str(w).strip()])
    if target_webhook and target_webhook not in webhook_list:
        webhook_list.append(target_webhook)
    webhook_list = list(dict.fromkeys(webhook_list))

    if dry_run:
        return {
            "push_status": "success",
            "message": f"[DryRun] 演练成功，匹配 {len(items)} 条数据，群数: {len(chat_id_list)}，Webhook数: {len(webhook_list)}，通知人员数: {len(target_users)}",
        }

    is_app_mode = (
        notify_mode == "app"
        or (notify_mode == "auto" and (chat_id_list or emails or open_ids or mobiles))
    )
    if not is_app_mode and not webhook_list:
        return {
            "push_status": "failed",
            "message": (
                "未配置飞书通知目标（既未配置企业应用接收目标，也未配置 Webhook 地址）。\n"
                "可在定时任务编辑界面配置【📢 飞书消息推送与通知配置】，或在 config.json 中配置 task_routes。"
            ),
        }

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

    # 分离正负增量Top5店铺与普通明细
    pos_shops = [x for x in items if str(x.get("category") or x.get("_channel") or "") in ("店铺正增量Top5", "正增量Top5")]
    neg_shops = [x for x in items if str(x.get("category") or x.get("_channel") or "") in ("店铺负增量Top5", "负增量Top5")]
    regular_items = [x for x in items if x not in pos_shops and x not in neg_shops]

    shop_card_elements = []
    if not is_first_push and (pos_shops or neg_shops):
        pos_lines = []
        for i, s in enumerate(pos_shops[:5], 1):
            name = s.get("item") or s.get("平台店铺") or s.get("name") or s.get("_identifier") or "未知店铺"
            net = float(s.get("净销_万") or s.get("price") or 0)
            diff = float(s.get("环比增量_万") or s.get("diff_amount") or 0)
            pos_lines.append(f"{i}. **{name}**\n   增量: <font color='green'>**+{diff:.2f}万**</font> | 净销: {net:.2f}万")

        neg_lines = []
        for i, s in enumerate(neg_shops[:5], 1):
            name = s.get("item") or s.get("平台店铺") or s.get("name") or s.get("_identifier") or "未知店铺"
            net = float(s.get("净销_万") or s.get("price") or 0)
            diff = float(s.get("环比增量_万") or s.get("diff_amount") or 0)
            diff_str = f"{diff:.2f}万" if diff < 0 else f"-{abs(diff):.2f}万"
            neg_lines.append(f"{i}. **{name}**\n   增量: <font color='red'>**{diff_str}**</font> | 净销: {net:.2f}万")

        if not neg_lines:
            neg_lines = ["*(其余活跃店铺增量均为正或持平)*"]

        shop_card_elements.extend([
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
            {"tag": "hr"},
        ])
    elif is_first_push and (pos_shops or neg_shops or any(str(x.get("category") or "") == "大盘" for x in items)):
        shop_card_elements.extend([
            {
                "tag": "markdown",
                "content": "**🏪 实时销售数据汇报：店铺正负增量动态排行**\n<font color='grey'>*(当日首次播报，增量统一计为 0.00万，不展示上一时刻店铺增量排行)*</font>",
                "text_align": "left",
                "text_size": "normal",
            },
            {"tag": "hr"},
        ])

    card_elements = [
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
    ]

    # 播报模式下的整体汇总文案（含运营端与达播端各自上一时刻增量）
    summary_item = next(
        (x for x in items if str(x.get("category") or "") == "大盘" and str(x.get("item") or "") == "电商整体"),
        None,
    )
    if summary_item:
        tot_net = float(summary_item.get("净销_万") or 0)
        tot_ops = float(summary_item.get("运营净销_万") or 0)
        tot_live = float(summary_item.get("达播净销_万") or 0)
        now_time = str(summary_item.get("数据更新时间") or datetime.now(timezone.utc).strftime("%m-%d %H:%M"))

        if is_first_push:
            tot_diff = 0.0
            tot_ops_diff = 0.0
            tot_live_diff = 0.0
            summary_content = (
                f"📢 **整体销售播报**：截止 {now_time}，电商整体净销 **{tot_net:.2f}万**"
                f"（当日首次播报，增量计为 <font color='grey'>0.00万</font>，不对比前一日数据），"
                f"其中运营端 **{tot_ops:.2f}万**（增量 <font color='grey'>0.00万</font>）；"
                f"达播端 **{tot_live:.2f}万**（增量 <font color='grey'>0.00万</font>），各渠道运行平稳。"
            )
        else:
            tot_diff = float(summary_item.get("环比增量_万") or 0)
            tot_ops_diff = float(summary_item.get("运营增量_万") or 0)
            tot_live_diff = float(summary_item.get("达播增量_万") or 0)

            diff_str = f"+{tot_diff:.2f}万" if tot_diff > 0 else f"{tot_diff:.2f}万"
            ops_diff_str = f"+{tot_ops_diff:.2f}万" if tot_ops_diff > 0 else f"{tot_ops_diff:.2f}万"
            live_diff_str = f"+{tot_live_diff:.2f}万" if tot_live_diff > 0 else f"{tot_live_diff:.2f}万"
            summary_content = (
                f"📢 **整体销售播报**：截止 {now_time}，电商整体净销 **{tot_net:.2f}万**"
                f"（较上一时刻增量 <font color='{'green' if tot_diff >= 0 else 'red'}'>**{diff_str}**</font>，"
                f"其中运营端 **{tot_ops:.2f}万**，较上一时刻增量 <font color='{'green' if tot_ops_diff >= 0 else 'red'}'>**{ops_diff_str}**</font>；"
                f"达播端 **{tot_live:.2f}万**，较上一时刻增量 <font color='{'green' if tot_live_diff >= 0 else 'red'}'>**{live_diff_str}**</font>），各渠道运行平稳。"
            )

        card_elements.insert(
            0,
            {
                "tag": "div",
                "text": {
                    "tag": "lark_md",
                    "content": summary_content,
                },
            },
        )

    # 插入中间板块
    if shop_card_elements:
        card_elements.extend(shop_card_elements)

    if regular_items:
        card_elements.append({
            "tag": "div",
            "text": {"tag": "lark_md", "content": f"**明细清单：**\n{content_text}"},
        })

    # 插入通知人员 / @关注人 (Mention)
    at_tags: list[str] = []
    if should_at_all:
        at_tags.append('<at id="all">所有人</at>')
    for u in target_users:
        uid = str(u.get("id") or u.get("open_id") or "").strip()
        uname = str(u.get("name") or "").strip()
        if not uid:
            continue
        if "@" in uid and not uid.startswith("ou_"):
            at_tags.append(f'<at email="{uid}">{uname or uid}</at>')
        else:
            at_tags.append(f'<at id="{uid}">{uname or uid}</at>')

    if at_tags:
        card_elements.append({
            "tag": "div",
            "text": {
                "tag": "lark_md",
                "content": f"🔔 **通知负责人**：{' '.join(at_tags)}",
            },
        })

    card_elements.append({
        "tag": "note",
        "elements": [
            {
                "tag": "plain_text",
                "content": "已开启状态指纹去重，已通知的相同记录今日不会重复推送。",
            }
        ],
    })

    payload = {
        "msg_type": "interactive",
        "card": {
            "header": {
                "title": {"tag": "plain_text", "content": card_title},
                "template": header_color,
            },
            "elements": card_elements,
        },
    }

    try:
        # 分支 A: 统一通过出站中心派发 (支持多群、多Webhook与多手机号)
        if is_app_mode or len(webhook_list) > 1:
            headers = {
                "Content-Type": "application/json",
                "X-UltraRAG-Internal-Token": get_internal_service_token(),
            }
            mobile_list = [m.strip() for m in mobiles.split(",") if m.strip()] if isinstance(mobiles, str) else list(mobiles or [])
            email_list = [e.strip() for e in emails.split(",") if e.strip()] if isinstance(emails, str) else list(emails or [])
            oid_list = [o.strip() for o in open_ids.split(",") if o.strip()] if isinstance(open_ids, str) else list(open_ids or [])
            for u in target_users:
                uid = str(u.get("id") or u.get("open_id") or "").strip()
                if uid.isdigit() and len(uid) == 11 and uid not in mobile_list:
                    mobile_list.append(uid)
                elif "@" in uid and uid not in email_list:
                    email_list.append(uid)
                elif uid.startswith("ou_") and uid not in oid_list:
                    oid_list.append(uid)

            app_payload = {
                "tenant_id": os.getenv("STAFFDECK_TENANT_ID", "tenant_demo"),
                "chat_ids": chat_id_list,
                "webhooks": webhook_list,
                "mobiles": mobile_list,
                "emails": email_list,
                "open_ids": oid_list,
                "card": payload["card"],
            }
            # 定时任务上下文：服务端据此读取该任务所选飞书应用，无需依赖 argv 传递应用标识。
            if task_id:
                app_payload["scheduled_task_id"] = task_id
            if binding_id:
                app_payload["binding_id"] = binding_id
            notify_url = f"{STAFFDECK_BASE_URL}/api/mock/feishu-app-notify"
            resp = requests.post(notify_url, json=app_payload, headers=headers, timeout=20)
            if resp.status_code == 200:
                resp_json = resp.json()
                if resp_json.get("ok"):
                    for item in items:
                        record_pushed(
                            item.get("_fingerprint", ""),
                            item.get("_channel", ""),
                            item.get("_identifier", ""),
                            str(item.get("current_price") or item.get("price") or ""),
                        )
                    record_daily_pushed(task_id or "")
                    sent_cnt = resp_json.get("sent_count", 0)
                    failed_cnt = resp_json.get("failed_count", 0)
                    msg = f"成功发送至 {sent_cnt} 个目标"
                    if failed_cnt > 0:
                        msg += f"（{failed_cnt} 个目标发送失败，见内部日志）"
                    return {"push_status": "success", "pushed_count": len(items), "message": msg}
                return {"push_status": "failed", "message": f"飞书通知出站失败: {resp_json.get('error', resp.text[:200])}"}
            return {"push_status": "failed", "message": f"飞书通知出站接口返回 HTTP {resp.status_code}: {resp.text[:200]}"}

        # 分支 B: 单一群机器人 Webhook 回退模式
        target_wh = webhook_list[0] if webhook_list else target_webhook
        resp = requests.post(target_wh, json=payload, timeout=15)
        if resp.status_code == 200:
            for item in items:
                record_pushed(
                    item.get("_fingerprint", ""),
                    item.get("_channel", ""),
                    item.get("_identifier", ""),
                    str(item.get("current_price") or item.get("price") or ""),
                )
            record_daily_pushed(task_id or "")
            return {"push_status": "success", "pushed_count": len(items)}
        return {"push_status": "failed", "message": f"飞书返回 HTTP {resp.status_code}: {resp.text[:200]}"}
    except Exception as exc:  # noqa: BLE001
        return {"push_status": "failed", "message": f"通知推送请求异常: {exc}"}


def main() -> None:
    parser = argparse.ArgumentParser(description="Fixed ETL & Notify Runner")
    parser.add_argument("--mode", default="audit", choices=["audit", "digest"])
    parser.add_argument("--template-id", default=DEFAULT_TEMPLATE_ID)
    parser.add_argument("--agent-id", default=os.getenv("STAFFDECK_AGENT_ID", ""), help="数字员工 ID")
    parser.add_argument("--task-id", default=os.getenv("STAFFDECK_TASK_ID", ""), help="定时任务 ID（精准匹配专属群与关注人）")
    parser.add_argument("--task-title", default=os.getenv("STAFFDECK_TASK_TITLE", ""), help="定时任务标题（支持按业务名称模糊匹配专属群）")
    parser.add_argument("--notify-mode", default=os.getenv("FEISHU_NOTIFY_MODE", "auto"), choices=["auto", "app", "webhook"], help="通知通道模式：app(企业应用) | webhook(群机器人)")
    parser.add_argument("--chat-id", default=os.getenv("FEISHU_NOTIFY_CHAT_ID", ""), help="目标群会话 ID (chat_id)")
    parser.add_argument("--chat-ids", default=os.getenv("FEISHU_NOTIFY_CHAT_IDS", ""), help="目标群会话 ID 列表（逗号隔开）")
    parser.add_argument("--webhook-url", default=None, help="显式指定/覆盖飞书机器人 Webhook 地址")
    parser.add_argument("--webhooks", default=os.getenv("FEISHU_NOTIFY_WEBHOOKS", ""), help="飞书机器人 Webhook 地址列表（逗号隔开）")
    parser.add_argument("--mobiles", default=os.getenv("FEISHU_NOTIFY_MOBILES", ""), help="企业自建应用推送的责任人手机号列表（英文逗号隔开，自动反查 OpenID 发送私聊）")
    parser.add_argument("--emails", default=os.getenv("FEISHU_NOTIFY_EMAILS", ""), help="企业自建应用推送的责任人邮箱列表（英文逗号隔开，自动反查 OpenID 发送私聊）")
    parser.add_argument("--open-ids", default=os.getenv("FEISHU_NOTIFY_OPEN_IDS", ""), help="企业自建应用推送的责任人 OpenID 列表（英文逗号隔开）")
    parser.add_argument("--binding-id", default=os.getenv("FEISHU_NOTIFY_BINDING_ID", ""), help="指定推送使用的飞书应用（定时任务通常无需传入，由服务端按 --task-id 解析）")
    parser.add_argument("--notify-users", default="", help="需要@通知的飞书人员列表（英文逗号隔开，格式如 'ou_xxx:张三,ou_yyy:李四' 或 'ou_xxx'）")
    parser.add_argument("--mention-all", action="store_true", help="是否在飞书群中 @所有人")
    parser.add_argument("--first-push", action="store_true", help="强制当日首次推送模式（增量置为0，不对比前一日）")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    try:
        # 1. 取数
        rows = fetch_from_query_template(args.template_id, dry_run=args.dry_run)
        is_first = bool(args.first_push or check_is_first_push_today(args.task_id))
        # 2. 处理 & 去重
        items, skipped_count = process_records(rows, mode=args.mode)
        # 3. 推送
        push_res = push_feishu_card(
            items,
            total_checked=len(rows),
            mode=args.mode,
            dry_run=args.dry_run,
            is_first_push=is_first,
            agent_id=args.agent_id,
            task_id=args.task_id,
            task_title=args.task_title,
            webhook_url=args.webhook_url,
            webhooks=args.webhooks,
            notify_users=args.notify_users,
            mention_all=args.mention_all,
            notify_mode=args.notify_mode,
            chat_id=args.chat_id,
            chat_ids=args.chat_ids,
            mobiles=args.mobiles,
            emails=args.emails,
            open_ids=args.open_ids,
            binding_id=args.binding_id,
        )

        push_status = push_res.get("push_status", "failed")
        is_success = push_status in ("success", "skipped")
        result = {
            "status": "success" if is_success else "failed",
            "push_status": push_status,
            "total_checked": len(rows),
            "new_items": len(items),
            "skipped_duplicates": skipped_count,
            "is_first_push": is_first,
            "agent_id": args.agent_id or "default",
            "task_id": args.task_id or "",
            "task_title": args.task_title or "",
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

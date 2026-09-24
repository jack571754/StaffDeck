"""Generic table card renderer for scheduled task outputs."""

from __future__ import annotations

from typing import Any


def build_generic_feishu_card(
    rows: list[dict[str, Any]],
    title: str = "",
    template: str = "blue",
    *,
    max_rows: int = 15,
    at_users: list[dict[str, Any]] | None = None,
    should_at_all: bool = False,
    **_kwargs: Any,
) -> dict[str, Any]:
    """Universal interactive card 2.0 rendering any query result rows as a clean table."""
    card_title = title or "📊 定时任务数据推送"
    elements: list[dict[str, Any]] = [
        {
            "tag": "markdown",
            "content": f"**执行完成**：共获取 {len(rows)} 条数据记录。",
            "text_align": "left",
            "text_size": "normal",
        }
    ]

    if rows:
        sample_keys = list(rows[0].keys())[:8]
        cols = [{"data_type": "lark_md", "name": k, "display_name": k, "width": "auto"} for k in sample_keys]
        table_rows = [{k: str(r.get(k, "")) for k in sample_keys} for r in rows[:max_rows]]
        elements.append({
            "tag": "table",
            "columns": cols,
            "rows": table_rows,
            "row_height": "low",
            "page_size": min(10, max_rows),
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
            "config": {"wide_screen_mode": True},
            "header": {
                "title": {"tag": "plain_text", "content": card_title},
                "template": template,
            },
            "body": {"elements": elements},
        },
    }

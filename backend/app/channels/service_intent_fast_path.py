"""Fast-path execution for high-confidence data query intents.

Intercepts inbound channel messages that match an approved QueryTemplate,
executes the query directly via data_query service, and delivers the formatted
result back to the channel, bypassing the multi-step Harness Agent loop.
"""

from __future__ import annotations

import logging
from typing import Any

from sqlmodel import Session

from app.channels.service_outbox import stage_channel_delivery
from app.data_query.intent_router import route_question
from app.data_query.models import QueryExecuteResult
from app.data_query.service import execute_query_by_id
from app.db.models import ChannelBinding, ChannelInboundEvent, ChatSession, Message, new_id, utc_now

logger = logging.getLogger(__name__)


def _format_markdown_table(result: QueryExecuteResult) -> str:
    """Format QueryExecuteResult columns and rows into a Markdown table."""
    if not result.columns or not result.rows:
        return "查询结果为空（0 行）。"

    headers = [str(c) for c in result.columns]
    header_row = "| " + " | ".join(headers) + " |"
    separator_row = "| " + " | ".join(["---"] * len(headers)) + " |"

    data_rows = []
    # Cap at 50 rows in channel display
    display_rows = result.rows[:50]
    for row in display_rows:
        cols = [str(row.get(col, "")) if row.get(col) is not None else "" for col in headers]
        data_rows.append("| " + " | ".join(cols) + " |")

    table = "\n".join([header_row, separator_row] + data_rows)
    if len(result.rows) > 50:
        table += f"\n\n*(共 {len(result.rows)} 行，此处仅展示前 50 行)*"
    return table


def try_handle_intent_fast_path(
    db: Session,
    binding: ChannelBinding,
    chat_session: ChatSession,
    event: ChannelInboundEvent,
    user_message: str,
    user_id: str,
    target: dict[str, Any],
) -> bool:
    """Attempt to route and directly execute a data query for the user message.

    Returns True if handled and delivered; False if unmatched or on any error.
    """
    if not user_message or not user_message.strip():
        return False

    try:
        route_result = route_question(
            question=user_message,
            tenant_id=binding.tenant_id,
            db=db,
        )
        if not route_result:
            return False

        logger.info(
            "Fast-path matched query template %s (id=%s) with confidence %.2f",
            route_result.template_name,
            route_result.template_id,
            route_result.confidence,
        )

        query_res = execute_query_by_id(
            db=db,
            template_id=route_result.template_id,
            tenant_id=binding.tenant_id,
            params=route_result.params,
        )

        # Format markdown response
        table_md = _format_markdown_table(query_res)
        param_summary = ", ".join(f"{k}={v}" for k, v in route_result.params.items()) if route_result.params else "默认参数"
        reply_text = (
            f"📊 **数据查询结果** · {route_result.template_name}\n"
            f"参数：`{param_summary}`\n\n"
            f"{table_md}\n\n"
            f"> 💡 数据来自模板「{route_result.template_name}」，耗时 {query_res.execution_time_ms:.1f}ms"
        )

        now = utc_now()
        # Save messages to session history
        user_msg = Message(
            id=new_id("msg"),
            tenant_id=binding.tenant_id,
            session_id=chat_session.id,
            user_id=user_id,
            role="user",
            content=user_message,
            client_turn_id=event.event_id,
            created_at=now,
            updated_at=now,
        )
        asst_msg = Message(
            id=new_id("msg"),
            tenant_id=binding.tenant_id,
            session_id=chat_session.id,
            role="assistant",
            content=reply_text,
            client_turn_id=event.event_id,
            created_at=now,
            updated_at=now,
        )
        db.add(user_msg)
        db.add(asst_msg)

        # Stage reply delivery
        stage_channel_delivery(db, chat_session, asst_msg)

        # Mark event completed
        event.status = "done"
        event.processed_at = now
        event.updated_at = now
        db.add(event)
        db.commit()
        return True

    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "Intent fast-path processing failed, smoothly falling back to normal agent turn: %s",
            exc,
        )
        db.rollback()
        return False

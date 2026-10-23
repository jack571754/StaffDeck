from __future__ import annotations

import hashlib
import json
import logging
import mimetypes
import re
import threading
import time
import traceback
from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import HTMLResponse, Response, StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import and_, or_, update
from sqlmodel import Session, select
from starlette.background import BackgroundTask

from app.agents.branching import model_for_agent
from app.channels.service_outbox import stage_channel_delivery
from app.config import get_settings
from app.core import AgentLoop
from app.core.artifact_owners import resolve_artifact_owner
from app.core.cancellation import cancel_chat_turn, is_chat_turn_cancelled
from app.core.handoff_reply_service import (  # noqa: F401 - legacy import compatibility
    resume_human_handoff_async as _resume_human_handoff_async,
)
from app.core.handoff_reply_service import (
    resume_human_handoff_worker as _resume_human_handoff_worker,  # noqa: F401
)
from app.core.harness_turn_store import HarnessTurnStore
from app.core.slash_commands import SlashCommandRead, slash_command_catalog
from app.db import engine, get_session
from app.db.models import (
    AgentEvent,
    AgentProfile,
    ArtifactShareLink,
    ChatSession,
    HarnessTaskFrameRecord,
    HarnessTurnRecord,
    HumanHandoffRequest,
    Message,
    MessageFeedback,
    ScheduledTask,
    ScheduledTaskRun,
    Skill,
    SkillFeedback,
    Team,
    User,
    new_id,
    utc_now,
)
from app.feedback import enqueue_feedback_analysis
from app.harness import (
    ARTIFACT_OWNER_DEFAULT_KIND,
    HarnessArtifactAccessError,
    open_harness_artifact,
)
from app.llm import LLMClient, LLMError
from app.observability.spans import (
    bind_span_sink,
    llm_operation,
    reset_span_sink,
    set_span_sink,
)
from app.scheduled_tasks.schema import ScheduledTaskDraftRead
from app.scheduled_tasks.service import (
    DEFAULT_TASK_TIME,
    _ensure_task_access,
    detect_scheduled_task_draft,
)
from app.security import artifact_share as artifact_share_mod
from app.security.auth import get_current_user, get_current_user_optional
from app.security.permissions import agent_owned_by_user, is_admin_user
from app.security.tenant import ensure_tenant
from app.session.attachments import (
    parse_chat_attachment,
    validate_chat_turn_attachments,
)
from app.session.cleanup import (
    purge_chat_session_records,
    remove_chat_session_workspace,
)
from app.session.helpers import public_session
from app.session.message_read import message_read
from app.session.message_visibility import (
    internal_message_turn_ids,
    visible_message_content,
    visible_message_rows,
)
from app.session.origin import pilotdeck_origin_session_ids
from app.session.session_schema import (
    ChatAttachmentRead,
    ChatSessionCreateRequest,
    ChatSessionRead,
    ChatSessionUpdateRequest,
    ChatTurnRequest,
    ChatTurnResponse,
    MessageFeedbackRequest,
    MessageRead,
)
from app.skills.nesting import discoverable_sops
from app.teams.service import get_team_leader
from app.teams.wakeup import build_tl_chat_context, process_tl_reply

router = APIRouter(prefix="/api/chat", tags=["chat"])
logger = logging.getLogger(__name__)
CANCELLED_ASSISTANT_REPLY = "已停止生成"
INTERRUPTED_ASSISTANT_REPLY = "本次响应中断，请重试发送。"
STREAM_REPLY_CHUNK_SIZE = 96
STREAM_RELAY_POLL_SECONDS = 0.08
STREAM_RELAY_HEARTBEAT_SECONDS = 5.0
STREAM_RELAY_IDLE_TIMEOUT_SECONDS = 660.0
STREAM_INTERRUPTED_TRACEBACK_CHAR_LIMIT = 6000
MAX_CHAT_ATTACHMENT_BYTES = 12 * 1024 * 1024
MAX_CHAT_ATTACHMENTS = 8
SESSION_TITLE_SUMMARY_EVENT = "session_title_summarized"
SCHEDULE_WEEKDAY_LABELS = ("周一", "周二", "周三", "周四", "周五", "周六", "周日")
EVENT_PAYLOAD_META_KEYS = {"id", "event", "type", "event_type", "created_at", "data"}
STREAM_RELAY_EVENT_ALIASES = {
    "router_decision_created": "router_decision",
    "stream_status": "status",
}
STREAM_RELAY_TERMINAL_EVENTS = {
    "complete",
    "error_occurred",
    "stream_cancelled",
    "stream_interrupted",
}
SPAN_EVENT_TYPES = {
    "llm_call_started",
    "llm_call_finished",
    "llm_call_failed",
    "knowledge_span_started",
    "knowledge_span_finished",
    "knowledge_span_failed",
}
KNOWLEDGE_TRACE_PHASES = {
    "knowledge",
    "okf_route",
    "okf_only",
    "document_route",
    "document_route_lexical",
    "bucket_route",
    "bucket_route_lexical",
    "section_expand",
    "read_chunks",
    "evidence_pack",
    "no_visible_knowledge",
    "no_documents",
    "no_buckets",
}
SESSION_TITLE_PROMPT = """你是任务派发台的会话标题编辑器。

根据首轮用户需求和员工回复，生成一个简短、可读、具体的中文标题。

要求：
- 输出 JSON object，格式为 {"title": "..."}。
- 直接输出标题 JSON，不输出分析、候选标题或解释。
- 标题 4 到 18 个中文字符优先，最多 24 个字符。
- 不要使用“新任务”“任务记录”“用户咨询”等空泛标题。
- 不要包含标点符号、引号、编号、员工名或用户称呼。
- 如果无法判断，就返回最能概括用户需求的短语。
"""
_session_title_summary_jobs: set[str] = set()
_session_title_summary_jobs_lock = threading.Lock()


class HumanHandoffRead(BaseModel):
    id: str
    tenant_id: str
    session_id: str
    agent_id: str | None = None
    requester_user_id: str | None = None
    assignee_user_id: str | None = None
    trigger_skill_id: str | None = None
    trigger_step_id: str | None = None
    context_summary: str | None = None
    pending_question: str | None = None
    status: str
    human_reply: str | None = None
    metadata: dict[str, object]
    created_at: str
    updated_at: str
    answered_at: str | None = None


class ChatTurnCancelRequest(BaseModel):
    tenant_id: str
    turn_id: str


class HumanHandoffReplyRequest(BaseModel):
    tenant_id: str
    reply: str


def session_read(
    row: ChatSession,
    *,
    is_scheduled: bool = False,
    team_name: str | None = None,
    agent_name: str | None = None,
    agent_available: bool | None = None,
) -> ChatSessionRead:
    return ChatSessionRead(
        id=row.id,
        tenant_id=row.tenant_id,
        user_id=row.user_id,
        agent_id=row.agent_id,
        agent_name=agent_name,
        agent_available=agent_available,
        title=row.title,
        active_skill_id=row.active_skill_id,
        active_step_id=row.active_step_id,
        status=row.status,
        summary=row.summary,
        last_agent_question=row.last_agent_question,
        is_scheduled=is_scheduled,
        team_id=row.team_id,
        team_name=team_name,
        created_at=row.created_at.isoformat(),
        updated_at=row.updated_at.isoformat(),
    )


def human_handoff_read(row: HumanHandoffRequest) -> HumanHandoffRead:
    return HumanHandoffRead(
        id=row.id,
        tenant_id=row.tenant_id,
        session_id=row.session_id,
        agent_id=row.agent_id,
        requester_user_id=row.requester_user_id,
        assignee_user_id=row.assignee_user_id,
        trigger_skill_id=row.trigger_skill_id,
        trigger_step_id=row.trigger_step_id,
        context_summary=row.context_summary,
        pending_question=row.pending_question,
        status=row.status,
        human_reply=row.human_reply,
        metadata=row.metadata_json or {},
        created_at=row.created_at.isoformat(),
        updated_at=row.updated_at.isoformat(),
        answered_at=row.answered_at.isoformat() if row.answered_at else None,
    )


def _user_message_metadata(request: ChatTurnRequest) -> dict[str, object]:
    metadata: dict[str, object] = {}
    if request.client_turn_id:
        metadata["client_turn_id"] = request.client_turn_id
    if request.interaction_mode == "scheduled_task":
        metadata["interaction_mode"] = "scheduled_task"
    if request.model_config_id:
        metadata["model_config_id"] = request.model_config_id
    if request.attachments:
        metadata["attachments"] = [item.model_dump(mode="json") for item in request.attachments]
    return metadata


def _schedule_session_title_summary(
    tenant_id: str,
    user_id: str,
    session_id: str,
    agent_id: str | None,
    *, data_services=None,
) -> None:
    if not session_id:
        return
    job_key = f"{getattr(data_services, 'namespace', 'oss-local')}:{tenant_id}:{user_id}:{session_id}"
    with _session_title_summary_jobs_lock:
        if job_key in _session_title_summary_jobs:
            return
        _session_title_summary_jobs.add(job_key)

    def run() -> None:
        try:
            _summarize_session_title_once(tenant_id, user_id, session_id, agent_id, data_services=data_services)
        finally:
            with _session_title_summary_jobs_lock:
                _session_title_summary_jobs.discard(job_key)

    from staffdeck_harness.runtime.jobs import enqueue_runtime_job
    try:
        enqueue_runtime_job("session.title", run, registry=getattr(data_services, "registry", None))
    except Exception:
        with _session_title_summary_jobs_lock:
            _session_title_summary_jobs.discard(job_key)


def _summarize_session_title_once(
    tenant_id: str,
    user_id: str,
    session_id: str,
    agent_id: str | None,
    *, data_services=None,
) -> None:
    def session_factory():
        return data_services.session(None, purpose="session.title") if data_services else Session(engine)
    try:
        for attempt in range(8):
            messages: list[Message] = []
            model_config = None
            effective_agent_id = agent_id
            with session_factory() as db:
                session = db.exec(
                    select(ChatSession).where(
                        ChatSession.id == session_id,
                        ChatSession.tenant_id == tenant_id,
                        ChatSession.user_id == user_id,
                    )
                ).first()
                if not session:
                    return
                if (session.title or "").strip():
                    return
                existing = db.exec(
                    select(AgentEvent).where(
                        AgentEvent.tenant_id == tenant_id,
                        AgentEvent.session_id == session_id,
                        AgentEvent.event_type == SESSION_TITLE_SUMMARY_EVENT,
                    )
                ).first()
                if existing:
                    return
                messages = db.exec(
                    select(Message)
                    .where(Message.tenant_id == tenant_id, Message.session_id == session_id)
                    .order_by(Message.created_at)
                    .limit(6)
                ).all()
                if not any(row.role == "user" for row in messages):
                    messages = []
                else:
                    effective_agent_id = agent_id or session.agent_id
                    if data_services:
                        from staffdeck_harness.composition.sources import resolve_source
                        from staffdeck_harness.contracts.manifest import SlotName
                        from staffdeck_harness.contracts.sources import SourceContext
                        from staffdeck_harness.modules.registry import peek_registry
                        model_config = resolve_source(getattr(data_services, "registry", None) or peek_registry(),
                            SlotName.STAFF_SOURCE, db).model(SourceContext(tenant_id, effective_agent_id,
                            session_id, "web", user_id))
                    else:
                        model_config = model_for_agent(db, tenant_id, effective_agent_id)

            if not messages:
                if attempt < 7:
                    time.sleep(0.25)
                    continue
                return

            payload = {
                "current_title": "",
                "messages": [
                    {"role": row.role, "content": row.content[:1200]}
                    for row in messages
                    if row.role in {"user", "assistant"}
                ],
            }
            title = ""
            title_source = "first_user_fallback"
            if model_config:
                try:
                    title_turn_id = next((row.id for row in messages if row.role == "user"), "")

                    def persist_title_span(
                        event_type: str, event_payload: dict[str, object]
                    ) -> None:
                        traced_payload = dict(event_payload)
                        if title_turn_id:
                            traced_payload.setdefault("turn_id", title_turn_id)
                            traced_payload.setdefault("user_message_id", title_turn_id)
                        with session_factory() as span_db:
                            _persist_relay_only_event(
                                span_db,
                                tenant_id,
                                session_id,
                                event_type,
                                traced_payload,
                            )

                    with bind_span_sink(persist_title_span), llm_operation("session.title"):
                        raw = LLMClient(model_config).generate_json(SESSION_TITLE_PROMPT, payload)
                    title = _normalize_auto_title(str(raw.get("title") or ""))
                    if title:
                        title_source = "first_turn_summary"
                except LLMError:
                    title = ""
            if not title:
                title = _fallback_session_title(messages)
            if not title:
                return

            with session_factory() as db:
                session = db.exec(
                    select(ChatSession).where(
                        ChatSession.id == session_id,
                        ChatSession.tenant_id == tenant_id,
                        ChatSession.user_id == user_id,
                    )
                ).first()
                if not session:
                    return
                if (session.title or "").strip():
                    return
                existing = db.exec(
                    select(AgentEvent).where(
                        AgentEvent.tenant_id == tenant_id,
                        AgentEvent.session_id == session_id,
                        AgentEvent.event_type == SESSION_TITLE_SUMMARY_EVENT,
                    )
                ).first()
                if existing:
                    return
                session.title = title
                db.add(session)
                db.add(
                    AgentEvent(
                        tenant_id=tenant_id,
                        session_id=session_id,
                        event_type=SESSION_TITLE_SUMMARY_EVENT,
                        payload_json={
                            "title": title,
                            "source": title_source,
                            "agent_id": effective_agent_id,
                        },
                    )
                )
                db.commit()
                return
    except (LLMError, Exception):
        return


def _session_title_summary_payload(db: Session, tenant_id: str, session_id: str) -> dict[str, str] | None:
    event = db.exec(
        select(AgentEvent)
        .where(
            AgentEvent.tenant_id == tenant_id,
            AgentEvent.session_id == session_id,
            AgentEvent.event_type == SESSION_TITLE_SUMMARY_EVENT,
        )
        .order_by(AgentEvent.created_at.desc())
        .limit(1)
    ).first()
    payload = event.payload_json if event else None
    title = payload.get("title") if isinstance(payload, dict) else None
    if not isinstance(title, str) or not title.strip():
        return None
    return {"sessionId": session_id, "title": title.strip()}


def _normalize_auto_title(value: str) -> str:
    title = value.strip().strip("\"'“”‘’`")
    for token in ("\n", "\r", "\t", "：", ":", "。", "，", ",", "；", ";"):
        title = title.replace(token, " ")
    title = " ".join(part for part in title.split() if part)
    return title[:24]


def _fallback_session_title(messages: list[Message]) -> str:
    first_user = next((row.content for row in messages if row.role == "user" and row.content.strip()), "")
    if not first_user:
        return ""
    return _normalize_auto_title(first_user)


def _normalized_session_event_payload(row: AgentEvent) -> dict[str, object]:
    payload = dict(row.payload_json or {})
    event_name = str(payload.get("event") or payload.get("type") or row.event_type)
    data = payload.get("data")
    if not isinstance(data, dict):
        data = {key: value for key, value in payload.items() if key not in EVENT_PAYLOAD_META_KEYS}
    normalized: dict[str, object] = {
        **payload,
        "id": str(payload.get("id") or row.id),
        "event": event_name,
        "type": str(payload.get("type") or event_name),
        "event_type": str(payload.get("event_type") or event_name),
        "created_at": str(payload.get("created_at") or row.created_at.isoformat()),
        "data": data,
    }
    if "run_id" not in normalized and data.get("run_id"):
        normalized["run_id"] = str(data.get("run_id"))
    return normalized


def _apply_handoff_reply(db, row, reply, *, answered_by_user_id, source="web"):
    from app.core.handoff_reply_service import apply_handoff_reply

    return apply_handoff_reply(db, row, reply, answered_by_user_id=answered_by_user_id,
                              source=source, resume=_resume_human_handoff_async)


def _maybe_handle_scheduled_task_request(
    db: Session,
    request: ChatTurnRequest,
    chat_session: ChatSession,
) -> tuple[ChatTurnResponse, ScheduledTaskDraftRead] | None:
    if request.interaction_mode != "scheduled_task" or not request.agent_id:
        return None
    if request.client_turn_id and is_chat_turn_cancelled(
        chat_session.id,
        request.client_turn_id,
        db=db,
        identity_kind="client",
    ):
        # Cancellation wins over the shortcut. Let the normal Harness path
        # claim and terminalize the logical turn instead of creating a draft.
        return None
    draft = detect_scheduled_task_draft(
        db,
        request.tenant_id,
        request.agent_id,
        request.user_id,
        request.message,
        chat_session.id,
        request.client_timezone,
    )
    if not draft or not draft.should_create:
        return None

    turn_store = HarnessTurnStore(db)
    turn_claim = turn_store.claim(chat_session, request)
    if turn_claim.replay is not None:
        return turn_claim.replay, draft

    reply = _scheduled_task_draft_reply(draft)
    now = utc_now()
    intent_time = now + timedelta(microseconds=1)
    parse_time = now + timedelta(microseconds=2)
    draft_status_time = now + timedelta(microseconds=3)
    event_time = now + timedelta(microseconds=4)
    assistant_time = now + timedelta(microseconds=5)
    state_time = now + timedelta(microseconds=6)
    chat_session.updated_at = assistant_time
    chat_session.summary = f"最近回复：{reply[:120]}"
    user_message = Message(
        tenant_id=request.tenant_id,
        session_id=chat_session.id,
        role="user",
        content=request.message,
        metadata_json=_user_message_metadata(request),
        created_at=now,
    )
    db.add(user_message)
    db.flush()
    turn_store.bind_user_message(turn_claim.record, user_message.id)
    draft_payload = draft.model_dump(mode="json")
    db.add(
        AgentEvent(
            tenant_id=request.tenant_id,
            session_id=chat_session.id,
            event_type="user_message_received",
            payload_json={
                "message_id": user_message.id,
                "client_turn_id": request.client_turn_id,
                "message": request.message,
                "channel": request.channel,
                "user_id": request.user_id,
            },
            created_at=now,
        )
    )
    _add_stream_status_event(
        db,
        request.tenant_id,
        chat_session.id,
        user_message.id,
        "scheduled_task_intent",
        "识别定时任务需求",
        created_at=intent_time,
    )
    _add_stream_status_event(
        db,
        request.tenant_id,
        chat_session.id,
        user_message.id,
        "scheduled_task_parse",
        "解析执行计划",
        created_at=parse_time,
    )
    _add_stream_status_event(
        db,
        request.tenant_id,
        chat_session.id,
        user_message.id,
        "scheduled_task_draft",
        "生成定时任务草案",
        extra=draft_payload,
        created_at=draft_status_time,
    )
    assistant_message = Message(
        tenant_id=request.tenant_id,
        session_id=chat_session.id,
        role="assistant",
        content=reply,
        metadata_json={
            "scheduled_task_draft": draft_payload,
            "user_message_id": user_message.id,
            "turn_id": user_message.id,
        },
        created_at=assistant_time,
    )
    db.add(assistant_message)
    stage_channel_delivery(db, chat_session, assistant_message)
    db.add(
        AgentEvent(
            tenant_id=request.tenant_id,
            session_id=chat_session.id,
            event_type="scheduled_task_draft_created",
            payload_json={**draft_payload, "user_message_id": user_message.id, "turn_id": user_message.id},
            created_at=event_time,
        )
    )
    db.add(
        AgentEvent(
            tenant_id=request.tenant_id,
            session_id=chat_session.id,
            event_type="assistant_message_created",
            payload_json={
                "message_id": assistant_message.id,
                "assistant_message_id": assistant_message.id,
                "user_message_id": user_message.id,
                "turn_id": user_message.id,
                "reply": reply,
                "scheduled_task_draft": draft_payload,
            },
            created_at=assistant_time,
        )
    )
    state = public_session(chat_session)
    db.add(
        AgentEvent(
            tenant_id=request.tenant_id,
            session_id=chat_session.id,
            event_type="session_state_changed",
            payload_json=state.model_dump(),
            created_at=state_time,
        )
    )
    response = ChatTurnResponse(
        reply=reply,
        session_id=chat_session.id,
        session_state=public_session(chat_session),
    )
    turn_store.complete(turn_claim.record, response)
    db.refresh(chat_session)
    return response, draft


def _add_stream_status_event(
    db: Session,
    tenant_id: str,
    session_id: str,
    user_message_id: str,
    phase: str,
    text: str,
    *,
    extra: dict | None = None,
    created_at=None,
) -> None:
    payload = {
        "phase": phase,
        "text": text,
        "user_message_id": user_message_id,
        "turn_id": user_message_id,
        **(extra or {}),
    }
    db.add(
        AgentEvent(
            tenant_id=tenant_id,
            session_id=session_id,
            event_type="stream_status",
            payload_json=payload,
            created_at=created_at or utc_now(),
        )
    )


def _scheduled_task_draft_reply(draft: ScheduledTaskDraftRead) -> str:
    lines = [
        "我已按你选择的定时项目整理成自动任务草案。",
        f"任务：{draft.title}",
        f"计划：{_format_draft_schedule(draft)}",
        f"执行内容：{draft.prompt}",
        "确认下方卡片后才会启用；确认前不会创建自动任务。",
    ]
    return "\n".join(lines)


def _format_draft_schedule(draft: ScheduledTaskDraftRead) -> str:
    return _format_scheduled_task_schedule(draft.schedule_type, draft.schedule or {})


def _format_once_schedule(schedule: dict) -> str:
    return f"一次性 {schedule.get('run_at') or '待确认时间'}"


def _format_weekly_schedule(schedule: dict) -> str:
    return f"每周 {_format_weekday_labels(schedule.get('weekdays'))} {schedule.get('time') or DEFAULT_TASK_TIME}"


def _format_monthly_schedule(schedule: dict) -> str:
    return f"每月 {schedule.get('day_of_month') or 1} 号 {schedule.get('time') or DEFAULT_TASK_TIME}"


def _format_daily_schedule(schedule: dict) -> str:
    return f"每天 {schedule.get('time') or DEFAULT_TASK_TIME}"


SCHEDULE_TEXT_FORMATTERS: dict[str, Callable[[dict], str]] = {
    "once": _format_once_schedule,
    "weekly": _format_weekly_schedule,
    "monthly": _format_monthly_schedule,
    "daily": _format_daily_schedule,
}


def _format_scheduled_task_schedule(schedule_type: object, schedule_value: object) -> str:
    schedule = schedule_value if isinstance(schedule_value, dict) else {}
    schedule_type_text = str(schedule_type or "daily")
    formatter = SCHEDULE_TEXT_FORMATTERS.get(schedule_type_text, _format_daily_schedule)
    return formatter(schedule)


def _format_weekday_labels(value: object) -> str:
    if not isinstance(value, list):
        return SCHEDULE_WEEKDAY_LABELS[0]
    labels: list[str] = []
    for item in value:
        text = str(item).strip()
        if not text.isdigit():
            continue
        day = int(text)
        if 0 <= day < len(SCHEDULE_WEEKDAY_LABELS):
            labels.append(SCHEDULE_WEEKDAY_LABELS[day])
    return "、".join(labels) or SCHEDULE_WEEKDAY_LABELS[0]


def _scheduled_task_trace_detail(payload: dict) -> str | None:
    title = str(payload.get("title") or "").strip()
    schedule = _format_scheduled_task_schedule(payload.get("schedule_type"), payload.get("schedule"))
    detail = " · ".join(part for part in (title, schedule, "等待确认后启用") if part)
    return detail or None


def _scheduled_task_trace_lines(payload: dict, *, state: str = "completed") -> list[dict]:
    schedule = _format_scheduled_task_schedule(payload.get("schedule_type"), payload.get("schedule"))
    return [
        {
            "id": "scheduled_task_intent",
            "kind": "decision",
            "text": "识别定时任务需求",
            "detail": "用户选择了创建定时任务模式",
            "state": "completed",
        },
        {
            "id": "scheduled_task_parse",
            "kind": "decision",
            "text": "解析执行计划",
            "detail": f"计划：{schedule}" if schedule else None,
            "state": "completed",
        },
        {
            "id": "scheduled_task_draft",
            "kind": "decision",
            "text": "生成定时任务草案",
            "detail": _scheduled_task_trace_detail(payload),
            "state": state,
        },
    ]


def _persist_scheduled_task_draft(
    db: Session,
    tenant_id: str,
    session_id: str,
    draft: ScheduledTaskDraftRead,
) -> None:
    if not session_id:
        return
    payload = draft.model_dump(mode="json")
    latest_assistant = db.exec(
        select(Message)
        .where(Message.tenant_id == tenant_id, Message.session_id == session_id, Message.role == "assistant")
        .order_by(Message.created_at.desc())
    ).first()
    if latest_assistant:
        metadata = dict(latest_assistant.metadata_json or {})
        metadata["scheduled_task_draft"] = payload
        latest_assistant.metadata_json = metadata
        db.add(latest_assistant)
    db.add(
        AgentEvent(
            tenant_id=tenant_id,
            session_id=session_id,
            event_type="scheduled_task_draft_created",
            payload_json=payload,
            created_at=utc_now(),
        )
    )
    db.commit()


def _reply_chunks(reply: str) -> Iterator[str]:
    for index in range(0, len(reply), STREAM_REPLY_CHUNK_SIZE):
        yield reply[index : index + STREAM_REPLY_CHUNK_SIZE]


def _maybe_handle_data_query_request(
    db: Session,
    request: ChatTurnRequest,
    chat_session: ChatSession,
) -> tuple[ChatTurnResponse, Any] | None:
    if not request.message or not request.message.strip():
        return None
    if request.interaction_mode == "scheduled_task":
        return None
    if request.client_turn_id and is_chat_turn_cancelled(
        chat_session.id,
        request.client_turn_id,
        db=db,
        identity_kind="client",
    ):
        return None

    from app.channels.service_intent_fast_path import _format_markdown_table
    from app.data_query.intent_router import route_question
    from app.data_query.service import execute_query_by_id

    route_result = route_question(
        question=request.message,
        tenant_id=request.tenant_id,
        db=db,
    )
    if not route_result or route_result.confidence < 0.8:
        return None

    try:
        query_res = execute_query_by_id(
            db=db,
            template_id=route_result.template_id,
            tenant_id=request.tenant_id,
            params=route_result.params,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("Data query execution failed in chat fast-path: %s", exc)
        return None

    table_md = _format_markdown_table(query_res)
    param_summary = (
        ", ".join(f"{k}={v}" for k, v in route_result.params.items())
        if route_result.params
        else "默认参数"
    )
    reply = (
        f"📊 **数据查询结果** · {route_result.template_name}\n"
        f"参数：`{param_summary}`\n\n"
        f"{table_md}\n\n"
        f"> 💡 数据来自模板「{route_result.template_name}」，耗时 {query_res.execution_time_ms:.1f}ms"
    )

    turn_store = HarnessTurnStore(db)
    turn_claim = turn_store.claim(chat_session, request)
    if turn_claim.replay is not None:
        return turn_claim.replay, route_result

    now = utc_now()
    intent_time = now + timedelta(microseconds=1)
    assistant_time = now + timedelta(microseconds=2)
    state_time = now + timedelta(microseconds=3)
    chat_session.updated_at = assistant_time
    chat_session.summary = f"最近回复：{reply[:120]}"

    user_message = Message(
        tenant_id=request.tenant_id,
        session_id=chat_session.id,
        role="user",
        content=request.message,
        metadata_json=_user_message_metadata(request),
        created_at=now,
    )
    db.add(user_message)
    db.flush()
    turn_store.bind_user_message(turn_claim.record, user_message.id)

    db.add(
        AgentEvent(
            tenant_id=request.tenant_id,
            session_id=chat_session.id,
            event_type="user_message_received",
            payload_json={
                "message_id": user_message.id,
                "client_turn_id": request.client_turn_id,
                "message": request.message,
                "channel": request.channel,
                "user_id": request.user_id,
            },
            created_at=now,
        )
    )
    _add_stream_status_event(
        db,
        request.tenant_id,
        chat_session.id,
        user_message.id,
        "data_query",
        f"查询业务数据 · {route_result.template_name}",
        extra={"template_id": route_result.template_id, "params": route_result.params},
        created_at=intent_time,
    )

    assistant_message = Message(
        tenant_id=request.tenant_id,
        session_id=chat_session.id,
        role="assistant",
        content=reply,
        metadata_json={
            "data_query_result": {
                "template_id": route_result.template_id,
                "template_name": route_result.template_name,
                "params": route_result.params,
                "row_count": len(query_res.rows),
                "execution_time_ms": query_res.execution_time_ms,
            },
            "user_message_id": user_message.id,
            "turn_id": user_message.id,
        },
        created_at=assistant_time,
    )
    db.add(assistant_message)
    stage_channel_delivery(db, chat_session, assistant_message)
    db.add(
        AgentEvent(
            tenant_id=request.tenant_id,
            session_id=chat_session.id,
            event_type="assistant_message_created",
            payload_json={
                "message_id": assistant_message.id,
                "assistant_message_id": assistant_message.id,
                "user_message_id": user_message.id,
                "turn_id": user_message.id,
                "reply": reply,
            },
            created_at=assistant_time,
        )
    )
    state = public_session(chat_session)
    db.add(
        AgentEvent(
            tenant_id=request.tenant_id,
            session_id=chat_session.id,
            event_type="session_state_changed",
            payload_json=state.model_dump(),
            created_at=state_time,
        )
    )
    response = ChatTurnResponse(
        reply=reply,
        session_id=chat_session.id,
        session_state=public_session(chat_session),
    )
    turn_store.complete(turn_claim.record, response)
    db.refresh(chat_session)
    return response, route_result


def _validate_chat_turn_attachments(
    request: ChatTurnRequest,
) -> ChatTurnRequest:
    try:
        attachments = validate_chat_turn_attachments(
            request.attachments,
            max_attachments=MAX_CHAT_ATTACHMENTS,
            max_attachment_bytes=MAX_CHAT_ATTACHMENT_BYTES,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return request.model_copy(update={"attachments": attachments})


@router.get("/slash-commands", response_model=list[SlashCommandRead])
def list_slash_commands(
    tenant_id: str = Query(...),
    agent_id: str = Query(...),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> list[SlashCommandRead]:
    _ensure_request_tenant(tenant_id, current_user)
    agent = _ensure_chat_agent_available(
        db,
        tenant_id,
        agent_id,
        current_user,
    )
    from types import SimpleNamespace

    from app.config import get_settings
    from staffdeck_harness.capabilities.manifest import SnapshotManifestBuilder
    from staffdeck_harness.composition.compiler import CompositionCompiler
    from staffdeck_harness.composition.sources import resolve_staff
    from staffdeck_harness.contracts.sop import SopDefinition
    from staffdeck_harness.contracts.sources import SourceContext
    from staffdeck_harness.modules.registry import get_registry
    from staffdeck_harness.security.profile import Guard, get_profile
    registry, profile = get_registry(get_settings()), get_profile(get_settings())
    staff, identity = resolve_staff(registry, db, SourceContext(tenant_id, agent.id, user_id=current_user.id), profile)
    snapshot = CompositionCompiler().compile(staff, generation=registry.generation, strict=False)
    skills = discoverable_sops([SopDefinition(v.row_id, tenant_id, v.skill_id, v.version,
        v.name, dict(v.content)) for v in staff.sops if snapshot.sop(v.skill_id)])
    manifest = SnapshotManifestBuilder(SimpleNamespace(db=db, registry=registry, snapshot=snapshot,
        user_message_id=None, session=None, security_context=identity, guard=Guard("staff", profile))).build(
            tenant_id, agent.id, None, None)
    return slash_command_catalog(skills, manifest)


@router.post("/attachments", response_model=list[ChatAttachmentRead])
async def upload_chat_attachments(
    tenant_id: str = Query(...),
    files: list[UploadFile] = File(...),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> list[ChatAttachmentRead]:
    _ensure_request_tenant(tenant_id, current_user)
    ensure_tenant(db, tenant_id)
    if not files:
        raise HTTPException(status_code=400, detail="No files uploaded")
    if len(files) > MAX_CHAT_ATTACHMENTS:
        raise HTTPException(status_code=400, detail=f"最多一次上传 {MAX_CHAT_ATTACHMENTS} 个文件")
    parsed: list[ChatAttachmentRead] = []
    from app.session.attachment_store import stage_chat_attachment

    for file in files:
        data = await file.read()
        if len(data) > MAX_CHAT_ATTACHMENT_BYTES:
            raise HTTPException(status_code=413, detail=f"{file.filename or '文件'} 超过上传大小限制")
        attachment = parse_chat_attachment(
            file.filename or "uploaded-file",
            file.content_type,
            data,
            extract_text=False,
        )
        parsed.append(
            stage_chat_attachment(
                attachment,
                data,
                tenant_id=tenant_id,
                user_id=current_user.id,
            )
        )
    return parsed


@router.post("/turn", response_model=ChatTurnResponse)
def chat_turn(
    request: ChatTurnRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> ChatTurnResponse:
    _ensure_request_tenant(request.tenant_id, current_user)
    request = request.model_copy(
        update={
            "user_id": current_user.id,
            "context_injection": None,
            "scheduled_task_id": None,
            "message_visibility": "visible",
        }
    )
    request = _validate_chat_turn_attachments(request)
    team_tl_team: Team | None = None
    if request.session_id:
        chat_session = _ensure_chat_session_available(db, request.tenant_id, current_user.id, request.session_id)
        _ensure_team_session_human_writable(chat_session)
        request = _bind_request_to_session_agent(db, request, chat_session, current_user)
        team_tl_team = _team_tl_session_team(db, chat_session)
    else:
        _ensure_chat_agent_available(db, request.tenant_id, request.agent_id, current_user)
    ensure_tenant(db, request.tenant_id)
    if not request.message.strip() and not request.attachments:
        raise HTTPException(status_code=400, detail="Message cannot be empty")
    original_message = request.message
    if team_tl_team is not None:
        # 团队 TL 会话:注入团队上下文(花名册/未闭环任务/黑板/派任务格式)后再走正常引擎
        request = request.model_copy(
            update={
                "context_injection": build_tl_chat_context(
                    db, team_tl_team, original_message
                ),
                "interaction_mode": "team_tl",
            }
        )
    if request.session_id:
        scheduled_response = _maybe_handle_scheduled_task_request(db, request, chat_session)
        if scheduled_response:
            response, _draft = scheduled_response
            from staffdeck_harness.runtime.services import runtime_services
            _schedule_session_title_summary(request.tenant_id, request.user_id, response.session_id, request.agent_id, data_services=runtime_services(db))
            return response
        data_query_resp = _maybe_handle_data_query_request(db, request, chat_session)
        if data_query_resp:
            response, _route = data_query_resp
            _schedule_session_title_summary(request.tenant_id, request.user_id, response.session_id, request.agent_id)
            return response
    response = AgentLoop(db).handle_turn(request)
    from staffdeck_harness.runtime.services import runtime_services
    _schedule_session_title_summary(request.tenant_id, request.user_id, response.session_id, request.agent_id, data_services=runtime_services(db))
    if team_tl_team is not None:
        # TL 回复后处理:解析派任务块并创建任务(与 tl_chat 端点同语义);
        # 后处理失败不影响本轮回复
        try:
            process_tl_reply(
                db,
                team=team_tl_team,
                session=chat_session,
                user=current_user,
                user_message=original_message,
                reply=response.reply or "",
                client_turn_id=request.client_turn_id,
            )
        except Exception:
            logger.exception("team TL reply post-processing failed")
    if request.interaction_mode == "scheduled_task" and request.agent_id:
        draft = detect_scheduled_task_draft(
            db,
            request.tenant_id,
            request.agent_id,
            request.user_id,
            request.message,
            response.session_id,
            request.client_timezone,
        )
        if draft and draft.should_create:
            _persist_scheduled_task_draft(db, request.tenant_id, response.session_id, draft)
    return response


@router.post("/stream")
def chat_stream(
    request: ChatTurnRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> StreamingResponse:
    _ensure_request_tenant(request.tenant_id, current_user)
    request = request.model_copy(
        update={
            "user_id": current_user.id,
            "context_injection": None,
            "scheduled_task_id": None,
            "message_visibility": "visible",
        }
    )
    request = _validate_chat_turn_attachments(request)
    ensure_tenant(db, request.tenant_id)
    team_tl_team_id: str | None = None
    if request.session_id:
        chat_session = _ensure_chat_session_available(db, request.tenant_id, current_user.id, request.session_id)
        _ensure_team_session_human_writable(chat_session)
        request = _bind_request_to_session_agent(db, request, chat_session, current_user)
        team_tl_team = _team_tl_session_team(db, chat_session)
        team_tl_team_id = team_tl_team.id if team_tl_team is not None else None
    else:
        _ensure_chat_agent_available(db, request.tenant_id, request.agent_id, current_user)
    if not request.message.strip() and not request.attachments:
        raise HTTPException(status_code=400, detail="Message cannot be empty")
    original_message = request.message
    if team_tl_team_id is not None:
        # 团队 TL 会话:注入团队上下文(花名册/未闭环任务/黑板/派任务格式)后再走正常引擎
        request = request.model_copy(
            update={
                "context_injection": build_tl_chat_context(
                    db, db.get(Team, team_tl_team_id), original_message
                ),
                "interaction_mode": "team_tl",
            }
        )

    relay_ready = threading.Event()
    from staffdeck_harness.runtime.services import runtime_services
    data_services = runtime_services(db)
    worker_done = threading.Event()
    source_session_id = {"value": request.session_id or ""}
    worker_terminal = {"seen": False}
    initial_cursor = _latest_event_cursor(db, request.tenant_id, request.session_id) if request.session_id else None

    def set_source_session(session_id: str) -> None:
        if not session_id:
            return
        source_session_id["value"] = session_id
        relay_ready.set()

    if source_session_id["value"]:
        relay_ready.set()

    def run_stream_worker() -> None:
        span_sink_token = None
        try:
            with data_services.session(None, purpose="stream-worker") as worker_db:
                span_turn_id = {"value": ""}

                def persist_span(event_type: str, payload: dict[str, object]) -> None:
                    session_id = source_session_id["value"] or request.session_id or ""
                    if not session_id:
                        return
                    turn_id = span_turn_id["value"]
                    event_payload = dict(payload)
                    if turn_id:
                        event_payload.setdefault("turn_id", turn_id)
                        event_payload.setdefault("user_message_id", turn_id)
                    if request.client_turn_id:
                        event_payload.setdefault("client_turn_id", request.client_turn_id)
                    _persist_relay_only_event(
                        worker_db,
                        request.tenant_id,
                        session_id,
                        event_type,
                        event_payload,
                    )

                span_sink_token = set_span_sink(persist_span)
                ensure_tenant(worker_db, request.tenant_id)
                if request.session_id:
                    chat_session = _ensure_chat_session_available(
                        worker_db,
                        request.tenant_id,
                        request.user_id,
                        request.session_id,
                    )
                    if request.interaction_mode == "scheduled_task":
                        _persist_relay_only_event(
                            worker_db,
                            request.tenant_id,
                            chat_session.id,
                            "stream_status",
                            {"phase": "scheduled_task_intent", "text": "识别定时任务需求"},
                        )
                        _persist_relay_only_event(
                            worker_db,
                            request.tenant_id,
                            chat_session.id,
                            "stream_status",
                            {"phase": "scheduled_task_parse", "text": "解析执行计划"},
                        )
                    scheduled_response = _maybe_handle_scheduled_task_request(worker_db, request, chat_session)
                    if scheduled_response:
                        response, draft = scheduled_response
                        set_source_session(response.session_id)
                        message_id, client_turn_id = _resolve_turn_ids_from_events(
                            worker_db,
                            request.tenant_id,
                            response.session_id,
                            request.client_turn_id or "",
                        )
                        turn_payload = {
                            "turn_id": message_id,
                            "user_message_id": message_id,
                            "client_turn_id": client_turn_id or None,
                        }
                        _persist_relay_only_event(
                            worker_db,
                            request.tenant_id,
                            response.session_id,
                            "stream_status",
                            {
                                "phase": "scheduled_task_draft",
                                "text": "生成定时任务草案",
                                **draft.model_dump(mode="json"),
                                **turn_payload,
                            },
                        )
                        _persist_relay_only_event(
                            worker_db,
                            request.tenant_id,
                            response.session_id,
                            "scheduled_task_draft",
                            {**draft.model_dump(mode="json"), **turn_payload},
                        )
                        for chunk in _reply_chunks(response.reply):
                            _persist_relay_only_event(
                                worker_db,
                                request.tenant_id,
                                response.session_id,
                                "stream_delta",
                                {"content": chunk, **turn_payload},
                            )
                        _persist_relay_only_event(
                            worker_db,
                            request.tenant_id,
                            response.session_id,
                            "stream_end",
                            turn_payload,
                        )
                        _persist_relay_only_event(
                            worker_db,
                            request.tenant_id,
                            response.session_id,
                            "complete",
                            {**response.model_dump(mode="json"), **turn_payload},
                        )
                        worker_terminal["seen"] = True
                        _schedule_session_title_summary(
                            request.tenant_id,
                            request.user_id,
                            response.session_id,
                            request.agent_id,
                            data_services=data_services,
                        )
                        return
                    data_query_resp = _maybe_handle_data_query_request(worker_db, request, chat_session)
                    if data_query_resp:
                        response, route_result = data_query_resp
                        set_source_session(response.session_id)
                        message_id, client_turn_id = _resolve_turn_ids_from_events(
                            worker_db,
                            request.tenant_id,
                            response.session_id,
                            request.client_turn_id or "",
                        )
                        turn_payload = {
                            "turn_id": message_id,
                            "user_message_id": message_id,
                            "client_turn_id": client_turn_id or None,
                        }
                        _persist_relay_only_event(
                            worker_db,
                            request.tenant_id,
                            response.session_id,
                            "stream_status",
                            {
                                "phase": "data_query",
                                "text": f"查询业务数据 · {route_result.template_name}",
                                **turn_payload,
                            },
                        )
                        for chunk in _reply_chunks(response.reply):
                            _persist_relay_only_event(
                                worker_db,
                                request.tenant_id,
                                response.session_id,
                                "stream_delta",
                                {"content": chunk, **turn_payload},
                            )
                        _persist_relay_only_event(
                            worker_db,
                            request.tenant_id,
                            response.session_id,
                            "stream_end",
                            turn_payload,
                        )
                        _persist_relay_only_event(
                            worker_db,
                            request.tenant_id,
                            response.session_id,
                            "complete",
                            {**response.model_dump(mode="json"), **turn_payload},
                        )
                        worker_terminal["seen"] = True
                        _schedule_session_title_summary(
                            request.tenant_id,
                            request.user_id,
                            response.session_id,
                            request.agent_id,
                        )
                        return
                for item in AgentLoop(worker_db).handle_turn_stream(request):
                    event_name = str(item["event"])
                    data = item["data"] if isinstance(item.get("data"), dict) else {}
                    item_session_id = str(data.get("sessionId") or request.session_id or source_session_id["value"] or "")
                    if item_session_id:
                        set_source_session(item_session_id)
                    if event_name == "session_created" and item_session_id:
                        _persist_relay_only_event(worker_db, request.tenant_id, item_session_id, event_name, data)
                    elif event_name == "complete" and item_session_id:
                        _persist_relay_only_event(worker_db, request.tenant_id, item_session_id, event_name, data)
                        worker_terminal["seen"] = True
                    elif event_name in {"stream_cancelled", "stream_interrupted", "error", "error_occurred"}:
                        worker_terminal["seen"] = True
                    if item["event"] == "user_message_received":
                        event_source_session_id = str(item["data"].get("sessionId") or request.session_id or "")
                        set_source_session(event_source_session_id)
                        span_turn_id["value"] = str(
                            data.get("turn_id")
                            or data.get("user_message_id")
                            or data.get("message_id")
                            or ""
                        )
                        _schedule_session_title_summary(
                            request.tenant_id,
                            request.user_id,
                            event_source_session_id,
                            request.agent_id,
                            data_services=data_services,
                        )
                        continue
                    if item["event"] == "complete":
                        event_source_session_id = str(item["data"].get("sessionId") or request.session_id or "")
                        _schedule_session_title_summary(
                            request.tenant_id,
                            request.user_id,
                            event_source_session_id,
                            request.agent_id,
                            data_services=data_services,
                        )
                        if team_tl_team_id is not None:
                            # 团队 TL 会话:complete 后做派任务后处理(与 tl_chat 端点同语义);
                            # 后处理失败不影响本轮回复
                            try:
                                tl_team = worker_db.get(Team, team_tl_team_id)
                                tl_session = worker_db.get(
                                    ChatSession, event_source_session_id or request.session_id or ""
                                )
                                tl_user = worker_db.get(User, request.user_id) if request.user_id else None
                                if tl_team is not None and tl_session is not None and tl_user is not None:
                                    process_tl_reply(
                                        worker_db,
                                        team=tl_team,
                                        session=tl_session,
                                        user=tl_user,
                                        user_message=original_message,
                                        reply=str(data.get("reply") or ""),
                                        client_turn_id=request.client_turn_id,
                                    )
                            except Exception:
                                logger.exception("team TL reply post-processing failed")
                        if event_source_session_id:
                            summary_payload = _session_title_summary_payload(worker_db, request.tenant_id, event_source_session_id)
                            if summary_payload:
                                _persist_relay_only_event(
                                    worker_db,
                                    request.tenant_id,
                                    event_source_session_id,
                                    SESSION_TITLE_SUMMARY_EVENT,
                                    summary_payload,
                                )
                        if request.interaction_mode != "scheduled_task" or not request.agent_id:
                            continue
                        draft = detect_scheduled_task_draft(
                            worker_db,
                            request.tenant_id,
                            request.agent_id,
                            request.user_id,
                            request.message,
                            event_source_session_id or None,
                            request.client_timezone,
                        )
                        if draft and draft.should_create:
                            _persist_scheduled_task_draft(worker_db, request.tenant_id, event_source_session_id, draft)
                            _persist_relay_only_event(
                                worker_db,
                                request.tenant_id,
                                event_source_session_id,
                                "scheduled_task_draft",
                                draft.model_dump(mode="json"),
                            )
        except Exception as exc:
            logger.exception("chat stream worker failed")
            unavailable = _is_chat_agent_unavailable(exc, request.agent_id)
            session_id = source_session_id["value"] or request.session_id or ""
            if session_id:
                with data_services.session(None, purpose="stream-error") as error_db:
                    chat_session = error_db.get(ChatSession, session_id)
                    if chat_session:
                        _persist_chat_turn_interrupted(
                            error_db,
                            request.tenant_id,
                            chat_session,
                            request.client_turn_id or "",
                            str(exc) or "stream worker failed",
                            error_details={
                                **(
                                    {"error_code": "AGENT_UNAVAILABLE", "message": "此数字员工不再可用"}
                                    if unavailable else {}
                                ),
                                "error_type": exc.__class__.__name__,
                                "error_traceback": traceback.format_exc()[-STREAM_INTERRUPTED_TRACEBACK_CHAR_LIMIT:],
                            },
                        )
                        error_db.commit()
                        worker_terminal["seen"] = True
                        set_source_session(session_id)
        except BaseException as exc:
            logger.exception("chat stream worker stopped with base exception")
            session_id = source_session_id["value"] or request.session_id or ""
            if session_id:
                with data_services.session(None, purpose="stream-error") as error_db:
                    chat_session = error_db.get(ChatSession, session_id)
                    if chat_session:
                        _persist_chat_turn_interrupted(
                            error_db,
                            request.tenant_id,
                            chat_session,
                            request.client_turn_id or "",
                            exc.__class__.__name__,
                            error_details={
                                "error_type": exc.__class__.__name__,
                                "error_traceback": traceback.format_exc()[-STREAM_INTERRUPTED_TRACEBACK_CHAR_LIMIT:],
                            },
                        )
                        error_db.commit()
                        worker_terminal["seen"] = True
                        set_source_session(session_id)
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
        finally:
            if span_sink_token is not None:
                reset_span_sink(span_sink_token)
            session_id = source_session_id["value"] or request.session_id or ""
            if session_id and not worker_terminal["seen"]:
                with data_services.session(None, purpose="stream-finalize") as final_db:
                    chat_session = final_db.get(ChatSession, session_id)
                    if chat_session:
                        changed = _persist_chat_turn_interrupted(
                            final_db,
                            request.tenant_id,
                            chat_session,
                            request.client_turn_id or "",
                            "stream worker ended before terminal event",
                        )
                        if changed:
                            final_db.commit()
                            set_source_session(session_id)
            worker_done.set()

    threading.Thread(target=run_stream_worker, daemon=True).start()

    def stream_events() -> Iterator[str]:
        nonlocal initial_cursor
        relay_ready.wait(15)
        deadline = time.monotonic() + STREAM_RELAY_IDLE_TIMEOUT_SECONDS
        last_heartbeat_at = time.monotonic()
        terminal_sent = False
        internal_relay_turn_ids: set[str] = set()
        while True:
            session_id = source_session_id["value"]
            emitted = False
            if session_id:
                with data_services.session(None, purpose="stream-relay") as relay_db:
                    rows = _events_after_cursor(relay_db, request.tenant_id, session_id, initial_cursor)
                for row in rows:
                    payload = row.payload_json or {}
                    row_turn_ids = {
                        str(payload.get(key) or "").strip()
                        for key in ("turn_id", "user_message_id", "message_id", "client_turn_id")
                        if str(payload.get(key) or "").strip()
                    }
                    if payload.get("message_visibility") == "internal":
                        internal_relay_turn_ids.update(row_turn_ids)
                    event_name, data = _relay_event_payload(row)
                    initial_cursor = (row.created_at, row.id)
                    emitted = True
                    if row_turn_ids & internal_relay_turn_ids:
                        continue
                    yield _sse(event_name, data, row.id)
                    if event_name in STREAM_RELAY_TERMINAL_EVENTS:
                        terminal_sent = True
                if emitted:
                    deadline = time.monotonic() + STREAM_RELAY_IDLE_TIMEOUT_SECONDS
                    last_heartbeat_at = time.monotonic()
            if terminal_sent and worker_done.is_set() and not emitted:
                return
            if worker_done.is_set() and not emitted:
                return
            if time.monotonic() > deadline:
                if session_id:
                    with data_services.session(None, purpose="stream-timeout") as timeout_db:
                        chat_session = timeout_db.get(ChatSession, session_id)
                        if chat_session:
                            _persist_chat_turn_interrupted(
                                timeout_db,
                                request.tenant_id,
                                chat_session,
                                request.client_turn_id or "",
                                "stream relay timed out waiting for terminal event",
                            )
                            timeout_db.commit()
                    continue
                return
            now = time.monotonic()
            if now - last_heartbeat_at >= STREAM_RELAY_HEARTBEAT_SECONDS:
                last_heartbeat_at = now
                yield _sse(
                    "heartbeat",
                    {
                        "phase": "relay",
                        "sessionId": session_id or request.session_id or "",
                    },
                )
            time.sleep(STREAM_RELAY_POLL_SECONDS)

    return StreamingResponse(stream_events(), media_type="text/event-stream")


@router.post("/sessions/{session_id}/cancel")
def cancel_chat_turn_endpoint(
    session_id: str,
    request: ChatTurnCancelRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> dict[str, bool]:
    _ensure_request_tenant(request.tenant_id, current_user)
    chat_session = _ensure_chat_session_available(db, request.tenant_id, current_user.id, session_id)
    persisted = _persist_chat_turn_cancelled(
        db,
        request.tenant_id,
        chat_session,
        request.turn_id,
        current_user.id,
    )
    db.commit()
    # Publish the process-local fast path only after the durable cancellation
    # event commits. A failed commit must not change the outcome of a retry in
    # this process compared with a fresh worker.
    if persisted:
        cancel_chat_turn(session_id, request.turn_id)
    return {"ok": True}


def _persist_chat_turn_cancelled(
    db: Session,
    tenant_id: str,
    chat_session: ChatSession,
    requested_turn_id: str,
    cancelled_by_user_id: str | None = None,
) -> bool:
    requested_turn_id = requested_turn_id.strip()
    if not requested_turn_id:
        return False

    events = db.exec(
        select(AgentEvent)
        .where(AgentEvent.tenant_id == tenant_id, AgentEvent.session_id == chat_session.id)
        .order_by(AgentEvent.created_at)
    ).all()
    message_id = ""
    client_turn_id = ""
    for event in reversed(events):
        if event.event_type != "user_message_received":
            continue
        payload = event.payload_json or {}
        candidate_message_id = str(payload.get("message_id") or payload.get("user_message_id") or "").strip()
        candidate_client_turn_id = str(payload.get("client_turn_id") or "").strip()
        if requested_turn_id in {candidate_message_id, candidate_client_turn_id}:
            message_id = candidate_message_id
            client_turn_id = candidate_client_turn_id
            break
    if not message_id:
        message_id = requested_turn_id
        client_turn_id = requested_turn_id

    turn_ids = {message_id}
    if client_turn_id:
        turn_ids.add(client_turn_id)
    for event in events:
        if event.event_type not in {"assistant_message_created", "stream_cancelled"}:
            continue
        payload = event.payload_json or {}
        event_turn_ids = {
            str(payload.get("turn_id") or "").strip(),
            str(payload.get("user_message_id") or "").strip(),
            str(payload.get("message_id") or "").strip(),
            str(payload.get("client_turn_id") or "").strip(),
        }
        matches_message = bool(message_id and message_id in event_turn_ids)
        matches_client_turn = bool(client_turn_id and client_turn_id in event_turn_ids)
        if not matches_message and not matches_client_turn:
            continue
        if event.event_type == "stream_cancelled":
            _cancel_harness_turn_receipt(
                db,
                tenant_id,
                chat_session.id,
                message_id,
                client_turn_id,
            )
            return _ensure_cancelled_assistant_message(
                db,
                tenant_id,
                chat_session,
                message_id,
                client_turn_id,
                event.created_at + timedelta(microseconds=1),
            )
        return False

    now = utc_now()
    receipt_cancelled = _cancel_harness_turn_receipt(
        db,
        tenant_id,
        chat_session.id,
        message_id,
        client_turn_id,
    )
    if receipt_cancelled is False:
        # Normal completion already owns the terminal receipt. Do not append a
        # contradictory cancellation event/message after that linearization.
        return False
    db.add(
        AgentEvent(
            tenant_id=tenant_id,
            session_id=chat_session.id,
            event_type="stream_cancelled",
            payload_json={
                "turn_id": message_id,
                "user_message_id": message_id,
                "client_turn_id": client_turn_id or None,
                "phase": "cancelled",
                "text": "已停止生成",
                "cancelled_by_user_id": cancelled_by_user_id,
            },
            created_at=now,
        )
    )
    _ensure_cancelled_assistant_message(
        db,
        tenant_id,
        chat_session,
        message_id,
        client_turn_id,
        now + timedelta(microseconds=1),
    )
    chat_session.status = "active"
    chat_session.updated_at = now
    db.add(chat_session)
    return True


def _cancel_harness_turn_receipt(
    db: Session,
    tenant_id: str,
    session_id: str,
    user_message_id: str,
    client_turn_id: str,
) -> bool | None:
    """Fence the worker in the same transaction as the cancellation event."""

    identities = {value for value in (user_message_id, client_turn_id) if value}
    if not identities:
        return None
    matching = db.exec(
        select(HarnessTurnRecord).where(
            HarnessTurnRecord.tenant_id == tenant_id,
            HarnessTurnRecord.session_id == session_id,
            (
                HarnessTurnRecord.client_turn_id.in_(identities)
                | HarnessTurnRecord.user_message_id.in_(identities)
            ),
        )
    ).first()
    if matching is None:
        return None
    if matching.status == "cancelled":
        return True
    if matching.status != "started":
        return False
    now = utc_now()
    result = db.exec(
        update(HarnessTurnRecord)
        .where(
            HarnessTurnRecord.tenant_id == tenant_id,
            HarnessTurnRecord.session_id == session_id,
            HarnessTurnRecord.status == "started",
            (
                HarnessTurnRecord.client_turn_id.in_(identities)
                | HarnessTurnRecord.user_message_id.in_(identities)
            ),
        )
        .values(
            status="cancelled",
            error_json={
                "code": "CANCELLED",
                "message": "用户取消了当前 Harness 执行。",
            },
            finished_at=now,
            updated_at=now,
        )
        .execution_options(synchronize_session=False)
    )
    return getattr(result, "rowcount", 0) == 1


def _ensure_cancelled_assistant_message(
    db: Session,
    tenant_id: str,
    chat_session: ChatSession,
    user_message_id: str,
    client_turn_id: str,
    created_at,
) -> bool:
    user_message = db.get(Message, user_message_id)
    if not user_message or user_message.tenant_id != tenant_id or user_message.session_id != chat_session.id:
        return False
    if user_message.role != "user":
        return False

    turn_ids = {user_message_id}
    if client_turn_id:
        turn_ids.add(client_turn_id)
    messages = db.exec(
        select(Message)
        .where(Message.tenant_id == tenant_id, Message.session_id == chat_session.id, Message.role == "assistant")
        .order_by(Message.created_at)
    ).all()
    for message_row in messages:
        metadata = message_row.metadata_json or {}
        row_turn_ids = {
            str(metadata.get("turn_id") or "").strip(),
            str(metadata.get("user_message_id") or "").strip(),
            str(metadata.get("client_turn_id") or "").strip(),
        }
        if turn_ids & row_turn_ids:
            return False

    assistant_message = Message(
        tenant_id=tenant_id,
        session_id=chat_session.id,
        role="assistant",
        content=CANCELLED_ASSISTANT_REPLY,
        metadata_json={
            "turn_id": user_message_id,
            "user_message_id": user_message_id,
            "client_turn_id": client_turn_id or None,
            "status": "cancelled",
        },
        created_at=created_at,
    )
    db.add(assistant_message)
    stage_channel_delivery(db, chat_session, assistant_message)
    db.add(
        AgentEvent(
            tenant_id=tenant_id,
            session_id=chat_session.id,
            event_type="assistant_message_created",
            payload_json={
                "message_id": assistant_message.id,
                "assistant_message_id": assistant_message.id,
                "user_message_id": user_message_id,
                "turn_id": user_message_id,
                "client_turn_id": client_turn_id or None,
                "reply": CANCELLED_ASSISTANT_REPLY,
                "status": "cancelled",
            },
            created_at=created_at,
        )
    )
    chat_session.summary = f"最近回复：{CANCELLED_ASSISTANT_REPLY}"
    chat_session.updated_at = created_at
    db.add(chat_session)
    return True


def _persist_chat_turn_interrupted(
    db: Session,
    tenant_id: str,
    chat_session: ChatSession,
    requested_turn_id: str,
    reason: str,
    error_details: dict[str, object] | None = None,
) -> bool:
    message_id, client_turn_id = _resolve_turn_ids_from_events(db, tenant_id, chat_session.id, requested_turn_id)
    if not message_id:
        message_id = requested_turn_id.strip()
    if not message_id:
        return False

    if _turn_has_terminal_event(db, tenant_id, chat_session.id, message_id, client_turn_id):
        return False

    now = utc_now()
    payload = {
        "turn_id": message_id,
        "user_message_id": message_id,
        "client_turn_id": client_turn_id or None,
        "phase": "interrupted",
        "text": "响应生成中断",
        "reason": reason[:2000],
    }
    if error_details:
        payload.update(error_details)
    db.add(
        AgentEvent(
            tenant_id=tenant_id,
            session_id=chat_session.id,
            event_type="stream_interrupted",
            payload_json=payload,
            created_at=now,
        )
    )
    _ensure_interrupted_assistant_message(
        db,
        tenant_id,
        chat_session,
        message_id,
        client_turn_id,
        now + timedelta(microseconds=1),
    )
    chat_session.status = "active"
    chat_session.updated_at = now
    db.add(chat_session)
    return True


def _resolve_turn_ids_from_events(
    db: Session,
    tenant_id: str,
    session_id: str,
    requested_turn_id: str,
) -> tuple[str, str]:
    requested_turn_id = requested_turn_id.strip()
    if not requested_turn_id:
        return "", ""
    events = db.exec(
        select(AgentEvent)
        .where(AgentEvent.tenant_id == tenant_id, AgentEvent.session_id == session_id)
        .order_by(AgentEvent.created_at)
    ).all()
    for event in reversed(events):
        if event.event_type != "user_message_received":
            continue
        payload = event.payload_json or {}
        candidate_message_id = str(payload.get("message_id") or payload.get("user_message_id") or "").strip()
        candidate_client_turn_id = str(payload.get("client_turn_id") or "").strip()
        if requested_turn_id in {candidate_message_id, candidate_client_turn_id}:
            return candidate_message_id, candidate_client_turn_id
    return requested_turn_id, requested_turn_id


def _turn_has_terminal_event(
    db: Session,
    tenant_id: str,
    session_id: str,
    message_id: str,
    client_turn_id: str = "",
) -> bool:
    turn_ids = {message_id}
    if client_turn_id:
        turn_ids.add(client_turn_id)
    events = db.exec(
        select(AgentEvent)
        .where(AgentEvent.tenant_id == tenant_id, AgentEvent.session_id == session_id)
        .order_by(AgentEvent.created_at)
    ).all()
    for event in events:
        if event.event_type not in {
            "assistant_message_created",
            "complete",
            "error_occurred",
            "stream_cancelled",
            "stream_interrupted",
        }:
            continue
        payload = event.payload_json or {}
        event_turn_ids = {
            str(payload.get("turn_id") or "").strip(),
            str(payload.get("user_message_id") or "").strip(),
            str(payload.get("message_id") or "").strip(),
            str(payload.get("client_turn_id") or "").strip(),
        }
        if turn_ids & event_turn_ids:
            return True
    return False


def _ensure_interrupted_assistant_message(
    db: Session,
    tenant_id: str,
    chat_session: ChatSession,
    user_message_id: str,
    client_turn_id: str,
    created_at,
) -> bool:
    user_message = db.get(Message, user_message_id)
    if not user_message or user_message.tenant_id != tenant_id or user_message.session_id != chat_session.id:
        return False
    if user_message.role != "user":
        return False

    turn_ids = {user_message_id}
    if client_turn_id:
        turn_ids.add(client_turn_id)
    messages = db.exec(
        select(Message)
        .where(Message.tenant_id == tenant_id, Message.session_id == chat_session.id, Message.role == "assistant")
        .order_by(Message.created_at)
    ).all()
    for message_row in messages:
        metadata = message_row.metadata_json or {}
        row_turn_ids = {
            str(metadata.get("turn_id") or "").strip(),
            str(metadata.get("user_message_id") or "").strip(),
            str(metadata.get("client_turn_id") or "").strip(),
        }
        if turn_ids & row_turn_ids:
            return False

    assistant_message = Message(
        tenant_id=tenant_id,
        session_id=chat_session.id,
        role="assistant",
        content=INTERRUPTED_ASSISTANT_REPLY,
        metadata_json={
            "turn_id": user_message_id,
            "user_message_id": user_message_id,
            "client_turn_id": client_turn_id or None,
            "status": "interrupted",
        },
        created_at=created_at,
    )
    db.add(assistant_message)
    stage_channel_delivery(db, chat_session, assistant_message)
    db.add(
        AgentEvent(
            tenant_id=tenant_id,
            session_id=chat_session.id,
            event_type="assistant_message_created",
            payload_json={
                "message_id": assistant_message.id,
                "assistant_message_id": assistant_message.id,
                "user_message_id": user_message_id,
                "turn_id": user_message_id,
                "client_turn_id": client_turn_id or None,
                "reply": INTERRUPTED_ASSISTANT_REPLY,
                "status": "interrupted",
            },
            created_at=created_at,
        )
    )
    chat_session.summary = f"最近回复：{INTERRUPTED_ASSISTANT_REPLY}"
    chat_session.updated_at = created_at
    db.add(chat_session)
    return True


def _persist_relay_only_event(
    db: Session,
    tenant_id: str,
    session_id: str,
    event_type: str,
    payload: dict[str, object],
) -> None:
    db.add(
        AgentEvent(
            tenant_id=tenant_id,
            session_id=session_id,
            event_type=event_type,
            payload_json=payload,
        )
    )
    db.commit()


def _relay_event_payload(row: AgentEvent) -> tuple[str, dict[str, object]]:
    payload = dict(row.payload_json or {})
    event_name = STREAM_RELAY_EVENT_ALIASES.get(row.event_type, row.event_type)
    data: dict[str, object] = {
        "kind": event_name,
        "sessionId": row.session_id,
        "timestamp": row.created_at.isoformat(),
        "provider": "skill",
        **payload,
    }
    return event_name, data


def _events_after_cursor(
    db: Session,
    tenant_id: str,
    session_id: str,
    cursor: tuple[object, str] | None,
) -> list[AgentEvent]:
    statement = select(AgentEvent).where(
        AgentEvent.tenant_id == tenant_id,
        AgentEvent.session_id == session_id,
        AgentEvent.event_type.notin_(SPAN_EVENT_TYPES),
    )
    if cursor:
        last_created_at, last_id = cursor
        statement = statement.where(
            or_(
                AgentEvent.created_at > last_created_at,
                (AgentEvent.created_at == last_created_at) & (AgentEvent.id > last_id),
            )
        )
    return db.exec(statement.order_by(AgentEvent.created_at, AgentEvent.id).limit(200)).all()


def _latest_event_cursor(db: Session, tenant_id: str, session_id: str) -> tuple[object, str] | None:
    row = db.exec(
        select(AgentEvent)
        .where(AgentEvent.tenant_id == tenant_id, AgentEvent.session_id == session_id)
        .order_by(AgentEvent.created_at.desc(), AgentEvent.id.desc())
        .limit(1)
    ).first()
    if not row:
        return None
    return row.created_at, row.id


def _sse(event: object, data: object, event_id: str | None = None) -> str:
    payload = json.dumps(data, ensure_ascii=False)
    id_line = f"id: {event_id}\n" if event_id else ""
    return f"{id_line}event: {event}\ndata: {payload}\n\n"


@router.post("/sessions", response_model=ChatSessionRead)
def create_chat_session(
    request: ChatSessionCreateRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> ChatSessionRead:
    _ensure_request_tenant(request.tenant_id, current_user)
    ensure_tenant(db, request.tenant_id)
    _ensure_chat_agent_available(db, request.tenant_id, request.agent_id, current_user)
    title = _normalize_title(request.title)
    row = ChatSession(
        id=new_id("session"),
        tenant_id=request.tenant_id,
        user_id=current_user.id,
        agent_id=request.agent_id,
        title=title,
    )
    from staffdeck_harness.runtime.session_binding import bind_session
    bind_session(db, row, created=True)
    db.add(row)
    db.commit()
    db.refresh(row)
    return session_read(row)


@router.get("/sessions", response_model=list[ChatSessionRead])
def list_chat_sessions(
    tenant_id: str = Query(...),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> list[ChatSessionRead]:
    _ensure_request_tenant(tenant_id, current_user)
    ensure_tenant(db, tenant_id)
    rows = list(
        db.exec(
            select(ChatSession)
            .where(
                ChatSession.tenant_id == tenant_id,
                or_(
                    ChatSession.channel.is_(None),
                    ChatSession.channel != "skill_test",
                ),
                or_(
                    and_(
                        ChatSession.user_id == current_user.id,
                        ChatSession.team_id.is_(None),
                    ),
                    and_(
                        ChatSession.team_id.is_not(None),
                        ChatSession.title.like("%TL 对话%"),
                    ),
                ),
            )
            .order_by(ChatSession.updated_at.desc())
        )
        .all()
    )
    if not is_admin_user(current_user):
        team_ids = {row.team_id for row in rows if row.team_id}
        owned_team_ids = (
            {
                team.id
                for team in db.exec(
                    select(Team).where(
                        Team.id.in_(team_ids),
                        Team.owner_user_id == current_user.id,
                    )
                ).all()
            }
            if team_ids
            else set()
        )
        rows = [
            row
            for row in rows
            if not row.team_id or row.user_id == current_user.id or row.team_id in owned_team_ids
        ]
    hidden_session_ids = pilotdeck_origin_session_ids(
        db,
        tenant_id,
        (row.id for row in rows),
    )
    rows = [row for row in rows if row.id not in hidden_session_ids]
    _cleanup_stale_completed_sessions(db, tenant_id, rows)
    scheduled_session_ids = {
        session_id
        for session_id in db.exec(
            select(ScheduledTaskRun.session_id).where(
                ScheduledTaskRun.tenant_id == tenant_id,
                ScheduledTaskRun.user_id == current_user.id,
                ScheduledTaskRun.session_id.is_not(None),
            )
        ).all()
        if session_id
    }
    team_ids = {row.team_id for row in rows if row.team_id}
    team_names = {
        team.id: team.name
        for team in db.exec(select(Team).where(Team.id.in_(team_ids))).all()
    } if team_ids else {}
    from staffdeck_harness.runtime.staff_directory import staff_names
    agent_names = staff_names(db, tenant_id, [row.agent_id for row in rows if row.agent_id])
    return [
        session_read(
            row,
            is_scheduled=row.id in scheduled_session_ids,
            team_name=team_names.get(row.team_id) if row.team_id else None,
            agent_name=agent_names.get(row.agent_id or ""),
        )
        for row in rows
    ]


@router.get("/sessions/{session_id}", response_model=ChatSessionRead)
def get_chat_session(
    session_id: str,
    tenant_id: str = Query(...),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> ChatSessionRead:
    """Read one conversation without adding team sessions to the global list."""
    _ensure_request_tenant(tenant_id, current_user)
    row = _get_readable_chat_session(db, tenant_id, current_user, session_id)
    team = db.get(Team, row.team_id) if row.team_id else None
    from staffdeck_harness.runtime.staff_directory import staff_names
    names = staff_names(db, tenant_id, [row.agent_id] if row.agent_id else [])
    available = None
    if row.agent_id:
        try:
            _ensure_chat_agent_available(db, tenant_id, row.agent_id, current_user)
            available = True
        except HTTPException as exc:
            # Reading one's history does not grant execution rights. Outages remain unknown.
            if isinstance(exc.detail, dict) and exc.detail.get("code") == "AGENT_UNAVAILABLE":
                available = False
    return session_read(
        row,
        team_name=team.name if team else None,
        agent_name=names.get(row.agent_id or ""),
        agent_available=available,
    )


@router.put("/sessions/{session_id}", response_model=ChatSessionRead)
def rename_chat_session(
    session_id: str,
    request: ChatSessionUpdateRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> ChatSessionRead:
    _ensure_request_tenant(request.tenant_id, current_user)
    row = _get_user_chat_session(db, request.tenant_id, current_user.id, session_id)
    row.title = _normalize_title(request.title)
    row.updated_at = utc_now()
    db.add(row)
    db.commit()
    db.refresh(row)
    is_scheduled = db.exec(
        select(ScheduledTaskRun.id).where(
            ScheduledTaskRun.tenant_id == request.tenant_id,
            ScheduledTaskRun.user_id == current_user.id,
            ScheduledTaskRun.session_id == row.id,
        )
    ).first() is not None
    team = db.get(Team, row.team_id) if row.team_id else None
    return session_read(row, is_scheduled=is_scheduled, team_name=team.name if team else None)


@router.delete("/sessions/{session_id}")
def delete_chat_session(
    session_id: str,
    tenant_id: str = Query(...),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> dict[str, str]:
    _ensure_request_tenant(tenant_id, current_user)
    row = _get_user_chat_session(db, tenant_id, current_user.id, session_id)
    purge_chat_session_records(db, row)
    db.commit()
    remove_chat_session_workspace(tenant_id=tenant_id, session_id=session_id, db=db)
    return {"status": "deleted"}


@router.get("/sessions/{session_id}/messages", response_model=list[MessageRead])
def list_chat_messages(
    session_id: str,
    tenant_id: str = Query(...),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> list[MessageRead]:
    _ensure_request_tenant(tenant_id, current_user)
    chat_session = _get_readable_chat_session(db, tenant_id, current_user, session_id)
    _cleanup_stale_completed_sessions(db, tenant_id, [chat_session])
    rows = db.exec(
        select(Message)
        .where(Message.tenant_id == tenant_id, Message.session_id == session_id)
        .order_by(Message.created_at)
    ).all()
    events = db.exec(
        select(AgentEvent)
        .where(
            AgentEvent.tenant_id == tenant_id,
            AgentEvent.session_id == session_id,
            AgentEvent.event_type.in_(["user_message_received", "assistant_message_created"]),  # type: ignore[attr-defined]
        )
        .order_by(AgentEvent.created_at)
    ).all()
    turn_ids_by_message = _message_turn_ids_from_events(events)
    rows = visible_message_rows(rows)
    feedback_by_message = _feedback_by_message(db, tenant_id, current_user.id, [row.id for row in rows])
    return [
        message_read(
            row,
            feedback_by_message.get(row.id),
            turn_ids_by_message.get(row.id),
            db,
            content_override=visible_message_content(row),
        )
        for row in rows
    ]


@router.get("/sessions/{session_id}/artifacts/{task_frame_id}")
def download_harness_artifact(
    session_id: str,
    task_frame_id: str,
    tenant_id: str = Query(...),
    path: str = Query(..., min_length=1),
    # A plain default (not Query(...)) so the endpoint stays directly callable in
    # tests: calling it with `Query(...)` would pass a FieldInfo, not a string.
    owner_kind: str = ARTIFACT_OWNER_DEFAULT_KIND,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> StreamingResponse:
    """Download a file explicitly published by an artifact owner.

    The path segment stays ``task_frame_id`` for URL compatibility; ``owner_kind``
    selects the resolver, and defaults to a Harness TaskFrame.
    """

    _ensure_request_tenant(tenant_id, current_user)
    _get_readable_chat_session(db, tenant_id, current_user, session_id)
    resolved = resolve_artifact_owner(
        db,
        owner_kind=owner_kind,
        tenant_id=tenant_id,
        session_id=session_id,
        owner_id=task_frame_id,
        path=path,
    )
    if resolved is None:
        raise HTTPException(status_code=404, detail="Artifact not found")
    artifact = resolved.artifact

    opened = None
    try:
        opened = open_harness_artifact(resolved.workspace_root, path)
        digest = opened.sha256()
        expected_digest = str(artifact.get("sha256") or "").strip().lower()
        expected_size = artifact.get("size")
        if (
            (expected_digest and expected_digest != digest.lower())
            or (isinstance(expected_size, int) and expected_size != opened.size)
        ):
            opened.close()
            raise HTTPException(status_code=409, detail="Artifact has changed")
    except (HarnessArtifactAccessError, OSError):
        if opened is not None:
            opened.close()
        raise HTTPException(status_code=404, detail="Artifact not found") from None

    filename = _safe_artifact_download_name(
        str(artifact.get("display_name") or opened.filename)
    )
    fallback_filename = re.sub(r"[^A-Za-z0-9._-]+", "_", filename).strip("._")
    fallback_filename = (fallback_filename or "artifact")[:120]
    media_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"
    return StreamingResponse(
        opened.iter_bytes(),
        media_type=media_type,
        headers={
            "Cache-Control": "private, no-store",
            "Content-Disposition": (
                f'attachment; filename="{fallback_filename}"; '
                f"filename*=UTF-8''{quote(filename, safe='')}"
            ),
            "Content-Length": str(opened.size),
            "ETag": f'"sha256:{digest}"',
            "X-Content-Type-Options": "nosniff",
        },
        background=BackgroundTask(opened.close),
    )


class ShareLinkRequest(BaseModel):
    tenant_id: str
    session_id: str
    task_frame_id: str
    path: str
    owner_kind: str = Field(
        default=ARTIFACT_OWNER_DEFAULT_KIND,
        pattern=artifact_share_mod.OWNER_KIND_PATTERN,
    )
    ttl_seconds: int | None = None
    days: int | None = None
    title: str | None = None


class ShareLinkResponse(BaseModel):
    token: str
    url: str
    expires_at: int
    link_id: str | None = None
    title: str | None = None


class ArtifactShareLinkRead(BaseModel):
    id: str
    tenant_id: str
    session_id: str
    owner_kind: str
    owner_id: str
    path: str
    title: str | None = None
    token: str
    url: str
    created_by_user_id: str | None = None
    created_by_username: str | None = None
    ttl_seconds: int
    expires_at: datetime
    is_revoked: bool
    revoked_at: datetime | None = None
    revoked_by_user_id: str | None = None
    view_count: int
    last_viewed_at: datetime | None = None
    created_at: datetime
    updated_at: datetime
    status: str
    remaining_seconds: int
    bound_task_id: str | None = None
    bound_task_title: str | None = None


class ArtifactShareLinksListResponse(BaseModel):
    items: list[ArtifactShareLinkRead]
    total: int
    active_count: int
    expiring_soon_count: int
    expired_count: int
    revoked_count: int
    total_views: int


DEMO_SHARE_TITLE_PREFIX = "【演示数据】"


class ExtendShareLinkRequest(BaseModel):
    days: int | None = 7
    ttl_seconds: int | None = None


class UpdateShareLinkRequest(BaseModel):
    title: str | None = None
    expires_at: datetime | None = None


class ScheduledPushConfig(BaseModel):
    enabled: bool = False
    schedule_type: str = "daily"
    time: str = "09:00"
    times: list[str] = Field(default_factory=list)
    interval_minutes: int | None = 60
    feishu_webhook: str | None = None
    feishu_chat_id: str | None = None
    task_title: str | None = None
    agent_id: str | None = None


class BindScheduledTaskRequest(BaseModel):
    tenant_id: str | None = None
    task_id: str | None = None
    mode: str | None = None
    existing_task_id: str | None = None
    create_new_task: bool = False
    task_title: str | None = None
    schedule_type: str = "daily"
    time: str = "09:00"
    times: list[str] = Field(default_factory=list)
    interval_minutes: int | None = 60
    feishu_webhook: str | None = None
    feishu_chat_id: str | None = None
    agent_id: str | None = None


class BindScheduledTaskResponse(BaseModel):
    ok: bool = True
    task_id: str
    task_title: str
    schedule_type: str
    url: str


class SemanticGenerateShareLinkRequest(BaseModel):
    prompt: str = Field(..., description="语义需求描述，如：生成近7天各门店销售与运营分析报表")
    tenant_id: str | None = None
    title: str | None = Field(default=None, description="自定义标题（可选）")
    renderer: str | None = Field(default="sales_report", description="报表模板风格：sales_report 或 generic_table")
    days: int | None = Field(default=7, description="有效天数，默认 7 天")
    session_id: str | None = Field(default=None, description="所属会话ID（可选）")
    data_template_id: str | None = Field(
        default=None,
        description="真实数据查询模板 ID；提供时报表只使用查询结果，未提供则标记为演示数据",
    )
    schedule_push: ScheduledPushConfig | None = None



def _render_share_link_notice_page(
    title: str,
    message: str,
    status_code: int = 410,
) -> HTMLResponse:
    content = f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{title} - StaffDeck</title>
  <style>
    :root {{
      --bg: #0b0f19;
      --card-bg: rgba(23, 31, 48, 0.85);
      --border: rgba(255, 255, 255, 0.1);
      --text: #f8fafc;
      --text-muted: #94a3b8;
    }}
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
      background: var(--bg);
      background-image: radial-gradient(circle at 50% 25%, rgba(59, 130, 246, 0.15) 0%, transparent 60%);
      color: var(--text);
      min-height: 100vh;
      display: flex;
      align-items: center;
      justify-content: center;
      padding: 24px;
    }}
    .card {{
      background: var(--card-bg);
      backdrop-filter: blur(16px);
      -webkit-backdrop-filter: blur(16px);
      border: 1px solid var(--border);
      border-radius: 16px;
      padding: 44px 32px;
      max-width: 480px;
      width: 100%;
      text-align: center;
      box-shadow: 0 25px 50px -12px rgba(0, 0, 0, 0.5);
    }}
    .icon-wrap {{
      width: 68px;
      height: 68px;
      border-radius: 50%;
      background: rgba(239, 68, 68, 0.12);
      border: 1px solid rgba(239, 68, 68, 0.25);
      display: inline-flex;
      align-items: center;
      justify-content: center;
      margin-bottom: 24px;
    }}
    .icon {{
      width: 34px;
      height: 34px;
      color: #f87171;
    }}
    h1 {{
      font-size: 20px;
      font-weight: 600;
      margin-bottom: 12px;
    }}
    p {{
      color: var(--text-muted);
      font-size: 14px;
      line-height: 1.6;
      margin-bottom: 24px;
    }}
    .badge {{
      display: inline-block;
      font-size: 12px;
      padding: 4px 12px;
      border-radius: 20px;
      background: rgba(255, 255, 255, 0.06);
      color: var(--text-muted);
      border: 1px solid var(--border);
    }}
  </style>
</head>
<body>
  <div class="card">
    <div class="icon-wrap">
      <svg class="icon" fill="none" viewBox="0 0 24 24" stroke="currentColor">
        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z"/>
      </svg>
    </div>
    <h1>{title}</h1>
    <p>{message}</p>
    <span class="badge">StaffDeck 报告安全访问控制</span>
  </div>
</body>
</html>"""
    return HTMLResponse(content=content, status_code=status_code)


def _ensure_share_link_access(link: ArtifactShareLink, current_user: User) -> None:
    """管理类动作（撤回/延期/删除）只允许管理员或链接创建者。"""

    if is_admin_user(current_user):
        return
    if link.created_by_user_id != current_user.id:
        raise HTTPException(status_code=403, detail="无权管理该分享链接")


def _to_share_link_read(
    link: ArtifactShareLink,
    now: datetime,
    bound_task_id: str | None = None,
    bound_task_title: str | None = None,
    *,
    include_token: bool = True,
) -> ArtifactShareLinkRead:
    remaining = max(0, int((link.expires_at - now).total_seconds()))
    if link.is_revoked:
        status = "revoked"
    elif remaining == 0:
        status = "expired"
    elif remaining <= 86400:
        status = "expiring_soon"
    else:
        status = "active"
    return ArtifactShareLinkRead(
        id=link.id,
        tenant_id=link.tenant_id,
        session_id=link.session_id,
        owner_kind=link.owner_kind,
        owner_id=link.owner_id,
        path=link.path,
        title=link.title,
        token=link.token if include_token else "",
        url=link.url,
        created_by_user_id=link.created_by_user_id,
        created_by_username=link.created_by_username,
        ttl_seconds=link.ttl_seconds,
        expires_at=link.expires_at,
        is_revoked=link.is_revoked,
        revoked_at=link.revoked_at,
        revoked_by_user_id=link.revoked_by_user_id,
        view_count=link.view_count,
        last_viewed_at=link.last_viewed_at,
        created_at=link.created_at,
        updated_at=link.updated_at,
        status=status,
        remaining_seconds=remaining,
        bound_task_id=bound_task_id,
        bound_task_title=bound_task_title,
    )


# CSP for inline HTML artifacts: sandboxed into an opaque origin (no
# allow-same-origin), so a script inside the artifact can never reach the parent
# app's localStorage user token. https:/data:/blob: sources support CDN-backed
# charts/slides/tailwind. CSP3 ignores `frame-ancestors` when `sandbox` is
# present, so frame control falls back to X-Frame-Options.
_HTML_INLINE_CSP = (
    "sandbox allow-scripts allow-popups allow-forms allow-downloads; "
    "default-src 'none'; "
    "script-src 'unsafe-inline' https: data:; "
    "style-src 'unsafe-inline' https: data:; "
    "img-src https: data: blob:; "
    "media-src https: data: blob:; "
    "font-src https: data:; "
    "connect-src https:; "
    "frame-src https: data: blob:; "
    "object-src 'none'; "
    "base-uri 'none'; "
    "form-action https:"
)


def _is_html_previewable(relative_path: str) -> bool:
    """Only bare .html/.htm files are eligible for inline rendering.

    Deliberately not `mimetypes.guess_type` (which would inline-execute .svg as
    image/svg+xml and .xml) and not the manifest `content_type` (untrusted
    source). Everything else falls back to attachment.
    """
    return relative_path.lower().endswith((".html", ".htm"))


def _shared_artifact_headers(digest: str, size: int) -> dict[str, str]:
    return {
        "Cache-Control": "private, no-store",
        "ETag": f'"sha256:{digest}"',
        "X-Content-Type-Options": "nosniff",
        "Cross-Origin-Resource-Policy": "same-origin",
        "Content-Length": str(size),
    }


@router.get("/artifacts/view/{token}", response_model=None)
def view_published_artifact(
    token: str,
    request: Request = None,
    current_user: User | None = Depends(get_current_user_optional),
    db: Session = Depends(get_session),
) -> Response:
    """Render a published Harness artifact inline, or open it anonymously.

    Dual-path auth: a valid signed share token always opens the artifact; when a
    Bearer user is also present, their session visibility is additionally
    enforced (a broken Bearer header is 401, never silently downgraded to
    anonymous). HTML files are served inline under a sandbox CSP; every other
    extension is served as an attachment.
    """
    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    link_record = db.exec(
        select(ArtifactShareLink).where(
            or_(
                ArtifactShareLink.token_hash == token_hash,
                ArtifactShareLink.token == token,
            )
        )
    ).first()

    wants_html = False
    if request is not None and "text/html" in request.headers.get("accept", ""):
        wants_html = True

    if link_record is not None:
        if link_record.is_revoked:
            if wants_html:
                return _render_share_link_notice_page(
                    title="链接已作废",
                    message="抱歉，该报告分享链接已被管理员手动撤销或作废，无法继续访问。",
                    status_code=403,
                )
            raise HTTPException(status_code=403, detail="该分享链接已被撤销作废")
        if link_record.expires_at < utc_now():
            if wants_html:
                return _render_share_link_notice_page(
                    title="链接已过期",
                    message="抱歉，该报告分享链接已超出有效期（默认7天），如需查看请联系分享人重新生成链接。",
                    status_code=410,
                )
            raise HTTPException(status_code=410, detail="该分享链接已过期失效")

    try:
        payload = artifact_share_mod.decode_artifact_share_token(token)
    except HTTPException:
        if wants_html:
            return _render_share_link_notice_page(
                title="链接无效或已过期",
                message="抱歉，未能找到对应的报告，该分享链接可能已过期或已被修改。",
                status_code=404,
            )
        raise

    tenant_id = payload["tenant_id"]
    session_id = payload["session_id"]
    cn_path = payload["path"]

    if current_user is not None:
        # Enterprise tightening: an authenticated user (e.g. someone who opened
        # the link in a logged-in browser) may only view the artifact if they
        # can read the owning session. Always 404, never 403, to avoid leaking
        # existence. Frontend previews navigate the iframe natively (no Bearer),
        # so they take the anonymous path and are unaffected.
        _get_readable_chat_session(db, tenant_id, current_user, session_id)

    resolved = resolve_artifact_owner(
        db,
        owner_kind=payload["owner_kind"],
        tenant_id=tenant_id,
        session_id=session_id,
        owner_id=payload["owner_id"],
        path=cn_path,
    )
    if resolved is None:
        raise HTTPException(status_code=404, detail="Artifact not found")
    artifact = resolved.artifact

    opened = None
    try:
        opened = open_harness_artifact(resolved.workspace_root, cn_path)
        digest = opened.sha256()
        expected_digest = str(artifact.get("sha256") or "").strip().lower()
        expected_size = artifact.get("size")
        if (
            (expected_digest and expected_digest != digest.lower())
            or (isinstance(expected_size, int) and expected_size != opened.size)
        ):
            opened.close()
            raise HTTPException(status_code=409, detail="Artifact has changed")
    except (HarnessArtifactAccessError, OSError):
        if opened is not None:
            opened.close()
        raise HTTPException(status_code=404, detail="Artifact not found") from None

    if link_record is not None:
        link_record.view_count += 1
        link_record.last_viewed_at = utc_now()
        db.add(link_record)
        db.commit()

    filename = _safe_artifact_download_name(
        str(artifact.get("display_name") or opened.filename)
    )
    fallback_filename = re.sub(r"[^A-Za-z0-9._-]+", "_", filename).strip("._")
    fallback_filename = (fallback_filename or "artifact")[:120]

    headers = _shared_artifact_headers(digest, opened.size)
    if _is_html_previewable(cn_path):
        headers["Content-Type"] = "text/html; charset=utf-8"
        headers["Content-Disposition"] = (
            f'inline; filename="{fallback_filename}"; '
            f"filename*=UTF-8''{quote(filename, safe='')}"
        )
        headers["X-Frame-Options"] = "SAMEORIGIN"
        headers["Content-Security-Policy"] = _HTML_INLINE_CSP
    else:
        headers["Content-Type"] = "application/octet-stream"
        headers["Content-Disposition"] = (
            f'attachment; filename="{fallback_filename}"; '
            f"filename*=UTF-8''{quote(filename, safe='')}"
        )
    return StreamingResponse(
        opened.iter_bytes(),
        media_type=headers["Content-Type"],
        headers=headers,
        background=BackgroundTask(opened.close),
    )


@router.post("/artifacts/share-link")
@router.post("/artifacts/share-links")
def mint_artifact_share(
    request: ShareLinkRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> ShareLinkResponse:
    """Create a signed, expiring share link for an already-published artifact.

    Only a user who can read the owning session may mint a link, and only for a
    path that is actually published in the owner's manifest. Records the link in
    the database for lifecycle management.
    """
    _ensure_request_tenant(request.tenant_id, current_user)
    _get_readable_chat_session(db, request.tenant_id, current_user, request.session_id)
    resolved = resolve_artifact_owner(
        db,
        owner_kind=request.owner_kind,
        tenant_id=request.tenant_id,
        session_id=request.session_id,
        owner_id=request.task_frame_id,
        path=request.path,
    )
    if resolved is None:
        raise HTTPException(status_code=404, detail="Artifact not found")

    ttl = (
        (request.days * 86400)
        if request.days is not None
        else (
            request.ttl_seconds
            if request.ttl_seconds is not None
            else artifact_share_mod.ARTIFACT_SHARE_TTL_SECONDS
        )
    )
    token = artifact_share_mod.mint_artifact_share_token(
        tenant_id=request.tenant_id,
        session_id=request.session_id,
        owner_kind=resolved.owner_kind,
        owner_id=resolved.owner_id,
        path=request.path,
        ttl_seconds=ttl,
    )
    payload = artifact_share_mod.decode_artifact_share_token(token)
    url = f"{get_settings().normalized_tool_base_url}/api/chat/artifacts/view/{token}"
    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    exp_dt = datetime.fromtimestamp(payload["exp"], tz=UTC).replace(tzinfo=None)

    display_title = (
        request.title
        or resolved.artifact.get("display_name")
        or Path(request.path).name
    )

    share_record = ArtifactShareLink(
        tenant_id=request.tenant_id,
        session_id=request.session_id,
        owner_kind=resolved.owner_kind,
        owner_id=resolved.owner_id,
        path=request.path,
        title=display_title,
        token=token,
        token_hash=token_hash,
        url=url,
        created_by_user_id=current_user.id,
        created_by_username=current_user.display_name or current_user.username,
        ttl_seconds=ttl,
        expires_at=exp_dt,
        is_revoked=False,
    )
    db.add(share_record)
    db.commit()
    db.refresh(share_record)

    return ShareLinkResponse(
        token=token,
        url=url,
        expires_at=payload["exp"],
        link_id=share_record.id,
        title=share_record.title,
    )


@router.get("/artifacts/share-links")
def list_artifact_share_links(
    tenant_id: str | None = None,
    status: str = "all",
    search: str | None = None,
    skip: int = 0,
    limit: int = 50,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> ArtifactShareLinksListResponse:
    """List and manage generated HTML artifact share links with lifecycle metrics."""
    tid = tenant_id or current_user.tenant_id
    _ensure_request_tenant(tid, current_user)

    stmt = select(ArtifactShareLink).where(ArtifactShareLink.tenant_id == tid)
    if not is_admin_user(current_user):
        # 普通成员只能看到自己创建的链接，避免枚举他人的报告路径与 token
        stmt = stmt.where(ArtifactShareLink.created_by_user_id == current_user.id)
    all_links = db.exec(stmt.order_by(ArtifactShareLink.created_at.desc())).all()

    # Map tasks bound to artifact share links
    task_map: dict[str, tuple[str, str]] = {}
    try:
        tasks = db.exec(select(ScheduledTask).where(ScheduledTask.tenant_id == tid)).all()
        for t in tasks:
            meta = t.metadata_json if isinstance(t.metadata_json, dict) else {}
            lid = meta.get("artifact_share_link_id")
            if lid and str(lid).strip():
                task_map[str(lid).strip()] = (t.id, t.title)
    except Exception:
        pass

    now = utc_now()
    active_count = 0
    expiring_soon_count = 0
    expired_count = 0
    revoked_count = 0
    total_views = 0

    read_items: list[ArtifactShareLinkRead] = []
    for link in all_links:
        bound_id, bound_title = task_map.get(link.id, (None, None))
        read = _to_share_link_read(
            link,
            now,
            bound_task_id=bound_id,
            bound_task_title=bound_title,
            include_token=False,
        )
        total_views += link.view_count
        if read.status == "revoked":
            revoked_count += 1
        elif read.status == "expired":
            expired_count += 1
        elif read.status == "expiring_soon":
            expiring_soon_count += 1
            active_count += 1
        elif read.status == "active":
            active_count += 1

        if status == "active" and read.status not in ("active", "expiring_soon"):
            continue
        if status == "expiring_soon" and read.status != "expiring_soon":
            continue
        if status == "expired" and read.status != "expired":
            continue
        if status == "revoked" and read.status != "revoked":
            continue

        if search:
            q = search.lower().strip()
            title_match = bool(link.title and q in link.title.lower())
            path_match = q in link.path.lower()
            user_match = bool(link.created_by_username and q in link.created_by_username.lower())
            if not (title_match or path_match or user_match):
                continue

        read_items.append(read)

    total = len(read_items)
    paginated = read_items[skip : skip + limit]

    return ArtifactShareLinksListResponse(
        items=paginated,
        total=total,
        active_count=active_count,
        expiring_soon_count=expiring_soon_count,
        expired_count=expired_count,
        revoked_count=revoked_count,
        total_views=total_views,
    )


@router.post("/artifacts/share-links/{link_id}/revoke")
def revoke_artifact_share_link(
    link_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> ArtifactShareLinkRead:
    """Manually revoke a share link so it can no longer be accessed."""
    link = db.get(ArtifactShareLink, link_id)
    if not link:
        raise HTTPException(status_code=404, detail="分享链接不存在")
    _ensure_request_tenant(link.tenant_id, current_user)
    _ensure_share_link_access(link, current_user)

    link.is_revoked = True
    link.revoked_at = utc_now()
    link.revoked_by_user_id = current_user.id
    link.updated_at = utc_now()
    db.add(link)
    db.commit()
    db.refresh(link)
    return _to_share_link_read(link, utc_now())


def _rotate_share_link_token(
    db: Session,
    link: ArtifactShareLink,
    new_expires_at: datetime,
    current_user: User,
    now: datetime,
) -> str:
    total_ttl = max(1, int((new_expires_at - now).total_seconds()))
    new_token = artifact_share_mod.mint_artifact_share_token(
        tenant_id=link.tenant_id,
        session_id=link.session_id,
        owner_kind=link.owner_kind,
        owner_id=link.owner_id,
        path=link.path,
        ttl_seconds=total_ttl,
    )
    new_token_hash = hashlib.sha256(new_token.encode("utf-8")).hexdigest()
    new_url = f"{get_settings().normalized_tool_base_url}/api/chat/artifacts/view/{new_token}"

    old_token = link.token or ""
    if old_token:
        old_token_hash = hashlib.sha256(old_token.encode("utf-8")).hexdigest()
        if old_token_hash != new_token_hash:
            db.add(
                ArtifactShareLink(
                    tenant_id=link.tenant_id,
                    session_id=link.session_id,
                    owner_kind=link.owner_kind,
                    owner_id=link.owner_id,
                    path=link.path,
                    title=link.title,
                    token=old_token,
                    token_hash=old_token_hash,
                    url=link.url,
                    created_by_user_id=link.created_by_user_id,
                    created_by_username=link.created_by_username,
                    ttl_seconds=link.ttl_seconds,
                    expires_at=link.expires_at,
                    is_revoked=True,
                    revoked_at=now,
                    revoked_by_user_id=current_user.id,
                    view_count=link.view_count,
                    last_viewed_at=link.last_viewed_at,
                    created_at=link.created_at,
                    updated_at=now,
                )
            )

    link.token = new_token
    link.token_hash = new_token_hash
    link.url = new_url
    link.ttl_seconds = total_ttl
    link.expires_at = new_expires_at
    link.is_revoked = False
    link.revoked_at = None
    link.revoked_by_user_id = None
    return new_url


def _sync_share_link_bound_tasks(
    db: Session,
    link: ArtifactShareLink,
    *,
    title: str | None,
    url: str,
    now: datetime,
) -> None:
    try:
        tasks = db.exec(
            select(ScheduledTask).where(ScheduledTask.tenant_id == link.tenant_id)
        ).all()
        for t in tasks:
            meta = t.metadata_json if isinstance(t.metadata_json, dict) else {}
            if str(meta.get("artifact_share_link_id") or "").strip() == link.id:
                new_meta = dict(meta)
                if title:
                    new_meta["artifact_title"] = title
                else:
                    new_meta.pop("artifact_title", None)
                new_meta["artifact_share_url"] = url
                t.metadata_json = new_meta
                t.updated_at = now
                db.add(t)
    except Exception as e:
        logger.warning("Failed to sync bound tasks for link %s: %s", link.id, e)


@router.patch("/artifacts/share-links/{link_id}")
def update_artifact_share_link(
    link_id: str,
    req: UpdateShareLinkRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> ArtifactShareLinkRead:
    """Update title and/or expiration time of a share link."""
    link = db.get(ArtifactShareLink, link_id)
    if not link:
        raise HTTPException(status_code=404, detail="分享链接不存在")
    _ensure_request_tenant(link.tenant_id, current_user)
    _ensure_share_link_access(link, current_user)

    now = utc_now()
    has_changes = False
    new_title = link.title

    if req.title is not None:
        trimmed = req.title.strip()
        if len(trimmed) > 200:
            raise HTTPException(status_code=400, detail="报告名称不能超过 200 个字符")
        if (link.title or "").startswith(DEMO_SHARE_TITLE_PREFIX) and not trimmed.startswith(DEMO_SHARE_TITLE_PREFIX):
            raise HTTPException(
                status_code=400,
                detail=f"演示数据链接的名称必须保留{DEMO_SHARE_TITLE_PREFIX}前缀",
            )
        target_title = trimmed if trimmed else None
        if target_title != link.title:
            link.title = target_title
            new_title = target_title
            has_changes = True

    if req.expires_at is not None:
        raw_exp = req.expires_at
        if raw_exp.tzinfo is not None:
            raw_exp = raw_exp.astimezone(UTC).replace(tzinfo=None)

        if link.is_revoked:
            raise HTTPException(
                status_code=400,
                detail="已作废的分享链接不能修改有效期，请重新生成链接",
            )

        if raw_exp <= now + timedelta(seconds=60):
            raise HTTPException(status_code=400, detail="到期时间必须晚于当前时间")

        if abs((raw_exp - link.expires_at).total_seconds()) > 1:
            _rotate_share_link_token(db, link, raw_exp, current_user, now)
            has_changes = True

    if has_changes:
        link.updated_at = now
        db.add(link)
        _sync_share_link_bound_tasks(db, link, title=new_title, url=link.url, now=now)
        db.commit()
        db.refresh(link)

    return _to_share_link_read(link, now)



@router.post("/artifacts/share-links/{link_id}/extend")
def extend_artifact_share_link(
    link_id: str,
    req: ExtendShareLinkRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> ArtifactShareLinkRead:
    """Extend the expiration time of a share link and re-sign the capability token."""
    link = db.get(ArtifactShareLink, link_id)
    if not link:
        raise HTTPException(status_code=404, detail="分享链接不存在")
    _ensure_request_tenant(link.tenant_id, current_user)
    _ensure_share_link_access(link, current_user)

    if link.is_revoked:
        raise HTTPException(
            status_code=400,
            detail="已作废的分享链接不能延期，请重新生成链接",
        )

    now = utc_now()
    add_seconds = (req.days * 86400) if req.days is not None else (req.ttl_seconds or (7 * 86400))
    base_time = link.expires_at if (not link.is_revoked and link.expires_at > now) else now
    new_expires_at = base_time + timedelta(seconds=add_seconds)

    _rotate_share_link_token(db, link, new_expires_at, current_user, now)
    link.updated_at = now
    db.add(link)
    db.commit()
    db.refresh(link)
    return _to_share_link_read(link, now)



@router.delete("/artifacts/share-links/{link_id}")
def delete_artifact_share_link(
    link_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> dict[str, Any]:
    """Delete a share link: revoke the signed token and keep the row for audit.

    Hard-deleting the row would orphan the stateless signed token: the viewer
    only checks revocation/expiry when it can find the row, so a deleted record
    used to keep serving the artifact until the token's own ``exp``. We therefore
    soft-delete (``is_revoked=True``) so ``/artifacts/view/{token}`` rejects it
    immediately while the audit trail survives.
    """
    link = db.get(ArtifactShareLink, link_id)
    if not link:
        raise HTTPException(status_code=404, detail="分享链接不存在")
    _ensure_request_tenant(link.tenant_id, current_user)
    _ensure_share_link_access(link, current_user)

    if not link.is_revoked:
        link.is_revoked = True
        link.revoked_at = utc_now()
        link.revoked_by_user_id = current_user.id
        link.updated_at = utc_now()
        db.add(link)
        db.commit()
    return {"success": True, "deleted_id": link_id, "revoked": True}


@router.post("/artifacts/share-links/{link_id}/bind-scheduled-task", response_model=BindScheduledTaskResponse)
def bind_artifact_share_link_to_scheduled_task(
    link_id: str,
    req: BindScheduledTaskRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> BindScheduledTaskResponse:
    """Bind an HTML artifact share link to a scheduled task for periodic push notifications."""
    link = db.get(ArtifactShareLink, link_id)
    if not link:
        raise HTTPException(status_code=404, detail="分享链接不存在")
    tid = req.tenant_id or current_user.tenant_id
    _ensure_request_tenant(tid, current_user)
    if link.tenant_id != tid:
        raise HTTPException(status_code=403, detail="跨租户访问被拒绝")
    _ensure_share_link_access(link, current_user)
    if link.is_revoked:
        raise HTTPException(status_code=400, detail="分享链接已撤回，无法绑定")
    if link.expires_at and link.expires_at <= utc_now():
        raise HTTPException(status_code=400, detail="分享链接已过期，无法绑定")

    if (link.title or "").startswith(DEMO_SHARE_TITLE_PREFIX):
        raise HTTPException(
            status_code=400,
            detail="演示数据报表不能绑定定时推送，请使用真实数据查询模板生成链接",
        )

    title_clean = (link.title or "业务数据分析报告").strip()
    target_task_id = req.task_id or req.existing_task_id
    is_new = req.create_new_task or (req.mode == "new")

    if not is_new and target_task_id:
        task = db.get(ScheduledTask, target_task_id)
        if not task or task.tenant_id != tid:
            raise HTTPException(status_code=404, detail="指定的定时任务不存在")
        _ensure_task_access(task, current_user)
        meta = dict(task.metadata_json or {})
        meta["artifact_share_link_id"] = link.id
        meta["artifact_title"] = title_clean
        if req.feishu_webhook or req.feishu_chat_id:
            fn = dict(meta.get("feishu_notify") or {})
            fn["enabled"] = True
            if req.feishu_webhook:
                whs = list(fn.get("webhooks") or [])
                wh_clean = req.feishu_webhook.strip()
                if wh_clean and wh_clean not in whs:
                    whs.append(wh_clean)
                fn["webhooks"] = whs
            if req.feishu_chat_id:
                cids = list(fn.get("chat_ids") or [])
                cid_clean = req.feishu_chat_id.strip()
                if cid_clean and cid_clean not in cids:
                    cids.append(cid_clean)
                fn["chat_ids"] = cids
            meta["feishu_notify"] = fn
        task.metadata_json = meta
        task.updated_at = utc_now()
        db.add(task)
        db.commit()
        db.refresh(task)
        return BindScheduledTaskResponse(
            ok=True,
            task_id=task.id,
            task_title=task.title,
            schedule_type=task.schedule_type,
            url=link.url,
        )

    # Create a new scheduled task
    agent_id = req.agent_id
    if not agent_id:
        agent = db.exec(
            select(AgentProfile)
            .where(AgentProfile.tenant_id == tid)
            .order_by(AgentProfile.is_overall.desc(), AgentProfile.created_at.asc())
        ).first()
        agent_id = agent.id if agent else "agent_overall"

    task_title = req.task_title or f"【定时推送】{title_clean}"
    times = [t.strip() for t in req.times if t.strip()]
    if not times:
        times = [req.time.strip() if req.time else "09:00"]

    schedule_cfg: dict[str, Any] = {
        "time": times[0],
        "times": times,
        "interval_minutes": req.interval_minutes or 60,
    }

    fn_cfg: dict[str, Any] = {
        "enabled": bool(req.feishu_webhook or req.feishu_chat_id),
        "webhooks": [req.feishu_webhook.strip()] if req.feishu_webhook and req.feishu_webhook.strip() else [],
        "chat_ids": [req.feishu_chat_id.strip()] if req.feishu_chat_id and req.feishu_chat_id.strip() else [],
        "mobiles": [],
        "open_ids": [],
    }

    metadata = {
        "artifact_share_link_id": link.id,
        "artifact_title": title_clean,
        "feishu_notify": fn_cfg,
    }

    new_task = ScheduledTask(
        tenant_id=tid,
        agent_id=agent_id,
        created_by_user_id=current_user.id,
        title=task_title,
        prompt=(
            f"定时播报并推送 HTML 业务报告。\n\n"
            f"报告标题: {title_clean}\n\n"
            f"服务端会在飞书卡片中附加报告访问按钮；播报正文不要自行拼接链接或 token。"
        ),
        schedule_type=req.schedule_type or "daily",
        schedule_json=schedule_cfg,
        status="active",
        metadata_json=metadata,
    )
    db.add(new_task)
    db.commit()
    db.refresh(new_task)

    return BindScheduledTaskResponse(
        ok=True,
        task_id=new_task.id,
        task_title=new_task.title,
        schedule_type=new_task.schedule_type,
        url=link.url,
    )


@router.get("/artifacts/share-links/recent-reports")
def list_recent_reports_for_sharing(
    tenant_id: str | None = None,
    limit: int = 20,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> list[dict[str, Any]]:
    """List recent HTML reports/artifacts across sessions to easily pick and share."""
    tid = tenant_id or current_user.tenant_id
    _ensure_request_tenant(tid, current_user)

    messages = db.exec(
        select(Message)
        .where(
            Message.tenant_id == tid,
            Message.role == "assistant",
        )
        .order_by(Message.created_at.desc())
        .limit(100)
    ).all()

    seen = set()
    results: list[dict[str, Any]] = []
    readable_session_ids: set[str] = set()

    def _can_read_session(session_id: str) -> bool:
        if is_admin_user(current_user):
            return True
        if session_id in readable_session_ids:
            return True
        try:
            _get_readable_chat_session(db, tid, current_user, session_id)
        except HTTPException:
            return False
        readable_session_ids.add(session_id)
        return True

    for msg in messages:
        if not _can_read_session(msg.session_id):
            continue
        artifacts = (msg.metadata_json or {}).get("harness_artifacts", [])
        for art in artifacts:
            path = str(art.get("path") or "")
            if _is_html_previewable(path):
                key = (msg.session_id, path)
                if key not in seen:
                    seen.add(key)
                    results.append({
                        "session_id": msg.session_id,
                        "task_frame_id": art.get("task_frame_id") or art.get("owner_id"),
                        "owner_kind": art.get("owner_kind") or "harness_frame",
                        "owner_id": art.get("owner_id") or art.get("task_frame_id"),
                        "path": path,
                        "display_name": art.get("display_name") or Path(path).name,
                        "size": art.get("size"),
                        "created_at": msg.created_at.isoformat() if msg.created_at else None,
                    })
                    if len(results) >= limit:
                        return results
    return results


def _synthesize_report_spec_from_prompt(
    prompt: str,
    custom_title: str | None = None,
    preferred_renderer: str | None = None,
) -> dict[str, Any]:
    text = prompt.lower()
    cst_now = datetime.now(UTC) + timedelta(hours=8)
    date_str = cst_now.strftime("%Y-%m-%d")

    is_sales = any(k in text for k in ["销", "sale", "门店", "业绩", "达成", "达播", "净销", "gmv", "电商", "大盘"])
    is_inventory = any(k in text for k in ["库存", "采购", "入库", "出库", "供应链", "周转", "sku", "备货"])
    is_service = any(k in text for k in ["客服", "工单", "客户", "服务", "满意度", "响应", "投诉"])

    if preferred_renderer in ("sales_report", "generic_table"):
        renderer = preferred_renderer
    elif is_sales:
        renderer = "sales_report"
    else:
        renderer = "generic_table"

    if renderer == "sales_report" or is_sales:
        title = custom_title or (
            f"销售业务综合分析与达成看板 ({date_str})"
            if "周" not in text
            else f"近7天销售业绩与渠道大屏 ({date_str})"
        )
        return {
            "title": title,
            "subtitle": f"AI 语义分析生成 · 统计截止至 {date_str}",
            "renderer": "sales_report",
            "summary": (
                f"根据需求指令「{prompt}」完成全维度数据聚合。核心业务指标表现稳健，"
                "线下直营旗舰店与线上达播渠道形成良好协同，重点大区目标达成率超 100%。"
            ),
            "kpis": [
                {"label": "大盘总净销", "value": "412.8万", "delta": "+14.2%", "tone": "up"},
                {"label": "自营净销", "value": "236.5万", "delta": "+9.1%", "tone": "up"},
                {"label": "达播净销", "value": "176.3万", "delta": "+21.8%", "tone": "up"},
                {"label": "综合达成率", "value": "106.5%", "delta": "+6.5%", "tone": "up"},
            ],
            "rows": [
                {"category": "华东大区", "item": "杭州西湖直营店", "净销_万": 118.5, "运营净销_万": 68.2, "达播净销_万": 50.3, "达成率": "112%"},
                {"category": "华东大区", "item": "上海淮海路旗舰店", "净销_万": 96.2, "运营净销_万": 58.0, "达播净销_万": 38.2, "达成率": "106%"},
                {"category": "华东大区", "item": "南京新街口中心店", "净销_万": 78.4, "运营净销_万": 46.1, "达播净销_万": 32.3, "达成率": "102%"},
                {"category": "华南大区", "item": "深圳万象城店", "净销_万": 82.6, "运营净销_万": 49.0, "达播净销_万": 33.6, "达成率": "108%"},
                {"category": "华南大区", "item": "广州天河城店", "净销_万": 69.1, "运营净销_万": 41.5, "达播净销_万": 27.6, "达成率": "98%"},
                {"category": "华北大区", "item": "北京三里屯店", "净销_万": 88.0, "运营净销_万": 52.3, "达播净销_万": 35.7, "达成率": "105%"},
            ],
            "column_labels": {
                "category": "所属大区",
                "item": "门店/渠道",
                "净销_万": "总净销(万)",
                "运营净销_万": "自营净销(万)",
                "达播净销_万": "达播净销(万)",
                "达成率": "目标达成率",
            },
        }
    elif is_inventory:
        title = custom_title or f"供应链库存周转与备货分析看板 ({date_str})"
        return {
            "title": title,
            "subtitle": f"AI 语义分析生成 · 统计截止至 {date_str}",
            "renderer": "generic_table",
            "summary": (
                f"根据指令「{prompt}」生成供应链分析报表。当前全仓周转天数稳定在 18.5 天，"
                "重点爆品现货率 98.2%，部分热销款已触发自动安全补货预警。"
            ),
            "kpis": [
                {"label": "在库总件数", "value": "128,450", "delta": "+5.2%", "tone": "up"},
                {"label": "周转天数", "value": "18.5天", "delta": "-2.1天", "tone": "up"},
                {"label": "现货满足率", "value": "98.2%", "delta": "+1.4%", "tone": "up"},
                {"label": "预警品类数", "value": "3类", "delta": "-2类", "tone": "up"},
            ],
            "rows": [
                {"品类": "核心服饰系列", "主仓库存": "45,200", "华东分仓": "18,400", "华南分仓": "15,800", "周转天数": "16.2", "状态": "正常"},
                {"品类": "当季新品鞋履", "主仓库存": "28,600", "华东分仓": "12,100", "华南分仓": "9,800", "周转天数": "19.5", "状态": "正常"},
                {"品类": "配饰及周边", "主仓库存": "32,150", "华东分仓": "14,200", "华南分仓": "11,500", "周转天数": "22.8", "状态": "关注"},
                {"品类": "联名限定款", "主仓库存": "22,500", "华东分仓": "8,900", "华南分仓": "7,600", "周转天数": "12.0", "状态": "紧俏"},
            ],
            "column_labels": {
                "品类": "商品品类",
                "主仓库存": "中央主仓",
                "华东分仓": "华东仓存",
                "华南分仓": "华南仓存",
                "周转天数": "周转周期(天)",
                "状态": "库存健康度",
            },
        }
    elif is_service:
        title = custom_title or f"客户服务质量与工单时效看板 ({date_str})"
        return {
            "title": title,
            "subtitle": f"AI 语义分析生成 · 统计截止至 {date_str}",
            "renderer": "generic_table",
            "summary": (
                f"根据指令「{prompt}」汇总客户服务分析数据。本期平均工单处理时长缩短至 14.2 分钟，"
                "一次性解决率达到 92.4%，用户满意度综合评分为 4.88 分。"
            ),
            "kpis": [
                {"label": "工单处理量", "value": "12,680件", "delta": "+8.4%", "tone": "up"},
                {"label": "平均响应时长", "value": "45秒", "delta": "-12秒", "tone": "up"},
                {"label": "一次解决率", "value": "92.4%", "delta": "+3.1%", "tone": "up"},
                {"label": "满意度评分", "value": "4.88分", "delta": "+0.05", "tone": "up"},
            ],
            "rows": [
                {"渠道": "在线客服群", "接入总量": "5,420", "平均解决时长": "12.5分钟", "解决率": "94.6%", "满意度": "4.92"},
                {"渠道": "企业飞书服务台", "接入总量": "3,890", "平均解决时长": "15.1分钟", "解决率": "93.2%", "满意度": "4.89"},
                {"渠道": "微信客服", "接入总量": "2,150", "平均解决时长": "16.8分钟", "解决率": "90.1%", "满意度": "4.82"},
                {"渠道": "VIP专属通道", "接入总量": "1,220", "平均解决时长": "8.2分钟", "解决率": "98.5%", "满意度": "4.97"},
            ],
            "column_labels": {
                "渠道": "服务接入渠道",
                "接入总量": "工单总数",
                "平均解决时长": "平均时效",
                "解决率": "解决率",
                "满意度": "满意度(5分制)",
            },
        }
    else:
        title = custom_title or f"综合业务数据分析大屏 ({date_str})"
        return {
            "title": title,
            "subtitle": f"AI 语义分析生成 · 统计截止至 {date_str}",
            "renderer": "generic_table",
            "summary": (
                f"根据需求描述「{prompt}」提炼核心业务数据指标。各大维度业务表现良好，"
                "协同配合紧密，整体运行处于预定轨道范围内。"
            ),
            "kpis": [
                {"label": "核心综合指标", "value": "98.6分", "delta": "+2.4%", "tone": "up"},
                {"label": "业务活跃度", "value": "1,850", "delta": "+15.2%", "tone": "up"},
                {"label": "完成度", "value": "94.8%", "delta": "+4.1%", "tone": "up"},
            ],
            "rows": [
                {"维度": "核心业务板块A", "指标值": "850", "环比": "+12.4%", "状态": "良好"},
                {"维度": "核心业务板块B", "指标值": "620", "环比": "+8.9%", "状态": "平稳"},
                {"维度": "核心业务板块C", "指标值": "380", "环比": "+24.1%", "状态": "高速增长"},
            ],
            "column_labels": {
                "维度": "分析维度",
                "指标值": "业务数值",
                "环比": "环比增长",
                "状态": "运行状态",
            },
        }


@router.post("/artifacts/share-links/semantic-generate")
def semantic_generate_share_link(
    request: SemanticGenerateShareLinkRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> ArtifactShareLinkRead:
    """Generate a full standalone HTML report directly from natural language semantics, and mint a share link."""
    from app.reporting.errors import ReportError
    from app.reporting.spec import SPEC_KEYS, build_report_from_spec
    from app.reporting.writer import write_report_html

    tid = request.tenant_id or current_user.tenant_id
    _ensure_request_tenant(tid, current_user)

    prompt = (request.prompt or "").strip()
    if not prompt:
        raise HTTPException(status_code=400, detail="需求描述不能为空")

    # 1. Resolve or create chat session
    if request.session_id:
        session = db.get(ChatSession, request.session_id)
        if not session or session.tenant_id != tid:
            raise HTTPException(status_code=404, detail="指定的会话不存在")
    else:
        session = ChatSession(
            id=new_id("session"),
            tenant_id=tid,
            user_id=current_user.id,
            title=request.title or f"语义报表：{prompt[:18]}",
        )
        db.add(session)
        db.commit()
        db.refresh(session)

    # 2. Record task frame
    frame_id = new_id("task")
    frame_record = HarnessTaskFrameRecord(
        id=new_id("htask"),
        tenant_id=tid,
        session_id=session.id,
        task_id=frame_id,
        source_turn_id=new_id("turn"),
        status="completed",
    )
    db.add(frame_record)
    db.commit()

    # 3. 取数：有 data_template_id 时只用真实查询结果；否则生成明确标记的“演示数据”。
    demo = not request.data_template_id
    wants_push = bool(request.schedule_push and request.schedule_push.enabled)
    if demo and wants_push:
        raise HTTPException(
            status_code=400,
            detail="演示报表不能配置定时推送，请先提供 data_template_id 绑定真实数据查询模板",
        )

    spec: dict[str, Any] | None = None
    if request.data_template_id:
        from app.data_query.service import execute_query_by_id

        try:
            query_res = execute_query_by_id(db, request.data_template_id, tid, {})
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"数据查询失败：{exc}") from exc
        rows = list(query_res.rows or [])
        columns = list(query_res.columns or [])
        if not rows:
            raise HTTPException(status_code=400, detail="查询结果为空，未生成报表")
        spec = {
            "title": request.title or "数据查询报表",
            "subtitle": f"数据源：{request.data_template_id} · 共 {len(rows)} 行",
            "renderer": "generic_table",
            "summary": f"本报表由数据查询模板 `{request.data_template_id}` 的真实查询结果生成。",
            "rows": rows,
            "column_labels": {str(c): str(c) for c in columns},
        }
    else:
        # 3a. Generate spec (LLM first, smart synthesis fallback) — 仅用于演示模式
        try:
            from app.db.models import ModelConfig
            model_config = db.exec(
                select(ModelConfig).where(
                    ModelConfig.tenant_id == tid,
                    ModelConfig.is_default == True,  # noqa: E712
                )
            ).first()
            if not model_config:
                model_config = db.exec(
                    select(ModelConfig).where(ModelConfig.tenant_id == tid)
                ).first()

            if model_config and getattr(model_config, "api_key_encrypted", None):
                from app.llm import LLMClient
                client = LLMClient(model_config)
                system_prompt = (
                    "你是一个企业级 BI 报表与数据分析专家。根据用户的语义需求和报表偏好，"
                    "生成一份结构化报表 spec JSON 配置。\n"
                    "输出必须是严格合法的 JSON 对象，包含：\n"
                    "- title (string): 报表主标题\n"
                    "- subtitle (string, 可选): 副标题/统计周期\n"
                    "- renderer (string): 'sales_report' 或 'generic_table'\n"
                    "- summary (string): 核心分析与结论摘要 markdown\n"
                    "- kpis (array): 3-5个指标卡，每个含 label, value, delta, tone ('up'|'down'|'flat')\n"
                    "- rows (array): 5-15行明细数据对象\n"
                    "- column_labels (object, 可选): 字段名中文标签映射\n"
                )
                raw_spec = client.generate_json(
                    system_prompt,
                    {"prompt": prompt, "title_hint": request.title, "renderer_hint": request.renderer},
                )
                if isinstance(raw_spec, dict) and raw_spec.get("title") and raw_spec.get("rows"):
                    spec = {k: v for k, v in raw_spec.items() if k in SPEC_KEYS}
        except (OSError, ValueError, RuntimeError, KeyError, TypeError, LLMError):
            spec = None

        if not spec:
            spec = _synthesize_report_spec_from_prompt(
                prompt=prompt,
                custom_title=request.title,
                preferred_renderer=request.renderer,
            )
        spec["title"] = f"【演示数据】{spec.get('title') or request.title or '语义报表'}"
        spec["subtitle"] = "演示数据 · 未连接真实数据源，不可用于业务决策"

    # 4. Build and write HTML
    try:
        doc = build_report_from_spec(spec, tenant_id=tid)
        published = write_report_html(
            db=db,
            tenant_id=tid,
            session_id=session.id,
            owner_kind=ARTIFACT_OWNER_DEFAULT_KIND,
            owner_id=frame_id,
            document=doc,
        )
    except ReportError as exc:
        raise HTTPException(status_code=400, detail=f"报表构建失败: {exc}")
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"报表落盘失败: {exc}")

    # 5. Assistant message record
    assistant_msg = Message(
        id=new_id("msg"),
        tenant_id=tid,
        session_id=session.id,
        role="assistant",
        content=f"已通过语义需求「{prompt}」生成 HTML 独立报表大屏《{published.display_name}》。",
        metadata_json={"harness_artifacts": [published.artifact]},
    )
    db.add(assistant_msg)
    db.commit()

    # 6. Mint capability token & persist ArtifactShareLink
    days = request.days if request.days and request.days > 0 else 7
    ttl = days * 86400
    token = artifact_share_mod.mint_artifact_share_token(
        tenant_id=tid,
        session_id=session.id,
        owner_kind=ARTIFACT_OWNER_DEFAULT_KIND,
        owner_id=frame_id,
        path=published.path,
        ttl_seconds=ttl,
    )
    payload = artifact_share_mod.decode_artifact_share_token(token)
    url = f"{get_settings().normalized_tool_base_url}/api/chat/artifacts/view/{token}"
    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    exp_dt = datetime.fromtimestamp(payload["exp"], tz=UTC).replace(tzinfo=None)

    if demo:
        display_title = f"【演示数据】{request.title or published.display_name or Path(published.path).name}"
    else:
        display_title = (
            request.title
            or spec.get("title")
            or published.display_name
            or Path(published.path).name
        )

    share_record = ArtifactShareLink(
        tenant_id=tid,
        session_id=session.id,
        owner_kind=ARTIFACT_OWNER_DEFAULT_KIND,
        owner_id=frame_id,
        path=published.path,
        title=display_title,
        token=token,
        token_hash=token_hash,
        url=url,
        created_by_user_id=current_user.id,
        created_by_username=current_user.display_name or current_user.username,
        ttl_seconds=ttl,
        expires_at=exp_dt,
        is_revoked=False,
    )
    db.add(share_record)
    db.commit()
    db.refresh(share_record)

    bound_task_id: str | None = None
    bound_task_title: str | None = None
    if request.schedule_push and request.schedule_push.enabled:
        sp = request.schedule_push
        agent_id = sp.agent_id
        if not agent_id:
            agent = db.exec(
                select(AgentProfile)
                .where(AgentProfile.tenant_id == tid)
                .order_by(AgentProfile.is_overall.desc(), AgentProfile.created_at.asc())
            ).first()
            agent_id = agent.id if agent else "agent_overall"

        task_title = sp.task_title or f"【定时推送】{display_title}"
        times = [t.strip() for t in sp.times if t.strip()]
        if not times:
            times = [sp.time.strip() if sp.time else "09:00"]
        schedule_cfg = {
            "time": times[0],
            "times": times,
            "interval_minutes": sp.interval_minutes or 60,
        }
        fn_cfg = {
            "enabled": bool(sp.feishu_webhook or sp.feishu_chat_id),
            "webhooks": [sp.feishu_webhook.strip()] if sp.feishu_webhook and sp.feishu_webhook.strip() else [],
            "chat_ids": [sp.feishu_chat_id.strip()] if sp.feishu_chat_id and sp.feishu_chat_id.strip() else [],
            "mobiles": [],
            "open_ids": [],
        }
        new_task = ScheduledTask(
            tenant_id=tid,
            agent_id=agent_id,
            created_by_user_id=current_user.id,
            title=task_title,
            prompt=(
                f"定时播报并推送 HTML 业务报告。\n\n"
                f"报告标题: {display_title}\n\n"
                f"服务端会在飞书卡片中附加报告访问按钮；播报正文不要自行拼接链接或 token。"
            ),
            schedule_type=sp.schedule_type or "daily",
            schedule_json=schedule_cfg,
            status="active",
            metadata_json={
                "artifact_share_link_id": share_record.id,
                "artifact_title": display_title,
                "feishu_notify": fn_cfg,
            },
        )
        db.add(new_task)
        db.commit()
        db.refresh(new_task)
        bound_task_id = new_task.id
        bound_task_title = new_task.title

    return _to_share_link_read(
        share_record,
        utc_now(),
        bound_task_id=bound_task_id,
        bound_task_title=bound_task_title,
    )



@router.get("/sessions/{session_id}/events")
def list_chat_session_events(
    session_id: str,
    tenant_id: str = Query(...),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> list[dict]:
    _ensure_request_tenant(tenant_id, current_user)
    _get_readable_chat_session(db, tenant_id, current_user, session_id)
    messages = db.exec(
        select(Message).where(
            Message.tenant_id == tenant_id,
            Message.session_id == session_id,
        )
    ).all()
    internal_turn_ids = internal_message_turn_ids(messages)
    rows = db.exec(
        select(AgentEvent)
        .where(
            AgentEvent.tenant_id == tenant_id,
            AgentEvent.session_id == session_id,
        )
        .order_by(AgentEvent.created_at)
        .limit(500)
    ).all()
    if internal_turn_ids:
        rows = [
            row
            for row in rows
            if str(
                (row.payload_json or {}).get("turn_id")
                or (row.payload_json or {}).get("user_message_id")
                or (row.payload_json or {}).get("message_id")
                or ""
            ).strip()
            not in internal_turn_ids
        ]
    return [_normalized_session_event_payload(row) for row in rows]


@router.get("/handoffs", response_model=list[HumanHandoffRead])
def list_human_handoffs(
    tenant_id: str = Query(...),
    status: str = Query("pending"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> list[HumanHandoffRead]:
    _ensure_request_tenant(tenant_id, current_user)
    ensure_tenant(db, tenant_id)
    conditions = [HumanHandoffRequest.tenant_id == tenant_id]
    if status != "all":
        conditions.append(HumanHandoffRequest.status == status)
    if not is_admin_user(current_user):
        if status == "pending":
            conditions.append(
                or_(
                    HumanHandoffRequest.assignee_user_id == current_user.id,
                    HumanHandoffRequest.assignee_user_id.is_(None),
                )
            )
        else:
            conditions.append(
                or_(
                    HumanHandoffRequest.assignee_user_id == current_user.id,
                    HumanHandoffRequest.requester_user_id == current_user.id,
                )
            )
    candidate_session_ids = db.exec(
        select(HumanHandoffRequest.session_id).where(*conditions)
    ).all()
    hidden_session_ids = pilotdeck_origin_session_ids(db, tenant_id, candidate_session_ids)
    stmt = select(HumanHandoffRequest).where(*conditions)
    if hidden_session_ids:
        stmt = stmt.where(HumanHandoffRequest.session_id.notin_(hidden_session_ids))
    rows = db.exec(stmt.order_by(HumanHandoffRequest.updated_at.desc()).limit(200)).all()
    return [human_handoff_read(row) for row in rows]


@router.post("/handoffs/{handoff_id}/reply", response_model=HumanHandoffRead)
def reply_human_handoff(
    handoff_id: str,
    request: HumanHandoffReplyRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> HumanHandoffRead:
    _ensure_request_tenant(request.tenant_id, current_user)
    row = db.get(HumanHandoffRequest, handoff_id)
    if not row or row.tenant_id != request.tenant_id:
        raise HTTPException(status_code=404, detail="Handoff request not found")
    if not is_admin_user(current_user) and row.assignee_user_id not in {None, current_user.id}:
        raise HTTPException(status_code=403, detail="Handoff request not assigned to current user")
    reply = request.reply.strip()
    if not reply:
        raise HTTPException(status_code=400, detail="Reply is required")
    if row.status != "pending":
        raise HTTPException(status_code=409, detail="Handoff request is not pending")
    chat_session = db.get(ChatSession, row.session_id)
    if not chat_session or chat_session.tenant_id != request.tenant_id:
        raise HTTPException(status_code=409, detail="Original handoff session is not available")

    _apply_handoff_reply(
        db, row, reply, answered_by_user_id=current_user.id, source="web"
    )
    return human_handoff_read(row)


class HumanHandoffCreateRequest(BaseModel):
    tenant_id: str
    message_id: str | None = None
    message_ids: list[str] = []
    reasons: list[str] = []
    note: str = ""
    include_feedback: bool = False


@router.post("/sessions/{session_id}/handoffs", response_model=HumanHandoffRead)
def create_human_handoff(
    session_id: str,
    request: HumanHandoffCreateRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> HumanHandoffRead:
    """用户在会话里主动转人工：复用 SOP 节点转人工的建单逻辑，处理人按数字员工负责人解析。"""
    from app.core.human_handoff_service import HumanHandoffService
    from app.observability import EventLog
    from app.session.session_schema import StepAgentResult

    _ensure_request_tenant(request.tenant_id, current_user)
    chat_session = _get_readable_chat_session(db, request.tenant_id, current_user, session_id)
    if chat_session.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Only the session owner can request a handoff")
    service = HumanHandoffService(db, EventLog(db))
    reason_text = "；".join(item for item in request.reasons if item)
    question = "；".join(item for item in (reason_text, request.note.strip()) if item)
    row = service.create(
        request.tenant_id,
        chat_session,
        StepAgentResult(action="handoff", reply=question or None, handoff=True),
        current_step_resolver=lambda: None,
        assignee_resolver=lambda tenant_id, agent_id, fallback: service.assignee_user_id(
            tenant_id, agent_id, fallback, tenant_admin_resolver=service.tenant_admin_user_id
        ),
        context_summary=service.context_summary,
        pending_question=service.pending_question,
    )
    row.metadata_json = {
        **dict(row.metadata_json or {}),
        "source": "user_request",
        "reasons": request.reasons,
        "note": request.note,
        "message_ids": request.message_ids or ([request.message_id] if request.message_id else []),
    }
    db.add(row)
    db.commit()
    db.refresh(row)
    return human_handoff_read(row)


@router.post("/messages/{message_id}/feedback")
def upsert_message_feedback(
    message_id: str,
    request: MessageFeedbackRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> dict:
    _ensure_request_tenant(request.tenant_id, current_user)
    message_row = _get_feedback_target_message(db, request.tenant_id, current_user.id, message_id)
    existing = db.exec(
        select(MessageFeedback).where(
            MessageFeedback.tenant_id == request.tenant_id,
            MessageFeedback.message_id == message_id,
            MessageFeedback.user_id == current_user.id,
        )
    ).first()
    now = utc_now()
    if existing:
        existing.rating = request.rating
        existing.analysis_status = "pending"
        existing.analysis_bucket = None
        existing.analysis_reason = None
        existing.analysis_summary = None
        existing.analysis_confidence = None
        existing.analysis_json = {}
        existing.analyzed_at = None
        existing.updated_at = now
        row = existing
    else:
        row = MessageFeedback(
            tenant_id=request.tenant_id,
            session_id=message_row.session_id,
            message_id=message_row.id,
            user_id=current_user.id,
            rating=request.rating,
            analysis_status="pending",
            analysis_json={},
            created_at=now,
            updated_at=now,
        )
    db.add(row)
    _upsert_skill_feedback_for_message(db, request.tenant_id, current_user.id, message_row, request.rating, now)
    db.add(
        AgentEvent(
            tenant_id=request.tenant_id,
            session_id=message_row.session_id,
            event_type="message_feedback_changed",
            payload_json={"message_id": message_row.id, "rating": request.rating, "user_id": current_user.id},
        )
    )
    db.commit()
    db.refresh(row)
    enqueue_feedback_analysis(row.tenant_id, row.id, row.session_id)
    return {
        "id": row.id,
        "tenant_id": row.tenant_id,
        "session_id": row.session_id,
        "message_id": row.message_id,
        "rating": row.rating,
        "analysis_status": row.analysis_status,
        "updated_at": row.updated_at.isoformat(),
    }


@router.delete("/messages/{message_id}/feedback")
def delete_message_feedback(
    message_id: str,
    tenant_id: str = Query(...),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> dict:
    _ensure_request_tenant(tenant_id, current_user)
    message_row = _get_feedback_target_message(db, tenant_id, current_user.id, message_id)
    existing = db.exec(
        select(MessageFeedback).where(
            MessageFeedback.tenant_id == tenant_id,
            MessageFeedback.message_id == message_id,
            MessageFeedback.user_id == current_user.id,
        )
    ).first()
    if existing:
        db.delete(existing)
        _delete_skill_feedback_for_message(db, tenant_id, current_user.id, message_row)
        db.add(
            AgentEvent(
                tenant_id=tenant_id,
                session_id=message_row.session_id,
                event_type="message_feedback_changed",
                payload_json={"message_id": message_row.id, "rating": None, "user_id": current_user.id},
            )
        )
        db.commit()
    return {"status": "deleted"}


@router.get("/sessions/{session_id}/trace")
def list_chat_session_trace(
    session_id: str,
    tenant_id: str = Query(...),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> list[dict]:
    _ensure_request_tenant(tenant_id, current_user)
    _get_readable_chat_session(db, tenant_id, current_user, session_id)
    messages = db.exec(
        select(Message)
        .where(Message.tenant_id == tenant_id, Message.session_id == session_id)
        .order_by(Message.created_at)
    ).all()
    internal_turn_ids = internal_message_turn_ids(messages)
    messages = visible_message_rows(messages)
    events = db.exec(
        select(AgentEvent)
        .where(AgentEvent.tenant_id == tenant_id, AgentEvent.session_id == session_id)
        .order_by(AgentEvent.created_at)
    ).all()
    if internal_turn_ids:
        events = [
            event
            for event in events
            if str(
                (event.payload_json or {}).get("turn_id")
                or (event.payload_json or {}).get("user_message_id")
                or (event.payload_json or {}).get("message_id")
                or ""
            ).strip()
            not in internal_turn_ids
        ]
    skills = db.exec(select(Skill).where(Skill.tenant_id == tenant_id)).all()
    skill_names = {skill.skill_id: skill.name for skill in skills}
    return _build_turn_traces(messages, events, skill_names)


@router.get("/sessions/{session_id}/spans")
def list_chat_session_spans(
    session_id: str,
    tenant_id: str = Query(...),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> list[dict[str, object]]:
    _ensure_request_tenant(tenant_id, current_user)
    _get_readable_chat_session(db, tenant_id, current_user, session_id)
    rows = db.exec(
        select(AgentEvent)
        .where(
            AgentEvent.tenant_id == tenant_id,
            AgentEvent.session_id == session_id,
            AgentEvent.event_type.in_(SPAN_EVENT_TYPES),
        )
        .order_by(AgentEvent.created_at, AgentEvent.id)
    ).all()
    return [
        {
            "event_id": row.id,
            "event_type": row.event_type,
            "created_at": row.created_at.isoformat(),
            **dict(row.payload_json or {}),
        }
        for row in rows
    ]


def _get_user_chat_session(db: Session, tenant_id: str, user_id: str, session_id: str) -> ChatSession:
    ensure_tenant(db, tenant_id)
    row = db.get(ChatSession, session_id)
    if not row or row.tenant_id != tenant_id or row.user_id != user_id:
        raise HTTPException(status_code=404, detail="Session not found")
    return row


def _get_readable_chat_session(db: Session, tenant_id: str, current_user: User, session_id: str) -> ChatSession:
    ensure_tenant(db, tenant_id)
    row = db.get(ChatSession, session_id)
    if not row or row.tenant_id != tenant_id:
        raise HTTPException(status_code=404, detail="Session not found")
    if row.user_id == current_user.id:
        return row
    if row.team_id:
        team = db.get(Team, row.team_id)
        if team and team.tenant_id == tenant_id and (
            current_user.role == "admin" or team.owner_user_id == current_user.id
        ):
            return row
    if _user_can_read_handoff_session(db, tenant_id, current_user, session_id):
        return row
    raise HTTPException(status_code=404, detail="Session not found")


def _safe_artifact_download_name(filename: str) -> str:
    cleaned = "".join(
        character
        for character in filename
        if character not in {"\r", "\n", "\x00"}
        and (character.isprintable() or character == "\t")
    ).strip()
    return cleaned[:180] or "artifact"


def _user_can_read_handoff_session(db: Session, tenant_id: str, current_user: User, session_id: str) -> bool:
    statement = select(HumanHandoffRequest).where(
        HumanHandoffRequest.tenant_id == tenant_id,
        HumanHandoffRequest.session_id == session_id,
    )
    if not is_admin_user(current_user):
        statement = statement.where(
            or_(
                HumanHandoffRequest.assignee_user_id == current_user.id,
                HumanHandoffRequest.assignee_user_id.is_(None),
                HumanHandoffRequest.requester_user_id == current_user.id,
            )
        )
    return db.exec(statement).first() is not None


def _is_chat_agent_unavailable(exc: Exception, agent_id: str | None) -> bool:
    from staffdeck_harness.contracts.errors import ModuleSdkError, PermissionDenied

    if not isinstance(exc, ModuleSdkError):
        return False
    if exc.code == "STAFF_NOT_FOUND":
        return True
    if not isinstance(exc, PermissionDenied):
        return False
    return exc.message == "target Staff unavailable" or (
        exc.details.get("operation") == "staff.use/v1"
        and exc.details.get("resource_type") == "agent"
        and exc.details.get("resource") == agent_id
    )


def _ensure_chat_agent_available(
    db: Session,
    tenant_id: str,
    agent_id: str | None,
    current_user: User,
):
    if not agent_id:
        raise HTTPException(status_code=400, detail="Agent is required")
    ensure_tenant(db, tenant_id)
    from app.config import get_settings
    from staffdeck_harness.composition.sources import authorize_staff
    from staffdeck_harness.contracts.errors import ModuleSdkError, PermissionDenied
    from staffdeck_harness.contracts.sources import SourceContext
    from staffdeck_harness.modules.registry import get_registry
    from staffdeck_harness.security.profile import get_profile
    settings = get_settings()
    try:
        _, _, ref = authorize_staff(get_registry(settings), db,
            SourceContext(tenant_id, agent_id, user_id=current_user.id), get_profile(settings))
    except PermissionDenied as exc:
        if _is_chat_agent_unavailable(exc, agent_id):
            code = 404 if exc.message == "target Staff unavailable" else 403
            raise HTTPException(
                status_code=code,
                detail={"code": "AGENT_UNAVAILABLE", "message": "此数字员工不再可用"},
            ) from exc
        raise HTTPException(status_code=403, detail=exc.to_dict()) from exc
    except ModuleSdkError as exc:
        status = 404 if exc.code == "STAFF_NOT_FOUND" else 503
        detail = (
            {"code": "AGENT_UNAVAILABLE", "message": "此数字员工不再可用"}
            if status == 404 else exc.to_dict()
        )
        raise HTTPException(status_code=status, detail=detail) from exc
    if ref.attributes.get("is_overall"):
        raise HTTPException(
            status_code=404,
            detail={"code": "AGENT_UNAVAILABLE", "message": "此数字员工不再可用"},
        )
    return ref


def _bind_request_to_session_agent(
    db: Session,
    request: ChatTurnRequest,
    chat_session: ChatSession,
    current_user: User,
) -> ChatTurnRequest:
    if chat_session.agent_id:
        if request.agent_id and request.agent_id != chat_session.agent_id:
            raise HTTPException(status_code=409, detail="Session is already bound to another agent")
        _ensure_chat_agent_available(db, request.tenant_id, chat_session.agent_id, current_user)
        return request.model_copy(update={"agent_id": chat_session.agent_id})

    agent = _ensure_chat_agent_available(db, request.tenant_id, request.agent_id, current_user)
    chat_session.agent_id = agent.id
    chat_session.updated_at = utc_now()
    db.add(chat_session)
    db.commit()
    return request.model_copy(update={"agent_id": agent.id})


def _ensure_chat_session_available(db: Session, tenant_id: str, user_id: str, session_id: str) -> ChatSession:
    ensure_tenant(db, tenant_id)
    row = db.get(ChatSession, session_id)
    if not row or row.tenant_id != tenant_id:
        raise HTTPException(status_code=404, detail="Session not found")
    # 团队会话(team_id 非空)对本租户成员开放发言(如 TL 工作台聊天室);
    # 普通会话仍仅创建者可见
    if row.user_id != user_id and not row.team_id:
        raise HTTPException(status_code=404, detail="Session not found")
    return row


def _ensure_team_session_human_writable(chat_session: ChatSession) -> None:
    """团队内部会话(任务执行/竞标/验收)仅可查看,不允许人工 /turn、/stream 写入。

    判据与 _team_tl_session_team 一致:只有「TL 对话」标题的团队会话才对人类开放发言,
    其余团队会话由唤醒机制自主驱动,人工写入会污染任务历史并绕过 Agent 权限校验。
    """
    if chat_session.team_id and "TL 对话" not in (chat_session.title or ""):
        raise HTTPException(status_code=403, detail="Team execution sessions are read-only")


def _team_tl_session_team(db: Session, chat_session: ChatSession) -> Team | None:
    """识别团队 TL 会话:session 挂 team_id 且绑定 agent 是该团队现任 TL。

    非 TL 的团队会话(任务执行/竞标等)不对人直接聊,返回 None(不注入、不后处理)。
    """
    if not chat_session.team_id or not chat_session.agent_id:
        return None
    # 团队会话全量绑定 team_id 后,任务验收/竞标打分等会话同样挂在 TL 名下;
    # 只有「TL 对话」标题的会话才按人对 TL 聊天处理(与 team-threads 列表同判据)
    if "TL 对话" not in (chat_session.title or ""):
        return None
    team = db.get(Team, chat_session.team_id)
    if team is None or team.tenant_id != chat_session.tenant_id or team.status != "active":
        return None
    leader = get_team_leader(db, team.id)
    if leader is None or leader.agent_id != chat_session.agent_id:
        return None
    return team


def _get_feedback_target_message(db: Session, tenant_id: str, user_id: str, message_id: str) -> Message:
    ensure_tenant(db, tenant_id)
    row = db.get(Message, message_id)
    if not row or row.tenant_id != tenant_id or row.role != "assistant":
        raise HTTPException(status_code=404, detail="Message not found")
    chat_session = db.get(ChatSession, row.session_id)
    if not chat_session or chat_session.tenant_id != tenant_id or chat_session.user_id != user_id:
        raise HTTPException(status_code=404, detail="Message not found")
    return row


def _feedback_by_message(
    db: Session,
    tenant_id: str,
    user_id: str,
    message_ids: list[str],
) -> dict[str, str]:
    if not message_ids:
        return {}
    rows = db.exec(
        select(MessageFeedback).where(
            MessageFeedback.tenant_id == tenant_id,
            MessageFeedback.user_id == user_id,
            MessageFeedback.message_id.in_(message_ids),  # type: ignore[attr-defined]
        )
    ).all()
    return {row.message_id: row.rating for row in rows}


def _cleanup_stale_completed_sessions(
    db: Session,
    tenant_id: str,
    rows: list[ChatSession],
) -> None:
    candidates = [row for row in rows if row.active_skill_id]
    if not candidates:
        return
    skills = list(
        db.exec(
            select(Skill).where(Skill.tenant_id == tenant_id, Skill.status == "published")
        ).all()
    )
    if not skills:
        return
    loop = AgentLoop(db)
    changed = False
    for row in candidates:
        before = (
            row.active_skill_id,
            row.active_step_id,
            json.dumps(row.slots_json or {}, sort_keys=True, ensure_ascii=False),
        )
        loop._finish_stale_completed_skill(tenant_id, row, skills)
        after = (
            row.active_skill_id,
            row.active_step_id,
            json.dumps(row.slots_json or {}, sort_keys=True, ensure_ascii=False),
        )
        changed = changed or before != after
    if changed:
        db.commit()
        for row in candidates:
            db.refresh(row)


def _upsert_skill_feedback_for_message(
    db: Session,
    tenant_id: str,
    user_id: str,
    message_row: Message,
    rating: str,
    now,
) -> None:
    skill_context = _active_skill_context_for_assistant_message(db, tenant_id, message_row)
    if not skill_context:
        return
    skill_id = skill_context["skill_id"]
    skill_version = skill_context.get("skill_version")
    step_id = skill_context.get("node_id") or skill_context.get("step_id")
    existing = db.exec(
        select(SkillFeedback).where(
            SkillFeedback.tenant_id == tenant_id,
            SkillFeedback.message_id == message_row.id,
            SkillFeedback.user_id == user_id,
        )
    ).first()
    if existing:
        existing.skill_id = skill_id
        existing.skill_version = skill_version
        existing.step_id = step_id
        existing.rating = rating
        existing.updated_at = now
        db.add(existing)
        return
    db.add(
        SkillFeedback(
            tenant_id=tenant_id,
            skill_id=skill_id,
            skill_version=skill_version,
            step_id=step_id,
            session_id=message_row.session_id,
            message_id=message_row.id,
            user_id=user_id,
            rating=rating,
            created_at=now,
            updated_at=now,
        )
    )


def _delete_skill_feedback_for_message(
    db: Session,
    tenant_id: str,
    user_id: str,
    message_row: Message,
) -> None:
    existing = db.exec(
        select(SkillFeedback).where(
            SkillFeedback.tenant_id == tenant_id,
            SkillFeedback.message_id == message_row.id,
            SkillFeedback.user_id == user_id,
        )
    ).first()
    if existing:
        db.delete(existing)


def _active_skill_for_assistant_message(db: Session, tenant_id: str, message_row: Message) -> str | None:
    context = _active_skill_context_for_assistant_message(db, tenant_id, message_row)
    return context["skill_id"] if context else None


def _active_skill_context_for_assistant_message(
    db: Session, tenant_id: str, message_row: Message
) -> dict[str, str | None] | None:
    messages = db.exec(
        select(Message)
        .where(Message.tenant_id == tenant_id, Message.session_id == message_row.session_id)
        .order_by(Message.created_at)
    ).all()
    target_index = next((index for index, item in enumerate(messages) if item.id == message_row.id), -1)
    if target_index < 0:
        return None
    user_message = next(
        (item for item in reversed(messages[:target_index]) if item.role == "user"),
        None,
    )
    if not user_message:
        return None

    events = db.exec(
        select(AgentEvent)
        .where(AgentEvent.tenant_id == tenant_id, AgentEvent.session_id == message_row.session_id)
        .order_by(AgentEvent.created_at)
    ).all()
    collecting = False
    last_context: dict[str, str | None] | None = None
    skill_hint: str | None = None
    for event in events:
        payload = event.payload_json or {}
        if event.event_type == "user_message_received":
            event_message_id = str(payload.get("message_id") or payload.get("user_message_id") or "").strip()
            collecting = bool(event_message_id and event_message_id == user_message.id)
            last_context = None if collecting else last_context
            skill_hint = None if collecting else skill_hint
            continue
        if not collecting:
            continue
        if event.event_type == "router_decision_created":
            target_skill_id = str(payload.get("target_skill_id") or "").strip()
            if target_skill_id:
                skill_hint = target_skill_id
        event_context = _skill_context_from_event(event, skill_hint=skill_hint)
        if event_context:
            last_context = event_context
            if event_context.get("skill_id"):
                skill_hint = event_context["skill_id"]
        if event.event_type == "assistant_message_created":
            assistant_message_id = str(
                payload.get("message_id") or payload.get("assistant_message_id") or ""
            ).strip()
            if assistant_message_id == message_row.id:
                return _fill_skill_context_version(db, tenant_id, last_context)
            continue
    return _fill_skill_context_version(db, tenant_id, last_context)


def _skill_id_from_event(event: AgentEvent) -> str | None:
    context = _skill_context_from_event(event)
    return context["skill_id"] if context else None


def _skill_context_from_event(event: AgentEvent, skill_hint: str | None = None) -> dict[str, str | None] | None:
    payload = event.payload_json or {}
    if event.event_type in {"skill_started", "skill_resumed", "skill_step_changed"}:
        skill_id = str(payload.get("to_skill_id") or payload.get("from_skill_id") or skill_hint or "") or None
        if not skill_id:
            return None
        skill_version = str(payload.get("to_skill_version") or payload.get("from_skill_version") or "") or None
        node_id = str(
            payload.get("to_node_id")
            or payload.get("from_node_id")
            or payload.get("to_step_id")
            or payload.get("from_step_id")
            or ""
        ) or None
        return {"skill_id": skill_id, "skill_version": skill_version, "node_id": node_id}
    if event.event_type == "skill_completed":
        skill_id = str(payload.get("skill_id") or "") or None
        if not skill_id:
            return None
        return {
            "skill_id": skill_id,
            "skill_version": str(payload.get("skill_version") or "") or None,
            "node_id": str(payload.get("node_id") or payload.get("step_id") or "") or None,
        }
    if event.event_type == "reflection_decision_created":
        skill_id = str(payload.get("target_skill_id") or "") or None
        if not skill_id:
            return None
        return {
            "skill_id": skill_id,
            "skill_version": str(payload.get("target_skill_version") or "") or None,
            "node_id": str(payload.get("target_node_id") or payload.get("target_step_id") or "") or None,
        }
    return None


def _fill_skill_context_version(
    db: Session, tenant_id: str, context: dict[str, str | None] | None
) -> dict[str, str | None] | None:
    if not context or context.get("skill_version"):
        return context
    skill_id = context.get("skill_id")
    if not skill_id:
        return context
    skill = db.exec(select(Skill).where(Skill.tenant_id == tenant_id, Skill.skill_id == skill_id)).first()
    if skill:
        return {**context, "skill_version": skill.version}
    return context


def _trace_payload_text(value: object) -> str:
    if value is None or value == "":
        return ""
    if isinstance(value, str):
        try:
            return json.dumps(json.loads(value), ensure_ascii=False, indent=2)
        except Exception:
            return value
    return json.dumps(value, ensure_ascii=False, indent=2)


def _trace_payload_language(value: str) -> str:
    if not value.strip():
        return "text"
    try:
        json.loads(value)
        return "json"
    except Exception:
        return "text"


def _general_skill_trace_detail(payload: dict, phase: str) -> str | None:
    review = payload.get("review") if isinstance(payload.get("review"), dict) else {}
    if phase.startswith("reflection_"):
        parts = [
            str(review.get("reason") or "").strip(),
            str(review.get("repair_hint") or "").strip(),
        ]
        text = " · ".join(part for part in parts if part)
        return text or None
    detail = str(payload.get("rationale") or payload.get("text") or "").strip()
    if _general_skill_trace_failed(phase):
        error = str(payload.get("error") or payload.get("stderr_preview") or "").strip()
        if error and error not in detail:
            detail = f"{detail} · {error}" if detail else error
    return detail or None


def _general_skill_trace_failed(phase: str) -> bool:
    return "failed" in phase or phase == "code_timeout" or phase.endswith("_error")


def _error_trace_text(payload: dict, *, interrupted: bool = False) -> str:
    code = str(payload.get("code") or "").strip()
    if code == "LLM_ERROR":
        return "模型调用失败"
    if interrupted:
        return "响应生成中断"
    if code:
        return f"执行失败 {code}"
    error_type = str(payload.get("error_type") or "").strip()
    if error_type:
        return f"执行失败 {error_type}"
    return "执行失败"


def _error_trace_detail(payload: dict) -> str | None:
    code = str(payload.get("code") or "").strip()
    error_type = str(payload.get("error_type") or "").strip()
    message = str(payload.get("message") or payload.get("reason") or payload.get("text") or "").strip()
    parts = [code, error_type, message]
    detail = " · ".join(part for part in parts if part)
    return detail[:2000] if detail else None


def _general_skill_trace_output(payload: dict, phase: str) -> dict[str, str]:
    if phase == "stdout_chunk":
        output = _trace_payload_text(payload.get("stdout_preview") or payload.get("text"))
        return {
            "output": output,
            "outputLanguage": _trace_payload_language(output),
            "outputTitle": "查看运行输出",
        } if output else {}
    if phase == "stderr_chunk":
        output = _trace_payload_text(payload.get("stderr_preview") or payload.get("text"))
        return {
            "output": output,
            "outputLanguage": _trace_payload_language(output),
            "outputTitle": "查看错误输出",
        } if output else {}
    if phase in {"code_finished", "code_timeout"}:
        result: dict[str, object] = {}
        if "return_code" in payload:
            result["return_code"] = payload.get("return_code")
        if "structured_result" in payload:
            result["structured_result"] = payload.get("structured_result")
        if str(payload.get("stdout_preview") or "").strip():
            result["stdout"] = payload.get("stdout_preview")
        if str(payload.get("stderr_preview") or "").strip():
            result["stderr"] = payload.get("stderr_preview")
        output = _trace_payload_text(result if result else payload.get("stdout_preview") or payload.get("stderr_preview"))
        return {
            "output": output,
            "outputLanguage": _trace_payload_language(output),
            "outputTitle": "查看超时结果" if phase == "code_timeout" else "查看执行结果",
        } if output else {}
    if phase.startswith("reflection_"):
        result: dict[str, object] = {}
        if "structured_result" in payload:
            result["structured_result"] = payload.get("structured_result")
        if "review" in payload:
            result["review"] = payload.get("review")
        if str(payload.get("stdout_preview") or "").strip():
            result["stdout"] = payload.get("stdout_preview")
        if str(payload.get("stderr_preview") or "").strip():
            result["stderr"] = payload.get("stderr_preview")
        output = _trace_payload_text(result)
        return {
            "output": output,
            "outputLanguage": _trace_payload_language(output),
            "outputTitle": "查看校验详情",
        } if result and output else {}
    return {}


_FRAME_STATUS_TEXTS = {
    "completed": "任务执行完成",
    "awaiting_user": "等待用户补充信息",
    "handoff": "已转人工处理",
    "action_budget": "本轮处理已达上限",
    "failed": "任务执行失败",
    "blocked": "暂时无法继续处理",
    "cancelled": "任务已取消",
}

_SKILL_STATE_LABELS = {
    "suspended": "挂起SOP",
    "pending": "等待SOP",
    "select": "选择SOP",
    "switch": "切换SOP",
    "resume": "恢复SOP",
    "advance": "推进SOP",
}

_SKILL_STATE_LABELS_FRIENDLY = {
    "suspended": "已暂停",
    "pending": "排队处理",
    "select": "进入流程",
    "switch": "切换流程",
    "resume": "恢复流程",
    "advance": "推进流程",
}

_SKILL_EVENT_LABELS = {
    "skill_started": "选择SOP",
    "skill_resumed": "恢复SOP",
    "skill_step_changed": "推进SOP",
}

_SKILL_EVENT_LABELS_FRIENDLY = {
    "skill_started": "进入流程",
    "skill_resumed": "恢复流程",
    "skill_step_changed": "推进流程",
}

_STEP_EXACT_LABELS = {
    "end": "流程结束",
    "start": "开始处理",
    "reply_final_result": "反馈最终结果",
}

# 内置能力与文件/命令工具的中文名（不走 tools 表的保留能力）。
_RESERVED_TOOL_LABELS = {
    "capability_search": "检索可用能力",
    "capability_describe": "查看能力详情",
    "knowledge_search": "检索知识库",
    "exec_command": "执行命令",
    "read_file": "读取文件",
    "extract_document_text": "提取文档内容",
    "write_file": "写入文件",
    "edit_file": "编辑文件",
    "list_directory": "查看目录",
    "glob": "查找文件",
    "grep": "搜索文件内容",
    "file_info": "查看文件信息",
    "publish_artifact": "发布产物",
    "mkdir": "创建目录",
    "delete_file": "删除文件",
    "move_file": "移动文件",
    "copy_file": "复制文件",
}

_SKILL_COMPLETED_REASON_LABELS = {
    "step_completed": "全部步骤已完成",
    "stale_terminal_state": "会话状态已更新，流程自动结束",
}


def _resolve_tool_label(tool_name: str, tool_names: dict[str, str] | None) -> str:
    tool_name = str(tool_name or "").strip()
    if not tool_name:
        return ""
    if tool_names is None:
        return tool_name
    return tool_names.get(tool_name) or _RESERVED_TOOL_LABELS.get(tool_name, tool_name)


def _fallback_step_label(step_id: str) -> str | None:
    normalized = step_id.strip().lower().replace("-", "_")
    if not normalized:
        return None
    exact = _STEP_EXACT_LABELS.get(normalized)
    if exact:
        return exact
    tokens = [token for token in normalized.split("_") if token]

    def has_word(*roots: str) -> bool:
        return any(
            token == root or token.startswith(root)
            for token in tokens
            for root in roots
        )

    if has_word("handoff", "escalate", "manual", "human"):
        return "转人工处理"
    if has_word("final", "last") and has_word("reply", "answer", "result", "feedback"):
        return "反馈最终结果"
    if has_word("collect", "gather"):
        return "收集需要的信息"
    if has_word("confirm", "verify", "validate", "check"):
        return "核对信息"
    if has_word("identify", "recognize", "detect", "intent"):
        return "识别用户需求"
    if has_word("summarize", "summary", "recap"):
        return "总结处理情况"
    if has_word("schedule", "remind", "appointment", "reservation"):
        return "安排提醒"
    if has_word("apolog", "comfort", "soothe"):
        return "安抚用户"
    if has_word("reply", "answer", "respond", "feedback", "notify", "inform", "send", "message"):
        return "反馈处理结果"
    if has_word("end", "finish", "close", "done", "complete"):
        return "流程结束"
    if has_word("start", "begin", "init"):
        return "开始处理"
    return None


def _resolve_step_label(
    step_id: str,
    step_names: dict[str, dict[str, str]] | None,
    skill_id: str | None = None,
) -> str:
    step_id = str(step_id or "").strip()
    if not step_id:
        return ""
    if step_names is None:
        return step_id
    scoped = step_names.get(skill_id or "") or {}
    label = scoped.get(step_id)
    if not label:
        for steps in step_names.values():
            label = steps.get(step_id)
            if label:
                break
    if not label:
        label = _fallback_step_label(step_id)
    return label or step_id


def _harness_event_trace_line(
    event: AgentEvent,
    skill_hint: str | None = None,
    step_names: dict[str, dict[str, str]] | None = None,
    tool_names: dict[str, str] | None = None,
) -> dict | None:
    payload = event.payload_json or {}
    event_type = event.event_type
    frame_id = str(payload.get("task_frame_id") or event.id).strip()
    iteration = str(payload.get("iteration") or "").strip()
    tool_name = str(payload.get("tool_name") or "").strip()

    if event_type == "task_frame_started":
        kind = str(payload.get("kind") or "conversation").strip()
        skill_name = str(payload.get("skill_name") or payload.get("skill_id") or "").strip()
        step_id = str(payload.get("step_id") or "").strip()
        if step_names is None:
            detail_parts = [
                "SOP TaskFrame" if kind == "sop" else "对话 TaskFrame",
                f"步骤 {step_id}" if step_id else "",
                (
                    f"单步上限 {payload.get('step_timeout_seconds')} 秒"
                    if payload.get("step_timeout_seconds")
                    else ""
                ),
                (
                    f"Harness 最多 {payload.get('harness_max_actions')} 轮"
                    if payload.get("harness_max_actions")
                    else ""
                ),
            ]
        else:
            detail_parts = [
                f"当前环节 {_resolve_step_label(step_id, step_names, skill_hint)}" if step_id else "",
            ]
        return {
            "id": f"harness_frame_{frame_id}",
            "kind": "skill" if kind == "sop" else "decision",
            "text": f"开始SOP {skill_name}" if kind == "sop" and skill_name else "开始执行任务",
            "detail": " · ".join(part for part in detail_parts if part) or None,
            "state": "running",
        }
    if event_type == "task_frame_finished":
        kind = str(payload.get("kind") or "conversation").strip()
        skill_name = str(payload.get("skill_name") or payload.get("skill_id") or "").strip()
        step_id = str(payload.get("step_id") or "").strip()
        status = str(payload.get("status") or "completed").strip()
        action_count = payload.get("action_count")
        failed = status in {"failed", "blocked", "cancelled"}
        if step_names is None:
            detail_parts = [
                f"状态 {status}",
                f"步骤 {step_id}" if step_id else "",
                f"执行 {action_count} 个动作" if isinstance(action_count, int) else "",
            ]
            if kind == "sop" and skill_name:
                if failed:
                    text = f"SOP执行失败 {skill_name}"
                elif status == "awaiting_user":
                    text = f"等待用户补充 {skill_name}"
                else:
                    text = f"SOP任务执行完成 {skill_name}"
            else:
                text = "任务执行失败" if failed else "任务执行完成"
        else:
            text = _FRAME_STATUS_TEXTS.get(status, "任务执行失败" if failed else "任务执行完成")
            detail_parts = [
                f"共执行 {action_count} 个操作" if isinstance(action_count, int) else "",
            ]
        return {
            "id": f"harness_frame_{frame_id}",
            "kind": "skill" if kind == "sop" else "decision",
            "text": text,
            "detail": " · ".join(part for part in detail_parts if part) or None,
            "state": "failed" if failed else ("running" if status == "awaiting_user" else "completed"),
        }
    if event_type == "harness_action_created":
        action = str(payload.get("action") or "").strip()
        if action == "tool":
            display_tool_name = _resolve_tool_label(tool_name, tool_names)
            return {
                "id": f"harness_action_{frame_id}_{payload.get('call_id') or iteration or event.id}",
                "kind": "tool",
                "text": f"调用能力 {display_tool_name}" if display_tool_name else "调用能力",
                "detail": f"第 {iteration} 个动作" if iteration else None,
                "state": "running",
            }
        if action == "finish":
            return {
                "id": f"harness_finish_{frame_id}_{iteration or event.id}",
                "kind": "decision",
                "text": "整理任务结果",
                "detail": f"第 {iteration} 个动作" if iteration else None,
                "state": "completed",
            }
        return None
    if event_type == "harness_mcp_app_view":
        mcp_app = payload.get("mcp_app") if isinstance(payload.get("mcp_app"), dict) else None
        if mcp_app is None:
            return None
        app_tool_name = str(payload.get("tool_name") or mcp_app.get("tool_name") or "").strip()
        return {
            "id": f"harness_mcp_app_{frame_id}_{event.id}",
            "kind": "tool",
            "text": f"展示 MCP App {app_tool_name}" if app_tool_name else "展示 MCP App",
            "detail": "隔离视图；加载失败时保留文本结果",
            "mcpApp": mcp_app,
            "state": "completed",
        }
    if event_type in {"harness_tool_completed", "harness_tool_result"}:
        success = bool(payload.get("success")) if 'success' in payload else payload.get('is_error') is False
        error = payload.get("error") if isinstance(payload.get("error"), dict) else {}
        error_detail = " · ".join(
            part
            for part in (
                str(error.get("code") or "").strip(),
                str(error.get("message") or "").strip(),
                f"{payload['duration_ms'] / 1000:.2f} s" if isinstance(payload.get('duration_ms'), (int, float)) else '',
            )
            if part
        )
        result_payload = payload.get("result")
        output = _trace_payload_text(result_payload)
        display_tool_name = _resolve_tool_label(tool_name, tool_names)
        mcp_app = (
            result_payload.get("mcp_app")
            if isinstance(result_payload, dict)
            and isinstance(result_payload.get("mcp_app"), dict)
            else None
        )
        return {
            "id": f"harness_action_{frame_id}_{payload.get('call_id') or iteration or event.id}",
            "kind": "tool",
            "text": (
                f"能力调用完成 {display_tool_name}"
                if success and display_tool_name
                else f"能力调用失败 {display_tool_name}"
                if display_tool_name
                else "能力调用完成"
                if success
                else "能力调用失败"
            ),
            "detail": error_detail or None,
            "output": output or None,
            "code": _trace_payload_text(payload.get('arguments')) or None,
            "language": 'json',
            "outputLanguage": _trace_payload_language(output) if output else None,
            "outputTitle": "查看能力结果" if output else None,
            "collapsible": bool(output),
            "mcpApp": mcp_app,
            "state": "completed" if success else "failed",
        }
    if event_type == "harness_step_timeout":
        timeout_seconds = payload.get("timeout_seconds")
        action_count = payload.get("action_count")
        return {
            "id": f"harness_timeout_{frame_id}",
            "kind": "skill",
            "text": "SOP 单步运行超时",
            "detail": " · ".join(
                part
                for part in (
                    f"上限 {timeout_seconds} 秒" if timeout_seconds else "",
                    f"已执行 {action_count} 个动作" if isinstance(action_count, int) else "",
                )
                if part
            ) or None,
            "state": "failed",
        }
    return None


def _ensure_request_tenant(tenant_id: str, current_user: User) -> None:
    if tenant_id != current_user.tenant_id:
        raise HTTPException(status_code=403, detail="Tenant mismatch")


def _chat_agent_visible_to_user(row: AgentProfile, user: User) -> bool:
    if is_admin_user(user):
        return True
    metadata = row.metadata_json or {}
    return agent_owned_by_user(row, user) or metadata.get("published_to_gallery") is True


def _normalize_title(value: str | None) -> str | None:
    if value is None:
        return None
    title = value.strip()
    if not title:
        raise HTTPException(status_code=400, detail="Session title cannot be empty")
    return title[:80]


def _message_turn_ids_from_events(events: list[AgentEvent]) -> dict[str, str]:
    turn_ids: dict[str, str] = {}
    for event in events:
        payload = event.payload_json or {}
        if event.event_type == "user_message_received":
            message_id = str(payload.get("message_id") or payload.get("user_message_id") or "").strip()
            if message_id:
                turn_ids[message_id] = message_id
            continue
        if event.event_type == "assistant_message_created":
            assistant_message_id = str(
                payload.get("message_id") or payload.get("assistant_message_id") or ""
            ).strip()
            explicit_turn_id = str(payload.get("turn_id") or payload.get("user_message_id") or "").strip()
            if assistant_message_id and explicit_turn_id:
                turn_ids[assistant_message_id] = explicit_turn_id
    return turn_ids


def _build_turn_traces(
    messages: list[Message],
    events: list[AgentEvent],
    skill_names: dict[str, str],
) -> list[dict]:
    if not events:
        return []

    user_messages_by_id = {message.id: message for message in messages if message.role == "user"}
    traces: list[dict] = []
    traces_by_turn_id: dict[str, dict] = {}
    skill_hints_by_turn_id: dict[str, str | None] = {}
    active_turn_id: str | None = None

    for event in events:
        payload = event.payload_json or {}
        if event.event_type == "user_message_received":
            text = str(payload.get("message") or "")
            message_id = str(payload.get("message_id") or payload.get("user_message_id") or "").strip()
            user_message = user_messages_by_id.get(message_id) if message_id else None
            turn_id = message_id or event.id
            active_turn_id = turn_id
            current = {
                "turn_id": turn_id,
                "user_message_id": message_id or None,
                "_user_message_content": user_message.content if user_message else text,
                "started_at": event.created_at.isoformat(),
                "completed_at": None,
                "lines": [],
            }
            traces.append(current)
            traces_by_turn_id[turn_id] = current
            skill_hints_by_turn_id[turn_id] = None
            continue

        target_turn_id = _event_trace_turn_id(event, active_turn_id)
        if not target_turn_id:
            continue
        current = traces_by_turn_id.get(target_turn_id)
        if not current:
            continue
        if event.event_type == "router_decision_created":
            target_skill_id = str(payload.get("target_skill_id") or "").strip()
            if target_skill_id:
                skill_hints_by_turn_id[target_turn_id] = target_skill_id

        skill_hint = skill_hints_by_turn_id.get(target_turn_id)
        trace_was_completed = bool(current.get("completed_at"))
        lines = _event_trace_lines(event, skill_names, skill_hint)
        for line in lines:
            if trace_was_completed and line.get("state") == "running":
                line = {**line, "state": "completed"}
            _upsert_trace_line(current["lines"], line)
        event_context = _skill_context_from_event(event, skill_hint=skill_hint)
        if event_context and event_context.get("skill_id"):
            skill_hints_by_turn_id[target_turn_id] = event_context["skill_id"]
        if event.event_type == "assistant_message_created":
            if not current.get("completed_at"):
                current["completed_at"] = event.created_at.isoformat()
            _complete_trace_lines(current["lines"])
            if active_turn_id == target_turn_id:
                active_turn_id = None
        elif event.event_type in {"stream_cancelled", "stream_interrupted", "error_occurred"}:
            if not current.get("completed_at"):
                current["completed_at"] = event.created_at.isoformat()
            _finish_trace_if_needed(current, event.created_at)
            if active_turn_id == target_turn_id:
                active_turn_id = None

    fallback_time = events[-1].created_at if events else None
    open_turn_id = active_turn_id
    for current in traces:
        if open_turn_id and current.get("turn_id") == open_turn_id and not current.get("completed_at"):
            continue
        _finish_trace_if_needed(current, fallback_time)

    for trace in traces:
        trace.pop("_user_message_content", None)
    return _with_scheduled_draft_message_traces(traces, messages)


def _event_trace_turn_id(event: AgentEvent, _active_turn_id: str | None) -> str | None:
    payload = event.payload_json or {}
    if event.event_type == "user_message_received":
        return str(payload.get("message_id") or payload.get("user_message_id") or "").strip() or event.id
    explicit_turn_id = str(payload.get("turn_id") or payload.get("user_message_id") or "").strip()
    if explicit_turn_id:
        return explicit_turn_id
    return None


def _with_scheduled_draft_message_traces(traces: list[dict], messages: list[Message]) -> list[dict]:
    traced_turn_ids = {str(trace.get("turn_id") or "") for trace in traces}
    next_traces = list(traces)
    previous_user: Message | None = None
    for message in messages:
        if message.role == "user":
            previous_user = message
            continue
        if message.role != "assistant" or not previous_user:
            continue
        metadata = message.metadata_json or {}
        draft = metadata.get("scheduled_task_draft") if isinstance(metadata, dict) else None
        if not isinstance(draft, dict) or previous_user.id in traced_turn_ids:
            continue
        next_traces.append(
            {
                "turn_id": previous_user.id,
                "user_message_id": previous_user.id,
                "started_at": previous_user.created_at.isoformat(),
                "completed_at": message.created_at.isoformat(),
                "lines": _scheduled_task_trace_lines(draft),
            }
        )
        traced_turn_ids.add(previous_user.id)
    next_traces.sort(key=lambda item: str(item.get("started_at") or ""))
    return next_traces


def _event_trace_lines(
    event: AgentEvent,
    skill_names: dict[str, str],
    skill_hint: str | None = None,
    step_names: dict[str, dict[str, str]] | None = None,
    tool_names: dict[str, str] | None = None,
) -> list[dict]:
    if event.event_type in {'composition_snapshot_compiled', 'capability_manifest_resolved'}:
        from staffdeck_harness.runtime.trace_names import merge_trace_names
        merge_trace_names(event.payload_json or {}, skill_names,
                          step_names if step_names is not None else {},
                          tool_names if tool_names is not None else {})
    line = _event_trace_line(event, skill_names, skill_hint, step_names, tool_names)
    if not line:
        return []
    lines = line if isinstance(line, list) else [line]
    for item in lines:
        item.setdefault("icon", _event_trace_icon(event, item))
    return lines


def _event_trace_icon(event: AgentEvent, line: dict) -> str:
    event_type = event.event_type
    payload = event.payload_json or {}
    phase = str(payload.get("phase") or "").strip()
    if event_type in {"router_decision_created", "general_skill_intent_checked"}:
        return "judge"
    if event_type == "stream_status" and phase in {"routing", "scheduled_task_intent"}:
        return "judge"
    if event_type == "general_skill_trace":
        if phase in {
            "plan_created",
            "attempt_started",
            "running_code",
            "stdout_chunk",
            "stderr_chunk",
            "code_finished",
            "code_timeout",
            "plan_failed",
        }:
            return "generated"
        if phase.startswith("reflection_") or phase == "repair_planning":
            return "loading"
        return "advance"
    if event_type in {
        "agent_loop_continued",
        "agent_loop_completed",
        "reflection_decision_created",
        "reflection_decision",
        "reflection_skipped",
        "reflection_retry_started",
        "stream_cancelled",
        "stream_interrupted",
        "error_occurred",
    }:
        return "loading"
    kind = str(line.get("kind") or "")
    if kind == "tool":
        return "tool"
    if kind == "code":
        return "generated"
    if kind == "thinking":
        return "loading"
    return "advance"


def _event_trace_line(
    event: AgentEvent,
    skill_names: dict[str, str],
    skill_hint: str | None = None,
    step_names: dict[str, dict[str, str]] | None = None,
    tool_names: dict[str, str] | None = None,
) -> dict | list[dict] | None:
    payload = event.payload_json or {}
    if event.event_type in {
        "task_frame_started",
        "task_frame_finished",
        "harness_action_created",
        "harness_mcp_app_view",
        "harness_tool_completed",
        "harness_tool_result",
        "harness_step_timeout",
    }:
        return _harness_event_trace_line(
            event,
            skill_hint=skill_hint,
            step_names=step_names,
            tool_names=tool_names,
        )
    if event.event_type == "stream_status":
        phase = str(payload.get("phase") or "").strip()
        text = str(payload.get("text") or "").strip()
        if phase == "scheduled_task_intent":
            return {
                "id": "scheduled_task_intent",
                "kind": "decision",
                "text": text or "识别定时任务需求",
                "detail": "用户选择了创建定时任务模式",
                "state": "running",
            }
        if phase == "scheduled_task_parse":
            return {
                "id": "scheduled_task_parse",
                "kind": "decision",
                "text": text or "解析执行计划",
                "detail": None,
                "state": "running",
            }
        if phase == "scheduled_task_draft":
            return {
                "id": "scheduled_task_draft",
                "kind": "decision",
                "text": text or "生成定时任务草案",
                "detail": _scheduled_task_trace_detail(payload),
                "state": "running",
            }
        if phase == "routing":
            return {
                "id": "decision_router",
                "kind": "decision",
                "text": "判断意图",
                "detail": None,
                "state": "running",
            }
        if phase == "error":
            code = str(payload.get("code") or payload.get("error_type") or "status").strip()
            return {
                "id": f"error_{code}",
                "kind": "decision",
                "text": _error_trace_text(payload),
                "detail": _error_trace_detail(payload),
                "state": "failed",
            }
        if phase == "responding":
            return None
        if phase == "stepping":
            repair_reason = str(payload.get("repair_reason") or "main").strip()
            iteration = payload.get("iteration")
            iteration_suffix = (
                f"_{iteration}" if isinstance(iteration, (int, float, str)) else ""
            )
            return {
                "id": f"decision_stepping_{repair_reason}{iteration_suffix}",
                "kind": "decision",
                "text": "决定下一步" if repair_reason == "main" else "重新分析",
                "detail": None,
                "state": "running",
            }
        if phase == "reflecting":
            return {
                "id": "reflection",
                "kind": "decision",
                "text": "正在反思",
                "detail": None,
                "state": "running",
            }
        if phase in KNOWLEDGE_TRACE_PHASES:
            query = payload.get("query") if isinstance(payload.get("query"), dict) else {}
            detail_parts = [
                f"查询：{query['query']}" if query.get("query") else "",
                f"命中知识图谱 {payload['selected_count']} 个"
                if isinstance(payload.get("selected_count"), int)
                else "",
                f"候选 {payload['candidate_count']} 个"
                if isinstance(payload.get("candidate_count"), int)
                else "",
                f"读取 {payload['chunk_count']} 个片段"
                if isinstance(payload.get("chunk_count"), int)
                else "",
                f"整理 {payload['evidence_count']} 条证据"
                if isinstance(payload.get("evidence_count"), int)
                else "",
            ]
            return {
                "id": _knowledge_trace_line_id(payload),
                "kind": "knowledge",
                "text": text or "检索知识库",
                "detail": " · ".join(part for part in detail_parts if part) or None,
                "state": "completed"
                if phase == "evidence_pack" or phase.startswith("no_") or phase == "okf_only"
                else "running",
            }
        if phase == "tool" and payload.get("tool_name"):
            tool_name = str(payload["tool_name"])
            tool_call_id = str(payload.get("tool_call_id") or tool_name)
            return {
                "id": f"tool_{tool_call_id}",
                "kind": "tool",
                "text": f"正在调用 {tool_name}",
                "detail": None,
                "state": "running",
            }
        if phase and phase != "received":
            return {
                "id": f"decision_status_{phase}",
                "kind": "decision",
                "text": text or phase,
                "detail": None,
                "state": "running",
            }
        return None
    if event.event_type == "stream_cancelled":
        return {
            "id": "generation_stopped",
            "kind": "decision",
            "text": "用户已停止生成",
            "detail": None,
            "state": "completed",
        }
    if event.event_type == "stream_interrupted":
        return {
            "id": "generation_interrupted",
            "kind": "thinking",
            "text": _error_trace_text(payload, interrupted=True),
            "detail": _error_trace_detail(payload),
            "state": "failed",
        }
    if event.event_type == "general_skill_selected":
        skill_name = str(payload.get("skill_name") or payload.get("skill_slug") or "").strip()
        reason = str(payload.get("reason") or "").strip()
        return {
            "id": f"general_skill_selected_{event.id}",
            "kind": "skill",
            "text": f"选择通用技能 {skill_name}" if skill_name else "选择通用技能",
            "detail": reason or None,
            "state": "completed",
        }
    if event.event_type == "general_skill_intent_checked":
        skill_name = str(payload.get("skill_name") or payload.get("skill_slug") or "").strip()
        reason = str(payload.get("reason") or "").strip()
        return {
            "id": f"general_skill_intent_{event.id}",
            "kind": "decision",
            "text": "判断意图" if not skill_name else f"判断意图 {skill_name}",
            "detail": reason or None,
            "state": "completed",
        }
    if event.event_type == "general_skill_trace":
        message = str(payload.get("message") or "").strip()
        phase = str(payload.get("phase") or "").strip()
        if phase == "replying":
            return None
        detail = _general_skill_trace_detail(payload, phase)
        output = _general_skill_trace_output(payload, phase)
        code = str(payload.get("code") or "").strip()
        runtime = str(payload.get("runtime") or "").strip().lower()
        code_phases = {
            "plan_created",
            "attempt_started",
            "running_code",
            "stdout_chunk",
            "stderr_chunk",
            "code_finished",
            "code_timeout",
            "plan_failed",
        }
        return {
            "id": f"general_skill_trace_{event.id}",
            "kind": "code" if code or phase in code_phases else "decision",
            "text": message or phase or "执行通用技能",
            "detail": detail or None,
            "code": code or None,
            "language": "bash" if code and runtime == "bash" else "python" if code else None,
            "state": "failed" if _general_skill_trace_failed(phase) else "completed",
            "collapsible": bool(code or output.get("output")),
            **output,
        }
    if event.event_type == "general_skill_run_finished":
        success = bool(payload.get("success"))
        operation = str(payload.get("operation") or "execute")
        action = "阅读" if operation == "read" else "运行"
        return {
            "id": f"general_skill_finished_{event.id}",
            "kind": "skill",
            "text": f"通用技能{action}{'完成' if success else '失败'}",
            "detail": str(payload.get("skill_slug") or "") or None,
            "state": "completed" if success else "failed",
        }
    if event.event_type == "skill_state":
        lines = []
        runtime_decision = str(payload.get("runtimeDecision") or "").strip()
        from_skill_id = str(payload.get("fromSkillId") or "").strip()
        to_skill_id = str(payload.get("toSkillId") or "").strip()
        for index, entry in enumerate(payload.get("currentSkills") or []):
            if not isinstance(entry, dict):
                continue
            skill_id = str(entry.get("skillId") or "").strip()
            if not skill_id:
                continue
            name = str(entry.get("name") or skill_id).strip()
            state = str(entry.get("state") or "active").strip()
            if state == "suspended":
                label_key = "suspended"
            elif state == "pending":
                label_key = "pending"
            elif runtime_decision in {"start_skill", "start_new_task"}:
                label_key = "select"
            elif runtime_decision == "suspend_current_and_start_new_skill" or (
                runtime_decision
                in {"answer_related_question_then_resume", "answer_chitchat_then_resume"}
                and from_skill_id
                and to_skill_id
                and from_skill_id != to_skill_id
            ):
                label_key = "switch"
            elif runtime_decision == "exit_current_skill":
                label_key = "resume"
            else:
                label_key = "advance"
            state_labels = (
                _SKILL_STATE_LABELS_FRIENDLY
                if step_names is not None
                else _SKILL_STATE_LABELS
            )
            label = state_labels[label_key]
            step_id = str(entry.get("stepId") or "").strip()
            step_label = (
                _resolve_step_label(step_id, step_names, skill_id)
                if step_names is not None and step_id
                else step_id
            )
            state_key = step_id or str(index)
            lines.append(
                {
                    "id": f"skill_state_{skill_id}_{state}_{state_key}",
                    "kind": "skill",
                    "text": f"{label} {name}",
                    "detail": f"当前步骤 {step_label}" if step_id else None,
                    "state": "completed" if state == "suspended" else "running",
                }
            )
        return lines or None
    if event.event_type == "scheduled_task_draft_created":
        return _scheduled_task_trace_lines(payload)
    if event.event_type == "router_decision_created":
        intent = str(payload.get("user_intent") or "").strip()
        reason = str(payload.get("reason") or "").strip()
        return {
            "id": "decision_router",
            "kind": "decision",
            "text": f"判断意图 {intent}" if intent else "完成SOP判断",
            "detail": reason or None,
            "state": "completed",
        }
    if event.event_type == "step_result":
        tool_call = payload.get("tool_call") if isinstance(payload.get("tool_call"), dict) else {}
        knowledge_query = payload.get("knowledge_query") if isinstance(payload.get("knowledge_query"), dict) else {}
        next_step_id = str(payload.get("next_step_id") or "").strip()
        if next_step_id:
            if step_names is None:
                next_step_part = f"下一节点 {next_step_id}"
            else:
                next_step_part = f"下一步 {_resolve_step_label(next_step_id, step_names, skill_hint)}"
        else:
            next_step_part = ""
        reply = str(payload.get("reply") or "").strip()
        raw_tool_name = tool_call.get("name") if isinstance(tool_call, dict) else ""
        raw_knowledge_query = knowledge_query.get("query") if isinstance(knowledge_query, dict) else ""
        tool_name = str(raw_tool_name or "").strip()
        knowledge_query_text = str(raw_knowledge_query or "").strip()
        detail = " · ".join(
            part
            for part in (
                next_step_part,
                f"查询：{knowledge_query_text}" if knowledge_query_text else "",
                reply[:80] if not tool_name and not knowledge_query_text and reply else "",
            )
            if part
        )
        if tool_name:
            return {
                "id": f"decision_step_tool_{tool_name}",
                "kind": "decision",
                "text": f"决定调用工具 {tool_name}",
                "detail": detail or None,
                "state": "running",
            }
        if knowledge_query_text:
            return {
                "id": "decision_step_knowledge",
                "kind": "decision",
                "text": "决定查询知识库",
                "detail": detail or None,
                "state": "running",
            }
        return {
            "id": "decision_step_result",
            "kind": "decision",
            "text": "决定下一步" if next_step_id else "完成步骤判断",
            "detail": detail or None,
            "state": "completed",
        }
    if event.event_type in {"skill_started", "skill_resumed", "skill_step_changed"}:
        to_skill_id = str(payload.get("to_skill_id") or "")
        from_skill_id = str(payload.get("from_skill_id") or "")
        if (
            event.event_type == "skill_step_changed"
            and from_skill_id == to_skill_id
            and str(payload.get("from_step_id") or "")
            == str(payload.get("to_step_id") or "")
        ):
            return None
        skill_id = to_skill_id or from_skill_id or (skill_hint or "")
        if not skill_id:
            return None
        event_labels = (
            _SKILL_EVENT_LABELS_FRIENDLY
            if step_names is not None
            else _SKILL_EVENT_LABELS
        )
        label = event_labels[event.event_type]
        detail_parts = []
        if from_skill_id and from_skill_id != to_skill_id:
            from_name = skill_names.get(from_skill_id, from_skill_id)
            detail_parts.append(
                f"原流程 {from_name}" if step_names is not None else f"from {from_name}"
            )
        if payload.get("to_step_id"):
            to_step_id = str(payload["to_step_id"])
            if step_names is None:
                detail_parts.append(f"step {to_step_id}")
            else:
                detail_parts.append(
                    f"当前步骤 {_resolve_step_label(to_step_id, step_names, skill_id)}"
                )
        step_id = str(payload.get("to_step_id") or payload.get("from_step_id") or "").strip()
        state_key = step_id or "0"
        return {
            "id": f"skill_state_{skill_id}_active_{state_key}",
            "kind": "skill",
            "text": f"{label} {skill_names.get(skill_id, skill_id)}",
            "detail": " · ".join(detail_parts) or None,
            "state": "completed",
        }
    if event.event_type == "skill_completed":
        skill_id = str(payload.get("skill_id") or "")
        prefix = "完成流程" if step_names is not None else "完成SOP"
        reason = str(payload.get("reason") or "").strip()
        if step_names is not None:
            reason = _SKILL_COMPLETED_REASON_LABELS.get(reason, reason)
        return {
            "id": f"skill_{event.id}",
            "kind": "skill",
            "text": f"{prefix} {skill_names.get(skill_id, skill_id)}" if skill_id else prefix,
            "detail": reason or None,
            "state": "completed",
        }
    if event.event_type == "tool_call_started":
        name = str(payload.get("name") or "")
        tool_call_id = str(payload.get("tool_call_id") or name or event.id)
        if not name:
            return None
        return {
            "id": f"tool_{tool_call_id}",
            "kind": "tool",
            "text": f"调用工具 {name}",
            "detail": None,
            "state": "running",
        }
    if event.event_type == "knowledge_query_started":
        query = payload.get("query") if isinstance(payload.get("query"), dict) else {}
        text = str(query.get("query") if isinstance(query, dict) else payload.get("text") or "").strip()
        return {
            "id": _knowledge_trace_line_id(payload),
            "kind": "knowledge",
            "phase": "query",
            "text": "查询业务资料",
            "detail": text or None,
            "state": "running",
        }
    if event.event_type in {"knowledge_query_finished", "knowledge_result"}:
        chunks = payload.get("chunks") if isinstance(payload.get("chunks"), list) else []
        buckets = payload.get("selected_buckets") if isinstance(payload.get("selected_buckets"), list) else []
        concepts = payload.get("selected_concepts") if isinstance(payload.get("selected_concepts"), list) else []
        evidence = payload.get("evidence_pack") if isinstance(payload.get("evidence_pack"), list) else []
        parts = [
            f"命中 Wiki {len(concepts)} 个" if concepts else "",
            f"展开 {len(buckets)} 个知识桶" if buckets else "",
            f"读取 {len(chunks)} 个片段" if chunks else "",
            f"生成 {len(evidence)} 条引用候选" if evidence else "",
        ]
        return {
            "id": _knowledge_trace_line_id(payload),
            "kind": "knowledge",
            "phase": "result",
            "text": "读取业务资料",
            "detail": " · ".join(part for part in parts if part),
            "state": "completed",
        }
    if event.event_type == "tool_result":
        content = payload.get("content") if isinstance(payload.get("content"), dict) else {}
        raw_name = str(
            payload.get("rawToolName")
            or payload.get("toolId")
            or content.get("tool_name")
            or ""
        ).strip()
        display_name = str(payload.get("toolName") or raw_name).strip()
        tool_call_id = str(payload.get("toolCallId") or raw_name or event.id)
        success = payload.get("success")
        is_error = bool(payload.get("isError")) if success is None else not bool(success)
        detail_payload = content if isinstance(content, dict) else payload
        output = _trace_payload_text(detail_payload)
        return {
            "id": f"tool_{tool_call_id}",
            "kind": "tool",
            "text": f"{'工具调用失败' if is_error else '调用工具'} {display_name}",
            "detail": _tool_trace_detail(detail_payload),
            "output": output or None,
            "outputLanguage": _trace_payload_language(output) if output else None,
            "outputTitle": "查看工具结果" if output else None,
            "collapsible": bool(output),
            "state": "failed" if is_error else "completed",
        }
    if event.event_type == "tool_call_finished":
        name = str(payload.get("tool_name") or "")
        tool_call_id = str(payload.get("tool_call_id") or name or event.id)
        success = bool(payload.get("success"))
        return {
            "id": f"tool_{tool_call_id}",
            "kind": "tool",
            "text": f"{'调用工具' if success else '工具调用失败'} {name}",
            "detail": _tool_trace_detail(payload),
            "state": "completed" if success else "failed",
        }
    if event.event_type == "agent_loop_continued":
        iteration = str(payload.get("iteration") or event.id)
        target_tool = str(payload.get("target_tool_name") or "").strip()
        return {
            "id": f"decision_stepping_tool_continuation_{iteration}",
            "kind": "decision",
            "text": "重新分析执行动作",
            "detail": f"决定继续调用工具 {target_tool}" if target_tool else "决定继续调用工具",
            "state": "completed",
        }
    if event.event_type == "agent_loop_completed":
        iteration = str(payload.get("iteration") or event.id)
        return {
            "id": f"decision_stepping_tool_continuation_{iteration}",
            "kind": "decision",
            "text": "重新分析执行动作",
            "detail": "判断无需继续调用工具",
            "state": "completed",
        }
    if event.event_type in {"reflection_decision_created", "reflection_decision"}:
        needs_retry = bool(payload.get("needs_retry"))
        return {
            "id": "reflection",
            "kind": "decision",
            "text": "反思后继续尝试" if needs_retry else "反思通过",
            "detail": _reflection_trace_detail(
                payload,
                step_names=step_names,
                skill_hint=skill_hint,
            ),
            "state": "completed",
        }
    if event.event_type == "reflection_skipped":
        return {
            "id": "reflection",
            "kind": "decision",
            "text": "反思已关闭",
            "detail": str(payload.get("reason") or "") or None,
            "state": "completed",
        }
    if event.event_type == "reflection_retry_started":
        mode = str(payload.get("mode") or "").strip()
        target_tool = str(payload.get("target_tool_name") or "").strip()
        target_skill = str(payload.get("target_skill_id") or "").strip()
        target = target_tool or skill_names.get(target_skill, target_skill)
        target_kind = "工具" if mode == "tool" else ("流程" if step_names is not None else "SOP")
        return {
            "id": "reflection",
            "kind": "decision",
            "text": f"重试{target_kind} {target}".strip(),
            "detail": str(payload.get("reason") or "") or None,
            "state": "completed",
        }
    if event.event_type == "error_occurred":
        code = str(payload.get("code") or payload.get("error_type") or event.id).strip()
        return {
            "id": f"error_{code}",
            "kind": "decision",
            "text": _error_trace_text(payload),
            "detail": _error_trace_detail(payload),
            "state": "failed",
        }
    return None


def _tool_trace_detail(payload: dict) -> str | None:
    data = payload.get("data")
    data_dict = data if isinstance(data, dict) else {}
    parts = [
        "已复用此前成功结果" if payload.get("idempotent_replay") or data_dict.get("idempotent_replay") else "",
        str(data_dict.get("source") or "").strip(),
        "未命中" if data_dict.get("found") is False else "已命中" if data_dict.get("found") is True else "",
        str(data_dict.get("miss_reason") or "").strip(),
        str(data_dict.get("recommendation") or "").strip(),
    ]
    text = " · ".join(part for part in parts if part)
    return text or None


def _reflection_trace_detail(
    payload: dict,
    step_names: dict[str, dict[str, str]] | None = None,
    skill_hint: str | None = None,
) -> str | None:
    target_step_id = str(payload.get("target_step_id") or "").strip()
    if not target_step_id:
        step_part = ""
    elif step_names is None:
        step_part = f"步骤 {target_step_id}"
    else:
        step_part = f"步骤 {_resolve_step_label(target_step_id, step_names, skill_hint)}"
    parts = [
        str(payload.get("reason") or "").strip(),
        f"工具 {payload['target_tool_name']}" if payload.get("target_tool_name") else "",
        f"技能 {payload['target_skill_id']}" if payload.get("target_skill_id") else "",
        step_part,
    ]
    text = " · ".join(part for part in parts if part)
    return text or None


def _knowledge_trace_line_id(payload: dict) -> str:
    raw_query = payload.get("query")
    if isinstance(raw_query, dict):
        raw_query = raw_query.get("query")
    query = " ".join(str(raw_query or "").split())
    return f"knowledge_lookup_{query}" if query else "knowledge_lookup"


def _upsert_trace_line(lines: list[dict], line: dict) -> None:
    for index, item in enumerate(lines):
        if item.get("id") == line.get("id"):
            lines[index] = line
            return
    lines.append(line)


def _complete_trace_lines(lines: list[dict]) -> None:
    for line in lines:
        if line.get("state") == "running":
            line["state"] = "completed"
    thinking = next((line for line in lines if line.get("id") == "thinking"), None)
    if thinking:
        thinking["text"] = "已完成思考"
        thinking["state"] = "completed"


def _finish_trace_if_needed(trace: dict, fallback_time) -> None:
    if not trace.get("completed_at") and fallback_time:
        trace["completed_at"] = fallback_time.isoformat()
    _complete_trace_lines(trace["lines"])

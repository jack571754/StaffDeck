from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlmodel import Session, select

from pydantic import BaseModel, Field

from app.api.mock import FeishuAppNotifyRequest, feishu_app_notify
from app.channels.feishu_binding import (
    list_tenant_feishu_bindings,
    resolve_feishu_binding,
)
from app.db import get_session
from app.db.models import ChannelIdentity, Message, ScheduledTask, ScheduledTaskRun, User, utc_now
from app.scheduled_tasks.schema import (
    ScheduledTaskCreateRequest,
    ScheduledTaskDraftRead,
    ScheduledTaskDraftRequest,
    ScheduledTaskRead,
    ScheduledTaskRunRead,
    ScheduledTaskUpdateRequest,
)
from app.scheduled_tasks.service import (
    build_test_feishu_card,
    create_scheduled_task,
    detect_scheduled_task_draft,
    duplicate_scheduled_task,
    scheduled_task_read,
    scheduled_task_run_read,
    start_scheduled_task_async,
    update_scheduled_task,
)
from app.security.auth import get_current_user
from app.security.permissions import is_admin_user as _is_admin_user
from app.security.tenant import ensure_tenant


class ScheduledTaskTestNotifyRequest(BaseModel):
    tenant_id: str
    task_id: str | None = None
    title: str | None = None
    feishu_notify: dict[str, Any] = Field(default_factory=dict)


enterprise_router = APIRouter(prefix="/api/enterprise/scheduled-tasks", tags=["enterprise:scheduled-tasks"])
chat_router = APIRouter(prefix="/api/chat/scheduled-tasks", tags=["chat:scheduled-tasks"])
chat_draft_router = APIRouter(prefix="/api/chat/scheduled-task-drafts", tags=["chat:scheduled-task-drafts"])


@enterprise_router.get("", response_model=list[ScheduledTaskRead])
def list_enterprise_scheduled_tasks(
    tenant_id: str = Query(...),
    agent_id: str | None = Query(None),
    status: str | None = Query(None),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> list[ScheduledTaskRead]:
    _ensure_request_tenant(tenant_id, current_user)
    rows = _list_tasks(db, tenant_id, current_user, agent_id, status)
    return [scheduled_task_read(row) for row in rows]


@enterprise_router.post("", response_model=ScheduledTaskRead)
def create_enterprise_scheduled_task(
    request: ScheduledTaskCreateRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> ScheduledTaskRead:
    _ensure_request_tenant(request.tenant_id, current_user)
    row = create_scheduled_task(db, request, current_user)
    return scheduled_task_read(row)


@enterprise_router.get("/runs", response_model=list[ScheduledTaskRunRead])
def list_enterprise_scheduled_task_runs_for_agent(
    tenant_id: str = Query(...),
    agent_id: str | None = Query(None),
    status: str | None = Query(None),
    limit: int = Query(100, ge=1, le=500),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> list[ScheduledTaskRunRead]:
    _ensure_request_tenant(tenant_id, current_user)
    ensure_tenant(db, tenant_id)
    conditions = [ScheduledTaskRun.tenant_id == tenant_id]
    if agent_id:
        conditions.append(ScheduledTaskRun.agent_id == agent_id)
    if status:
        conditions.append(ScheduledTaskRun.status == status)
    if not _is_admin_user(current_user):
        conditions.append(ScheduledTaskRun.user_id == current_user.id)
    rows = db.exec(
        select(ScheduledTaskRun, ScheduledTask)
        .join(ScheduledTask, ScheduledTaskRun.scheduled_task_id == ScheduledTask.id)
        .where(*conditions)
        .order_by(ScheduledTaskRun.created_at.desc())
        .limit(limit)
    ).all()
    return [scheduled_task_run_read(run, task) for run, task in rows]


@enterprise_router.get("/feishu-chats")
def list_enterprise_feishu_chats(
    tenant_id: str = Query(...),
    binding_id: str | None = Query(None),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> list[dict[str, Any]]:
    """获取指定飞书自建应用所加入/拥有的全部群聊列表。

    未指定 binding_id 时沿用"租户最新一条 active 飞书绑定"的旧行为；指定了但
    该应用不存在或已停用时直接报错，绝不回退到另一个应用。
    """
    from app.channels.adapters.feishu import FeishuAdapter

    _ensure_request_tenant(tenant_id, current_user)

    binding, error = resolve_feishu_binding(db, tenant_id, binding_id)
    if binding is None:
        raise HTTPException(status_code=400, detail=error)

    adapter = FeishuAdapter()
    return adapter.list_chats(binding)


@enterprise_router.get("/feishu-apps")
def list_enterprise_feishu_apps(
    tenant_id: str = Query(...),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> list[dict[str, Any]]:
    """列出本租户可选的飞书应用，供定时任务通知配置选择推送应用。

    只暴露应用标识，不含凭证与渠道配置。已停用/过期的应用也会返回，便于编辑页
    提示"原绑定的应用已停用"而不是显示成未知应用。
    """
    _ensure_request_tenant(tenant_id, current_user)

    bindings = list_tenant_feishu_bindings(db, tenant_id)
    default_marked = False
    apps: list[dict[str, Any]] = []
    for binding in bindings:
        is_default = False
        if binding.status == "active" and not default_marked:
            is_default = True
            default_marked = True
        config = binding.config_json if isinstance(binding.config_json, dict) else {}
        apps.append(
            {
                "id": binding.id,
                "name": binding.name,
                "app_id": config.get("app_id"),
                "bot_open_id": config.get("bot_open_id"),
                "bot_name": config.get("bot_name"),
                "status": binding.status,
                "is_default": is_default,
            }
        )
    return apps


@enterprise_router.post("/test-notify")
def test_draft_scheduled_task_notify(
    request: ScheduledTaskTestNotifyRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> dict[str, Any]:
    _ensure_request_tenant(request.tenant_id, current_user)
    fn_cfg = request.feishu_notify or {}
    card_content = build_test_feishu_card(request.title or "定时任务")
    notify_req = FeishuAppNotifyRequest(
        scheduled_task_id=request.task_id,
        tenant_id=request.tenant_id,
        webhooks=list(fn_cfg.get("webhooks") or []),
        mobiles=list(fn_cfg.get("mobiles") or []),
        open_ids=list(fn_cfg.get("open_ids") or []),
        chat_ids=list(fn_cfg.get("chat_ids") or []),
        binding_id=fn_cfg.get("binding_id"),
        card=card_content,
        title=f"【测试推送】{request.title or '定时任务'}",
    )
    return feishu_app_notify(notify_req, db)


@enterprise_router.get("/feishu-recipients")
def list_feishu_recipients(
    tenant_id: str = Query(...),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> list[dict[str, Any]]:
    _ensure_request_tenant(tenant_id, current_user)
    rows = db.exec(
        select(ChannelIdentity).where(
            ChannelIdentity.tenant_id == tenant_id,
            ChannelIdentity.channel == "feishu",
        )
    ).all()
    recipients: list[dict[str, Any]] = []
    seen = set()
    for row in rows:
        if row.external_user_id.startswith("group:"):
            continue
        oid = row.external_user_id
        if oid in seen:
            continue
        seen.add(oid)
        recipients.append({
            "open_id": oid,
            "display_name": row.display_name or row.staffdeck_user_id or oid,
            "user_id": row.staffdeck_user_id or "",
        })
    return recipients


@enterprise_router.get("/{task_id}", response_model=ScheduledTaskRead)
def get_enterprise_scheduled_task(
    task_id: str,
    tenant_id: str = Query(...),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> ScheduledTaskRead:
    row = _get_task(db, tenant_id, task_id, current_user)
    return scheduled_task_read(row)


@enterprise_router.put("/{task_id}", response_model=ScheduledTaskRead)
def update_enterprise_scheduled_task(
    task_id: str,
    request: ScheduledTaskUpdateRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> ScheduledTaskRead:
    _ensure_request_tenant(request.tenant_id, current_user)
    row = _get_task(db, request.tenant_id, task_id, current_user)
    row = update_scheduled_task(db, row, request, current_user)
    return scheduled_task_read(row)


@enterprise_router.delete("/{task_id}")
def archive_enterprise_scheduled_task(
    task_id: str,
    tenant_id: str = Query(...),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> dict[str, bool]:
    row = _get_task(db, tenant_id, task_id, current_user)
    row.status = "archived"
    row.next_run_at = None
    row.updated_at = utc_now()
    db.add(row)
    db.commit()
    return {"ok": True}


@enterprise_router.get("/{task_id}/runs", response_model=list[ScheduledTaskRunRead])
def list_enterprise_scheduled_task_runs(
    task_id: str,
    tenant_id: str = Query(...),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> list[ScheduledTaskRunRead]:
    row = _get_task(db, tenant_id, task_id, current_user)
    runs = db.exec(
        select(ScheduledTaskRun)
        .where(ScheduledTaskRun.tenant_id == tenant_id, ScheduledTaskRun.scheduled_task_id == row.id)
        .order_by(ScheduledTaskRun.scheduled_for.desc())
    ).all()
    return [scheduled_task_run_read(item, row) for item in runs]


@enterprise_router.post("/{task_id}/run-now", response_model=ScheduledTaskRunRead)
def run_enterprise_scheduled_task_now(
    task_id: str,
    tenant_id: str = Query(...),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> ScheduledTaskRunRead:
    row = _get_task(db, tenant_id, task_id, current_user)
    if row.status == "archived":
        raise HTTPException(status_code=400, detail="已删除的自动任务不能运行")
    run = start_scheduled_task_async(db, row, scheduled_for=utc_now(), manual=True)
    return scheduled_task_run_read(run, row)


@enterprise_router.post("/{task_id}/duplicate", response_model=ScheduledTaskRead)
def duplicate_enterprise_scheduled_task(
    task_id: str,
    tenant_id: str = Query(...),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> ScheduledTaskRead:
    row = _get_task(db, tenant_id, task_id, current_user)
    if row.status == "archived":
        raise HTTPException(status_code=400, detail="已归档的自动任务不能复制")
    duplicated = duplicate_scheduled_task(db, row, current_user.id)
    return scheduled_task_read(duplicated)


@enterprise_router.post("/{task_id}/test-notify")
def test_enterprise_scheduled_task_notify(
    task_id: str,
    tenant_id: str = Query(...),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> dict[str, Any]:
    row = _get_task(db, tenant_id, task_id, current_user)
    metadata = row.metadata_json if isinstance(row.metadata_json, dict) else {}
    fn_cfg = metadata.get("feishu_notify") if isinstance(metadata.get("feishu_notify"), dict) else {}
    card_content = build_test_feishu_card(row.title)
    notify_req = FeishuAppNotifyRequest(
        scheduled_task_id=row.id,
        tenant_id=tenant_id,
        webhooks=list(fn_cfg.get("webhooks") or []),
        mobiles=list(fn_cfg.get("mobiles") or []),
        open_ids=list(fn_cfg.get("open_ids") or []),
        chat_ids=list(fn_cfg.get("chat_ids") or []),
        binding_id=fn_cfg.get("binding_id"),
        card=card_content,
        title=f"【测试推送】{row.title}",
    )
    return feishu_app_notify(notify_req, db)


@chat_router.get("", response_model=list[ScheduledTaskRead])
def list_chat_scheduled_tasks(
    tenant_id: str = Query(...),
    agent_id: str | None = Query(None),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> list[ScheduledTaskRead]:
    _ensure_request_tenant(tenant_id, current_user)
    rows = _list_tasks(db, tenant_id, current_user, agent_id, None)
    return [scheduled_task_read(row) for row in rows]


@chat_router.post("", response_model=ScheduledTaskRead)
def create_chat_scheduled_task(
    request: ScheduledTaskCreateRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> ScheduledTaskRead:
    _ensure_request_tenant(request.tenant_id, current_user)
    row = create_scheduled_task(db, request, current_user)
    read = scheduled_task_read(row)
    _mark_chat_draft_created(db, row, read)
    return read


@chat_draft_router.post("", response_model=ScheduledTaskDraftRead)
def create_chat_scheduled_task_draft(
    request: ScheduledTaskDraftRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> ScheduledTaskDraftRead:
    _ensure_request_tenant(request.tenant_id, current_user)
    draft = detect_scheduled_task_draft(
        db,
        request.tenant_id,
        request.agent_id,
        current_user.id,
        request.message,
        request.session_id,
        request.timezone,
    )
    if not draft:
        return ScheduledTaskDraftRead(
            should_create=False,
            tenant_id=request.tenant_id,
            agent_id=request.agent_id,
            source_session_id=request.session_id,
        )
    return draft


def _mark_chat_draft_created(db: Session, row: ScheduledTask, read: ScheduledTaskRead) -> None:
    if not row.source_session_id:
        return
    messages = db.exec(
        select(Message)
        .where(
            Message.tenant_id == row.tenant_id,
            Message.session_id == row.source_session_id,
            Message.role == "assistant",
        )
        .order_by(Message.created_at.desc())
        .limit(20)
    ).all()
    for message in messages:
        metadata = dict(message.metadata_json or {})
        if not isinstance(metadata.get("scheduled_task_draft"), dict):
            continue
        metadata["scheduled_task_created"] = read.model_dump(mode="json")
        message.metadata_json = metadata
        db.add(message)
        db.commit()
        return


def _list_tasks(
    db: Session,
    tenant_id: str,
    current_user: User,
    agent_id: str | None,
    status: str | None,
) -> list[ScheduledTask]:
    ensure_tenant(db, tenant_id)
    conditions = [ScheduledTask.tenant_id == tenant_id]
    if agent_id:
        conditions.append(ScheduledTask.agent_id == agent_id)
    if status:
        conditions.append(ScheduledTask.status == status)
    if not _is_admin_user(current_user):
        conditions.append(ScheduledTask.created_by_user_id == current_user.id)
    return db.exec(select(ScheduledTask).where(*conditions).order_by(ScheduledTask.updated_at.desc())).all()


def _get_task(db: Session, tenant_id: str, task_id: str, current_user: User) -> ScheduledTask:
    _ensure_request_tenant(tenant_id, current_user)
    row = db.get(ScheduledTask, task_id)
    if not row or row.tenant_id != tenant_id:
        raise HTTPException(status_code=404, detail="自动任务不存在")
    if not _is_admin_user(current_user) and row.created_by_user_id != current_user.id:
        raise HTTPException(status_code=403, detail="无权访问该自动任务")
    return row


def _ensure_request_tenant(tenant_id: str, current_user: User) -> None:
    if current_user.tenant_id != tenant_id:
        raise HTTPException(status_code=403, detail="Cannot access another tenant")


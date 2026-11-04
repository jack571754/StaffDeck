"""为启用的销售任务发布并更新独立店铺页，不发送通知。"""

import copy
import hashlib
import logging
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm.attributes import flag_modified
from sqlmodel import Session, select

from app.core.artifact_owners import ResolvedArtifact, resolve_artifact_owner
from app.core.harness_session_cleanup import harness_path_segment, harness_reports_root
from app.db.models import ArtifactShareLink, Message, ScheduledTask, User, new_id
from app.harness import artifact_owner_pair, publish_harness_artifacts
from app.reporting.link import mint_report_share_link
from app.reporting.shop_sales_report import render_shop_sales_report
from app.reporting.writer import MAX_REPORT_BYTES

logger = logging.getLogger(__name__)
SHOP_LINK_KEY = "shop_sales_share_link_id"


def _replace_file(target: Path, content: bytes) -> None:
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=target.parent, prefix=".tmp-shop-sales-",
                                         delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _task_context(
    db: Session, task: ScheduledTask,
) -> tuple[ArtifactShareLink, ResolvedArtifact, User] | None:
    metadata = dict(task.metadata_json or {})
    if metadata.get("shop_sales_report_enabled") is not True:
        return None
    bound_id = str(metadata.get(SHOP_LINK_KEY) or "").strip()
    context_id = bound_id or str(metadata.get("artifact_share_link_id") or "").strip()
    context = db.get(ArtifactShareLink, context_id) if context_id else None
    if context is None or context.is_revoked or context.tenant_id != task.tenant_id:
        return None
    actor = db.get(User, task.created_by_user_id)
    if actor is None or actor.tenant_id != task.tenant_id:
        return None
    if actor.role != "admin" and context.created_by_user_id != actor.id:
        return None
    resolved = resolve_artifact_owner(
        db, owner_kind=context.owner_kind, tenant_id=task.tenant_id,
        session_id=context.session_id, owner_id=context.owner_id, path=context.path,
    )
    if resolved is None:
        return None
    return context, resolved, actor


def resolve_task_shop_sales_url(db: Session, task: ScheduledTask) -> str | None:
    """查询失败时也仅提供经过 owner 校验的旧页面，不重新取数。"""
    if not (task.metadata_json or {}).get(SHOP_LINK_KEY):
        return None
    result = _task_context(db, task)
    if result is None:
        return None
    context, resolved, _ = result
    root = harness_reports_root(
        db=db, tenant_id=task.tenant_id, session_id=context.session_id,
        owner_kind=context.owner_kind, owner_id=context.owner_id,
    )
    expected = root / f"shop-sales-{harness_path_segment(task.id)}.html"
    if expected.is_symlink() or context.path != expected.relative_to(resolved.workspace_root).as_posix():
        return None
    from app.scheduled_tasks.service import resolve_task_share_link_url

    return resolve_task_share_link_url(db, task, metadata_key=SHOP_LINK_KEY)


def sync_task_shop_sales_report(
    db: Session, task: ScheduledTask, *, shops: list[dict[str, Any]] | None = None,
) -> str | None:
    """保留上次成功快照，返回经过绑定与 owner 校验的可用分享 URL。"""
    result = _task_context(db, task)
    if result is None:
        return None
    context, resolved, actor = result
    metadata = dict(task.metadata_json or {})
    bound_id = str(metadata.get(SHOP_LINK_KEY) or "").strip()
    reports_root = harness_reports_root(
        db=db, tenant_id=task.tenant_id, session_id=context.session_id,
        owner_kind=context.owner_kind, owner_id=context.owner_id,
    )
    target = reports_root / f"shop-sales-{harness_path_segment(task.id)}.html"
    relative_path = target.relative_to(resolved.workspace_root).as_posix()
    if target.is_symlink() or (bound_id and context.path != relative_path):
        return None
    from app.scheduled_tasks.service import resolve_task_share_link_url

    if shops is None:
        from app.reporting.dashboard_shops import fetch_task_all_shops

        shops = fetch_task_all_shops(db, task)
    if shops is None:
        return resolve_task_share_link_url(db, task, metadata_key=SHOP_LINK_KEY) if bound_id else None

    content = render_shop_sales_report(shops, generated_at=datetime.now(UTC)).encode("utf-8")
    if len(content) > MAX_REPORT_BYTES:
        logger.warning("店铺销售页面超出交付物大小上限")
        return None
    messages = db.exec(select(Message).where(
        Message.tenant_id == task.tenant_id, Message.session_id == context.session_id,
        Message.role == "assistant",
    )).all()
    owner = (context.owner_kind, context.owner_id)
    parents = [message for message in messages if any(
        isinstance(artifact, dict) and artifact_owner_pair(artifact) == owner
        and artifact.get("path") == context.path
        for artifact in (message.metadata_json or {}).get("harness_artifacts", [])
    )]
    if not parents:
        return None
    reports_root.mkdir(parents=True, exist_ok=True)
    previous = target.read_bytes() if target.exists() else None
    try:
        _replace_file(target, content)
        artifacts = publish_harness_artifacts(
            resolved.workspace_root, context.owner_id, [{"path": relative_path}],
            operation="report_generation", max_file_bytes=MAX_REPORT_BYTES,
        )
        if not artifacts:
            raise ValueError("店铺销售页面未能登记为交付物")
        artifact = dict(artifacts[0], owner_kind=context.owner_kind, owner_id=context.owner_id,
                        display_name="全部店铺实时销售.html", content_type="text/html; charset=utf-8",
                        description="全部店铺运营与达播净销及源数据更新时间")
        updated = False
        for message in messages:
            meta = copy.deepcopy(message.metadata_json or {})
            manifest = meta.get("harness_artifacts", [])
            matched = False
            for index, item in enumerate(manifest):
                if (isinstance(item, dict) and item.get("path") == relative_path
                        and artifact_owner_pair(item) == owner):
                    manifest[index] = artifact
                    matched = updated = True
            if matched:
                message.metadata_json = meta
                flag_modified(message, "metadata_json")
                db.add(message)
        if not updated:
            message = parents[0]
            meta = copy.deepcopy(message.metadata_json or {})
            meta.setdefault("harness_artifacts", []).append(artifact)
            message.metadata_json = meta
            flag_modified(message, "metadata_json")
            db.add(message)
        if not bound_id:
            link_id = new_id("ashare")
            share = mint_report_share_link(
                tenant_id=task.tenant_id, session_id=context.session_id,
                owner_kind=context.owner_kind, owner_id=context.owner_id, path=relative_path,
                share_link_id=link_id,
            )
            link = ArtifactShareLink(
                id=link_id,
                tenant_id=task.tenant_id, session_id=context.session_id,
                owner_kind=context.owner_kind, owner_id=context.owner_id, path=relative_path,
                title="全部店铺实时销售", token=share.token,
                token_hash=hashlib.sha256(share.token.encode()).hexdigest(), url=share.url,
                created_by_user_id=actor.id, created_by_username=actor.display_name or actor.username,
                ttl_seconds=share.remaining_seconds,
                expires_at=datetime.fromtimestamp(share.expires_at, UTC).replace(tzinfo=None),
            )
            db.add(link)
            metadata[SHOP_LINK_KEY] = link.id
            task.metadata_json = metadata
            db.add(task)
        db.commit()
    except (OSError, ValueError, SQLAlchemyError) as exc:
        db.rollback()
        if previous is None:
            target.unlink(missing_ok=True)
        else:
            _replace_file(target, previous)
        logger.warning("店铺销售页面更新失败，保留上次结果：%s", type(exc).__name__)
        return None
    return resolve_task_share_link_url(db, task, metadata_key=SHOP_LINK_KEY)

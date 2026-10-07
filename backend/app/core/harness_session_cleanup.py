from __future__ import annotations

import hashlib
import shutil
from dataclasses import dataclass
from pathlib import Path

from sqlmodel import Session, select

from app import paths
from app.db.models import (
    HarnessInvocationRecord,
    HarnessRunRecord,
    HarnessSessionLeaseRecord,
    HarnessTaskFrameRecord,
    HarnessTurnRecord,
    UIConfig,
    utc_now,
)
from app.harness.artifacts import ARTIFACT_OWNER_DEFAULT_KIND

# Directory segment for generated HTML reports inside a Harness workspace. Never
# collides with a frame directory, whose name always ends in "-<12 hex>".
HARNESS_REPORTS_DIR = "reports"


@dataclass(frozen=True)
class HarnessSessionRecordCleanup:
    session_lease_count: int
    turn_count: int
    invocation_count: int
    run_count: int
    task_frame_count: int


def stage_harness_session_execution_reset(
    db: Session,
    *,
    tenant_id: str,
    session_id: str,
) -> None:
    """Cancel durable Harness execution state while preserving turn receipts."""
    invocations = db.exec(
        select(HarnessInvocationRecord).where(
            HarnessInvocationRecord.tenant_id == tenant_id,
            HarnessInvocationRecord.session_id == session_id,
            HarnessInvocationRecord.status == "started",
        )
    ).all()
    runs = db.exec(
        select(HarnessRunRecord).where(
            HarnessRunRecord.tenant_id == tenant_id,
            HarnessRunRecord.session_id == session_id,
            HarnessRunRecord.status == "running",
        )
    ).all()
    task_frames = db.exec(
        select(HarnessTaskFrameRecord).where(
            HarnessTaskFrameRecord.tenant_id == tenant_id,
            HarnessTaskFrameRecord.session_id == session_id,
            HarnessTaskFrameRecord.status.notin_(
                {"completed", "cancelled", "failed"}
            ),
        )
    ).all()
    turns = db.exec(
        select(HarnessTurnRecord).where(
            HarnessTurnRecord.tenant_id == tenant_id,
            HarnessTurnRecord.session_id == session_id,
            HarnessTurnRecord.status == "started",
        )
    ).all()
    leases = db.exec(
        select(HarnessSessionLeaseRecord).where(
            HarnessSessionLeaseRecord.tenant_id == tenant_id,
            HarnessSessionLeaseRecord.session_id == session_id,
        )
    ).all()

    now = utc_now()
    for turn in turns:
        turn.status = "cancelled"
        turn.error_json = {
            "code": "SESSION_RESET",
            "message": "会话已重置，原 Harness turn 已取消。",
        }
        turn.finished_at = now
        turn.updated_at = now
        db.add(turn)
    for invocation in invocations:
        db.delete(invocation)
    db.flush()
    for run in runs:
        db.delete(run)
    db.flush()
    for task_frame in task_frames:
        db.delete(task_frame)
    db.flush()
    for lease in leases:
        db.delete(lease)
    db.flush()


def stage_harness_session_record_deletion(
    db: Session,
    *,
    tenant_id: str,
    session_id: str,
) -> HarnessSessionRecordCleanup:
    """Stage Harness v2 records for deletion in dependency order.

    The caller owns the surrounding transaction so chat-session deletion remains
    atomic with the existing message, event, and feedback cleanup.
    """

    invocations = db.exec(
        select(HarnessInvocationRecord).where(
            HarnessInvocationRecord.tenant_id == tenant_id,
            HarnessInvocationRecord.session_id == session_id,
        )
    ).all()
    runs = db.exec(
        select(HarnessRunRecord).where(
            HarnessRunRecord.tenant_id == tenant_id,
            HarnessRunRecord.session_id == session_id,
        )
    ).all()
    task_frames = db.exec(
        select(HarnessTaskFrameRecord).where(
            HarnessTaskFrameRecord.tenant_id == tenant_id,
            HarnessTaskFrameRecord.session_id == session_id,
        )
    ).all()
    turns = db.exec(
        select(HarnessTurnRecord).where(
            HarnessTurnRecord.tenant_id == tenant_id,
            HarnessTurnRecord.session_id == session_id,
        )
    ).all()
    session_leases = db.exec(
        select(HarnessSessionLeaseRecord).where(
            HarnessSessionLeaseRecord.tenant_id == tenant_id,
            HarnessSessionLeaseRecord.session_id == session_id,
        )
    ).all()

    for invocation in invocations:
        db.delete(invocation)
    db.flush()
    for run in runs:
        db.delete(run)
    db.flush()
    for task_frame in task_frames:
        db.delete(task_frame)
    db.flush()
    for turn in turns:
        db.delete(turn)
    db.flush()
    for session_lease in session_leases:
        db.delete(session_lease)
    db.flush()

    return HarnessSessionRecordCleanup(
        session_lease_count=len(session_leases),
        turn_count=len(turns),
        invocation_count=len(invocations),
        run_count=len(runs),
        task_frame_count=len(task_frames),
    )


def harness_path_segment(value: str) -> str:
    """Map an external identifier to the exact Harness workspace segment."""

    raw = str(value or "")
    normalized = "".join(
        character
        for character in raw
        if character.isalnum() or character in {"-", "_"}
    )
    prefix = normalized[:72] or "unknown"
    suffix = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:12]
    return f"{prefix}-{suffix}"


def harness_storage_root(*, tenant_id: str, db: Session | None = None) -> Path:
    """Resolve the administrator-selected root for new non-sandboxed workspaces."""

    default_root = paths.user_data_dir().resolve() / "harness_workspaces"
    if db is not None:
        row = db.get(UIConfig, tenant_id)
        configured = str(getattr(row, "harness_storage_path", "") or "").strip()
        if row is not None and not bool(getattr(row, "sandbox_enabled", False)) and configured:
            return Path(configured).expanduser().resolve()
    return default_root


def harness_session_workspace_path(
    *, tenant_id: str, session_id: str, db: Session | None = None
) -> Path:
    return (
        harness_storage_root(tenant_id=tenant_id, db=db)
        / harness_path_segment(tenant_id)
        / harness_path_segment(session_id)
    )


def _validated_workspace_subdir(session_path: Path, *segments: str) -> Path:
    """Join segments beneath one session workspace, rejecting symlinked components.

    Every writer and reader of a workspace path must agree on this check, otherwise a
    writer could publish a file that ``open_harness_artifact`` — which opens each path
    component with ``O_NOFOLLOW`` — then refuses to read. Components that do not exist
    yet are not symlinks, so this is safe to call before creating the directory.
    """

    chain = [session_path.parents[1], session_path.parent, session_path]
    current = session_path
    for segment in segments:
        current = current / segment
        chain.append(current)
    for component in chain:
        if component.is_symlink():
            raise OSError(
                "refusing to provision Harness workspace through a symlink"
            )
    return current


def harness_task_workspace_path(
    *,
    tenant_id: str,
    session_id: str,
    task_frame_id: str,
    db: Session | None = None,
) -> Path:
    session_path = harness_session_workspace_path(
        tenant_id=tenant_id,
        session_id=session_id,
        db=db,
    )
    return _validated_workspace_subdir(session_path, harness_path_segment(task_frame_id))


def harness_owner_workspace_root(
    *,
    tenant_id: str,
    session_id: str,
    owner_kind: str,
    owner_id: str,
    db: Session | None = None,
) -> Path:
    """Resolve the workspace root that an artifact owner's relative paths anchor to.

    This is the directory ``open_harness_artifact`` is given, so it must agree exactly
    with the root the writer used. A ``harness_frame`` owner is anchored at its frame
    workspace (the same path ``harness_task_workspace_path`` returns); every other
    owner kind — currently ``scheduled_run``, for deterministic pipeline runs that have
    no frame at all — is anchored at the session workspace.
    """

    session_path = harness_session_workspace_path(
        tenant_id=tenant_id,
        session_id=session_id,
        db=db,
    )
    if str(owner_kind or "").strip() == ARTIFACT_OWNER_DEFAULT_KIND:
        return _validated_workspace_subdir(session_path, harness_path_segment(owner_id))
    return _validated_workspace_subdir(session_path)


def harness_reports_root(
    *,
    tenant_id: str,
    session_id: str,
    owner_kind: str,
    owner_id: str,
    db: Session | None = None,
) -> Path:
    """Resolve the directory holding generated HTML reports for one artifact owner.

    A ``harness_frame`` owner keeps its reports inside that frame's workspace, so the
    file stays visible to the frame's later ``exec_command`` calls and the relative
    artifact path matches what the frame publishes. Every other owner kind keeps them
    at the session root instead.

    Both locations live inside the session workspace, so
    ``remove_harness_session_workspace`` deletes reports together with the session and
    no separate reaper is required. The per-segment id also makes a collision between
    the frame directory and the literal ``reports`` segment impossible.
    """

    workspace_root = harness_owner_workspace_root(
        tenant_id=tenant_id,
        session_id=session_id,
        owner_kind=owner_kind,
        owner_id=owner_id,
        db=db,
    )
    if str(owner_kind or "").strip() == ARTIFACT_OWNER_DEFAULT_KIND:
        return _validated_workspace_subdir(workspace_root, HARNESS_REPORTS_DIR)
    return _validated_workspace_subdir(
        workspace_root, HARNESS_REPORTS_DIR, harness_path_segment(owner_id)
    )


def remove_harness_session_workspace(
    *, tenant_id: str, session_id: str, db: Session | None = None
) -> bool:
    """Remove only one exact tenant/session Harness workspace.

    Parent symlinks are rejected so cleanup can never traverse a redirected
    ``harness_workspaces`` or tenant directory. A symlink at the exact session
    path is unlinked without touching its target.
    """

    session_path = harness_session_workspace_path(
        tenant_id=tenant_id,
        session_id=session_id,
        db=db,
    )
    harness_root = session_path.parents[1]
    tenant_path = session_path.parent

    if harness_root.is_symlink() or tenant_path.is_symlink():
        raise OSError("refusing to clean Harness workspace through a symlinked parent")
    if session_path.is_symlink():
        session_path.unlink()
        return True
    if not session_path.exists():
        return False
    if not session_path.is_dir():
        session_path.unlink()
        return True

    shutil.rmtree(session_path)
    return True

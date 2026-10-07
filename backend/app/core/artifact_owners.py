"""Artifact ownership resolution for downloads and signed share links.

Every published workspace artifact belongs to *something*: a Harness TaskFrame for
files an agent produced, a ``ScheduledTaskRun`` for deterministic pipeline reports,
and whatever a future producer registers. ``security/artifact_share.py`` signs only
the opaque ``(owner_kind, owner_id)`` pair. The semantic half of the check — does the
owner exist, does it belong to this tenant and session, is the path actually
published under it, and which directory is that path relative to — happens here.

That split is deliberate. ``app.security`` must not import ``app.core``, and a
signature-verification module must not consult a business registry to decide whether
a token is authentic. A token whose ``owner_kind`` is unknown here is simply not
resolvable, and every failure mode collapses to "not found" so the caller can return
a uniform 404.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlmodel import Session, select

from app.core.harness_session_cleanup import (
    harness_session_workspace_path,
    harness_task_workspace_path,
)
from app.core.published_deliverables import find_published_workspace_artifact
from app.db.models import HarnessTaskFrameRecord, ScheduledTaskRun
from app.harness.artifacts import ARTIFACT_OWNER_DEFAULT_KIND

# Owner kind of an artifact produced by a deterministic scheduled-task pipeline run.
# Such a run has no Harness TaskFrame at all, so it cannot be addressed the usual way.
SCHEDULED_RUN_OWNER_KIND = "scheduled_run"


@dataclass(frozen=True)
class ResolvedArtifact:
    """A published artifact plus the workspace its relative path is anchored to."""

    owner_kind: str
    owner_id: str
    workspace_root: Path
    artifact: dict[str, Any]

    @property
    def path(self) -> str:
        return str(self.artifact.get("path") or "")

    @property
    def sha256(self) -> str:
        return str(self.artifact.get("sha256") or "").strip().lower()

    @property
    def size(self) -> int | None:
        size = self.artifact.get("size")
        return size if isinstance(size, int) else None

    @property
    def display_name(self) -> str:
        return str(self.artifact.get("display_name") or "").strip()


# A resolver receives the already-verified ``(tenant_id, session_id)`` from the token
# or request and must return ``None`` — never raise — for every failure, including a
# storage root that refuses to provision (see ``_validated_workspace_subdir``).
ArtifactOwnerResolver = Callable[..., "ResolvedArtifact | None"]

_ARTIFACT_OWNER_RESOLVERS: dict[str, ArtifactOwnerResolver] = {}


def register_artifact_owner_resolver(
    owner_kind: str,
    resolver: ArtifactOwnerResolver,
) -> None:
    """Bind one ``owner_kind`` string to its resolver, replacing any previous one."""

    kind = str(owner_kind or "").strip().lower()
    if not kind:
        raise ValueError("Artifact owner kind cannot be empty.")
    _ARTIFACT_OWNER_RESOLVERS[kind] = resolver


def registered_artifact_owner_kinds() -> tuple[str, ...]:
    return tuple(sorted(_ARTIFACT_OWNER_RESOLVERS))


def resolve_artifact_owner(
    db: Session,
    *,
    owner_kind: str,
    tenant_id: str,
    session_id: str,
    owner_id: str,
    path: str,
) -> ResolvedArtifact | None:
    """Resolve an artifact owner, or ``None`` when anything about it does not hold.

    ``owner_kind`` defaults to the Harness TaskFrame kind so tokens minted before
    ownership was generalized keep resolving.
    """

    kind = str(owner_kind or "").strip().lower() or ARTIFACT_OWNER_DEFAULT_KIND
    identity = str(owner_id or "").strip()
    if not identity or not str(session_id or "").strip():
        return None
    resolver = _ARTIFACT_OWNER_RESOLVERS.get(kind)
    if resolver is None:
        return None
    return resolver(
        db,
        tenant_id=tenant_id,
        session_id=session_id,
        owner_id=identity,
        path=path,
    )


def _resolve_harness_frame_owner(
    db: Session,
    *,
    tenant_id: str,
    session_id: str,
    owner_id: str,
    path: str,
) -> ResolvedArtifact | None:
    """Resolve an artifact published by a Harness TaskFrame.

    This is the pre-existing behaviour, moved out of ``api/chat.py`` unchanged: the
    frame row must exist for the tenant and session, the artifact must be published
    in some assistant message under that frame, and the file lives inside the frame's
    own workspace.
    """

    frame = db.exec(
        select(HarnessTaskFrameRecord.id).where(
            HarnessTaskFrameRecord.tenant_id == tenant_id,
            HarnessTaskFrameRecord.session_id == session_id,
            HarnessTaskFrameRecord.task_id == owner_id,
        )
    ).first()
    if frame is None:
        return None
    artifact = find_published_workspace_artifact(
        db,
        tenant_id=tenant_id,
        session_id=session_id,
        owner_kind=ARTIFACT_OWNER_DEFAULT_KIND,
        owner_id=owner_id,
        path=path,
    )
    if artifact is None:
        return None
    try:
        workspace_root = harness_task_workspace_path(
            tenant_id=tenant_id,
            session_id=session_id,
            task_frame_id=owner_id,
            db=db,
        )
    except OSError:
        return None
    return ResolvedArtifact(
        owner_kind=ARTIFACT_OWNER_DEFAULT_KIND,
        owner_id=owner_id,
        workspace_root=workspace_root,
        artifact=artifact,
    )


def _resolve_scheduled_run_owner(
    db: Session,
    *,
    tenant_id: str,
    session_id: str,
    owner_id: str,
    path: str,
) -> ResolvedArtifact | None:
    """Resolve an artifact published by a deterministic scheduled-task run.

    A pipeline run has no TaskFrame, so ownership is the run itself and the report
    lives under ``reports/<run>/`` at the session workspace root. ``session_id`` is
    optional on the model, so a run that never got a session is unresolvable rather
    than an error.
    """

    run = db.get(ScheduledTaskRun, owner_id)
    if run is None:
        return None
    if str(run.tenant_id or "") != str(tenant_id):
        return None
    run_session_id = str(run.session_id or "").strip()
    if not run_session_id or run_session_id != str(session_id):
        return None
    artifact = find_published_workspace_artifact(
        db,
        tenant_id=tenant_id,
        session_id=session_id,
        owner_kind=SCHEDULED_RUN_OWNER_KIND,
        owner_id=owner_id,
        path=path,
    )
    if artifact is None:
        return None
    try:
        workspace_root = harness_session_workspace_path(
            tenant_id=tenant_id,
            session_id=session_id,
            db=db,
        )
    except OSError:
        return None
    return ResolvedArtifact(
        owner_kind=SCHEDULED_RUN_OWNER_KIND,
        owner_id=owner_id,
        workspace_root=workspace_root,
        artifact=artifact,
    )


register_artifact_owner_resolver(ARTIFACT_OWNER_DEFAULT_KIND, _resolve_harness_frame_owner)
register_artifact_owner_resolver(SCHEDULED_RUN_OWNER_KIND, _resolve_scheduled_run_owner)


__all__ = [
    "SCHEDULED_RUN_OWNER_KIND",
    "ArtifactOwnerResolver",
    "ResolvedArtifact",
    "register_artifact_owner_resolver",
    "registered_artifact_owner_kinds",
    "resolve_artifact_owner",
]

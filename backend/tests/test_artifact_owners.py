"""Tests for artifact ownership resolution (app/core/artifact_owners.py).

The resolver is the security boundary behind every download and share link: it is
what turns an opaque signed ``(owner_kind, owner_id)`` pair into a file. So the tests
here are mostly about what must NOT resolve — a missing owner, a mismatched session,
a foreign tenant, an unpublished path — plus the layout agreement between the frame
workspace and the reports root.

No filesystem is written: the resolver reads the manifest from the DB and only
computes the workspace root, so the symlink-sensitive paths are exercised as
nonexistent components (which are not symlinks).
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.core.artifact_owners import (
    SCHEDULED_RUN_OWNER_KIND,
    register_artifact_owner_resolver,
    registered_artifact_owner_kinds,
    resolve_artifact_owner,
)
from app.core.harness_session_cleanup import (
    HARNESS_REPORTS_DIR,
    harness_path_segment,
    harness_reports_root,
    harness_session_workspace_path,
    harness_task_workspace_path,
)
from app.db.models import (
    ChatSession,
    HarnessTaskFrameRecord,
    Message,
    ScheduledTaskRun,
    Tenant,
    User,
)
from app.harness import ARTIFACT_OWNER_DEFAULT_KIND, artifact_owner_pair

TENANT_ID = "tenant_demo"
SESSION_ID = "session_demo"
FRAME_ID = "task_demo"
RUN_ID = "schedrun_demo"
FRAME_PATH = "reports/report.html"


@pytest.fixture(autouse=True)
def _hermetic_data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep workspace-root resolution out of the developer's real data directory."""

    monkeypatch.setenv("ULTRARAG_DATA_DIR", str(tmp_path / "data"))


def _test_engine():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    return engine


@pytest.fixture()
def db() -> Session:
    with Session(_test_engine()) as session:
        session.add(Tenant(id=TENANT_ID, name="Demo"))
        session.add(
            User(
                id="user_owner",
                tenant_id=TENANT_ID,
                username="owner",
                password_hash="test",
            )
        )
        session.add(
            ChatSession(id=SESSION_ID, tenant_id=TENANT_ID, user_id="user_owner")
        )
        session.commit()
        yield session


def _publish(
    db: Session,
    *,
    path: str,
    owner_kind: str | None = None,
    owner_id: str | None = None,
    task_frame_id: str | None = None,
    message_id: str = "msg_assistant",
    session_id: str = SESSION_ID,
    tenant_id: str = TENANT_ID,
) -> None:
    """Record one published artifact in an assistant message's manifest."""

    artifact: dict[str, object] = {
        "type": "workspace_file",
        "path": path,
        "sha256": "a" * 64,
        "size": 123,
        "operation": "publish_artifact",
    }
    if task_frame_id is not None:
        artifact["task_frame_id"] = task_frame_id
    if owner_kind is not None:
        artifact["owner_kind"] = owner_kind
    if owner_id is not None:
        artifact["owner_id"] = owner_id
    db.add(
        Message(
            id=message_id,
            tenant_id=tenant_id,
            session_id=session_id,
            role="assistant",
            content="已生成报告。",
            metadata_json={"harness_artifacts": [artifact]},
        )
    )
    db.commit()


def _add_frame(db: Session, *, task_id: str = FRAME_ID, session_id: str = SESSION_ID) -> None:
    db.add(
        HarnessTaskFrameRecord(
            id=f"htask_{task_id}",
            tenant_id=TENANT_ID,
            session_id=session_id,
            source_turn_id="turn_demo",
            task_id=task_id,
        )
    )
    db.commit()


def _add_run(
    db: Session,
    *,
    run_id: str = RUN_ID,
    session_id: str | None = SESSION_ID,
    tenant_id: str = TENANT_ID,
) -> None:
    db.add(
        ScheduledTaskRun(
            id=run_id,
            tenant_id=tenant_id,
            scheduled_task_id="sched_demo",
            agent_id="agent_demo",
            user_id="user_owner",
            session_id=session_id,
            scheduled_for=datetime(2026, 9, 28, 8, 0, tzinfo=UTC),
        )
    )
    db.commit()


# ------------------------------------------------------- ownership schema


def test_owner_pair_reads_legacy_task_frame_id() -> None:
    # Manifests written before ownership was generalized carry only task_frame_id.
    assert artifact_owner_pair({"task_frame_id": FRAME_ID}) == (
        ARTIFACT_OWNER_DEFAULT_KIND,
        FRAME_ID,
    )


def test_owner_pair_prefers_explicit_owner_fields() -> None:
    assert artifact_owner_pair(
        {"task_frame_id": FRAME_ID, "owner_kind": SCHEDULED_RUN_OWNER_KIND, "owner_id": RUN_ID}
    ) == (SCHEDULED_RUN_OWNER_KIND, RUN_ID)


def test_owner_pair_rejects_ownership_free_dicts() -> None:
    assert artifact_owner_pair({"path": "a.html"}) is None
    assert artifact_owner_pair({"task_frame_id": "   "}) is None


# ------------------------------------------------------- harness_frame


def test_harness_frame_resolves_published_artifact(db: Session) -> None:
    _add_frame(db)
    _publish(db, path=FRAME_PATH, task_frame_id=FRAME_ID)

    resolved = resolve_artifact_owner(
        db,
        owner_kind=ARTIFACT_OWNER_DEFAULT_KIND,
        tenant_id=TENANT_ID,
        session_id=SESSION_ID,
        owner_id=FRAME_ID,
        path=FRAME_PATH,
    )

    assert resolved is not None
    assert resolved.owner_kind == ARTIFACT_OWNER_DEFAULT_KIND
    assert resolved.owner_id == FRAME_ID
    assert resolved.path == FRAME_PATH
    assert resolved.size == 123
    # The frame workspace is the anchor, exactly as the inline chat.py logic had it.
    assert resolved.workspace_root == harness_task_workspace_path(
        tenant_id=TENANT_ID, session_id=SESSION_ID, task_frame_id=FRAME_ID
    )


def test_owner_kind_defaults_to_harness_frame(db: Session) -> None:
    _add_frame(db)
    _publish(db, path=FRAME_PATH, task_frame_id=FRAME_ID)

    resolved = resolve_artifact_owner(
        db,
        owner_kind="",
        tenant_id=TENANT_ID,
        session_id=SESSION_ID,
        owner_id=FRAME_ID,
        path=FRAME_PATH,
    )

    assert resolved is not None
    assert resolved.owner_kind == ARTIFACT_OWNER_DEFAULT_KIND


def test_harness_frame_without_a_frame_row_does_not_resolve(db: Session) -> None:
    _publish(db, path=FRAME_PATH, task_frame_id=FRAME_ID)

    assert (
        resolve_artifact_owner(
            db,
            owner_kind=ARTIFACT_OWNER_DEFAULT_KIND,
            tenant_id=TENANT_ID,
            session_id=SESSION_ID,
            owner_id=FRAME_ID,
            path=FRAME_PATH,
        )
        is None
    )


def test_unpublished_path_does_not_resolve(db: Session) -> None:
    _add_frame(db)
    _publish(db, path=FRAME_PATH, task_frame_id=FRAME_ID)

    assert (
        resolve_artifact_owner(
            db,
            owner_kind=ARTIFACT_OWNER_DEFAULT_KIND,
            tenant_id=TENANT_ID,
            session_id=SESSION_ID,
            owner_id=FRAME_ID,
            path="reports/other.html",
        )
        is None
    )


def test_path_traversal_does_not_resolve(db: Session) -> None:
    _add_frame(db)
    _publish(db, path=FRAME_PATH, task_frame_id=FRAME_ID)

    assert (
        resolve_artifact_owner(
            db,
            owner_kind=ARTIFACT_OWNER_DEFAULT_KIND,
            tenant_id=TENANT_ID,
            session_id=SESSION_ID,
            owner_id=FRAME_ID,
            path="../../etc/passwd",
        )
        is None
    )


def test_frame_owned_by_another_session_does_not_resolve(db: Session) -> None:
    _add_frame(db, session_id="session_other")
    _publish(db, path=FRAME_PATH, task_frame_id=FRAME_ID)

    assert (
        resolve_artifact_owner(
            db,
            owner_kind=ARTIFACT_OWNER_DEFAULT_KIND,
            tenant_id=TENANT_ID,
            session_id=SESSION_ID,
            owner_id=FRAME_ID,
            path=FRAME_PATH,
        )
        is None
    )


# ------------------------------------------------------- scheduled_run


def test_scheduled_run_resolves_pipeline_report(db: Session) -> None:
    _add_run(db)
    _publish(
        db,
        path=f"{HARNESS_REPORTS_DIR}/{harness_path_segment(RUN_ID)}/report-abc.html",
        owner_kind=SCHEDULED_RUN_OWNER_KIND,
        owner_id=RUN_ID,
    )

    resolved = resolve_artifact_owner(
        db,
        owner_kind=SCHEDULED_RUN_OWNER_KIND,
        tenant_id=TENANT_ID,
        session_id=SESSION_ID,
        owner_id=RUN_ID,
        path=f"{HARNESS_REPORTS_DIR}/{harness_path_segment(RUN_ID)}/report-abc.html",
    )

    assert resolved is not None
    assert resolved.owner_kind == SCHEDULED_RUN_OWNER_KIND
    # Pipeline reports are anchored at the session workspace root, not a frame dir.
    assert resolved.workspace_root == harness_session_workspace_path(
        tenant_id=TENANT_ID, session_id=SESSION_ID
    )


def test_scheduled_run_without_a_session_id_does_not_resolve(db: Session) -> None:
    # ScheduledTaskRun.session_id is Optional; a run that never got a session must
    # resolve to None rather than raising.
    _add_run(db, session_id=None)
    _publish(
        db,
        path="reports/x/report-abc.html",
        owner_kind=SCHEDULED_RUN_OWNER_KIND,
        owner_id=RUN_ID,
    )

    assert (
        resolve_artifact_owner(
            db,
            owner_kind=SCHEDULED_RUN_OWNER_KIND,
            tenant_id=TENANT_ID,
            session_id=SESSION_ID,
            owner_id=RUN_ID,
            path="reports/x/report-abc.html",
        )
        is None
    )


def test_scheduled_run_from_another_session_does_not_resolve(db: Session) -> None:
    _add_run(db, session_id="session_other")
    _publish(
        db,
        path="reports/x/report-abc.html",
        owner_kind=SCHEDULED_RUN_OWNER_KIND,
        owner_id=RUN_ID,
    )

    assert (
        resolve_artifact_owner(
            db,
            owner_kind=SCHEDULED_RUN_OWNER_KIND,
            tenant_id=TENANT_ID,
            session_id=SESSION_ID,
            owner_id=RUN_ID,
            path="reports/x/report-abc.html",
        )
        is None
    )


def test_scheduled_run_from_another_tenant_does_not_resolve(db: Session) -> None:
    _add_run(db, tenant_id="tenant_other")
    _publish(
        db,
        path="reports/x/report-abc.html",
        owner_kind=SCHEDULED_RUN_OWNER_KIND,
        owner_id=RUN_ID,
    )

    assert (
        resolve_artifact_owner(
            db,
            owner_kind=SCHEDULED_RUN_OWNER_KIND,
            tenant_id=TENANT_ID,
            session_id=SESSION_ID,
            owner_id=RUN_ID,
            path="reports/x/report-abc.html",
        )
        is None
    )


def test_missing_scheduled_run_does_not_resolve(db: Session) -> None:
    _publish(
        db,
        path="reports/x/report-abc.html",
        owner_kind=SCHEDULED_RUN_OWNER_KIND,
        owner_id=RUN_ID,
    )

    assert (
        resolve_artifact_owner(
            db,
            owner_kind=SCHEDULED_RUN_OWNER_KIND,
            tenant_id=TENANT_ID,
            session_id=SESSION_ID,
            owner_id=RUN_ID,
            path="reports/x/report-abc.html",
        )
        is None
    )


def test_frame_manifest_is_not_reachable_as_a_scheduled_run(db: Session) -> None:
    # A manifest entry written for a frame carries no scheduled_run ownership, so the
    # run resolver must not pick it up even when the id happens to match.
    _add_run(db, run_id=FRAME_ID)
    _publish(db, path=FRAME_PATH, task_frame_id=FRAME_ID)

    assert (
        resolve_artifact_owner(
            db,
            owner_kind=SCHEDULED_RUN_OWNER_KIND,
            tenant_id=TENANT_ID,
            session_id=SESSION_ID,
            owner_id=FRAME_ID,
            path=FRAME_PATH,
        )
        is None
    )


# ------------------------------------------------------- registry


def test_unknown_owner_kind_does_not_resolve(db: Session) -> None:
    _add_frame(db)
    _publish(db, path=FRAME_PATH, task_frame_id=FRAME_ID)

    assert (
        resolve_artifact_owner(
            db,
            owner_kind="not_registered",
            tenant_id=TENANT_ID,
            session_id=SESSION_ID,
            owner_id=FRAME_ID,
            path=FRAME_PATH,
        )
        is None
    )


def test_empty_owner_or_session_id_does_not_resolve(db: Session) -> None:
    _add_frame(db)
    _publish(db, path=FRAME_PATH, task_frame_id=FRAME_ID)

    for owner_id, session_id in (("", SESSION_ID), (FRAME_ID, ""), ("  ", SESSION_ID)):
        assert (
            resolve_artifact_owner(
                db,
                owner_kind=ARTIFACT_OWNER_DEFAULT_KIND,
                tenant_id=TENANT_ID,
                session_id=session_id,
                owner_id=owner_id,
                path=FRAME_PATH,
            )
            is None
        )


def test_builtin_owner_kinds_are_registered() -> None:
    assert set(registered_artifact_owner_kinds()) >= {
        ARTIFACT_OWNER_DEFAULT_KIND,
        SCHEDULED_RUN_OWNER_KIND,
    }


def test_registering_a_blank_kind_is_rejected() -> None:
    with pytest.raises(ValueError):
        register_artifact_owner_resolver("  ", lambda *args, **kwargs: None)


# ------------------------------------------------------- reports root layout


def test_reports_root_for_a_frame_lives_inside_the_frame_workspace() -> None:
    root = harness_reports_root(
        tenant_id=TENANT_ID,
        session_id=SESSION_ID,
        owner_kind=ARTIFACT_OWNER_DEFAULT_KIND,
        owner_id=FRAME_ID,
    )
    frame_root = harness_task_workspace_path(
        tenant_id=TENANT_ID, session_id=SESSION_ID, task_frame_id=FRAME_ID
    )

    assert root == frame_root / HARNESS_REPORTS_DIR


def test_reports_root_for_a_run_is_namespaced_by_run() -> None:
    root = harness_reports_root(
        tenant_id=TENANT_ID,
        session_id=SESSION_ID,
        owner_kind=SCHEDULED_RUN_OWNER_KIND,
        owner_id=RUN_ID,
    )

    assert root.name == harness_path_segment(RUN_ID)
    assert root.parent.name == HARNESS_REPORTS_DIR


def test_reports_dir_never_collides_with_a_frame_directory() -> None:
    # Frame directories always end in "-<12 hex>", so the literal reports segment
    # cannot be mistaken for one.
    assert harness_path_segment(HARNESS_REPORTS_DIR) != HARNESS_REPORTS_DIR
    assert HARNESS_REPORTS_DIR != harness_path_segment(FRAME_ID)

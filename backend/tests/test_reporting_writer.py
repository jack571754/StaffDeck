"""Tests for report persistence (app/reporting/writer.py + link.py).

The central property is a *round trip*: a report this module writes must be readable
through the same resolver the download and share endpoints use. That is not a
formality — the writer picks a workspace root, the resolver re-derives it, and
`open_harness_artifact` opens every path component with O_NOFOLLOW and rejects
symlinks. If those three ever disagree, the write succeeds and the read 404s, which is
exactly the failure this test exists to catch.

It is also the one place where a byte-level mismatch would show up: the digest
recorded at publish time must equal the digest recomputed at read time, so a
text-mode write on Windows (which would translate newlines) cannot slip through.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.core.artifact_owners import SCHEDULED_RUN_OWNER_KIND, resolve_artifact_owner
from app.core.harness_session_cleanup import (
    HARNESS_REPORTS_DIR,
    harness_owner_workspace_root,
    harness_path_segment,
)
from app.db.models import (
    ChatSession,
    HarnessTaskFrameRecord,
    Message,
    ScheduledTaskRun,
    Tenant,
    User,
)
from app.harness import ARTIFACT_OWNER_DEFAULT_KIND, open_harness_artifact
from app.reporting import (
    PublishedReport,
    ReportDocumentError,
    ReportRenderError,
    build_generic_table_report,
    mint_report_share_link,
    write_report_html,
)
from app.security import artifact_share as artifact_share_mod

TENANT_ID = "tenant_demo"
SESSION_ID = "session_demo"
FRAME_ID = "task_demo"
RUN_ID = "schedrun_demo"


@pytest.fixture(autouse=True)
def _hermetic_data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
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


def _document(title: str = "每日播报"):
    return build_generic_table_report(
        [{"门店": "甲", "净销_万": 12.5}, {"门店": "乙", "净销_万": 8.25}],
        title=title,
        generated_at=datetime(2026, 9, 28, 8, 0, tzinfo=UTC),
    )


def _record_artifact(
    db: Session, published: PublishedReport, *, message_id: str = "msg_report"
) -> None:
    """Stand in for the pipeline/capability layer that attaches artifacts to a reply."""

    db.add(
        Message(
            id=message_id,
            tenant_id=TENANT_ID,
            session_id=SESSION_ID,
            role="assistant",
            content="报告已生成。",
            metadata_json={"harness_artifacts": [published.artifact]},
        )
    )
    db.commit()


def _add_frame(db: Session) -> None:
    db.add(
        HarnessTaskFrameRecord(
            id="htask_demo",
            tenant_id=TENANT_ID,
            session_id=SESSION_ID,
            source_turn_id="turn_demo",
            task_id=FRAME_ID,
        )
    )
    db.commit()


def _add_run(db: Session, *, session_id: str | None = SESSION_ID) -> None:
    db.add(
        ScheduledTaskRun(
            id=RUN_ID,
            tenant_id=TENANT_ID,
            scheduled_task_id="sched_demo",
            agent_id="agent_demo",
            user_id="user_owner",
            session_id=session_id,
            scheduled_for=datetime(2026, 9, 28, 8, 0, tzinfo=UTC),
        )
    )
    db.commit()


# ------------------------------------------------------- round trip


def test_scheduled_run_report_is_readable_through_the_resolver(db: Session) -> None:
    _add_run(db)

    published = write_report_html(
        db=db,
        tenant_id=TENANT_ID,
        session_id=SESSION_ID,
        owner_kind=SCHEDULED_RUN_OWNER_KIND,
        owner_id=RUN_ID,
        document=_document(),
    )
    _record_artifact(db, published)

    assert published.path.startswith(f"{HARNESS_REPORTS_DIR}/")
    assert published.owner_kind == SCHEDULED_RUN_OWNER_KIND
    assert published.size > 0

    resolved = resolve_artifact_owner(
        db,
        owner_kind=SCHEDULED_RUN_OWNER_KIND,
        tenant_id=TENANT_ID,
        session_id=SESSION_ID,
        owner_id=RUN_ID,
        path=published.path,
    )
    assert resolved is not None

    opened = open_harness_artifact(resolved.workspace_root, published.path)
    try:
        # The digest recorded at publish time must match the bytes on disk now: this
        # is what makes the endpoint's 409 integrity check meaningful.
        assert opened.sha256() == published.sha256
        assert opened.size == published.size
        body = b"".join(opened.iter_bytes()).decode("utf-8")
    finally:
        opened.close()
    assert "每日播报" in body
    assert body.startswith("<!doctype html>")


def test_frame_report_is_readable_through_the_resolver(db: Session) -> None:
    _add_frame(db)

    published = write_report_html(
        db=db,
        tenant_id=TENANT_ID,
        session_id=SESSION_ID,
        owner_kind=ARTIFACT_OWNER_DEFAULT_KIND,
        owner_id=FRAME_ID,
        document=_document(),
    )
    _record_artifact(db, published)

    resolved = resolve_artifact_owner(
        db,
        owner_kind=ARTIFACT_OWNER_DEFAULT_KIND,
        tenant_id=TENANT_ID,
        session_id=SESSION_ID,
        owner_id=FRAME_ID,
        path=published.path,
    )
    assert resolved is not None
    # A frame-owned report lives inside the frame workspace, so a later exec_command
    # in the same frame can see it.
    assert resolved.workspace_root.name == harness_path_segment(FRAME_ID)

    opened = open_harness_artifact(resolved.workspace_root, published.path)
    try:
        assert opened.sha256() == published.sha256
    finally:
        opened.close()


def test_report_lands_under_the_owner_reports_directory(db: Session) -> None:
    _add_run(db)

    published = write_report_html(
        db=db,
        tenant_id=TENANT_ID,
        session_id=SESSION_ID,
        owner_kind=SCHEDULED_RUN_OWNER_KIND,
        owner_id=RUN_ID,
        document=_document(),
    )
    workspace_root = harness_owner_workspace_root(
        tenant_id=TENANT_ID,
        session_id=SESSION_ID,
        owner_kind=SCHEDULED_RUN_OWNER_KIND,
        owner_id=RUN_ID,
        db=db,
    )

    on_disk = workspace_root / published.path
    assert on_disk.is_file()
    assert on_disk.parent == (
        workspace_root / HARNESS_REPORTS_DIR / harness_path_segment(RUN_ID)
    )


def test_each_write_gets_a_distinct_path(db: Session) -> None:
    _add_run(db)

    first = write_report_html(
        db=db,
        tenant_id=TENANT_ID,
        session_id=SESSION_ID,
        owner_kind=SCHEDULED_RUN_OWNER_KIND,
        owner_id=RUN_ID,
        document=_document("第一次"),
    )
    second = write_report_html(
        db=db,
        tenant_id=TENANT_ID,
        session_id=SESSION_ID,
        owner_kind=SCHEDULED_RUN_OWNER_KIND,
        owner_id=RUN_ID,
        document=_document("第二次"),
    )

    assert first.path != second.path
    assert first.display_name == "第一次.html"
    assert second.display_name == "第二次.html"


# ------------------------------------------------------- hygiene


def test_no_temp_file_is_left_behind(db: Session) -> None:
    _add_run(db)

    published = write_report_html(
        db=db,
        tenant_id=TENANT_ID,
        session_id=SESSION_ID,
        owner_kind=SCHEDULED_RUN_OWNER_KIND,
        owner_id=RUN_ID,
        document=_document(),
    )
    workspace_root = harness_owner_workspace_root(
        tenant_id=TENANT_ID,
        session_id=SESSION_ID,
        owner_kind=SCHEDULED_RUN_OWNER_KIND,
        owner_id=RUN_ID,
        db=db,
    )
    report_dir = (workspace_root / published.path).parent

    assert [item.name for item in report_dir.iterdir()] == [
        Path(published.path).name
    ]


def test_oversized_report_is_rejected_before_writing(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    _add_run(db)
    monkeypatch.setattr("app.reporting.writer.MAX_REPORT_BYTES", 64)

    with pytest.raises(ReportRenderError, match="超过上限"):
        write_report_html(
            db=db,
            tenant_id=TENANT_ID,
            session_id=SESSION_ID,
            owner_kind=SCHEDULED_RUN_OWNER_KIND,
            owner_id=RUN_ID,
            document=_document(),
        )


def test_invalid_document_is_rejected_by_the_writer(db: Session) -> None:
    _add_run(db)
    document = replace(_document(), meta=replace(_document().meta, title="   "))

    with pytest.raises(ReportDocumentError):
        write_report_html(
            db=db,
            tenant_id=TENANT_ID,
            session_id=SESSION_ID,
            owner_kind=SCHEDULED_RUN_OWNER_KIND,
            owner_id=RUN_ID,
            document=document,
        )


def test_explicit_file_name_is_used_verbatim(db: Session) -> None:
    _add_run(db)

    published = write_report_html(
        db=db,
        tenant_id=TENANT_ID,
        session_id=SESSION_ID,
        owner_kind=SCHEDULED_RUN_OWNER_KIND,
        owner_id=RUN_ID,
        document=_document(),
        file_name="daily.html",
    )

    assert published.path.endswith("/daily.html")
    assert published.display_name == "daily.html"


# ------------------------------------------------------- share link


def test_minted_link_carries_the_owner_pair(db: Session) -> None:
    _add_run(db)
    published = write_report_html(
        db=db,
        tenant_id=TENANT_ID,
        session_id=SESSION_ID,
        owner_kind=SCHEDULED_RUN_OWNER_KIND,
        owner_id=RUN_ID,
        document=_document(),
    )

    link = mint_report_share_link(
        tenant_id=TENANT_ID,
        session_id=SESSION_ID,
        owner_kind=published.owner_kind,
        owner_id=published.owner_id,
        path=published.path,
    )

    assert link.url.endswith(f"/api/chat/artifacts/view/{link.token}")
    decoded = artifact_share_mod.decode_artifact_share_token(link.token)
    assert decoded["owner_kind"] == SCHEDULED_RUN_OWNER_KIND
    assert decoded["owner_id"] == RUN_ID
    assert decoded["path"] == published.path
    assert decoded["session_id"] == SESSION_ID
    assert link.remaining_seconds > 0


def test_link_ttl_can_be_overridden(db: Session) -> None:
    _add_run(db)
    published = write_report_html(
        db=db,
        tenant_id=TENANT_ID,
        session_id=SESSION_ID,
        owner_kind=SCHEDULED_RUN_OWNER_KIND,
        owner_id=RUN_ID,
        document=_document(),
    )

    link = mint_report_share_link(
        tenant_id=TENANT_ID,
        session_id=SESSION_ID,
        owner_kind=published.owner_kind,
        owner_id=published.owner_id,
        path=published.path,
        ttl_seconds=60,
    )

    assert 0 < link.remaining_seconds <= 60

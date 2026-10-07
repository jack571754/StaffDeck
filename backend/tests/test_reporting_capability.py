"""Tests for ``app/reporting/spec.py`` and the ``report_generate`` capability.

The feature is judged by one *closed loop*:

    report_generate -> HTML file in the frame workspace -> artifact attached to the
    assistant message -> resolve_artifact_owner -> signed share link opens the page

Every hop already exists for agent-produced files; what is new is that a capability
now produces one. So the loop test drives the real capability through the real invoker
and then follows the artifact through exactly the resolver and token path the download
and view endpoints use — asserting on the capability's return value alone would not
catch a workspace-root or owner mismatch.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.core.artifact_owners import resolve_artifact_owner
from app.core.capability_discovery import ALWAYS_EXPANDED_CAPABILITIES
from app.core.capability_manifest import (
    RESERVED_HARNESS_CAPABILITY_NAMES,
    CapabilityManifestBuilder,
)
from app.core.harness_capability_invoker import HarnessCapabilityInvoker
from app.core.harness_session_cleanup import HARNESS_REPORTS_DIR
from app.db.models import ChatSession, HarnessTaskFrameRecord, Message, Tenant, User
from app.harness import ARTIFACT_OWNER_DEFAULT_KIND, open_harness_artifact
from app.reporting import ReportDocumentError, build_report_from_spec, mint_report_share_link
from app.security import artifact_share as artifact_share_mod

TENANT_ID = "tenant_demo"
SESSION_ID = "session_demo"
FRAME_ID = "task_demo"

_ROWS = [{"门店": "甲", "净销_万": 12.5}, {"门店": "乙", "净销_万": 8.25}]


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


def _invoker(db: Session) -> HarnessCapabilityInvoker:
    manifest = CapabilityManifestBuilder(db).build(TENANT_ID, None, None, None)
    session = db.get(ChatSession, SESSION_ID)
    assert session is not None
    return HarnessCapabilityInvoker(
        db=db,
        tenant_id=TENANT_ID,
        session=session,
        task_frame_id=FRAME_ID,
        model_config=None,
        manifest=manifest,
        active_skill=None,
        active_step_id=None,
        agent_id=None,
    )


# ------------------------------------------------------- registration


def test_report_generate_is_reserved_visible_and_authorized(db: Session) -> None:
    assert "report_generate" in RESERVED_HARNESS_CAPABILITY_NAMES
    # Always expanded: a model must see the schema without a capability_search round trip.
    assert "report_generate" in ALWAYS_EXPANDED_CAPABILITIES

    manifest = CapabilityManifestBuilder(db).build(TENANT_ID, None, None, None)
    descriptor = next(
        (item for item in manifest.available if item.name == "report_generate"),
        None,
    )
    assert descriptor is not None
    assert descriptor.available is True
    assert descriptor.kind == "internal"
    assert descriptor.metadata.get("side_effect") == "write"
    assert descriptor.input_schema["required"] == ["title"]


# ------------------------------------------------------- spec -> document


def test_spec_builds_report_with_summary_kpis_and_table() -> None:
    document = build_report_from_spec(
        {
            "title": "门店日报",
            "subtitle": "2026-09-30",
            "summary": "**结论**：两家门店均超目标。",
            "kpis": [
                {"label": "净销", "value": "12.50万", "delta": "+1.20万", "delta_tone": "up"},
                {"label": "  ", "value": "无标签的卡片被丢弃"},
            ],
            "columns": ["门店", "净销_万"],
            "rows": [{"净销_万": 12.5, "门店": "甲", "额外列": "x"}],
        },
        tenant_id=TENANT_ID,
    )

    assert document.meta.title == "门店日报"
    assert document.meta.subtitle == "2026-09-30"
    assert [block.kind for block in document.blocks] == ["markdown", "kpi_row", "table"]

    kpi_block = document.blocks[1]
    assert kpi_block.kpis is not None
    assert [kpi.label for kpi in kpi_block.kpis] == ["净销"]
    assert kpi_block.kpis[0].delta_tone == "up"

    dataset = document.datasets[0]
    # Explicit columns come first, then first-seen extras — declared order, not
    # dict-insertion order, which is what a report header must be stable against.
    assert dataset.columns == ["门店", "净销_万", "额外列"]
    assert dataset.rows[0]["净销_万"] == 12.5


def test_spec_without_rows_degrades_to_a_note_block() -> None:
    document = build_report_from_spec({"title": "空报告"})
    assert [block.kind for block in document.blocks] == ["note"]


@pytest.mark.parametrize(
    "spec",
    [
        {"title": "   "},
        {"title": "x", "rows": "not-a-list"},
        {"title": "x", "rendererx": "generic_table"},
        {"title": "x", "max_rows": 0},
        {"title": "x", "rows": [{"a": 1}], "kpis": [{"label": "a", "value": "1", "delta_tone": "sideways"}]},
        [("not", "a", "mapping")],
    ],
)
def test_spec_rejects_malformed_payloads(spec: object) -> None:
    with pytest.raises(ReportDocumentError):
        build_report_from_spec(spec)  # type: ignore[arg-type]


# ------------------------------------------------------- the closed loop


def test_report_generate_publishes_a_report_the_share_link_can_open(db: Session) -> None:
    _add_frame(db)
    invoker = _invoker(db)

    result = invoker.invoke(
        "report_generate",
        {
            "title": "门店日报",
            "summary": "数据来源：查询模板 qt_demo。",
            "kpis": [{"label": "净销", "value": "20.75万", "delta": "+1.20万", "delta_tone": "up"}],
            "columns": ["门店", "净销_万"],
            "rows": _ROWS,
        },
    )
    assert result["success"] is True, result
    published_path = result["data"]["path"]
    assert published_path.startswith(f"{HARNESS_REPORTS_DIR}/")
    assert result["data"]["size"] > 0
    assert "独立 HTML 报告已生成" in result["data"]["notice"]

    # 1) The AgentLoop forwards a capability's ``artifacts`` onto the assistant message
    #    (harness_agent.py), which is the "落库" hop — nothing else persists the report.
    artifact = result["artifacts"][0]
    assert artifact["type"] == "workspace_file"
    assert artifact["owner_kind"] == ARTIFACT_OWNER_DEFAULT_KIND
    assert artifact["owner_id"] == FRAME_ID
    db.add(
        Message(
            id="msg_report",
            tenant_id=TENANT_ID,
            session_id=SESSION_ID,
            role="assistant",
            content="报告已生成。",
            metadata_json={"harness_artifacts": [artifact]},
        )
    )
    db.commit()

    # 2) Same resolver as GET /api/chat/.../artifacts/{task_frame_id} and the view route.
    resolved = resolve_artifact_owner(
        db,
        owner_kind=ARTIFACT_OWNER_DEFAULT_KIND,
        tenant_id=TENANT_ID,
        session_id=SESSION_ID,
        owner_id=FRAME_ID,
        path=published_path,
    )
    assert resolved is not None

    opened = open_harness_artifact(resolved.workspace_root, published_path)
    try:
        # Digest/size must be read before iterating: draining ``iter_bytes`` closes the
        # handle, and a later ``sha256()`` then raises "already closed".
        assert opened.sha256() == artifact["sha256"]
        assert opened.size == artifact["size"]
        body = b"".join(opened.iter_bytes()).decode("utf-8")
    finally:
        opened.close()
    assert body.startswith("<!doctype html>")
    assert "门店日报" in body
    assert "20.75万" in body

    # 3) A minted share link round-trips to the same owner pair and path.
    link = mint_report_share_link(
        tenant_id=TENANT_ID,
        session_id=SESSION_ID,
        owner_kind=resolved.owner_kind,
        owner_id=resolved.owner_id,
        path=published_path,
    )
    payload = artifact_share_mod.decode_artifact_share_token(link.token)
    assert (payload["owner_kind"], payload["owner_id"]) == (
        ARTIFACT_OWNER_DEFAULT_KIND,
        FRAME_ID,
    )
    assert payload["path"] == published_path
    assert link.url.endswith(link.token)

    # An anonymous bearer (no user session) resolves the same file from the token alone.
    anonymous = resolve_artifact_owner(
        db,
        owner_kind=payload["owner_kind"],
        tenant_id=payload["tenant_id"],
        session_id=payload["session_id"],
        owner_id=payload["owner_id"],
        path=payload["path"],
    )
    assert anonymous is not None

    # 4) Workspace discovery would attach it even if the capability returned nothing:
    #    ``reports/*.html`` is a user-facing path, so the engine's own sweep finds it.
    assert published_path in {item["path"] for item in invoker.discover_artifacts()}

    # 5) The page lives in the frame's own reports directory, with no temp file left over.
    reports_dir = resolved.workspace_root / HARNESS_REPORTS_DIR
    assert reports_dir.is_dir()
    assert not [path for path in reports_dir.iterdir() if path.name.startswith(".tmp-")]


def test_report_generate_returns_a_failure_instead_of_raising(db: Session) -> None:
    invoker = _invoker(db)

    result = invoker.invoke("report_generate", {"title": "", "rows": []})

    assert result["success"] is False
    assert result["error"]["code"] == "REPORT_GENERATION_ERROR"

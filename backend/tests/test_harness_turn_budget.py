"""定时任务 turn 预算在 Harness 执行层的贯通（2026-09-22 生产事故回归）。

service 层为 interval 任务注入 turn_budget_seconds；engine 必须把它转成
conversation frame 的 wall-clock deadline（SOP 帧仍以技能 step 超时优先），
agent 超时收尾时对 conversation frame 报 TURN_BUDGET_TIMEOUT 而非 SOP_STEP_TIMEOUT。
"""

from __future__ import annotations

import time
from datetime import timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.core.capability_manifest import CapabilityManifest
from app.core.harness_agent import HarnessTaskAgent
from app.core.harness_v2_engine import HarnessV2Engine
from app.core.task_frame_store import planned_frame_from_record
from app.core.task_request_compiler import TaskExecutionResult, TaskRequirement
from app.db.models import (
    ChatSession,
    HarnessTaskFrameRecord,
    ModelConfig,
    Skill,
    utc_now,
)
from app.session.session_schema import ChatTurnRequest

TENANT = "tenant-budget"
OWNER = "lease-owner-budget"


def _db_engine():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    return engine


def _model_config() -> ModelConfig:
    return ModelConfig(
        id="model-budget",
        tenant_id=TENANT,
        name="预算模型",
        api_key_encrypted="test",
        model="test-model",
    )


def _request() -> ChatTurnRequest:
    return ChatTurnRequest(
        tenant_id=TENANT,
        session_id="sess-budget",
        client_turn_id="turn-budget",
        message="执行播报",
        channel="scheduled_task",
        turn_budget_seconds=480,
    )


def _seed_frame(db: Session, *, kind: str) -> HarnessTaskFrameRecord:
    db.add(ChatSession(id="sess-budget", tenant_id=TENANT))
    row = HarnessTaskFrameRecord(
        tenant_id=TENANT,
        session_id="sess-budget",
        source_turn_id="turn-budget",
        task_id="task-budget",
        kind=kind,
        status="queued",
        attempt_no=1,
        lease_owner=OWNER,
        lease_expires_at=utc_now() + timedelta(seconds=900),
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def _engine_for(db_engine) -> HarnessV2Engine:
    owner = SimpleNamespace(
        db=Session(db_engine),
        events=SimpleNamespace(record=lambda *args, **kwargs: None),
        _default_next_step=lambda skill, step_id: None,
        _apply_step_result=lambda *args, **kwargs: None,
        _finalize_execution_after_reply=lambda *args, **kwargs: "completed",
    )
    return HarnessV2Engine(owner)


def _capture_agent_run(monkeypatch) -> list[dict]:
    captured: list[dict] = []

    def fake_run(self, requirement, model_config, invoker, **kwargs):
        captured.append({"requirement": requirement, **kwargs})
        return TaskExecutionResult(
            task_frame_id=requirement.task_frame_id,
            status="completed",
            reply_fragment="ok",
            action_count=1,
        )

    monkeypatch.setattr(HarnessTaskAgent, "run", fake_run)
    return captured


@pytest.fixture
def workspace_env(tmp_path, monkeypatch):
    monkeypatch.setenv("ULTRARAG_DATA_DIR", str(tmp_path / "data"))


def test_conversation_frame_uses_turn_deadline_when_no_step_timeout(
    monkeypatch, workspace_env
) -> None:
    """conversation frame 无 step 超时时必须回退到 turn 预算 deadline。"""
    engine = _engine_for(_db_engine())
    captured = _capture_agent_run(monkeypatch)
    row = _seed_frame(engine.db, kind="conversation")
    session = engine.db.get(ChatSession, "sess-budget")

    engine._run_frame(
        _request(),
        session,
        row,
        planned_frame_from_record(row),
        None,
        _model_config(),
        [],
        [],
        32,
        turn_deadline_monotonic=time.monotonic() + 480,
    )

    assert captured, "task_agent.run 未被调用"
    assert captured[0]["step_deadline_monotonic"] is not None
    # conversation frame 的 deadline 直接沿用 turn 预算 deadline
    assert captured[0]["step_deadline_monotonic"] <= time.monotonic() + 480
    # 展示用预算应随 deadline 传给 agent 用于超时文案
    assert captured[0]["step_timeout_seconds"] == 480


def test_sop_frame_prefers_skill_step_timeout_over_turn_deadline(
    monkeypatch, workspace_env
) -> None:
    """SOP 帧配置了 step 超时时，必须优先于 turn 预算 deadline。"""
    engine = _engine_for(_db_engine())
    captured = _capture_agent_run(monkeypatch)
    engine.db.add(
        Skill(
            id="skill-budget",
            skill_id="skill-budget",
            tenant_id=TENANT,
            name="限时技能",
            status="published",
            content_json={"step_timeout_seconds": 120},
        )
    )
    engine.db.commit()
    row = _seed_frame(engine.db, kind="sop")
    row.skill_id = "skill-budget"
    engine.db.add(row)
    engine.db.commit()
    engine.db.refresh(row)
    session = engine.db.get(ChatSession, "sess-budget")
    skill = engine.db.get(Skill, "skill-budget")

    before = time.monotonic()
    engine._run_frame(
        _request(),
        session,
        row,
        planned_frame_from_record(row),
        skill,
        _model_config(),
        [],
        [],
        32,
        turn_deadline_monotonic=before + 480,
    )

    assert captured, "task_agent.run 未被调用"
    assert captured[0]["step_timeout_seconds"] == 120
    # SOP step 超时优先：deadline 基于 step_timeout 重新计算，而非沿用 turn deadline
    assert captured[0]["step_deadline_monotonic"] > before


def test_harness_task_agent_reports_turn_budget_timeout_for_conversation_frames() -> None:
    """conversation frame 超时收尾必须报 TURN_BUDGET_TIMEOUT 而非 SOP_STEP_TIMEOUT。"""
    result = HarnessTaskAgent().run(
        TaskRequirement(
            task_frame_id="task-budget",
            kind="conversation",
            goal="执行播报",
            requirements=["在预算内完成"],
            capability_manifest=CapabilityManifest(),
        ),
        _model_config(),
        lambda _name, _arguments: {"success": True},
        max_actions=3,
        step_deadline_monotonic=0,
        step_timeout_seconds=480,
    )

    assert result.status == "failed"
    assert result.error is not None
    assert result.error["code"] == "TURN_BUDGET_TIMEOUT"
    assert "480 秒" in result.error["message"]

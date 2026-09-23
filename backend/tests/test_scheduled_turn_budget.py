"""定时任务 turn 预算（2026-09-22 生产事故回归）。

interval 任务单轮执行若超过调度周期仍不收尾，会持续占用 worker、触发 forbid
跳过与租约争抢。service 层必须为 interval 任务注入总时长预算（周期的 0.8 倍，
下限 30s），由 Harness 执行层作为 turn deadline 强制收尾；非 interval 任务不注入。
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.db.models import AgentProfile, ScheduledTask, ScheduledTaskRun, utc_now
from app.scheduled_tasks.service import _execute_prepared_scheduled_task
from app.session.session_schema import ChatTurnRequest


@pytest.fixture
def db_session():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        yield session


_captured: list[ChatTurnRequest] = []


class _FakeAgentLoop:
    def __init__(self, db: Session) -> None:
        self.db = db

    def handle_turn_stream(self, request: ChatTurnRequest):
        _captured.append(request)
        return iter(())


def _seed(db: Session, *, task_id: str, schedule_type: str, schedule_json: dict):
    now = utc_now()
    db.add(
        AgentProfile(id="agent_sales", tenant_id="tenant_t", name="销售汇报员", status="active")
    )
    task = ScheduledTask(
        id=task_id,
        tenant_id="tenant_t",
        agent_id="agent_sales",
        created_by_user_id="user_1",
        title="实时销售播报",
        prompt="播报",
        schedule_type=schedule_type,
        schedule_json=schedule_json,
        status="active",
        created_at=now,
        updated_at=now,
    )
    run = ScheduledTaskRun(
        id=f"run_{task_id}",
        tenant_id="tenant_t",
        scheduled_task_id=task_id,
        agent_id="agent_sales",
        user_id="user_1",
        session_id="sess-1",
        scheduled_for=now,
        status="queued",
        created_at=now,
        updated_at=now,
    )
    db.add_all([task, run])
    db.commit()
    return task, run


def test_interval_task_injects_turn_budget(db_session: Session):
    """每 10 分钟的 interval 任务必须获得 480s（0.8 倍）turn 预算。"""
    task, run = _seed(
        db_session,
        task_id="task_iv",
        schedule_type="interval",
        schedule_json={"interval_seconds": 600},
    )
    _captured.clear()
    with patch("app.scheduled_tasks.service.AgentLoop", _FakeAgentLoop):
        _execute_prepared_scheduled_task(db_session, task, run, manual=False)

    assert _captured, "handle_turn_stream 未被调用"
    assert _captured[0].turn_budget_seconds == 480


def test_interval_task_budget_floors_at_30s(db_session: Session):
    """周期过短的 interval 任务预算下限为 30s，避免预算小于一次工具调用。"""
    task, run = _seed(
        db_session,
        task_id="task_iv2",
        schedule_type="interval",
        schedule_json={"interval_seconds": 15},
    )
    _captured.clear()
    with patch("app.scheduled_tasks.service.AgentLoop", _FakeAgentLoop):
        _execute_prepared_scheduled_task(db_session, task, run, manual=False)

    assert _captured, "handle_turn_stream 未被调用"
    assert _captured[0].turn_budget_seconds == 30


def test_non_interval_task_has_no_turn_budget(db_session: Session):
    """daily 等非 interval 任务无固定周期约束，不注入预算。"""
    task, run = _seed(
        db_session,
        task_id="task_daily",
        schedule_type="daily",
        schedule_json={"time": "09:00"},
    )
    _captured.clear()
    with patch("app.scheduled_tasks.service.AgentLoop", _FakeAgentLoop):
        _execute_prepared_scheduled_task(db_session, task, run, manual=False)

    assert _captured, "handle_turn_stream 未被调用"
    assert _captured[0].turn_budget_seconds is None

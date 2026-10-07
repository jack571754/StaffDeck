"""Tests for scheduled task orphan reaper and auto-healing."""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.db.models import (
    AgentProfile,
    ChatSession,
    Message,
    ScheduledTask,
    ScheduledTaskRun,
    Tenant,
    User,
    new_id,
    utc_now,
)
from app.scheduled_tasks.service import (
    TASK_RUN_STALE_SECONDS,
    _prepare_scheduled_task_run,
    reap_stale_scheduled_task_runs,
)


def _test_session() -> Session:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    return Session(engine)


def _seed(db: Session) -> tuple[Tenant, User, AgentProfile]:
    tenant = Tenant(id="tenant_demo", name="Demo")
    user = User(
        id="user_demo",
        tenant_id="tenant_demo",
        username="demo",
        display_name="演示用户",
        role="admin",
        password_hash="test-hash",
    )
    agent = AgentProfile(
        id="agent_demo",
        tenant_id="tenant_demo",
        name="测试播报员工",
        is_overall=False,
        status="active",
    )
    db.add(tenant)
    db.add(user)
    db.add(agent)
    db.commit()
    return tenant, user, agent


def test_reap_stale_scheduled_task_runs() -> None:
    db = _test_session()
    tenant, user, agent = _seed(db)

    task = ScheduledTask(
        id="sched_test_1",
        tenant_id=tenant.id,
        agent_id=agent.id,
        created_by_user_id=user.id,
        title="测试定时任务",
        prompt="测试提示词",
        status="active",
        concurrency_policy="forbid",
        lease_owner="worker-1",
        lease_until=utc_now() + timedelta(minutes=5),
    )
    db.add(task)
    db.commit()

    old_time = utc_now() - timedelta(seconds=TASK_RUN_STALE_SECONDS + 100)
    session = ChatSession(
        id=new_id("session"),
        tenant_id=tenant.id,
        user_id=user.id,
        agent_id=agent.id,
        title="自动任务测试会话",
        status="active",
    )
    db.add(session)
    db.commit()

    stale_run = ScheduledTaskRun(
        id="schedrun_stale",
        scheduled_task_id=task.id,
        tenant_id=tenant.id,
        agent_id=agent.id,
        user_id=user.id,
        status="running",
        scheduled_for=old_time,
        started_at=old_time,
        created_at=old_time,
        updated_at=old_time,
        session_id=session.id,
    )
    db.add(stale_run)

    # Also add a fresh running run
    fresh_time = utc_now() - timedelta(seconds=30)
    fresh_run = ScheduledTaskRun(
        id="schedrun_fresh",
        scheduled_task_id=task.id,
        tenant_id=tenant.id,
        agent_id=agent.id,
        user_id=user.id,
        status="running",
        scheduled_for=fresh_time,
        started_at=fresh_time,
        created_at=fresh_time,
        updated_at=fresh_time,
    )
    db.add(fresh_run)
    db.commit()

    # Reap
    reaped = reap_stale_scheduled_task_runs(db)
    assert len(reaped) == 1
    assert reaped[0].id == "schedrun_stale"
    assert reaped[0].status == "failed"
    assert "超时或异常中断" in (reaped[0].error or "")
    assert reaped[0].finished_at is not None

    # Verify task lease was cleared
    db.refresh(task)
    assert task.lease_owner is None
    assert task.lease_until is None

    # Verify session received assistant message explaining the abort
    messages = db.exec(
        select(Message).where(Message.session_id == session.id).order_by(Message.created_at)
    ).all()
    assert len(messages) >= 1
    asst_msg = next((m for m in messages if m.role == "assistant"), None)
    assert asst_msg is not None
    assert "异常中断" in asst_msg.content

    # Verify fresh run was NOT reaped
    db.refresh(fresh_run)
    assert fresh_run.status == "running"


def test_prepare_run_auto_heals_stale_forbid_run() -> None:
    db = _test_session()
    tenant, user, agent = _seed(db)

    task = ScheduledTask(
        id="sched_forbid_test",
        tenant_id=tenant.id,
        agent_id=agent.id,
        created_by_user_id=user.id,
        title="测试每30分钟任务",
        prompt="测试提示词",
        status="active",
        concurrency_policy="forbid",
    )
    db.add(task)
    db.commit()

    old_time = utc_now() - timedelta(minutes=35)
    stale_run = ScheduledTaskRun(
        id="schedrun_old",
        scheduled_task_id=task.id,
        tenant_id=tenant.id,
        agent_id=agent.id,
        user_id=user.id,
        status="running",
        scheduled_for=old_time,
        started_at=old_time,
        created_at=old_time,
        updated_at=old_time,
    )
    db.add(stale_run)
    db.commit()

    # Now a new run triggers!
    new_scheduled_for = utc_now()
    new_run = _prepare_scheduled_task_run(db, task, new_scheduled_for, manual=False)

    # It must NOT be skipped!
    assert new_run.status == "running"
    assert new_run.id != "schedrun_old"

    # And the old run must have been healed to failed
    db.refresh(stale_run)
    assert stale_run.status == "failed"


def test_pipeline_session_immediately_has_user_message() -> None:
    from app.scheduled_tasks.pipeline import execute_pipeline_scheduled_task

    db = _test_session()
    tenant, user, agent = _seed(db)

    task = ScheduledTask(
        id="sched_pipe_test",
        tenant_id=tenant.id,
        agent_id=agent.id,
        created_by_user_id=user.id,
        title="测试流水线任务",
        prompt="测试提示词内容",
        status="active",
        execution_mode="pipeline",
        pipeline_steps_json=[],
    )
    db.add(task)
    db.commit()

    run = _prepare_scheduled_task_run(db, task, utc_now(), manual=False)
    assert run.session_id is not None

    execute_pipeline_scheduled_task(db, task, run, manual=False)

    msgs = db.exec(
        select(Message).where(Message.session_id == run.session_id).order_by(Message.created_at)
    ).all()
    assert len(msgs) == 2
    assert msgs[0].role == "user"
    assert "测试提示词内容" in msgs[0].content
    assert msgs[1].role == "assistant"
    assert "流水线执行成功" in msgs[1].content


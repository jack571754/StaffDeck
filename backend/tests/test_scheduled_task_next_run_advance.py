"""Tests for scheduled task next_run_at advancement, lease release, and auto-recovery."""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.db.models import (
    AgentProfile,
    ScheduledTask,
    ScheduledTaskRun,
    Tenant,
    User,
    utc_now,
)
from app.scheduled_tasks.schema import ScheduledTaskUpdateRequest
from app.scheduled_tasks.service import (
    _finish_task_schedule,
    execute_scheduled_task,
    update_scheduled_task,
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


def test_execute_scheduled_task_clears_lease_when_skipped_by_forbid() -> None:
    db = _test_session()
    tenant, user, agent = _seed(db)

    now = utc_now()
    task = ScheduledTask(
        id="sched_forbid_test",
        tenant_id=tenant.id,
        agent_id=agent.id,
        created_by_user_id=user.id,
        title="价格巡检任务",
        prompt="巡检价格",
        schedule_type="interval",
        schedule_json={"interval_minutes": 10, "interval_seconds": 600},
        timezone="Asia/Shanghai",
        status="active",
        concurrency_policy="forbid",
        next_run_at=now,
        lease_owner="worker-1",
        lease_until=now + timedelta(seconds=900),
    )
    db.add(task)

    # An active running run that causes the next run to be skipped
    running_run = ScheduledTaskRun(
        id="run_active_prev",
        scheduled_task_id=task.id,
        tenant_id=tenant.id,
        agent_id=agent.id,
        user_id=user.id,
        status="running",
        scheduled_for=now - timedelta(minutes=2),
        started_at=now - timedelta(minutes=2),
    )
    db.add(running_run)
    db.commit()

    run = execute_scheduled_task(db, task, scheduled_for=now, manual=False)
    assert run.status == "skipped"

    db.refresh(task)
    # Crucial assertion: lease MUST be released immediately, not held for 15 minutes!
    assert task.lease_owner is None
    assert task.lease_until is None
    # Next run MUST be advanced into the future
    assert task.next_run_at is not None
    assert task.next_run_at > now


def test_execute_scheduled_task_clears_lease_and_advances_next_run_on_terminal_existing() -> None:
    db = _test_session()
    tenant, user, agent = _seed(db)

    now = utc_now()
    task = ScheduledTask(
        id="sched_existing_test",
        tenant_id=tenant.id,
        agent_id=agent.id,
        created_by_user_id=user.id,
        title="报表推送",
        prompt="推送报表",
        schedule_type="interval",
        schedule_json={"interval_minutes": 10, "interval_seconds": 600},
        timezone="Asia/Shanghai",
        status="active",
        concurrency_policy="forbid",
        next_run_at=now,
        lease_owner="worker-1",
        lease_until=now + timedelta(seconds=900),
    )
    db.add(task)

    # Existing completed run for this exact scheduled_for
    completed_run = ScheduledTaskRun(
        id="run_completed_same_time",
        scheduled_task_id=task.id,
        tenant_id=tenant.id,
        agent_id=agent.id,
        user_id=user.id,
        status="succeeded",
        scheduled_for=now,
        started_at=now,
        finished_at=now,
    )
    db.add(completed_run)
    db.commit()

    run = execute_scheduled_task(db, task, scheduled_for=now, manual=False)
    assert run.id == completed_run.id

    db.refresh(task)
    # Must clear lease
    assert task.lease_owner is None
    assert task.lease_until is None
    # Must advance next_run_at to the future
    assert task.next_run_at is not None
    assert task.next_run_at > now


def test_manual_run_advances_expired_next_run_at() -> None:
    db = _test_session()
    tenant, user, agent = _seed(db)

    past = utc_now() - timedelta(hours=2)
    task = ScheduledTask(
        id="sched_manual_test",
        tenant_id=tenant.id,
        agent_id=agent.id,
        created_by_user_id=user.id,
        title="每日播报",
        prompt="播报销售",
        schedule_type="daily",
        schedule_json={"time": "09:00", "times": ["09:00"]},
        timezone="Asia/Shanghai",
        status="active",
        next_run_at=past,  # Expired in past
        run_count=3,
    )
    db.add(task)
    db.commit()

    # Manual run completes
    _finish_task_schedule(db, task, scheduled_for=past, status="succeeded", manual=True)
    db.commit()
    db.refresh(task)

    # Manual run does NOT increment plan run_count
    assert task.run_count == 3
    # Manual run DOES advance expired next_run_at so it doesn't get stuck in the past
    assert task.next_run_at is not None
    assert task.next_run_at > utc_now()


def test_update_scheduled_task_clears_stale_lease() -> None:
    db = _test_session()
    tenant, user, agent = _seed(db)

    now = utc_now()
    task = ScheduledTask(
        id="sched_update_lease_test",
        tenant_id=tenant.id,
        agent_id=agent.id,
        created_by_user_id=user.id,
        title="旧任务",
        prompt="旧提示词",
        schedule_type="daily",
        schedule_json={"time": "09:00", "times": ["09:00"]},
        timezone="Asia/Shanghai",
        status="active",
        lease_owner="crashed-worker",
        lease_until=now + timedelta(seconds=600),
    )
    db.add(task)
    db.commit()

    update_req = ScheduledTaskUpdateRequest(
        tenant_id=tenant.id,
        title="新任务名称",
    )
    updated = update_scheduled_task(db, task, update_req, user)

    assert updated.title == "新任务名称"
    assert updated.lease_owner is None
    assert updated.lease_until is None
    assert updated.next_run_at is not None

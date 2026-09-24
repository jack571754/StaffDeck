from datetime import datetime
from zoneinfo import ZoneInfo
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.db.models import AgentProfile, ScheduledTask, Tenant, User
from app.scheduled_tasks.schema import ScheduledTaskCreateRequest
from app.scheduled_tasks.service import (
    build_rrule,
    compute_next_run_at,
    create_scheduled_task,
    normalize_schedule,
)


def test_normalize_schedule_daily_multi_times() -> None:
    # Multiple unsorted with duplicates
    norm = normalize_schedule("daily", {"times": ["20:00", "09:00", "13:00", "09:00"]}, "Asia/Shanghai")
    assert norm == {
        "times": ["09:00", "13:00", "20:00"],
        "time": "09:00",
    }

    # Backward compatibility with single time
    norm_single = normalize_schedule("daily", {"time": "14:30"}, "Asia/Shanghai")
    assert norm_single == {
        "times": ["14:30"],
        "time": "14:30",
    }


def test_build_rrule_daily_multi_times() -> None:
    # Same minute
    rrule_same_minute = build_rrule("daily", {"times": ["09:00", "13:00", "20:00"]})
    assert rrule_same_minute == "FREQ=DAILY;BYHOUR=9,13,20;BYMINUTE=0;BYSECOND=0"

    # Different minutes
    rrule_diff = build_rrule("daily", {"times": ["09:00", "13:30", "20:15"]})
    assert rrule_diff == "FREQ=DAILY;BYHOUR=9;BYMINUTE=0;BYSECOND=0"


def test_compute_next_run_at_daily_multi_times_cycle() -> None:
    task = ScheduledTask(
        id="task_test_daily",
        tenant_id="tenant_demo",
        agent_id="agent_demo",
        title="多时段任务",
        prompt="测试",
        schedule_type="daily",
        schedule_json={"times": ["09:00", "13:00", "20:00"], "time": "09:00"},
        timezone="Asia/Shanghai",
        status="active",
    )

    tz = ZoneInfo("Asia/Shanghai")

    # 1. Before 09:00 (e.g. 08:30) -> next run should be 09:00 today
    t1 = datetime(2026, 9, 24, 8, 30, tzinfo=tz)
    next_1 = compute_next_run_at(task, after=t1)
    assert next_1 == datetime(2026, 9, 24, 1, 0)  # 09:00 Shanghai is 01:00 UTC

    # 2. Right after 09:00 run (e.g. 09:00:05) -> next run should be 13:00 today
    t2 = datetime(2026, 9, 24, 9, 0, 5, tzinfo=tz)
    next_2 = compute_next_run_at(task, after=t2)
    assert next_2 == datetime(2026, 9, 24, 5, 0)  # 13:00 Shanghai is 05:00 UTC

    # 3. Right after 13:00 run (e.g. 13:05) -> next run should be 20:00 today
    t3 = datetime(2026, 9, 24, 13, 5, tzinfo=tz)
    next_3 = compute_next_run_at(task, after=t3)
    assert next_3 == datetime(2026, 9, 24, 12, 0)  # 20:00 Shanghai is 12:00 UTC

    # 4. After 20:00 run (e.g. 21:00) -> next run should be 09:00 tomorrow (Sept 25)
    t4 = datetime(2026, 9, 24, 21, 0, tzinfo=tz)
    next_4 = compute_next_run_at(task, after=t4)
    assert next_4 == datetime(2026, 9, 25, 1, 0)  # 2026-09-25 09:00 Shanghai


def test_create_scheduled_task_with_daily_multi_times() -> None:
    with _test_session() as db:
        db.add(Tenant(id="tenant_demo", name="Demo"))
        db.add(AgentProfile(id="agent_demo", tenant_id="tenant_demo", name="客服", is_overall=False))
        db.add(
            User(
                id="user_demo",
                tenant_id="tenant_demo",
                username="demo",
                display_name="演示用户",
                role="admin",
                password_hash="test-hash",
            )
        )
        db.commit()

        request = ScheduledTaskCreateRequest(
            tenant_id="tenant_demo",
            agent_id="agent_demo",
            title="每日多时段战报",
            prompt="播报实时销售数据",
            schedule_type="daily",
            schedule={"times": ["09:00", "13:00", "20:00"]},
        )
        row = create_scheduled_task(db, request, db.get(User, "user_demo"))

        assert row.schedule_type == "daily"
        assert row.schedule_json == {"times": ["09:00", "13:00", "20:00"], "time": "09:00"}
        assert row.rrule == "FREQ=DAILY;BYHOUR=9,13,20;BYMINUTE=0;BYSECOND=0"
        assert row.next_run_at is not None


def _test_session() -> Session:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    return Session(engine)

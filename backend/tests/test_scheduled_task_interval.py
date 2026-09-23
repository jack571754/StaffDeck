from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.db.models import AgentProfile, Tenant, User
from app.scheduled_tasks.schema import ScheduledTaskCreateRequest
from app.scheduled_tasks.service import compute_next_run_at, create_scheduled_task


def test_create_request_accepts_interval_schedule_type() -> None:
    request = ScheduledTaskCreateRequest(
        tenant_id="tenant_demo",
        agent_id="agent_demo",
        title="间隔轮询任务",
        prompt="每隔 5 分钟检查一次",
        schedule_type="interval",
        schedule={"interval_minutes": 5},
    )

    assert request.schedule_type == "interval"


def test_create_scheduled_task_with_interval_computes_next_run() -> None:
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
            title="间隔轮询任务",
            prompt="每隔 5 分钟检查一次",
            schedule_type="interval",
            schedule={"interval_minutes": 5},
        )
        row = create_scheduled_task(db, request, db.get(User, "user_demo"))

        assert row.schedule_type == "interval"
        assert row.schedule_json == {"interval_minutes": 5, "interval_seconds": 300}
        assert row.rrule == "FREQ=MINUTELY;INTERVAL=5"
        assert compute_next_run_at(row) is not None


def _test_session() -> Session:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    return Session(engine)

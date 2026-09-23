from __future__ import annotations

from datetime import timedelta

from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.core.harness_agent import HarnessTaskAgent
from app.core.task_request_compiler import CapabilityManifest, TaskRequirement
from app.db.models import (
    AgentProfile,
    ModelConfig,
    ScheduledTask,
    ScheduledTaskRun,
    Tenant,
    User,
    utc_now,
)
from app.llm import LLMError
from app.scheduled_tasks.service import execute_scheduled_task


def _test_session() -> Session:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    return Session(engine)


def test_execute_scheduled_task_advances_next_run_when_existing_terminal_run_present() -> None:
    with _test_session() as db:
        now = utc_now()
        past_sched = now - timedelta(hours=2)

        tenant = Tenant(id="tenant_demo", name="Demo")
        user = User(
            id="user_demo",
            tenant_id="tenant_demo",
            username="demo",
            display_name="Demo",
            role="admin",
            password_hash="hash",
        )
        agent = AgentProfile(
            id="agent_demo",
            tenant_id="tenant_demo",
            name="Sales",
            is_overall=False,
            status="active",
        )
        task = ScheduledTask(
            id="sched_test_1",
            tenant_id="tenant_demo",
            agent_id="agent_demo",
            created_by_user_id="user_demo",
            title="实时销售播报",
            prompt="播报销售",
            schedule_type="interval",
            schedule_json={"interval_minutes": 10, "interval_seconds": 600},
            rrule="FREQ=MINUTELY;INTERVAL=10",
            misfire_policy="coalesce",
            concurrency_policy="forbid",
            status="active",
            next_run_at=past_sched,
            last_status="failed",
            run_count=5,
            lease_owner="worker_test:1",
            lease_until=now + timedelta(seconds=300),
        )
        existing_run = ScheduledTaskRun(
            id="run_old_1",
            tenant_id="tenant_demo",
            scheduled_task_id="sched_test_1",
            agent_id="agent_demo",
            user_id="user_demo",
            scheduled_for=past_sched,
            status="failed",
            error="测试历史失败",
            started_at=past_sched,
            finished_at=past_sched + timedelta(minutes=2),
        )
        db.add(tenant)
        db.add(user)
        db.add(agent)
        db.add(task)
        db.add(existing_run)
        db.commit()

        run = execute_scheduled_task(db, task, scheduled_for=task.next_run_at, manual=False)

        assert run.id == "run_old_1"
        db.refresh(task)
        # Verify next_run_at was advanced into the future!
        assert task.next_run_at is not None
        assert task.next_run_at > now
        # Verify lease was cleared
        assert task.lease_owner is None
        assert task.lease_until is None


def test_harness_agent_reports_llm_call_failed_on_llm_error(monkeypatch) -> None:
    class FailingLLMClient:
        def __init__(self, _model_config: ModelConfig):
            pass

        def generate_json_sequence(self, *args, **kwargs):
            raise LLMError("Request timed out or interrupted.", code="MODEL_TIMEOUT")

    import app.core.harness_agent as harness_agent_module

    monkeypatch.setattr(harness_agent_module, "LLMClient", FailingLLMClient)

    requirement = TaskRequirement(
        task_frame_id="task-llm-fail",
        kind="conversation",
        goal="测试",
        source_user_message="测试",
        capability_manifest=CapabilityManifest(),
    )
    model_config = ModelConfig(
        id="model_test",
        tenant_id="tenant_demo",
        name="test",
        provider="openai_compatible",
        model="test-model",
    )

    result = HarnessTaskAgent().run(
        requirement,
        model_config,
        lambda name, arguments: {"success": True},
        max_actions=2,
    )

    assert result.status == "failed"
    assert "模型服务调用异常（MODEL_TIMEOUT）" in (result.reply_fragment or "")
    assert result.error is not None
    assert result.error.get("code") == "LLM_CALL_FAILED"

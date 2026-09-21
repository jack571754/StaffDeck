from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.api import chat as chat_api
from app.db.models import AgentEvent, ChatSession, HarnessTurnRecord, Message
from app.scheduled_tasks.schema import ScheduledTaskDraftRead
from app.session.session_schema import ChatTurnRequest


def test_scheduled_task_shortcut_replays_same_turn_without_duplicate_side_effects(monkeypatch) -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    draft = ScheduledTaskDraftRead(
        should_create=True,
        tenant_id="tenant-demo",
        agent_id="agent-demo",
        title="每日提醒",
        prompt="每天提醒我",
        schedule_type="daily",
        schedule={"time": "09:00"},
    )
    monkeypatch.setattr(chat_api, "detect_scheduled_task_draft", lambda *args, **kwargs: draft)
    request = ChatTurnRequest(
        tenant_id="tenant-demo",
        session_id="session-demo",
        agent_id="agent-demo",
        user_id="user-demo",
        client_turn_id="scheduled-client-turn",
        interaction_mode="scheduled_task",
        message="每天九点提醒我",
    )

    with Session(engine) as db:
        chat_session = ChatSession(
            id="session-demo",
            tenant_id="tenant-demo",
            agent_id="agent-demo",
            user_id="user-demo",
        )
        db.add(chat_session)
        db.commit()

        first = chat_api._maybe_handle_scheduled_task_request(db, request, chat_session)
        second = chat_api._maybe_handle_scheduled_task_request(db, request, chat_session)

        assert first is not None and second is not None
        assert second[0].reply == first[0].reply
        assert len(db.exec(select(HarnessTurnRecord)).all()) == 1
        assert len(db.exec(select(Message)).all()) == 2
        assert len(
            db.exec(
                select(AgentEvent).where(AgentEvent.event_type == "assistant_message_created")
            ).all()
        ) == 1


def test_scheduled_task_shortcut_honors_pre_message_cancellation(monkeypatch) -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    detector_calls = 0

    def detect(*args, **kwargs):
        nonlocal detector_calls
        detector_calls += 1
        raise AssertionError("cancelled shortcut must not run the detector")

    monkeypatch.setattr(chat_api, "detect_scheduled_task_draft", detect)
    request = ChatTurnRequest(
        tenant_id="tenant-demo",
        session_id="session-cancelled-shortcut",
        agent_id="agent-demo",
        user_id="user-demo",
        client_turn_id="scheduled-cancelled-turn",
        interaction_mode="scheduled_task",
        message="每天九点提醒我",
    )

    with Session(engine) as db:
        chat_session = ChatSession(
            id=request.session_id,
            tenant_id=request.tenant_id,
            agent_id=request.agent_id,
            user_id=request.user_id,
        )
        db.add(chat_session)
        db.add(
            AgentEvent(
                tenant_id=request.tenant_id,
                session_id=request.session_id,
                event_type="stream_cancelled",
                payload_json={
                    "turn_id": request.client_turn_id,
                    "user_message_id": request.client_turn_id,
                    "client_turn_id": request.client_turn_id,
                },
            )
        )
        db.commit()

        assert chat_api._maybe_handle_scheduled_task_request(db, request, chat_session) is None
        assert detector_calls == 0
        assert db.exec(select(HarnessTurnRecord)).all() == []
        assert db.exec(select(Message)).all() == []


def test_prepare_scheduled_task_run_recovers_orphan_when_forbid() -> None:
    from datetime import timedelta

    from app.db.models import ScheduledTask, ScheduledTaskRun, utc_now
    from app.scheduled_tasks.service import _prepare_scheduled_task_run

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)

    with Session(engine) as db:
        now = utc_now()
        task = ScheduledTask(
            id="task-orphan-test",
            tenant_id="tenant-demo",
            agent_id="agent-demo",
            created_by_user_id="user-demo",
            title="测试任务",
            prompt="测试提示词",
            concurrency_policy="forbid",
            lease_until=now - timedelta(minutes=5),  # expired lease
        )
        db.add(task)
        db.commit()

        # Previous run stuck in running without active turn.
        # 启动时间需超过 ORPHAN_RUN_GRACE_SECONDS（15 分钟）才会被判僵尸。
        stuck_run = ScheduledTaskRun(
            id="stuck-run-id",
            tenant_id="tenant-demo",
            scheduled_task_id=task.id,
            agent_id=task.agent_id,
            user_id=task.created_by_user_id,
            scheduled_for=now - timedelta(minutes=20),
            status="running",
            started_at=now - timedelta(minutes=20),
            updated_at=now - timedelta(minutes=20),
        )
        db.add(stuck_run)
        db.commit()

        new_scheduled_for = now
        new_run = _prepare_scheduled_task_run(db, task, new_scheduled_for, manual=False)

        # Stuck run should have been transitioned to failed
        db.refresh(stuck_run)
        assert stuck_run.status == "failed"
        assert "异常中断" in (stuck_run.error or "")

        # New run should be allowed to run
        assert new_run.status == "running"
        assert new_run.id != stuck_run.id


def test_prepare_scheduled_task_run_recovers_orphan_despite_fresh_worker_lease() -> None:
    """worker 主路径：due_scheduled_tasks 刚把 task lease 续期，僵尸 run 仍必须能被回收。

    回归背景：孤儿判定若依赖 task.lease_until < now，在 worker 同一次调度内
    （先 claim 续 lease、再判孤儿）恒为 False，僵尸 run 永远只被 forbid 跳过。
    """
    from datetime import timedelta

    from app.db.models import ScheduledTask, ScheduledTaskRun, utc_now
    from app.scheduled_tasks.service import LEASE_SECONDS, _prepare_scheduled_task_run

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)

    with Session(engine) as db:
        now = utc_now()
        task = ScheduledTask(
            id="task-fresh-lease-test",
            tenant_id="tenant-demo",
            agent_id="agent-demo",
            created_by_user_id="user-demo",
            title="测试任务",
            prompt="测试提示词",
            concurrency_policy="forbid",
            # 模拟 worker 刚 claim：lease 远未过期
            lease_until=now + timedelta(seconds=LEASE_SECONDS),
        )
        db.add(task)
        db.commit()

        # 僵尸 run：启动已久、长时间无进展（updated_at 陈旧）、会话无活跃 turn
        stuck_run = ScheduledTaskRun(
            id="stuck-run-fresh-lease",
            tenant_id="tenant-demo",
            scheduled_task_id=task.id,
            agent_id=task.agent_id,
            user_id=task.created_by_user_id,
            scheduled_for=now - timedelta(minutes=30),
            status="running",
            started_at=now - timedelta(minutes=30),
            updated_at=now - timedelta(minutes=30),
        )
        db.add(stuck_run)
        db.commit()

        new_run = _prepare_scheduled_task_run(db, task, now, manual=False)

        db.refresh(stuck_run)
        assert stuck_run.status == "failed"
        assert "异常中断" in (stuck_run.error or "")
        assert new_run.status == "running"
        assert new_run.id != stuck_run.id


def test_prepare_scheduled_task_run_keeps_live_run_with_active_turn() -> None:
    """活着的 agent 执行（turn 仍 started）即使 run 时间戳陈旧也不得被回收。"""
    from datetime import timedelta

    from app.db.models import ChatSession, ScheduledTask, ScheduledTaskRun, utc_now
    from app.scheduled_tasks.service import LEASE_SECONDS, _prepare_scheduled_task_run

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)

    with Session(engine) as db:
        now = utc_now()
        task = ScheduledTask(
            id="task-live-run-test",
            tenant_id="tenant-demo",
            agent_id="agent-demo",
            created_by_user_id="user-demo",
            title="测试任务",
            prompt="测试提示词",
            concurrency_policy="forbid",
            lease_until=now + timedelta(seconds=LEASE_SECONDS),
        )
        db.add(task)
        db.commit()

        session = ChatSession(
            id="session-live-run-test",
            tenant_id="tenant-demo",
            agent_id="agent-demo",
            user_id="user-demo",
        )
        db.add(session)
        live_run = ScheduledTaskRun(
            id="live-run-id",
            tenant_id="tenant-demo",
            scheduled_task_id=task.id,
            agent_id=task.agent_id,
            user_id=task.created_by_user_id,
            scheduled_for=now - timedelta(minutes=30),
            status="running",
            started_at=now - timedelta(minutes=30),
            updated_at=now - timedelta(minutes=30),
            session_id=session.id,
        )
        db.add(live_run)
        db.add(
            HarnessTurnRecord(
                tenant_id="tenant-demo",
                session_id=session.id,
                client_turn_id="live-run-id",
                request_digest="sha256:test-live-run",
                lease_owner="hturnlease-test-live-run",
                status="started",
                started_at=now - timedelta(minutes=25),
                lease_expires_at=now + timedelta(minutes=5),
            )
        )
        db.commit()

        new_run = _prepare_scheduled_task_run(db, task, now, manual=False)

        db.refresh(live_run)
        assert live_run.status == "running"
        assert new_run.status == "skipped"
        assert new_run.id != live_run.id


def test_prepare_scheduled_task_run_keeps_recent_run_without_turn() -> None:
    """刚创建、还没来得及建 turn 的 run（宽限期内）不得被回收。"""
    from datetime import timedelta

    from app.db.models import ScheduledTask, ScheduledTaskRun, utc_now
    from app.scheduled_tasks.service import _prepare_scheduled_task_run

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)

    with Session(engine) as db:
        now = utc_now()
        task = ScheduledTask(
            id="task-recent-run-test",
            tenant_id="tenant-demo",
            agent_id="agent-demo",
            created_by_user_id="user-demo",
            title="测试任务",
            prompt="测试提示词",
            concurrency_policy="forbid",
        )
        db.add(task)
        db.commit()

        recent_run = ScheduledTaskRun(
            id="recent-run-id",
            tenant_id="tenant-demo",
            scheduled_task_id=task.id,
            agent_id=task.agent_id,
            user_id=task.created_by_user_id,
            scheduled_for=now - timedelta(seconds=30),
            status="running",
            started_at=now - timedelta(seconds=30),
            updated_at=now - timedelta(seconds=30),
        )
        db.add(recent_run)
        db.commit()

        new_run = _prepare_scheduled_task_run(db, task, now, manual=False)

        db.refresh(recent_run)
        assert recent_run.status == "running"
        assert new_run.status == "skipped"


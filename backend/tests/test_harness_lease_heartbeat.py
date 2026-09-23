from __future__ import annotations

import time
from datetime import timedelta

from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.core.harness_lease_heartbeat import ExecutionLeaseHeartbeat
from app.core.harness_session_lease import HarnessSessionLeaseStore
from app.core.task_frame_store import TaskFrameClaimConflict
from app.db.models import (
    ChatSession,
    HarnessInvocationRecord,
    HarnessRunRecord,
    HarnessTaskFrameRecord,
    HarnessTurnRecord,
    utc_now,
)

TENANT = "tenant-demo"
SESSION_ID = "session-1"
OWNER = "hsleaseowner-demo"
ATTEMPT = 1


def _engine():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    return engine


def _seed(db: Session, *, lease_seconds: int) -> str:
    expires_at = utc_now() + timedelta(seconds=lease_seconds)
    db.add(ChatSession(id=SESSION_ID, tenant_id=TENANT))
    db.add(
        HarnessTurnRecord(
            id="turn-1",
            tenant_id=TENANT,
            session_id=SESSION_ID,
            client_turn_id="turn-1",
            request_digest="digest-1",
            status="started",
            lease_owner=OWNER,
            lease_expires_at=expires_at,
        )
    )
    frame = HarnessTaskFrameRecord(
        tenant_id=TENANT,
        session_id=SESSION_ID,
        source_turn_id="turn-1",
        task_id="task-1",
        status="running",
        attempt_no=ATTEMPT,
        lease_owner=OWNER,
        lease_expires_at=expires_at,
    )
    db.add(frame)
    db.flush()
    db.add(
        HarnessRunRecord(
            tenant_id=TENANT,
            session_id=SESSION_ID,
            task_frame_record_id=frame.id,
            task_id="task-1",
            source_turn_id="turn-1",
            status="running",
            attempt_no=ATTEMPT,
            lease_owner=OWNER,
            lease_expires_at=expires_at,
        )
    )
    db.commit()
    return frame.id


def _heartbeat(engine, frame_id: str, **overrides) -> ExecutionLeaseHeartbeat:
    with Session(engine) as db:
        store = HarnessSessionLeaseStore(db)
        lease = store.acquire(db.get(ChatSession, SESSION_ID))
    kwargs: dict = {
        "session_lease": lease,
        "turn_record_id": "turn-1",
        "frame_record_id": frame_id,
        "lease_owner": OWNER,
        "attempt_no": ATTEMPT,
        "db_engine": engine,
    }
    kwargs.update(overrides)
    return ExecutionLeaseHeartbeat(**kwargs)


def _leases(engine, frame_id: str) -> dict[str, object]:
    with Session(engine) as db:
        return {
            "frame": db.get(HarnessTaskFrameRecord, frame_id).lease_expires_at,
            "run": db.exec(
                select(HarnessRunRecord).where(
                    HarnessRunRecord.task_frame_record_id == frame_id
                )
            ).first().lease_expires_at,
            "turn": db.exec(
                select(HarnessTurnRecord).where(
                    HarnessTurnRecord.client_turn_id == "turn-1"
                )
            ).first().lease_expires_at,
        }


def test_heartbeat_renews_frame_run_turn_and_session_leases() -> None:
    engine = _engine()
    with Session(engine) as db:
        frame_id = _seed(db, lease_seconds=30)
    before = _leases(engine, frame_id)

    _heartbeat(engine, frame_id).renew_once()

    after = _leases(engine, frame_id)
    for name, before_value in before.items():
        after_value = after[name]
        assert after_value is not None
        assert after_value > before_value, name


def test_heartbeat_loop_exits_after_fence() -> None:
    engine = _engine()
    with Session(engine) as db:
        frame_id = _seed(db, lease_seconds=30)
        # 模拟租约已过期：下一次 renew_once 必须被 fence。
        frame = db.get(HarnessTaskFrameRecord, frame_id)
        frame.lease_expires_at = utc_now() - timedelta(seconds=1)
        db.add(frame)
        db.commit()

    heartbeat = _heartbeat(engine, frame_id, interval_seconds=0.05)
    heartbeat.start()
    deadline = time.monotonic() + 5
    while heartbeat.lost is None and time.monotonic() < deadline:
        time.sleep(0.05)
    heartbeat.stop()

    assert isinstance(heartbeat.lost, TaskFrameClaimConflict)
    assert heartbeat._thread is None


def test_heartbeat_stop_joins_promptly() -> None:
    engine = _engine()
    with Session(engine) as db:
        frame_id = _seed(db, lease_seconds=30)

    heartbeat = _heartbeat(engine, frame_id, interval_seconds=3600.0)
    heartbeat.start()
    started = time.monotonic()
    heartbeat.stop()

    assert time.monotonic() - started < 5
    assert heartbeat._thread is None


def _backdate_run(db: Session, frame_id: str, *, created_at) -> None:
    run = db.exec(
        select(HarnessRunRecord).where(
            HarnessRunRecord.task_frame_record_id == frame_id
        )
    ).first()
    run.created_at = created_at
    run.updated_at = created_at
    db.add(run)
    db.commit()


def test_heartbeat_loop_stops_without_run_progress() -> None:
    """执行长期无任何动作进展时，心跳必须停止续租，让孤儿回收可以接管。

    回归背景：2026-09-22 定时任务执行因模型长思考/空响应重试在第一个动作上
    卡住，心跳无条件续租使僵尸 run 永远显示"执行中"，孤儿回收永不触发。
    """
    engine = _engine()
    with Session(engine) as db:
        frame_id = _seed(db, lease_seconds=30)
        _backdate_run(db, frame_id, created_at=utc_now() - timedelta(seconds=60))

    heartbeat = _heartbeat(
        engine,
        frame_id,
        interval_seconds=0.05,
        stall_threshold_seconds=0.1,
    )
    before = _leases(engine, frame_id)

    heartbeat.start()
    deadline = time.monotonic() + 5
    while heartbeat.stalled is None and time.monotonic() < deadline:
        time.sleep(0.05)
    heartbeat.stop()

    assert heartbeat.stalled is True
    assert heartbeat._thread is None
    after = _leases(engine, frame_id)
    assert after["frame"] == before["frame"], "stalled 心跳不得续租"
    assert after["run"] == before["run"], "stalled 心跳不得续租"


def test_heartbeat_keeps_renewing_with_recent_invocation_progress() -> None:
    engine = _engine()
    with Session(engine) as db:
        frame_id = _seed(db, lease_seconds=30)
        run = db.exec(
            select(HarnessRunRecord).where(
                HarnessRunRecord.task_frame_record_id == frame_id
            )
        ).first()
        db.add(
            HarnessInvocationRecord(
                tenant_id=TENANT,
                session_id=SESSION_ID,
                task_id=run.task_id,
                run_id=run.id,
                call_id="call-1",
                tool_name="read_file",
                request_digest="digest-1",
                status="completed",
                finished_at=utc_now(),
            )
        )
        db.commit()

    heartbeat = _heartbeat(
        engine,
        frame_id,
        interval_seconds=0.05,
        stall_threshold_seconds=0.5,
    )
    before = _leases(engine, frame_id)

    heartbeat.start()
    time.sleep(0.25)
    heartbeat.stop()

    assert heartbeat.stalled is None
    after = _leases(engine, frame_id)
    assert after["frame"] > before["frame"], "有进展的心跳必须继续续租"

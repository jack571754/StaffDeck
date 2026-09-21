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

from __future__ import annotations

import logging
import threading

from sqlmodel import Session, select

from app.core.harness_session_lease import (
    HarnessSessionLeaseLost,
    HarnessSessionLeaseStore,
    HarnessSessionLeaseToken,
)
from app.core.harness_turn_store import HarnessTurnStore
from app.core.task_frame_store import TaskFrameClaimConflict, TaskFrameStore
from app.db import engine
from app.db.models import (
    AgentEvent,
    HarnessInvocationRecord,
    HarnessRunRecord,
    HarnessTaskFrameRecord,
    HarnessTurnRecord,
    utc_now,
)

logger = logging.getLogger(__name__)

# 心跳间隔必须远小于 900s 租约：即使连续多次心跳失败（SQLite 偶发写锁等），
# 租约仍有充足余量；下一次成功心跳即可续满。
HEARTBEAT_INTERVAL_SECONDS = 60.0

# 执行停滞判定阈值：一次模型调用最坏 600s 超时 × 空响应重试（3 次）≈ 30 分钟，
# 阈值取 35 分钟留出余量。超过阈值没有任何已收尾的工具调用（含失败），视为执行
# 已失去推进能力：心跳停止续租，租约到期后由孤儿回收/恢复清扫接管，避免僵尸
# run 永远显示"执行中"（2026-09-22 定时任务生产事故）。
EXECUTION_STALL_THRESHOLD_SECONDS = 2100.0

LEASE_LOST_EXCEPTIONS = (HarnessSessionLeaseLost, TaskFrameClaimConflict)


class ExecutionLeaseHeartbeat:
    """在 Harness 执行期间周期性续约全部执行租约。

    此前租约只在工具调用前后由 HarnessCapabilityInvoker 续约；LLM 调用期间
    （超时 × 空响应重试可达 30 分钟）没有任何续约，超过 900s 后恢复清扫线程
    会把活执行判死并 fence，表现为 HARNESS_EXECUTION_LOST / completion was
    fenced。心跳用独立 DB 会话按固定间隔续约，不依赖执行线程当前在做什么。

    心跳线程是纯辅助：丢失租约时静默退出并记录异常，fence 由执行线程自己的
    下一次续约/收尾自然暴露，避免心跳与执行线程双重上报。

    停滞保护：若本次执行超过 stall_threshold_seconds 没有任何已收尾的工具调用
    （completed/failed/replayed 都算收尾），视为执行失去推进能力，心跳主动停止
    续租——这不是误杀保护对象，而是让恢复机制有机会接管真正的僵尸执行。
    """

    def __init__(
        self,
        *,
        session_lease: HarnessSessionLeaseToken | None,
        turn_record_id: str | None,
        frame_record_id: str,
        lease_owner: str,
        attempt_no: int,
        interval_seconds: float = HEARTBEAT_INTERVAL_SECONDS,
        stall_threshold_seconds: float = EXECUTION_STALL_THRESHOLD_SECONDS,
        db_engine: object | None = None,
    ) -> None:
        self.session_lease = session_lease
        self.turn_record_id = turn_record_id
        self.frame_record_id = frame_record_id
        self.lease_owner = lease_owner
        self.attempt_no = attempt_no
        self.interval_seconds = interval_seconds
        self.stall_threshold_seconds = stall_threshold_seconds
        self.db_engine = db_engine if db_engine is not None else engine
        self.lost: Exception | None = None
        self.stalled: bool | None = None
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._thread = threading.Thread(
            target=self._loop,
            name="harness-lease-heartbeat",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=self.interval_seconds + 5)
            self._thread = None

    def _loop(self) -> None:
        while not self._stop_event.wait(self.interval_seconds):
            try:
                if self._execution_stalled():
                    self.stalled = True
                    logger.warning(
                        "harness execution stalled: no finished invocation for "
                        "%.0fs, heartbeat stops renewing (frame=%s attempt=%s)",
                        self.stall_threshold_seconds,
                        self.frame_record_id,
                        self.attempt_no,
                    )
                    return
                self.renew_once()
            except LEASE_LOST_EXCEPTIONS as exc:
                # 租约被他人接管：执行已不可延续，停止心跳，fence 由执行线程暴露。
                self.lost = exc
                return
            except Exception:
                # 瞬时 DB 故障（如 SQLite 写锁）不应终止心跳；租约余量足够等下一次 tick 重试。
                logger.debug("harness lease heartbeat renew failed", exc_info=True)
                continue

    def _execution_stalled(self) -> bool:
        if self.stall_threshold_seconds <= 0:
            return False
        with Session(self.db_engine) as db:  # type: ignore[arg-type]
            run = db.exec(
                select(HarnessRunRecord)
                .where(
                    HarnessRunRecord.task_frame_record_id == self.frame_record_id,
                    HarnessRunRecord.attempt_no == self.attempt_no,
                )
                .order_by(HarnessRunRecord.created_at.desc())
            ).first()
            if run is None:
                return False
            last_finished_at = db.exec(
                select(HarnessInvocationRecord.finished_at)
                .where(
                    HarnessInvocationRecord.run_id == run.id,
                    HarnessInvocationRecord.finished_at.is_not(None),  # type: ignore[union-attr]
                )
                .order_by(HarnessInvocationRecord.finished_at.desc())  # type: ignore[arg-type]
            ).first()
            last_event_at = None
            if run.session_id:
                last_event_at = db.exec(
                    select(AgentEvent.created_at)
                    .where(AgentEvent.session_id == run.session_id)
                    .order_by(AgentEvent.created_at.desc())
                ).first()
            candidates = [
                t
                for t in (last_finished_at, last_event_at, run.updated_at, run.created_at)
                if t is not None
            ]
            if not candidates:
                return False
            progress_at = max(candidates)
            return (
                utc_now() - progress_at
            ).total_seconds() > self.stall_threshold_seconds

    def renew_once(self) -> None:
        with Session(self.db_engine) as db:  # type: ignore[arg-type]
            HarnessSessionLeaseStore(db).renew(self.session_lease)
            turn = db.get(HarnessTurnRecord, self.turn_record_id)
            HarnessTurnStore(db).renew(turn)
            frame = db.get(HarnessTaskFrameRecord, self.frame_record_id)
            if frame is not None:
                TaskFrameStore(db).renew_running_lease(
                    frame,
                    lease_owner=self.lease_owner,
                    attempt_no=self.attempt_no,
                )
            db.commit()


__all__ = ["HEARTBEAT_INTERVAL_SECONDS", "ExecutionLeaseHeartbeat"]

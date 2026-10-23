from __future__ import annotations

import argparse
import logging
import signal
import threading

from sqlmodel import Session, update

from app.config import get_settings
from app.db import engine, init_db
from app.db.models import ScheduledTask, utc_now
from app.db.seed import seed_demo_data
from app.scheduled_tasks.service import (
    WORKER_SLEEP_SECONDS,
    due_scheduled_tasks,
    execute_scheduled_task,
    reap_stale_scheduled_task_runs,
)

logger = logging.getLogger(__name__)

_stop_event = threading.Event()

_background_thread: threading.Thread | None = None
_scheduler_ready: bool | None = None


def _handle_stop(_signum: int, _frame: object) -> None:
    _stop_event.set()


def run_worker(*, once: bool = False, poll_seconds: float = WORKER_SLEEP_SECONDS) -> None:
    init_db()
    if get_settings().demo_seed_enabled:
        with Session(engine) as db:
            seed_demo_data(db)
    while not _stop_event.is_set():
        try:
            _run_due_tasks()
        except Exception:
            logger.exception("scheduled-task sweep failed; retrying after %.0fs", max(1.0, poll_seconds))

        if once:
            return
        _stop_event.wait(timeout=max(1.0, poll_seconds))


def _reap_stale_tasks(db: Session) -> None:
    reaped = reap_stale_scheduled_task_runs(db)
    if reaped:
        logger.info("Worker self-healing reaped %d stale scheduled task runs", len(reaped))
    # Clear only expired dangling leases left behind from an ungraceful shutdown
    # (do NOT clear active leases held by other living worker instances)
    now = utc_now()
    db.exec(
        update(ScheduledTask)
        .where(
            ScheduledTask.lease_until.is_not(None),
            ScheduledTask.lease_until < now,
        )
        .values(lease_owner=None, lease_until=None)
    )
    db.commit()


def _run_due_tasks() -> None:
    global _scheduler_ready
    from staffdeck_harness.contracts.manifest import SlotName
    from staffdeck_harness.modules.registry import peek_registry
    registry = peek_registry()
    if registry is not None:
        providers = [item for item in registry.providers(SlotName.STAFF_INGRESS)
                     if item.manifest.metadata.get('ingress', item.manifest.module_id.removeprefix('ingress.')) == 'scheduler']
        if len(providers) != 1:
            if _scheduler_ready is not False:
                logger.warning("scheduled-task dispatcher disabled; no due tasks will be claimed")
            _scheduler_ready = False
            return  # Disabled modules must not even claim tasks.
        if _scheduler_ready is not True:
            logger.info("scheduled-task dispatcher ready")
        _scheduler_ready = True
        from staffdeck_harness.runtime.services import maintenance_sessions
        for db in maintenance_sessions():
            _reap_stale_tasks(db)
            for task in due_scheduled_tasks(db):
                task_id = task.id
                try:
                    providers[0].provider.dispatch(db, task)
                except Exception:  # One failed admission must not block other tasks.
                    db.rollback()
                    logger.exception("scheduled-task dispatch failed task_id=%s", task_id)
        return
    with Session(engine) as db:
        _reap_stale_tasks(db)
        due = due_scheduled_tasks(db)
        for task in due:
            task_id = task.id
            try:
                execute_scheduled_task(db, task)
            except Exception:
                db.rollback()
                logger.exception("Failed to execute due scheduled task %s", task_id)
                task = db.get(ScheduledTask, task_id)
                if task:
                    task.lease_owner = None
                    task.lease_until = None
                    db.add(task)
                    db.commit()


def start_background_worker(*, poll_seconds: float = WORKER_SLEEP_SECONDS) -> None:
    global _background_thread
    if _background_thread and _background_thread.is_alive():
        return
    _stop_event.clear()
    _background_thread = threading.Thread(
        target=run_worker,
        kwargs={"once": False, "poll_seconds": poll_seconds},
        name="ultrarag-scheduled-task-worker",
        daemon=True,
    )
    _background_thread.start()


def stop_background_worker() -> None:
    _stop_event.set()


def main() -> None:
    parser = argparse.ArgumentParser(description="Run StaffDeck scheduled task worker")
    parser.add_argument("--once", action="store_true", help="scan and execute due tasks once, then exit")
    parser.add_argument("--poll-seconds", type=float, default=WORKER_SLEEP_SECONDS)
    args = parser.parse_args()
    signal.signal(signal.SIGTERM, _handle_stop)
    signal.signal(signal.SIGINT, _handle_stop)
    run_worker(once=args.once, poll_seconds=args.poll_seconds)


if __name__ == "__main__":
    main()

from __future__ import annotations

import argparse
import logging
import signal
import threading

from sqlmodel import Session, update

from app.db import engine, init_db
from app.db.models import ScheduledTask
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


def _handle_stop(_signum: int, _frame: object) -> None:
    _stop_event.set()


def run_worker(*, once: bool = False, poll_seconds: float = WORKER_SLEEP_SECONDS) -> None:
    init_db()
    with Session(engine) as db:
        seed_demo_data(db)
        reaped = reap_stale_scheduled_task_runs(db)
        if reaped:
            logger.info("Worker startup self-healing reaped %d stale scheduled task runs", len(reaped))
        # Clear any dangling leases left behind from an ungraceful shutdown
        db.exec(
            update(ScheduledTask)
            .where(ScheduledTask.lease_until.is_not(None))
            .values(lease_owner=None, lease_until=None)
        )
        db.commit()
    while not _stop_event.is_set():
        try:
            with Session(engine) as db:
                due = due_scheduled_tasks(db)
                for task in due:
                    try:
                        execute_scheduled_task(db, task)
                    except Exception:
                        logger.exception("Failed to execute due scheduled task %s", task.id)
                        try:
                            task.lease_owner = None
                            task.lease_until = None
                            db.add(task)
                            db.commit()
                        except Exception as cleanup_exc:  # noqa: BLE001
                            logger.warning("Failed to clear task lease after execution error: %s", cleanup_exc)
        except Exception:
            logger.exception("Unexpected error in scheduled task worker loop")
        if once:
            return
        _stop_event.wait(timeout=max(1.0, poll_seconds))


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

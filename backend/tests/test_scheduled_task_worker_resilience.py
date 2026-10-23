"""A transient sweep failure (e.g. SQLite "database is locked") must not end the worker thread."""

from __future__ import annotations

import logging
from contextlib import nullcontext

from sqlalchemy.exc import OperationalError

from app.scheduled_tasks import worker


def test_worker_logs_and_continues_when_a_sweep_fails(monkeypatch, caplog) -> None:
    calls = []

    def flaky_due(db):
        calls.append(1)
        raise OperationalError("SELECT 1", {}, Exception("database is locked"))

    monkeypatch.setattr(worker, "init_db", lambda: None)
    monkeypatch.setattr(worker, "seed_demo_data", lambda db: None)
    monkeypatch.setattr(worker, "Session", lambda engine: nullcontext(object()))
    monkeypatch.setattr(worker, "due_scheduled_tasks", flaky_due)
    monkeypatch.setattr(worker, "_reap_stale_tasks", lambda db: None)
    worker._stop_event.clear()
    with caplog.at_level(logging.ERROR, logger=worker.__name__):
        worker.run_worker(once=True, poll_seconds=1.0)  # must not raise
    assert calls == [1]
    assert any("scheduled-task sweep failed" in r.getMessage() for r in caplog.records), "the failure is visible in the log"


def test_registered_scheduler_dispatches_pipeline_task(monkeypatch):
    from types import SimpleNamespace
    from staffdeck_harness.modules import registry as registry_module
    from staffdeck_harness.runtime import services

    task = SimpleNamespace(id="pipeline_task", execution_mode="pipeline")
    db = object()
    calls = []
    provider = SimpleNamespace(
        manifest=SimpleNamespace(metadata={"ingress": "scheduler"}, module_id="ingress.scheduler"),
        provider=SimpleNamespace(dispatch=lambda session, value: calls.append((session, value))),
    )
    monkeypatch.setattr(registry_module, "peek_registry", lambda: SimpleNamespace(
        providers=lambda slot: [provider],
    ))
    monkeypatch.setattr(services, "maintenance_sessions", lambda: iter([db]))
    monkeypatch.setattr(worker, "_reap_stale_tasks", lambda session: None)
    monkeypatch.setattr(worker, "due_scheduled_tasks", lambda session: [task])

    worker._run_due_tasks()

    assert calls == [(db, task)]


def test_disabled_scheduler_does_not_claim_or_reap_tasks(monkeypatch):
    from types import SimpleNamespace
    from staffdeck_harness.modules import registry as registry_module

    calls = []
    monkeypatch.setattr(registry_module, "peek_registry", lambda: SimpleNamespace(
        providers=lambda slot: [],
    ))
    monkeypatch.setattr(worker, "_reap_stale_tasks", lambda session: calls.append("reap"))
    monkeypatch.setattr(worker, "due_scheduled_tasks", lambda session: calls.append("claim"))

    worker._run_due_tasks()

    assert calls == []

from __future__ import annotations

import os
from pathlib import Path

import pytest

from app.runtime_lock import (
    RuntimeInstanceLockError,
    acquire_runtime_instance_lock,
    release_runtime_instance_lock,
)

# Windows LockFile mutex offset used by app.runtime_lock (past EOF so the
# owner pid stays readable while the lock is held).
_LOCK_MUTEX_OFFSET = 4096


@pytest.fixture(autouse=True)
def _reset_lock_state() -> None:
    """Keep the module-level lock handle from leaking between tests."""
    from app import runtime_lock

    runtime_lock._lock_handle = None
    yield
    release_runtime_instance_lock()
    runtime_lock._lock_handle = None


def _hold_lock(handle) -> None:
    """Lock the mutex region the same way the application does, per platform."""
    if os.name == "nt":
        import msvcrt

        handle.seek(_LOCK_MUTEX_OFFSET)
        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        return

    import fcntl

    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)


def _release_hold(handle) -> None:
    if os.name == "nt":
        import msvcrt

        handle.seek(_LOCK_MUTEX_OFFSET)
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        return

    import fcntl

    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def test_runtime_lock_rejects_second_process_for_same_sqlite_database(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "staffdeck.db"
    monkeypatch.setattr(
        "app.runtime_lock.get_settings",
        lambda: type("Settings", (), {"database_url": f"sqlite:///{database_path}"})(),
    )

    lock_path = acquire_runtime_instance_lock()
    assert lock_path == tmp_path / "staffdeck.db.runtime.lock"
    assert lock_path.read_text(encoding="utf-8").isdigit()

    # Re-entry by the owning application is harmless; another open file
    # descriptor is covered separately by the OS-level flock semantics.
    assert acquire_runtime_instance_lock() == lock_path
    release_runtime_instance_lock()


def test_runtime_lock_reports_existing_owner(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "staffdeck.db"
    lock_path = tmp_path / "staffdeck.db.runtime.lock"
    owner = lock_path.open("a+", encoding="utf-8")
    owner.write("4242")
    owner.flush()
    _hold_lock(owner)
    monkeypatch.setattr(
        "app.runtime_lock.get_settings",
        lambda: type("Settings", (), {"database_url": f"sqlite:///{database_path}"})(),
    )

    try:
        with pytest.raises(RuntimeInstanceLockError, match="pid=4242"):
            acquire_runtime_instance_lock()
    finally:
        _release_hold(owner)
        owner.close()
        release_runtime_instance_lock()


def test_runtime_lock_release_allows_reacquire(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "staffdeck.db"
    monkeypatch.setattr(
        "app.runtime_lock.get_settings",
        lambda: type("Settings", (), {"database_url": f"sqlite:///{database_path}"})(),
    )

    lock_path = acquire_runtime_instance_lock()
    release_runtime_instance_lock()

    reacquired = acquire_runtime_instance_lock()
    assert reacquired == lock_path
    release_runtime_instance_lock()

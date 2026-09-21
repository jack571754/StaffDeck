from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT_DIR = Path(__file__).resolve().parents[2]
SCRIPTS_DIR = ROOT_DIR / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))


def _load_script(name: str):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS_DIR / f"{name}.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def test_supervisor_uses_platform_specific_executables() -> None:
    supervisor = _load_script("dev_supervisor")

    assert supervisor._backend_python("win32") == ROOT_DIR / "backend/.venv/Scripts/python.exe"
    assert supervisor._backend_python("linux") == ROOT_DIR / "backend/.venv/bin/python"
    assert supervisor._vite_executable("win32") == ROOT_DIR / "frontend-enterprise/node_modules/.bin/vite.cmd"
    assert supervisor._vite_executable("darwin") == ROOT_DIR / "frontend-enterprise/node_modules/.bin/vite"


def test_pid_alive_recognizes_current_process() -> None:
    process_utils = _load_script("process_utils")

    assert process_utils.pid_alive(os.getpid())


def test_dev_cli_uses_next_port_in_packaged_app_range(monkeypatch) -> None:
    dev = _load_script("dev")
    monkeypatch.delenv("ULTRARAG_PORT_RANGE_START", raising=False)
    monkeypatch.delenv("ULTRARAG_PORT_RANGE_END", raising=False)
    monkeypatch.setattr(dev, "_port_available", lambda _host, port: port != 5173)

    assert dev._select_available_port("127.0.0.1", 5173) == 5174


def test_url_ready_does_not_require_reading_response_body(monkeypatch) -> None:
    dev = _load_script("dev")

    class Response:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            raise ConnectionResetError("body connection closed")

    monkeypatch.setattr(dev.urllib.request, "urlopen", lambda *_args, **_kwargs: Response())
    assert dev._url_ready("http://127.0.0.1:5173/api/health") is True


def test_url_ready_ignores_response_close_failure(monkeypatch) -> None:
    dev = _load_script("dev")

    class Response:
        status = 200

        def close(self):
            raise ConnectionResetError("connection closed")

    monkeypatch.setattr(dev.urllib.request, "urlopen", lambda *_args, **_kwargs: Response())
    assert dev._url_ready("http://127.0.0.1:5173/api/health") is True


def test_dev_cli_honors_packaged_app_port_range(monkeypatch) -> None:
    dev = _load_script("dev")
    monkeypatch.setenv("ULTRARAG_PORT_RANGE_START", "6200")
    monkeypatch.setenv("ULTRARAG_PORT_RANGE_END", "6202")
    monkeypatch.setattr(dev, "_port_available", lambda _host, port: port == 6202)

    assert dev._select_available_port("127.0.0.1", 6200) == 6202


def test_dev_cli_keeps_complete_frontend_dependencies(monkeypatch) -> None:
    dev = _load_script("dev")
    calls: list[list[str]] = []
    monkeypatch.setattr(dev, "_npm_executable", lambda: "npm")

    def run(command, **_kwargs):
        calls.append(command)
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(dev.subprocess, "run", run)

    dev._ensure_frontend_dependencies()

    assert len(calls) == 1
    assert calls[0][-3:] == ["ls", "--depth=0", "--json"]


def test_dev_cli_refreshes_incomplete_frontend_dependencies(monkeypatch) -> None:
    dev = _load_script("dev")
    calls: list[list[str]] = []
    monkeypatch.setattr(dev, "_npm_executable", lambda: "npm")

    def run(command, **_kwargs):
        calls.append(command)
        return subprocess.CompletedProcess(command, 1 if "ls" in command else 0)

    monkeypatch.setattr(dev.subprocess, "run", run)

    dev._ensure_frontend_dependencies()

    assert len(calls) == 2
    assert calls[1][-3:] == ["ci", "--no-audit", "--no-fund"]


def test_dev_cli_detects_missing_backend_dependency(tmp_path, monkeypatch) -> None:
    dev = _load_script("dev")
    backend = tmp_path / "backend"
    backend.mkdir()
    (backend / "pyproject.toml").write_text(
        '[project]\ndependencies = ["present>=1", "missing>=1"]\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(dev, "ROOT_DIR", tmp_path)
    monkeypatch.setattr(
        dev,
        "_installed_distribution_version",
        lambda name: "1.2" if name == "present" else None,
    )

    assert dev._backend_dependencies_complete() is False


def test_dev_cli_refreshes_incomplete_backend_dependencies(monkeypatch) -> None:
    dev = _load_script("dev")
    calls: list[list[str]] = []
    monkeypatch.setattr(dev, "_backend_dependencies_complete", lambda: False)

    def run(command, **_kwargs):
        calls.append(command)
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(dev.subprocess, "run", run)

    dev._ensure_backend_dependencies()

    assert calls == [
        [
            sys.executable,
            "-m",
            "pip",
            "install",
            "-e",
            str(ROOT_DIR / "backend"),
        ]
    ]


def test_supervisor_does_not_restart_during_startup_grace(monkeypatch) -> None:
    supervisor = _load_script("dev_supervisor")

    class RunningProcess:
        def poll(self):
            return None

    service = supervisor.Service(name="app", cwd=ROOT_DIR, command=["unused"])
    service.health_url = "http://127.0.0.1:5173/api/health"
    service.process = RunningProcess()
    service.startup_deadline = 100.0
    monkeypatch.setattr(supervisor.time, "monotonic", lambda: 50.0)
    monkeypatch.setattr(service, "healthy", lambda: False)

    service.poll()

    assert service.unhealthy_count == 0
    assert service.restart_count == 0


def test_supervisor_opens_circuit_after_repeated_fast_exits(monkeypatch) -> None:
    supervisor = _load_script("dev_supervisor")
    starts: list[int] = []

    class ExitedProcess:
        def __init__(self) -> None:
            self.pid = 4242

        def poll(self):
            return 1

    service = supervisor.Service(name="app", cwd=ROOT_DIR, command=["unused"])
    monkeypatch.setattr(supervisor.time, "monotonic", lambda: 0.0)
    monkeypatch.setattr(supervisor.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(supervisor, "remove_pid_file", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(service, "start", lambda: starts.append(1))

    for _ in range(supervisor.FAST_EXIT_LIMIT):
        service.process = ExitedProcess()
        service.last_start_monotonic = 0.0
        service.poll()

    assert service.circuit_open is True
    assert service.fast_exit_count == supervisor.FAST_EXIT_LIMIT
    assert service.restart_count == supervisor.FAST_EXIT_LIMIT - 1
    assert len(starts) == supervisor.FAST_EXIT_LIMIT - 1

    # Once the circuit is open, poll is a no-op and no further restart happens.
    service.process = ExitedProcess()
    service.poll()
    assert len(starts) == supervisor.FAST_EXIT_LIMIT - 1


def test_supervisor_resets_counters_after_stable_uptime(monkeypatch) -> None:
    supervisor = _load_script("dev_supervisor")
    starts: list[int] = []

    class ExitedProcess:
        def __init__(self) -> None:
            self.pid = 4242

        def poll(self):
            return 1

    service = supervisor.Service(name="app", cwd=ROOT_DIR, command=["unused"])
    service.restart_count = 3
    service.fast_exit_count = 2
    # The previous run was stable well beyond FAST_EXIT_SECONDS, so the
    # counters reset instead of accumulating towards the circuit breaker.
    clock = {"now": supervisor.STABLE_SECONDS + 5.0}
    monkeypatch.setattr(supervisor.time, "monotonic", lambda: clock["now"])
    monkeypatch.setattr(supervisor.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(supervisor, "remove_pid_file", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(service, "start", lambda: starts.append(1))

    service.process = ExitedProcess()
    service.last_start_monotonic = 0.0
    service.poll()

    assert service.restart_count == 1
    assert service.fast_exit_count == 0
    assert service.circuit_open is False


def test_supervisor_refuses_to_start_over_live_supervisor_pid(tmp_path, monkeypatch) -> None:
    supervisor = _load_script("dev_supervisor")
    monkeypatch.setattr(supervisor, "log", lambda _message: None)
    pid_file = tmp_path / "supervisor.pid"
    pid_file.write_text(f"{os.getpid()}\n", encoding="utf-8")

    try:
        with pytest.raises(RuntimeError, match="supervisor already running"):
            supervisor._ensure_single_supervisor(pid_file, force=False)
        # force explicitly overrides the guard.
        supervisor._ensure_single_supervisor(pid_file, force=True)
    finally:
        pid_file.unlink(missing_ok=True)


def test_supervisor_accepts_stale_supervisor_pid(tmp_path) -> None:
    supervisor = _load_script("dev_supervisor")
    pid_file = tmp_path / "supervisor.pid"
    pid_file.write_text("999999999\n", encoding="utf-8")

    supervisor._ensure_single_supervisor(pid_file, force=False)

    pid_file.unlink(missing_ok=True)


def test_supervisor_port_precheck_blocks_start(monkeypatch, tmp_path) -> None:
    supervisor = _load_script("dev_supervisor")
    service = supervisor.Service(
        name="app",
        cwd=tmp_path,
        command=["unused"],
        host="127.0.0.1",
        port=59999,
    )
    monkeypatch.setattr(supervisor, "_port_bindable", lambda _host, _port: False)

    with pytest.raises(RuntimeError, match="already in use"):
        service.start()


def test_shell_wrappers_delegate_to_cross_platform_cli() -> None:
    for command in ("up", "down", "status"):
        script = (SCRIPTS_DIR / f"dev_{command}.sh").read_text(encoding="utf-8")
        assert '$ROOT_DIR/backend/.venv/bin/python' in script
        assert 'scripts/dev.py" ' + command in script


def test_powershell_wrappers_delegate_to_cross_platform_cli() -> None:
    for command in ("up", "down", "status"):
        script = (SCRIPTS_DIR / f"dev_{command}.ps1").read_text(encoding="utf-8")
        assert f'"$PSScriptRoot\\dev.ps1" {command}' in script


def test_powershell_launcher_accepts_newer_python_3_versions() -> None:
    script = (SCRIPTS_DIR / "dev.ps1").read_text(encoding="utf-8")

    assert 'Prefix = @("-3.11")' in script
    assert 'Prefix = @("-3")' in script

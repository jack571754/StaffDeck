from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.harness.command import run_sandboxed_process
from app.harness.contracts import HarnessToolContext
from app.harness.errors import HarnessExecutionError
from app.harness.registry import HarnessRegistry

_PACKAGE_ROOT = Path(".harness/skill-packages")


class RunSkillScriptArguments(BaseModel):
    """Typed invocation of an existing script shipped in a GeneralSkill package."""

    model_config = ConfigDict(extra="forbid")

    script_path: str = Field(min_length=1)
    argv: list[str] = Field(default_factory=list, max_length=64)
    stdin: str = Field(default="", max_length=256_000)
    timeout_seconds: float = Field(default=120.0, ge=0.1, le=600.0)
    max_output_bytes: int = Field(default=64 * 1024, ge=128, le=128 * 1024)


def run_skill_script(
    context: HarnessToolContext,
    arguments: BaseModel,
) -> dict[str, Any]:
    if not isinstance(arguments, RunSkillScriptArguments):
        raise HarnessExecutionError("INVALID_ARGUMENTS", "Invalid skill script arguments.")
    workspace = context.workspace_root.resolve()
    raw_path = arguments.script_path.replace("\\", "/")
    if raw_path.startswith("/workspace/"):
        raw_path = raw_path.removeprefix("/workspace/")
    candidate = Path(raw_path)
    if not candidate.is_absolute():
        candidate = workspace / candidate
    try:
        script = candidate.resolve(strict=True)
    except FileNotFoundError as exc:
        raise HarnessExecutionError(
            "SKILL_SCRIPT_NOT_FOUND",
            "The requested GeneralSkill script does not exist.",
        ) from exc
    try:
        package_root = (workspace / _PACKAGE_ROOT).resolve(strict=True)
    except FileNotFoundError as exc:
        raise HarnessExecutionError(
            "SKILL_PACKAGE_NOT_LOADED",
            "Load the GeneralSkill package before running one of its scripts.",
        ) from exc
    try:
        script.relative_to(package_root)
    except ValueError as exc:
        raise HarnessExecutionError(
            "SKILL_SCRIPT_NOT_ALLOWED",
            "run_skill_script only executes files materialized from a GeneralSkill package.",
        ) from exc
    if script.is_symlink() or not script.is_file():
        raise HarnessExecutionError("SKILL_SCRIPT_NOT_FOUND", "Skill script is not a regular file.")

    command = _script_argv(script, arguments.argv)
    process = run_sandboxed_process(
        workspace=workspace,
        argv=command,
        stdin_bytes=arguments.stdin.encode("utf-8"),
        cwd=script.parent,
        timeout_seconds=arguments.timeout_seconds,
        output_limit=max(
            1,
            min(arguments.max_output_bytes, context.limits.max_result_bytes // 4),
        ),
        network_mode=context.sandbox_network_mode,
        allowed_domains=context.sandbox_allowed_domains,
        sandbox_enabled=context.sandbox_enabled,
        env=_skill_process_env(context),
    )
    stdout = process.stdout.decode("utf-8", errors="replace")
    stderr = process.stderr.decode("utf-8", errors="replace")
    structured_result = _parse_structured_result(stdout)
    business_failed = _structured_result_failed(structured_result)
    return {
        "ok": process.returncode == 0 and not process.timed_out and not business_failed,
        "status": (
            "timed_out"
            if process.timed_out
            else "failed"
            if process.returncode != 0 or business_failed
            else "completed"
        ),
        "exit_code": process.returncode,
        "stdout": stdout,
        "stderr": stderr,
        "structured_result": structured_result,
        "stdout_bytes": process.stdout_bytes,
        "stderr_bytes": process.stderr_bytes,
        "output_truncated": process.output_truncated,
        "duration_ms": process.duration_ms,
        "script_path": script.relative_to(workspace).as_posix(),
        "isolation_mode": process.isolation_mode,
    }


def _skill_process_env(context: HarnessToolContext) -> dict[str, str]:
    """Non-secret identifiers a skill script needs to report its origin.

    Lets the server resolve task-scoped configuration (which Feishu app to push
    with) without trusting the model to forward opaque ids through argv.
    """

    identifiers = {
        "STAFFDECK_TASK_ID": context.scheduled_task_id,
        "STAFFDECK_TENANT_ID": context.tenant_id,
    }
    return {
        key: str(value).strip()
        for key, value in identifiers.items()
        if value and str(value).strip()
    }


def _parse_structured_result(stdout: str) -> dict[str, Any] | None:
    """Read a JSON object emitted by a skill, including pretty-printed output."""

    candidates = [stdout.strip()]
    decoder = json.JSONDecoder()
    candidates.extend(stdout[index:] for index, char in enumerate(stdout) if char == "{")
    for candidate in candidates:
        if not candidate:
            continue
        try:
            value, _ = decoder.raw_decode(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value
    return None


def _structured_result_failed(result: dict[str, Any] | None) -> bool:
    if not result:
        return False
    if str(result.get("status") or "").strip().lower() in {"error", "failed", "failure"}:
        return True
    return str(result.get("push_status") or "").strip().lower() in {
        "error",
        "failed",
        "failure",
    }


def _script_argv(script: Path, argv: list[str]) -> list[str]:
    suffix = script.suffix.lower()
    if suffix == ".py":
        return [sys.executable, str(script), *argv]
    if suffix == ".sh":
        shell = shutil.which("bash")
        if not shell:
            raise HarnessExecutionError("RUNTIME_NOT_FOUND", "Bash runtime is unavailable.")
        return [shell, str(script), *argv]
    if suffix == ".ps1":
        shell = shutil.which("pwsh") or shutil.which("powershell.exe")
        if not shell:
            raise HarnessExecutionError("RUNTIME_NOT_FOUND", "PowerShell runtime is unavailable.")
        return [shell, "-NoProfile", "-File", str(script), *argv]
    if suffix in {".js", ".mjs", ".cjs"}:
        node = shutil.which("node")
        if not node:
            raise HarnessExecutionError("RUNTIME_NOT_FOUND", "Node.js runtime is unavailable.")
        return [node, str(script), *argv]
    raise HarnessExecutionError(
        "UNSUPPORTED_SKILL_SCRIPT",
        "Supported GeneralSkill script types are .py, .sh, .ps1, .js, .mjs, and .cjs.",
    )


def register_skill_script_tools(registry: HarnessRegistry) -> HarnessRegistry:
    registry.register(
        name="run_skill_script",
        description=(
            "Run an existing script materialized from a loaded GeneralSkill package using a "
            "typed argv/stdin contract and the TaskFrame sandbox. Do not construct a shell launch "
            "command; pass the returned package file path directly."
        ),
        argument_model=RunSkillScriptArguments,
        handler=run_skill_script,
        side_effect="write",
    )
    return registry


__all__ = [
    "RunSkillScriptArguments",
    "register_skill_script_tools",
    "run_skill_script",
]

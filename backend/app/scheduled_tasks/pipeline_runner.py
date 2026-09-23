"""Deterministic Pipeline Runner for Scheduled Tasks.

Executes sequential, non-LLM pipelines (query -> render -> notify / skill_notify)
without entering the Harness Agent loop, achieving execution times < 2s and zero
token consumption.
"""

from __future__ import annotations

import json
import logging
import os
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

import requests
from sqlmodel import Session, select

from app.data_query.date_resolver import resolve_date_expression
from app.data_query.models import QueryExecuteResult
from app.data_query.service import execute_query_by_id
from app.db.models import GeneralSkill, ScheduledTask, ScheduledTaskRun, utc_now
from app.harness.command import run_sandboxed_process
from app.security.skill_env import skill_secret_environment

logger = logging.getLogger(__name__)

_SKILL_NOTIFY_DEFAULT_ENTRYPOINT = "run.py"
_SKILL_NOTIFY_DEFAULT_TIMEOUT_SECONDS = 60.0
_SKILL_NOTIFY_MAX_TIMEOUT_SECONDS = 300.0
_SKILL_NOTIFY_OUTPUT_LIMIT = 64 * 1024


def _format_markdown_table(result: QueryExecuteResult, title: str = "") -> str:
    parts: list[str] = []
    if title:
        parts.append(f"### {title}")

    if not result.columns or not result.rows:
        parts.append("查询结果为空（0 行）。")
        return "\n\n".join(parts)

    headers = [str(c) for c in result.columns]
    header_row = "| " + " | ".join(headers) + " |"
    separator_row = "| " + " | ".join(["---"] * len(headers)) + " |"

    data_rows = []
    for row in result.rows:
        cols = [str(row.get(col, "")) if row.get(col) is not None else "" for col in headers]
        data_rows.append("| " + " | ".join(cols) + " |")

    table = "\n".join([header_row, separator_row] + data_rows)
    parts.append(table)
    return "\n\n".join(parts)


def _resolve_webhook_url(raw_url: str) -> str:
    url = (raw_url or "").strip()
    if url.startswith("env:"):
        env_var = url[4:].strip()
        resolved = os.environ.get(env_var, "").strip()
        if not resolved:
            logger.warning("Pipeline webhook env var %s is empty or not set", env_var)
        return resolved
    return url


def _safe_relative_path(path: str) -> str:
    """归一化包内相对路径，拒绝绝对路径、盘符与 .. 逃逸。"""
    normalized = path.replace("\\", "/").strip().strip("/")
    parts = [part for part in normalized.split("/") if part and part != "."]
    if not parts or any(":" in part or part == ".." for part in parts):
        return ""
    return "/".join(parts)


def _run_skill_notify_step(
    db: Session,
    task: ScheduledTask,
    step: dict[str, Any],
    idx: int,
    context: dict[str, Any],
) -> dict[str, Any]:
    """执行 skill_notify：物化 GeneralSkill 包并运行其 .py 入口脚本。

    查询 rows 以 --text-file JSON 传递（run.py 播报模式可直接识别），
    凭证经 skill_secret_environment() 白名单注入环境，绝不写入步骤配置。
    """
    slug = str(step.get("skill") or "").strip()
    if not slug:
        raise ValueError(f"Step {idx} (skill_notify) missing skill slug")
    entrypoint = _safe_relative_path(
        str(step.get("entrypoint") or _SKILL_NOTIFY_DEFAULT_ENTRYPOINT)
    )
    if not entrypoint:
        raise ValueError(f"Step {idx} (skill_notify) has unsafe entrypoint path")
    try:
        timeout_seconds = float(
            step.get("timeout_seconds") or _SKILL_NOTIFY_DEFAULT_TIMEOUT_SECONDS
        )
    except (TypeError, ValueError):
        timeout_seconds = _SKILL_NOTIFY_DEFAULT_TIMEOUT_SECONDS
    timeout_seconds = min(max(timeout_seconds, 1.0), _SKILL_NOTIFY_MAX_TIMEOUT_SECONDS)

    skill = db.exec(
        select(GeneralSkill).where(
            GeneralSkill.tenant_id == task.tenant_id,
            GeneralSkill.slug == slug,
        )
    ).first()
    if skill is None:
        raise ValueError(
            f"Step {idx} (skill_notify) skill '{slug}' not found for tenant {task.tenant_id}"
        )

    secret_env = skill_secret_environment()
    with tempfile.TemporaryDirectory(prefix="pipeline_skill_") as tmp_name:
        root = Path(tmp_name)
        package_dir = root / "package"
        package_dir.mkdir(parents=True)
        for raw_file in skill.skill_files_json or []:
            if not isinstance(raw_file, dict):
                continue
            relative_path = _safe_relative_path(str(raw_file.get("path") or ""))
            if not relative_path:
                continue
            target = package_dir / relative_path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(str(raw_file.get("content") or ""), encoding="utf-8")

        script = package_dir / entrypoint
        if script.suffix.lower() != ".py" or not script.is_file():
            raise ValueError(
                f"Step {idx} (skill_notify) entrypoint '{entrypoint}' not found in "
                f"skill '{slug}' (only .py entrypoints are supported)"
            )

        payload_dir = root / "payload"
        payload_dir.mkdir(parents=True)
        rows = context.get("rows")
        if str(step.get("payload") or "").strip().lower() != "text" and isinstance(rows, list):
            payload = {
                "text": context.get("text") or "",
                "columns": context.get("columns") or [],
                "rows": rows,
                "row_count": len(rows),
            }
            payload_path = payload_dir / "input.json"
            payload_path.write_text(
                json.dumps(payload, ensure_ascii=False, default=str), encoding="utf-8"
            )
        else:
            payload_path = payload_dir / "input.md"
            payload_path.write_text(
                str(context.get("text") or task.prompt or ""), encoding="utf-8"
            )

        process = run_sandboxed_process(
            workspace=root,
            argv=[sys.executable, str(script), "--text-file", str(payload_path)],
            cwd=script.parent,
            timeout_seconds=timeout_seconds,
            output_limit=_SKILL_NOTIFY_OUTPUT_LIMIT,
            network_mode="all",
            env=secret_env,
            env_allowed_extra=frozenset(secret_env),
        )

    if process.timed_out:
        raise RuntimeError(
            f"Step {idx} (skill_notify) '{slug}' timed out after {timeout_seconds:g}s"
        )
    if process.returncode != 0:
        stderr_tail = process.stderr.decode("utf-8", errors="replace").strip()[-400:]
        stdout_tail = process.stdout.decode("utf-8", errors="replace").strip()[-200:]
        raise RuntimeError(
            f"Step {idx} (skill_notify) '{slug}' failed with exit code "
            f"{process.returncode}: {stderr_tail or stdout_tail}"
        )
    return {
        "exit_code": process.returncode,
        "stdout_bytes": process.stdout_bytes,
        "stderr_bytes": process.stderr_bytes,
    }


def _send_pipeline_failure_alert(
    task: ScheduledTask,
    steps: list[dict[str, Any]],
    exc: Exception,
) -> None:
    """失败可见性：复用第一个 notify 步骤的 webhook 发一条 text 告警。

    告警发送自身失败一律静默（仅记日志），不得吞掉原始失败状态。
    """
    for step in steps:
        if str(step.get("type") or "").lower() != "notify":
            continue
        webhook_url = _resolve_webhook_url(str(step.get("webhook_url") or ""))
        if not webhook_url:
            continue
        try:
            alert_text = (
                f"⚠️ 定时任务「{task.title}」pipeline 执行失败：{str(exc)[:300]}"
            )
            requests.post(
                webhook_url,
                json={"msg_type": "text", "content": {"text": alert_text}},
                timeout=10,
            )
        except Exception:
            logger.warning(
                "Pipeline failure alert could not be delivered for task %s",
                task.id,
                exc_info=True,
            )
        return


def execute_pipeline(
    db: Session,
    task: ScheduledTask,
    run: ScheduledTaskRun,
    *,
    manual: bool = False,
) -> None:
    """Execute a scheduled task in pipeline mode."""
    from app.scheduled_tasks.service import _finish_task_schedule

    t0 = time.perf_counter()
    steps = task.pipeline_steps_json or []
    context: dict[str, Any] = {
        "tenant_id": task.tenant_id,
        "task_id": task.id,
        "run_id": run.id,
    }
    step_traces: list[dict[str, Any]] = []

    try:
        run.started_at = utc_now()
        run.status = "running"
        db.add(run)
        db.commit()

        for idx, step in enumerate(steps, start=1):
            step_type = str(step.get("type") or "").lower()
            step_start = time.perf_counter()
            step_info: dict[str, Any] = {"step": idx, "type": step_type, "status": "success"}

            if step_type == "query":
                template_id = str(step.get("template_id") or "").strip()
                if not template_id:
                    raise ValueError(f"Step {idx} (query) missing template_id")

                raw_params = step.get("params") or {}
                resolved_params = {}
                for k, v in raw_params.items():
                    v_str = str(v) if v is not None else ""
                    resolved_params[k] = resolve_date_expression(v_str)

                query_res = execute_query_by_id(
                    db=db,
                    template_id=template_id,
                    tenant_id=task.tenant_id,
                    params=resolved_params,
                )
                context["query_result"] = query_res
                context["rows"] = query_res.rows
                context["columns"] = query_res.columns
                step_info["row_count"] = query_res.row_count
                step_info["query_duration_ms"] = query_res.execution_time_ms

            elif step_type == "render":
                title = str(step.get("title") or "").strip()
                template_str = str(step.get("template_str") or "").strip()

                if template_str:
                    rendered = template_str.format(**context)
                elif "query_result" in context:
                    rendered = _format_markdown_table(context["query_result"], title=title)
                else:
                    rendered = title or task.prompt

                context["text"] = rendered
                step_info["text_length"] = len(rendered)

            elif step_type == "notify":
                webhook_url = _resolve_webhook_url(str(step.get("webhook_url") or ""))
                if not webhook_url:
                    raise ValueError(f"Step {idx} (notify) missing or unresolvable webhook_url")

                text_to_send = context.get("text") or task.prompt
                # Feishu / Standard bot webhook format
                payload: dict[str, Any] = {
                    "msg_type": "text",
                    "content": {"text": text_to_send},
                }

                resp = requests.post(webhook_url, json=payload, timeout=15)
                step_info["status_code"] = resp.status_code
                if resp.status_code >= 400:
                    raise RuntimeError(f"Webhook notification failed with HTTP {resp.status_code}: {resp.text[:200]}")

            elif step_type == "skill_notify":
                step_info.update(
                    _run_skill_notify_step(db, task, step, idx, context)
                )

            else:
                logger.warning("Unknown pipeline step type %s, skipping", step_type)
                step_info["status"] = "skipped"

            step_info["duration_ms"] = round((time.perf_counter() - step_start) * 1000, 2)
            step_traces.append(step_info)
            # 每步心跳：孤儿回收依赖 run.updated_at 判定 pipeline 执行是否仍然存活
            run.updated_at = utc_now()
            db.add(run)
            db.commit()

        # Success completion
        total_duration_ms = round((time.perf_counter() - t0) * 1000, 2)
        run.status = "completed"
        run.error = None
        run.result_summary = (context.get("text") or "Pipeline executed successfully")[:500]
        run.trace_json = {
            "steps": step_traces,
            "total_duration_ms": total_duration_ms,
        }
        run.finished_at = utc_now()
        _finish_task_schedule(db, task, run.scheduled_for, "completed", manual)
        db.commit()

    except Exception as exc:
        total_duration_ms = round((time.perf_counter() - t0) * 1000, 2)
        logger.exception("Scheduled task %s pipeline failed", task.id)
        _send_pipeline_failure_alert(task, steps, exc)
        run.status = "failed"
        run.error = str(exc)[:500]
        run.trace_json = {
            "steps": step_traces,
            "error": str(exc),
            "total_duration_ms": total_duration_ms,
        }
        run.finished_at = utc_now()
        _finish_task_schedule(db, task, run.scheduled_for, "failed", manual)
        db.commit()
    finally:
        task.lease_owner = None
        db.add(task)
        db.commit()

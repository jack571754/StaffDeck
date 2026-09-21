"""Deterministic Pipeline Runner for Scheduled Tasks.

Executes sequential, non-LLM pipelines (query -> render -> notify) without
entering the Harness Agent loop, achieving execution times < 2s and zero token consumption.
"""

from __future__ import annotations

import logging
import os
import time
from typing import Any

import requests
from sqlmodel import Session

from app.data_query.date_resolver import resolve_date_expression
from app.data_query.models import QueryExecuteResult
from app.data_query.service import execute_query_by_id
from app.db.models import ScheduledTask, ScheduledTaskRun, utc_now

logger = logging.getLogger(__name__)


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

"""Pipeline Execution Engine for Scheduled Tasks.

Executes deterministic, multi-step pipeline tasks (Query -> Process -> Notify)
without LLM hallucination, context limits, or sandbox timeouts.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta, timezone
from typing import Any

from sqlmodel import Session, select

from app.api.mock import FeishuAppNotifyRequest, feishu_app_notify
from app.data_query.service import execute_query_by_id
from app.db.models import ScheduledTask, ScheduledTaskRun, utc_now

logger = logging.getLogger(__name__)


from app.scheduled_tasks.renderers import (
    CARD_RENDERERS,
    build_generic_feishu_card,
    build_sales_feishu_card,
    get_card_renderer,
)
from app.scheduled_tasks.renderers.base import fmt_diff_styled, fmt_val_styled

# Backward compatibility alias
_fmt_val_styled = fmt_val_styled
_fmt_diff_styled = fmt_diff_styled
_build_generic_feishu_card = build_generic_feishu_card

__all__ = [
    "CARD_RENDERERS",
    "_build_generic_feishu_card",
    "_fmt_diff_styled",
    "_fmt_val_styled",
    "build_generic_feishu_card",
    "build_sales_feishu_card",
    "execute_pipeline_scheduled_task",
    "get_card_renderer",
    "is_task_first_push_today",
]



def is_task_first_push_today(
    task: ScheduledTask,
    run: ScheduledTaskRun | None,
    db: Session,
) -> bool:
    """判断当前任务执行是否为当日首次推送。

    判定规则：
    1. 任务 metadata 或 pipeline 步骤参数中若显式指定 is_first_push，以显式配置为准；
    2. 若计划为 daily 多时段（如 ["08:00", "17:00", "23:58"]），且当前计划执行时间 scheduled_for 匹配当天的首个时段（如 08:00），视为当日首次推送；
    3. 查询在本地时区（Asia/Shanghai，UTC+8）下当天此前是否已有状态为 succeeded 的执行记录。若无，则视为当日首次推送。
    """
    metadata = task.metadata_json if isinstance(task.metadata_json, dict) else {}
    if "is_first_push" in metadata:
        return bool(metadata["is_first_push"])

    for step in (task.pipeline_steps_json or []):
        if isinstance(step, dict) and isinstance(step.get("params"), dict) and "is_first_push" in step["params"]:
            return bool(step["params"]["is_first_push"])

    cst = timezone(timedelta(hours=8))
    now_cst = datetime.now(cst)
    today_cst_str = now_cst.strftime("%Y-%m-%d")

    schedule_cfg = task.schedule_json if isinstance(task.schedule_json, dict) else {}
    times = schedule_cfg.get("times")
    if isinstance(times, list) and times:
        first_time = str(times[0]).strip()
        sched_for = run.scheduled_for if run and run.scheduled_for else None
        if sched_for:
            sched_for_cst = (
                sched_for.replace(tzinfo=UTC).astimezone(cst)
                if sched_for.tzinfo is None
                else sched_for.astimezone(cst)
            )
            if sched_for_cst.strftime("%H:%M") == first_time:
                return True

    current_run_id = run.id if run else ""
    runs = db.exec(
        select(ScheduledTaskRun).where(
            ScheduledTaskRun.scheduled_task_id == task.id,
            ScheduledTaskRun.status == "succeeded",
            ScheduledTaskRun.id != current_run_id,
        )
    ).all()

    succeeded_today = [
        r for r in runs
        if r.created_at and (
            r.created_at.replace(tzinfo=UTC).astimezone(cst)
            if r.created_at.tzinfo is None
            else r.created_at.astimezone(cst)
        ).strftime("%Y-%m-%d") == today_cst_str
    ]

    return len(succeeded_today) == 0


def execute_pipeline_scheduled_task(
    db: Session,
    task: ScheduledTask,
    run: ScheduledTaskRun,
    *,
    manual: bool,
) -> ScheduledTaskRun:
    """Execute a scheduled task via the deterministic pipeline engine."""
    from app.scheduled_tasks.service import (
        _finish_task_schedule,
        _record_scheduled_task_stream_event,
    )

    logger.info("Executing pipeline scheduled task %s (run %s)", task.id, run.id)
    steps = task.pipeline_steps_json or []
    query_rows: list[dict[str, Any]] = []
    step_results: list[dict[str, Any]] = []
    is_first_push = is_task_first_push_today(task, run, db)
    if is_first_push:
        logger.info(
            "Task %s (run %s) identified as FIRST push of today. Increment logic set to 0.00万.",
            task.id,
            run.id,
        )

    try:
        # Step 1: Execute query steps
        for idx, step in enumerate(steps, 1):
            step_type = step.get("type")
            if step_type == "query":
                template_id = step.get("template_id")
                if not template_id:
                    raise ValueError(f"流水线步骤 {idx} 缺少 template_id")
                params = dict(step.get("params") or {})
                params["is_first_push"] = is_first_push
                query_res = execute_query_by_id(db, template_id, task.tenant_id, params)
                query_rows = query_res.rows
                step_results.append({
                    "step": idx,
                    "type": "query",
                    "template_id": template_id,
                    "rows_count": len(query_rows),
                    "execution_time_ms": query_res.execution_time_ms,
                    "is_first_push": is_first_push,
                })
                logger.info(
                    "Pipeline step %d (query): template %s returned %d rows in %.2fms (is_first_push=%s)",
                    idx,
                    template_id,
                    len(query_rows),
                    query_res.execution_time_ms,
                    is_first_push,
                )

            elif step_type in ("skill_notify", "feishu_notify", "notify"):
                metadata = task.metadata_json if isinstance(task.metadata_json, dict) else {}
                fn_cfg = metadata.get("feishu_notify") if isinstance(metadata.get("feishu_notify"), dict) else {}
                if fn_cfg.get("enabled") is False:
                    logger.info("Pipeline step %d (%s): feishu_notify is disabled, skipping push", idx, step_type)
                    step_results.append({
                        "step": idx,
                        "type": step_type,
                        "status": "skipped",
                        "reason": "飞书消息通知配置已关闭，跳过推送",
                    })
                    continue

                renderer_name = (
                    step.get("renderer")
                    or (step.get("params") or {}).get("renderer")
                    or metadata.get("renderer")
                    or "sales_card"
                )
                renderer_func = get_card_renderer(renderer_name)
                card_payload = renderer_func(
                    query_rows,
                    title=task.title,
                    is_first_push=is_first_push,
                    **(step.get("card_params") or {}),
                )
                notify_req = FeishuAppNotifyRequest(
                    scheduled_task_id=task.id,
                    tenant_id=task.tenant_id,
                    webhooks=list(fn_cfg.get("webhooks") or []),
                    mobiles=list(fn_cfg.get("mobiles") or []),
                    open_ids=list(fn_cfg.get("open_ids") or []),
                    chat_ids=list(fn_cfg.get("chat_ids") or []),
                    binding_id=fn_cfg.get("binding_id"),
                    card=card_payload,
                )
                notify_res = feishu_app_notify(notify_req, db)
                step_results.append({
                    "step": idx,
                    "type": step_type,
                    "notify_res": notify_res,
                })
                if not notify_res.get("ok"):
                    err = notify_res.get("error") or "飞书推送失败"
                    raise RuntimeError(f"飞书推送失败: {err}")
                logger.info("Pipeline step %d (notify): push succeeded: %s", idx, notify_res)

        # Mark succeeded
        run.status = "succeeded"
        run.error = None
        has_push = any(s.get("type") in ("skill_notify", "feishu_notify", "notify") and s.get("status") != "skipped" for s in step_results)
        push_summary = "已成功装配并推送至飞书" if has_push else "飞书消息通知未启用（已跳过推送）"
        push_first_note = "（当日首次推送，增量置为0）" if is_first_push else ""
        run.result_summary = (
            f"流水线执行成功{push_first_note}：共完成 {len(steps)} 个阶段，"
            f"查询到 {len(query_rows)} 条数据记录，{push_summary}。"
        )
        run.trace_json = {
            "execution_mode": "pipeline",
            "steps_count": len(steps),
            "step_results": step_results,
            "rows_count": len(query_rows),
        }
        run.finished_at = utc_now()

        if run.session_id:
            from app.db.models import ChatSession, Message
            from app.scheduled_tasks.service import automatic_task_message

            user_msg = Message(
                tenant_id=task.tenant_id,
                session_id=run.session_id,
                role="user",
                content=automatic_task_message(task),
                metadata_json={"source": "scheduled_task", "scheduled_task_id": task.id, "run_id": run.id},
            )
            query_info = f"{len(query_rows)} 条数据记录" if query_rows else "0 条记录"
            notify_info = "原生卡片 2.0 已成功组装并通过 Webhook/应用通道推送" if has_push else "未启用推送（已跳过出站通知）"
            task_title = task.title or "定时任务"
            asst_content = (
                f"✅ **{task_title}流水线执行成功**\n\n"
                f"- **执行模式**：确定性流水线 (Pipeline Engine)\n"
                f"- **数据查询**：已完成取数，共获取 {query_info}\n"
                f"- **飞书播报**：{notify_info}\n"
                f"- **状态**：成功 (Succeeded)"
            )
            asst_msg = Message(
                tenant_id=task.tenant_id,
                session_id=run.session_id,
                role="assistant",
                content=asst_content,
                metadata_json={
                    "source": "scheduled_task",
                    "scheduled_task_id": task.id,
                    "run_id": run.id,
                    "trace": run.trace_json,
                },
            )
            db.add(user_msg)
            db.add(asst_msg)
            sess = db.get(ChatSession, run.session_id)
            if sess:
                sess.updated_at = utc_now()
                db.add(sess)

            _record_scheduled_task_stream_event(
                db,
                run,
                run.session_id,
                1,
                {
                    "event": "complete",
                    "data": {
                        "reply": run.result_summary,
                        "sessionId": run.session_id,
                    },
                },
            )

        _finish_task_schedule(db, task, run.scheduled_for, run.status, manual)

    except Exception as exc:
        logger.exception("Pipeline execution failed for task %s", task.id)
        run.status = "failed"
        run.error = str(exc)
        run.finished_at = utc_now()
        run.trace_json = {
            "execution_mode": "pipeline",
            "error": str(exc),
            "step_results": step_results,
        }
        if run.session_id:
            from app.db.models import ChatSession, Message
            from app.scheduled_tasks.service import automatic_task_message

            user_msg = Message(
                tenant_id=task.tenant_id,
                session_id=run.session_id,
                role="user",
                content=automatic_task_message(task),
                metadata_json={"source": "scheduled_task", "scheduled_task_id": task.id, "run_id": run.id},
            )
            task_title = task.title or "定时任务"
            asst_msg = Message(
                tenant_id=task.tenant_id,
                session_id=run.session_id,
                role="assistant",
                content=f"❌ **{task_title}流水线执行失败**\n\n- **错误原因**：{exc}",
                metadata_json={"source": "scheduled_task", "scheduled_task_id": task.id, "run_id": run.id, "error": str(exc)},
            )
            db.add(user_msg)
            db.add(asst_msg)
            sess = db.get(ChatSession, run.session_id)
            if sess:
                sess.updated_at = utc_now()
                db.add(sess)

            _record_scheduled_task_stream_event(
                db,
                run,
                run.session_id,
                0,
                {
                    "event": "error",
                    "data": {"message": str(exc), "sessionId": run.session_id},
                },
            )
        _finish_task_schedule(db, task, run.scheduled_for, "failed", manual)

    finally:
        task.lease_owner = None
        task.lease_until = None
        run.updated_at = utc_now()
        task.updated_at = utc_now()
        db.add(task)
        db.add(run)
        db.commit()
        db.refresh(run)

    return run

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
from app.db.models import ChatSession, Message, ScheduledTask, ScheduledTaskRun, utc_now
from app.scheduled_tasks.renderers import (
    CARD_RENDERERS,
    build_generic_feishu_card,
    build_sales_feishu_card,
    get_card_renderer,
)
from app.scheduled_tasks.renderers.base import fmt_diff_styled, fmt_val_styled

logger = logging.getLogger(__name__)

# Backward compatibility alias
_fmt_val_styled = fmt_val_styled
_fmt_diff_styled = fmt_diff_styled
_build_generic_feishu_card = build_generic_feishu_card

__all__ = [
    "CARD_RENDERERS",
    "_build_generic_feishu_card",
    "_fmt_diff_styled",
    "_fmt_val_styled",
    "apply_push_incremental_diff",
    "build_generic_feishu_card",
    "build_sales_feishu_card",
    "execute_pipeline_scheduled_task",
    "extract_sales_push_snapshot",
    "get_card_renderer",
    "is_task_first_push_today",
    "sanitize_hourly_cumulative_trend",
]


def sanitize_hourly_cumulative_trend(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """对 24 小时分时段累计数据执行 Forward-Fill 结转与当前小时大盘快照校准。

    业务背景：
    分时走势图展示的是「当日累计销售额」。当定时任务在整点过几分钟（如 09:03）执行时，
    当前小时由于各店铺异步抓取时延，仅有部分店铺入库，导致当前小时的累计值断崖式低于上一小时。
    保底校准规则：
    1. 提取大盘/电商整体当前的最新累计总净销；
    2. 按 00时..23时 顺序遍历，保证累计序列单调不减（Forward-Fill）；
    3. 当前最后一个有数据的小时若低于大盘总净销，向前补齐至大盘总净销；
    4. 未到达的未来时段保持 0.0。
    """
    total_net: float | None = None
    for r in rows:
        cat = str(r.get("category") or "").strip()
        item = str(r.get("item") or r.get("name") or "").strip()
        if (cat == "大盘" and item == "电商整体") or item == "电商整体":
            try:
                total_net = float(r.get("净销_万") or r.get("price") or 0.0)
                break
            except (ValueError, TypeError):
                pass

    hourly_rows = [
        r for r in rows
        if str(r.get("category") or "").strip() in ("24小时环比", "hourly_trend")
    ]
    if not hourly_rows:
        return rows

    def _parse_hour(r: dict[str, Any]) -> int:
        it = str(r.get("item") or r.get("hour") or "").strip()
        digits = "".join(ch for ch in it if ch.isdigit())
        return int(digits) if digits else -1

    valid_entries: list[tuple[int, dict[str, Any]]] = []
    for r in hourly_rows:
        hr_val = _parse_hour(r)
        if 0 <= hr_val <= 23:
            valid_entries.append((hr_val, r))
    valid_entries.sort(key=lambda x: x[0])

    if not valid_entries:
        return rows

    last_active_idx = -1
    for idx, (_h, r) in enumerate(valid_entries):
        curr_val = float(r.get("运营净销_万") or r.get("净销_万") or 0.0)
        if curr_val > 0:
            last_active_idx = idx

    if last_active_idx < 0:
        return rows

    max_seen = 0.0
    for idx, (_h, r) in enumerate(valid_entries):
        if idx > last_active_idx:
            continue

        curr_val = float(r.get("运营净销_万") or r.get("净销_万") or 0.0)
        if idx == last_active_idx and total_net is not None and total_net > max_seen:
            curr_val = max(curr_val, total_net)

        if curr_val < max_seen:
            curr_val = max_seen
        else:
            max_seen = curr_val

        curr_val = round(curr_val, 2)
        r["运营净销_万"] = curr_val
        r["净销_万"] = curr_val

        if "运营增量_万" in r:
            yest_val = float(r.get("运营增量_万") or 0.0)
            r["环比增量_万"] = round(curr_val - yest_val, 2)

    return rows


def _run_has_successful_push(run: ScheduledTaskRun) -> bool:
    """该 run 是否真的成功推送过飞书（notify 步骤存在且 notify_res.ok）。

    仅凭 ``run.status == "succeeded"`` 不够：只取数、推送被关闭的 run 同样会标记
    succeeded，若把它算作“当日已推送”，当天真正的首推会被误判为非首推，增量归零失效。
    """

    trace = run.trace_json if isinstance(run.trace_json, dict) else {}
    for step in trace.get("step_results") or []:
        if not isinstance(step, dict):
            continue
        if step.get("type") not in ("skill_notify", "feishu_notify", "notify"):
            continue
        notify_res = step.get("notify_res")
        if isinstance(notify_res, dict) and notify_res.get("ok"):
            return True
    return False


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
        if r.created_at and _run_has_successful_push(r) and (
            r.created_at.replace(tzinfo=UTC).astimezone(cst)
            if r.created_at.tzinfo is None
            else r.created_at.astimezone(cst)
        ).strftime("%Y-%m-%d") == today_cst_str
    ]

    return len(succeeded_today) == 0


def extract_sales_push_snapshot(rows: list[dict[str, Any]]) -> dict[str, dict[str, float]]:
    """从查询结果行中提取核心指标与各维度累计值快照。"""
    snapshot: dict[str, dict[str, float]] = {}
    for r in rows:
        cat = str(r.get("category") or "").strip()
        item = str(r.get("item") or r.get("name") or "").strip()
        key = f"{cat}:{item}" if cat else item
        if not key:
            continue
        try:
            net = float(r.get("净销_万") or r.get("price") or 0.0)
            ops = float(r.get("运营净销_万") or 0.0)
            live = float(r.get("达播净销_万") or 0.0)
            snapshot[key] = {
                "net": net,
                "ops": ops,
                "live": live,
            }
        except (ValueError, TypeError):
            continue
    return snapshot


def apply_push_incremental_diff(
    rows: list[dict[str, Any]],
    task: ScheduledTask,
    is_first_push: bool,
    db: Session,
) -> tuple[list[dict[str, Any]], dict[str, Any], str | None]:
    """基于上一次成功推送的历史快照，在内存中重新计算各维度与大盘的增量差值。

    返回: (处理后的行列表, 本次快照数据字典, 上次推送时间字符串或None)
    """
    rows = sanitize_hourly_cumulative_trend(rows)
    current_snapshot = extract_sales_push_snapshot(rows)
    cst = timezone(timedelta(hours=8))
    today_cst_str = datetime.now(cst).strftime("%Y-%m-%d")

    metadata = task.metadata_json if isinstance(task.metadata_json, dict) else {}
    last_record = metadata.get("last_push_snapshot")
    prev_push_time: str | None = None
    last_metrics: dict[str, dict[str, float]] | None = None

    if not is_first_push and isinstance(last_record, dict) and last_record.get("date") == today_cst_str:
        last_metrics = last_record.get("metrics")
        prev_push_time = last_record.get("pushed_at")

    # 如果是当日首次推送，所有增量统一归零（保证首播不出现异常跨日大差值）
    if is_first_push:
        for r in rows:
            cat = str(r.get("category") or "").strip()
            if cat in ("24小时环比", "hourly_trend"):
                continue
            r["环比增量_万"] = 0.0
            r["运营增量_万"] = 0.0
            r["达播增量_万"] = 0.0
            if "diff_amount" in r:
                r["diff_amount"] = 0.0
        return rows, current_snapshot, None

    # 如果非首次推送且存在上一轮成功推送快照，执行快照差值计算
    if last_metrics:
        for r in rows:
            cat = str(r.get("category") or "").strip()
            if cat in ("24小时环比", "hourly_trend"):
                continue
            item = str(r.get("item") or r.get("name") or "").strip()
            key = f"{cat}:{item}" if cat else item
            prev = last_metrics.get(key)
            if not prev and item:
                for k, v in last_metrics.items():
                    if k.endswith(f":{item}") or k == item:
                        prev = v
                        break

            if prev:
                curr_net = float(r.get("净销_万") or r.get("price") or 0.0)
                prev_net = float(prev.get("net", curr_net))
                diff_net = round(curr_net - prev_net, 2)
                r["环比增量_万"] = diff_net
                r["diff_amount"] = diff_net

                if "运营净销_万" in r or "运营增量_万" in r:
                    curr_ops = float(r.get("运营净销_万") or 0.0)
                    prev_ops = float(prev.get("ops", curr_ops))
                    r["运营增量_万"] = round(curr_ops - prev_ops, 2)

                if "达播净销_万" in r or "达播增量_万" in r:
                    curr_live = float(r.get("达播净销_万") or 0.0)
                    prev_live = float(prev.get("live", curr_live))
                    r["达播增量_万"] = round(curr_live - prev_live, 2)

    return rows, current_snapshot, prev_push_time


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
    # Notify 分支才会赋值；空 steps / 仅 query / 推送关闭的任务也必须能收尾。
    prev_push_time: str | None = None
    current_snapshot: dict[str, dict[str, float]] = {}
    is_first_push = is_task_first_push_today(task, run, db)
    if is_first_push:
        logger.info(
            "Task %s (run %s) identified as FIRST push of today. Increment logic set to 0.00万.",
            task.id,
            run.id,
        )

    # Immediately initialize user message so session is never empty even if interrupted
    if run.session_id:
        from app.scheduled_tasks.service import automatic_task_message

        existing_user_msg = db.exec(
            select(Message).where(Message.session_id == run.session_id, Message.role == "user")
        ).first()
        if not existing_user_msg:
            user_msg = Message(
                tenant_id=task.tenant_id,
                session_id=run.session_id,
                role="user",
                content=automatic_task_message(task),
                metadata_json={"source": "scheduled_task", "scheduled_task_id": task.id, "run_id": run.id},
            )
            db.add(user_msg)
            db.commit()

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
                step_rows = list(query_res.rows or [])
                query_rows.extend(step_rows)
                step_results.append({
                    "step": idx,
                    "type": "query",
                    "template_id": template_id,
                    "rows_count": len(step_rows),
                    "execution_time_ms": query_res.execution_time_ms,
                    "is_first_push": is_first_push,
                })
                logger.info(
                    "Pipeline step %d (query): template %s returned %d rows in %.2fms (is_first_push=%s)",
                    idx,
                    template_id,
                    len(step_rows),
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

                # 核心：计算较上一轮实际推送的增量差值
                from app.reporting.sales_metrics import inject_dabo_matrix_rows

                query_rows = inject_dabo_matrix_rows(query_rows)

                processed_rows, current_snapshot, prev_push_time = apply_push_incremental_diff(
                    query_rows, task, is_first_push, db
                )

                renderer_name = (
                    step.get("renderer")
                    or (step.get("params") or {}).get("renderer")
                    or metadata.get("renderer")
                    or "sales_card"
                )
                renderer_func = get_card_renderer(renderer_name)
                card_params = dict(step.get("card_params") or {})
                if prev_push_time and "prev_push_time" not in card_params:
                    card_params["prev_push_time"] = prev_push_time

                card_payload = renderer_func(
                    processed_rows,
                    title=task.title,
                    is_first_push=is_first_push,
                    **card_params,
                )

                from app.scheduled_tasks.service import resolve_task_share_link_url

                try:
                    from app.reporting.realtime_dashboard_syncer import sync_task_artifact_report

                    sync_task_artifact_report(db, task, query_rows)
                except Exception as sync_err:  # noqa: BLE001
                    logger.warning("Pipeline step %d: failed to sync artifact report: %s", idx, sync_err)

                artifact_url = resolve_task_share_link_url(db, task) or ""
                if not artifact_url and not str(metadata.get("artifact_share_link_id") or "").strip():
                    # 旧任务只在 metadata 里存过 URL（没有 link_id），保留兼容
                    artifact_url = str(metadata.get("artifact_share_url") or "").strip()
                artifact_title = str(metadata.get("artifact_title") or "在线交互式 HTML 报告").strip()
                if artifact_url and isinstance(card_payload, dict):
                    card_obj = card_payload.get("card") if isinstance(card_payload.get("card"), dict) else card_payload
                    is_schema_v2 = card_obj.get("schema") == "2.0" or "body" in card_obj
                    btn_text = f"📊 查看完整 HTML 报告: {artifact_title}"
                    if is_schema_v2:
                        card_body = card_obj.get("body", {}) if isinstance(card_obj.get("body"), dict) else {}
                        elements = card_body.get("elements")
                        if isinstance(elements, list):
                            elements.append({
                                "tag": "button",
                                "text": {
                                    "tag": "plain_text",
                                    "content": btn_text,
                                },
                                "type": "primary",
                                "behaviors": [
                                    {
                                        "type": "open_url",
                                        "default_url": artifact_url,
                                        "pc_url": artifact_url,
                                        "android_url": artifact_url,
                                        "ios_url": artifact_url,
                                    }
                                ],
                            })
                    else:
                        card_body = card_obj.get("body", {}) if isinstance(card_obj.get("body"), dict) else {}
                        elements = card_body.get("elements") or card_obj.get("elements")
                        if isinstance(elements, list):
                            elements.append({
                                "tag": "action",
                                "actions": [
                                    {
                                        "tag": "button",
                                        "text": {
                                            "tag": "plain_text",
                                            "content": btn_text,
                                        },
                                        "type": "primary",
                                        "url": artifact_url,
                                    }
                                ],
                            })
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

                # 推送成功，原子持久化本次快照作为下次执行的增量对比基准
                if current_snapshot:
                    cst = timezone(timedelta(hours=8))
                    now_cst = datetime.now(cst)
                    updated_meta = dict(task.metadata_json or {})
                    updated_meta["last_push_snapshot"] = {
                        "date": now_cst.strftime("%Y-%m-%d"),
                        "pushed_at": now_cst.strftime("%H:%M"),
                        "metrics": current_snapshot,
                    }
                    task.metadata_json = updated_meta
                    db.add(task)
                    db.commit()

                logger.info("Pipeline step %d (notify): push succeeded: %s", idx, notify_res)

            else:
                raise ValueError(f"未知的流水线步骤类型: {step_type}")

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
            "snapshot_applied": bool(prev_push_time),
            "prev_push_time": prev_push_time,
        }
        run.finished_at = utc_now()

        if run.session_id:
            from app.scheduled_tasks.service import automatic_task_message

            existing_user_msg = db.exec(
                select(Message).where(Message.session_id == run.session_id, Message.role == "user")
            ).first()
            if not existing_user_msg:
                user_msg = Message(
                    tenant_id=task.tenant_id,
                    session_id=run.session_id,
                    role="user",
                    content=automatic_task_message(task),
                    metadata_json={"source": "scheduled_task", "scheduled_task_id": task.id, "run_id": run.id},
                )
                db.add(user_msg)

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
            from app.scheduled_tasks.service import automatic_task_message

            existing_user_msg = db.exec(
                select(Message).where(Message.session_id == run.session_id, Message.role == "user")
            ).first()
            if not existing_user_msg:
                user_msg = Message(
                    tenant_id=task.tenant_id,
                    session_id=run.session_id,
                    role="user",
                    content=automatic_task_message(task),
                    metadata_json={"source": "scheduled_task", "scheduled_task_id": task.id, "run_id": run.id},
                )
                db.add(user_msg)

            task_title = task.title or "定时任务"
            asst_msg = Message(
                tenant_id=task.tenant_id,
                session_id=run.session_id,
                role="assistant",
                content=f"❌ **{task_title}流水线执行失败**\n\n- **错误原因**：{exc}",
                metadata_json={"source": "scheduled_task", "scheduled_task_id": task.id, "run_id": run.id, "error": str(exc)},
            )
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

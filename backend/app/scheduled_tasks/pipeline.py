"""Pipeline Execution Engine for Scheduled Tasks.

Executes deterministic, multi-step pipeline tasks (Query -> Process -> Notify)
without LLM hallucination, context limits, or sandbox timeouts.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from sqlmodel import Session

from app.api.mock import FeishuAppNotifyRequest, feishu_app_notify
from app.data_query.service import execute_query_by_id
from app.db.models import ScheduledTask, ScheduledTaskRun, utc_now

logger = logging.getLogger(__name__)


def _fmt_val_styled(v: Any, is_core: bool = False) -> str:
    try:
        f = float(v or 0)
        if abs(f) < 0.0001:
            return "<font color='grey'>0.00万</font>"
        txt = f"{f:.2f}万"
        return f"**{txt}**" if is_core else txt
    except (ValueError, TypeError):
        return "<font color='grey'>0.00万</font>"


def _fmt_diff_styled(v: Any, is_core: bool = False) -> str:
    try:
        f = float(v or 0)
        if abs(f) < 0.0001:
            return "<font color='grey'>0.00万</font>"
        sign = "+" if f > 0 else ""
        txt = f"{sign}{f:.2f}万"
        color = "green" if f > 0 else "red"
        body = f"**{txt}**" if is_core else txt
        return f"<font color='{color}'>{body}</font>"
    except (ValueError, TypeError):
        return "<font color='grey'>0.00万</font>"


def build_sales_feishu_card(
    rows: list[dict[str, Any]],
    title: str = "",
    template: str = "blue",
) -> dict[str, Any]:
    """基于实时销售查询结果行构建原生飞书卡片 2.0：

    1. 顶部指标汇总栏 (column_set): 今日净销、达播净销、运营端净销
    2. 整体销售播报摘要
    3. 中间板块（店铺正负增量动态排行 Top 5）
    4. 品牌与渠道明细表格 (table): 7 列自适应宽度
    5. 24小时时段环比柱状图 (chart): 今日 vs 昨日 24 时段走势对比
    """
    brand_rows: list[tuple[str, dict[str, Any]]] = []
    hourly_rows: list[dict[str, Any]] = []
    summary_row: dict[str, Any] | None = None
    pos_increment_shops: list[dict[str, Any]] = []
    neg_increment_shops: list[dict[str, Any]] = []

    for r in rows:
        cat = str(r.get("category") or "")
        item = str(r.get("item") or "")
        if cat == "24小时环比":
            hourly_rows.append(r)
        elif cat == "大盘" and item == "电商整体":
            summary_row = r
            brand_rows.append(("电商整体", r))
        elif cat in ("店铺正增量Top5", "正增量Top5"):
            pos_increment_shops.append(r)
        elif cat in ("店铺负增量Top5", "负增量Top5"):
            neg_increment_shops.append(r)
        elif cat == "可复美" and item == "整体":
            brand_rows.append(("可复美整体", r))
        elif cat == "可复美":
            brand_rows.append((f"{item}可复美", r))
        elif cat == "可丽金" and item == "整体":
            brand_rows.append(("可丽金整体", r))
        elif cat == "可丽金":
            brand_rows.append((f"{item}可丽金", r))
        else:
            brand_rows.append((item or cat, r))

    # If this is not a sales dataset (no summary row and no brand rows), fall back to generic card
    if not summary_row and not pos_increment_shops and not hourly_rows:
        return _build_generic_feishu_card(rows, title=title, template=template)

    tot_net = float(summary_row.get("净销_万") or 0) if summary_row else 0.0
    tot_ops = float(summary_row.get("运营净销_万") or 0) if summary_row else 0.0
    tot_live = float(summary_row.get("达播净销_万") or 0) if summary_row else 0.0
    tot_diff = float(summary_row.get("环比增量_万") or 0) if summary_row else 0.0
    tot_ops_diff = float(summary_row.get("运营增量_万") or 0) if summary_row else 0.0
    tot_live_diff = float(summary_row.get("达播增量_万") or 0) if summary_row else 0.0

    update_time = None
    if summary_row:
        update_time = (
            summary_row.get("数据更新时间")
            or summary_row.get("更新时间")
            or summary_row.get("data_time")
        )
    if not update_time and rows:
        for r in rows:
            t_val = r.get("数据更新时间") or r.get("更新时间") or r.get("data_time")
            if t_val:
                update_time = str(t_val).strip()
                break

    if not update_time:
        update_time = datetime.now(UTC).strftime("%m-%d %H:%M")
    elif len(update_time) >= 16 and update_time[4] == "-" and update_time[7] == "-":
        update_time = update_time[5:16]

    card_title = title or f"📊 实时销售播报 · {update_time}（当日累计）"

    # Assemble elements
    elements: list[dict[str, Any]] = [
        {
            "tag": "markdown",
            "content": f"**实时汇总** <font color='grey'>（数据更新至 {update_time}）</font>",
            "text_align": "left",
            "text_size": "normal",
        },
        {
            "tag": "column_set",
            "flex_mode": "bisect",
            "background_style": "grey",
            "horizontal_spacing": "8px",
            "horizontal_align": "left",
            "columns": [
                {
                    "tag": "column",
                    "width": "weighted",
                    "weight": 1,
                    "vertical_align": "top",
                    "vertical_spacing": "8px",
                    "elements": [
                        {
                            "tag": "markdown",
                            "content": (
                                f"<font color='grey'>今日净销</font>\n"
                                f"**{tot_net:.2f}万**\n"
                                f"{_fmt_diff_styled(tot_diff)}"
                            ),
                            "text_align": "center",
                            "text_size": "normal",
                        }
                    ],
                },
                {
                    "tag": "column",
                    "width": "weighted",
                    "weight": 1,
                    "vertical_align": "top",
                    "vertical_spacing": "8px",
                    "elements": [
                        {
                            "tag": "markdown",
                            "content": (
                                f"<font color='grey'>达播净销</font>\n"
                                f"**{tot_live:.2f}万**\n"
                                f"{_fmt_diff_styled(tot_live_diff)}"
                            ),
                            "text_align": "center",
                            "text_size": "normal",
                        }
                    ],
                },
                {
                    "tag": "column",
                    "width": "weighted",
                    "weight": 1,
                    "vertical_align": "top",
                    "vertical_spacing": "8px",
                    "elements": [
                        {
                            "tag": "markdown",
                            "content": (
                                f"<font color='grey'>运营端净销</font>\n"
                                f"**{tot_ops:.2f}万**\n"
                                f"{_fmt_diff_styled(tot_ops_diff)}"
                            ),
                            "text_align": "center",
                            "text_size": "normal",
                        }
                    ],
                },
            ],
        },
        {
            "tag": "markdown",
            "content": (
                f"📢 **整体销售播报**：截止 {update_time}，电商整体净销 **{tot_net:.2f}万**"
                f"（较上一时刻增量 {_fmt_diff_styled(tot_diff, is_core=True)}，"
                f"其中运营端 **{tot_ops:.2f}万**，较上一时刻增量 {_fmt_diff_styled(tot_ops_diff, is_core=True)}；"
                f"达播端 **{tot_live:.2f}万**，较上一时刻增量 {_fmt_diff_styled(tot_live_diff, is_core=True)}），各渠道运行平稳。"
            ),
            "text_align": "left",
            "text_size": "normal",
        },
    ]

    # Dynamic Top 5 shops
    if pos_increment_shops or neg_increment_shops:
        pos_lines = []
        for i, s in enumerate(pos_increment_shops[:5], 1):
            name = s.get("item") or s.get("平台店铺") or "未知店铺"
            net = float(s.get("净销_万") or 0)
            diff = float(s.get("环比增量_万") or 0)
            pos_lines.append(f"{i}. **{name}**\n   增量: <font color='green'>**+{diff:.2f}万**</font> | 净销: {net:.2f}万")

        neg_lines = []
        for i, s in enumerate(neg_increment_shops[:5], 1):
            name = s.get("item") or s.get("平台店铺") or "未知店铺"
            net = float(s.get("净销_万") or 0)
            diff = float(s.get("环比增量_万") or 0)
            diff_str = f"{diff:.2f}万" if diff < 0 else f"-{abs(diff):.2f}万"
            neg_lines.append(f"{i}. **{name}**\n   增量: <font color='red'>**{diff_str}**</font> | 净销: {net:.2f}万")

        if not neg_lines:
            neg_lines = ["*(其余活跃店铺增量均为正或持平)*"]

        elements.extend([
            {
                "tag": "markdown",
                "content": "**🏪 实时销售数据汇报：店铺正负增量动态排行（较上一时刻 Top 5）**",
                "text_align": "left",
                "text_size": "normal",
            },
            {
                "tag": "column_set",
                "flex_mode": "bisect",
                "background_style": "grey",
                "horizontal_spacing": "12px",
                "columns": [
                    {
                        "tag": "column",
                        "width": "weighted",
                        "weight": 1,
                        "vertical_align": "top",
                        "vertical_spacing": "4px",
                        "elements": [
                            {
                                "tag": "markdown",
                                "content": "📈 <font color='green'>**正增量领跑 Top 5**</font>\n" + ("\n".join(pos_lines) if pos_lines else "暂无"),
                                "text_size": "normal",
                            }
                        ],
                    },
                    {
                        "tag": "column",
                        "width": "weighted",
                        "weight": 1,
                        "vertical_align": "top",
                        "vertical_spacing": "4px",
                        "elements": [
                            {
                                "tag": "markdown",
                                "content": "📉 <font color='red'>**负增量预警 Top 5**</font>\n" + "\n".join(neg_lines),
                                "text_size": "normal",
                            }
                        ],
                    },
                ],
            },
        ])

    # Table rows
    table_rows: list[dict[str, Any]] = []
    for display_name, r in brand_rows:
        is_core_row = display_name in ("电商整体", "可复美整体", "可丽金整体")
        brand_label = f"**{display_name}**" if is_core_row else display_name
        table_rows.append({
            "brand": brand_label,
            "net_sales": _fmt_val_styled(r.get("净销_万"), is_core=is_core_row),
            "net_diff": _fmt_diff_styled(r.get("环比增量_万"), is_core=is_core_row),
            "ops_net_sales": _fmt_val_styled(r.get("运营净销_万"), is_core=is_core_row),
            "ops_diff": _fmt_diff_styled(r.get("运营增量_万"), is_core=is_core_row),
            "live_net_sales": _fmt_val_styled(r.get("达播净销_万"), is_core=is_core_row),
            "live_diff": _fmt_diff_styled(r.get("达播增量_万"), is_core=is_core_row),
        })

    elements.extend([
        {
            "tag": "markdown",
            "content": "**品牌与渠道销售明细（万元）**",
            "text_align": "left",
        },
        {
            "tag": "table",
            "columns": [
                {"data_type": "lark_md", "name": "brand", "display_name": "品牌", "horizontal_align": "left", "width": "auto"},
                {"data_type": "lark_md", "name": "net_sales", "display_name": "净销", "horizontal_align": "right", "width": "auto"},
                {"data_type": "lark_md", "name": "net_diff", "display_name": "环比净销", "horizontal_align": "right", "width": "auto"},
                {"data_type": "lark_md", "name": "ops_net_sales", "display_name": "运营端净销", "horizontal_align": "right", "width": "auto"},
                {"data_type": "lark_md", "name": "ops_diff", "display_name": "环比净销", "horizontal_align": "right", "width": "auto"},
                {"data_type": "lark_md", "name": "live_net_sales", "display_name": "达播端净销", "horizontal_align": "right", "width": "auto"},
                {"data_type": "lark_md", "name": "live_diff", "display_name": "环比净销", "horizontal_align": "right", "width": "auto"},
            ],
            "rows": table_rows,
            "row_height": "low",
            "freeze_first_column": False,
            "header_style": {
                "background_style": "grey",
                "bold": True,
                "lines": 1,
            },
            "page_size": 10,
        },
    ])

    # 24h Hourly chart
    if hourly_rows:
        chart_values = []
        for hr in hourly_rows:
            h_str = str(hr.get("item", ""))
            v_today = float(hr.get("运营净销_万") or 0)
            v_yest = float(hr.get("运营增量_万") or 0)
            chart_values.append({"hour": h_str, "type": "今日", "value": v_today})
            chart_values.append({"hour": h_str, "type": "昨日", "value": v_yest})

        elements.append({
            "tag": "chart",
            "chart_spec": {
                "type": "bar",
                "title": {
                    "text": "24小时时段走势环比（今日 vs 昨日，万元）",
                },
                "data": {
                    "values": chart_values,
                },
                "xField": [
                    "hour",
                    "type",
                ],
                "yField": "value",
                "seriesField": "type",
                "legends": {
                    "visible": True,
                    "orient": "bottom",
                },
            },
            "preview": True,
            "color_theme": "converse",
            "height": "300px",
        })

    return {
        "msg_type": "interactive",
        "card": {
            "schema": "2.0",
            "config": {
                "wide_screen_mode": True,
                "update_multi": True,
            },
            "header": {
                "title": {
                    "tag": "plain_text",
                    "content": card_title,
                },
                "template": template or "blue",
            },
            "body": {
                "elements": elements,
            },
        },
    }


def _build_generic_feishu_card(
    rows: list[dict[str, Any]],
    title: str = "",
    template: str = "blue",
) -> dict[str, Any]:
    card_title = title or "📊 定时任务数据推送"
    elements: list[dict[str, Any]] = [
        {
            "tag": "div",
            "text": {
                "tag": "lark_md",
                "content": f"**执行完成**：共获取 {len(rows)} 条数据记录。",
            },
        }
    ]
    if rows:
        sample_keys = list(rows[0].keys())[:6]
        cols = [{"data_type": "lark_md", "name": k, "display_name": k, "width": "auto"} for k in sample_keys]
        table_rows = [{k: str(r.get(k, "")) for k in sample_keys} for r in rows[:15]]
        elements.append({
            "tag": "table",
            "columns": cols,
            "rows": table_rows,
            "row_height": "low",
            "page_size": 10,
        })

    return {
        "msg_type": "interactive",
        "card": {
            "schema": "2.0",
            "config": {"wide_screen_mode": True},
            "header": {
                "title": {"tag": "plain_text", "content": card_title},
                "template": template,
            },
            "body": {"elements": elements},
        },
    }


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

    try:
        # Step 1: Execute query steps
        for idx, step in enumerate(steps, 1):
            step_type = step.get("type")
            if step_type == "query":
                template_id = step.get("template_id")
                if not template_id:
                    raise ValueError(f"流水线步骤 {idx} 缺少 template_id")
                params = dict(step.get("params") or {})
                query_res = execute_query_by_id(db, template_id, task.tenant_id, params)
                query_rows = query_res.rows
                step_results.append({
                    "step": idx,
                    "type": "query",
                    "template_id": template_id,
                    "rows_count": len(query_rows),
                    "execution_time_ms": query_res.execution_time_ms,
                })
                logger.info(
                    "Pipeline step %d (query): template %s returned %d rows in %.2fms",
                    idx,
                    template_id,
                    len(query_rows),
                    query_res.execution_time_ms,
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

                card_payload = build_sales_feishu_card(query_rows, title=task.title)
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
        run.result_summary = (
            f"流水线执行成功：共完成 {len(steps)} 个阶段，"
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
            asst_content = (
                f"✅ **实时销售播报流水线执行成功**\n\n"
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
            asst_msg = Message(
                tenant_id=task.tenant_id,
                session_id=run.session_id,
                role="assistant",
                content=f"❌ **流水线执行失败**\n\n- **错误原因**：{exc}",
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

"""Tests for deterministic pipeline scheduled tasks execution.

Verifies:
1. `build_sales_feishu_card` correctly assembles Feishu 2.0 interactive cards.
2. `_execute_prepared_scheduled_task` correctly branches to `execute_pipeline_scheduled_task`.
3. Succeeded runs update status, result_summary, and task schedule cleanly.
4. Outbound failures accurately mark the run and task as failed.
5. Schema serialization correctly preserves `execution_mode` and `pipeline_steps`.
"""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import patch

from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.data_query.models import QueryExecuteResult
from app.db.models import AgentProfile, ScheduledTaskRun, Tenant, User, new_id, utc_now
from app.scheduled_tasks.pipeline import build_sales_feishu_card, is_task_first_push_today
from app.scheduled_tasks.schema import ScheduledTaskCreateRequest
from app.scheduled_tasks.service import (
    _execute_prepared_scheduled_task,
    create_scheduled_task,
    scheduled_task_read,
)


def _test_session() -> Session:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    return Session(engine)


def _seed(db: Session) -> tuple[Tenant, User, AgentProfile]:
    tenant = Tenant(id="tenant_demo", name="Demo")
    user = User(
        id="user_demo",
        tenant_id="tenant_demo",
        username="demo",
        display_name="演示用户",
        role="admin",
        password_hash="test-hash",
    )
    agent = AgentProfile(
        id="agent_demo",
        tenant_id="tenant_demo",
        name="实时销售播报员工",
        is_overall=False,
        status="active",
    )
    db.add(tenant)
    db.add(user)
    db.add(agent)
    db.commit()
    return tenant, user, agent


def _sample_sales_rows() -> list[dict[str, object]]:
    return [
        {
            "category": "大盘",
            "item": "电商整体",
            "净销_万": 927.1,
            "环比增量_万": 50.66,
            "运营净销_万": 574.8,
            "运营增量_万": -267.41,
            "达播净销_万": 352.3,
            "达播增量_万": 318.07,
            "数据更新时间": "09-23 22:04",
        },
        {
            "category": "可复美",
            "item": "整体",
            "净销_万": 822.69,
            "环比增量_万": 48.94,
            "运营净销_万": 488.74,
            "运营增量_万": -253.38,
            "达播净销_万": 333.94,
            "达播增量_万": 302.32,
            "数据更新时间": "09-23 22:04",
        },
        {
            "category": "店铺正增量Top5",
            "item": "抖音-可复美旗舰店",
            "净销_万": 372.04,
            "环比增量_万": 40.87,
            "运营净销_万": 74.73,
            "运营增量_万": -256.44,
            "达播净销_万": 297.31,
            "达播增量_万": 297.31,
            "数据更新时间": "09-23 22:04",
        },
        {
            "category": "店铺负增量Top5",
            "item": "天猫-测试负增量店",
            "净销_万": 10.0,
            "环比增量_万": -5.2,
            "运营净销_万": 10.0,
            "运营增量_万": -5.2,
            "达播净销_万": 0,
            "达播增量_万": 0,
            "数据更新时间": "09-23 22:04",
        },
        {
            "category": "24小时环比",
            "item": "22时",
            "净销_万": 927.1,
            "环比增量_万": -268.06,
            "运营净销_万": 927.1,
            "运营增量_万": 1195.16,
            "达播净销_万": 0,
            "达播增量_万": 0,
            "数据更新时间": "09-23 22:04",
        },
    ]


def test_build_sales_feishu_card_structure() -> None:
    rows = _sample_sales_rows()
    card = build_sales_feishu_card(rows, title="实时销售播报（每10分钟）")
    assert card["msg_type"] == "interactive"
    assert card["card"]["schema"] == "2.0"
    elements = card["card"]["body"]["elements"]
    assert len(elements) >= 5

    # Check top metric column_set
    top_colset = next(e for e in elements if e.get("tag") == "column_set" and len(e.get("columns", [])) == 3)
    assert len(top_colset["columns"]) == 3

    # Check broadcast text
    summary_el = next(e for e in elements if "整体销售播报" in str(e.get("content", "")))
    assert "927.10万" in summary_el["content"]
    assert "运营端 **574.80万**" in summary_el["content"]

    # Check shop ranking bisect
    ranking_colset = next(e for e in elements if e.get("tag") == "column_set" and e.get("flex_mode") == "bisect")
    assert ranking_colset is not None

    # Check table
    table_el = next(e for e in elements if e.get("tag") == "table")
    assert len(table_el["columns"]) == 7
    assert all(c.get("width") == "auto" for c in table_el["columns"])

    # Check chart
    chart_el = next(e for e in elements if e.get("tag") == "chart")
    assert chart_el["chart_spec"]["type"] == "bar"


def test_execute_pipeline_scheduled_task_success() -> None:
    with _test_session() as db:
        _seed(db)
        user = db.get(User, "user_demo")

        req = ScheduledTaskCreateRequest(
            tenant_id="tenant_demo",
            agent_id="agent_demo",
            title="实时销售播报（每10分钟）",
            prompt="查询并播报实时销售",
            schedule_type="interval",
            schedule={"interval_seconds": 600},
            execution_mode="pipeline",
            pipeline_steps=[
                {"type": "query", "template_id": "qt_50f463a815af4801", "params": {"end_date": "今天"}},
                {"type": "skill_notify", "skill": "feishu-webhook-notify"},
            ],
            metadata={
                "feishu_notify": {
                    "enabled": True,
                    "webhooks": ["https://open.feishu.cn/open-apis/bot/v2/hook/mock"],
                }
            },
        )
        task = create_scheduled_task(db, req, user)
        assert task.execution_mode == "pipeline"
        assert len(task.pipeline_steps_json) == 2

        now = utc_now()
        run = ScheduledTaskRun(
            id=new_id("run"),
            tenant_id=task.tenant_id,
            scheduled_task_id=task.id,
            agent_id=task.agent_id,
            user_id=user.id,
            scheduled_for=now,
            status="running",
            started_at=now,
            created_at=now,
            updated_at=now,
        )
        db.add(run)
        db.commit()
        db.refresh(run)

        # Mock execute_query_by_id and feishu_app_notify
        mock_result = QueryExecuteResult(
            template_id="qt_50f463a815af4801",
            columns=["category", "item", "净销_万"],
            rows=_sample_sales_rows(),
            row_count=len(_sample_sales_rows()),
            execution_time_ms=12.5,
        )

        with (
            patch("app.scheduled_tasks.pipeline.execute_query_by_id", return_value=mock_result) as mock_query,
            patch("app.scheduled_tasks.pipeline.feishu_app_notify", return_value={"ok": True, "sent_count": 1, "failed_count": 0}) as _mock_notify,
        ):
            res_run = _execute_prepared_scheduled_task(db, task, run, manual=True)

            assert res_run.status == "succeeded"
            assert res_run.error is None
            assert "流水线执行成功" in res_run.result_summary
            assert res_run.trace_json["execution_mode"] == "pipeline"
            assert res_run.trace_json["steps_count"] == 2
            mock_query.assert_called_once_with(db, "qt_50f463a815af4801", "tenant_demo", {"end_date": "今天", "is_first_push": True})


def test_execute_pipeline_scheduled_task_failure() -> None:
    with _test_session() as db:
        _seed(db)
        user = db.get(User, "user_demo")

        req = ScheduledTaskCreateRequest(
            tenant_id="tenant_demo",
            agent_id="agent_demo",
            title="实时销售播报（每10分钟）",
            prompt="查询并播报实时销售",
            schedule_type="interval",
            schedule={"interval_seconds": 600},
            execution_mode="pipeline",
            pipeline_steps=[
                {"type": "query", "template_id": "qt_50f463a815af4801", "params": {"end_date": "今天"}},
                {"type": "skill_notify", "skill": "feishu-webhook-notify"},
            ],
        )
        task = create_scheduled_task(db, req, user)

        now = utc_now()
        run = ScheduledTaskRun(
            id=new_id("run"),
            tenant_id=task.tenant_id,
            scheduled_task_id=task.id,
            agent_id=task.agent_id,
            user_id=user.id,
            scheduled_for=now,
            status="running",
            started_at=now,
            created_at=now,
            updated_at=now,
        )
        db.add(run)
        db.commit()
        db.refresh(run)

        mock_result = QueryExecuteResult(
            template_id="qt_50f463a815af4801",
            columns=["category", "item", "净销_万"],
            rows=_sample_sales_rows(),
            row_count=len(_sample_sales_rows()),
            execution_time_ms=10.0,
        )

        with (
            patch("app.scheduled_tasks.pipeline.execute_query_by_id", return_value=mock_result),
            patch("app.scheduled_tasks.pipeline.feishu_app_notify", return_value={"ok": False, "error": "飞书 Webhook 响应超时"}),
        ):
            res_run = _execute_prepared_scheduled_task(db, task, run, manual=True)

            assert res_run.status == "failed"
            assert "飞书推送失败: 飞书 Webhook 响应超时" in str(res_run.error)
            assert task.last_status == "failed"


def test_scheduled_task_read_includes_pipeline_mode() -> None:
    with _test_session() as db:
        _seed(db)
        user = db.get(User, "user_demo")

        req = ScheduledTaskCreateRequest(
            tenant_id="tenant_demo",
            agent_id="agent_demo",
            title="实时销售播报（每10分钟）",
            prompt="查询并播报实时销售",
            schedule_type="interval",
            schedule={"interval_seconds": 600},
            execution_mode="pipeline",
            pipeline_steps=[
                {"type": "query", "template_id": "qt_50f463a815af4801", "params": {"end_date": "今天"}},
            ],
        )
        task = create_scheduled_task(db, req, user)
        read = scheduled_task_read(task)
        assert read.execution_mode == "pipeline"
        assert len(read.pipeline_steps) == 1
        assert read.pipeline_steps[0]["template_id"] == "qt_50f463a815af4801"


def test_build_sales_feishu_card_first_push() -> None:
    rows = _sample_sales_rows()
    card = build_sales_feishu_card(rows, title="实时销售播报 · 当日首次推送", is_first_push=True)
    elements = card["card"]["body"]["elements"]

    # 1. 顶部汇总栏指标的增量全为 0.00万
    top_colset = next(e for e in elements if e.get("tag") == "column_set" and len(e.get("columns", [])) == 3)
    for col in top_colset["columns"]:
        text = col["elements"][0]["content"]
        assert "0.00万" in text
        assert "+58.20万" not in text
        assert "+26.10万" not in text

    # 2. 播报文本应显示当日首次播报且增量计为0，且不对比前一日数据
    summary_el = next(e for e in elements if "整体销售播报" in str(e.get("content", "")))
    assert "当日首次播报，增量计为 <font color='grey'>0.00万</font>，不对比前一日数据" in summary_el["content"]
    assert "较上一时刻增量" not in summary_el["content"]

    # 3. 店铺正负增量排行应提示当日首次播报置灰信息，不展示正负增量柱
    assert not any(e.get("tag") == "column_set" and e.get("flex_mode") == "bisect" and "正增量领跑" in str(e) for e in elements)
    first_push_tip = next(e for e in elements if "不展示上一时刻店铺增量排行" in str(e.get("content", "")))
    assert first_push_tip is not None

    # 4. 表格中各渠道的增量必须全部置为 0.00万
    table_el = next(e for e in elements if e.get("tag") == "table")
    for r in table_el["rows"]:
        assert r["net_diff"] == "<font color='grey'>0.00万</font>"
        assert r["ops_diff"] == "<font color='grey'>0.00万</font>"
        assert r["live_diff"] == "<font color='grey'>0.00万</font>"

    # 5. 24小时走势图展示今日与昨日环比走势
    chart_el = next(e for e in elements if e.get("tag") == "chart")
    assert chart_el["chart_spec"]["title"]["text"] == "24小时时段走势环比（今日 vs 昨日，万元）"
    chart_values = chart_el["chart_spec"]["data"]["values"]
    assert any(item["type"] == "今日" for item in chart_values)
    assert any(item["type"] == "昨日" for item in chart_values)


def test_is_task_first_push_today_detection() -> None:
    with _test_session() as db:
        _seed(db)
        user = db.get(User, "user_demo")

        req = ScheduledTaskCreateRequest(
            tenant_id="tenant_demo",
            agent_id="agent_demo",
            title="实时销售播报（每日多时段）",
            prompt="查询并播报实时销售",
            schedule_type="daily",
            schedule={"times": ["08:00", "17:00", "23:58"]},
            execution_mode="pipeline",
            pipeline_steps=[{"type": "query", "template_id": "qt_50f463a815af4801"}],
        )
        task = create_scheduled_task(db, req, user)

        # 1. 首次执行（今日尚无成功记录）-> 判定为首次推送
        run1 = ScheduledTaskRun(
            id=new_id("run"),
            tenant_id=task.tenant_id,
            scheduled_task_id=task.id,
            agent_id=task.agent_id,
            user_id=user.id,
            scheduled_for=utc_now(),
            status="running",
            started_at=utc_now(),
            created_at=utc_now(),
            updated_at=utc_now(),
        )
        db.add(run1)
        db.commit()
        db.refresh(run1)

        assert is_task_first_push_today(task, run1, db) is True

        # 2. 将 run1 状态标记为 succeeded（且确有成功推送）
        run1.status = "succeeded"
        run1.finished_at = utc_now()
        run1.trace_json = {"step_results": [{"type": "notify", "notify_res": {"ok": True}}]}
        db.add(run1)
        db.commit()

        # 3. 当日第二次执行（如下午 17:00 的 run2）-> 判定为非首次推送
        run2 = ScheduledTaskRun(
            id=new_id("run"),
            tenant_id=task.tenant_id,
            scheduled_task_id=task.id,
            agent_id=task.agent_id,
            user_id=user.id,
            scheduled_for=utc_now() + timedelta(minutes=1),
            status="running",
            started_at=utc_now(),
            created_at=utc_now(),
            updated_at=utc_now(),
        )
        db.add(run2)
        db.commit()
        db.refresh(run2)

        assert is_task_first_push_today(task, run2, db) is False


def test_card_renderers_registry() -> None:
    from app.scheduled_tasks.renderers import (
        build_generic_feishu_card,
        build_sales_feishu_card,
        get_card_renderer,
    )

    assert get_card_renderer("sales_card") is build_sales_feishu_card
    assert get_card_renderer("sales_card_v2") is build_sales_feishu_card
    assert get_card_renderer("generic_table") is build_generic_feishu_card
    # Unknown renderer falls back to generic_table
    assert get_card_renderer("non_existent_custom_renderer") is build_generic_feishu_card
    # None falls back to sales_card for backward compatibility
    assert get_card_renderer(None) is build_sales_feishu_card

    # Generic card rendering
    sample_rows = [
        {"order_id": "OD1001", "refund_reason": "七天无理由", "amount": 128.5, "status": "待审核"},
        {"order_id": "OD1002", "refund_reason": "商品破损", "amount": 299.0, "status": "已同意"},
    ]
    generic_card = build_generic_feishu_card(
        sample_rows,
        title="🚨 售后退款实时监控",
        at_users=[{"id": "ou_test_user_1", "name": "售后主管"}],
    )
    assert generic_card["msg_type"] == "interactive"
    card_body = generic_card["card"]["body"]
    table_element = next(el for el in card_body["elements"] if el.get("tag") == "table")
    assert len(table_element["columns"]) == 4
    assert len(table_element["rows"]) == 2
    assert generic_card["card"]["header"]["title"]["content"] == "🚨 售后退款实时监控"


def test_pipeline_execution_with_generic_renderer_and_dynamic_title() -> None:
    from app.db.models import Message

    with _test_session() as db:
        tenant, user, agent = _seed(db)
        task = create_scheduled_task(
            db,
            ScheduledTaskCreateRequest(
                tenant_id=tenant.id,
                agent_id=agent.id,
                title="售后退款异常监控巡检",
                prompt="每10分钟监控售后退款异常",
                schedule_type="interval",
                schedule={"minutes": 10},
                execution_mode="pipeline",
                pipeline_steps=[
                    {"type": "query", "template_id": "qt_refund_monitor"},
                    {"type": "notify", "renderer": "generic_table"},
                ],
                metadata={
                    "feishu_notify": {
                        "enabled": True,
                        "webhooks": ["https://open.feishu.cn/open-apis/bot/v2/hook/mock"],
                    }
                },
            ),
            user,
        )

        from app.db.models import ChatSession
        session = ChatSession(
            id=new_id("session"),
            tenant_id=tenant.id,
            agent_id=agent.id,
            user_id=user.id,
            channel="scheduled_task",
            title=f"定时任务: {task.title}",
        )
        db.add(session)
        db.commit()
        db.refresh(session)

        run = ScheduledTaskRun(
            id=new_id("schedrun"),
            tenant_id=tenant.id,
            scheduled_task_id=task.id,
            agent_id=agent.id,
            user_id=user.id,
            session_id=session.id,
            status="pending",
            scheduled_for=utc_now(),
        )
        db.add(run)
        db.commit()
        db.refresh(run)

        mock_rows = [
            {"refund_id": "RF1001", "shop_name": "天猫旗舰店", "amount": 349.0},
            {"refund_id": "RF1002", "shop_name": "抖音直营店", "amount": 199.0},
        ]
        mock_query_result = QueryExecuteResult(
            template_id="qt_refund_monitor",
            columns=["refund_id", "shop_name", "amount"],
            rows=mock_rows,
            execution_time_ms=15.2,
        )

        with (
            patch("app.scheduled_tasks.pipeline.execute_query_by_id", return_value=mock_query_result),
            patch("app.scheduled_tasks.pipeline.feishu_app_notify", return_value={"ok": True, "sent_count": 1}) as mock_notify,
        ):
            finished_run = _execute_prepared_scheduled_task(db, task, run, manual=True)

        assert finished_run.status == "succeeded"
        assert mock_notify.called
        call_arg = mock_notify.call_args[0][0]
        # Verify card payload is generic table
        card_elements = call_arg.card["card"]["body"]["elements"]
        assert any(el.get("tag") == "table" for el in card_elements)

        # Verify dynamic title in assistant message (NOT hardcoded 实时销售播报)
        from sqlmodel import select
        asst_msg = db.exec(select(Message).where(Message.session_id == session.id, Message.role == "assistant")).first()
        assert asst_msg is not None
        assert "售后退款异常监控巡检流水线执行成功" in asst_msg.content
        assert "实时销售播报流水线执行成功" not in asst_msg.content


def test_push_incremental_calculation_across_runs() -> None:
    """验证按每次实际推送作为增量计算基准：第一轮推送归零记录基准，第二轮推送做差值并展示对比基准。"""
    with _test_session() as db:
        _tenant, user, _agent = _seed(db)
        req = ScheduledTaskCreateRequest(
            tenant_id="tenant_demo",
            agent_id="agent_demo",
            title="实时销售播报（半小时增量）",
            prompt="查询并播报实时销售",
            schedule_type="interval",
            schedule={"interval_seconds": 1800},
            execution_mode="pipeline",
            pipeline_steps=[
                {"type": "query", "template_id": "qt_50f463a815af4801"},
                {"type": "skill_notify", "skill": "feishu-webhook-notify"},
            ],
        )
        task = create_scheduled_task(db, req, user)

        # -----------------------------
        # 第 1 轮推送 (10:00，当日首次推送)
        # -----------------------------
        run1 = ScheduledTaskRun(
            id=new_id("run"),
            tenant_id=task.tenant_id,
            scheduled_task_id=task.id,
            agent_id=task.agent_id,
            user_id=user.id,
            scheduled_for=utc_now(),
            status="running",
            started_at=utc_now(),
            created_at=utc_now(),
            updated_at=utc_now(),
        )
        db.add(run1)
        db.commit()

        round1_rows = [
            {
                "category": "大盘",
                "item": "电商整体",
                "净销_万": 1000.0,
                "运营净销_万": 600.0,
                "达播净销_万": 400.0,
            },
            {
                "category": "店铺正增量Top5",
                "item": "抖音旗舰店",
                "净销_万": 300.0,
            },
        ]

        query_res1 = QueryExecuteResult(
            template_id="qt_50f463a815af4801",
            columns=["category", "item", "净销_万"],
            rows=round1_rows,
            row_count=len(round1_rows),
            execution_time_ms=10.0,
        )

        with (
            patch("app.scheduled_tasks.pipeline.execute_query_by_id", return_value=query_res1),
            patch("app.scheduled_tasks.pipeline.feishu_app_notify", return_value={"ok": True, "sent_count": 1}) as mock_notify1,
        ):
            res_run1 = _execute_prepared_scheduled_task(db, task, run1, manual=True)

        assert res_run1.status == "succeeded"
        assert mock_notify1.called
        card1 = mock_notify1.call_args[0][0].card
        # 首次推送：增量全部归零
        summary_text1 = str(card1)
        assert "0.00万" in summary_text1
        # task.metadata_json 中持久化了首轮快照
        db.refresh(task)
        snapshot1 = (task.metadata_json or {}).get("last_push_snapshot")
        assert snapshot1 is not None
        assert snapshot1["metrics"]["大盘:电商整体"]["net"] == 1000.0

        # -----------------------------
        # 第 2 轮推送 (10:30，非首次推送，累计净销增加)
        # -----------------------------
        run2 = ScheduledTaskRun(
            id=new_id("run"),
            tenant_id=task.tenant_id,
            scheduled_task_id=task.id,
            agent_id=task.agent_id,
            user_id=user.id,
            scheduled_for=utc_now(),
            status="running",
            started_at=utc_now(),
            created_at=utc_now(),
            updated_at=utc_now(),
        )
        db.add(run2)
        db.commit()

        # 电商整体从 1000.0 增长至 1025.0 (+25.0万，运营+15万，达播+10万)
        # 抖音旗舰店从 300.0 增长至 320.0 (+20.0万)
        round2_rows = [
            {
                "category": "大盘",
                "item": "电商整体",
                "净销_万": 1025.0,
                "运营净销_万": 615.0,
                "达播净销_万": 410.0,
            },
            {
                "category": "店铺正增量Top5",
                "item": "抖音旗舰店",
                "净销_万": 320.0,
            },
        ]

        query_res2 = QueryExecuteResult(
            template_id="qt_50f463a815af4801",
            columns=["category", "item", "净销_万"],
            rows=round2_rows,
            row_count=len(round2_rows),
            execution_time_ms=10.0,
        )

        with (
            patch("app.scheduled_tasks.pipeline.execute_query_by_id", return_value=query_res2),
            patch("app.scheduled_tasks.pipeline.feishu_app_notify", return_value={"ok": True, "sent_count": 1}) as mock_notify2,
        ):
            res_run2 = _execute_prepared_scheduled_task(db, task, run2, manual=True)

        assert res_run2.status == "succeeded"
        assert res_run2.trace_json.get("snapshot_applied") is True
        card2 = mock_notify2.call_args[0][0].card
        summary_text2 = str(card2)
        # 验证增量做差值结果：1025 - 1000 = +25.00万，运营 +15.00万，达播 +10.00万
        assert "+25.00万" in summary_text2
        assert "+15.00万" in summary_text2
        assert "+10.00万" in summary_text2
        assert "+20.00万" in summary_text2

        # 快照更新至 1025.0
        db.refresh(task)
        snapshot2 = (task.metadata_json or {}).get("last_push_snapshot")
        assert snapshot2["metrics"]["大盘:电商整体"]["net"] == 1025.0


def test_sanitize_hourly_cumulative_trend_forward_fill():
    from app.scheduled_tasks.pipeline import sanitize_hourly_cumulative_trend

    rows = [
        {"category": "大盘", "item": "电商整体", "净销_万": 125.42, "运营净销_万": 119.72},
        {"category": "24小时环比", "item": "00时", "运营净销_万": 26.69, "运营增量_万": 20.0},
        {"category": "24小时环比", "item": "01时", "运营净销_万": 42.18, "运营增量_万": 30.0},
        {"category": "24小时环比", "item": "07时", "运营净销_万": 85.80, "运营增量_万": 65.0},
        {"category": "24小时环比", "item": "08时", "运营净销_万": 120.42, "运营增量_万": 90.0},
        # 09点刚过3分钟，因部分店铺未完成同步，原始统计缩水至 77.64
        {"category": "24小时环比", "item": "09时", "运营净销_万": 77.64, "运营增量_万": 110.0},
        # 未来时段
        {"category": "24小时环比", "item": "10时", "运营净销_万": 0.0, "运营增量_万": 130.0},
        {"category": "24小时环比", "item": "11时", "运营净销_万": 0.0, "运营增量_万": 140.0},
    ]

    sanitized = sanitize_hourly_cumulative_trend(rows)

    by_item = {r["item"]: r for r in sanitized if r.get("category") == "24小时环比"}
    # 08时维持 120.42
    assert by_item["08时"]["运营净销_万"] == 120.42
    # 09时向前补齐并校准至大盘最新总净销 125.42（不再倒挂为 77.64）
    assert by_item["09时"]["运营净销_万"] == 125.42
    assert by_item["09时"]["净销_万"] == 125.42
    assert by_item["09时"]["环比增量_万"] == 15.42  # 125.42 - 110.0
    # 未来时段保持 0.0
    assert by_item["10时"]["运营净销_万"] == 0.0
    assert by_item["11时"]["运营净销_万"] == 0.0


def test_build_sales_feishu_card_with_dabo_top5() -> None:
    rows = _sample_sales_rows() + [
        {
            "统计维度": "1_平台汇总",
            "日期": "2026-10-20",
            "平台": "抖音",
            "平台店铺": "-",
            "达人": "-",
            "商品名称": "-",
            "数据源": "实时数据源",
            "品牌": "可复美",
            "成交金额": "1500000.00",
            "退款金额": "200000.00",
            "净销": "1300000.00",
        },
        {
            "统计维度": "3_达人TOP18",
            "日期": "2026-10-20~2026-10-20",
            "平台": "抖音",
            "平台店铺": "抖音-可丽金官方旗舰店",
            "达人": "小豆芽儿-20号早8点护肤场超级补贴",
            "商品名称": "-",
            "数据源": "实时数据源",
            "品牌": "可丽金",
            "成交金额": "793889.33",
            "退款金额": "135532.67",
            "净销": "658356.66",
        },
        {
            "统计维度": "3_达人TOP18",
            "日期": "2026-10-20~2026-10-20",
            "平台": "抖音",
            "平台店铺": "抖音-可复美官方旗舰店",
            "达人": "小豆芽儿-20号早8点护肤场超级补贴",
            "商品名称": "-",
            "数据源": "实时数据源",
            "品牌": "可复美",
            "成交金额": "14014.10",
            "退款金额": "3995.00",
            "净销": "10019.10",
        },
        {
            "统计维度": "3_达人TOP18",
            "日期": "2026-10-20~2026-10-20",
            "平台": "抖音",
            "平台店铺": "抖音-可复美官方旗舰店",
            "达人": "诸十亿（可复美焦点面霜专场）",
            "商品名称": "-",
            "数据源": "实时数据源",
            "品牌": "可复美",
            "成交金额": "188677.49",
            "退款金额": "21149.40",
            "净销": "167528.09",
        },
        {
            "统计维度": "3_达人TOP18",
            "日期": "2026-10-20~2026-10-20",
            "平台": "天猫",
            "平台店铺": "天猫-可复美旗舰店",
            "达人": "-",
            "商品名称": "-",
            "数据源": "实时数据源",
            "品牌": "可复美",
            "成交金额": "218885.68",
            "退款金额": "75594.30",
            "净销": "143291.38",
        },
    ]
    card = build_sales_feishu_card(rows, title="线上实时销售播报（每30分钟）")
    card_str = str(card)
    assert "达人销售TOP 5" in card_str
    assert "负增量预警 Top 5" not in card_str
    # Aggregated net sales for 小豆芽儿: (658356.66 + 10019.10) / 10000 = 66.84万
    assert "小豆芽儿-20号早8点护肤场超级补贴" in card_str
    assert "66.84万" in card_str
    assert "诸十亿（可复美焦点面霜专场）" in card_str
    assert "16.75万" in card_str

    # Ensure 1_平台汇总 and 3_达人TOP18 rows did not leak into the 7-column brand table
    elements = card["card"]["body"]["elements"]
    table_el = next(e for e in elements if e.get("tag") == "table")
    assert len(table_el["rows"]) == 2


def _make_pipeline_task(
    db: Session,
    user: User,
    steps: list[dict],
    metadata: dict | None = None,
):
    req = ScheduledTaskCreateRequest(
        tenant_id="tenant_demo",
        agent_id="agent_demo",
        title="流水线回归测试",
        prompt="流水线回归测试",
        schedule_type="interval",
        schedule={"interval_seconds": 600},
        execution_mode="pipeline",
        pipeline_steps=steps,
        metadata=metadata or {},
    )
    task = create_scheduled_task(db, req, user)
    now = utc_now()
    run = ScheduledTaskRun(
        id=new_id("run"),
        tenant_id=task.tenant_id,
        scheduled_task_id=task.id,
        agent_id=task.agent_id,
        user_id=user.id,
        scheduled_for=now,
        status="running",
        started_at=now,
        created_at=now,
        updated_at=now,
    )
    db.add(run)
    db.commit()
    db.refresh(run)
    return task, run


def test_pipeline_query_only_succeeds() -> None:
    """只有 query、没有 notify 的流水线必须成功，且快照字段为空。"""

    with _test_session() as db:
        _seed(db)
        user = db.get(User, "user_demo")
        task, run = _make_pipeline_task(
            db, user, [{"type": "query", "template_id": "qt_demo"}]
        )
        mock_result = QueryExecuteResult(
            template_id="qt_demo",
            columns=["category", "item", "净销_万"],
            rows=_sample_sales_rows(),
            row_count=len(_sample_sales_rows()),
            execution_time_ms=3.0,
        )
        with patch("app.scheduled_tasks.pipeline.execute_query_by_id", return_value=mock_result) as mock_query:
            res_run = _execute_prepared_scheduled_task(db, task, run, manual=True)

        assert res_run.status == "succeeded", res_run.error
        mock_query.assert_called_once()
        assert res_run.trace_json["prev_push_time"] is None
        assert res_run.trace_json["snapshot_applied"] is False


def test_pipeline_notify_disabled_succeeds() -> None:
    """feishu_notify.enabled=False 时跳过推送，但 run 仍必须成功。"""

    with _test_session() as db:
        _seed(db)
        user = db.get(User, "user_demo")
        task, run = _make_pipeline_task(
            db,
            user,
            [
                {"type": "query", "template_id": "qt_demo"},
                {"type": "skill_notify"},
            ],
            metadata={"feishu_notify": {"enabled": False, "webhooks": []}},
        )
        mock_result = QueryExecuteResult(
            template_id="qt_demo",
            columns=["category", "item", "净销_万"],
            rows=_sample_sales_rows(),
            row_count=len(_sample_sales_rows()),
            execution_time_ms=3.0,
        )
        with (
            patch("app.scheduled_tasks.pipeline.execute_query_by_id", return_value=mock_result),
            patch("app.scheduled_tasks.pipeline.feishu_app_notify") as mock_notify,
        ):
            res_run = _execute_prepared_scheduled_task(db, task, run, manual=True)

        assert res_run.status == "succeeded", res_run.error
        mock_notify.assert_not_called()
        assert "未启用" in res_run.result_summary
        assert res_run.trace_json["prev_push_time"] is None


def test_pipeline_unknown_step_type_fails() -> None:
    """未知步骤类型必须让 run 失败，而不是静默报「流水线执行成功」。"""

    with _test_session() as db:
        _seed(db)
        user = db.get(User, "user_demo")
        task, run = _make_pipeline_task(db, user, [{"type": "process"}])

        res_run = _execute_prepared_scheduled_task(db, task, run, manual=True)

        assert res_run.status == "failed"
        assert "未知的流水线步骤类型" in (res_run.error or "")


def test_first_push_not_consumed_by_run_without_push() -> None:
    """推送关闭的成功 run 不应消耗当日首推（否则增量归零失效）。"""

    with _test_session() as db:
        _seed(db)
        user = db.get(User, "user_demo")
        task, run1 = _make_pipeline_task(
            db,
            user,
            [{"type": "skill_notify"}],
            metadata={"feishu_notify": {"enabled": False, "webhooks": []}},
        )
        res1 = _execute_prepared_scheduled_task(db, task, run1, manual=True)
        assert res1.status == "succeeded", res1.error

        run2 = ScheduledTaskRun(
            id=new_id("run"),
            tenant_id=task.tenant_id,
            scheduled_task_id=task.id,
            agent_id=task.agent_id,
            user_id=user.id,
            scheduled_for=utc_now() + timedelta(minutes=1),
            status="running",
            started_at=utc_now(),
            created_at=utc_now(),
            updated_at=utc_now(),
        )
        db.add(run2)
        db.commit()
        db.refresh(run2)

        # run1 成功但没有推送，因此 run2 仍是当日首次推送
        assert is_task_first_push_today(task, run2, db) is True


def test_pipeline_empty_steps_succeeds() -> None:
    """空 pipeline_steps_json 的任务必须正常完成收尾，不可报 UnboundLocalError。"""

    with _test_session() as db:
        _seed(db)
        user = db.get(User, "user_demo")
        task, run = _make_pipeline_task(db, user, [])

        res_run = _execute_prepared_scheduled_task(db, task, run, manual=True)

        assert res_run.status == "succeeded", res_run.error
        assert "流水线执行成功" in (res_run.result_summary or "")
        assert res_run.trace_json["steps_count"] == 0
        assert res_run.trace_json["rows_count"] == 0
        assert res_run.trace_json["prev_push_time"] is None


def test_pipeline_artifact_url_schema_v2_button_appended() -> None:
    """当存在分享链接且卡片为 Schema 2.0 时，按钮必须以 Schema 2.0 规范挂载，绝不可产出已废弃的 tag: action。"""

    with _test_session() as db:
        _seed(db)
        user = db.get(User, "user_demo")
        task, run = _make_pipeline_task(
            db,
            user,
            [
                {"type": "query", "template_id": "qt_demo"},
                {"type": "skill_notify"},
            ],
            metadata={
                "feishu_notify": {"enabled": True, "webhooks": ["https://open.feishu.cn/open-apis/bot/v2/hook/xxx"]},
                "artifact_share_url": "http://127.0.0.1:5173/api/chat/artifacts/view/test-token",
                "artifact_title": "测试销售大屏",
            },
        )
        mock_result = QueryExecuteResult(
            template_id="qt_demo",
            columns=["category", "item", "净销_万"],
            rows=_sample_sales_rows(),
            row_count=len(_sample_sales_rows()),
            execution_time_ms=3.0,
        )
        with (
            patch("app.scheduled_tasks.pipeline.execute_query_by_id", return_value=mock_result),
            patch("app.scheduled_tasks.pipeline.feishu_app_notify", return_value={"ok": True, "sent_count": 1, "failed_count": 0}) as mock_notify,
        ):
            res_run = _execute_prepared_scheduled_task(db, task, run, manual=True)

        assert res_run.status == "succeeded", res_run.error
        mock_notify.assert_called_once()
        sent_req = mock_notify.call_args[0][0]
        card = sent_req.card
        card_obj = card.get("card") if isinstance(card.get("card"), dict) else card
        elements = card_obj["body"]["elements"]

        # 必须没有 tag: action
        action_elements = [el for el in elements if el.get("tag") == "action"]
        assert len(action_elements) == 0, f"Found forbidden tag: action in Schema 2.0 card: {action_elements}"

        # 必须包含 Schema 2.0 button
        button_elements = [el for el in elements if el.get("tag") == "button"]
        assert len(button_elements) == 1
        btn = button_elements[0]
        assert "测试销售大屏" in btn["text"]["content"]
        assert btn["behaviors"][0]["type"] == "open_url"
        assert btn["behaviors"][0]["default_url"] == "http://127.0.0.1:5173/api/chat/artifacts/view/test-token"


def test_dabo_matrix_table_structure_and_position():
    """验证达播数据表结构正确性及在飞书卡片中位于24小时时段走势上方。"""
    from app.reporting.sales_metrics import build_dabo_matrix_table_elements
    from app.scheduled_tasks.renderers.sales_card import build_sales_feishu_card

    mock_matrix_rows = [
        {"category": "达播矩阵", "item": "天猫", "label": "**天猫**", "成交金额_万": 10.0, "净销_万": 8.0, "退款金额_万": 2.0, "环比增量_万": 1.0, "is_core": True},
        {"category": "达播矩阵", "item": "天猫:可复美", "label": "**可复美**", "成交金额_万": 6.0, "净销_万": 5.0, "退款金额_万": 1.0, "环比增量_万": 0.5, "is_core": True},
        {"category": "达播矩阵", "item": "天猫:可复美:李佳琦", "label": "　　李佳琦", "成交金额_万": 4.0, "净销_万": 3.5, "退款金额_万": 0.5, "环比增量_万": 0.3, "is_core": False},
        {"category": "达播矩阵", "item": "抖音", "label": "**抖音**", "成交金额_万": 50.0, "净销_万": 40.0, "退款金额_万": 10.0, "环比增量_万": 5.0, "is_core": True},
        {"category": "达播矩阵", "item": "抖音:可复美", "label": "**可复美**", "成交金额_万": 30.0, "净销_万": 25.0, "退款金额_万": 5.0, "环比增量_万": 3.0, "is_core": True},
        {"category": "达播矩阵", "item": "抖音:可复美:达人A", "label": "　　达人A", "成交金额_万": 15.0, "净销_万": 12.0, "退款金额_万": 3.0, "环比增量_万": 1.5, "is_core": False},
        {"category": "达播矩阵", "item": "抖音:可复美:达人B", "label": "　　达人B", "成交金额_万": 10.0, "净销_万": 8.0, "退款金额_万": 2.0, "环比增量_万": 1.0, "is_core": False},
        {"category": "达播矩阵", "item": "抖音:可复美:达人C", "label": "　　达人C", "成交金额_万": 5.0, "净销_万": 5.0, "退款金额_万": 0.0, "环比增量_万": 0.5, "is_core": False},
        {"category": "达播矩阵", "item": "小红书", "label": "**小红书**", "成交金额_万": 2.0, "净销_万": 1.8, "退款金额_万": 0.2, "环比增量_万": 0.1, "is_core": True},
    ]

    elements = build_dabo_matrix_table_elements(mock_matrix_rows, is_first_push=False)
    assert len(elements) == 2
    assert "达播数据表" in elements[0]["content"]
    table = elements[1]
    col_names = [c["name"] for c in table["columns"]]
    assert col_names == ["platform", "gmv", "net_sales", "refund_rate", "diff_sales"]

    # 验证没有聚合的 TOP3 行，而是具体达人明细
    row_platforms = [r["platform"] for r in table["rows"]]
    assert not any("TOP3" in p for p in row_platforms)
    assert any("达人A" in p for p in row_platforms)
    assert any("达人B" in p for p in row_platforms)
    assert any("达人C" in p for p in row_platforms)

    # 验证在卡片中放置于 24小时时段走势上方
    card_rows = [
        {"category": "大盘", "item": "电商整体", "净销_万": 100.0, "运营净销_万": 80.0, "达播净销_万": 20.0, "环比增量_万": 10.0},
        {"category": "hourly_trend", "item": "18时", "运营净销_万": 10.0, "运营增量_万": 2.0},
        *mock_matrix_rows,
    ]
    card = build_sales_feishu_card(card_rows, title="测试卡片", is_first_push=False)
    body_elements = card["card"]["body"]["elements"]

    dabo_table_idx = -1
    hourly_chart_idx = -1
    for idx, el in enumerate(body_elements):
        if el.get("tag") == "table" and el.get("columns", [{}])[0].get("name") == "platform":
            dabo_table_idx = idx
        elif el.get("tag") == "chart":
            hourly_chart_idx = idx

    assert dabo_table_idx != -1, "Dabo matrix table not found in card elements"
    assert hourly_chart_idx != -1, "24h hourly chart not found in card elements"
    assert dabo_table_idx < hourly_chart_idx, f"Dabo table ({dabo_table_idx}) must be above hourly chart ({hourly_chart_idx})"


def test_sales_card_brand_table_bolding_and_yiwo() -> None:
    from app.scheduled_tasks.renderers.sales_card import build_sales_feishu_card

    rows = [
        # 故意乱序与混入非规范渠道（如拼多多、得物）
        {"category": "其他品牌", "item": "分销", "净销_万": 30.0, "环比增量_万": 1.5},
        {"category": "可复美", "item": "拼多多", "净销_万": 10.0, "环比增量_万": 0.5},
        {"category": "大盘", "item": "电商整体", "净销_万": 1000.0, "环比增量_万": 50.0},
        {"category": "可丽金", "item": "抖音", "净销_万": 200.0, "环比增量_万": 10.0},
        {"category": "可复美", "item": "抖音", "净销_万": 250.0, "环比增量_万": 12.0},
        {"category": "可复美", "item": "整体", "净销_万": 500.0, "环比增量_万": 25.0},
        {"category": "可复美", "item": "天猫", "净销_万": 300.0, "环比增量_万": 15.0},
        {"category": "可丽金", "item": "整体", "净销_万": 300.0, "环比增量_万": 15.0},
        {"category": "可丽金", "item": "天猫", "净销_万": 100.0, "环比增量_万": 5.0},
        {"category": "其他品牌", "item": "巨子生物", "净销_万": 100.0, "环比增量_万": 5.0},
        {"category": "其他品牌", "item": "以我", "净销_万": 20.0, "环比增量_万": 1.0},
        {"category": "其他品牌", "item": "可预", "净销_万": 50.0, "环比增量_万": 2.5},
        {"category": "可复美", "item": "得物", "净销_万": 2.0, "环比增量_万": 0.1},
    ]

    card = build_sales_feishu_card(rows, title="品牌明细加粗与新增品牌测试")
    brand_table = next(
        e for e in card["card"]["body"]["elements"]
        if e.get("tag") == "table" and e.get("columns", [{}])[0].get("name") == "brand"
    )

    row_names = [r["brand"] for r in brand_table["rows"]]
    # 严格按照规范顺序排列：电商整体 -> 可复美整体及规范渠道 -> 可丽金整体及规范渠道 -> 巨子生物 -> 可预 -> 分销 -> 以我
    expected_order = [
        "**电商整体**",
        "**可复美整体**",
        "天猫可复美",
        "抖音可复美",
        "**可丽金整体**",
        "天猫可丽金",
        "抖音可丽金",
        "**巨子生物**",
        "**可预**",
        "**分销**",
        "**以我**",
    ]
    assert row_names == expected_order
    # 验证非规范渠道（拼多多、得物）被严格过滤，不增加多余品牌与渠道行
    assert "拼多多可复美" not in row_names
    assert "得物可复美" not in row_names

    brand_map = {r["brand"]: r for r in brand_table["rows"]}
    # 验证主要品牌与新品牌均加粗
    assert "**电商整体**" in brand_map
    assert "**可复美整体**" in brand_map
    assert "**可丽金整体**" in brand_map
    assert "**巨子生物**" in brand_map
    assert "**可预**" in brand_map
    assert "**分销**" in brand_map
    assert "**以我**" in brand_map

    # 验证子渠道不加粗
    assert "天猫可复美" in brand_map
    assert "抖音可丽金" in brand_map

    # 验证数值单元格对应加粗
    assert brand_map["**巨子生物**"]["net_sales"] == "**100.00万**"
    assert brand_map["**以我**"]["net_sales"] == "**20.00万**"
    assert brand_map["天猫可复美"]["net_sales"] == "300.00万"


def test_pipeline_partial_notification_delivery() -> None:
    with _test_session() as db:
        _seed(db)
        user = db.get(User, "user_demo")

        req = ScheduledTaskCreateRequest(
            tenant_id="tenant_demo",
            agent_id="agent_demo",
            title="部分投递定时任务",
            prompt="测试提示词",
            execution_mode="pipeline",
            schedule_type="interval",
            schedule_json={"interval_seconds": 3600},
            pipeline_steps=[
                {"type": "query", "template_id": "qt_50f463a815af4801"},
                {
                    "type": "feishu_notify",
                },
            ],
            metadata_json={
                "feishu_notify": {
                    "enabled": True,
                    "webhooks": ["https://open.feishu.cn/open-apis/bot/v2/hook/wh1", "https://open.feishu.cn/open-apis/bot/v2/hook/wh2"],
                }
            },
        )
        task = create_scheduled_task(db, req, user)
        now = utc_now()
        run = ScheduledTaskRun(
            id=new_id("run"),
            tenant_id=task.tenant_id,
            scheduled_task_id=task.id,
            agent_id=task.agent_id,
            user_id=user.id,
            scheduled_for=now,
            status="running",
            started_at=now,
            created_at=now,
            updated_at=now,
        )
        db.add(run)
        db.commit()
        db.refresh(run)

        mock_result = QueryExecuteResult(
            template_id="qt_50f463a815af4801",
            columns=["category", "item", "净销_万"],
            rows=_sample_sales_rows(),
            row_count=len(_sample_sales_rows()),
            execution_time_ms=5.0,
            cached=False,
        )

        # wh1 succeeds, wh2 fails
        partial_res = {
            "ok": False,
            "partial": True,
            "sent_count": 1,
            "failed_count": 1,
            "sent": [{"target_type": "webhook", "target": "https://open.feishu.cn/open-apis/bot/v2/hook/wh1"}],
            "failed": [{"target_type": "webhook", "target": "https://open.feishu.cn/open-apis/bot/v2/hook/wh2", "error": "timeout"}],
            "error": "部分发送失败：1 个通道未送达",
        }

        with (
            patch("app.scheduled_tasks.pipeline.execute_query_by_id", return_value=mock_result),
            patch("app.scheduled_tasks.pipeline.feishu_app_notify", return_value=partial_res) as mock_notify,
        ):
            res_run = _execute_prepared_scheduled_task(db, task, run, manual=True)
            mock_notify.assert_called_once()

        # Must be marked partial, not succeeded
        assert res_run.status == "partial"
        assert "部分发送失败" in str(res_run.error)

        db.refresh(task)
        meta = task.metadata_json or {}
        # Global last_push_snapshot should NOT advance when delivery was only partial
        assert "last_push_snapshot" not in meta
        # Target snapshots should advance for wh1 only
        assert "target_snapshots" in meta
        assert "webhook:https://open.feishu.cn/open-apis/bot/v2/hook/wh1" in meta["target_snapshots"]
        assert "webhook:https://open.feishu.cn/open-apis/bot/v2/hook/wh2" not in meta["target_snapshots"]


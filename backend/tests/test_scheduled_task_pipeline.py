"""Tests for deterministic pipeline scheduled tasks execution.

Verifies:
1. `build_sales_feishu_card` correctly assembles Feishu 2.0 interactive cards.
2. `_execute_prepared_scheduled_task` correctly branches to `execute_pipeline_scheduled_task`.
3. Succeeded runs update status, result_summary, and task schedule cleanly.
4. Outbound failures accurately mark the run and task as failed.
5. Schema serialization correctly preserves `execution_mode` and `pipeline_steps`.
"""

from __future__ import annotations

from unittest.mock import patch

from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.data_query.models import QueryExecuteResult
from app.db.models import AgentProfile, ScheduledTaskRun, Tenant, User, new_id, utc_now
from app.scheduled_tasks.pipeline import build_sales_feishu_card
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
            patch("app.scheduled_tasks.pipeline.feishu_app_notify", return_value={"ok": True, "sent_count": 1, "failed_count": 0}) as mock_notify,
        ):
            res_run = _execute_prepared_scheduled_task(db, task, run, manual=True)

            assert res_run.status == "succeeded"
            assert res_run.error is None
            assert "流水线执行成功" in res_run.result_summary
            assert res_run.trace_json["execution_mode"] == "pipeline"
            assert res_run.trace_json["steps_count"] == 2
            mock_query.assert_called_once_with(db, "qt_50f463a815af4801", "tenant_demo", {"end_date": "今天"})
            mock_notify.assert_called_once()


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

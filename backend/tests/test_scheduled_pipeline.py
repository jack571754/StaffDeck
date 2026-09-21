from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from sqlmodel import Session, SQLModel, create_engine
from sqlmodel.pool import StaticPool

from app.data_query.models import QueryExecuteResult
from app.db.models import ScheduledTask, ScheduledTaskRun, utc_now
from app.scheduled_tasks.pipeline_runner import execute_pipeline
from app.scheduled_tasks.service import _execute_prepared_scheduled_task


@pytest.fixture
def db_session():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        yield session


def test_pipeline_execution_success(db_session: Session):
    now = utc_now()
    task = ScheduledTask(
        id="task_p1",
        tenant_id="tenant_p",
        agent_id="agent_sales",
        created_by_user_id="user_1",
        title="实时销售播报",
        prompt="按管道播报",
        execution_mode="pipeline",
        pipeline_steps_json=[
            {
                "type": "query",
                "template_id": "qt_summary",
                "params": {"end_date": "今天"},
            },
            {
                "type": "render",
                "title": "📊 实时销售播报",
            },
            {
                "type": "notify",
                "target": "webhook",
                "webhook_url": "https://open.feishu.cn/open-apis/bot/v2/hook/mock_token",
            },
        ],
        status="active",
        created_at=now,
        updated_at=now,
    )
    run = ScheduledTaskRun(
        id="run_p1",
        tenant_id="tenant_p",
        scheduled_task_id="task_p1",
        agent_id="agent_sales",
        user_id="user_1",
        scheduled_for=now,
        status="queued",
        created_at=now,
        updated_at=now,
    )
    db_session.add_all([task, run])
    db_session.commit()

    mock_exec_result = QueryExecuteResult(
        template_id="qt_summary",
        columns=["dept", "total_sales"],
        rows=[
            {"dept": "女装运营部", "total_sales": 80000.0},
            {"dept": "TOTAL", "total_sales": 80000.0},
        ],
        row_count=2,
        execution_time_ms=12.5,
    )

    mock_http_response = MagicMock()
    mock_http_response.status_code = 200
    mock_http_response.json.return_value = {"StatusCode": 0, "StatusMessage": "success"}

    with (
        patch("app.scheduled_tasks.pipeline_runner.execute_query_by_id", return_value=mock_exec_result) as mock_query,
        patch("requests.post", return_value=mock_http_response) as mock_post,
    ):
        execute_pipeline(db_session, task, run, manual=True)

    db_session.refresh(run)
    db_session.refresh(task)

    assert run.status == "completed"
    assert run.error is None
    assert "女装运营部" in (run.result_summary or "")
    assert len(run.trace_json.get("steps", [])) == 3
    assert mock_query.called
    assert mock_post.called


def test_pipeline_execution_step_failure(db_session: Session):
    now = utc_now()
    task = ScheduledTask(
        id="task_p2",
        tenant_id="tenant_p",
        agent_id="agent_sales",
        created_by_user_id="user_1",
        title="异常播报任务",
        prompt="按管道播报",
        execution_mode="pipeline",
        pipeline_steps_json=[
            {
                "type": "query",
                "template_id": "qt_not_exist",
                "params": {},
            },
            {
                "type": "notify",
                "target": "webhook",
                "webhook_url": "https://mock.webhook",
            },
        ],
        status="active",
        created_at=now,
        updated_at=now,
    )
    run = ScheduledTaskRun(
        id="run_p2",
        tenant_id="tenant_p",
        scheduled_task_id="task_p2",
        agent_id="agent_sales",
        user_id="user_1",
        scheduled_for=now,
        status="queued",
        created_at=now,
        updated_at=now,
    )
    db_session.add_all([task, run])
    db_session.commit()

    with (
        patch("app.scheduled_tasks.pipeline_runner.execute_query_by_id", side_effect=ValueError("Query template not found")),
        patch("requests.post") as mock_post,
    ):
        execute_pipeline(db_session, task, run, manual=True)

    db_session.refresh(run)
    assert run.status == "failed"
    assert "Query template not found" in (run.error or "")
    # Second step should not have been called
    assert not mock_post.called


def test_service_dispatches_pipeline_mode(db_session: Session):
    now = utc_now()
    task = ScheduledTask(
        id="task_p3",
        tenant_id="tenant_p",
        agent_id="agent_sales",
        created_by_user_id="user_1",
        title="管道调度分发",
        prompt="测试分发",
        execution_mode="pipeline",
        pipeline_steps_json=[
            {"type": "render", "template_str": "纯文本渲染播报"},
        ],
        status="active",
        created_at=now,
        updated_at=now,
    )
    run = ScheduledTaskRun(
        id="run_p3",
        tenant_id="tenant_p",
        scheduled_task_id="task_p3",
        agent_id="agent_sales",
        user_id="user_1",
        scheduled_for=now,
        status="queued",
        created_at=now,
        updated_at=now,
    )
    db_session.add_all([task, run])
    db_session.commit()

    with patch("app.scheduled_tasks.pipeline_runner.execute_pipeline") as mock_exec:
        _execute_prepared_scheduled_task(db_session, task, run, manual=True)
        assert mock_exec.called

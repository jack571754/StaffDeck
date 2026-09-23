"""pipeline skill_notify 步骤回归测试。

背景：pipeline_runner 的 notify 步骤只能发纯文本 webhook，丢失
feishu-webhook-notify 技能包的卡片能力。skill_notify 允许 pipeline 直接
物化 GeneralSkill 包并执行其 .py 入口脚本（默认 run.py），把查询 rows 以
--text-file JSON 形式交给脚本（凭证走 skill_secret_environment 白名单，
绝不写入步骤配置）。

本组测试用最小假 run.py（校验 payload JSON 含标记值后退出 0，否则非零）
锁四类行为：
1. query -> skill_notify 全链：脚本收到含 rows 的 JSON payload，成功完成；
2. 脚本非零退出（payload 不合格）→ 整轮 failed，错误含脚本输出；
3. 步骤缺 skill slug → 配置错误即 failed（不得静默跳过）；
4. slug 在租户内不存在 → failed。
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from sqlmodel import Session, SQLModel, create_engine
from sqlmodel.pool import StaticPool

from app.data_query.models import QueryExecuteResult
from app.db.models import GeneralSkill, ScheduledTask, ScheduledTaskRun, utc_now
from app.scheduled_tasks.pipeline_runner import execute_pipeline

FAKE_RUN_PY = """import json
import sys
from pathlib import Path

argv = sys.argv[1:]
if "--text-file" not in argv:
    print("missing --text-file", file=sys.stderr)
    sys.exit(2)
payload_path = Path(argv[argv.index("--text-file") + 1])
content = payload_path.read_text(encoding="utf-8")
try:
    data = json.loads(content)
except json.JSONDecodeError:
    print("payload is not json", file=sys.stderr)
    sys.exit(4)
rows = data.get("rows") or []
if not any("MARKER_ROW_VALUE" in json.dumps(row, ensure_ascii=False) for row in rows):
    print("marker not found in rows", file=sys.stderr)
    sys.exit(3)
print("skill_notify fake ok")
"""


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


def _create_task_and_run(
    db_session: Session,
    task_id: str,
    steps: list[dict],
) -> tuple[ScheduledTask, ScheduledTaskRun]:
    now = utc_now()
    task = ScheduledTask(
        id=task_id,
        tenant_id="tenant_p",
        agent_id="agent_sales",
        created_by_user_id="user_1",
        title="技能通知播报",
        prompt="按管道播报",
        execution_mode="pipeline",
        pipeline_steps_json=steps,
        status="active",
        created_at=now,
        updated_at=now,
    )
    run = ScheduledTaskRun(
        id=f"run_{task_id}",
        tenant_id="tenant_p",
        scheduled_task_id=task_id,
        agent_id="agent_sales",
        user_id="user_1",
        scheduled_for=now,
        status="queued",
        created_at=now,
        updated_at=now,
    )
    db_session.add_all([task, run])
    db_session.commit()
    return task, run


def _create_fake_skill(db_session: Session) -> None:
    db_session.add(
        GeneralSkill(
            id="genskill_fake",
            tenant_id="tenant_p",
            slug="fake-notify",
            name="Fake Notify",
            skill_markdown="# fake",
            skill_files_json=[
                {
                    "path": "run.py",
                    "content": FAKE_RUN_PY,
                    "size": len(FAKE_RUN_PY.encode("utf-8")),
                    "mime_type": "text/x-python",
                }
            ],
            status="published",
        )
    )
    db_session.commit()


def _mock_query_result(with_marker: bool) -> QueryExecuteResult:
    rows = [
        {
            "category": "MARKER_ROW_VALUE" if with_marker else "其他品类",
            "item": "商品A",
            "net_sales": 1000,
        },
        {"category": "大盘", "item": "商品B", "net_sales": 2000},
    ]
    return QueryExecuteResult(
        template_id="qt_summary",
        columns=["category", "item", "net_sales"],
        rows=rows,
        row_count=len(rows),
        execution_time_ms=1.0,
    )


def test_skill_notify_delivers_rows_payload_to_skill_script(db_session: Session):
    _create_fake_skill(db_session)
    task, run = _create_task_and_run(
        db_session,
        "task_sn1",
        steps=[
            {"type": "query", "template_id": "qt_summary", "params": {}},
            {"type": "skill_notify", "skill": "fake-notify"},
        ],
    )

    with patch(
        "app.scheduled_tasks.pipeline_runner.execute_query_by_id",
        return_value=_mock_query_result(with_marker=True),
    ):
        execute_pipeline(db_session, task, run, manual=True)

    db_session.refresh(run)
    assert run.status == "completed", run.error
    steps = (run.trace_json or {}).get("steps", [])
    notify_trace = steps[-1]
    assert notify_trace["type"] == "skill_notify"
    assert notify_trace["status"] == "success"
    assert notify_trace["exit_code"] == 0


def test_skill_notify_fails_run_when_script_rejects_payload(db_session: Session):
    _create_fake_skill(db_session)
    task, run = _create_task_and_run(
        db_session,
        "task_sn2",
        steps=[
            {"type": "query", "template_id": "qt_summary", "params": {}},
            {"type": "skill_notify", "skill": "fake-notify"},
        ],
    )

    with patch(
        "app.scheduled_tasks.pipeline_runner.execute_query_by_id",
        return_value=_mock_query_result(with_marker=False),
    ):
        execute_pipeline(db_session, task, run, manual=True)

    db_session.refresh(run)
    assert run.status == "failed"
    assert "skill_notify" in (run.error or "")
    assert "marker not found in rows" in (run.error or "")


def test_skill_notify_missing_slug_fails(db_session: Session):
    task, run = _create_task_and_run(
        db_session,
        "task_sn3",
        steps=[{"type": "skill_notify"}],
    )

    execute_pipeline(db_session, task, run, manual=True)

    db_session.refresh(run)
    assert run.status == "failed"
    assert "missing skill slug" in (run.error or "")


def test_skill_notify_unknown_slug_fails(db_session: Session):
    task, run = _create_task_and_run(
        db_session,
        "task_sn4",
        steps=[{"type": "skill_notify", "skill": "no-such-skill"}],
    )

    execute_pipeline(db_session, task, run, manual=True)

    db_session.refresh(run)
    assert run.status == "failed"
    assert "no-such-skill" in (run.error or "")
    assert "not found" in (run.error or "")


def test_pipeline_failure_sends_alert_to_notify_webhook(db_session: Session):
    """P0-3 失败可见性：pipeline 任一步骤失败时，复用 notify 步骤的 webhook
    发一条 text 告警（含任务标题与错误摘要），群里不再静默。"""
    from unittest.mock import MagicMock

    task, run = _create_task_and_run(
        db_session,
        "task_alert1",
        steps=[
            {"type": "query", "template_id": "qt_not_exist", "params": {}},
            {
                "type": "notify",
                "webhook_url": "https://mock.webhook/alert",
            },
        ],
    )

    mock_response = MagicMock()
    mock_response.status_code = 200
    with (
        patch(
            "app.scheduled_tasks.pipeline_runner.execute_query_by_id",
            side_effect=ValueError("Query template not found"),
        ),
        patch("requests.post", return_value=mock_response) as mock_post,
    ):
        execute_pipeline(db_session, task, run, manual=True)

    db_session.refresh(run)
    assert run.status == "failed"
    assert mock_post.called, "失败时应向 notify webhook 发送告警"
    payload = mock_post.call_args.kwargs["json"]
    assert payload["msg_type"] == "text"
    alert_text = payload["content"]["text"]
    assert "技能通知播报" in alert_text, "告警应包含任务标题"
    assert "Query template not found" in alert_text, "告警应包含错误摘要"


def test_pipeline_failure_alert_failure_is_silent(db_session: Session):
    """告警发送自身失败不得吞掉原始失败状态。"""
    from unittest.mock import MagicMock

    task, run = _create_task_and_run(
        db_session,
        "task_alert2",
        steps=[
            {"type": "query", "template_id": "qt_not_exist", "params": {}},
            {"type": "notify", "webhook_url": "https://mock.webhook/alert"},
        ],
    )

    mock_response = MagicMock()
    mock_response.status_code = 200
    with (
        patch(
            "app.scheduled_tasks.pipeline_runner.execute_query_by_id",
            side_effect=ValueError("Query template not found"),
        ),
        patch("requests.post", side_effect=RuntimeError("network down")),
    ):
        execute_pipeline(db_session, task, run, manual=True)

    db_session.refresh(run)
    assert run.status == "failed"
    assert "Query template not found" in (run.error or "")

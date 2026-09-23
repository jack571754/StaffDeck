from __future__ import annotations

from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.db.models import AgentProfile, HarnessInvocationRecord, Tenant
from app.scheduled_tasks import service as scheduled_service


def _test_session() -> Session:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    return Session(engine)


def test_schedule_draft_prompt_contains_fixed_process_guidelines() -> None:
    prompt = scheduled_service.SCHEDULE_DRAFT_PROMPT
    assert "固定流程类任务" in prompt
    assert "run_skill_script" in prompt
    assert "operation='read'" in prompt
    assert "禁止自行发挥或调用 exec_command" in prompt


def test_fixed_process_task_draft_detection(monkeypatch) -> None:
    with _test_session() as db:
        db.add(Tenant(id="tenant_demo", name="Demo"))
        db.add(AgentProfile(id="agent_demo", tenant_id="tenant_demo", name="价格合规员", is_overall=False))
        db.commit()

        deterministic_prompt = (
            "你是价格合规巡检员工。请严格按以下步骤确定性执行，禁止自行发挥或调用 exec_command：\n"
            "1. 调用对应业务能力的技能包（operation='read'），获取执行脚本 run.py 的文件路径；\n"
            "2. 调用工具 run_skill_script 执行脚本（设置 timeout_seconds=60）；\n"
            "3. 读取 run_skill_script 返回的 JSON 结构并如实简报（包含巡检总数、发现数、推送状态），汇报后立即结束任务。"
        )

        monkeypatch.setattr(
            scheduled_service,
            "_detect_with_llm",
            lambda *args, **kwargs: scheduled_service._LLMScheduledTaskDraft(
                should_create=True,
                title="全渠道价格巡检与飞书推送",
                prompt=deterministic_prompt,
                schedule_type="interval",
                schedule={"interval_minutes": 10},
                confidence=0.95,
                reason="用户明确要求每10分钟固定流程巡检并推送到飞书",
            ),
        )

        draft = scheduled_service.detect_scheduled_task_draft(
            db,
            "tenant_demo",
            "agent_demo",
            "user_demo",
            "每10分钟巡检全渠道商品价格，并将异常情况推送到飞书群",
            "session_demo",
        )

        assert draft is not None
        assert draft.should_create is True
        assert draft.schedule_type == "interval"
        assert draft.schedule["interval_minutes"] == 10
        assert "run_skill_script" in draft.prompt
        assert "禁止自行发挥或调用 exec_command" in draft.prompt


def test_scheduled_business_failures_handles_structured_push_status() -> None:
    # 1. Successful push
    success_inv = HarnessInvocationRecord(
        id="inv_success",
        tenant_id="tenant_demo",
        session_id="session_demo",
        run_id="run_demo",
        tool_name="run_skill_script",
        arguments_json={"script_path": "run.py"},
        result_json={
            "ok": True,
            "data": {
                "ok": True,
                "status": "completed",
                "structured_result": {
                    "status": "success",
                    "push_status": "success",
                    "total_checked": 50,
                    "new_items": 2,
                },
            },
        },
    )
    failures = scheduled_service._scheduled_business_failures([success_inv])
    assert len(failures) == 0

    # 2. Skipped push (no anomalies / all deduplicated)
    skipped_inv = HarnessInvocationRecord(
        id="inv_skipped",
        tenant_id="tenant_demo",
        session_id="session_demo",
        run_id="run_demo",
        tool_name="run_skill_script",
        arguments_json={"script_path": "run.py"},
        result_json={
            "ok": True,
            "data": {
                "ok": True,
                "status": "completed",
                "structured_result": {
                    "status": "success",
                    "push_status": "skipped",
                    "total_checked": 50,
                    "new_items": 0,
                },
            },
        },
    )
    failures_skip = scheduled_service._scheduled_business_failures([skipped_inv])
    assert len(failures_skip) == 0

    # 3. Failed push (Feishu Webhook failed)
    failed_inv = HarnessInvocationRecord(
        id="inv_failed",
        tenant_id="tenant_demo",
        session_id="session_demo",
        run_id="run_demo",
        tool_name="run_skill_script",
        arguments_json={"script_path": "run.py"},
        result_json={
            "ok": False,
            "data": {
                "ok": False,
                "status": "failed",
                "structured_result": {
                    "status": "failed",
                    "push_status": "failed",
                    "message": "飞书返回 HTTP 400: invalid webhook",
                },
            },
        },
    )
    failures_err = scheduled_service._scheduled_business_failures([failed_inv])
    assert len(failures_err) == 1
    assert failures_err[0]["tool"] == "run_skill_script"
    assert "飞书返回 HTTP 400" in failures_err[0]["message"]

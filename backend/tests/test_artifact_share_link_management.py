from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi import HTTPException
from fastapi.responses import HTMLResponse, StreamingResponse
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine
from starlette.requests import Request as StarletteRequest

from app.api.chat import (
    ExtendShareLinkRequest,
    ShareLinkRequest,
    UpdateShareLinkRequest,
    delete_artifact_share_link,
    extend_artifact_share_link,
    list_artifact_share_links,
    list_recent_reports_for_sharing,
    mint_artifact_share,
    revoke_artifact_share_link,
    update_artifact_share_link,
    view_published_artifact,
)
from app.core.harness_session_cleanup import harness_task_workspace_path
from app.data_query.models import QueryExecuteResult
from app.db.models import (
    ArtifactShareLink,
    ChatSession,
    HarnessTaskFrameRecord,
    Message,
    ScheduledTask,
    Tenant,
    User,
    new_id,
)
from app.harness import publish_harness_artifacts

HTML_BODY = (
    "<!doctype html><html><head><meta charset=\"utf-8\">"
    "<title>Sales Report</title></head><body><h1>实时销售分析</h1></body></html>"
)


def _test_engine():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    return engine


def _seed_artifact(
    db: Session,
    *,
    tenant_id: str = "tenant_demo",
    session_id: str = "session_demo",
    task_frame_id: str = "task_demo",
    artifact_path: str = "reports/sales.html",
    display_name: str = "销售数据大屏.html",
    body: str = HTML_BODY,
) -> User:
    db.add(Tenant(id=tenant_id, name="Demo"))
    user = User(
        id="user_owner",
        tenant_id=tenant_id,
        username="owner",
        display_name="张三",
        password_hash="test",
    )
    db.add(user)
    db.add(
        ChatSession(
            id=session_id,
            tenant_id=tenant_id,
            user_id=user.id,
        )
    )
    db.add(
        HarnessTaskFrameRecord(
            id="htask_demo",
            tenant_id=tenant_id,
            session_id=session_id,
            source_turn_id="turn_demo",
            task_id=task_frame_id,
        )
    )
    db.add(
        Message(
            id="msg_assistant",
            tenant_id=tenant_id,
            session_id=session_id,
            role="assistant",
            content="报告已生成。",
            metadata_json={"harness_artifacts": []},
        )
    )
    db.commit()

    workspace = harness_task_workspace_path(
        tenant_id=tenant_id,
        session_id=session_id,
        task_frame_id=task_frame_id,
    )
    file_path = workspace / artifact_path
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_text(body, encoding="utf-8")
    published = publish_harness_artifacts(
        workspace,
        task_frame_id,
        [{"path": artifact_path, "display_name": display_name}],
        operation="report_generation",
    )
    assistant = db.get(Message, "msg_assistant")
    assert assistant is not None
    assistant.metadata_json = {"harness_artifacts": published}
    db.add(assistant)
    db.commit()
    return user


def test_mint_share_link_creates_db_record(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ULTRARAG_DATA_DIR", str(tmp_path / "data"))
    engine = _test_engine()
    with Session(engine) as db:
        user = _seed_artifact(db)
        resp = mint_artifact_share(
            ShareLinkRequest(
                tenant_id="tenant_demo",
                session_id="session_demo",
                task_frame_id="task_demo",
                path="reports/sales.html",
                title="Q3 销售报告",
                days=7,
            ),
            current_user=user,
            db=db,
        )

        assert resp.link_id is not None
        assert resp.title == "Q3 销售报告"
        assert "/api/chat/artifacts/view/" in resp.url

        record = db.get(ArtifactShareLink, resp.link_id)
        assert record is not None
        assert record.title == "Q3 销售报告"
        assert record.ttl_seconds == 7 * 86400
        assert record.is_revoked is False
        assert record.view_count == 0
        assert record.created_by_username == "张三"


def test_list_share_links_and_filtering(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ULTRARAG_DATA_DIR", str(tmp_path / "data"))
    engine = _test_engine()
    with Session(engine) as db:
        user = _seed_artifact(db)
        # Mint active link
        mint_artifact_share(
            ShareLinkRequest(
                tenant_id="tenant_demo",
                session_id="session_demo",
                task_frame_id="task_demo",
                path="reports/sales.html",
                title="报告A",
                days=7,
            ),
            current_user=user,
            db=db,
        )
        # Mint another link with 1 day
        resp2 = mint_artifact_share(
            ShareLinkRequest(
                tenant_id="tenant_demo",
                session_id="session_demo",
                task_frame_id="task_demo",
                path="reports/sales.html",
                title="报告B",
                days=1,
            ),
            current_user=user,
            db=db,
        )

        # Revoke second link
        revoke_artifact_share_link(resp2.link_id, current_user=user, db=db)

        # List all
        all_res = list_artifact_share_links(
            tenant_id="tenant_demo",
            status="all",
            current_user=user,
            db=db,
        )
        assert all_res.total == 2
        assert all_res.active_count == 1
        assert all_res.revoked_count == 1

        # Filter active
        active_res = list_artifact_share_links(
            tenant_id="tenant_demo",
            status="active",
            current_user=user,
            db=db,
        )
        assert active_res.total == 1
        assert active_res.items[0].title == "报告A"

        # Search keyword
        search_res = list_artifact_share_links(
            tenant_id="tenant_demo",
            search="报告B",
            current_user=user,
            db=db,
        )
        assert search_res.total == 1
        assert search_res.items[0].title == "报告B"


def test_revoked_link_denies_access_and_renders_notice(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ULTRARAG_DATA_DIR", str(tmp_path / "data"))
    engine = _test_engine()
    with Session(engine) as db:
        user = _seed_artifact(db)
        resp = mint_artifact_share(
            ShareLinkRequest(
                tenant_id="tenant_demo",
                session_id="session_demo",
                task_frame_id="task_demo",
                path="reports/sales.html",
                days=7,
            ),
            current_user=user,
            db=db,
        )

        # Valid before revoking
        res = view_published_artifact(resp.token, current_user=None, db=db)
        assert isinstance(res, StreamingResponse)

        # Check view count incremented
        rec = db.get(ArtifactShareLink, resp.link_id)
        assert rec is not None
        assert rec.view_count == 1
        assert rec.last_viewed_at is not None

        # Revoke it
        revoke_artifact_share_link(resp.link_id, current_user=user, db=db)

        # Anonymous API call (no text/html in accept) raises 403
        with pytest.raises(HTTPException) as exc_info:
            view_published_artifact(resp.token, current_user=None, db=db)
        assert exc_info.value.status_code == 403

        # Browser navigation (text/html) returns friendly notice page
        req = StarletteRequest({
            "type": "http",
            "method": "GET",
            "headers": [(b"accept", b"text/html,application/xhtml+xml")],
        })
        notice_resp = view_published_artifact(resp.token, request=req, current_user=None, db=db)
        assert isinstance(notice_resp, HTMLResponse)
        assert notice_resp.status_code == 403
        assert "已被管理员手动撤销或作废" in notice_resp.body.decode("utf-8")


def test_extend_link_validity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ULTRARAG_DATA_DIR", str(tmp_path / "data"))
    engine = _test_engine()
    with Session(engine) as db:
        user = _seed_artifact(db)
        resp = mint_artifact_share(
            ShareLinkRequest(
                tenant_id="tenant_demo",
                session_id="session_demo",
                task_frame_id="task_demo",
                path="reports/sales.html",
                days=1,
            ),
            current_user=user,
            db=db,
        )

        initial_record = db.get(ArtifactShareLink, resp.link_id)
        assert initial_record is not None
        old_expires_at = initial_record.expires_at

        # Extend by 7 days
        extended = extend_artifact_share_link(
            resp.link_id,
            ExtendShareLinkRequest(days=7),
            current_user=user,
            db=db,
        )

        assert extended.expires_at > old_expires_at
        # Verify the new token can open the artifact
        opened = view_published_artifact(extended.token, current_user=None, db=db)
        assert isinstance(opened, StreamingResponse)
        # 旧 token 必须随续期立即失效，而不是留成无法吊销的孤儿
        with pytest.raises(HTTPException) as old_exc:
            view_published_artifact(resp.token, current_user=None, db=db)
        assert old_exc.value.status_code == 403


def test_delete_link_and_recent_reports(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ULTRARAG_DATA_DIR", str(tmp_path / "data"))
    engine = _test_engine()
    with Session(engine) as db:
        user = _seed_artifact(db)
        resp = mint_artifact_share(
            ShareLinkRequest(
                tenant_id="tenant_demo",
                session_id="session_demo",
                task_frame_id="task_demo",
                path="reports/sales.html",
            ),
            current_user=user,
            db=db,
        )

        # Recent reports discovery
        reports = list_recent_reports_for_sharing(
            tenant_id="tenant_demo",
            current_user=user,
            db=db,
        )
        assert len(reports) == 1
        assert reports[0]["path"] == "reports/sales.html"

        # Delete = 软删除：保留审计行并立即吊销 token，而不是留下仍可访问的孤儿签名
        del_res = delete_artifact_share_link(resp.link_id, current_user=user, db=db)
        assert del_res["success"] is True
        db.expire_all()
        deleted = db.get(ArtifactShareLink, resp.link_id)
        assert deleted is not None
        assert deleted.is_revoked is True
        with pytest.raises(HTTPException) as exc_info:
            view_published_artifact(resp.token, current_user=None, db=db)
        assert exc_info.value.status_code == 403


def test_report_generate_automatically_creates_share_link(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.core.capability_manifest import CapabilityManifestBuilder
    from app.core.harness_capability_invoker import HarnessCapabilityInvoker

    monkeypatch.setenv("ULTRARAG_DATA_DIR", str(tmp_path / "data"))
    engine = _test_engine()
    with Session(engine) as db:
        user = _seed_artifact(db)
        manifest = CapabilityManifestBuilder(db).build("tenant_demo", None, None, None)
        session = db.get(ChatSession, "session_demo")
        assert session is not None
        invoker = HarnessCapabilityInvoker(
            db=db,
            tenant_id="tenant_demo",
            session=session,
            task_frame_id="task_demo",
            model_config=None,
            manifest=manifest,
            active_skill=None,
            active_step_id=None,
            agent_id=None,
        )
        result = invoker.invoke(
            "report_generate",
            {
                "title": "自动分析报表",
                "rows": [{"指标": "销售额", "数值": 100}],
            },
        )
        assert result["success"] is True
        assert "share_link_id" in result["data"]
        # 签名 token 绝不能进模型上下文（否则会被复述进回复/消息/trace）
        assert "share_url" not in result["data"]
        assert "share_token" not in result["data"]

        # Confirm the auto-generated share link is stored in DB and queryable
        links_res = list_artifact_share_links(
            tenant_id="tenant_demo",
            current_user=user,
            db=db,
        )
        found = [item for item in links_res.items if item.id == result["data"]["share_link_id"]]
        assert len(found) == 1
        assert found[0].url.startswith("http")
        assert found[0].token == ""  # 列表不回传明文 token
        assert found[0].status == "active"
        assert found[0].view_count == 0


def test_semantic_generate_share_link(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.api.chat import SemanticGenerateShareLinkRequest, semantic_generate_share_link

    monkeypatch.setenv("ULTRARAG_DATA_DIR", str(tmp_path / "data"))
    engine = _test_engine()
    with Session(engine) as db:
        user = _seed_artifact(db)
        req = SemanticGenerateShareLinkRequest(
            prompt="帮我生成一份近7天各大区门店销售分析大屏与达播业绩",
            title="近7天大区销售业绩看板",
            renderer="sales_report",
            days=14,
        )
        res = semantic_generate_share_link(req, current_user=user, db=db)
        assert res.id.startswith("ashare_")
        # 未提供 data_template_id 时必须是显式演示数据，避免被当成真实业务报表
        assert res.title == "【演示数据】近7天大区销售业绩看板"
        assert res.status == "active"
        assert res.ttl_seconds == 14 * 86400
        assert res.view_count == 0
        assert res.url.startswith("http")

        # Test viewing the newly generated HTML artifact anonymously
        opened = view_published_artifact(res.token, current_user=None, db=db)
        assert isinstance(opened, StreamingResponse)

        # Check listed in management
        links_res = list_artifact_share_links(
            tenant_id="tenant_demo",
            current_user=user,
            db=db,
        )
        assert any(item.id == res.id for item in links_res.items)


def test_bind_scheduled_task_push(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.api.chat import (
        BindScheduledTaskRequest,
        ScheduledPushConfig,
        SemanticGenerateShareLinkRequest,
        bind_artifact_share_link_to_scheduled_task,
        semantic_generate_share_link,
    )
    from app.db.models import ScheduledTask
    from app.scheduled_tasks.service import automatic_task_message

    monkeypatch.setenv("ULTRARAG_DATA_DIR", str(tmp_path / "data"))
    engine = _test_engine()
    with Session(engine) as db:
        user = _seed_artifact(db)
        mock_result = QueryExecuteResult(
            template_id="qt_real",
            columns=["区域", "净销"],
            rows=[{"区域": "华东", "净销": 123.4}],
            row_count=1,
            execution_time_ms=1.0,
        )

        # 1. 真实数据报表可直接配置定时推送
        gen_req = SemanticGenerateShareLinkRequest(
            prompt="分析各区周报",
            title="区域周度销售汇报",
            renderer="sales_report",
            days=7,
            data_template_id="qt_real",
            schedule_push=ScheduledPushConfig(
                enabled=True,
                schedule_type="daily",
                time="10:30",
                feishu_webhook="https://open.feishu.cn/open-apis/bot/v2/hook/test12345",
            ),
        )
        with patch("app.data_query.service.execute_query_by_id", return_value=mock_result):
            gen_res = semantic_generate_share_link(gen_req, current_user=user, db=db)
        assert gen_res.bound_task_id is not None
        assert "周度销售汇报" in (gen_res.bound_task_title or "")
        assert not gen_res.title.startswith("【演示数据】")

        # Verify task is created in DB and metadata has link
        task = db.get(ScheduledTask, gen_res.bound_task_id)
        assert task is not None
        assert task.metadata_json.get("artifact_share_link_id") == gen_res.id
        assert "artifact_share_url" not in task.metadata_json
        assert task.metadata_json.get("artifact_title") == "区域周度销售汇报"

        # Check prompt enhancement for agent execution：只给稳定引用，不给 token
        msg = automatic_task_message(task)
        assert "【关联推送 HTML 报告】" in msg
        assert gen_res.url not in msg
        assert "区域周度销售汇报" in msg

        # 2. Bind another real-data link to an existing task
        req2 = SemanticGenerateShareLinkRequest(
            prompt="库存明细",
            title="全国仓储库存实时大屏",
            renderer="generic_table",
            days=30,
            data_template_id="qt_real",
        )
        with patch("app.data_query.service.execute_query_by_id", return_value=mock_result):
            res2 = semantic_generate_share_link(req2, current_user=user, db=db)
        assert res2.bound_task_id is None

        bind_res = bind_artifact_share_link_to_scheduled_task(
            link_id=res2.id,
            req=BindScheduledTaskRequest(
                mode="existing",
                existing_task_id=task.id,
            ),
            current_user=user,
            db=db,
        )
        assert bind_res.task_id == task.id
        db.refresh(task)
        assert task.metadata_json.get("artifact_share_link_id") == res2.id
        assert task.metadata_json.get("artifact_title") == "全国仓储库存实时大屏"


def test_semantic_generate_blocks_demo_push(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.api.chat import (
        ScheduledPushConfig,
        SemanticGenerateShareLinkRequest,
        semantic_generate_share_link,
    )

    monkeypatch.setenv("ULTRARAG_DATA_DIR", str(tmp_path / "data"))
    engine = _test_engine()
    with Session(engine) as db:
        user = _seed_artifact(db)
        req = SemanticGenerateShareLinkRequest(
            prompt="演示报表",
            schedule_push=ScheduledPushConfig(enabled=True, time="09:00"),
            days=7,
        )
        with pytest.raises(HTTPException) as exc_info:
            semantic_generate_share_link(req, current_user=user, db=db)
        assert exc_info.value.status_code == 400
        assert "演示" in exc_info.value.detail


def test_semantic_generate_with_data_template_uses_real_rows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.api.chat import SemanticGenerateShareLinkRequest, semantic_generate_share_link

    monkeypatch.setenv("ULTRARAG_DATA_DIR", str(tmp_path / "data"))
    engine = _test_engine()
    with Session(engine) as db:
        user = _seed_artifact(db)
        mock_result = QueryExecuteResult(
            template_id="qt_real",
            columns=["区域", "净销"],
            rows=[{"区域": "华东", "净销": 123.4}],
            row_count=1,
            execution_time_ms=1.0,
        )
        req = SemanticGenerateShareLinkRequest(
            prompt="按区域统计净销",
            title="区域净销明细",
            renderer="generic_table",
            data_template_id="qt_real",
        )
        with patch("app.data_query.service.execute_query_by_id", return_value=mock_result) as mock_query:
            res = semantic_generate_share_link(req, current_user=user, db=db)

        mock_query.assert_called_once()
        assert res.title == "区域净销明细"
        assert not res.title.startswith("【演示数据】")


def test_bind_rejects_task_owned_by_another_member(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.api.chat import (
        BindScheduledTaskRequest,
        bind_artifact_share_link_to_scheduled_task,
    )
    from app.db.models import ScheduledTask, new_id

    monkeypatch.setenv("ULTRARAG_DATA_DIR", str(tmp_path / "data"))
    engine = _test_engine()
    with Session(engine) as db:
        admin = _seed_artifact(db)
        member = User(
            id="user_member",
            tenant_id="tenant_demo",
            username="member",
            display_name="普通成员",
            role="member",
            password_hash="test-hash",
        )
        db.add(member)
        db.commit()

        # 管理员创建的定时任务，普通成员不能通过 bind 接口改写其推送配置
        task = ScheduledTask(
            id=new_id("task_sched"),
            tenant_id="tenant_demo",
            agent_id="agent_demo",
            created_by_user_id=admin.id,
            title="管理员的定时任务",
            prompt="测试",
            status="active",
            execution_mode="pipeline",
            pipeline_steps_json=[{"type": "query", "template_id": "qt_demo"}],
            schedule_type="interval",
            schedule_json={"interval_seconds": 600},
        )
        db.add(task)
        db.commit()
        db.refresh(task)
        link = mint_artifact_share(
            ShareLinkRequest(
                tenant_id="tenant_demo",
                session_id="session_demo",
                task_frame_id="task_demo",
                path="reports/sales.html",
            ),
            current_user=admin,
            db=db,
        )

        with pytest.raises(HTTPException) as exc_info:
            bind_artifact_share_link_to_scheduled_task(
                link_id=link.link_id,
                req=BindScheduledTaskRequest(mode="existing", existing_task_id=task.id),
                current_user=member,
                db=db,
            )
        assert exc_info.value.status_code == 403







def test_bind_rejects_link_owned_by_another_member(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.api.chat import (
        BindScheduledTaskRequest,
        bind_artifact_share_link_to_scheduled_task,
    )
    from app.db.models import ScheduledTask, new_id

    monkeypatch.setenv("ULTRARAG_DATA_DIR", str(tmp_path / "data"))
    engine = _test_engine()
    with Session(engine) as db:
        user_a = _seed_artifact(db)
        user_b = User(
            id="user_b",
            tenant_id="tenant_demo",
            username="user_b",
            display_name="成员B",
            role="member",
            password_hash="test-hash",
        )
        db.add(user_b)
        db.commit()

        # user_b 拥有自己的任务
        task = ScheduledTask(
            id=new_id("task_sched"),
            tenant_id="tenant_demo",
            agent_id="agent_demo",
            created_by_user_id=user_b.id,
            title="成员B的定时任务",
            prompt="测试",
            status="active",
            execution_mode="pipeline",
            pipeline_steps_json=[{"type": "query", "template_id": "qt_demo"}],
            schedule_type="interval",
            schedule_json={"interval_seconds": 600},
        )
        db.add(task)
        db.commit()

        # user_a 创建了分享链接
        link = mint_artifact_share(
            ShareLinkRequest(
                tenant_id="tenant_demo",
                session_id="session_demo",
                task_frame_id="task_demo",
                path="reports/sales.html",
            ),
            current_user=user_a,
            db=db,
        )

        # user_b 尝试把 user_a 的链接绑定到 user_b 自己的任务上
        with pytest.raises(HTTPException) as exc_info:
            bind_artifact_share_link_to_scheduled_task(
                link_id=link.link_id,
                req=BindScheduledTaskRequest(mode="existing", existing_task_id=task.id),
                current_user=user_b,
                db=db,
            )
        assert exc_info.value.status_code == 403
        assert "无权管理该分享链接" in exc_info.value.detail

def test_update_share_link_title_syncs_bound_task_metadata():
    engine = _test_engine()
    with Session(engine) as db:
        admin = _seed_artifact(db)
        admin.role = "admin"
        db.add(admin)
        db.commit()

        link = mint_artifact_share(
            ShareLinkRequest(
                tenant_id="tenant_demo",
                session_id="session_demo",
                task_frame_id="task_demo",
                path="reports/sales.html",
                title="原始报告",
            ),
            current_user=admin,
            db=db,
        )

        task = ScheduledTask(
            id=new_id("task_sched"),
            tenant_id="tenant_demo",
            agent_id="agent_demo",
            created_by_user_id=admin.id,
            title="绑定的定时任务",
            prompt="测试",
            status="active",
            execution_mode="pipeline",
            pipeline_steps_json=[],
            schedule_type="interval",
            schedule_json={"interval_seconds": 600},
            metadata_json={
                "artifact_share_link_id": link.link_id,
                "artifact_title": "旧标题",
            },
        )
        db.add(task)
        db.commit()

        res = update_artifact_share_link(
            link_id=link.link_id,
            req=UpdateShareLinkRequest(title="华东大区销售日报"),
            current_user=admin,
            db=db,
        )

        assert res.title == "华东大区销售日报"
        db.refresh(task)
        assert task.metadata_json["artifact_title"] == "华东大区销售日报"
        assert task.metadata_json["artifact_share_url"] == link.url

        # 清空标题时移除 artifact_title 键
        update_artifact_share_link(
            link_id=link.link_id,
            req=UpdateShareLinkRequest(title=""),
            current_user=admin,
            db=db,
        )
        db.refresh(task)
        assert "artifact_title" not in task.metadata_json


def test_update_share_link_shortens_expiry_and_invalidates_old_token():
    from datetime import UTC, datetime, timedelta

    engine = _test_engine()
    with Session(engine) as db:
        admin = _seed_artifact(db)
        link = mint_artifact_share(
            ShareLinkRequest(
                tenant_id="tenant_demo",
                session_id="session_demo",
                task_frame_id="task_demo",
                path="reports/sales.html",
                title="长期报告",
                days=30,
            ),
            current_user=admin,
            db=db,
        )
        old_token = link.token

        now = datetime.now(UTC)
        target_exp = now + timedelta(days=2)
        res = update_artifact_share_link(
            link_id=link.link_id,
            req=UpdateShareLinkRequest(expires_at=target_exp),
            current_user=admin,
            db=db,
        )

        assert res.token != old_token
        # 旧 token 打开抛 403
        req_mock = StarletteRequest(
            {"type": "http", "method": "GET", "path": f"/artifacts/view/{old_token}", "headers": []}
        )
        with pytest.raises(HTTPException) as exc_info:
            view_published_artifact(token=old_token, request=req_mock, current_user=None, db=db)
        assert exc_info.value.status_code == 403

        # 新 token 正常打开
        new_token = res.token
        new_req_mock = StarletteRequest(
            {"type": "http", "method": "GET", "path": f"/artifacts/view/{new_token}", "headers": []}
        )
        resp = view_published_artifact(token=new_token, request=new_req_mock, current_user=None, db=db)
        assert resp.status_code == 200


def test_update_share_link_extends_expiry():
    from datetime import UTC, datetime, timedelta

    engine = _test_engine()
    with Session(engine) as db:
        admin = _seed_artifact(db)
        link = mint_artifact_share(
            ShareLinkRequest(
                tenant_id="tenant_demo",
                session_id="session_demo",
                task_frame_id="task_demo",
                path="reports/sales.html",
                days=1,
            ),
            current_user=admin,
            db=db,
        )
        old_token = link.token

        now = datetime.now(UTC)
        target_exp = now + timedelta(days=15)
        res = update_artifact_share_link(
            link_id=link.link_id,
            req=UpdateShareLinkRequest(expires_at=target_exp),
            current_user=admin,
            db=db,
        )
        assert res.token != old_token
        assert res.status == "active"


def test_update_share_link_rejects_past_expiry_and_revoked_expiry():
    from datetime import UTC, datetime, timedelta

    engine = _test_engine()
    with Session(engine) as db:
        admin = _seed_artifact(db)
        link = mint_artifact_share(
            ShareLinkRequest(
                tenant_id="tenant_demo",
                session_id="session_demo",
                task_frame_id="task_demo",
                path="reports/sales.html",
            ),
            current_user=admin,
            db=db,
        )

        # 过去时间报错 400
        past_time = datetime.now(UTC) - timedelta(hours=1)
        with pytest.raises(HTTPException) as exc:
            update_artifact_share_link(
                link_id=link.link_id,
                req=UpdateShareLinkRequest(expires_at=past_time),
                current_user=admin,
                db=db,
            )
        assert exc.value.status_code == 400

        # 作废链接
        revoke_artifact_share_link(link.link_id, current_user=admin, db=db)

        # 改有效期报错 400
        future_time = datetime.now(UTC) + timedelta(days=5)
        with pytest.raises(HTTPException) as exc:
            update_artifact_share_link(
                link_id=link.link_id,
                req=UpdateShareLinkRequest(expires_at=future_time),
                current_user=admin,
                db=db,
            )
        assert exc.value.status_code == 400

        # 作废链接仅改标题成功
        res = update_artifact_share_link(
            link_id=link.link_id,
            req=UpdateShareLinkRequest(title="作废后的新名称"),
            current_user=admin,
            db=db,
        )
        assert res.title == "作废后的新名称"


def test_update_share_link_rejects_demo_prefix_removal():
    engine = _test_engine()
    with Session(engine) as db:
        admin = _seed_artifact(db)
        link = mint_artifact_share(
            ShareLinkRequest(
                tenant_id="tenant_demo",
                session_id="session_demo",
                task_frame_id="task_demo",
                path="reports/sales.html",
                title="【演示数据】销售分析",
            ),
            current_user=admin,
            db=db,
        )

        # 去掉前缀报错 400
        with pytest.raises(HTTPException) as exc:
            update_artifact_share_link(
                link_id=link.link_id,
                req=UpdateShareLinkRequest(title="正式销售分析"),
                current_user=admin,
                db=db,
            )
        assert exc.value.status_code == 400

        # 保留前缀成功
        res = update_artifact_share_link(
            link_id=link.link_id,
            req=UpdateShareLinkRequest(title="【演示数据】最新销售分析"),
            current_user=admin,
            db=db,
        )
        assert res.title == "【演示数据】最新销售分析"


def test_update_share_link_requires_creator_or_admin():
    engine = _test_engine()
    with Session(engine) as db:
        admin = _seed_artifact(db)
        admin.role = "admin"
        db.add(admin)

        other_member = User(
            id="user_other",
            tenant_id="tenant_demo",
            username="other",
            display_name="李四",
            password_hash="test",
            role="member",
        )
        db.add(other_member)
        db.commit()

        link = mint_artifact_share(
            ShareLinkRequest(
                tenant_id="tenant_demo",
                session_id="session_demo",
                task_frame_id="task_demo",
                path="reports/sales.html",
                title="管理员的链接",
            ),
            current_user=admin,
            db=db,
        )

        # 普通成员无法编辑其他人的链接 -> 403
        with pytest.raises(HTTPException) as exc:
            update_artifact_share_link(
                link_id=link.link_id,
                req=UpdateShareLinkRequest(title="恶意篡改"),
                current_user=other_member,
                db=db,
            )
        assert exc.value.status_code == 403

        # 管理员编辑成功
        res = update_artifact_share_link(
            link_id=link.link_id,
            req=UpdateShareLinkRequest(title="管理员修改"),
            current_user=admin,
            db=db,
        )
        assert res.title == "管理员修改"


def test_update_share_link_noop():
    engine = _test_engine()
    with Session(engine) as db:
        admin = _seed_artifact(db)
        link = mint_artifact_share(
            ShareLinkRequest(
                tenant_id="tenant_demo",
                session_id="session_demo",
                task_frame_id="task_demo",
                path="reports/sales.html",
                title="幂等测试",
            ),
            current_user=admin,
            db=db,
        )

        # 记录初始行数
        from sqlmodel import select
        initial_count = len(db.exec(select(ArtifactShareLink)).all())

        # 提交相同值
        res = update_artifact_share_link(
            link_id=link.link_id,
            req=UpdateShareLinkRequest(title="幂等测试"),
            current_user=admin,
            db=db,
        )
        assert res.title == "幂等测试"
        current_count = len(db.exec(select(ArtifactShareLink)).all())
        assert current_count == initial_count

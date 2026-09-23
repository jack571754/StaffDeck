from __future__ import annotations

from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.api.mock import router as mock_router
from app.api.scheduled_tasks import enterprise_router
from app.db import get_session
from app.db.models import ChannelBinding, ScheduledTask, Tenant, User, utc_now
from app.scheduled_tasks.service import automatic_task_message
from app.security.auth import get_current_user
from app.security.internal_service import INTERNAL_SERVICE_HEADER, internal_service_token


def _build_test_db() -> Session:
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    session = Session(engine)
    session.add(Tenant(id="tenant_demo", name="Demo"))
    session.commit()
    return session


def test_feishu_app_notify_auth_required() -> None:
    session = _build_test_db()
    app = FastAPI()
    app.include_router(mock_router)
    app.dependency_overrides[get_session] = lambda: session
    client = TestClient(app)

    # 1. Reject without token
    resp = client.post(
        "/api/mock/feishu-app-notify",
        json={"tenant_id": "tenant_demo", "chat_id": "oc_test", "card": {"elements": []}},
    )
    assert resp.status_code == 401


def test_feishu_app_notify_no_binding() -> None:
    session = _build_test_db()
    app = FastAPI()
    app.include_router(mock_router)
    app.dependency_overrides[get_session] = lambda: session
    client = TestClient(app)

    token = internal_service_token()
    resp = client.post(
        "/api/mock/feishu-app-notify",
        json={"tenant_id": "tenant_demo", "chat_id": "oc_test", "card": {"elements": []}},
        headers={INTERNAL_SERVICE_HEADER: token},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is False
    assert "未找到租户 tenant_demo 有效的飞书应用绑定" in data["error"]


def test_feishu_app_notify_no_targets() -> None:
    session = _build_test_db()
    session.add(
        ChannelBinding(
            id="bind_feishu_1",
            tenant_id="tenant_demo",
            agent_id="agent_demo",
            channel="feishu",
            status="active",
            config_json={"app_id": "cli_mock_app"},
            credentials_enc="mock_enc",
        )
    )
    session.commit()

    app = FastAPI()
    app.include_router(mock_router)
    app.dependency_overrides[get_session] = lambda: session
    client = TestClient(app)

    token = internal_service_token()
    resp = client.post(
        "/api/mock/feishu-app-notify",
        json={"tenant_id": "tenant_demo", "card": {"elements": []}},
        headers={INTERNAL_SERVICE_HEADER: token},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is False
    assert "未提供任何有效的推送目标" in data["error"]


def test_feishu_app_notify_success_flow_with_mobiles() -> None:
    session = _build_test_db()
    session.add(
        ChannelBinding(
            id="bind_feishu_1",
            tenant_id="tenant_demo",
            agent_id="agent_demo",
            channel="feishu",
            status="active",
            config_json={"app_id": "cli_mock_app"},
            credentials_enc="mock_enc",
        )
    )
    session.commit()

    app = FastAPI()
    app.include_router(mock_router)
    app.dependency_overrides[get_session] = lambda: session
    client = TestClient(app)

    token = internal_service_token()

    with patch("app.channels.adapters.feishu.FeishuAdapter") as mock_adapter_cls:
        mock_adapter = MagicMock()
        mock_adapter_cls.return_value = mock_adapter

        # 模拟手机号反查
        mock_adapter.resolve_open_ids_by_mobiles.return_value = {
            "13800138000": "ou_mobile_user_1",
        }

        # 模拟邮箱反查
        def mock_resolve(binding: ChannelBinding, *, email: str | None = None, **kwargs: object) -> str | None:
            if email == "admin@example.com":
                return "ou_admin_123"
            return None

        mock_adapter.resolve_open_id_by_mobile_or_email.side_effect = mock_resolve
        mock_adapter.create_card.return_value = "om_fake_msg_id"

        payload = {
            "tenant_id": "tenant_demo",
            "chat_id": "oc_test_group",
            "mobiles": ["13800138000", "13900000000"],
            "emails": ["admin@example.com"],
            "open_ids": ["ou_direct_user"],
            "card": {"header": {"title": "Test Title"}, "elements": []},
        }

        resp = client.post(
            "/api/mock/feishu-app-notify",
            json=payload,
            headers={INTERNAL_SERVICE_HEADER: token},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        # chat_id (1) + 13800138000 (1) + admin@example.com (1) + ou_direct_user (1) = 4 sent
        assert data["sent_count"] == 4
        # 13900000000 反查失败 = 1 failed
        assert data["failed_count"] == 1


def test_feishu_app_notify_multi_targets_and_webhooks() -> None:
    session = _build_test_db()
    session.add(
        ChannelBinding(
            id="bind_feishu_1",
            tenant_id="tenant_demo",
            agent_id="agent_demo",
            channel="feishu",
            status="active",
            config_json={"app_id": "cli_mock_app"},
            credentials_enc="mock_enc",
        )
    )
    session.commit()

    app = FastAPI()
    app.include_router(mock_router)
    app.dependency_overrides[get_session] = lambda: session
    client = TestClient(app)

    token = internal_service_token()

    with patch("app.channels.adapters.feishu.FeishuAdapter") as mock_adapter_cls, patch("requests.post") as mock_post:
        mock_adapter = MagicMock()
        mock_adapter_cls.return_value = mock_adapter

        mock_adapter.resolve_open_ids_by_mobiles.return_value = {
            "13800138000": "ou_mobile_user_1",
        }
        mock_adapter.create_card.return_value = "om_fake_msg_id"

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"code": 0, "data": {"message_id": "wh_msg_1"}}
        mock_post.return_value = mock_resp

        payload = {
            "tenant_id": "tenant_demo",
            "chat_ids": ["oc_group_1", "oc_group_2"],
            "webhooks": ["https://open.feishu.cn/bot/hook_1", "https://open.feishu.cn/bot/hook_2"],
            "mobiles": ["13800138000"],
            "card": {"header": {"title": "Test Title"}, "elements": []},
        }

        resp = client.post(
            "/api/mock/feishu-app-notify",
            json=payload,
            headers={INTERNAL_SERVICE_HEADER: token},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        # 2 webhooks + 2 chat_ids + 1 mobile = 5 sent
        assert data["sent_count"] == 5
        assert data["failed_count"] == 0
        assert mock_post.call_count == 2
        assert mock_adapter.create_card.call_count == 3


def test_feishu_app_notify_webhooks_only_without_binding() -> None:
    session = _build_test_db()
    app = FastAPI()
    app.include_router(mock_router)
    app.dependency_overrides[get_session] = lambda: session
    client = TestClient(app)

    token = internal_service_token()

    with patch("requests.post") as mock_post:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"code": 0}
        mock_post.return_value = mock_resp

        payload = {
            "tenant_id": "tenant_demo",
            "webhooks": ["https://open.feishu.cn/bot/hook_external"],
            "card": {"elements": []},
        }

        resp = client.post(
            "/api/mock/feishu-app-notify",
            json=payload,
            headers={INTERNAL_SERVICE_HEADER: token},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert data["sent_count"] == 1
        assert data["failed_count"] == 0


def test_enterprise_list_feishu_chats() -> None:
    session = _build_test_db()
    session.add(
        ChannelBinding(
            id="bind_feishu_1",
            tenant_id="tenant_demo",
            agent_id="agent_demo",
            channel="feishu",
            status="active",
            config_json={"app_id": "cli_mock_app"},
            credentials_enc="mock_enc",
        )
    )
    user = User(id="user_admin", tenant_id="tenant_demo", username="admin", email="admin@test.com", password_hash="x", role="admin")
    session.add(user)
    session.commit()

    app = FastAPI()
    app.include_router(enterprise_router)
    app.dependency_overrides[get_session] = lambda: session
    app.dependency_overrides[get_current_user] = lambda: user
    client = TestClient(app)

    with patch("app.channels.adapters.feishu.FeishuAdapter") as mock_adapter_cls:
        mock_adapter = MagicMock()
        mock_adapter_cls.return_value = mock_adapter
        mock_adapter.list_chats.return_value = [
            {"chat_id": "oc_0abf53b9", "name": "价控小组", "avatar": "https://img.test/1.png"}
        ]

        resp = client.get("/api/enterprise/scheduled-tasks/feishu-chats?tenant_id=tenant_demo")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["name"] == "价控小组"
        assert data[0]["chat_id"] == "oc_0abf53b9"


def test_scheduled_task_automatic_message_feishu_injection() -> None:
    # 1. 没有配置通知时，保持原 prompt
    task1 = ScheduledTask(
        id="task_1",
        tenant_id="tenant_demo",
        agent_id="agent_1",
        created_by_user_id="user_1",
        title="测试未开启通知的任务",
        prompt="请统计当日销售额",
        schedule_type="daily",
        metadata_json={},
    )
    msg1 = automatic_task_message(task1)
    assert msg1 == "请统计当日销售额"

    # 2. 开启多群聊、多个 Webhook 与多个责任人通知
    task2 = ScheduledTask(
        id="task_2",
        tenant_id="tenant_demo",
        agent_id="agent_1",
        created_by_user_id="user_1",
        title="测试多目标通知的任务",
        prompt="请统计当日销售额",
        schedule_type="daily",
        metadata_json={
            "feishu_notify": {
                "enabled": True,
                "chat_ids": ["oc_0abf53b9", "oc_marketing_99"],
                "chat_names": ["价控小组", "营销大群"],
                "webhooks": ["https://open.feishu.cn/open-apis/bot/v2/hook/xxx_1", "https://open.feishu.cn/open-apis/bot/v2/hook/xxx_2"],
                "mobiles": ["13800138000", "13900139000"],
            }
        },
    )
    msg2 = automatic_task_message(task2)
    assert "请统计当日销售额" in msg2
    assert "【系统预设飞书推送配置】" in msg2
    assert "目标群聊（企业应用）: 价控小组 (oc_0abf53b9), 营销大群 (oc_marketing_99)" in msg2
    assert "目标群机器人 (Webhook): Webhook #1" in msg2
    assert "责任人手机号 (私聊直达): 13800138000, 13900139000" in msg2

    # 3. 选定飞书应用时，注入应用标识供 LLM 自述（权威通道仍是服务端解析）
    task3 = ScheduledTask(
        id="task_3",
        tenant_id="tenant_demo",
        agent_id="agent_1",
        created_by_user_id="user_1",
        title="测试选定应用的任务",
        prompt="请统计当日销售额",
        schedule_type="daily",
        metadata_json={
            "feishu_notify": {
                "enabled": True,
                "binding_id": "bind_price_app",
                "app_id": "cli_price_app",
                "app_name": "价控应用",
                "chat_ids": ["oc_0abf53b9"],
                "chat_names": ["价控小组"],
            }
        },
    )
    msg3 = automatic_task_message(task3)
    assert "飞书应用: 价控应用 (binding_id=bind_price_app)" in msg3
    assert "目标群聊（企业应用）: 价控小组 (oc_0abf53b9)" in msg3


# ---------------------------------------------------------------------------
# 飞书应用选择：解析收口、范围隔离与"绝不回退"
# ---------------------------------------------------------------------------


def _feishu_binding(
    binding_id: str,
    *,
    tenant_id: str = "tenant_demo",
    status: str = "active",
    channel: str = "feishu",
    name: str | None = None,
    app_id: str = "cli_mock_app",
    created_at: datetime | None = None,
) -> ChannelBinding:
    return ChannelBinding(
        id=binding_id,
        tenant_id=tenant_id,
        agent_id="agent_demo",
        channel=channel,
        status=status,
        name=name,
        config_json={"app_id": app_id, "bot_open_id": f"ou_bot_{binding_id}", "bot_name": f"bot_{binding_id}"},
        credentials_enc="mock_enc",
        created_at=created_at or utc_now(),
    )


def _scheduled_task(
    task_id: str,
    *,
    tenant_id: str = "tenant_demo",
    feishu_notify: dict[str, object] | None = None,
) -> ScheduledTask:
    return ScheduledTask(
        id=task_id,
        tenant_id=tenant_id,
        agent_id="agent_demo",
        created_by_user_id="user_admin",
        title="测试任务",
        prompt="请统计当日销售额",
        schedule_type="daily",
        metadata_json={"feishu_notify": feishu_notify} if feishu_notify is not None else {},
    )


def _enterprise_client(session: Session) -> TestClient:
    user = User(
        id="user_admin",
        tenant_id="tenant_demo",
        username="admin",
        email="admin@test.com",
        password_hash="x",
        role="admin",
    )
    session.add(user)
    session.commit()
    app = FastAPI()
    app.include_router(enterprise_router)
    app.dependency_overrides[get_session] = lambda: session
    app.dependency_overrides[get_current_user] = lambda: user
    return TestClient(app)


def _notify_client(session: Session) -> TestClient:
    app = FastAPI()
    app.include_router(mock_router)
    app.dependency_overrides[get_session] = lambda: session
    return TestClient(app)


def test_enterprise_list_feishu_chats_with_binding_id() -> None:
    """显式指定较旧的 active 应用时，群列表必须来自该应用而非最新的那条。"""

    session = _build_test_db()
    session.add(_feishu_binding("bind_old", name="旧应用", created_at=utc_now() - timedelta(days=3)))
    session.add(_feishu_binding("bind_new", name="新应用", created_at=utc_now()))
    client = _enterprise_client(session)

    with patch("app.channels.adapters.feishu.FeishuAdapter") as mock_adapter_cls:
        mock_adapter = MagicMock()
        mock_adapter_cls.return_value = mock_adapter
        mock_adapter.list_chats.return_value = [
            {"chat_id": "oc_old_group", "name": "旧应用群"}
        ]

        resp = client.get(
            "/api/enterprise/scheduled-tasks/feishu-chats?tenant_id=tenant_demo&binding_id=bind_old"
        )

        assert resp.status_code == 200
        assert resp.json()[0]["chat_id"] == "oc_old_group"
        mock_adapter.list_chats.assert_called_once()
        assert mock_adapter.list_chats.call_args[0][0].id == "bind_old"


def test_enterprise_list_feishu_chats_rejects_foreign_binding() -> None:
    """他租户的 binding_id 必须被拒，且不得去拉群。"""

    session = _build_test_db()
    session.add(Tenant(id="tenant_other", name="Other"))
    session.add(_feishu_binding("bind_other", tenant_id="tenant_other"))
    session.add(_feishu_binding("bind_mine"))
    client = _enterprise_client(session)

    with patch("app.channels.adapters.feishu.FeishuAdapter") as mock_adapter_cls:
        mock_adapter = MagicMock()
        mock_adapter_cls.return_value = mock_adapter

        resp = client.get(
            "/api/enterprise/scheduled-tasks/feishu-chats?tenant_id=tenant_demo&binding_id=bind_other"
        )

        assert resp.status_code == 400
        assert "所选飞书应用不存在或已停用" in resp.json()["detail"]
        mock_adapter.list_chats.assert_not_called()


def test_enterprise_list_feishu_chats_rejects_inactive_binding() -> None:
    """停用的应用直接报错，绝不回退到另一个 active 应用。"""

    session = _build_test_db()
    session.add(_feishu_binding("bind_disabled", status="disabled"))
    session.add(_feishu_binding("bind_active"))
    client = _enterprise_client(session)

    with patch("app.channels.adapters.feishu.FeishuAdapter") as mock_adapter_cls:
        mock_adapter = MagicMock()
        mock_adapter_cls.return_value = mock_adapter

        resp = client.get(
            "/api/enterprise/scheduled-tasks/feishu-chats?tenant_id=tenant_demo&binding_id=bind_disabled"
        )

        assert resp.status_code == 400
        assert "所选飞书应用不存在或已停用" in resp.json()["detail"]
        mock_adapter.list_chats.assert_not_called()


def test_enterprise_list_feishu_apps_scoped_to_tenant_and_channel() -> None:
    """只返回本租户的飞书应用，且不泄露凭证与渠道配置。"""

    session = _build_test_db()
    session.add(Tenant(id="tenant_other", name="Other"))
    session.add(_feishu_binding("bind_new", name="新应用", created_at=utc_now()))
    session.add(_feishu_binding("bind_old", name="旧应用", created_at=utc_now() - timedelta(days=3)))
    session.add(_feishu_binding("bind_disabled", status="disabled", created_at=utc_now() - timedelta(days=9)))
    session.add(_feishu_binding("bind_wecom", channel="wecom"))
    session.add(_feishu_binding("bind_other", tenant_id="tenant_other"))
    client = _enterprise_client(session)

    resp = client.get("/api/enterprise/scheduled-tasks/feishu-apps?tenant_id=tenant_demo")

    assert resp.status_code == 200
    apps = resp.json()
    assert [app["id"] for app in apps] == ["bind_new", "bind_old", "bind_disabled"]
    assert [app["is_default"] for app in apps] == [True, False, False]
    assert apps[0]["name"] == "新应用"
    assert apps[0]["app_id"] == "cli_mock_app"
    assert apps[0]["status"] == "active"
    for app in apps:
        assert "credentials_enc" not in app
        assert "config_json" not in app
        assert "created_by_user_id" not in app


def test_feishu_app_notify_resolves_binding_from_scheduled_task() -> None:
    """任务选定应用后，仅凭 scheduled_task_id 即可用该应用发卡。"""

    session = _build_test_db()
    session.add(_feishu_binding("bind_old", name="旧应用", created_at=utc_now() - timedelta(days=3)))
    session.add(_feishu_binding("bind_new", name="新应用", created_at=utc_now()))
    session.add(_scheduled_task("task_price", feishu_notify={"enabled": True, "binding_id": "bind_old"}))
    session.commit()
    client = _notify_client(session)

    with patch("app.channels.adapters.feishu.FeishuAdapter") as mock_adapter_cls:
        mock_adapter = MagicMock()
        mock_adapter_cls.return_value = mock_adapter
        mock_adapter.create_card.return_value = "om_msg_1"

        resp = client.post(
            "/api/mock/feishu-app-notify",
            json={
                "tenant_id": "tenant_demo",
                "scheduled_task_id": "task_price",
                "chat_ids": ["oc_group_1"],
                "card": {"elements": []},
            },
            headers={INTERNAL_SERVICE_HEADER: internal_service_token()},
        )

        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert data["sent_count"] == 1
        mock_adapter.create_card.assert_called_once()
        assert mock_adapter.create_card.call_args.kwargs["binding"].id == "bind_old"


def test_feishu_app_notify_scheduled_task_binding_unavailable_no_fallback() -> None:
    """任务所选应用已停用时报错，且绝不改用租户里的另一个 active 应用。"""

    session = _build_test_db()
    session.add(_feishu_binding("bind_disabled", status="disabled"))
    session.add(_feishu_binding("bind_active"))
    session.add(
        _scheduled_task("task_price", feishu_notify={"enabled": True, "binding_id": "bind_disabled"})
    )
    session.commit()
    client = _notify_client(session)

    with patch("app.channels.adapters.feishu.FeishuAdapter") as mock_adapter_cls:
        mock_adapter = MagicMock()
        mock_adapter_cls.return_value = mock_adapter

        resp = client.post(
            "/api/mock/feishu-app-notify",
            json={
                "tenant_id": "tenant_demo",
                "scheduled_task_id": "task_price",
                "chat_ids": ["oc_group_1"],
                "card": {"elements": []},
            },
            headers={INTERNAL_SERVICE_HEADER: internal_service_token()},
        )

        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is False
        assert "所选飞书应用不存在或已停用" in data["error"]
        mock_adapter.create_card.assert_not_called()


def test_feishu_app_notify_binding_hint_mismatch_errors() -> None:
    """任务绑定与请求参数指向不同应用时不猜、不择一，直接报错。"""

    session = _build_test_db()
    session.add(_feishu_binding("bind_old"))
    session.add(_feishu_binding("bind_new"))
    session.add(_scheduled_task("task_price", feishu_notify={"enabled": True, "binding_id": "bind_old"}))
    session.commit()
    client = _notify_client(session)

    with patch("app.channels.adapters.feishu.FeishuAdapter") as mock_adapter_cls:
        mock_adapter = MagicMock()
        mock_adapter_cls.return_value = mock_adapter

        resp = client.post(
            "/api/mock/feishu-app-notify",
            json={
                "tenant_id": "tenant_demo",
                "scheduled_task_id": "task_price",
                "binding_id": "bind_new",
                "chat_ids": ["oc_group_1"],
                "card": {"elements": []},
            },
            headers={INTERNAL_SERVICE_HEADER: internal_service_token()},
        )

        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is False
        assert "不一致" in data["error"]
        mock_adapter.create_card.assert_not_called()


def test_feishu_app_notify_scheduled_task_tenant_mismatch() -> None:
    """他租户的 scheduled_task_id 必须被拒，且不得读取其 metadata。"""

    session = _build_test_db()
    session.add(Tenant(id="tenant_other", name="Other"))
    session.add(_feishu_binding("bind_old"))
    session.add(
        _scheduled_task(
            "task_other",
            tenant_id="tenant_other",
            feishu_notify={"enabled": True, "binding_id": "bind_old"},
        )
    )
    session.commit()
    client = _notify_client(session)

    with patch("app.channels.adapters.feishu.FeishuAdapter") as mock_adapter_cls:
        mock_adapter = MagicMock()
        mock_adapter_cls.return_value = mock_adapter

        resp = client.post(
            "/api/mock/feishu-app-notify",
            json={
                "tenant_id": "tenant_demo",
                "scheduled_task_id": "task_other",
                "chat_ids": ["oc_group_1"],
                "card": {"elements": []},
            },
            headers={INTERNAL_SERVICE_HEADER: internal_service_token()},
        )

        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is False
        assert "不属于租户 tenant_demo" in data["error"]
        mock_adapter.create_card.assert_not_called()


def test_feishu_app_notify_legacy_task_without_binding_falls_back() -> None:
    """存量任务没有 binding_id，仍走"最新 active"旧行为（零回归守卫）。"""

    session = _build_test_db()
    session.add(_feishu_binding("bind_old", created_at=utc_now() - timedelta(days=3)))
    session.add(_feishu_binding("bind_new", created_at=utc_now()))
    session.add(_scheduled_task("task_legacy", feishu_notify={"enabled": True, "chat_ids": ["oc_group_1"]}))
    session.commit()
    client = _notify_client(session)

    with patch("app.channels.adapters.feishu.FeishuAdapter") as mock_adapter_cls:
        mock_adapter = MagicMock()
        mock_adapter_cls.return_value = mock_adapter
        mock_adapter.create_card.return_value = "om_msg_1"

        resp = client.post(
            "/api/mock/feishu-app-notify",
            json={
                "tenant_id": "tenant_demo",
                "scheduled_task_id": "task_legacy",
                "chat_ids": ["oc_group_1"],
                "card": {"elements": []},
            },
            headers={INTERNAL_SERVICE_HEADER: internal_service_token()},
        )

        assert resp.status_code == 200
        assert resp.json()["ok"] is True
        assert mock_adapter.create_card.call_args.kwargs["binding"].id == "bind_new"


def test_feishu_app_notify_rejects_foreign_binding_param() -> None:
    """请求直接指定他租户 binding_id 时必须报错（原有跨租户缺口）。"""

    session = _build_test_db()
    session.add(Tenant(id="tenant_other", name="Other"))
    session.add(_feishu_binding("bind_other", tenant_id="tenant_other"))
    session.commit()
    client = _notify_client(session)

    with patch("app.channels.adapters.feishu.FeishuAdapter") as mock_adapter_cls:
        mock_adapter = MagicMock()
        mock_adapter_cls.return_value = mock_adapter

        resp = client.post(
            "/api/mock/feishu-app-notify",
            json={
                "tenant_id": "tenant_demo",
                "binding_id": "bind_other",
                "chat_ids": ["oc_group_1"],
                "card": {"elements": []},
            },
            headers={INTERNAL_SERVICE_HEADER: internal_service_token()},
        )

        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is False
        assert "所选飞书应用不存在或已停用" in data["error"]
        mock_adapter.create_card.assert_not_called()


"""Tests for scheduled task duplication, test-notify, and Feishu recipient resolution."""

from __future__ import annotations

from unittest.mock import patch

from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.api.mock import FeishuAppNotifyRequest, feishu_app_notify
from app.db.models import AgentProfile, ChannelBinding, ChannelIdentity, Tenant, User
from app.scheduled_tasks.schema import ScheduledTaskCreateRequest
from app.scheduled_tasks.service import (
    build_test_feishu_card,
    create_scheduled_task,
    duplicate_scheduled_task,
)


def _test_session() -> Session:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    return Session(engine)


def _seed(db: Session) -> None:
    db.add(Tenant(id="tenant_demo", name="Demo"))
    db.add(AgentProfile(id="agent_demo", tenant_id="tenant_demo", name="销售播报员", is_overall=False))
    db.add(
        User(
            id="user_admin",
            tenant_id="tenant_demo",
            username="admin",
            display_name="管理员",
            role="admin",
            password_hash="test-hash",
        )
    )
    db.add(
        ChannelIdentity(
            id="ident_1",
            tenant_id="tenant_demo",
            channel="feishu",
            external_user_id="ou_test12345678",
            staffdeck_user_id="user_admin",
            display_name="张三",
        )
    )
    db.add(
        ChannelBinding(
            id="bind_1",
            tenant_id="tenant_demo",
            agent_id="agent_demo",
            channel="feishu",
            name="企业自建应用",
            status="active",
            credentials={"app_id": "cli_test", "app_secret": "sec_test"},
        )
    )
    db.commit()


def test_duplicate_scheduled_task():
    db = _test_session()
    _seed(db)
    creator = db.get(User, "user_admin")
    assert creator is not None

    req = ScheduledTaskCreateRequest(
        tenant_id="tenant_demo",
        agent_id="agent_demo",
        title="实时销售定时播报",
        prompt="请查询销售数据并播报",
        schedule_type="interval",
        schedule={"unit": "hours", "interval": 1},
        metadata={
            "feishu_notify": {
                "enabled": True,
                "binding_id": "bind_1",
                "chat_ids": ["oc_chat1"],
                "chat_names": ["销售数据群"],
                "mobiles": ["13800000000"],
                "open_ids": ["ou_test12345678"],
                "webhooks": ["https://open.feishu.cn/open-apis/bot/v2/hook/abc"],
            },
        },
    )
    original = create_scheduled_task(db, req, creator)
    original.run_count = 5
    db.add(original)
    db.commit()
    db.refresh(original)

    dup = duplicate_scheduled_task(db, original, creator)
    assert dup.id != original.id
    assert dup.title == "实时销售定时播报 (副本)"
    assert dup.status == "paused"
    assert dup.run_count == 0
    assert dup.last_run_at is None
    assert dup.prompt == original.prompt

    meta = dup.metadata_json or {}
    feishu = meta.get("feishu_notify") or {}
    assert feishu.get("enabled") is True
    assert feishu.get("binding_id") == "bind_1"
    assert feishu.get("chat_ids") == ["oc_chat1"]
    assert feishu.get("open_ids") == ["ou_test12345678"]
    assert feishu.get("mobiles") == ["13800000000"]
    assert feishu.get("webhooks") == ["https://open.feishu.cn/open-apis/bot/v2/hook/abc"]


def test_build_test_feishu_card():
    card = build_test_feishu_card(title="实时销售播报")
    header = card.get("header") or {}
    title = header.get("title") or {}
    assert "测试推送" in title.get("content", "")
    assert header.get("template") == "blue"
    elements = card.get("elements") or []
    assert len(elements) > 0


def test_feishu_app_notify_with_open_ids():
    from app.channels.adapters.feishu import FeishuAdapter

    db = _test_session()
    _seed(db)

    with patch.object(FeishuAdapter, "create_card", return_value="om_123"), \
         patch.object(FeishuAdapter, "resolve_open_ids_by_mobiles", return_value={"13800000000": "ou_from_mobile"}):

        req = FeishuAppNotifyRequest(
            tenant_id="tenant_demo",
            binding_id="bind_1",
            chat_ids=["oc_chat1"],
            open_ids=["ou_direct_user"],
            mobiles=["13800000000"],
            card={"type": "template", "data": {}},
        )
        res = feishu_app_notify(req, db=db)
        assert res.get("ok") is True
        assert res.get("sent_count") == 3
        assert res.get("failed_count") == 0


def test_list_feishu_recipients_resolution():
    from app.api.scheduled_tasks import list_feishu_recipients

    db = _test_session()
    _seed(db)
    user = db.get(User, "user_admin")
    assert user is not None

    res = list_feishu_recipients(tenant_id="tenant_demo", current_user=user, db=db)
    assert len(res) == 1
    assert res[0]["open_id"] == "ou_test12345678"
    assert "张三" in res[0]["display_name"]



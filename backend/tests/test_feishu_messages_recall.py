"""Tests for Feishu outbound message tracking, stats, and recall endpoints."""

from __future__ import annotations

from unittest.mock import patch

from fastapi import HTTPException
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.api.channels import (
    get_feishu_outbound_stats,
    list_feishu_outbound_messages,
    recall_feishu_message,
)
from app.channels.adapters.feishu import FeishuAdapter
from app.db.models import (
    ChannelBinding,
    FeishuOutboundMessage,
    Tenant,
    User,
)


def _test_session() -> Session:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    return Session(engine)


def _seed(db: Session) -> tuple[Tenant, User, ChannelBinding]:
    tenant = Tenant(id="tenant_demo", name="Demo")
    user = User(
        id="user_demo",
        tenant_id="tenant_demo",
        username="admin",
        display_name="系统管理员",
        role="admin",
        password_hash="test-hash",
    )
    from app.db.models import AgentProfile

    agent = AgentProfile(
        id="agent_demo",
        tenant_id="tenant_demo",
        name="测试播报员工",
        is_overall=False,
        status="active",
    )
    binding = ChannelBinding(
        id="ch_feishu_1",
        tenant_id="tenant_demo",
        agent_id=agent.id,
        channel="feishu",
        name="飞书生产应用",
        config_json={"app_id": "cli_test_123"},
        credentials_enc="test_enc",
        status="active",
    )
    db.add(tenant)
    db.add(user)
    db.add(agent)
    db.add(binding)
    db.commit()
    return tenant, user, binding


def test_list_and_stats_feishu_outbound_messages() -> None:
    db = _test_session()
    tenant, user, binding = _seed(db)

    # Insert 2 messages
    msg1 = FeishuOutboundMessage(
        id="fsmsg_1",
        tenant_id=tenant.id,
        binding_id=binding.id,
        channel_type="app_bot",
        feishu_message_id="om_111",
        target_type="chat_id",
        target_identifier="oc_sales_group",
        target_name="销售大盘群",
        title="实时销售早报",
        card_json={"header": {"title": {"content": "实时销售早报"}}},
        status="delivered",
    )
    msg2 = FeishuOutboundMessage(
        id="fsmsg_2",
        tenant_id=tenant.id,
        channel_type="webhook",
        target_type="webhook",
        target_identifier="https://open.feishu.cn/open-apis/bot/v2/hook/xxx",
        target_name="价格预警群",
        title="价格波动通知",
        card_json={},
        status="failed",
        error_message="HTTP 500",
    )
    db.add(msg1)
    db.add(msg2)
    db.commit()

    page = list_feishu_outbound_messages(tenant_id=tenant.id, current_user=user, db=db)
    assert page.total == 2
    assert len(page.items) == 2
    item_map = {item.id: item for item in page.items}
    assert item_map["fsmsg_1"].can_recall is True
    assert item_map["fsmsg_2"].can_recall is False

    stats = get_feishu_outbound_stats(tenant_id=tenant.id, current_user=user, db=db)
    assert stats.total_today == 2
    assert stats.delivered_today == 1
    assert stats.failed_today == 1
    assert stats.recalled_today == 0


def test_recall_feishu_message_success_and_webhook_reject() -> None:
    db = _test_session()
    tenant, user, binding = _seed(db)

    app_msg = FeishuOutboundMessage(
        id="fsmsg_app",
        tenant_id=tenant.id,
        binding_id=binding.id,
        channel_type="app_bot",
        feishu_message_id="om_target_to_recall",
        target_type="chat_id",
        target_identifier="oc_sales_group",
        title="销售战报",
        status="delivered",
    )
    webhook_msg = FeishuOutboundMessage(
        id="fsmsg_wh",
        tenant_id=tenant.id,
        channel_type="webhook",
        target_type="webhook",
        target_identifier="https://open.feishu.cn/open-apis/bot/v2/hook/xxx",
        status="delivered",
    )
    db.add(app_msg)
    db.add(webhook_msg)
    db.commit()

    # Reject webhook recall
    try:
        recall_feishu_message(message_id="fsmsg_wh", tenant_id=tenant.id, current_user=user, db=db)
        assert False, "Should raise HTTPException for webhook recall"
    except HTTPException as exc:
        assert exc.status_code == 400
        assert "Webhook 不支持 API 撤回" in exc.detail

    # Successful app bot message recall
    with patch.object(FeishuAdapter, "recall_message", return_value=None) as mock_recall:
        res = recall_feishu_message(message_id="fsmsg_app", tenant_id=tenant.id, current_user=user, db=db)
        assert res["ok"] is True
        mock_recall.assert_called_once()
        call_binding, call_msg_id = mock_recall.call_args[0]
        assert call_binding.id == binding.id
        assert call_msg_id == "om_target_to_recall"

    db.refresh(app_msg)
    assert app_msg.status == "recalled"
    assert app_msg.recalled_at is not None
    assert app_msg.recalled_by == "系统管理员"


def test_feishu_app_notify_persists_outbound_messages() -> None:
    from app.api.mock import FeishuAppNotifyRequest, feishu_app_notify

    db = _test_session()
    tenant, _user, _binding = _seed(db)

    req = FeishuAppNotifyRequest(
        tenant_id=tenant.id,
        chat_id="oc_test_123",
        card={"header": {"title": {"content": "测试推送卡片"}}, "elements": []},
    )

    with patch.object(FeishuAdapter, "create_card", return_value="om_created_test_123"):
        res = feishu_app_notify(req, db=db)
        assert res["ok"] is True
        assert res["sent_count"] == 1

    from sqlmodel import select
    msgs = db.exec(select(FeishuOutboundMessage).where(FeishuOutboundMessage.tenant_id == tenant.id)).all()
    assert len(msgs) == 1
    assert msgs[0].channel_type == "app_bot"
    assert msgs[0].feishu_message_id == "om_created_test_123"
    assert msgs[0].target_identifier == "oc_test_123"
    assert msgs[0].title == "测试推送卡片"
    assert msgs[0].status == "delivered"


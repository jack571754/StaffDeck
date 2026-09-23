from __future__ import annotations

from unittest.mock import patch

import pytest
from sqlmodel import Session, SQLModel, create_engine, select
from sqlmodel.pool import StaticPool

from app.channels.service_intent_fast_path import try_handle_intent_fast_path
from app.data_query.intent_router import RouteResult
from app.data_query.models import QueryExecuteResult
from app.db.models import (
    ChannelBinding,
    ChannelDelivery,
    ChannelInboundEvent,
    ChatSession,
    Message,
)


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


def test_intent_fast_path_unmatched(db_session: Session):
    binding = ChannelBinding(
        id="bind_1",
        tenant_id="tenant_test",
        channel="feishu",
        agent_id="agent_1",
    )
    session = ChatSession(
        id="sess_1",
        tenant_id="tenant_test",
        agent_id="agent_1",
        user_id="user_1",
    )
    event = ChannelInboundEvent(
        id="evt_1",
        tenant_id="tenant_test",
        binding_id="bind_1",
        event_id="feishu_event_1",
        channel="feishu",
        status="received",
    )
    db_session.add_all([binding, session, event])
    db_session.commit()

    with patch("app.channels.service_intent_fast_path.route_question", return_value=None):
        handled = try_handle_intent_fast_path(
            db=db_session,
            binding=binding,
            chat_session=session,
            event=event,
            user_message="今日天气怎么样",
            user_id="user_1",
            target={"receive_id": "ou_xxx"},
        )

    assert handled is False
    # Event remains received
    assert event.status == "received"


def test_intent_fast_path_matched_and_delivered(db_session: Session):
    binding = ChannelBinding(
        id="bind_1",
        tenant_id="tenant_test",
        channel="feishu",
        agent_id="agent_1",
        external_account_key="feishu_app",
        status="active",
    )
    session = ChatSession(
        id="sess_1",
        tenant_id="tenant_test",
        agent_id="agent_1",
        user_id="user_1",
        channel="feishu",
        channel_binding_id="bind_1",
        channel_account_key="feishu_app",
        channel_target_json={"receive_id": "ou_xxx"},
    )
    event = ChannelInboundEvent(
        id="evt_1",
        tenant_id="tenant_test",
        binding_id="bind_1",
        event_id="feishu_event_1",
        channel="feishu",
        status="received",
    )
    db_session.add_all([binding, session, event])
    db_session.commit()

    mock_route = RouteResult(
        template_id="qt_sales_summary",
        template_name="query_realtime_sales_summary",
        params={"end_date": "2026-09-21"},
        confidence=0.96,
        explanation="匹配销售汇总",
    )

    mock_exec_result = QueryExecuteResult(
        template_id="qt_sales_summary",
        columns=["department", "total_sales"],
        rows=[
            {"department": "美妆运营部", "total_sales": 128500.5},
            {"department": "TOTAL", "total_sales": 128500.5},
        ],
        row_count=2,
        execution_time_ms=45.2,
    )

    with (
        patch("app.channels.service_intent_fast_path.route_question", return_value=mock_route),
        patch("app.channels.service_intent_fast_path.execute_query_by_id", return_value=mock_exec_result),
    ):
        handled = try_handle_intent_fast_path(
            db=db_session,
            binding=binding,
            chat_session=session,
            event=event,
            user_message="查一下今天实时销售汇总",
            user_id="user_1",
            target={"receive_id": "ou_xxx"},
        )

    assert handled is True
    assert event.status == "done"

    # Verify assistant message was saved
    messages = db_session.exec(select(Message).where(Message.session_id == session.id)).all()
    assert len(messages) == 2  # user + assistant
    asst_msg = next(m for m in messages if m.role == "assistant")
    assert "美妆运营部" in asst_msg.content
    assert "128500.5" in asst_msg.content
    assert "query_realtime_sales_summary" in asst_msg.content

    # Verify channel delivery was staged
    deliveries = db_session.exec(select(ChannelDelivery)).all()
    assert len(deliveries) == 1
    assert deliveries[0].kind == "reply"
    assert "美妆运营部" in deliveries[0].text

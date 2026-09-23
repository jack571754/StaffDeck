from __future__ import annotations

from datetime import date
from unittest.mock import patch

import pytest
from sqlmodel import Session, SQLModel, create_engine
from sqlmodel.pool import StaticPool

from app.data_query.intent_router import (
    RouteResult,
    clear_intent_template_cache,
    get_cached_templates,
    route_question,
)
from app.data_query.models import DataSource, QueryTemplate


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


def test_cached_templates_tenant_isolation(db_session: Session):
    clear_intent_template_cache()

    # Create dummy data source
    ds = DataSource(
        id="ds_1",
        tenant_id="tenant_a",
        name="sales_db",
        type="mysql",
        config_json={},
    )
    db_session.add(ds)

    # Active template for tenant A
    qt_a = QueryTemplate(
        id="qt_sales_summary",
        tenant_id="tenant_a",
        data_source_id="ds_1",
        name="query_realtime_sales_summary",
        description="查询实时销售汇总数据",
        query_type="sql",
        query_content="SELECT 1",
        status="active",
        params_json=[
            {
                "name": "end_date",
                "type": "date",
                "description": "销售截止日期",
                "default": "today",
                "compare_to": "end_date_prev",
            }
        ],
    )
    # Draft template for tenant A (should be ignored)
    qt_draft = QueryTemplate(
        id="qt_draft",
        tenant_id="tenant_a",
        data_source_id="ds_1",
        name="draft_template",
        description="草稿模板",
        status="draft",
    )
    # Active template for tenant B
    qt_b = QueryTemplate(
        id="qt_b_only",
        tenant_id="tenant_b",
        data_source_id="ds_1",
        name="query_tenant_b",
        description="租户B专用模板",
        status="active",
    )
    db_session.add_all([qt_a, qt_draft, qt_b])
    db_session.commit()

    templates_a = get_cached_templates(db_session, "tenant_a")
    assert len(templates_a) == 1
    assert templates_a[0]["id"] == "qt_sales_summary"
    assert templates_a[0]["name"] == "query_realtime_sales_summary"

    templates_b = get_cached_templates(db_session, "tenant_b")
    assert len(templates_b) == 1
    assert templates_b[0]["id"] == "qt_b_only"

    # Invalidate cache
    clear_intent_template_cache("tenant_a")
    templates_a_reloaded = get_cached_templates(db_session, "tenant_a")
    assert len(templates_a_reloaded) == 1


def test_route_question_high_confidence(db_session: Session):
    clear_intent_template_cache()
    ds = DataSource(id="ds_1", tenant_id="tenant_a", name="sales_db", type="mysql")
    qt_a = QueryTemplate(
        id="qt_sales",
        tenant_id="tenant_a",
        data_source_id="ds_1",
        name="query_realtime_sales_summary",
        description="查询实时销售汇总数据",
        status="active",
        params_json=[
            {
                "name": "end_date",
                "type": "date",
                "description": "销售日期",
                "compare_to": "end_date_prev",
            }
        ],
    )
    db_session.add_all([ds, qt_a])
    db_session.commit()

    mock_llm_response = {
        "matched": True,
        "template_id": "qt_sales",
        "template_name": "query_realtime_sales_summary",
        "params": {"end_date": "昨天"},
        "confidence": 0.95,
        "explanation": "用户询问昨天的销售汇总",
    }

    with patch("app.data_query.intent_router._call_router_llm", return_value=mock_llm_response):
        result = route_question(
            question="看下昨天的实时销售汇总",
            tenant_id="tenant_a",
            db=db_session,
            base_date=date(2026, 9, 21),
        )

    assert result is not None
    assert isinstance(result, RouteResult)
    assert result.template_id == "qt_sales"
    assert result.template_name == "query_realtime_sales_summary"
    assert result.confidence == 0.95
    # Relative date '昨天' is converted relative to base_date 2026-09-21
    assert result.params["end_date"] == "2026-09-20"
    # compare_to auto injects end_date_prev!
    assert result.params["end_date_prev"] == "2026-09-19"


def test_route_question_low_confidence_fallback(db_session: Session):
    clear_intent_template_cache()
    ds = DataSource(id="ds_1", tenant_id="tenant_a", name="sales_db", type="mysql")
    qt_a = QueryTemplate(
        id="qt_sales",
        tenant_id="tenant_a",
        data_source_id="ds_1",
        name="query_realtime_sales_summary",
        description="查询实时销售汇总数据",
        status="active",
    )
    db_session.add_all([ds, qt_a])
    db_session.commit()

    # Below default threshold 0.8
    mock_low_conf = {
        "matched": True,
        "template_id": "qt_sales",
        "confidence": 0.65,
        "params": {},
    }

    with patch("app.data_query.intent_router._call_router_llm", return_value=mock_low_conf):
        result = route_question(
            question="天气怎么样",
            tenant_id="tenant_a",
            db=db_session,
        )

    assert result is None


def test_route_question_unmatched_or_error(db_session: Session):
    clear_intent_template_cache()

    # 1. Matched is False
    with patch("app.data_query.intent_router._call_router_llm", return_value={"matched": False}):
        assert (
            route_question(
                question="随便聊聊",
                tenant_id="tenant_a",
                db=db_session,
            )
            is None
        )

    # 2. LLM throws exception
    with patch("app.data_query.intent_router._call_router_llm", side_effect=RuntimeError("LLM Down")):
        assert (
            route_question(
                question="看下数据",
                tenant_id="tenant_a",
                db=db_session,
            )
            is None
        )

    # 3. Empty question
    assert (
        route_question(
            question="",
            tenant_id="tenant_a",
            db=db_session,
        )
        is None
    )

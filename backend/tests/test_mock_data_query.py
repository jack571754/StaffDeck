from __future__ import annotations

from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, create_engine
from sqlalchemy.pool import StaticPool

from app.api.mock import router as mock_router
from app.data_query.api import router as data_query_router
from app.data_query.models import DataSource, QueryExecuteResult, QueryTemplate
from app.data_query.service import execute_query_by_id
from app.db import get_session
from app.db.models import Tenant, User
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
    session.add(
        DataSource(
            id="ds_test",
            tenant_id="tenant_demo",
            name="Test DS",
            type="sqlite",
            status="active",
        )
    )
    session.add(
        QueryTemplate(
            id="qt_test_123",
            tenant_id="tenant_demo",
            name="实时销售汇总播报表",
            data_source_id="ds_test",
            query_type="sql",
            query_content="SELECT 1",
            status="active",
            tool_id="query_realtime_sales_summary",
        )
    )
    session.commit()
    return session


def test_mock_data_query_auth_and_execution() -> None:
    session = _build_test_db()
    app = FastAPI()
    app.include_router(mock_router)
    app.dependency_overrides[get_session] = lambda: session
    client = TestClient(app)

    # 1. Reject without token
    assert client.post("/api/mock/data-query/qt_test_123", json={"params": {}}).status_code == 401

    # 2. Succeed with internal token (mocking connector executor)
    fake_result = QueryExecuteResult(
        template_id="qt_test_123",
        columns=["category", "item", "净销_万"],
        rows=[{"category": "电商整体", "item": "整体", "净销_万": 368.52}],
        row_count=1,
        execution_time_ms=12.5,
    )

    with patch("app.data_query.service.test_query_template", return_value=fake_result):
        resp = client.post(
            "/api/mock/data-query/qt_test_123",
            headers={INTERNAL_SERVICE_HEADER: internal_service_token()},
            json={"params": {"end_date": "2026-09-23"}},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["template_id"] == "qt_test_123"
        assert data["columns"] == ["category", "item", "净销_万"]
        assert len(data["rows"]) == 1


def test_query_template_execute_by_path_endpoint() -> None:
    session = _build_test_db()
    app = FastAPI()
    app.include_router(data_query_router)
    admin_user = User(
        id="user_admin", tenant_id="tenant_demo", username="admin", role="admin", password_hash="test"
    )
    app.dependency_overrides[get_session] = lambda: session
    app.dependency_overrides[get_current_user] = lambda: admin_user
    client = TestClient(app)

    fake_result = QueryExecuteResult(
        template_id="qt_test_123",
        columns=["col1"],
        rows=[{"col1": "val1"}],
        row_count=1,
    )

    with patch("app.data_query.service.test_query_template", return_value=fake_result):
        resp = client.post(
            "/api/enterprise/data-query/query-templates/qt_test_123/execute",
            json={"params": {"end_date": "2026-09-23"}},
        )
        assert resp.status_code == 200
        assert resp.json()["columns"] == ["col1"]


def test_execute_query_by_id_name_or_tool_id_fallback() -> None:
    session = _build_test_db()
    fake_result = QueryExecuteResult(
        template_id="qt_test_123",
        columns=["col1"],
        rows=[{"col1": "val1"}],
        row_count=1,
    )

    with patch("app.data_query.service.test_query_template", return_value=fake_result):
        # By ID
        res1 = execute_query_by_id(session, "qt_test_123", "tenant_demo", {})
        assert res1.template_id == "qt_test_123"

        # By name
        res2 = execute_query_by_id(session, "实时销售汇总播报表", "tenant_demo", {})
        assert res2.template_id == "qt_test_123"

        # By tool_id
        res3 = execute_query_by_id(session, "query_realtime_sales_summary", "tenant_demo", {})
        assert res3.template_id == "qt_test_123"

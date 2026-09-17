"""Tests for the data query center REST API.

Uses FastAPI TestClient with an in-memory SQLite database.  Connector
calls are mocked so no real database / HTTP connections are needed.
"""

from __future__ import annotations

from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.data_query.api import router as data_query_router
from app.data_query.connectors.base import BaseConnector, QueryResult
from app.db import get_session
from app.db.models import Tenant, User
from app.security.auth import create_access_token, hash_password

# ---------------------------------------------------------------------------
# Test fixtures / helpers
# ---------------------------------------------------------------------------


def _test_engine():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    return engine


def _make_client(engine) -> TestClient:
    app = FastAPI()
    app.include_router(data_query_router)

    def override_get_session():
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_session] = override_get_session
    return TestClient(app)


def _seed_tenant_and_user(
    engine,
    *,
    tenant_id: str = "tenant_a",
    user_id: str = "user_a",
    username: str = "alice",
) -> User:
    with Session(engine) as db:
        db.add(Tenant(id=tenant_id, name=f"Tenant {tenant_id}"))
        user = User(
            id=user_id,
            tenant_id=tenant_id,
            username=username,
            display_name=username.title(),
            password_hash=hash_password("secret"),
            role="admin",
        )
        db.add(user)
        db.commit()
        db.refresh(user)
        db.expunge(user)
        return user


def _auth(user: User) -> dict[str, str]:
    return {"Authorization": f"Bearer {create_access_token(user)}"}


def _make_ds_payload(**overrides):
    payload = {
        "name": "Test MySQL",
        "description": "A test data source",
        "type": "mysql",
        "config_json": {
            "host": "localhost",
            "port": 3306,
            "user": "root",
            "password": "secret123",
            "database": "testdb",
        },
        "read_only": True,
        "status": "active",
    }
    payload.update(overrides)
    return payload


def _make_qt_payload(data_source_id: str, **overrides):
    payload = {
        "name": "User query",
        "description": "List users by status",
        "data_source_id": data_source_id,
        "query_type": "sql",
        "query_content": "SELECT id, name FROM users WHERE status = :status",
        "params_json": [
            {"name": "status", "type": "string", "default": "active"}
        ],
        "output_config_json": {"columns": ["id", "name"]},
        "cache_ttl": 300,
        "timeout_seconds": 30,
        "max_rows": 1000,
        "status": "draft",
    }
    payload.update(overrides)
    return payload


# ---------------------------------------------------------------------------
# 1. Data source CRUD
# ---------------------------------------------------------------------------


def test_data_source_crud() -> None:
    engine = _test_engine()
    user = _seed_tenant_and_user(engine)
    client = _make_client(engine)
    headers = _auth(user)
    params = {"tenant_id": "tenant_a"}

    # Create
    resp = client.post(
        "/api/enterprise/data-query/data-sources",
        params=params,
        json=_make_ds_payload(),
        headers=headers,
    )
    assert resp.status_code == 201
    ds = resp.json()
    assert ds["name"] == "Test MySQL"
    assert ds["type"] == "mysql"
    assert ds["status"] == "active"
    ds_id = ds["id"]

    # List
    resp = client.get(
        "/api/enterprise/data-query/data-sources",
        params=params,
        headers=headers,
    )
    assert resp.status_code == 200
    items = resp.json()
    assert len(items) >= 1
    assert any(item["id"] == ds_id for item in items)

    # Detail
    resp = client.get(
        f"/api/enterprise/data-query/data-sources/{ds_id}",
        params=params,
        headers=headers,
    )
    assert resp.status_code == 200
    assert resp.json()["id"] == ds_id

    # Update
    resp = client.put(
        f"/api/enterprise/data-query/data-sources/{ds_id}",
        params=params,
        json={"name": "Updated MySQL"},
        headers=headers,
    )
    assert resp.status_code == 200
    assert resp.json()["name"] == "Updated MySQL"

    # Delete
    resp = client.delete(
        f"/api/enterprise/data-query/data-sources/{ds_id}",
        params=params,
        headers=headers,
    )
    assert resp.status_code == 204

    # Confirm gone
    resp = client.get(
        f"/api/enterprise/data-query/data-sources/{ds_id}",
        params=params,
        headers=headers,
    )
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# 2. Data source config is encrypted (read response does NOT include config_json)
# ---------------------------------------------------------------------------


def test_data_source_read_omits_config_json() -> None:
    engine = _test_engine()
    user = _seed_tenant_and_user(engine)
    client = _make_client(engine)
    headers = _auth(user)
    params = {"tenant_id": "tenant_a"}

    resp = client.post(
        "/api/enterprise/data-query/data-sources",
        params=params,
        json=_make_ds_payload(),
        headers=headers,
    )
    assert resp.status_code == 201
    data = resp.json()
    assert "config_json" not in data
    assert "password" not in str(data)


# ---------------------------------------------------------------------------
# 3. Query template CRUD
# ---------------------------------------------------------------------------


def test_query_template_crud() -> None:
    engine = _test_engine()
    user = _seed_tenant_and_user(engine)
    client = _make_client(engine)
    headers = _auth(user)
    params = {"tenant_id": "tenant_a"}

    # Create a data source first
    ds_resp = client.post(
        "/api/enterprise/data-query/data-sources",
        params=params,
        json=_make_ds_payload(),
        headers=headers,
    )
    ds_id = ds_resp.json()["id"]

    # Create template
    resp = client.post(
        "/api/enterprise/data-query/query-templates",
        params=params,
        json=_make_qt_payload(ds_id),
        headers=headers,
    )
    assert resp.status_code == 201
    qt = resp.json()
    assert qt["name"] == "User query"
    assert qt["data_source_id"] == ds_id
    assert qt["status"] == "draft"
    qt_id = qt["id"]

    # List
    resp = client.get(
        "/api/enterprise/data-query/query-templates",
        params=params,
        headers=headers,
    )
    assert resp.status_code == 200
    items = resp.json()
    assert any(item["id"] == qt_id for item in items)

    # List filtered by data_source_id
    resp = client.get(
        "/api/enterprise/data-query/query-templates",
        params={**params, "data_source_id": ds_id},
        headers=headers,
    )
    assert resp.status_code == 200
    assert any(item["id"] == qt_id for item in resp.json())

    # Detail
    resp = client.get(
        f"/api/enterprise/data-query/query-templates/{qt_id}",
        params=params,
        headers=headers,
    )
    assert resp.status_code == 200
    assert resp.json()["query_content"]

    # Update
    resp = client.put(
        f"/api/enterprise/data-query/query-templates/{qt_id}",
        params=params,
        json={"name": "Renamed query", "status": "active"},
        headers=headers,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["name"] == "Renamed query"
    assert body["status"] == "active"

    # Delete
    resp = client.delete(
        f"/api/enterprise/data-query/query-templates/{qt_id}",
        params=params,
        headers=headers,
    )
    assert resp.status_code == 204

    resp = client.get(
        f"/api/enterprise/data-query/query-templates/{qt_id}",
        params=params,
        headers=headers,
    )
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# 4. Query template with non-existent data source -> 400
# ---------------------------------------------------------------------------


def test_query_template_with_invalid_data_source_returns_400() -> None:
    engine = _test_engine()
    user = _seed_tenant_and_user(engine)
    client = _make_client(engine)
    headers = _auth(user)
    params = {"tenant_id": "tenant_a"}

    resp = client.post(
        "/api/enterprise/data-query/query-templates",
        params=params,
        json=_make_qt_payload("ds_nonexistent"),
        headers=headers,
    )
    assert resp.status_code == 400
    assert "Data source" in resp.json()["detail"]


# ---------------------------------------------------------------------------
# 5. Query template test run (mocked executor)
# ---------------------------------------------------------------------------


def test_query_template_test_run_mocked() -> None:
    engine = _test_engine()
    user = _seed_tenant_and_user(engine)
    client = _make_client(engine)
    headers = _auth(user)
    params = {"tenant_id": "tenant_a"}

    # Create DS + template
    ds_resp = client.post(
        "/api/enterprise/data-query/data-sources",
        params=params,
        json=_make_ds_payload(),
        headers=headers,
    )
    ds_id = ds_resp.json()["id"]
    qt_resp = client.post(
        "/api/enterprise/data-query/query-templates",
        params=params,
        json=_make_qt_payload(ds_id, status="draft"),
        headers=headers,
    )
    qt_id = qt_resp.json()["id"]

    mock_result = QueryResult(
        columns=["id", "name"],
        rows=[{"id": 1, "name": "Alice"}, {"id": 2, "name": "Bob"}],
        row_count=2,
    )

    class _MockConnector(BaseConnector):
        def test_connection(self) -> bool:
            return True

        def execute(self, query, params, *, timeout=30, max_rows=1000):
            return mock_result

        def close(self) -> None:
            pass

    with patch(
        "app.data_query.executor.get_connector", return_value=_MockConnector(None)
    ):
        resp = client.post(
            f"/api/enterprise/data-query/query-templates/{qt_id}/test",
            params=params,
            json={"params": {"status": "active"}},
            headers=headers,
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["template_id"] == qt_id
    assert body["columns"] == ["id", "name"]
    assert body["row_count"] == 2
    assert len(body["rows"]) == 2
    assert body["cached"] is False


# ---------------------------------------------------------------------------
# 6. Execute endpoint: active templates ok, draft templates fail
# ---------------------------------------------------------------------------


def test_execute_endpoint_active_vs_draft() -> None:
    engine = _test_engine()
    user = _seed_tenant_and_user(engine)
    client = _make_client(engine)
    headers = _auth(user)
    params = {"tenant_id": "tenant_a"}

    ds_resp = client.post(
        "/api/enterprise/data-query/data-sources",
        params=params,
        json=_make_ds_payload(),
        headers=headers,
    )
    ds_id = ds_resp.json()["id"]

    # Draft template
    draft_resp = client.post(
        "/api/enterprise/data-query/query-templates",
        params=params,
        json=_make_qt_payload(ds_id, status="draft"),
        headers=headers,
    )
    draft_id = draft_resp.json()["id"]

    # Active template
    active_resp = client.post(
        "/api/enterprise/data-query/query-templates",
        params=params,
        json=_make_qt_payload(ds_id, status="active", name="Active query"),
        headers=headers,
    )
    active_id = active_resp.json()["id"]

    mock_result = QueryResult(
        columns=["id"],
        rows=[{"id": 1}],
        row_count=1,
    )

    class _MockConnector(BaseConnector):
        def test_connection(self) -> bool:
            return True

        def execute(self, query, params, *, timeout=30, max_rows=1000):
            return mock_result

        def close(self) -> None:
            pass

    # Draft -> should fail
    with patch(
        "app.data_query.executor.get_connector", return_value=_MockConnector(None)
    ):
        resp = client.post(
            "/api/enterprise/data-query/execute",
            params=params,
            json={"template_id": draft_id, "params": {"status": "active"}},
            headers=headers,
        )
    assert resp.status_code == 400
    assert "not active" in resp.json()["detail"]

    # Active -> should succeed
    with patch(
        "app.data_query.executor.get_connector", return_value=_MockConnector(None)
    ):
        resp = client.post(
            "/api/enterprise/data-query/execute",
            params=params,
            json={"template_id": active_id, "params": {"status": "active"}},
            headers=headers,
        )
    assert resp.status_code == 200
    assert resp.json()["row_count"] == 1


# ---------------------------------------------------------------------------
# 7. Tenant isolation: tenant B cannot access tenant A's data sources
# ---------------------------------------------------------------------------


def test_tenant_isolation_data_sources() -> None:
    engine = _test_engine()
    user_a = _seed_tenant_and_user(
        engine, tenant_id="tenant_a", user_id="user_a", username="alice"
    )
    user_b = _seed_tenant_and_user(
        engine, tenant_id="tenant_b", user_id="user_b", username="bob"
    )
    client = _make_client(engine)

    # Tenant A creates a data source
    resp = client.post(
        "/api/enterprise/data-query/data-sources",
        params={"tenant_id": "tenant_a"},
        json=_make_ds_payload(name="A's DS"),
        headers=_auth(user_a),
    )
    assert resp.status_code == 201
    ds_id = resp.json()["id"]

    # Tenant B cannot see it
    resp = client.get(
        f"/api/enterprise/data-query/data-sources/{ds_id}",
        params={"tenant_id": "tenant_b"},
        headers=_auth(user_b),
    )
    assert resp.status_code == 404

    # Tenant B list does not include it
    resp = client.get(
        "/api/enterprise/data-query/data-sources",
        params={"tenant_id": "tenant_b"},
        headers=_auth(user_b),
    )
    assert resp.status_code == 200
    assert all(item["id"] != ds_id for item in resp.json())

    # Tenant B cannot update or delete
    resp = client.put(
        f"/api/enterprise/data-query/data-sources/{ds_id}",
        params={"tenant_id": "tenant_b"},
        json={"name": "Hacked"},
        headers=_auth(user_b),
    )
    assert resp.status_code == 404

    resp = client.delete(
        f"/api/enterprise/data-query/data-sources/{ds_id}",
        params={"tenant_id": "tenant_b"},
        headers=_auth(user_b),
    )
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# 8. Non-existent resources return 404
# ---------------------------------------------------------------------------


def test_nonexistent_resources_return_404() -> None:
    engine = _test_engine()
    user = _seed_tenant_and_user(engine)
    client = _make_client(engine)
    headers = _auth(user)
    params = {"tenant_id": "tenant_a"}

    # Data source
    resp = client.get(
        "/api/enterprise/data-query/data-sources/ds_nonexistent",
        params=params,
        headers=headers,
    )
    assert resp.status_code == 404

    resp = client.put(
        "/api/enterprise/data-query/data-sources/ds_nonexistent",
        params=params,
        json={"name": "nope"},
        headers=headers,
    )
    assert resp.status_code == 404

    resp = client.delete(
        "/api/enterprise/data-query/data-sources/ds_nonexistent",
        params=params,
        headers=headers,
    )
    assert resp.status_code == 404

    resp = client.post(
        "/api/enterprise/data-query/data-sources/ds_nonexistent/test",
        params=params,
        headers=headers,
    )
    assert resp.status_code == 404

    # Query template
    resp = client.get(
        "/api/enterprise/data-query/query-templates/qt_nonexistent",
        params=params,
        headers=headers,
    )
    assert resp.status_code == 404

    resp = client.put(
        "/api/enterprise/data-query/query-templates/qt_nonexistent",
        params=params,
        json={"name": "nope"},
        headers=headers,
    )
    assert resp.status_code == 404

    resp = client.delete(
        "/api/enterprise/data-query/query-templates/qt_nonexistent",
        params=params,
        headers=headers,
    )
    assert resp.status_code == 404

    # Execute with non-existent template id
    resp = client.post(
        "/api/enterprise/data-query/execute",
        params=params,
        json={"template_id": "qt_nonexistent", "params": {}},
        headers=headers,
    )
    assert resp.status_code == 400  # ValueError -> 400 from execute_query_by_id

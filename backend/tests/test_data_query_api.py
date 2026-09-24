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
from app.data_query.executor import _cache
from app.data_query.models import DataSource
from app.data_query.security import decrypt_config
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
    role: str = "admin",
) -> User:
    with Session(engine) as db:
        if db.get(Tenant, tenant_id) is None:
            db.add(Tenant(id=tenant_id, name=f"Tenant {tenant_id}"))
        user = User(
            id=user_id,
            tenant_id=tenant_id,
            username=username,
            display_name=username.title(),
            password_hash=hash_password("secret"),
            role=role,
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


# ---------------------------------------------------------------------------
# 9. Cross-tenant access is rejected with 403 (C-1)
# ---------------------------------------------------------------------------


def test_cross_tenant_access_forbidden() -> None:
    """A user of tenant B passing ?tenant_id=tenant_a must get 403 everywhere."""
    engine = _test_engine()
    user_a = _seed_tenant_and_user(
        engine, tenant_id="tenant_a", user_id="user_a", username="alice"
    )
    user_b = _seed_tenant_and_user(
        engine, tenant_id="tenant_b", user_id="user_b", username="bob"
    )
    client = _make_client(engine)

    # Tenant A creates a data source and a template
    resp = client.post(
        "/api/enterprise/data-query/data-sources",
        params={"tenant_id": "tenant_a"},
        json=_make_ds_payload(name="A's DS"),
        headers=_auth(user_a),
    )
    assert resp.status_code == 201
    ds_id = resp.json()["id"]

    resp = client.post(
        "/api/enterprise/data-query/query-templates",
        params={"tenant_id": "tenant_a"},
        json=_make_qt_payload(ds_id, status="active"),
        headers=_auth(user_a),
    )
    assert resp.status_code == 201
    qt_id = resp.json()["id"]

    headers_b = _auth(user_b)
    injected = {"tenant_id": "tenant_a"}  # tenant B user targeting tenant A

    # list / get / test on data sources -> 403
    resp = client.get(
        "/api/enterprise/data-query/data-sources", params=injected, headers=headers_b
    )
    assert resp.status_code == 403
    resp = client.get(
        f"/api/enterprise/data-query/data-sources/{ds_id}",
        params=injected,
        headers=headers_b,
    )
    assert resp.status_code == 403
    resp = client.post(
        f"/api/enterprise/data-query/data-sources/{ds_id}/test",
        params=injected,
        headers=headers_b,
    )
    assert resp.status_code == 403

    # update / delete -> 403
    resp = client.put(
        f"/api/enterprise/data-query/data-sources/{ds_id}",
        params=injected,
        json={"name": "Hacked"},
        headers=headers_b,
    )
    assert resp.status_code == 403
    resp = client.delete(
        f"/api/enterprise/data-query/data-sources/{ds_id}",
        params=injected,
        headers=headers_b,
    )
    assert resp.status_code == 403

    # create / update / delete / list on query templates -> 403
    resp = client.post(
        "/api/enterprise/data-query/query-templates",
        params=injected,
        json=_make_qt_payload(ds_id),
        headers=headers_b,
    )
    assert resp.status_code == 403
    resp = client.put(
        f"/api/enterprise/data-query/query-templates/{qt_id}",
        params=injected,
        json={"name": "Hacked"},
        headers=headers_b,
    )
    assert resp.status_code == 403
    resp = client.delete(
        f"/api/enterprise/data-query/query-templates/{qt_id}",
        params=injected,
        headers=headers_b,
    )
    assert resp.status_code == 403
    resp = client.get(
        "/api/enterprise/data-query/query-templates", params=injected, headers=headers_b
    )
    assert resp.status_code == 403

    # test-run and execute -> 403
    resp = client.post(
        f"/api/enterprise/data-query/query-templates/{qt_id}/test",
        params=injected,
        json={"params": {"status": "active"}},
        headers=headers_b,
    )
    assert resp.status_code == 403
    resp = client.post(
        "/api/enterprise/data-query/execute",
        params=injected,
        json={"template_id": qt_id, "params": {"status": "active"}},
        headers=headers_b,
    )
    assert resp.status_code == 403


def test_member_cannot_manage_but_can_read() -> None:
    """Members may list/read/execute; only admins manage resources (I-1)."""
    engine = _test_engine()
    admin = _seed_tenant_and_user(
        engine, tenant_id="tenant_a", user_id="user_admin", username="admin"
    )
    member = _seed_tenant_and_user(
        engine,
        tenant_id="tenant_a",
        user_id="user_member",
        username="member",
        role="member",
    )
    client = _make_client(engine)
    params = {"tenant_id": "tenant_a"}

    resp = client.post(
        "/api/enterprise/data-query/data-sources",
        params=params,
        json=_make_ds_payload(),
        headers=_auth(admin),
    )
    assert resp.status_code == 201
    ds_id = resp.json()["id"]

    resp = client.post(
        "/api/enterprise/data-query/query-templates",
        params=params,
        json=_make_qt_payload(ds_id),
        headers=_auth(admin),
    )
    assert resp.status_code == 201
    qt_id = resp.json()["id"]

    headers_m = _auth(member)

    # Read access is allowed for members
    resp = client.get(
        "/api/enterprise/data-query/data-sources", params=params, headers=headers_m
    )
    assert resp.status_code == 200
    resp = client.get(
        f"/api/enterprise/data-query/data-sources/{ds_id}",
        params=params,
        headers=headers_m,
    )
    assert resp.status_code == 200

    # Management is forbidden for members
    resp = client.post(
        "/api/enterprise/data-query/data-sources",
        params=params,
        json=_make_ds_payload(name="Member DS"),
        headers=headers_m,
    )
    assert resp.status_code == 403
    resp = client.put(
        f"/api/enterprise/data-query/data-sources/{ds_id}",
        params=params,
        json={"name": "Member edit"},
        headers=headers_m,
    )
    assert resp.status_code == 403
    resp = client.delete(
        f"/api/enterprise/data-query/data-sources/{ds_id}",
        params=params,
        headers=headers_m,
    )
    assert resp.status_code == 403
    resp = client.post(
        "/api/enterprise/data-query/query-templates",
        params=params,
        json=_make_qt_payload(ds_id),
        headers=headers_m,
    )
    assert resp.status_code == 403
    resp = client.put(
        f"/api/enterprise/data-query/query-templates/{qt_id}",
        params=params,
        json={"name": "Member edit"},
        headers=headers_m,
    )
    assert resp.status_code == 403
    resp = client.delete(
        f"/api/enterprise/data-query/query-templates/{qt_id}",
        params=params,
        headers=headers_m,
    )
    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# 10. Updating a data source preserves credentials (C-2)
# ---------------------------------------------------------------------------


def _get_ds_config(engine, ds_id: str) -> dict:
    with Session(engine) as db:
        ds = db.get(DataSource, ds_id)
        assert ds is not None
        return decrypt_config(ds.config_json or {}, ["password"])


def _create_ds(engine, client: TestClient, headers) -> str:
    resp = client.post(
        "/api/enterprise/data-query/data-sources",
        params={"tenant_id": "tenant_a"},
        json=_make_ds_payload(),
        headers=headers,
    )
    assert resp.status_code == 201
    return resp.json()["id"]


def test_update_data_source_rename_preserves_config() -> None:
    """Rename-only update keeps host and password (C-2)."""
    engine = _test_engine()
    user = _seed_tenant_and_user(engine)
    client = _make_client(engine)
    headers = _auth(user)
    ds_id = _create_ds(engine, client, headers)

    resp = client.put(
        f"/api/enterprise/data-query/data-sources/{ds_id}",
        params={"tenant_id": "tenant_a"},
        json={"name": "Renamed"},
        headers=headers,
    )
    assert resp.status_code == 200

    cfg = _get_ds_config(engine, ds_id)
    assert cfg["host"] == "localhost"
    assert cfg["password"] == "secret123"


def test_update_data_source_partial_config_merges() -> None:
    """config_json without password keeps the stored password (C-2)."""
    engine = _test_engine()
    user = _seed_tenant_and_user(engine)
    client = _make_client(engine)
    headers = _auth(user)
    ds_id = _create_ds(engine, client, headers)

    resp = client.put(
        f"/api/enterprise/data-query/data-sources/{ds_id}",
        params={"tenant_id": "tenant_a"},
        json={"config_json": {"host": "newhost", "port": 3307}},
        headers=headers,
    )
    assert resp.status_code == 200

    cfg = _get_ds_config(engine, ds_id)
    assert cfg["host"] == "newhost"
    assert cfg["port"] == 3307
    assert cfg["password"] == "secret123"
    assert cfg["database"] == "testdb"


def test_update_data_source_empty_sensitive_value_keeps_old() -> None:
    """An empty password string means 'unchanged', never 'clear' (C-2)."""
    engine = _test_engine()
    user = _seed_tenant_and_user(engine)
    client = _make_client(engine)
    headers = _auth(user)
    ds_id = _create_ds(engine, client, headers)

    resp = client.put(
        f"/api/enterprise/data-query/data-sources/{ds_id}",
        params={"tenant_id": "tenant_a"},
        json={"config_json": {"password": ""}},
        headers=headers,
    )
    assert resp.status_code == 200

    cfg = _get_ds_config(engine, ds_id)
    assert cfg["password"] == "secret123"


def test_update_data_source_new_password_updates() -> None:
    """A non-empty new password replaces the old one (C-2)."""
    engine = _test_engine()
    user = _seed_tenant_and_user(engine)
    client = _make_client(engine)
    headers = _auth(user)
    ds_id = _create_ds(engine, client, headers)

    resp = client.put(
        f"/api/enterprise/data-query/data-sources/{ds_id}",
        params={"tenant_id": "tenant_a"},
        json={"config_json": {"password": "brand-new-pass"}},
        headers=headers,
    )
    assert resp.status_code == 200

    cfg = _get_ds_config(engine, ds_id)
    assert cfg["password"] == "brand-new-pass"
    assert cfg["host"] == "localhost"


# ---------------------------------------------------------------------------
# 11. Updating a query template invalidates cached results (I-3)
# ---------------------------------------------------------------------------


def test_template_update_invalidates_cache() -> None:
    """After changing a template's SQL, execution returns fresh results."""
    engine = _test_engine()
    user = _seed_tenant_and_user(engine)
    client = _make_client(engine)
    headers = _auth(user)
    params = {"tenant_id": "tenant_a"}
    _cache.clear()

    ds_id = _create_ds(engine, client, headers)
    resp = client.post(
        "/api/enterprise/data-query/query-templates",
        params=params,
        json=_make_qt_payload(ds_id, status="draft", cache_ttl=300),
        headers=headers,
    )
    assert resp.status_code == 201
    qt_id = resp.json()["id"]

    mock_result_old = QueryResult(columns=["v"], rows=[{"v": "old"}], row_count=1)
    mock_result_new = QueryResult(columns=["v"], rows=[{"v": "new"}], row_count=1)

    class _MockConnector(BaseConnector):
        def __init__(self, result: QueryResult) -> None:
            self.result = result

        def test_connection(self) -> bool:
            return True

        def execute(self, query, params, *, timeout=30, max_rows=1000):
            return self.result

        def close(self) -> None:
            pass

    with patch(
        "app.data_query.executor.get_connector",
        return_value=_MockConnector(mock_result_old),
    ):
        resp = client.post(
            f"/api/enterprise/data-query/query-templates/{qt_id}/test",
            params=params,
            json={"params": {"status": "active"}},
            headers=headers,
        )
    assert resp.status_code == 200
    assert resp.json()["rows"] == [{"v": "old"}]

    # Cached second run returns the old result...
    with patch(
        "app.data_query.executor.get_connector",
        return_value=_MockConnector(mock_result_old),
    ):
        resp = client.post(
            f"/api/enterprise/data-query/query-templates/{qt_id}/test",
            params=params,
            json={"params": {"status": "active"}},
            headers=headers,
        )
    assert resp.status_code == 200
    assert resp.json()["cached"] is True

    # ...but after editing the SQL, the run is fresh again.
    resp = client.put(
        f"/api/enterprise/data-query/query-templates/{qt_id}",
        params=params,
        json={"query_content": "SELECT v FROM other_table WHERE status = :status"},
        headers=headers,
    )
    assert resp.status_code == 200

    with patch(
        "app.data_query.executor.get_connector",
        return_value=_MockConnector(mock_result_new),
    ):
        resp = client.post(
            f"/api/enterprise/data-query/query-templates/{qt_id}/test",
            params=params,
            json={"params": {"status": "active"}},
            headers=headers,
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["cached"] is False
    assert body["rows"] == [{"v": "new"}]


def test_harness_data_query_search_and_execute() -> None:
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

    qt_resp = client.post(
        "/api/enterprise/data-query/query-templates",
        params=params,
        json={**_make_qt_payload(ds_id), "status": "active", "example_questions_json": ["查询用户列表"]},
        headers=headers,
    )
    qt_id = qt_resp.json()["id"]

    with Session(engine) as sess:
        from app.core.capability_manifest import CapabilityManifestBuilder
        from app.core.harness_capability_invoker import HarnessCapabilityInvoker
        from app.db.models import ChatSession, new_id

        cs = ChatSession(id=new_id("session"), tenant_id="tenant_a", user_id=user.id, title="Test Session")
        sess.add(cs)
        sess.commit()
        sess.refresh(cs)

        manifest = CapabilityManifestBuilder(sess).build("tenant_a", None, None, None)
        avail_names = {c.name for c in manifest.available}
        assert "data_query_search" in avail_names
        assert "data_query_execute" in avail_names

        invoker = HarnessCapabilityInvoker(
            db=sess,
            tenant_id="tenant_a",
            session=cs,
            task_frame_id="tf_test",
            run_id="run_test",
            model_config=None,
            manifest=manifest,
            active_skill=None,
            active_step_id=None,
            agent_id=None,
        )

        search_res = invoker._invoke_internal("data_query_search", {"query": "用户列表"})
        assert search_res["success"] is True
        assert any(m["template_id"] == qt_id for m in search_res["data"]["matches"])

        from app.data_query.models import QueryExecuteResult

        mock_result = QueryExecuteResult(
            template_id=qt_id,
            columns=["id", "username"],
            rows=[{"id": 1, "username": "alice"}],
            total_rows=1,
            execution_time_ms=12.5,
            cached=False,
        )
        from unittest.mock import MagicMock, patch

        with patch("app.data_query.executor.get_connector") as mock_conn:
            mock_inst = MagicMock()
            mock_inst.execute.return_value = mock_result
            mock_conn.return_value = mock_inst
            exec_res = invoker._invoke_internal("data_query_execute", {"template_id": qt_id})
            assert exec_res["success"] is True
            assert exec_res["data"]["row_count"] == 1
            assert "alice" in exec_res["data"]["table_markdown"]


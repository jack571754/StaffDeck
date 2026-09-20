"""Data query center REST API routes.

Endpoints for data source and query template CRUD, connection testing,
template test runs, and execution of active templates.

All endpoints require authentication and are scoped to a tenant.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlmodel import Session, select

from app.data_query import service
from app.data_query.models import (
    DataSourceCreate,
    DataSourceRead,
    DataSourceUpdate,
    QueryExecuteRequest,
    QueryExecuteResult,
    QueryTemplate,
    QueryTemplateCreate,
    QueryTemplateRead,
    QueryTemplateUpdate,
)
from app.db import get_session
from app.db.models import User
from app.security.auth import ensure_current_user_tenant, get_current_user
from app.security.permissions import ensure_tenant_admin
from app.security.tenant import ensure_tenant

router = APIRouter(
    prefix="/api/enterprise/data-query",
    tags=["enterprise:data-query"],
    dependencies=[Depends(get_current_user)],
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _resolve_tenant(
    db: Session,
    tenant_id: str | None,
    current_user: User,
) -> str:
    """Resolve the tenant_id and verify the current user belongs to it.

    A logged-in member may only ever operate on their own tenant: passing
    another tenant's id is rejected with 403 before any tenant lookup, so
    cross-tenant data sources / templates / executions are unreachable.
    """
    tid = (tenant_id or "").strip() or str(current_user.tenant_id)
    ensure_current_user_tenant(tid, current_user)
    ensure_tenant(db, tid)
    return tid


def _ensure_tenant_admin(tid: str, current_user: User) -> None:
    """Require an administrator of *tid* for management endpoints."""
    ensure_tenant_admin(tid, current_user)


def _data_source_read(ds) -> DataSourceRead:
    """Convert a DataSource row to the public read schema."""
    return DataSourceRead(
        id=ds.id,
        tenant_id=ds.tenant_id,
        name=ds.name,
        description=ds.description,
        type=ds.type,
        read_only=ds.read_only,
        status=ds.status,
        last_test_at=ds.last_test_at,
        created_at=ds.created_at,
        updated_at=ds.updated_at,
    )


def _get_data_source_or_404(
    db: Session,
    ds_id: str,
    tenant_id: str,
):
    ds = service.get_data_source(db, ds_id, tenant_id)
    if not ds:
        raise HTTPException(status_code=404, detail="Data source not found")
    return ds


def _get_query_template_or_404(
    db: Session,
    qt_id: str,
    tenant_id: str,
):
    qt = service.get_query_template(db, qt_id, tenant_id)
    if not qt:
        raise HTTPException(status_code=404, detail="Query template not found")
    return qt


# ===================================================================
# Data source endpoints
# ===================================================================


@router.get("/data-sources", response_model=list[DataSourceRead])
def list_data_sources(
    tenant_id: str = Query(default=""),
    limit: int = Query(default=100, ge=1, le=500),
    db: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> list[DataSourceRead]:
    """List data sources for a tenant."""
    tid = _resolve_tenant(db, tenant_id, current_user)
    rows = service.list_data_sources(db, tid, limit=limit)
    return [_data_source_read(ds) for ds in rows]


@router.get("/data-sources/{ds_id}", response_model=DataSourceRead)
def get_data_source(
    ds_id: str,
    tenant_id: str = Query(default=""),
    db: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> DataSourceRead:
    """Get a single data source by id."""
    tid = _resolve_tenant(db, tenant_id, current_user)
    ds = _get_data_source_or_404(db, ds_id, tid)
    return _data_source_read(ds)


@router.post("/data-sources", response_model=DataSourceRead, status_code=201)
def create_data_source(
    request: DataSourceCreate,
    tenant_id: str = Query(default=""),
    db: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> DataSourceRead:
    """Create a new data source."""
    tid = _resolve_tenant(db, tenant_id, current_user)
    _ensure_tenant_admin(tid, current_user)
    try:
        ds = service.create_data_source(db, tid, request)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _data_source_read(ds)


@router.put("/data-sources/{ds_id}", response_model=DataSourceRead)
def update_data_source(
    ds_id: str,
    request: DataSourceUpdate,
    tenant_id: str = Query(default=""),
    db: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> DataSourceRead:
    """Update an existing data source."""
    tid = _resolve_tenant(db, tenant_id, current_user)
    _ensure_tenant_admin(tid, current_user)
    ds = _get_data_source_or_404(db, ds_id, tid)
    try:
        ds = service.update_data_source(db, ds, request)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _data_source_read(ds)


@router.delete("/data-sources/{ds_id}", status_code=204)
def delete_data_source(
    ds_id: str,
    tenant_id: str = Query(default=""),
    db: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> None:
    """Delete a data source."""
    tid = _resolve_tenant(db, tenant_id, current_user)
    _ensure_tenant_admin(tid, current_user)
    ds = _get_data_source_or_404(db, ds_id, tid)
    service.delete_data_source(db, ds)


@router.post("/data-sources/{ds_id}/test")
def test_data_source(
    ds_id: str,
    tenant_id: str = Query(default=""),
    db: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> dict[str, Any]:
    """Test the connection to a data source."""
    tid = _resolve_tenant(db, tenant_id, current_user)
    ds = _get_data_source_or_404(db, ds_id, tid)
    success, message = service.test_data_source_connection(db, ds)
    return {"success": success, "message": message}


# ===================================================================
# Query template endpoints
# ===================================================================


@router.get("/query-templates", response_model=list[QueryTemplateRead])
def list_query_templates(
    tenant_id: str = Query(default=""),
    data_source_id: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    db: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> list[QueryTemplateRead]:
    """List query templates for a tenant."""
    tid = _resolve_tenant(db, tenant_id, current_user)
    rows = service.list_query_templates(
        db, tid, data_source_id=data_source_id, limit=limit
    )
    return [QueryTemplateRead.model_validate(row) for row in rows]


@router.get("/query-templates/{qt_id}", response_model=QueryTemplateRead)
def get_query_template(
    qt_id: str,
    tenant_id: str = Query(default=""),
    db: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> QueryTemplateRead:
    """Get a single query template by id."""
    tid = _resolve_tenant(db, tenant_id, current_user)
    qt = _get_query_template_or_404(db, qt_id, tid)
    return QueryTemplateRead.model_validate(qt)


@router.post("/query-templates", response_model=QueryTemplateRead, status_code=201)
def create_query_template(
    request: QueryTemplateCreate,
    tenant_id: str = Query(default=""),
    db: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> QueryTemplateRead:
    """Create a new query template."""
    tid = _resolve_tenant(db, tenant_id, current_user)
    _ensure_tenant_admin(tid, current_user)
    try:
        qt = service.create_query_template(db, tid, request)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return QueryTemplateRead.model_validate(qt)


@router.put("/query-templates/{qt_id}", response_model=QueryTemplateRead)
def update_query_template(
    qt_id: str,
    request: QueryTemplateUpdate,
    tenant_id: str = Query(default=""),
    db: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> QueryTemplateRead:
    """Update an existing query template."""
    tid = _resolve_tenant(db, tenant_id, current_user)
    _ensure_tenant_admin(tid, current_user)
    qt = _get_query_template_or_404(db, qt_id, tid)
    try:
        qt = service.update_query_template(db, qt, request)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return QueryTemplateRead.model_validate(qt)


@router.delete("/query-templates/{qt_id}", status_code=204)
def delete_query_template(
    qt_id: str,
    tenant_id: str = Query(default=""),
    db: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> None:
    """Delete a query template."""
    tid = _resolve_tenant(db, tenant_id, current_user)
    _ensure_tenant_admin(tid, current_user)
    qt = _get_query_template_or_404(db, qt_id, tid)
    service.delete_query_template(db, qt)


@router.post("/query-templates/{qt_id}/test", response_model=QueryExecuteResult)
def test_query_template(
    qt_id: str,
    request: dict[str, Any] | None = None,
    tenant_id: str = Query(default=""),
    db: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> QueryExecuteResult:
    """Test-run a query template (works for any status, including draft)."""
    tid = _resolve_tenant(db, tenant_id, current_user)
    qt = _get_query_template_or_404(db, qt_id, tid)
    params = (request or {}).get("params", {}) if request else {}
    try:
        return service.test_query_template(db, qt, params)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


# ===================================================================
# Execute endpoint
# ===================================================================


@router.post("/execute", response_model=QueryExecuteResult)
def execute_query(
    request: QueryExecuteRequest,
    tenant_id: str = Query(default=""),
    db: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> QueryExecuteResult:
    """Execute an active query template by id."""
    tid = _resolve_tenant(db, tenant_id, current_user)
    try:
        return service.execute_query_by_id(db, request.template_id, tid, request.params)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


# ===================================================================
# Agent data-source authorization
# ===================================================================


@router.get("/agents/{agent_id}/templates")
def list_agent_query_templates(
    agent_id: str,
    tenant_id: str = Query(default=""),
    db: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> list[dict[str, Any]]:
    """List active query templates usable by an agent.

    A template is visible to an agent when its data source is bound to the
    agent (AgentResourceBinding resource_type="data_source", active) and both
    the data source and the template are active.
    """
    from app.api.agents import _ensure_can_access_agent
    from app.db.models import AgentProfile

    tid = _resolve_tenant(db, tenant_id, current_user)
    agent = db.get(AgentProfile, agent_id)
    if not agent or agent.tenant_id != tid:
        raise HTTPException(status_code=404, detail="Agent not found")
    _ensure_can_access_agent(agent, current_user)

    from app.data_query.authorization import authorized_data_source_ids

    usable_source_ids = authorized_data_source_ids(db, tid, agent_id)
    if not usable_source_ids:
        return []

    rows = db.exec(
        select(QueryTemplate).where(
            QueryTemplate.tenant_id == tid,
            QueryTemplate.status == "active",
            QueryTemplate.data_source_id.in_(usable_source_ids),
        )
    ).all()
    return [
        {
            "id": qt.id,
            "name": qt.name,
            "description": qt.description,
            "data_source_id": qt.data_source_id,
            "query_type": qt.query_type,
            "params_json": qt.params_json,
        }
        for qt in sorted(rows, key=lambda row: row.name)
    ]

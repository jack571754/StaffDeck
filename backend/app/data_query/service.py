"""Data query center service layer.

Business logic for data sources and query templates: CRUD, encryption of
sensitive config fields, connection testing, template execution, and
tenant-scoped access checks.
"""

from __future__ import annotations

from typing import Any

from sqlmodel import Session, select

from app.data_query.executor import QueryExecutor, invalidate_template_cache
from app.data_query.intent_router import clear_intent_template_cache
from app.data_query.models import (
    DataSource,
    DataSourceCreate,
    DataSourceUpdate,
    QueryExecuteResult,
    QueryTemplate,
    QueryTemplateCreate,
    QueryTemplateUpdate,
)
from app.data_query.security import decrypt_config, encrypt_config
from app.db.utils import utc_now

# ---------------------------------------------------------------------------
# Sensitive field definitions per data source type
# ---------------------------------------------------------------------------

_SENSITIVE_KEYS_BY_TYPE: dict[str, list[str]] = {
    "mysql": ["password"],
    "postgres": ["password"],
    "http_api": ["api_key", "token", "secret"],
}
_DEFAULT_SENSITIVE_KEYS = ["password", "api_key", "token", "secret"]


def _sensitive_keys_for(ds_type: str) -> list[str]:
    """Return the list of sensitive config keys for a data source type."""
    return _SENSITIVE_KEYS_BY_TYPE.get(ds_type, _DEFAULT_SENSITIVE_KEYS)


# ---------------------------------------------------------------------------
# Data source CRUD
# ---------------------------------------------------------------------------


def create_data_source(
    db: Session,
    tenant_id: str,
    request: DataSourceCreate,
) -> DataSource:
    """Create a new data source with encrypted sensitive config fields.

    Args:
        db: Active database session.
        tenant_id: Owning tenant identifier.
        request: Creation payload.

    Returns:
        The newly persisted ``DataSource`` row.
    """
    sensitive_keys = _sensitive_keys_for(request.type)
    encrypted_config = encrypt_config(request.config_json or {}, sensitive_keys)
    now = utc_now()
    ds = DataSource(
        tenant_id=tenant_id,
        name=request.name,
        description=request.description or "",
        type=request.type,
        config_json=encrypted_config,
        read_only=request.read_only,
        status=request.status,
        created_at=now,
        updated_at=now,
    )
    db.add(ds)
    db.commit()
    db.refresh(ds)
    return ds


def update_data_source(
    db: Session,
    ds: DataSource,
    request: DataSourceUpdate,
) -> DataSource:
    """Update an existing data source, re-encrypting config when changed.

    Args:
        db: Active database session.
        ds: The data source row to update (must already be loaded).
        request: Update payload (partial fields accepted).

    Returns:
        The updated ``DataSource`` row.
    """
    changed = False

    if request.name is not None and request.name != ds.name:
        ds.name = request.name
        changed = True
    if request.description is not None and request.description != ds.description:
        ds.description = request.description
        changed = True
    if request.type is not None and request.type != ds.type:
        ds.type = request.type
        changed = True
    if request.read_only is not None and request.read_only != ds.read_only:
        ds.read_only = request.read_only
        changed = True
    if request.status is not None and request.status != ds.status:
        ds.status = request.status
        changed = True

    if request.config_json is not None:
        sensitive_keys = _sensitive_keys_for(ds.type)
        # Field-level merge instead of whole-dict replacement: only keys
        # explicitly present in the request's config_json override the
        # stored values. Edit forms leave the password field blank (thus
        # omitting or emptying the key) when the user only renames the
        # data source, and that must never silently wipe credentials.
        # (``config_json`` is ``dict | None``; an explicit JSON null is
        # treated the same as an absent field, i.e. no config change.)
        merged = decrypt_config(ds.config_json or {}, sensitive_keys)
        for key, value in request.config_json.items():
            if key in sensitive_keys and value == "":
                # An empty sensitive value means "unchanged", never "clear".
                continue
            merged[key] = value
        ds.config_json = encrypt_config(merged, sensitive_keys)
        changed = True

    if changed:
        ds.updated_at = utc_now()
        db.add(ds)
        db.commit()
        db.refresh(ds)

    return ds


def delete_data_source(db: Session, ds: DataSource) -> None:
    """Delete a data source row."""
    db.delete(ds)
    db.commit()


def get_data_source(
    db: Session,
    ds_id: str,
    tenant_id: str,
) -> DataSource | None:
    """Return a data source by id if it belongs to *tenant_id*, else None."""
    row = db.exec(
        select(DataSource).where(
            DataSource.id == ds_id,
            DataSource.tenant_id == tenant_id,
        )
    ).first()
    return row


def list_data_sources(
    db: Session,
    tenant_id: str,
    limit: int = 100,
) -> list[DataSource]:
    """Return data sources for a tenant, most recently updated first."""
    rows = db.exec(
        select(DataSource)
        .where(DataSource.tenant_id == tenant_id)
        .order_by(DataSource.updated_at.desc())
        .limit(limit)
    ).all()
    return list(rows)


def test_data_source_connection(
    db: Session,
    ds: DataSource,
) -> tuple[bool, str]:
    """Test connection to a data source and update its status fields.

    The config is decrypted before being handed to the connector.  The
    ``last_test_at`` and ``status`` fields on *ds* are updated and
    committed.

    Args:
        db: Active database session.
        ds: The data source to test (mutated in place).

    Returns:
        A ``(success, message)`` tuple.
    """
    # Import locally to avoid circular imports when tests mock connectors
    from app.data_query.connectors import get_connector

    # Decrypt config for the connector
    sensitive_keys = _sensitive_keys_for(ds.type)
    decrypted_config = decrypt_config(ds.config_json or {}, sensitive_keys)

    # Build a lightweight copy with decrypted config for the connector
    class _DecryptedDS:
        def __init__(self, src: DataSource, config: dict[str, Any]) -> None:
            self.type = src.type
            self.config_json = config
            self.read_only = src.read_only

    decrypted_ds = _DecryptedDS(ds, decrypted_config)
    ds.last_test_at = utc_now()

    try:
        connector = get_connector(decrypted_ds)
        ok = connector.test_connection()
        connector.close()
        if ok:
            ds.status = "active"
            db.add(ds)
            db.commit()
            return True, "Connection successful"
        ds.status = "error"
        db.add(ds)
        db.commit()
        return False, "Connection test failed"
    except Exception as exc:  # noqa: BLE001 - surface any connector error
        ds.status = "error"
        db.add(ds)
        db.commit()
        return False, str(exc)


# ---------------------------------------------------------------------------
# Query template CRUD
# ---------------------------------------------------------------------------


def create_query_template(
    db: Session,
    tenant_id: str,
    request: QueryTemplateCreate,
) -> QueryTemplate:
    """Create a new query template.

    Verifies that the referenced data source exists and belongs to the
    same tenant.

    Args:
        db: Active database session.
        tenant_id: Owning tenant identifier.
        request: Creation payload.

    Returns:
        The newly persisted ``QueryTemplate`` row.

    Raises:
        ValueError: If ``data_source_id`` does not exist for the tenant.
    """
    ds = get_data_source(db, request.data_source_id, tenant_id)
    if not ds:
        raise ValueError("Data source not found for this tenant")

    now = utc_now()
    qt = QueryTemplate(
        tenant_id=tenant_id,
        name=request.name,
        description=request.description or "",
        data_source_id=request.data_source_id,
        query_type=request.query_type,
        query_content=request.query_content,
        params_json=list(request.params_json or []),
        output_config_json=dict(request.output_config_json or {}),
        cache_ttl=request.cache_ttl,
        timeout_seconds=request.timeout_seconds,
        max_rows=request.max_rows,
        status=request.status,
        created_at=now,
        updated_at=now,
    )
    db.add(qt)
    db.commit()
    db.refresh(qt)
    clear_intent_template_cache(qt.tenant_id)
    return qt


def update_query_template(
    db: Session,
    qt: QueryTemplate,
    request: QueryTemplateUpdate,
) -> QueryTemplate:
    """Update an existing query template.

    If ``data_source_id`` changes, the new data source must exist and
    belong to the same tenant as the template.

    Args:
        db: Active database session.
        qt: The template row to update.
        request: Update payload (partial fields accepted).

    Returns:
        The updated ``QueryTemplate`` row.

    Raises:
        ValueError: If a new ``data_source_id`` is not found for the tenant.
    """
    changed = False

    if request.name is not None and request.name != qt.name:
        qt.name = request.name
        changed = True
    if request.description is not None and request.description != qt.description:
        qt.description = request.description
        changed = True
    if request.query_type is not None and request.query_type != qt.query_type:
        qt.query_type = request.query_type
        changed = True
    if request.query_content is not None and request.query_content != qt.query_content:
        qt.query_content = request.query_content
        changed = True
    if request.params_json is not None:
        qt.params_json = list(request.params_json)
        changed = True
    if request.output_config_json is not None:
        qt.output_config_json = dict(request.output_config_json)
        changed = True
    if request.cache_ttl is not None and request.cache_ttl != qt.cache_ttl:
        qt.cache_ttl = request.cache_ttl
        changed = True
    if request.timeout_seconds is not None and request.timeout_seconds != qt.timeout_seconds:
        qt.timeout_seconds = request.timeout_seconds
        changed = True
    if request.max_rows is not None and request.max_rows != qt.max_rows:
        qt.max_rows = request.max_rows
        changed = True
    if request.status is not None and request.status != qt.status:
        qt.status = request.status
        changed = True

    if request.data_source_id is not None and request.data_source_id != qt.data_source_id:
        ds = get_data_source(db, request.data_source_id, qt.tenant_id)
        if not ds:
            raise ValueError("Data source not found for this tenant")
        qt.data_source_id = request.data_source_id
        changed = True

    if changed:
        qt.updated_at = utc_now()
        db.add(qt)
        db.commit()
        db.refresh(qt)
        # Cached results of the previous SQL/params definition are stale now.
        invalidate_template_cache(qt.id)
        clear_intent_template_cache(qt.tenant_id)

    return qt


def delete_query_template(db: Session, qt: QueryTemplate) -> None:
    """Delete a query template row."""
    tenant_id = qt.tenant_id
    db.delete(qt)
    db.commit()
    clear_intent_template_cache(tenant_id)


def get_query_template(
    db: Session,
    qt_id: str,
    tenant_id: str,
) -> QueryTemplate | None:
    """Return a query template by id if it belongs to *tenant_id*, else None."""
    row = db.exec(
        select(QueryTemplate).where(
            QueryTemplate.id == qt_id,
            QueryTemplate.tenant_id == tenant_id,
        )
    ).first()
    return row


def list_query_templates(
    db: Session,
    tenant_id: str,
    data_source_id: str | None = None,
    limit: int = 100,
) -> list[QueryTemplate]:
    """Return query templates for a tenant, most recently updated first.

    Optionally filters by ``data_source_id``.
    """
    stmt = select(QueryTemplate).where(QueryTemplate.tenant_id == tenant_id)
    if data_source_id:
        stmt = stmt.where(QueryTemplate.data_source_id == data_source_id)
    stmt = stmt.order_by(QueryTemplate.updated_at.desc()).limit(limit)
    rows = db.exec(stmt).all()
    return list(rows)


# ---------------------------------------------------------------------------
# Query execution
# ---------------------------------------------------------------------------


def test_query_template(
    db: Session,
    qt: QueryTemplate,
    params: dict[str, Any],
) -> QueryExecuteResult:
    """Execute a template for testing purposes — skips status check.

    Draft templates can be tested; this does not require ``status == "active"``.

    Args:
        db: Active database session.
        qt: The template to execute.
        params: Parameter values to pass in.

    Returns:
        A ``QueryExecuteResult`` with columns, rows, and timing info.
    """
    executor = QueryExecutor(db_session=db)
    return executor.execute(qt, params)


def execute_query_by_id(
    db: Session,
    template_id: str,
    tenant_id: str,
    params: dict[str, Any],
) -> QueryExecuteResult:
    """Execute an active query template by id.

    Args:
        db: Active database session.
        template_id: Id of the template to execute.
        tenant_id: Tenant context (enforces ownership).
        params: Parameter values to pass in.

    Returns:
        A ``QueryExecuteResult`` with columns, rows, and timing info.

    Raises:
        ValueError: If the template does not exist, belongs to another
            tenant, or is not active.
    """
    qt = get_query_template(db, template_id, tenant_id)
    if not qt:
        raise ValueError("Query template not found")
    if qt.status != "active":
        raise ValueError("Query template is not active")
    return test_query_template(db, qt, params)

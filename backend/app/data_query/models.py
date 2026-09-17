"""Data query center SQLModel definitions.

DataSource and QueryTemplate models with full CRUD schemas.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, Column, UniqueConstraint
from sqlmodel import Field, SQLModel

from app.db.utils import new_id, utc_now

# ---------------------------------------------------------------------------
# DataSource
# ---------------------------------------------------------------------------


class DataSource(SQLModel, table=True):
    """A configured data source connection (database, API, etc.)."""

    __tablename__ = "data_sources"
    __table_args__ = (
        UniqueConstraint("tenant_id", "name", name="uq_data_source_tenant_name"),
    )

    id: str = Field(default_factory=lambda: new_id("ds"), primary_key=True)
    tenant_id: str = Field(index=True)
    name: str
    description: str | None = None
    # e.g. "mysql", "postgres", "sqlite", "http_api", "clickhouse"
    source_type: str = Field(index=True)
    # Connection/configuration dict; sensitive fields are encrypted with ``enc:`` prefix
    config_json: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    status: str = Field(default="active", index=True)
    # Optional SQLModel compat: which agent profile / scope can use this source
    capability_scope: str = Field(default="general", index=True)
    metadata_json: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class DataSourceCreate(SQLModel):
    name: str
    description: str | None = None
    source_type: str
    config_json: dict[str, Any] = Field(default_factory=dict)
    status: str = "active"
    capability_scope: str = "general"
    metadata_json: dict[str, Any] = Field(default_factory=dict)


class DataSourceUpdate(SQLModel):
    name: str | None = None
    description: str | None = None
    source_type: str | None = None
    config_json: dict[str, Any] | None = None
    status: str | None = None
    capability_scope: str | None = None
    metadata_json: dict[str, Any] | None = None


class DataSourceRead(SQLModel):
    """Public read schema — intentionally omits ``config_json`` to avoid leaking secrets."""

    id: str
    tenant_id: str
    name: str
    description: str | None = None
    source_type: str
    status: str
    capability_scope: str
    metadata_json: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
    updated_at: datetime


# ---------------------------------------------------------------------------
# QueryTemplate
# ---------------------------------------------------------------------------


class QueryTemplate(SQLModel, table=True):
    """A reusable SQL/API query template with parameter placeholders."""

    __tablename__ = "query_templates"
    __table_args__ = (
        UniqueConstraint("tenant_id", "name", name="uq_query_template_tenant_name"),
    )

    id: str = Field(default_factory=lambda: new_id("qt"), primary_key=True)
    tenant_id: str = Field(index=True)
    data_source_id: str = Field(index=True)
    name: str
    description: str | None = None
    # The query body (SQL statement, API endpoint path, etc.)
    query_text: str
    # Parameter definitions: [{"name": "...", "type": "string", "default": ...}, ...]
    parameters_json: list[dict[str, Any]] = Field(default_factory=list, sa_column=Column(JSON))
    # Expected output column metadata
    output_schema_json: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    status: str = Field(default="active", index=True)
    capability_scope: str = Field(default="general", index=True)
    metadata_json: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class QueryTemplateCreate(SQLModel):
    data_source_id: str
    name: str
    description: str | None = None
    query_text: str
    parameters_json: list[dict[str, Any]] = Field(default_factory=list)
    output_schema_json: dict[str, Any] = Field(default_factory=dict)
    status: str = "active"
    capability_scope: str = "general"
    metadata_json: dict[str, Any] = Field(default_factory=dict)


class QueryTemplateUpdate(SQLModel):
    data_source_id: str | None = None
    name: str | None = None
    description: str | None = None
    query_text: str | None = None
    parameters_json: list[dict[str, Any]] | None = None
    output_schema_json: dict[str, Any] | None = None
    status: str | None = None
    capability_scope: str | None = None
    metadata_json: dict[str, Any] | None = None


class QueryTemplateRead(SQLModel):
    id: str
    tenant_id: str
    data_source_id: str
    name: str
    description: str | None = None
    query_text: str
    parameters_json: list[dict[str, Any]] = Field(default_factory=list)
    output_schema_json: dict[str, Any] = Field(default_factory=dict)
    status: str
    capability_scope: str
    metadata_json: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
    updated_at: datetime


# ---------------------------------------------------------------------------
# Execution request / result (non-table)
# ---------------------------------------------------------------------------


class QueryExecuteRequest(SQLModel):
    """Request payload for executing a query template with concrete parameters."""

    template_id: str
    parameters: dict[str, Any] = Field(default_factory=dict)
    # Optional execution options (timeout, pagination, etc.)
    options: dict[str, Any] = Field(default_factory=dict)


class QueryExecuteResult(SQLModel):
    """Result payload from executing a query template."""

    template_id: str
    # Result rows as list of dicts
    rows: list[dict[str, Any]] = Field(default_factory=list)
    # Column metadata
    columns: list[dict[str, Any]] = Field(default_factory=list)
    row_count: int = 0
    # Execution stats
    execution_time_ms: float = 0.0
    error: str | None = None

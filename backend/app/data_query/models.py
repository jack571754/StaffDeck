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
    name: str = Field(max_length=100, index=True)
    description: str = Field(default="", max_length=500)
    # e.g. "mysql", "postgres", "sqlite", "http_api", "clickhouse"
    type: str = Field(max_length=20, index=True)
    # Connection/configuration dict; sensitive fields are encrypted with ``enc:`` prefix
    config_json: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    read_only: bool = Field(default=True)
    status: str = Field(default="active", max_length=20, index=True)
    allowed_tables_json: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    schema_cache_json: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    schema_refreshed_at: datetime | None = Field(default=None)
    last_test_at: datetime | None = Field(default=None)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class DataSourceCreate(SQLModel):
    name: str
    description: str = ""
    type: str
    config_json: dict[str, Any] = Field(default_factory=dict)
    read_only: bool = True
    status: str = "active"
    allowed_tables_json: list[str] = Field(default_factory=list)


class DataSourceUpdate(SQLModel):
    name: str | None = None
    description: str | None = None
    type: str | None = None
    config_json: dict[str, Any] | None = None
    read_only: bool | None = None
    status: str | None = None
    allowed_tables_json: list[str] | None = None


class DataSourceRead(SQLModel):
    """Public read schema — intentionally omits ``config_json`` to avoid leaking secrets."""

    id: str
    tenant_id: str
    name: str
    description: str = ""
    type: str
    read_only: bool
    status: str
    allowed_tables_json: list[str] = Field(default_factory=list)
    schema_cache_json: dict[str, Any] = Field(default_factory=dict)
    schema_refreshed_at: datetime | None = None
    last_test_at: datetime | None = None
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
    name: str = Field(max_length=100, index=True)
    description: str = Field(default="", max_length=1000)
    data_source_id: str = Field(index=True)
    # "sql" or "http"
    query_type: str = Field(default="sql", max_length=20)
    # The query body (SQL statement, API endpoint path, etc.)
    query_content: str = Field(default="")
    # Parameter definitions: [{"name": "...", "type": "string", "default": ...}, ...]
    params_json: list[dict[str, Any]] = Field(default_factory=list, sa_column=Column(JSON))
    # Output column configuration
    output_config_json: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    # Cache TTL in seconds; 0 means no caching
    cache_ttl: int = Field(default=300)
    # Execution timeout in seconds
    timeout_seconds: int = Field(default=30)
    # Maximum number of rows to return
    max_rows: int = Field(default=1000)
    status: str = Field(default="draft", max_length=20, index=True)
    tool_id: str | None = Field(default=None, index=True)
    origin_nl: str | None = Field(default=None)
    business_notes: str = Field(default="")
    dimensions_json: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    metrics_json: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    example_questions_json: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    evolution_version: int = Field(default=1)
    generated_by: str = Field(default="manual")
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class QueryTemplateCreate(SQLModel):
    name: str
    description: str = ""
    data_source_id: str
    query_type: str = "sql"
    query_content: str = ""
    params_json: list[dict[str, Any]] = Field(default_factory=list)
    output_config_json: dict[str, Any] = Field(default_factory=dict)
    cache_ttl: int = 300
    timeout_seconds: int = 30
    max_rows: int = 1000
    status: str = "draft"
    tool_id: str | None = None
    origin_nl: str | None = None
    business_notes: str = ""
    dimensions_json: list[str] = Field(default_factory=list)
    metrics_json: list[str] = Field(default_factory=list)
    example_questions_json: list[str] = Field(default_factory=list)
    evolution_version: int = 1
    generated_by: str = "manual"


class QueryTemplateUpdate(SQLModel):
    name: str | None = None
    description: str | None = None
    data_source_id: str | None = None
    query_type: str | None = None
    query_content: str | None = None
    params_json: list[dict[str, Any]] | None = None
    output_config_json: dict[str, Any] | None = None
    cache_ttl: int | None = None
    timeout_seconds: int | None = None
    max_rows: int | None = None
    status: str | None = None
    tool_id: str | None = None
    origin_nl: str | None = None
    business_notes: str | None = None
    dimensions_json: list[str] | None = None
    metrics_json: list[str] | None = None
    example_questions_json: list[str] | None = None
    evolution_version: int | None = None
    generated_by: str | None = None


class QueryTemplateRead(SQLModel):
    id: str
    tenant_id: str
    name: str
    description: str = ""
    data_source_id: str
    query_type: str
    query_content: str
    params_json: list[dict[str, Any]] = Field(default_factory=list)
    output_config_json: dict[str, Any] = Field(default_factory=dict)
    cache_ttl: int
    timeout_seconds: int
    max_rows: int
    status: str
    tool_id: str | None = None
    origin_nl: str | None = None
    business_notes: str = ""
    dimensions_json: list[str] = Field(default_factory=list)
    metrics_json: list[str] = Field(default_factory=list)
    example_questions_json: list[str] = Field(default_factory=list)
    evolution_version: int = 1
    generated_by: str = "manual"
    created_at: datetime
    updated_at: datetime


class QueryTemplateVersion(SQLModel, table=True):
    """Snapshot of a QueryTemplate at a specific evolution version."""

    __tablename__ = "query_template_versions"

    id: str = Field(default_factory=lambda: new_id("qtv"), primary_key=True)
    tenant_id: str = Field(index=True)
    template_id: str = Field(index=True)
    version: int = Field(default=1)
    snapshot_json: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    change_reason: str = Field(default="initial")
    created_by: str | None = Field(default=None)
    created_at: datetime = Field(default_factory=utc_now)


# ---------------------------------------------------------------------------
# Execution request / result (non-table)
# ---------------------------------------------------------------------------


class QueryExecuteRequest(SQLModel):
    """Request payload for executing a query template with concrete parameters."""

    template_id: str
    params: dict[str, Any] = Field(default_factory=dict)


class QueryExecuteResult(SQLModel):
    """Result payload from executing a query template."""

    template_id: str
    # Column names as ordered list of strings
    columns: list[str] = Field(default_factory=list)
    # Result rows as list of dicts
    rows: list[dict[str, Any]] = Field(default_factory=list)
    row_count: int = 0
    # Execution stats
    execution_time_ms: float = 0.0
    cached: bool = False


# ---------------------------------------------------------------------------
# Schema exploration & Data preview (non-table DTOs)
# ---------------------------------------------------------------------------


class TableSummary(SQLModel):
    """Summary of a database table."""

    name: str
    comment: str = ""
    row_count_estimate: int = 0


class ColumnMeta(SQLModel):
    """Metadata of a table column."""

    name: str
    data_type: str
    column_type: str = ""
    is_nullable: bool = True
    comment: str = ""


class TablePreviewResult(SQLModel):
    """Result of previewing a sample of table data."""

    table: str
    columns: list[str] = Field(default_factory=list)
    rows: list[dict[str, Any]] = Field(default_factory=list)
    row_count: int = 0


class AdhocTestRequest(SQLModel):
    """Request payload for executing an ad-hoc query without saving."""

    data_source_id: str
    query_content: str
    query_type: str = "sql"
    params: dict[str, Any] = Field(default_factory=dict)


class QueryTemplateVersionRead(SQLModel):
    """Public read schema for template version snapshot."""

    id: str
    tenant_id: str
    template_id: str
    version: int
    snapshot_json: dict[str, Any] = Field(default_factory=dict)
    change_reason: str = "initial"
    created_by: str | None = None
    created_at: datetime


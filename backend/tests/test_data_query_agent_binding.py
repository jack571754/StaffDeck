"""Tests for data-source-level agent authorization (数据源级授权).

Covers:
- Agent resource binding accepts resource_type="data_source"
- data_query tool execution requires the template's data source to be bound
  to the calling agent
- The agent-visible templates endpoint only lists active templates under
  bound data sources
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi import HTTPException
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.agents.schema import (
    AgentResourceBindingInput,
    AgentResourcesUpdateRequest,
)
from app.api.agents import get_agent_resources, update_agent_resources
from app.data_query.models import DataSource, QueryTemplate
from app.db.models import AgentProfile, AgentResourceBinding, Tenant, Tool, User
from app.tools.tool_executor import ToolExecutor
from app.tools.tool_schema import ToolCall


def _test_session():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    return Session(engine)


def _seed(db: Session) -> tuple[User, AgentProfile]:
    db.add(Tenant(id="tenant_demo", name="Demo"))
    user = User(
        id="user_admin",
        tenant_id="tenant_demo",
        username="admin",
        display_name="Admin",
        password_hash="x",
        role="admin",
    )
    agent = AgentProfile(id="agent_1", tenant_id="tenant_demo", name="数据员工")
    db.add_all([user, agent])
    db.commit()
    return user, agent


def _make_data_source(db: Session, ds_id: str, name: str) -> DataSource:
    row = DataSource(
        id=ds_id,
        tenant_id="tenant_demo",
        name=name,
        type="mysql",
        read_only=True,
        status="active",
    )
    db.add(row)
    return row


def _make_template(db: Session, qt_id: str, ds_id: str, status: str = "active") -> QueryTemplate:
    row = QueryTemplate(
        id=qt_id,
        tenant_id="tenant_demo",
        name=f"模板 {qt_id}",
        data_source_id=ds_id,
        query_type="sql",
        query_content="SELECT 1",
        status=status,
    )
    db.add(row)
    return row


# ---------------------------------------------------------------------------
# Binding: resource_type="data_source"
# ---------------------------------------------------------------------------


def test_agent_can_bind_data_source_resource() -> None:
    with _test_session() as db:
        user, _ = _seed(db)
        _make_data_source(db, "ds_sales", "销售库")
        db.commit()

        result = update_agent_resources(
            "agent_1",
            AgentResourcesUpdateRequest(
                tenant_id="tenant_demo",
                resources=[
                    AgentResourceBindingInput(
                        resource_type="data_source", resource_id="ds_sales"
                    )
                ],
            ),
            db,
            user,
        )

        assert [(row.resource_type, row.resource_id) for row in result] == [
            ("data_source", "ds_sales")
        ]
        listed = get_agent_resources("agent_1", "tenant_demo", db, user)
        assert len(listed) == 1
        assert listed[0].resource_type == "data_source"
        assert listed[0].resource_id == "ds_sales"


def test_bind_unknown_data_source_is_rejected() -> None:
    with _test_session() as db:
        user, _ = _seed(db)
        db.commit()

        with pytest.raises(HTTPException) as exc_info:
            update_agent_resources(
                "agent_1",
                AgentResourcesUpdateRequest(
                    tenant_id="tenant_demo",
                    resources=[
                        AgentResourceBindingInput(
                            resource_type="data_source", resource_id="ds_missing"
                        )
                    ],
                ),
                db,
                user,
            )
        assert exc_info.value.status_code == 404


# ---------------------------------------------------------------------------
# Execution authorization: data_query tool requires source binding
# ---------------------------------------------------------------------------


def _seed_data_query_tool(db: Session, qt_id: str, name: str = "query.sales") -> None:
    db.add(
        Tool(
            tenant_id="tenant_demo",
            name=name,
            display_name="销售查询",
            tool_type="data_query",
            method="POST",
            url=f"data_query://{qt_id}",
            config_json={"template_id": qt_id},
            enabled=True,
        )
    )


def test_data_query_tool_rejected_without_source_binding() -> None:
    with _test_session() as db:
        _seed(db)
        _make_data_source(db, "ds_sales", "销售库")
        _make_template(db, "qt_1", "ds_sales")
        _seed_data_query_tool(db, "qt_1")
        db.commit()

        result = ToolExecutor(db).execute(
            tenant_id="tenant_demo",
            tool_call=ToolCall(name="query.sales", arguments={"params": {}}),
            agent_id="agent_1",
        )

    assert result.success is False
    assert result.error is not None
    assert result.error.code == "NOT_ALLOWED"
    assert "数据源" in (result.error.message or "")


def test_data_query_tool_allowed_with_source_binding() -> None:
    from app.data_query.models import QueryExecuteResult

    with _test_session() as db:
        _seed(db)
        _make_data_source(db, "ds_sales", "销售库")
        _make_template(db, "qt_1", "ds_sales")
        _seed_data_query_tool(db, "qt_1")
        db.add(
            AgentResourceBinding(
                tenant_id="tenant_demo",
                agent_id="agent_1",
                resource_type="data_source",
                resource_id="ds_sales",
            )
        )
        db.commit()

        mock_result = QueryExecuteResult(
            template_id="qt_1",
            columns=["id"],
            rows=[{"id": 1}],
            row_count=1,
            execution_time_ms=1.0,
            cached=False,
        )
        with patch(
            "app.data_query.service.execute_query_by_id", return_value=mock_result
        ):
            result = ToolExecutor(db).execute(
                tenant_id="tenant_demo",
                tool_call=ToolCall(name="query.sales", arguments={"params": {}}),
                agent_id="agent_1",
            )

    assert result.success is True
    assert result.error is None
    assert result.data is not None
    assert result.data["row_count"] == 1


def test_data_query_tool_rejected_when_bound_source_is_inactive() -> None:
    with _test_session() as db:
        _seed(db)
        ds = _make_data_source(db, "ds_sales", "销售库")
        ds.status = "inactive"
        _make_template(db, "qt_1", "ds_sales")
        _seed_data_query_tool(db, "qt_1")
        db.add(
            AgentResourceBinding(
                tenant_id="tenant_demo",
                agent_id="agent_1",
                resource_type="data_source",
                resource_id="ds_sales",
            )
        )
        db.commit()

        result = ToolExecutor(db).execute(
            tenant_id="tenant_demo",
            tool_call=ToolCall(name="query.sales", arguments={"params": {}}),
            agent_id="agent_1",
        )

    assert result.success is False
    assert result.error is not None
    assert result.error.code == "NOT_ALLOWED"


# ---------------------------------------------------------------------------
# Agent-visible templates endpoint
# ---------------------------------------------------------------------------


def test_agent_templates_scoped_to_bound_sources() -> None:
    from app.data_query.api import list_agent_query_templates

    with _test_session() as db:
        user, _ = _seed(db)
        _make_data_source(db, "ds_sales", "销售库")
        _make_data_source(db, "ds_other", "其他库")
        _make_template(db, "qt_active", "ds_sales", status="active")
        _make_template(db, "qt_draft", "ds_sales", status="draft")
        _make_template(db, "qt_unbound", "ds_other", status="active")
        db.add(
            AgentResourceBinding(
                tenant_id="tenant_demo",
                agent_id="agent_1",
                resource_type="data_source",
                resource_id="ds_sales",
            )
        )
        db.commit()

        rows = list_agent_query_templates("agent_1", "tenant_demo", db, user)

    assert [row["id"] for row in rows] == ["qt_active"]
    assert rows[0]["data_source_id"] == "ds_sales"
    assert rows[0]["name"] == "模板 qt_active"


# ---------------------------------------------------------------------------
# data_query_grant_all（默认全量 + 例外收紧）
# ---------------------------------------------------------------------------


def _set_grant_all(db: Session, enabled: bool) -> None:
    from app.db.models import UIConfig

    row = db.get(UIConfig, "tenant_demo")
    if row is None:
        row = UIConfig(tenant_id="tenant_demo")
        db.add(row)
    row.data_query_grant_all = enabled
    db.commit()


def test_grant_all_allows_unbound_agent_query_tool() -> None:
    from app.data_query.models import QueryExecuteResult

    with _test_session() as db:
        _seed(db)
        _make_data_source(db, "ds_sales", "销售库")
        _make_template(db, "qt_1", "ds_sales")
        _seed_data_query_tool(db, "qt_1")
        _set_grant_all(db, True)

        mock_result = QueryExecuteResult(
            template_id="qt_1",
            columns=["id"],
            rows=[{"id": 1}],
            row_count=1,
            execution_time_ms=1.0,
            cached=False,
        )
        with patch(
            "app.data_query.service.execute_query_by_id", return_value=mock_result
        ):
            result = ToolExecutor(db).execute(
                tenant_id="tenant_demo",
                tool_call=ToolCall(name="query.sales", arguments={"params": {}}),
                agent_id="agent_1",
            )

    assert result.success is True
    assert result.error is None


def test_grant_all_excludes_source_bound_inactive() -> None:
    from app.data_query.models import QueryExecuteResult

    with _test_session() as db:
        _seed(db)
        _make_data_source(db, "ds_sales", "销售库")
        _make_data_source(db, "ds_erp", "ERP 库")
        _make_template(db, "qt_sales", "ds_sales")
        _make_template(db, "qt_erp", "ds_erp")
        _seed_data_query_tool(db, "qt_sales")
        _seed_data_query_tool(db, "qt_erp", name="query.erp")
        _set_grant_all(db, True)
        db.add(
            AgentResourceBinding(
                tenant_id="tenant_demo",
                agent_id="agent_1",
                resource_type="data_source",
                resource_id="ds_sales",
                status="inactive",
            )
        )
        db.commit()

        mock_result = QueryExecuteResult(
            template_id="qt_erp",
            columns=["id"],
            rows=[{"id": 1}],
            row_count=1,
            execution_time_ms=1.0,
            cached=False,
        )
        with patch(
            "app.data_query.service.execute_query_by_id", return_value=mock_result
        ):
            blocked = ToolExecutor(db).execute(
                tenant_id="tenant_demo",
                tool_call=ToolCall(name="query.sales", arguments={"params": {}}),
                agent_id="agent_1",
            )
            allowed = ToolExecutor(db).execute(
                tenant_id="tenant_demo",
                tool_call=ToolCall(name="query.erp", arguments={"params": {}}),
                agent_id="agent_1",
            )

    assert blocked.success is False
    assert blocked.error is not None
    assert blocked.error.code == "NOT_ALLOWED"
    assert allowed.success is True
    assert allowed.error is None


def test_grant_all_templates_endpoint_lists_all_active_sources() -> None:
    from app.data_query.api import list_agent_query_templates

    with _test_session() as db:
        user, _ = _seed(db)
        _make_data_source(db, "ds_sales", "销售库")
        _make_data_source(db, "ds_erp", "ERP 库")
        _make_template(db, "qt_sales", "ds_sales", status="active")
        _make_template(db, "qt_erp", "ds_erp", status="active")
        _make_template(db, "qt_draft", "ds_erp", status="draft")
        _set_grant_all(db, True)

        rows = list_agent_query_templates("agent_1", "tenant_demo", db, user)

    assert [row["id"] for row in rows] == ["qt_erp", "qt_sales"]


def test_switch_back_to_whitelist_restores_bindings() -> None:
    with _test_session() as db:
        _seed(db)
        _make_data_source(db, "ds_sales", "销售库")
        _make_data_source(db, "ds_erp", "ERP 库")
        _make_template(db, "qt_sales", "ds_sales")
        _make_template(db, "qt_erp", "ds_erp")
        _seed_data_query_tool(db, "qt_erp", name="query.erp")
        _set_grant_all(db, True)
        db.add(
            AgentResourceBinding(
                tenant_id="tenant_demo",
                agent_id="agent_1",
                resource_type="data_source",
                resource_id="ds_sales",
                status="inactive",
            )
        )
        _set_grant_all(db, False)

        result = ToolExecutor(db).execute(
            tenant_id="tenant_demo",
            tool_call=ToolCall(name="query.erp", arguments={"params": {}}),
            agent_id="agent_1",
        )

    # 白名单模式：无 active 绑定 → NOT_ALLOWED
    assert result.success is False
    assert result.error is not None
    assert result.error.code == "NOT_ALLOWED"

from unittest.mock import patch

import pytest
from fastapi import HTTPException
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.agents.branching import is_tool_visible_for_agent
from app.api.tools import get_tool
from app.api.tools import test_tool as _test_tool
from app.data_query.models import DataSource, QueryExecuteResult, QueryTemplate
from app.db.models import AgentProfile, AgentResourceBinding, Tenant, Tool, User
from app.tools.tool_schema import ToolTestRequest


def _test_session() -> Session:
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
        role="admin",
        password_hash="test",
    )
    agent = AgentProfile(
        id="agent_1",
        tenant_id="tenant_demo",
        name="销售分析员",
        is_overall=False,
    )
    overall = AgentProfile(
        id="agent_overall",
        tenant_id="tenant_demo",
        name="整体智能体",
        is_overall=True,
    )
    db.add(user)
    db.add(agent)
    db.add(overall)
    db.commit()
    return user, agent


def _seed_data_query(
    db: Session,
    ds_id: str = "ds_sales",
    qt_id: str = "qt_sales_summary",
    tool_id: str = "tool_sales_query",
    qt_status: str = "active",
) -> Tool:
    ds = DataSource(
        id=ds_id,
        tenant_id="tenant_demo",
        name="销售数据库",
        type="mysql",
        status="active",
    )
    qt = QueryTemplate(
        id=qt_id,
        tenant_id="tenant_demo",
        name="销售底表查询",
        data_source_id=ds_id,
        query_type="sql",
        query_content="SELECT 1",
        status=qt_status,
    )
    tool = Tool(
        id=tool_id,
        tenant_id="tenant_demo",
        name="query_sales_summary",
        display_name="销售底表查询",
        bucket="数据查询",
        tool_type="data_query",
        method="POST",
        url=f"data_query://{qt_id}",
        config_json={"template_id": qt_id, "output_format": "table"},
        input_schema={"type": "object", "properties": {"params": {"type": "object"}}},
        enabled=True,
    )
    db.add(ds)
    db.add(qt)
    db.add(tool)
    db.commit()
    db.refresh(tool)
    return tool


def test_data_query_tool_visibility_and_get_tool() -> None:
    with _test_session() as db:
        _user, agent = _seed(db)
        tool = _seed_data_query(db)

        # 1. 员工未授权对应数据源前：不可见
        assert is_tool_visible_for_agent(db, "tenant_demo", tool, agent.id) is False
        with pytest.raises(HTTPException) as exc_info:
            get_tool(tool.id, "tenant_demo", agent.id, db)
        assert exc_info.value.status_code == 404
        assert exc_info.value.detail == "Tool not visible to this agent"

        # 2. 员工绑定数据源后：变为可见并可通过 get_tool 访问
        db.add(
            AgentResourceBinding(
                tenant_id="tenant_demo",
                agent_id=agent.id,
                resource_type="data_source",
                resource_id="ds_sales",
                status="active",
            )
        )
        db.commit()

        assert is_tool_visible_for_agent(db, "tenant_demo", tool, agent.id) is True
        res = get_tool(tool.id, "tenant_demo", agent.id, db)
        assert res.id == tool.id
        assert res.name == "query_sales_summary"
        assert res.tool_type == "data_query"

        # 3. 全局视角（agent_id=None 或 overall）：默认可见
        assert is_tool_visible_for_agent(db, "tenant_demo", tool, None) is True
        res_overall = get_tool(tool.id, "tenant_demo", None, db)
        assert res_overall.id == tool.id


def test_data_query_tool_test_execution() -> None:
    with _test_session() as db:
        user, agent = _seed(db)
        tool = _seed_data_query(db)
        db.add(
            AgentResourceBinding(
                tenant_id="tenant_demo",
                agent_id=agent.id,
                resource_type="data_source",
                resource_id="ds_sales",
                status="active",
            )
        )
        db.commit()

        mock_result = QueryExecuteResult(
            template_id="qt_sales_summary",
            columns=["order_id", "amount"],
            rows=[{"order_id": "O1", "amount": 100}],
            row_count=1,
            execution_time_ms=5.0,
            cached=False,
        )
        with patch("app.data_query.service.execute_query_by_id", return_value=mock_result):
            req = ToolTestRequest(
                tenant_id="tenant_demo",
                arguments={"params": {"date": "2026-09-20"}},
            )
            result = _test_tool(tool.id, req, agent.id, db, user)

        assert result.success is True
        assert result.error is None
        assert result.data["row_count"] == 1
        assert result.data["columns"] == ["order_id", "amount"]


def test_data_query_tool_overall_visibility_requires_active_template() -> None:
    with _test_session() as db:
        _seed(db)
        tool = _seed_data_query(db, qt_status="draft")

        # draft 状态模板对全局广场不可见
        assert is_tool_visible_for_agent(db, "tenant_demo", tool, None) is False
        with pytest.raises(HTTPException) as exc_info:
            get_tool(tool.id, "tenant_demo", None, db)
        assert exc_info.value.status_code == 404
        assert exc_info.value.detail == "Tool not visible in open gallery"

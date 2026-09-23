from unittest.mock import patch

import pytest
from fastapi import HTTPException
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.agents.branching import is_tool_visible_for_agent, visible_tool_rows
from app.api.tools import create_tool, delete_tool, get_tool, list_tool_skills
from app.data_query.models import DataSource, QueryExecuteResult, QueryTemplate
from app.db.models import AgentProfile, AgentResourceBinding, Tenant, Tool, User
from app.tools import ToolExecutor
from app.tools.tool_schema import ToolCall, ToolCreateRequest


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


def test_create_data_query_source_tool_enforces_readonly() -> None:
    with _test_session() as db:
        user, _ = _seed(db)

        # 1. 创建非只读数据源
        ds_writable = DataSource(
            id="ds_writable",
            tenant_id="tenant_demo",
            name="可写数据库",
            type="mysql",
            read_only=False,
            status="active",
        )
        db.add(ds_writable)
        db.commit()

        # 尝试绑定非只读数据源：应当被拒绝 (400)
        req_invalid = ToolCreateRequest(
            tenant_id="tenant_demo",
            name="ds_tool_writable",
            display_name="可写数据源工具",
            tool_type="data_query_source",
            data_source_id="ds_writable",
        )
        with pytest.raises(HTTPException) as exc_info:
            create_tool(req_invalid, agent_id=None, db=db, current_user=user)
        assert exc_info.value.status_code == 400
        assert "只读模式" in exc_info.value.detail

        # 2. 创建只读数据源
        ds_readonly = DataSource(
            id="ds_readonly",
            tenant_id="tenant_demo",
            name="只读数据库",
            type="mysql",
            read_only=True,
            status="active",
        )
        db.add(ds_readonly)
        db.commit()

        req_valid = ToolCreateRequest(
            tenant_id="tenant_demo",
            name="ds_tool_readonly",
            display_name="只读数据源工具",
            tool_type="data_query_source",
            data_source_id="ds_readonly",
        )
        created = create_tool(req_valid, agent_id=None, db=db, current_user=user)
        assert created.name == "ds_tool_readonly"
        assert created.tool_type == "data_query_source"
        assert created.data_source_id == "ds_readonly"


def test_data_query_source_tool_visibility_inherits_source_binding() -> None:
    with _test_session() as db:
        user, agent = _seed(db)

        ds = DataSource(
            id="ds_main",
            tenant_id="tenant_demo",
            name="核心分析库",
            type="mysql",
            read_only=True,
            status="active",
        )
        db.add(ds)
        db.commit()

        req = ToolCreateRequest(
            tenant_id="tenant_demo",
            name="tool_core_source",
            display_name="核心数据源工具",
            tool_type="data_query_source",
            data_source_id="ds_main",
        )
        tool_read = create_tool(req, agent_id=None, db=db, current_user=user)
        tool = db.get(Tool, tool_read.id)
        assert tool is not None

        # 员工未绑定该数据源前：不可见
        assert is_tool_visible_for_agent(db, "tenant_demo", tool, agent.id) is False
        assert tool.id not in [row.id for row in visible_tool_rows(db, "tenant_demo", agent_id=agent.id)]

        with pytest.raises(HTTPException) as exc_info:
            get_tool(tool.id, "tenant_demo", agent.id, db)
        assert exc_info.value.status_code == 404

        # 员工绑定数据源后：自动获得可见性
        db.add(
            AgentResourceBinding(
                tenant_id="tenant_demo",
                agent_id=agent.id,
                resource_type="data_source",
                resource_id="ds_main",
                status="active",
            )
        )
        db.commit()

        assert is_tool_visible_for_agent(db, "tenant_demo", tool, agent.id) is True
        assert tool.id in [row.id for row in visible_tool_rows(db, "tenant_demo", agent_id=agent.id)]
        retrieved = get_tool(tool.id, "tenant_demo", agent.id, db)
        assert retrieved.id == tool.id
        assert retrieved.tool_type == "data_query_source"
        assert retrieved.data_source_id == "ds_main"


def test_execute_data_query_source_tool_by_skill() -> None:
    with _test_session() as db:
        _user, agent = _seed(db)

        ds = DataSource(
            id="ds_sales",
            tenant_id="tenant_demo",
            name="销售库",
            type="mysql",
            read_only=True,
            status="active",
        )
        qt = QueryTemplate(
            id="qt_daily_sales",
            tenant_id="tenant_demo",
            name="日销售汇总",
            description="按日期统计全渠道销售",
            data_source_id="ds_sales",
            query_type="sql",
            query_content="SELECT store, sum(amount) FROM orders GROUP BY store",
            status="active",
        )
        tool = Tool(
            id="tool_sales_ds",
            tenant_id="tenant_demo",
            name="sales_source_query",
            display_name="销售数据源工具",
            bucket="数据查询",
            tool_type="data_query_source",
            data_source_id="ds_sales",
            config_json={"data_source_id": "ds_sales", "output_format": "table"},
            input_schema={
                "type": "object",
                "properties": {
                    "skill": {"type": "string", "enum": ["日销售汇总"]},
                    "params": {"type": "object"},
                },
                "required": ["skill"],
            },
            enabled=True,
        )
        db.add(ds)
        db.add(qt)
        db.add(tool)
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
            template_id="qt_daily_sales",
            columns=["store", "total_amount"],
            rows=[{"store": "北京旗舰店", "total_amount": 158000.0}],
            row_count=1,
            execution_time_ms=12.5,
            cached=False,
        )

        with patch("app.data_query.service.execute_query_by_id", return_value=mock_result) as mock_exec:
            executor = ToolExecutor(db)
            tool_call = ToolCall(
                name="sales_source_query",
                arguments={"skill": "日销售汇总", "params": {"date": "2026-09-22"}},
            )
            result = executor.execute("tenant_demo", tool_call, agent_id=agent.id)

            assert result.success is True
            assert result.error is None
            assert result.data["skill"] == "日销售汇总"
            assert result.data["template_id"] == "qt_daily_sales"
            assert result.data["row_count"] == 1
            assert "北京旗舰店" in result.data["text"]
            mock_exec.assert_called_once_with(db, "qt_daily_sales", "tenant_demo", {"date": "2026-09-22"})


def test_execute_data_query_source_tool_missing_or_unknown_skill() -> None:
    with _test_session() as db:
        _user, agent = _seed(db)

        ds = DataSource(
            id="ds_sales",
            tenant_id="tenant_demo",
            name="销售库",
            type="mysql",
            read_only=True,
            status="active",
        )
        qt = QueryTemplate(
            id="qt_daily_sales",
            tenant_id="tenant_demo",
            name="日销售汇总",
            description="按日期统计销售",
            data_source_id="ds_sales",
            query_type="sql",
            query_content="SELECT 1",
            status="active",
        )
        tool = Tool(
            id="tool_sales_ds",
            tenant_id="tenant_demo",
            name="sales_source_query",
            tool_type="data_query_source",
            data_source_id="ds_sales",
            config_json={"data_source_id": "ds_sales"},
            enabled=True,
        )
        db.add(ds)
        db.add(qt)
        db.add(tool)
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

        executor = ToolExecutor(db)

        # 1. 缺失 skill 参数
        call_missing = ToolCall(name="sales_source_query", arguments={})
        res_missing = executor.execute("tenant_demo", call_missing, agent_id=agent.id)
        assert res_missing.success is False
        assert res_missing.error is not None
        assert res_missing.error.code == "MISSING_SKILL"
        assert "日销售汇总" in res_missing.error.message

        # 2. 未匹配到已知技能
        call_unknown = ToolCall(name="sales_source_query", arguments={"skill": "库存盘点"})
        res_unknown = executor.execute("tenant_demo", call_unknown, agent_id=agent.id)
        assert res_unknown.success is False
        assert res_unknown.error is not None
        assert res_unknown.error.code == "SKILL_NOT_FOUND"
        assert "日销售汇总" in res_unknown.error.message


def test_admin_delete_tool_clears_template_foreign_keys() -> None:
    with _test_session() as db:
        db.add(Tenant(id="tenant_demo", name="Demo"))
        user = User(
            id="user_admin",
            tenant_id="tenant_demo",
            username="admin",
            role="admin",
            password_hash="test",
        )
        db.add(user)

        ds = DataSource(
            id="ds_sales",
            tenant_id="tenant_demo",
            name="销售库",
            type="mysql",
            read_only=True,
            status="active",
        )
        tool = Tool(
            id="tool_ds_container",
            tenant_id="tenant_demo",
            name="sales_container",
            tool_type="data_query_source",
            data_source_id="ds_sales",
            enabled=True,
        )
        qt = QueryTemplate(
            id="qt_sub_skill",
            tenant_id="tenant_demo",
            name="子技能",
            data_source_id="ds_sales",
            tool_id="tool_ds_container",
            status="active",
        )
        db.add(ds)
        db.add(tool)
        db.add(qt)
        db.commit()

        # Admin 删除容器工具
        del_res = delete_tool(tool.id, tenant_id="tenant_demo", db=db, agent_id=None, current_user=user)
        assert del_res["status"] == "deleted"

        # 验证工具实体已被删除
        assert db.get(Tool, tool.id) is None

        # 验证子技能仍然存在，但 tool_id 已安全置空
        refreshed_qt = db.get(QueryTemplate, "qt_sub_skill")
        assert refreshed_qt is not None
        assert refreshed_qt.tool_id is None


def test_get_tool_skills_endpoint() -> None:
    with _test_session() as db:
        user, _agent = _seed(db)

        ds = DataSource(
            id="ds_sales",
            tenant_id="tenant_demo",
            name="销售库",
            type="mysql",
            read_only=True,
            status="active",
        )
        tool = Tool(
            id="tool_ds_container",
            tenant_id="tenant_demo",
            name="sales_container",
            tool_type="data_query_source",
            data_source_id="ds_sales",
            enabled=True,
        )
        qt1 = QueryTemplate(
            id="qt_active",
            tenant_id="tenant_demo",
            name="活跃技能",
            data_source_id="ds_sales",
            tool_id="tool_ds_container",
            status="active",
        )
        qt2 = QueryTemplate(
            id="qt_draft",
            tenant_id="tenant_demo",
            name="草稿技能",
            data_source_id="ds_sales",
            tool_id="tool_ds_container",
            status="draft",
        )
        db.add(ds)
        db.add(tool)
        db.add(qt1)
        db.add(qt2)
        db.commit()

        # 全部技能
        skills_all = list_tool_skills(tool.id, tenant_id="tenant_demo", status=None, agent_id=None, db=db, current_user=user)
        assert len(skills_all) == 2
        skill_ids = {s.id for s in skills_all}
        assert skill_ids == {"qt_active", "qt_draft"}

        # 按 status=active 过滤
        skills_active = list_tool_skills(tool.id, tenant_id="tenant_demo", status="active", agent_id=None, db=db, current_user=user)
        assert len(skills_active) == 1
        assert skills_active[0].id == "qt_active"

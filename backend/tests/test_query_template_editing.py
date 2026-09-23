from unittest.mock import patch

import pytest
from fastapi import HTTPException
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.data_query.api import (
    adhoc_test_query,
    list_query_template_versions,
    rollback_query_template_version,
    update_query_template,
)
from app.data_query.connectors.base import QueryResult
from app.data_query.models import (
    AdhocTestRequest,
    DataSource,
    QueryTemplate,
    QueryTemplateUpdate,
    QueryTemplateVersion,
)
from app.db.models import Tenant, User


def _test_session() -> Session:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    return Session(engine)


def _seed(db: Session) -> tuple[User, User, DataSource, QueryTemplate]:
    db.add(Tenant(id="tenant_demo", name="Demo"))
    admin = User(
        id="user_admin",
        tenant_id="tenant_demo",
        username="admin",
        role="admin",
        password_hash="test",
    )
    member = User(
        id="user_member",
        tenant_id="tenant_demo",
        username="member",
        role="member",
        password_hash="test",
    )
    ds = DataSource(
        id="ds_mysql_1",
        tenant_id="tenant_demo",
        name="业务核心库",
        type="mysql",
        read_only=True,
        status="active",
        config_json={"host": "127.0.0.1", "database": "sales_db"},
    )
    qt = QueryTemplate(
        id="qt_active_1",
        tenant_id="tenant_demo",
        name="日销售统计",
        data_source_id="ds_mysql_1",
        query_type="sql",
        query_content="SELECT store_name, SUM(amount) FROM orders GROUP BY store_name",
        params_json=[],
        status="active",
        evolution_version=1,
    )
    db.add(admin)
    db.add(member)
    db.add(ds)
    db.add(qt)
    db.commit()
    db.refresh(qt)
    return admin, member, ds, qt


def test_adhoc_test_query_admin_only_and_safety() -> None:
    with _test_session() as db:
        admin, member, ds, _ = _seed(db)

        # 1. 普通 member 调用被拒绝 (403)
        req = AdhocTestRequest(
            data_source_id=ds.id,
            query_content="SELECT 1",
            query_type="sql",
        )
        with pytest.raises(HTTPException) as exc_info:
            adhoc_test_query(
                request=req,
                tenant_id="tenant_demo",
                db=db,
                current_user=member,
            )
        assert exc_info.value.status_code == 403

        # 2. 尝试执行 UPDATE 写入语句被拒绝 (400)
        write_req = AdhocTestRequest(
            data_source_id=ds.id,
            query_content="UPDATE orders SET amount = 0",
            query_type="sql",
        )
        with pytest.raises(HTTPException) as exc_info:
            adhoc_test_query(
                request=write_req,
                tenant_id="tenant_demo",
                db=db,
                current_user=admin,
            )
        assert exc_info.value.status_code == 400
        assert "only SELECT and WITH" in exc_info.value.detail

        # 3. 正常只读查询，无 LIMIT 自动被包裹并成功执行
        mock_result = QueryResult(
            columns=["cnt"],
            rows=[{"cnt": 42}],
            row_count=1,
        )
        with patch("app.data_query.connectors.mysql_connector.MySQLConnector.execute", return_value=mock_result) as mock_exec:
            res = adhoc_test_query(
                request=req,
                tenant_id="tenant_demo",
                db=db,
                current_user=admin,
            )
            assert res.template_id == "adhoc"
            assert res.columns == ["cnt"]
            assert res.rows == [{"cnt": 42}]
            # 校验 SQL 被自动包裹 LIMIT
            called_sql = mock_exec.call_args[0][0]
            assert "LIMIT 50" in called_sql


def test_active_template_edit_creates_draft_copy_without_mutating_active() -> None:
    with _test_session() as db:
        admin, _, _, qt = _seed(db)

        # 针对 active 状态模板修改 query_content
        update_req = QueryTemplateUpdate(
            query_content="SELECT store_name, SUM(amount) FROM orders WHERE date >= :start_date GROUP BY store_name",
            params_json=[{"name": "start_date", "type": "date"}],
        )

        res = update_query_template(
            qt_id=qt.id,
            request=update_req,
            tenant_id="tenant_demo",
            db=db,
            current_user=admin,
        )

        # 1. 返回的对象是新创建的 draft 副本
        assert res.id != qt.id
        assert res.status == "draft"
        assert res.evolution_version == 2
        assert "草稿 v2" in res.name
        assert "WHERE date >=" in res.query_content

        # 2. 原 active 模板不受任何影响，保持稳定在线
        db.refresh(qt)
        assert qt.status == "active"
        assert qt.evolution_version == 1
        assert "WHERE date >=" not in qt.query_content

        # 3. 校验写入了 QueryTemplateVersion 快照
        versions = list_query_template_versions(
            qt_id=qt.id,
            tenant_id="tenant_demo",
            db=db,
            current_user=admin,
        )
        assert len(versions) >= 1
        assert any(v.version == 2 and v.change_reason == "manual_edit" for v in versions)


def test_template_version_rollback() -> None:
    with _test_session() as db:
        admin, _, _, qt = _seed(db)

        # 手动预置一个版本 1 快照
        v1 = QueryTemplateVersion(
            tenant_id="tenant_demo",
            template_id=qt.id,
            version=1,
            snapshot_json={"query_content": "SELECT 100 as test_val"},
            change_reason="initial",
        )
        db.add(v1)
        db.commit()

        # 发起回滚到版本 1
        rollback_draft = rollback_query_template_version(
            qt_id=qt.id,
            version=1,
            tenant_id="tenant_demo",
            db=db,
            current_user=admin,
        )

        assert rollback_draft.status == "draft"
        assert rollback_draft.query_content == "SELECT 100 as test_val"
        assert "回滚至 v1 草稿" in rollback_draft.name

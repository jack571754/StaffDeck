from unittest.mock import patch

import pytest
from fastapi import HTTPException
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.data_query.api import (
    describe_data_source_table,
    list_data_source_tables,
    preview_data_source_table,
    refresh_data_source_schema,
)
from app.data_query.connectors.base import QueryResult
from app.data_query.connectors.mysql_connector import MySQLConnector
from app.data_query.models import DataSource
from app.db.models import Tenant, User


def _test_session() -> Session:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    return Session(engine)


def _seed(db: Session) -> tuple[User, User, DataSource]:
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
        allowed_tables_json=["orders", "order_items"],
        config_json={"host": "127.0.0.1", "database": "sales_db"},
    )
    db.add(admin)
    db.add(member)
    db.add(ds)
    db.commit()
    db.refresh(ds)
    return admin, member, ds


def test_list_data_source_tables() -> None:
    with _test_session() as db:
        admin, _, ds = _seed(db)

        mock_tables = [
            {"name": "orders", "comment": "订单主表", "row_count_estimate": 1000},
            {"name": "order_items", "comment": "订单明细表", "row_count_estimate": 5000},
        ]
        with patch("app.data_query.connectors.mysql_connector.MySQLConnector.list_tables", return_value=mock_tables):
            tables = list_data_source_tables(
                ds_id=ds.id,
                tenant_id="tenant_demo",
                db=db,
                current_user=admin,
            )
            assert len(tables) == 2
            assert tables[0].name == "orders"
            assert tables[0].comment == "订单主表"
            assert tables[0].row_count_estimate == 1000


def test_describe_data_source_table_success_and_invalid_name() -> None:
    with _test_session() as db:
        admin, _, ds = _seed(db)

        mock_cols = [
            {"name": "id", "data_type": "int", "column_type": "int(11)", "is_nullable": False, "comment": "主键"},
            {"name": "total_amount", "data_type": "decimal", "column_type": "decimal(10,2)", "is_nullable": True, "comment": "金额"},
        ]
        with patch("app.data_query.connectors.mysql_connector.MySQLConnector.describe_table", return_value=mock_cols):
            cols = describe_data_source_table(
                ds_id=ds.id,
                table="orders",
                tenant_id="tenant_demo",
                db=db,
                current_user=admin,
            )
            assert len(cols) == 2
            assert cols[0].name == "id"
            assert cols[0].is_nullable is False
            assert cols[1].data_type == "decimal"

        # 测试非法表名抛出 400
        with pytest.raises(HTTPException) as exc_info:
            describe_data_source_table(
                ds_id=ds.id,
                table="orders; DROP TABLE users;",
                tenant_id="tenant_demo",
                db=db,
                current_user=admin,
            )
        assert exc_info.value.status_code == 400


def test_preview_data_source_table() -> None:
    with _test_session() as db:
        admin, _, ds = _seed(db)

        mock_result = QueryResult(
            columns=["id", "order_sn", "amount"],
            rows=[{"id": 1, "order_sn": "SN001", "amount": 99.9}],
            row_count=1,
        )
        with patch("app.data_query.connectors.mysql_connector.MySQLConnector.preview_table", return_value=mock_result) as mock_prev:
            res = preview_data_source_table(
                ds_id=ds.id,
                table="orders",
                limit=20,
                tenant_id="tenant_demo",
                db=db,
                current_user=admin,
            )
            assert res.table == "orders"
            assert len(res.rows) == 1
            assert res.columns == ["id", "order_sn", "amount"]
            mock_prev.assert_called_once_with("orders", limit=20)


def test_refresh_data_source_schema() -> None:
    with _test_session() as db:
        admin, member, ds = _seed(db)

        # 1. 普通 member 调用必须被拒绝（403）
        with pytest.raises(HTTPException) as exc_info:
            refresh_data_source_schema(
                ds_id=ds.id,
                tenant_id="tenant_demo",
                db=db,
                current_user=member,
            )
        assert exc_info.value.status_code == 403

        # 2. admin 调用刷新
        mock_tables = [{"name": "orders", "comment": "订单主表", "row_count_estimate": 200}]
        mock_cols = [{"name": "id", "data_type": "int", "column_type": "int(11)", "is_nullable": False, "comment": "PK"}]

        with patch("app.data_query.connectors.mysql_connector.MySQLConnector.list_tables", return_value=mock_tables), \
             patch("app.data_query.connectors.mysql_connector.MySQLConnector.describe_table", return_value=mock_cols):
            res = refresh_data_source_schema(
                ds_id=ds.id,
                tenant_id="tenant_demo",
                db=db,
                current_user=admin,
            )
            assert "tables" in res
            assert "orders" in res["tables"]
            assert res["tables"]["orders"]["comment"] == "订单主表"
            assert len(res["tables"]["orders"]["columns"]) == 1

            # 验证数据库中持久化了缓存与刷新时间
            db.refresh(ds)
            assert ds.schema_refreshed_at is not None
            assert ds.schema_cache_json["tables"]["orders"]["row_count_estimate"] == 200


def test_mysql_connector_whitelist_enforcement() -> None:
    ds = DataSource(
        id="ds_mysql_2",
        tenant_id="tenant_demo",
        name="带白名单库",
        type="mysql",
        read_only=True,
        status="active",
        allowed_tables_json=["allowed_table"],
        config_json={"host": "127.0.0.1", "database": "sales_db"},
    )
    connector = MySQLConnector(ds)

    # 1. 尝试 describe 非白名单表应被拦截
    with pytest.raises(ValueError, match="not in the allowed tables list"):
        connector.describe_table("secret_user_table")

    # 2. 尝试 preview 非白名单表应被拦截
    with pytest.raises(ValueError, match="not in the allowed tables list"):
        connector.preview_table("secret_user_table", limit=10)

    # 3. 尝试注入表名应被正则拦截
    with pytest.raises(ValueError, match="Invalid table identifier"):
        connector.describe_table("allowed_table` WHERE 1=1; --")

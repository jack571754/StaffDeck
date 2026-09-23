"""定时任务保存路径对"所选飞书应用"的校验。

覆盖 `_prepare_scheduled_task_feishu_metadata`：所选应用必须存在且启用，切换应用
时必须重新选择目标群聊（chat_id 是应用维度标识），未传 metadata 的局部更新不得
丢掉已绑定的应用。
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.db.models import AgentProfile, ChannelBinding, ScheduledTask, Tenant, User
from app.scheduled_tasks.schema import ScheduledTaskCreateRequest, ScheduledTaskUpdateRequest
from app.scheduled_tasks.service import create_scheduled_task, update_scheduled_task


def _test_session() -> Session:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    return Session(engine)


def _seed(db: Session) -> None:
    db.add(Tenant(id="tenant_demo", name="Demo"))
    db.add(AgentProfile(id="agent_demo", tenant_id="tenant_demo", name="客服", is_overall=False))
    db.add(
        User(
            id="user_demo",
            tenant_id="tenant_demo",
            username="demo",
            display_name="演示用户",
            role="admin",
            password_hash="test-hash",
        )
    )
    db.add(_binding("bind_a", name="A 应用"))
    db.add(_binding("bind_b", name="B 应用"))
    db.add(_binding("bind_disabled", name="停用应用", status="disabled"))
    db.commit()


def _binding(
    binding_id: str,
    *,
    name: str,
    status: str = "active",
    tenant_id: str = "tenant_demo",
) -> ChannelBinding:
    return ChannelBinding(
        id=binding_id,
        tenant_id=tenant_id,
        agent_id="agent_demo",
        channel="feishu",
        status=status,
        name=name,
        config_json={"app_id": f"cli_{binding_id}"},
        credentials_enc="mock_enc",
    )


def _create(db: Session, *, feishu_notify: dict[str, object]) -> ScheduledTask:
    request = ScheduledTaskCreateRequest(
        tenant_id="tenant_demo",
        agent_id="agent_demo",
        title="价格巡检",
        prompt="执行价格巡检",
        schedule_type="daily",
        schedule={"time": "09:00"},
        metadata={"feishu_notify": feishu_notify},
    )
    return create_scheduled_task(db, request, db.get(User, "user_demo"))


def test_create_scheduled_task_accepts_active_feishu_binding() -> None:
    with _test_session() as db:
        _seed(db)
        row = _create(
            db,
            feishu_notify={
                "enabled": True,
                "binding_id": "bind_a",
                "app_id": "cli_bind_a",
                "app_name": "A 应用",
                "chat_ids": ["oc_group_1"],
            },
        )

        notify = row.metadata_json["feishu_notify"]
        assert notify["binding_id"] == "bind_a"
        assert notify["app_name"] == "A 应用"


def test_create_scheduled_task_rejects_unknown_feishu_binding() -> None:
    with _test_session() as db:
        _seed(db)
        with pytest.raises(HTTPException) as excinfo:
            _create(db, feishu_notify={"enabled": True, "binding_id": "bind_missing"})

        assert excinfo.value.status_code == 400
        assert "所选飞书应用不存在或已停用" in excinfo.value.detail


def test_create_scheduled_task_rejects_inactive_feishu_binding() -> None:
    with _test_session() as db:
        _seed(db)
        with pytest.raises(HTTPException) as excinfo:
            _create(db, feishu_notify={"enabled": True, "binding_id": "bind_disabled"})

        assert excinfo.value.status_code == 400
        assert "所选飞书应用不存在或已停用" in excinfo.value.detail


def test_create_scheduled_task_allows_auto_mode_without_binding() -> None:
    """未选应用（含全部存量任务的形态）不触发校验，运行时回退最新启用应用。"""

    with _test_session() as db:
        _seed(db)
        row = _create(db, feishu_notify={"enabled": True, "chat_ids": ["oc_group_1"]})

        # 服务端不补键：缺省与空串都表示"自动"，与 resolve_feishu_binding 的判定一致。
        assert row.metadata_json["feishu_notify"].get("binding_id", "") == ""


def test_update_scheduled_task_rejects_chat_ids_after_app_switch() -> None:
    with _test_session() as db:
        _seed(db)
        row = _create(
            db,
            feishu_notify={"enabled": True, "binding_id": "bind_a", "chat_ids": ["oc_group_a"]},
        )

        with pytest.raises(HTTPException) as excinfo:
            update_scheduled_task(
                db,
                row,
                ScheduledTaskUpdateRequest(
                    tenant_id="tenant_demo",
                    metadata={
                        "feishu_notify": {
                            "enabled": True,
                            "binding_id": "bind_b",
                            "chat_ids": ["oc_group_a"],
                        }
                    },
                ),
                db.get(User, "user_demo"),
            )

        assert excinfo.value.status_code == 400
        assert "切换飞书应用后必须重新选择目标群聊" in excinfo.value.detail


def test_update_scheduled_task_accepts_app_switch_with_cleared_chat_ids() -> None:
    with _test_session() as db:
        _seed(db)
        row = _create(
            db,
            feishu_notify={"enabled": True, "binding_id": "bind_a", "chat_ids": ["oc_group_a"]},
        )

        updated = update_scheduled_task(
            db,
            row,
            ScheduledTaskUpdateRequest(
                tenant_id="tenant_demo",
                metadata={
                    "feishu_notify": {
                        "enabled": True,
                        "binding_id": "bind_b",
                        "app_id": "cli_bind_b",
                        "app_name": "B 应用",
                        "chat_ids": [],
                    }
                },
            ),
            db.get(User, "user_demo"),
        )

        notify = updated.metadata_json["feishu_notify"]
        assert notify["binding_id"] == "bind_b"
        assert notify["chat_ids"] == []


def test_update_scheduled_task_keeps_binding_when_metadata_omitted() -> None:
    """只改标题的局部更新不得丢掉已绑定的应用。"""

    with _test_session() as db:
        _seed(db)
        row = _create(
            db,
            feishu_notify={"enabled": True, "binding_id": "bind_a", "chat_ids": ["oc_group_a"]},
        )

        updated = update_scheduled_task(
            db,
            row,
            ScheduledTaskUpdateRequest(tenant_id="tenant_demo", title="价格巡检（改）"),
            db.get(User, "user_demo"),
        )

        assert updated.title == "价格巡检（改）"
        assert updated.metadata_json["feishu_notify"]["binding_id"] == "bind_a"
        assert updated.metadata_json["feishu_notify"]["chat_ids"] == ["oc_group_a"]


def test_update_scheduled_task_rejects_foreign_feishu_binding() -> None:
    with _test_session() as db:
        _seed(db)
        db.add(Tenant(id="tenant_other", name="Other"))
        db.add(_binding("bind_other", name="他租户应用", tenant_id="tenant_other"))
        db.commit()
        row = _create(db, feishu_notify={"enabled": True, "binding_id": "bind_a"})

        with pytest.raises(HTTPException) as excinfo:
            update_scheduled_task(
                db,
                row,
                ScheduledTaskUpdateRequest(
                    tenant_id="tenant_demo",
                    metadata={"feishu_notify": {"enabled": True, "binding_id": "bind_other"}},
                ),
                db.get(User, "user_demo"),
            )

        assert excinfo.value.status_code == 400
        assert "所选飞书应用不存在或已停用" in excinfo.value.detail

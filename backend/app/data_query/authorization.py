"""数据源授权：员工可用数据源集合的统一计算入口。"""

from __future__ import annotations

from sqlmodel import Session, select

from app.data_query.models import DataSource
from app.db.models import AgentResourceBinding


def authorized_data_source_ids(
    db: Session,
    tenant_id: str,
    agent_id: str,
    *,
    include_inactive: bool = False,
) -> set[str]:
    """员工可用的数据源 ID 集合（绑定 active ∩ 数据源 active）。

    include_inactive=True 时把未删除的 inactive 绑定也计入（管理视角）。
    """
    active_source_ids = {
        row.id
        for row in db.exec(
            select(DataSource).where(
                DataSource.tenant_id == tenant_id,
                DataSource.status == "active",
            )
        ).all()
    }

    bindings = db.exec(
        select(AgentResourceBinding).where(
            AgentResourceBinding.tenant_id == tenant_id,
            AgentResourceBinding.agent_id == agent_id,
            AgentResourceBinding.resource_type == "data_source",
            AgentResourceBinding.status != "deleted",
        )
    ).all()

    bound_ids = {
        binding.resource_id
        for binding in bindings
        if include_inactive or binding.status == "active"
    }
    return bound_ids & active_source_ids

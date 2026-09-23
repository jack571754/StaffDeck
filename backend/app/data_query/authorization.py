"""数据源授权：员工可用数据源集合的统一计算入口。

由租户 UIConfig.data_query_grant_all 开关驱动两种模式：

- 关闭（白名单）：员工绑定 status=active 的数据源 ∩ 未停用数据源
- 开启（默认全量）：全部未停用数据源 − 员工绑定 status=inactive 的（排除集合）

绑定行存在性 = 员工数据权限区的勾选集合，status 随全局模式翻转：
白名单勾选 → active；全量勾选 → inactive 表示排除。开关双向切换无损。
"""

from __future__ import annotations

from sqlmodel import Session, select

from app.data_query.models import DataSource
from app.db.models import AgentResourceBinding, UIConfig


def authorized_data_source_ids(
    db: Session,
    tenant_id: str,
    agent_id: str,
    *,
    include_inactive: bool = False,
) -> set[str]:
    """员工可用的数据源 ID 集合。

    include_inactive 仅作用于白名单模式：True 时把未删除的 inactive 绑定也
    计入（管理视角，如工具可见性展示），执行链路保持 False（仅 active）。
    grant_all 模式下排除集合始终是 inactive 绑定，不受该参数影响。
    """
    ui_row = db.get(UIConfig, tenant_id)
    grant_all = bool(ui_row.data_query_grant_all) if ui_row is not None else False

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

    if grant_all:
        excluded = {
            binding.resource_id
            for binding in bindings
            if binding.status == "inactive"
        }
        return active_source_ids - excluded

    bound_ids = {
        binding.resource_id
        for binding in bindings
        if include_inactive or binding.status == "active"
    }
    return bound_ids & active_source_ids

"""飞书应用（ChannelBinding）解析收口。

一个租户可以接入多个飞书自建应用，每个应用是一条 ``channel == "feishu"`` 的
``ChannelBinding``。定时任务推送、群列表拉取都必须先确定"用哪个应用"，本模块
是唯一的解析入口，避免各处各写一份"取最新一条 active"的逻辑。

约定：显式指定的应用失效时报错而非回退。静默回退会把卡片发到另一个企业应用，
且调用方与用户都不知情，是最坏的失败模式。
"""

from __future__ import annotations

from sqlmodel import Session, select

from app.db.models import ChannelBinding

FEISHU_CHANNEL = "feishu"
BINDING_UNAVAILABLE_MESSAGE = "所选飞书应用不存在或已停用"


def list_tenant_feishu_bindings(db: Session, tenant_id: str) -> list[ChannelBinding]:
    """列出该租户的全部飞书应用，active 优先，其余按创建时间倒序。"""

    rows = db.exec(
        select(ChannelBinding)
        .where(
            ChannelBinding.tenant_id == tenant_id,
            ChannelBinding.channel == FEISHU_CHANNEL,
        )
        .order_by(ChannelBinding.created_at.desc())
    ).all()
    # active 优先；组内保持 created_at 倒序（SQL 已排好，sorted 稳定）。
    return sorted(rows, key=lambda row: row.status != "active")


def resolve_feishu_binding(
    db: Session,
    tenant_id: str,
    binding_id: str | None,
) -> tuple[ChannelBinding | None, str | None]:
    """按显式 binding_id 或"最新 active"解析飞书应用。

    返回 ``(binding, error_reason)``，二者恰有一个非空。
    """

    explicit = (binding_id or "").strip()
    if explicit:
        binding = db.get(ChannelBinding, explicit)
        if (
            binding is None
            or binding.tenant_id != tenant_id
            or binding.channel != FEISHU_CHANNEL
            or binding.status != "active"
        ):
            return None, BINDING_UNAVAILABLE_MESSAGE
        return binding, None

    fallback = db.exec(
        select(ChannelBinding)
        .where(
            ChannelBinding.tenant_id == tenant_id,
            ChannelBinding.channel == FEISHU_CHANNEL,
            ChannelBinding.status == "active",
        )
        .order_by(ChannelBinding.created_at.desc())
    ).first()
    if fallback is None:
        return None, f"未找到租户 {tenant_id} 有效的飞书应用绑定，请先在渠道管理中配置"
    return fallback, None

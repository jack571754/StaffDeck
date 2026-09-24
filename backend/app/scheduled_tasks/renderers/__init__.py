"""Feishu card renderers registry for scheduled tasks."""

from __future__ import annotations

from app.scheduled_tasks.renderers.base import CardPayload, CardRenderer
from app.scheduled_tasks.renderers.generic_table import build_generic_feishu_card
from app.scheduled_tasks.renderers.sales_card import build_sales_feishu_card

CARD_RENDERERS: dict[str, CardRenderer] = {
    "sales_card": build_sales_feishu_card,
    "sales_card_v2": build_sales_feishu_card,
    "sales": build_sales_feishu_card,
    "generic_table": build_generic_feishu_card,
    "table": build_generic_feishu_card,
}


def get_card_renderer(name: str | None = None) -> CardRenderer:
    """Retrieve card renderer by registered name, falling back to generic table renderer."""
    if not name:
        return build_sales_feishu_card
    key = str(name).strip().lower()
    return CARD_RENDERERS.get(key, build_generic_feishu_card)


__all__ = [
    "CARD_RENDERERS",
    "CardPayload",
    "CardRenderer",
    "build_generic_feishu_card",
    "build_sales_feishu_card",
    "get_card_renderer",
]

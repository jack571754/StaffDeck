"""Common base utilities and types for Feishu card renderers."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

CardPayload = dict[str, Any]
CardRenderer = Callable[..., CardPayload]


def fmt_val_styled(v: Any, is_core: bool = False) -> str:
    """Format numerical values with 万 unit and optional bold styling."""
    try:
        f = float(v or 0)
        if abs(f) < 0.0001:
            return "<font color='grey'>0.00万</font>"
        txt = f"{f:.2f}万"
        return f"**{txt}**" if is_core else txt
    except (ValueError, TypeError):
        return "<font color='grey'>0.00万</font>"


def fmt_diff_styled(v: Any, is_core: bool = False) -> str:
    """Format difference/increment values with sign, color, and optional bold."""
    try:
        f = float(v or 0)
        if abs(f) < 0.0001:
            return "<font color='grey'>0.00万</font>"
        sign = "+" if f > 0 else ""
        txt = f"{sign}{f:.2f}万"
        color = "green" if f > 0 else "red"
        body = f"**{txt}**" if is_core else txt
        return f"<font color='{color}'>{body}</font>"
    except (ValueError, TypeError):
        return "<font color='grey'>0.00万</font>"

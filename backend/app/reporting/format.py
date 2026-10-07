"""Neutral value formatters for reports.

These intentionally do NOT reuse ``scheduled_tasks.renderers.base.fmt_val_styled`` /
``fmt_diff_styled``: those emit Feishu colour markup (``<font color='green'>``),
which is meaningless in HTML and unsafe to inline. They also keep their existing
call sites untouched.

The "万" unit convention matches the Feishu cards so the same dataset reads the
same way in both places.
"""

from __future__ import annotations

from typing import Any

from app.reporting.document import DeltaTone

DEFAULT_UNIT = "万"
ZERO_EPSILON = 0.0001


def _as_float(value: Any) -> float | None:
    try:
        result = float(value or 0)
    except (TypeError, ValueError):
        return None
    return result


def to_float(value: Any) -> float:
    """Best-effort float conversion; unparseable or missing values become ``0.0``."""
    result = _as_float(value)
    return 0.0 if result is None else result


def format_metric_value(value: Any, *, unit: str = DEFAULT_UNIT, digits: int = 2) -> str:
    """Format a magnitude, e.g. ``1.234`` -> ``"1.23万"``."""
    number = _as_float(value)
    if number is None or abs(number) < ZERO_EPSILON:
        return f"{0:.{digits}f}{unit}"
    return f"{number:.{digits}f}{unit}"


def format_metric_delta(
    value: Any, *, unit: str = DEFAULT_UNIT, digits: int = 2
) -> tuple[str, DeltaTone]:
    """Format a delta plus its tone, e.g. ``-0.4`` -> ``("-0.40万", "down")``."""
    number = _as_float(value)
    if number is None or abs(number) < ZERO_EPSILON:
        return f"{0:.{digits}f}{unit}", "flat"
    sign = "+" if number > 0 else ""
    tone: DeltaTone = "up" if number > 0 else "down"
    return f"{sign}{number:.{digits}f}{unit}", tone


def format_number(value: Any, *, digits: int = 2) -> str:
    """Format a plain number with no unit suffix."""
    number = _as_float(value)
    if number is None:
        return ""
    return f"{number:.{digits}f}"


def format_cell(value: Any) -> str:
    """Format an arbitrary cell for display. ``None`` becomes an em dash."""
    if value is None:
        return "—"
    if isinstance(value, bool):
        return "是" if value else "否"
    if isinstance(value, float):
        return format_number(value)
    return str(value)


def format_bytes(size: int) -> str:
    """Human-readable byte size."""
    value = float(size)
    for unit in ("B", "KB", "MB", "GB"):
        if abs(value) < 1024 or unit == "GB":
            return f"{value:.1f}{unit}" if unit != "B" else f"{int(value)}B"
        value /= 1024
    return f"{value:.1f}GB"

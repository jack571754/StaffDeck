"""Pure function date resolver for relative date expressions in natural language.

Supports expressions such as:
- Single date: "今天", "昨天", "前天", "2026-09-20", "2026/09/20", "2026年9月20日"
- Ranges: "近7天", "过去30天", "本周", "上周", "本月", "上月"
- Period comparison: previous day/week/month/year calculation
"""

from __future__ import annotations

import calendar
import re
from datetime import UTC, date, datetime, timedelta


def _normalize_base_date(base_date: date | None = None) -> date:
    if base_date is not None:
        return base_date
    return datetime.now(tz=UTC).date()


def _parse_explicit_date(text: str) -> date | None:
    """Parse common explicit date formats."""
    text = text.strip()
    # YYYY-MM-DD
    m1 = re.match(r"^(\d{4})-(\d{1,2})-(\d{1,2})$", text)
    if m1:
        y, m, d = int(m1.group(1)), int(m1.group(2)), int(m1.group(3))
        return date(y, m, d)

    # YYYY/MM/DD
    m2 = re.match(r"^(\d{4})/(\d{1,2})/(\d{1,2})$", text)
    if m2:
        y, m, d = int(m2.group(1)), int(m2.group(2)), int(m2.group(3))
        return date(y, m, d)

    # YYYY年M月D日
    m3 = re.match(r"^(\d{4})年(\d{1,2})月(\d{1,2})日?$", text)
    if m3:
        y, m, d = int(m3.group(1)), int(m3.group(2)), int(m3.group(3))
        return date(y, m, d)

    return None


def resolve_date_expression(expr: str, base_date: date | None = None) -> str:
    """Resolve a relative or explicit date string into an ISO YYYY-MM-DD string.

    If not resolvable, returns the original expression unchanged.
    """
    if not expr:
        return ""
    text = expr.strip()
    explicit = _parse_explicit_date(text)
    if explicit:
        return explicit.isoformat()

    today = _normalize_base_date(base_date)
    lower = text.lower()

    if text in {"今天", "当日", "本日"} or lower == "today":
        return today.isoformat()
    if text in {"昨天", "昨日"} or lower == "yesterday":
        return (today - timedelta(days=1)).isoformat()
    if text in {"前天", "前日"}:
        return (today - timedelta(days=2)).isoformat()
    if text in {"大前天"}:
        return (today - timedelta(days=3)).isoformat()

    # Pass through
    return text


def resolve_date_range(expr: str, base_date: date | None = None) -> tuple[str, str]:
    """Resolve a date range expression into (start_date, end_date) in ISO format.

    Supports:
    - "近N天" / "过去N天" / "最近N天" (inclusive: today - (N-1) to today)
    - "本周" (Monday to Sunday)
    - "上周" (Monday to Sunday of last week)
    - "本月" (1st to last day of current month)
    - "上月" (1st to last day of previous month)
    """
    today = _normalize_base_date(base_date)
    text = expr.strip()

    # 近/过去/最近 N 天
    m_days = re.match(r"^(?:近|过去|最近)\s*(\d+)\s*天$", text)
    if m_days:
        n = int(m_days.group(1))
        if n <= 0:
            return today.isoformat(), today.isoformat()
        start = today - timedelta(days=n - 1)
        return start.isoformat(), today.isoformat()

    if text in {"本周", "这周", "当前周"}:
        # weekday(): Monday is 0 and Sunday is 6
        start = today - timedelta(days=today.weekday())
        end = start + timedelta(days=6)
        return start.isoformat(), end.isoformat()

    if text in {"上周", "上星期", "上一周"}:
        start = today - timedelta(days=today.weekday() + 7)
        end = start + timedelta(days=6)
        return start.isoformat(), end.isoformat()

    if text in {"本月", "这个月", "当前月"}:
        start = date(today.year, today.month, 1)
        _, last_day = calendar.monthrange(today.year, today.month)
        end = date(today.year, today.month, last_day)
        return start.isoformat(), end.isoformat()

    if text in {"上月", "上个月", "上一月"}:
        first_of_this_month = date(today.year, today.month, 1)
        last_day_of_prev_month = first_of_this_month - timedelta(days=1)
        start = date(last_day_of_prev_month.year, last_day_of_prev_month.month, 1)
        return start.isoformat(), last_day_of_prev_month.isoformat()

    # Fallback: single date resolved as both start and end
    single = resolve_date_expression(text, base_date=today)
    return single, single


def compute_prev_period_date(target_date_str: str, period: str = "day") -> str:
    """Compute the comparison date for a given ISO date string.

    Period can be "day", "week", "month", or "year".
    """
    dt = datetime.strptime(target_date_str.strip(), "%Y-%m-%d").replace(tzinfo=UTC).date()

    if period == "day":
        return (dt - timedelta(days=1)).isoformat()
    if period == "week":
        return (dt - timedelta(days=7)).isoformat()
    if period == "month":
        # Decrement month
        if dt.month == 1:
            prev_year = dt.year - 1
            prev_month = 12
        else:
            prev_year = dt.year
            prev_month = dt.month - 1
        _, max_days = calendar.monthrange(prev_year, prev_month)
        prev_day = min(dt.day, max_days)
        return date(prev_year, prev_month, prev_day).isoformat()
    if period == "year":
        prev_year = dt.year - 1
        _, max_days = calendar.monthrange(prev_year, dt.month)
        prev_day = min(dt.day, max_days)
        return date(prev_year, dt.month, prev_day).isoformat()

    return (dt - timedelta(days=1)).isoformat()

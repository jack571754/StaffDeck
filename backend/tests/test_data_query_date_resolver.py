from __future__ import annotations

from datetime import date

from app.data_query.date_resolver import (
    compute_prev_period_date,
    resolve_date_expression,
    resolve_date_range,
)


def test_resolve_single_dates():
    base = date(2026, 9, 21)  # Monday

    assert resolve_date_expression("今天", base_date=base) == "2026-09-21"
    assert resolve_date_expression("当日", base_date=base) == "2026-09-21"
    assert resolve_date_expression("today", base_date=base) == "2026-09-21"

    assert resolve_date_expression("昨天", base_date=base) == "2026-09-20"
    assert resolve_date_expression("昨日", base_date=base) == "2026-09-20"
    assert resolve_date_expression("yesterday", base_date=base) == "2026-09-20"

    assert resolve_date_expression("前天", base_date=base) == "2026-09-19"
    assert resolve_date_expression("大前天", base_date=base) == "2026-09-18"


def test_resolve_exact_dates():
    base = date(2026, 9, 21)

    assert resolve_date_expression("2026-09-15", base_date=base) == "2026-09-15"
    assert resolve_date_expression("2026/09/15", base_date=base) == "2026-09-15"
    assert resolve_date_expression("2026年9月15日", base_date=base) == "2026-09-15"
    assert resolve_date_expression("2026年09月15日", base_date=base) == "2026-09-15"


def test_resolve_date_ranges():
    base = date(2026, 9, 21)  # 2026-09-21 is Monday

    # 近7天 (inclusive: 2026-09-15 ~ 2026-09-21)
    start, end = resolve_date_range("近7天", base_date=base)
    assert start == "2026-09-15"
    assert end == "2026-09-21"

    # 过去30天
    start30, end30 = resolve_date_range("过去30天", base_date=base)
    assert end30 == "2026-09-21"
    assert start30 == "2026-08-23"

    # 最近3天
    start3, end3 = resolve_date_range("最近3天", base_date=base)
    assert start3 == "2026-09-19"
    assert end3 == "2026-09-21"

    # 本周 (Monday to Sunday)
    w_start, w_end = resolve_date_range("本周", base_date=base)
    assert w_start == "2026-09-21"
    assert w_end == "2026-09-27"

    # 上周
    lw_start, lw_end = resolve_date_range("上周", base_date=base)
    assert lw_start == "2026-09-14"
    assert lw_end == "2026-09-20"

    # 本月
    m_start, m_end = resolve_date_range("本月", base_date=base)
    assert m_start == "2026-09-01"
    assert m_end == "2026-09-30"

    # 上月
    lm_start, lm_end = resolve_date_range("上月", base_date=base)
    assert lm_start == "2026-08-01"
    assert lm_end == "2026-08-31"


def test_compute_prev_period_date():
    assert compute_prev_period_date("2026-09-21", period="day") == "2026-09-20"
    assert compute_prev_period_date("2026-09-21", period="week") == "2026-09-14"
    assert compute_prev_period_date("2026-09-21", period="month") == "2026-08-21"
    assert compute_prev_period_date("2026-03-31", period="month") == "2026-02-28"
    assert compute_prev_period_date("2026-09-21", period="year") == "2025-09-21"


def test_fallback_and_passthrough():
    base = date(2026, 9, 21)
    # Unknown expression returns the original string or fallback
    assert resolve_date_expression("未知时间", base_date=base) == "未知时间"
    assert resolve_date_expression("", base_date=base) == ""

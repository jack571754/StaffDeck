"""Tests for the reporting HTML renderer (app/reporting).

Focus areas, in order of importance:
1. Injection safety — the JSON island is the only injection surface, and the single
   escaping exit must cover every value that reaches the markup.
2. Self-containment — the report must work offline with no external resources, and
   must not need any CSP relaxation beyond what `_HTML_INLINE_CSP` already allows.
3. Neutral defaults — an unspecified renderer must not produce a sales report.
4. Structural validation — bad documents fail loudly instead of rendering broken HTML.
"""

from __future__ import annotations

import base64
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from app.reporting import (
    ReportBlock,
    ReportDataset,
    ReportDocument,
    ReportDocumentError,
    ReportMeta,
    build_generic_table_report,
    build_sales_report,
    get_report_renderer,
    json_safe,
    render_report_html,
)
from app.reporting.assets import REPORT_CSS, REPORT_JS
from app.reporting.html import ISLAND_ID
from app.reporting.renderers import DEFAULT_RENDERER_NAME
from app.reporting.renderers import build_generic_table_report as generic

GENERATED_AT = datetime(2026, 9, 28, 6, 30, tzinfo=UTC)


def _doc(
    *,
    datasets: list[ReportDataset] | None = None,
    blocks: list[ReportBlock] | None = None,
    title: str = "测试报告",
) -> ReportDocument:
    return ReportDocument(
        meta=ReportMeta(title=title, generated_at=GENERATED_AT, tenant_id="tenant_demo"),
        datasets=list(datasets or []),
        blocks=list(blocks or []),
    )


# ------------------------------------------------------------------ json_safe


def test_json_safe_converts_database_types():
    payload = json_safe(
        {
            "amount": Decimal("12.34"),
            # Naive on purpose: pymysql hands back naive datetimes for MySQL DATETIME.
            "when": datetime(2026, 9, 28, 14, 30),  # noqa: DTZ001
            "day": date(2026, 9, 28),
            "blob": b"abc",
            "nested": [Decimal("1.5")],
        }
    )
    assert payload["amount"] == 12.34
    assert payload["when"] == "2026-09-28T14:30:00"
    assert payload["day"] == "2026-09-28"
    assert payload["blob"] == base64.b64encode(b"abc").decode("ascii")
    assert payload["nested"] == [1.5]


def test_json_safe_stringifies_non_finite_floats():
    # json.dumps emits bare NaN/Infinity for these, which is not valid JSON.
    assert json_safe(float("nan")) == "nan"
    assert json_safe(float("inf")) == "inf"


def test_json_safe_is_deterministic_for_sets():
    assert json_safe({"b", "a", "c"}) == json_safe({"c", "b", "a"})


def test_json_safe_keeps_bools_as_bools():
    assert json_safe(True) is True


# ------------------------------------------------------------------ escaping


def _island_of(html: str) -> str:
    return html.split(f'id="{ISLAND_ID}">', 1)[1].split("</script>", 1)[0]


def test_script_payload_is_escaped_in_body_and_island():
    payload = "<script>alert(1)</script>"
    # The payload is used as a *column key* so it reaches both surfaces: the table
    # header (body markup) and the island's column metadata.
    doc = build_generic_table_report(
        [{payload: "值", "note": "</script><script>alert(2)</script>"}],
        title="转义",
        generated_at=GENERATED_AT,
    )
    html = render_report_html(doc)

    assert payload not in html
    assert "<script>alert(2)" not in html
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html
    assert "&lt;/script&gt;" in html
    # Every '<' in the island is unicode-escaped, which also rules out the '<!--'
    # parser state that can swallow the element's real closing tag.
    assert "\\u003c" in _island_of(html)


def test_island_never_contains_a_raw_angle_bracket():
    doc = build_generic_table_report(
        [{"<img src=x onerror=alert(1)>": "<b>值</b>"}], title="岛", generated_at=GENERATED_AT
    )
    island = _island_of(render_report_html(doc))
    assert "<" not in island
    assert ">" not in island
    assert "\\u003c" in island


def test_title_is_escaped():
    doc = _doc(title="</title><script>alert(1)</script>")
    html = render_report_html(doc)
    assert "</title><script>alert(1)</script>" not in html
    assert "&lt;/title&gt;" in html


def test_inlined_assets_cannot_break_out_of_their_elements():
    assert "</" not in REPORT_CSS
    assert "</" not in REPORT_JS


# ------------------------------------------------------------------ self-contained


def test_report_has_no_external_resources():
    doc = build_sales_report(
        [
            {"category": "大盘", "item": "电商整体", "净销_万": 10.0, "运营净销_万": 6.0,
             "达播净销_万": 4.0},
            {"category": "24小时环比", "item": "08:00", "运营净销_万": 3.0, "运营增量_万": 2.0},
        ],
        title="自包含",
        generated_at=GENERATED_AT,
    )
    html = render_report_html(doc)
    assert "http://" not in html
    assert "https://" not in html
    assert "<link" not in html
    assert "<script src" not in html


# ------------------------------------------------------------------ charts


def test_charts_render_as_inline_svg():
    rows = [
        {"category": "24小时环比", "item": "08:00", "运营净销_万": 3.0, "运营增量_万": 2.0},
        {"category": "24小时环比", "item": "09:00", "运营净销_万": 5.0, "运营增量_万": 4.0},
    ]
    html = render_report_html(
        build_sales_report(rows, title="图", generated_at=GENERATED_AT)
    )
    assert "<rect" in html
    assert "<svg" in html
    # Native hover tooltips instead of a JS tooltip library.
    assert "<title>" in html


def test_line_chart_uses_polyline():
    dataset = ReportDataset(
        id="d",
        columns=["hour", "v"],
        rows=[{"hour": "a", "v": 1.0}, {"hour": "b", "v": 3.0}],
    )
    doc = _doc(
        datasets=[dataset],
        blocks=[ReportBlock(kind="line_chart", dataset_id="d", x="hour", y=["v"])],
    )
    html = render_report_html(doc)
    assert "<polyline" in html
    assert "<rect" not in html


# ------------------------------------------------------------------ validation


def test_unknown_block_kind_is_rejected():
    doc = _doc(blocks=[ReportBlock(kind="bogus")])  # type: ignore[arg-type]
    with pytest.raises(ReportDocumentError, match="未知类型"):
        render_report_html(doc)


def test_dangling_dataset_reference_is_rejected():
    doc = _doc(blocks=[ReportBlock(kind="table", dataset_id="missing")])
    with pytest.raises(ReportDocumentError, match="不存在的数据集"):
        render_report_html(doc)


def test_table_block_requires_dataset_id():
    doc = _doc(datasets=[ReportDataset(id="d", columns=[], rows=[])], blocks=[
        ReportBlock(kind="table"),
    ])
    with pytest.raises(ReportDocumentError, match="缺少 dataset_id"):
        render_report_html(doc)


def test_chart_block_requires_axis_columns():
    dataset = ReportDataset(id="d", columns=["hour"], rows=[{"hour": "a"}])
    doc = _doc(datasets=[dataset], blocks=[ReportBlock(kind="bar_chart", dataset_id="d")])
    with pytest.raises(ReportDocumentError, match="缺少 x 轴列"):
        render_report_html(doc)


def test_unknown_column_reference_is_rejected():
    dataset = ReportDataset(id="d", columns=["a"], rows=[{"a": 1}])
    doc = _doc(
        datasets=[dataset],
        blocks=[ReportBlock(kind="table", dataset_id="d", columns=["nope"])],
    )
    with pytest.raises(ReportDocumentError, match="不存在的列"):
        render_report_html(doc)


def test_empty_title_is_rejected():
    with pytest.raises(ReportDocumentError, match="标题"):
        render_report_html(_doc(title="   "))


# ------------------------------------------------------------------ neutral defaults


def test_default_renderer_is_neutral_generic_table():
    assert get_report_renderer(None) is generic
    assert get_report_renderer("") is generic
    assert get_report_renderer("does-not-exist") is generic
    assert DEFAULT_RENDERER_NAME == "generic_table"


def test_generic_renderer_never_produces_a_sales_report():
    # Non-sales rows must not be silently turned into a KPI/sales layout.
    doc = build_generic_table_report(
        [{"category": "大盘", "item": "电商整体", "净销_万": 10.0}],
        title="中性",
        generated_at=GENERATED_AT,
    )
    kinds = [block.kind for block in doc.blocks]
    assert kinds == ["table"]
    assert "kpi_row" not in kinds


def test_sales_renderer_falls_back_for_non_sales_rows():
    doc = build_sales_report([{"a": 1, "b": 2}], title="回退", generated_at=GENERATED_AT)
    assert [block.kind for block in doc.blocks] == ["table"]


# ------------------------------------------------------------------ shaping


def test_truncation_note_reports_the_original_row_count():
    rows = [{"a": index} for index in range(20)]
    doc = build_generic_table_report(rows, title="截断", max_rows=5, generated_at=GENERATED_AT)
    assert len(doc.datasets[0].rows) == 5
    assert doc.datasets[0].source["row_count"] == 20
    assert "仅展示前 5 行（共 20 行）" in render_report_html(doc)


def test_empty_rows_render_a_note_instead_of_crashing():
    doc = build_generic_table_report([], title="空", generated_at=GENERATED_AT)
    assert [block.kind for block in doc.blocks] == ["note"]
    assert "没有查询到数据" in render_report_html(doc)


def test_column_labels_allow_repeated_display_names():
    doc = build_sales_report(
        [
            {"category": "大盘", "item": "电商整体", "净销_万": 10.0, "运营净销_万": 6.0,
             "达播净销_万": 4.0},
        ],
        title="标签",
        generated_at=GENERATED_AT,
    )
    dataset = doc.datasets[0]
    # Three distinct keys share the same display label without colliding.
    assert dataset.label_for("net_diff") == "环比净销"
    assert dataset.label_for("ops_diff") == "环比净销"
    assert len({dataset.label_for(c) for c in dataset.columns}) < len(dataset.columns)


def test_first_push_zeroes_deltas_and_suppresses_rankings():
    rows = [
        {"category": "大盘", "item": "电商整体", "净销_万": 10.0, "运营净销_万": 6.0,
         "达播净销_万": 4.0, "环比增量_万": 9.9},
        {"category": "店铺正增量Top5", "item": "店铺A", "净销_万": 3.0, "环比增量_万": 2.0},
    ]
    html = render_report_html(
        build_sales_report(rows, title="首推", is_first_push=True, generated_at=GENERATED_AT)
    )
    assert "当日首次播报" in html
    assert "9.90万" not in html
    assert "店铺正负增量动态排行（较上一时刻 Top 5）" not in html


def test_first_push_chart_drops_the_previous_day_series():
    rows = [
        {"category": "大盘", "item": "电商整体", "净销_万": 10.0},
        {"category": "24小时环比", "item": "08:00", "运营净销_万": 3.0, "运营增量_万": 2.0},
    ]
    first_push = build_sales_report(rows, title="首推图", is_first_push=True,
                                    generated_at=GENERATED_AT)
    regular = build_sales_report(rows, title="常规图", is_first_push=False,
                                 generated_at=GENERATED_AT)
    chart_first = next(b for b in first_push.blocks if b.kind == "bar_chart")
    chart_regular = next(b for b in regular.blocks if b.kind == "bar_chart")
    assert chart_first.y == ["今日"]
    assert chart_regular.y == ["今日", "昨日"]


def test_numeric_columns_are_right_aligned_with_sort_keys():
    html = render_report_html(
        build_generic_table_report(
            [{"名称": "甲", "数量": 12.5}], title="对齐", generated_at=GENERATED_AT
        )
    )
    assert 'data-v="12.5"' in html
    assert 'class="num"' in html

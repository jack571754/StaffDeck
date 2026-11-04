"""Keep the sales breakdown limited to the approved summary rows."""

import pytest

from app.reporting.renderers.sales_report import build_sales_report
from app.scheduled_tasks.renderers.sales_card import build_sales_feishu_card


def test_all_approved_brand_rows_fit_on_one_page(monkeypatch):
    monkeypatch.setattr(
        "app.scheduled_tasks.renderers.sales_card.build_dabo_matrix_table_elements",
        lambda *args, **kwargs: [],
    )
    rows = [{"category": "大盘", "item": "电商整体", "净销_万": 100}]
    for brand in ("可复美", "可丽金"):
        for item in ("整体", "天猫", "抖音", "京东", "唯品会", "微信视频号", "小红书"):
            rows.append({"category": brand, "item": item, "净销_万": 1})
    rows.extend({"category": "其他品牌", "item": item, "净销_万": 1}
                for item in ("巨子生物", "可预", "分销", "以我"))
    card = build_sales_feishu_card(rows)
    table = next(element for element in card["card"]["body"]["elements"]
                 if element.get("tag") == "table"
                 and element["columns"][0]["name"] == "brand")
    assert len(table["rows"]) > 10
    assert table["page_size"] >= len(table["rows"])


@pytest.mark.parametrize("include_summary", [False, True])
def test_brand_breakdown_excludes_platform_totals_and_stream_details(
    monkeypatch, include_summary
):
    monkeypatch.setattr(
        "app.scheduled_tasks.renderers.sales_card.build_dabo_matrix_table_elements",
        lambda *args, **kwargs: [],
    )
    rows = [
        {"category": "24小时环比", "item": "10", "运营净销_万": 1},
        {"category": "平台", "item": "天猫", "净销_万": 100},
        *[
            {
                "category": platform,
                "item": f"{platform}:可复美:朱梓骁-可复美面霜双十一大破价！",
                "净销_万": 20,
            }
            for platform in ("抖音", "小红书", "快手")
        ],
    ]
    if include_summary:
        rows.extend([
            {"category": "大盘", "item": "电商整体", "净销_万": 100},
            {"category": "可复美", "item": "天猫", "净销_万": 50},
            *[
                {"category": "其他品牌", "item": name, "净销_万": 10}
                for name in ("可预", "分销", "以我")
            ],
        ])

    card = build_sales_feishu_card(rows)
    table = next(
        element for element in card["card"]["body"]["elements"]
        if element.get("tag") == "table"
        and element["columns"][0]["name"] == "brand"
    )
    expected = ["电商整体", "天猫可复美", "可预", "分销", "以我"] if include_summary else []
    assert [row["brand"].strip("*") for row in table["rows"]] == expected
    if include_summary:
        assert [row["brand"] for row in table["rows"]][-3:] == [
            "**可预**", "**分销**", "**以我**",
        ]
        assert table["rows"][1]["net_sales"] == "50.00万"

    report = build_sales_report(rows)
    dataset = next(dataset for dataset in report.datasets if dataset.id == "brand_detail")
    assert [row["brand"] for row in dataset.rows] == expected


def test_sales_report_sorts_positive_increment_shops_descending():
    rows = [
        {"category": "大盘", "item": "电商整体", "净销_万": 1000.0, "环比增量_万": 50.0},
        {"category": "店铺正增量Top5", "item": "京东-店铺A", "净销_万": 70.0, "环比增量_万": 7.0},
        {"category": "店铺正增量Top5", "item": "天猫-店铺B", "净销_万": 200.0, "环比增量_万": 11.0},
        {"category": "店铺正增量Top5", "item": "抖音-店铺C", "净销_万": 120.0, "环比增量_万": 10.0},
    ]
    report = build_sales_report(rows, is_first_push=False)
    rank_block = next(b for b in report.blocks if b.kind == "markdown" and "正增量领跑 Top 5" in b.text)
    assert rank_block.text.index("天猫-店铺B") < rank_block.text.index("抖音-店铺C") < rank_block.text.index("京东-店铺A")

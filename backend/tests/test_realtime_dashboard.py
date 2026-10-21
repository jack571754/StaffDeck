"""Regression checks for campaign dashboard totals and business splits."""

import json
import re
from io import BytesIO

from app.reporting.realtime_dashboard_syncer import (
    build_realtime_sales_dashboard_html,
    fetch_all_channel_daren_top5,
    parse_pipeline_query_metrics,
)


def test_influencer_ranking_combines_channels_and_brands_before_top_five(monkeypatch):
    queries = []
    batches = [
        [{"author": "共同达人", "platform": "抖音", "brand": "可复美", "net_wan": 30,
          "gmv_wan": 50},
         {"author": "抖音达人", "platform": "抖音", "brand": "可丽金", "net_wan": 80},
         {"author": "共同达人", "platform": "抖音", "brand": "可丽金", "net_wan": 20}],
        [{"author": "李佳琦", "platform": "天猫", "brand": "可复美", "net_wan": 100},
         {"author": "共同达人", "platform": "天猫", "brand": "可复美", "net_wan": 40},
         {"author": "通用", "platform": "天猫", "net_wan": 999}],
        [{"author": "小红书达人", "platform": "小红书", "net_wan": 70},
         {"author": "快手达人", "platform": "快手", "net_wan": 60},
         {"author": "第六名", "platform": "快手", "net_wan": 10}],
    ]

    def query(request, timeout):
        queries.append(request.data.decode())
        return BytesIO(json.dumps({"data": batches[len(queries) - 1]}).encode())

    monkeypatch.setattr("urllib.request.urlopen", query)
    ranked = fetch_all_channel_daren_top5("http://localhost")
    assert [row["author"] for row in ranked] == [
        "李佳琦", "共同达人", "抖音达人", "小红书达人", "快手达人",
    ]
    assert ranked[1]["net_wan"] == 90
    assert ranked[1]["platform"] == "抖音 / 天猫"
    assert ranked[1]["brand"] == "可复美 / 可丽金"
    assert all("LIMIT 5" not in sql for sql in queries)
    assert "tm_product_realtime_jieguobiao" in queries[1]
    html = build_realtime_sales_dashboard_html({}, {"daren_top5": ranked})
    assert "全渠道 / 万元" in html
    assert "天猫 · 可复美" in html


def test_influencer_channel_failure_does_not_skip_remaining_channels(monkeypatch):
    calls = []

    def query(request, timeout):
        calls.append(request)
        if len(calls) == 1:
            raise OSError("unavailable")
        return BytesIO(json.dumps({"data": [{"author": "天猫达人", "platform": "天猫",
                                             "net_wan": 50}] if len(calls) == 2 else []}).encode())

    monkeypatch.setattr("urllib.request.urlopen", query)
    assert fetch_all_channel_daren_top5("http://localhost")[0]["author"] == "天猫达人"
    assert len(calls) == 3


def test_channels_preserve_real_totals_and_split_missing_values():
    metrics = parse_pipeline_query_metrics([
        {"category": "大盘", "item": "电商整体", "净销_万": 120},
        {"category": "可复美", "item": "天猫", "净销_万": 80,
         "运营净销_万": 30, "达播净销_万": 50},
        {"category": "可丽金", "item": "天猫", "净销_万": 20,
         "运营净销_万": 5, "达播净销_万": 15},
        {"category": "其他品牌", "item": "以我", "净销_万": 10},
    ])
    channels = {row["name"]: row for row in metrics["channels_all"]}
    assert channels["天猫渠道"]["gmv"] == 100
    assert channels["天猫渠道"]["ops"] == 35
    assert channels["天猫渠道"]["live"] == 65
    assert channels["以我"]["ops"] is None
    assert sum(row["gmv"] for row in channels.values()) == 120


def test_priority_shops_are_deduplicated_and_sorted():
    metrics = parse_pipeline_query_metrics([
        {"category": "重点店铺", "item": "天猫-店铺A", "净销_万": 100},
        {"category": "店铺正增量Top5", "item": "天猫-店铺A", "净销_万": 100},
        {"category": "店铺负增量Top5", "item": "京东-店铺B", "净销_万": 150},
    ])
    assert [row["name"] for row in metrics["shops"]] == ["京东-店铺B", "天猫-店铺A"]
    assert metrics["shops"][0]["ops"] is None


def test_dashboard_trend_has_four_toggleable_series_and_real_summary():
    html = build_realtime_sales_dashboard_html({
        "gmv_all": 100, "ops_all": 40, "live_all": 60,
        "hourly": [{"h": "00时", "v": 100, "yesterday": 80, "ops": 40, "live": 60}],
    }, {})
    for label in ("今日总净销", "昨日总净销", "今日达播", "今日自营"):
        assert label in html
    assert 'data-trend-series="yesterday"' in html
    assert "昨日同期" in html
    assert "trendTooltip" in html
    assert "stroke-dasharray" in html
    assert "24小时累计净销走势" in html


def test_dashboard_adds_real_self_operated_shops_without_duplicate_or_fake_delta():
    metrics = parse_pipeline_query_metrics([
        {"category": "重点店铺", "item": "抖音-可复美官方旗舰店", "净销_万": 50},
    ])
    html = build_realtime_sales_dashboard_html(metrics, {"shops": [
        {"shop_name": "可复美官方旗舰店", "net_wan": 60},
        {"shop_name": "可丽金官方旗舰店", "net_wan": 30, "order_cnt": 200},
    ]})
    shops = json.loads(re.search(r"var SHOPS_DATA = (.*);", html).group(1))
    assert len(shops) == 2
    assert shops[1]["ops"] == 30
    assert shops[1]["live"] is None
    assert shops[1]["mom"] is None
    assert "抖音自营实时数据" in html
    assert "<th>达播</th>" in html
    assert "var pageSize = 10" in html

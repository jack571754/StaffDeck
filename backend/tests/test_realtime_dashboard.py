"""Regression checks for campaign dashboard totals and business splits."""

import json
import re
from io import BytesIO

from app.reporting.realtime_dashboard_syncer import (
    build_realtime_sales_dashboard_html,
    fetch_all_channel_daren_top5,
    parse_pipeline_query_metrics,
)


def test_direct_dashboard_refresh_uses_the_shared_tmall_snapshot(monkeypatch, tmp_path):
    from types import SimpleNamespace

    from app.reporting import dashboard_shops, sales_metrics
    from app.reporting import realtime_dashboard_syncer as syncer

    link = SimpleNamespace(path="sales.html", is_revoked=False, tenant_id="tenant",
                           session_id="session", owner_id="owner")
    db = SimpleNamespace(get=lambda *args: link,
                         exec=lambda *args: SimpleNamespace(all=list))
    task = SimpleNamespace(metadata_json={"artifact_share_link_id": "link"})
    monkeypatch.setattr(sales_metrics, "fetch_influencer_sales_rows", lambda **kw: [])
    monkeypatch.setattr(syncer, "fetch_clickhouse_dy_order_metrics", lambda: {
        "daren_top5": [{"author": "错误来源", "net_wan": 999}],
    })
    monkeypatch.setattr(dashboard_shops, "fetch_task_all_shops", lambda *a: [])
    monkeypatch.setattr(syncer, "harness_task_workspace_path", lambda **kw: tmp_path)
    rows = [{"统计维度": "5_天猫达播同源明细", "品牌": "可复美", "达人": "李佳琦-挂链",
             "成交金额_万": 41.82, "净销_万": 22.53}]
    digest = syncer.sync_task_artifact_report(db, task, rows, hourly_series=[])
    assert digest
    html = (tmp_path / "sales.html").read_text(encoding="utf-8")
    assert "错误来源" not in html
    assert "李佳琦" in html
    assert "22.53" in html


def test_influencer_ranking_combines_channels_and_brands_before_top_five(monkeypatch):
    queries = []
    batches = [
        [{"author": "共同达人", "platform": "抖音", "brand": "可复美", "net_wan": 30,
          "gmv_wan": 50},
         {"author": "抖音达人", "platform": "抖音", "brand": "可丽金", "net_wan": 80},
         {"author": "共同达人", "platform": "抖音", "brand": "可丽金", "net_wan": 20}],
        [{"author": "李佳琦", "platform": "天猫", "brand": "可复美", "net_wan": 100},
         {"author": "李佳琦-助播", "platform": "天猫", "brand": "可复美", "net_wan": 50},
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
    # 李佳琦-助播 的 50 万并入李佳琦，合并后 150 万。
    assert ranked[0]["net_wan"] == 150
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
    assert ">达播<" in html
    assert ">数据更新时间<" in html
    assert "formatUpdateTimeAndDelay" in html
    assert "time-cell" in html
    assert "var pageSize = 10" in html


def test_aggregate_tmall_hourly_migration_deadline_boundary_inclusive():
    from datetime import date, datetime

    from app.reporting.dashboard_hourly import aggregate_tmall_hourly

    # Snapshot at 15:11:21 on 2026-10-26 (today)
    product = {
        "商品ID": "703682622213",
        "统计日期": datetime(2026, 10, 26, 15, 11, 21),
        "支付金额": 20000,
        "成功退款金额": 5000,
    }
    # Migration rule with deadline set to today (date object without time)
    migrations = [
        {
            "迁移ID": "703682622213",
            "迁移开始日期": datetime(2025, 3, 18, 0, 0),
            "迁移截止日期": date(2026, 10, 26),
            "迁移归属": "达播",
            "原始归属": "运营",
        }
    ]

    # The entire deadline day should be included in migration attribution
    totals = aggregate_tmall_hourly([product], [], migrations)
    assert totals == {("2026-10-26", 15): 1.5}

    # When attribution is 运营, it should not be counted under live
    migrations[0]["迁移归属"] = "运营"
    migrations[0]["原始归属"] = "达播"
    totals_ops = aggregate_tmall_hourly([product], [], migrations)
    assert totals_ops == {}


def test_influencer_ranking_scrollable_and_all_sales_included():
    from app.reporting.sales_metrics import get_top_influencers

    dabo_rows = [
        {"统计维度": "3_达人TOP18", "达人": f"达人_{i}", "净销": i * 10000, "成交金额": (i + 1) * 10000}
        for i in range(1, 10)
    ]
    all_daren = get_top_influencers(dabo_rows, limit=None)
    assert len(all_daren) == 9
    assert all_daren[0]["name"] == "达人_9"

    daren_list = [
        {"author": d["name"], "net_wan": d["net_wan"], "gmv_wan": d["gmv_wan"], "platform": "抖音", "brand": "可复美"}
        for d in all_daren
    ]
    html = build_realtime_sales_dashboard_html({}, {"daren_top5": daren_list})
    assert "<h2>达人排名</h2>" in html
    assert "tal-scroll-wrap" in html
    assert "tal-list" in html
    assert "达人_9" in html
    assert "达人_1" in html
    assert "有销达人全域贡献合计" in html

def test_douyin_self_operated_shops_fallback_when_clickhouse_unavailable():
    from app.reporting.realtime_dashboard_syncer import (
        build_realtime_sales_dashboard_html,
        parse_pipeline_query_metrics,
    )

    query_rows = [
        {"统计维度": "2_店铺汇总", "平台": "抖音", "平台店铺": "抖音-可复美官方旗舰店", "成交金额": "600000", "退款金额": "150000", "净销": "450000"},
        {"统计维度": "2_店铺汇总", "平台": "抖音", "平台店铺": "抖音-可丽金官方旗舰店", "成交金额": "400000", "退款金额": "100000", "净销": "300000"},
    ]
    metrics = parse_pipeline_query_metrics(query_rows)
    assert len(metrics["dy_shops"]) == 2
    assert metrics["dy_shops"][0]["shop_name"] == "可复美官方旗舰店"
    assert metrics["dy_shops"][0]["net_wan"] == 45.0
    assert metrics["dy_shops"][0]["gmv_wan"] == 60.0

    # ck_data has empty shops (simulating missing ClickHouse)
    html = build_realtime_sales_dashboard_html(metrics, {"shops": []})
    assert "抖音自营实时数据" in html
    assert "可复美官方旗舰店" in html
    assert "可丽金官方旗舰店" in html
    assert "净销 45.00万" in html
    assert "GMV 100.00万 · 退款 25.00万 · 净销 75.00万" in html
    assert "核心店铺数" in html
    assert "2<small>家</small>" in html


def test_influencer_ranking_includes_cross_platform_pipeline_rows():
    from types import SimpleNamespace

    import app.reporting.realtime_dashboard_syncer as syncer
    from app.reporting import dashboard_shops, sales_metrics

    link = SimpleNamespace(path="sales.html", is_revoked=False, tenant_id="tenant",
                           session_id="session", owner_id="owner")
    db = SimpleNamespace(get=lambda *args: link, exec=lambda *args: SimpleNamespace(all=list))
    task = SimpleNamespace(metadata_json={"artifact_share_link_id": "link"})

    rows = [
        # Tmall同源
        {"统计维度": "5_天猫达播同源明细", "品牌": "可复美", "达人": "李佳琦-挂链", "成交金额_万": 100.0, "净销_万": 50.0},
        # 抖音达人
        {"统计维度": "3_达人TOP18", "平台": "抖音", "平台店铺": "抖音-可丽金官方旗舰店", "达人": "农家小闹", "品牌": "可丽金", "成交金额": "200000", "净销": "150000"},
        {"统计维度": "3_达人TOP18", "平台": "抖音", "平台店铺": "抖音-可复美官方旗舰店", "达人": "亮哥", "品牌": "可复美", "成交金额": "100000", "净销": "80000"},
    ]

    import tempfile
    from pathlib import Path
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir)
        import pytest
        monkeypatch = pytest.MonkeyPatch()
        monkeypatch.setattr(sales_metrics, "fetch_influencer_sales_rows", lambda **kw: [])
        monkeypatch.setattr(syncer, "fetch_clickhouse_dy_order_metrics", lambda: {
            "shops": [], "channels": [], "zibo_shops": [], "daren_top5": [],
        })
        monkeypatch.setattr(dashboard_shops, "fetch_task_all_shops", lambda *a: [])
        monkeypatch.setattr(syncer, "harness_task_workspace_path", lambda **kw: tmp_path)

        digest = syncer.sync_task_artifact_report(db, task, rows, hourly_series=[])
        assert digest
        html = (tmp_path / "sales.html").read_text(encoding="utf-8")
        assert "李佳琦" in html
        assert "农家小闹" in html
        assert "亮哥" in html
        assert "抖音 · 可丽金" in html
        assert "抖音 · 可复美" in html
        monkeypatch.undo()

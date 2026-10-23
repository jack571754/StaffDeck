"""The matrix and influencer ranking must share one sales snapshot."""

from app.reporting import sales_metrics as metrics


def test_snapshot_replaces_legacy_ranking_and_matches_tmall_brands(monkeypatch):
    authors = [
        {"author": "李佳琦", "platform": "天猫", "brand": "可复美", "gmv_wan": 616.5, "net_wan": 365.23},
        {"author": "李佳琦", "platform": "天猫", "brand": "可丽金", "gmv_wan": 35.1, "net_wan": 22.41},
        {"author": "李佳琦-助播", "platform": "天猫", "brand": "可复美", "gmv_wan": 30, "net_wan": 18},
        {"author": "通用", "platform": "天猫", "brand": "可复美", "gmv_wan": 10, "net_wan": 5},
    ]
    monkeypatch.setattr(metrics, "fetch_influencer_sales_rows", lambda: authors)
    monkeypatch.setattr(metrics, "clickhouse_query_url", lambda: "http://localhost")
    from io import BytesIO
    monkeypatch.setattr("urllib.request.urlopen", lambda *a, **kw: BytesIO(b'{"data": []}'))
    rows = metrics.inject_dabo_matrix_rows([
        {"统计维度": "3_达人TOP18", "达人": "李佳琦", "净销": 3592800},
        {"category": "大盘", "item": "电商整体", "净销_万": 999},
    ])
    ranked = metrics.get_top_influencers([r for r in rows if metrics.is_dabo_row(r)])
    assert [r["name"] for r in ranked] == ["李佳琦", "李佳琦-助播"]
    assert ranked[0]["net_wan"] == 387.64
    assert ranked[0]["gmv_wan"] == 651.6
    matrix = {r["item"]: r for r in rows if r.get("category") == "达播矩阵"}
    assert matrix["天猫:可复美:李佳琦"]["净销_万"] == 365.23
    assert matrix["天猫:可丽金:李佳琦"]["净销_万"] == 22.41
    assert matrix["天猫"]["净销_万"] == 410.64
    assert matrix["天猫:可复美:二梯队"]["净销_万"] == 23
    assert any(r.get("item") == "电商整体" for r in rows)


def test_snapshot_ranks_after_combining_brands_and_channels(monkeypatch):
    authors = [
        {"author": "共同达人", "platform": "天猫", "brand": "可复美", "gmv_wan": 100, "net_wan": 60},
        {"author": "共同达人", "platform": "抖音", "brand": "可丽金", "gmv_wan": 50, "net_wan": 30},
        {"author": "抖音达人", "platform": "抖音", "brand": "可复美", "gmv_wan": 90, "net_wan": 80},
    ]
    monkeypatch.setattr(metrics, "fetch_influencer_sales_rows", lambda: authors)
    rows = metrics.inject_dabo_matrix_rows([])
    ranked = metrics.get_top_influencers([r for r in rows if metrics.is_dabo_row(r)])
    assert ranked[0]["name"] == "共同达人"
    assert ranked[0]["net_wan"] == 90
    matrix = {r["item"]: r for r in rows if r.get("category") == "达播矩阵"}
    assert matrix["抖音"]["净销_万"] == 110
    assert matrix["抖音:可丽金:共同达人"]["净销_万"] == 30


def test_live_snapshot_queries_use_attributed_tmall_and_no_top_limit(monkeypatch):
    from io import BytesIO
    queries = []
    monkeypatch.setattr(metrics, "clickhouse_query_url", lambda: "http://localhost")

    def query(request, timeout):
        queries.append(request.data.decode("utf-8"))
        return BytesIO(b'{"data": []}')

    monkeypatch.setattr("urllib.request.urlopen", query)
    assert metrics.fetch_influencer_sales_rows() == []
    assert len(queries) == 3
    assert "`归属` = '达播'" in queries[1]
    assert all("LIMIT" not in sql for sql in queries)


def test_matrix_visibility_uses_daily_totals_not_increment():
    rows = [
        {"category": "达播矩阵", "item": "累计有销售", "成交金额_万": 10,
         "净销_万": 8, "退款金额_万": 2, "环比增量_万": 0},
        {"category": "达播矩阵", "item": "仅有退款", "退款金额_万": 2,
         "净销_万": -2, "环比增量_万": 0},
        {"category": "达播矩阵", "item": "累计为空但有增量", "成交金额_万": 0,
         "净销_万": 0, "退款金额_万": 0, "环比增量_万": -8},
        {"category": "达播矩阵", "item": "全空", "净销_万": 0},
    ]
    for first_push in (False, True):
        elements = metrics.build_dabo_matrix_table_elements(rows, is_first_push=first_push)
        table = next(element for element in elements if element["tag"] == "table")
        assert [row["platform"] for row in table["rows"]] == ["累计有销售", "仅有退款"]
        assert "0.00万" in table["rows"][0]["diff_sales"]

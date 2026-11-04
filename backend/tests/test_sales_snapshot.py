"""The matrix and influencer ranking must share one sales snapshot."""

from app.reporting import sales_metrics as metrics


def test_tmall_grouping_uses_only_li_jiaqi_substring():
    top_names = ["李佳琦", "新版账号·李佳琦直播间", "李佳琦-任意新场次", "品牌专场（李佳琦）"]
    other_names = ["其他达人", "其他达人-改名后", "李佳琪", "通用", "", None]
    authors = [
        {"author": name, "platform": "天猫", "brand": brand, "gmv_wan": 2, "net_wan": 1}
        for brand in ("可复美", "可丽金")
        for name in [*top_names, *other_names]
    ]
    matrix = {row["item"]: row for row in metrics.fetch_dabo_matrix_raw(authors)}
    for brand in ("可复美", "可丽金"):
        assert matrix[f"天猫:{brand}:李佳琦"]["net"] == len(top_names)
        assert matrix[f"天猫:{brand}:二梯队"]["net"] == len(other_names)
        assert matrix[f"天猫:{brand}:二梯队"]["label"].strip() == "二梯队达人"
        assert matrix[f"天猫:{brand}"]["net"] == len(top_names) + len(other_names)


def test_li_jiaqi_session_names_share_one_host_and_never_enter_second_tier():
    names = ["李佳琦-挂链", "李佳琦-助播-晚场", "李佳琦（双十一专场）", "李佳琦Austin"]
    for name in names:
        assert metrics.canonical_influencer_name(name) == "李佳琦"
    authors = [
        {"author": name, "platform": "天猫", "brand": "可复美",
         "gmv_wan": 2, "net_wan": 1}
        for name in names
    ]
    matrix = {row["item"]: row for row in metrics.fetch_dabo_matrix_raw(authors)}
    assert matrix["天猫:可复美:李佳琦"]["net"] == 4
    assert matrix["天猫:可复美:二梯队"]["net"] == 0
    assert metrics.canonical_influencer_name("其他达人-挂链") == "其他达人-挂链"


def test_tmall_matrix_and_ranking_use_the_summary_query_snapshot(monkeypatch):
    calls = []

    def fetch(**kwargs):
        calls.append(kwargs)
        # 模拟外部结果表归属错误；同源明细存在时不得混入该表的天猫数据。
        return [
            {"platform": "天猫", "brand": "可复美", "author": "错误来源",
             "gmv_wan": 999, "net_wan": 999},
            {"platform": "抖音", "brand": "可复美", "author": "抖音达人",
             "gmv_wan": 5, "net_wan": 3},
        ]

    monkeypatch.setattr(metrics, "fetch_influencer_sales_rows", fetch)
    summary = {"category": "可复美", "item": "天猫", "达播净销_万": 28.79}
    source_rows = [
        {"统计维度": "5_天猫达播同源明细", "品牌": "可复美", "达人": "李佳琦-挂链",
         "净销_万": 22.53, "成交金额_万": 41.8196},
        {"统计维度": "5_天猫达播同源明细", "品牌": "可复美", "达人": "通用",
         "净销_万": 6.2587, "成交金额_万": 11.0478},
        {"统计维度": "5_天猫达播同源明细", "品牌": "可丽金", "达人": "李佳琦-挂链",
         "净销_万": 3.4993, "成交金额_万": 8.4413},
    ]
    rows = metrics.inject_dabo_matrix_rows([summary, *source_rows])
    matrix = {row["item"]: row for row in rows if row.get("category") == "达播矩阵"}
    assert matrix["天猫:可复美"]["净销_万"] == summary["达播净销_万"]
    assert matrix["天猫:可丽金"]["净销_万"] == 3.5
    assert matrix["天猫:可复美:李佳琦"]["净销_万"] == 22.53
    ranked = metrics.get_top_influencers([row for row in rows if metrics.is_dabo_row(row)])
    assert ranked[0]["name"] == "李佳琦"
    assert ranked[0]["net_wan"] == 26.03
    assert calls == [{"include_tmall": False}]
    assert all(row.get("统计维度") != "5_天猫达播同源明细" for row in rows)
    assert summary in rows


def test_empty_same_source_tmall_snapshot_does_not_fall_back_to_external_totals(monkeypatch):
    def fetch(**kwargs):
        assert kwargs == {"include_tmall": False}
        return []

    monkeypatch.setattr(metrics, "fetch_influencer_sales_rows", fetch)
    rows = metrics.inject_dabo_matrix_rows([
        {"统计维度": "5_天猫达播同源明细", "品牌": "", "达人": "",
         "净销_万": 0, "成交金额_万": 0},
    ])
    assert len(rows) == 1
    assert rows[0]["category"] == "达播矩阵"
    assert rows[0]["净销_万"] == 0
    assert not any(metrics.is_dabo_row(row) for row in rows)


def test_shared_tmall_snapshot_skips_the_external_tmall_query(monkeypatch):
    from io import BytesIO

    queries = []
    monkeypatch.setattr(metrics, "clickhouse_query_url", lambda: "http://localhost")

    def query(request, timeout):
        queries.append(request.data.decode("utf-8"))
        return BytesIO(b'{"data": []}')

    monkeypatch.setattr("urllib.request.urlopen", query)
    assert metrics.fetch_influencer_sales_rows(include_tmall=False) == []
    assert len(queries) == 2
    assert all("tm_product_realtime_jieguobiao" not in sql for sql in queries)


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
    # 「李佳琦-助播」的销售必须并入李佳琦，不能单独占一个名次。
    assert [r["name"] for r in ranked] == ["李佳琦"]
    assert ranked[0]["net_wan"] == 405.64
    assert ranked[0]["gmv_wan"] == 681.6
    matrix = {r["item"]: r for r in rows if r.get("category") == "达播矩阵"}
    assert matrix["天猫:可复美:李佳琦"]["净销_万"] == 383.23
    assert matrix["天猫:可丽金:李佳琦"]["净销_万"] == 22.41
    assert matrix["天猫"]["净销_万"] == 410.64
    # 二梯队＝除李佳琦（含其助播）之外的达人，这里只剩「通用」的 5 万。
    assert matrix["天猫:可复美:二梯队"]["净销_万"] == 5
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


def test_assistant_stream_accounts_roll_up_into_their_host():
    assert metrics.canonical_influencer_name("李佳琦-助播") == "李佳琦"
    assert metrics.canonical_influencer_name("李佳琦（助播）") == "李佳琦"
    assert metrics.canonical_influencer_name("李佳琦 助播") == "李佳琦"
    assert metrics.canonical_influencer_name("李佳琦助播") == "李佳琦"
    assert metrics.canonical_influencer_name(" 李佳琦 ") == "李佳琦"

    # 场次/专场后缀不是助播场次，不能被误合并。
    assert metrics.canonical_influencer_name(
        "小豆芽儿-20号早8点护肤场超级补贴"
    ) == "小豆芽儿-20号早8点护肤场超级补贴"
    assert metrics.canonical_influencer_name(
        "诸十亿（可复美焦点面霜专场）"
    ) == "诸十亿（可复美焦点面霜专场）"

    # 名字本身只有「助播」时保留原名，不静默丢数。
    assert metrics.canonical_influencer_name("助播") == "助播"
    assert metrics.canonical_influencer_name("") == ""


def test_matrix_merges_assistant_accounts_across_platforms():
    authors = [
        {"author": "李佳琦", "platform": "天猫", "brand": "可复美",
         "gmv_wan": 100, "net_wan": 60},
        {"author": "李佳琦-助播", "platform": "天猫", "brand": "可复美",
         "gmv_wan": 20, "net_wan": 10},
        {"author": "李佳琦", "platform": "抖音", "brand": "可丽金",
         "gmv_wan": 50, "net_wan": 30},
        {"author": "李佳琦-助播", "platform": "抖音", "brand": "可丽金",
         "gmv_wan": 25, "net_wan": 15},
    ]
    matrix = {item["item"]: item for item in metrics.fetch_dabo_matrix_raw(authors)}
    # 天猫：助播 10 万并入李佳琦，二梯队不再含助播。
    assert matrix["天猫:可复美:李佳琦"]["net"] == 70
    assert matrix["天猫:可复美:二梯队"]["net"] == 0
    # 抖音：矩阵明细同样只留一行合并后的李佳琦。
    assert matrix["抖音:可丽金:李佳琦"]["net"] == 45
    assert "抖音:可丽金:李佳琦-助播" not in matrix

import pytest

from app.reporting.dashboard_shops import build_all_shops_query
from app.reporting.realtime_dashboard_syncer import build_realtime_sales_dashboard_html


def test_shop_query_uses_all_sales_snapshots_without_top_or_store_filters():
    sql = build_all_shops_query('WITH biao1 AS (SELECT 1), h1 AS (SELECT 1), '
                                'ss_sale_sata AS (SELECT 1) SELECT 1')
    assert 'ds_all.`实时销售数据汇总`' in sql
    assert 'WHERE 销售金额 > 0' not in sql
    assert 'ORDER BY 净销金额 DESC' in sql
    assert 'LIMIT 5' not in sql
    assert 'shop_top_pos' not in sql
    assert '合作成交' not in sql
    assert 'SUM(CASE WHEN s.rk = 1 THEN s.成交金额' in sql
    assert 'SUM(CASE WHEN s.rk = 1 THEN s.净销金额' in sql


def test_unknown_template_fails_closed():
    with pytest.raises(ValueError):
        build_all_shops_query('SELECT 1')


def test_full_shop_list_replaces_partial_rows_without_appending_core_shop_duplicates():
    html = build_realtime_sales_dashboard_html({
        'shops': [{'name': '旧重点店铺', 'net': 500}],
        'all_shops': [
            {'name': '低销量店', 'net': -1, 'ops': -1, 'live': 0, 'mom': None},
            {'name': '全渠道店', 'net': 100, 'ops': 40, 'live': 60, 'mom': None},
        ],
    }, {'shops': [{'shop_name': '核心店', 'net_wan': 30}]})
    import json
    import re
    shops = json.loads(re.search(r'var SHOPS_DATA = (.*);', html).group(1))
    assert [row['name'] for row in shops] == ['全渠道店', '低销量店']
    assert '全部销售店铺' in html


def test_full_shop_list_preserves_missing_amount_and_sorts_known_sales_first():
    import json
    import re

    html = build_realtime_sales_dashboard_html({'all_shops': [
        {'name': '金额未返回', 'net': None, 'ops': None, 'live': 1},
        {'name': '正常店铺', 'net': 2, 'ops': 1, 'live': 1},
    ]}, {})
    rows = json.loads(re.search(r'var SHOPS_DATA = (.*);', html).group(1))
    assert [row['name'] for row in rows] == ['正常店铺', '金额未返回']
    assert rows[1]['net'] is None


def test_latest_missing_sales_remains_null_when_previous_snapshot_has_sales():
    import sqlite3

    sql = build_all_shops_query('WITH h1 AS (SELECT 1 AS shop_id, 1 AS rk, 0 AS 达播净销), '
                               'ss_sale_sata AS (SELECT 1) SELECT 1')
    # 仅替换 SQLite 不支持的日期加法，其余聚合表达式直接执行生产查询。
    sql = sql.replace('DATE_ADD(:end_date, INTERVAL 1 DAY)', ':tomorrow')
    with sqlite3.connect(':memory:') as db:
        db.row_factory = sqlite3.Row
        db.create_function('CONCAT_WS', -1, lambda sep, *args: sep.join(str(x) for x in args
                                                                      if x is not None))
        db.create_function('CONCAT', -1, lambda *args: ''.join(str(x) for x in args))
        db.execute("ATTACH DATABASE ':memory:' AS ds_all")
        db.execute('CREATE TABLE ds_all.实时销售数据汇总 '
                   '(平台 TEXT, 店铺 TEXT, shop_id INTEGER, 成交金额 REAL, 退款金额 REAL, 时间 TEXT)')
        db.executemany('INSERT INTO ds_all.实时销售数据汇总 VALUES (?,?,?,?,?,?)', [
            ('天猫', '店铺', 1, 10000, 0, '2026-11-02 09:00:00'),
            ('天猫', '店铺', 1, None, 0, '2026-11-02 10:00:00'),
        ])
        row = dict(db.execute(sql, {'end_date': '2026-11-02',
                                   'tomorrow': '2026-11-03'}).fetchone())
    assert row['净销_万'] is None
    assert row['运营净销_万'] is None


def test_notebook_layout_can_be_applied_repeatedly_without_changing_sales_data():
    from app.reporting.dashboard_viewport import adapt_dashboard_viewport

    html = build_realtime_sales_dashboard_html({'all_shops': [
        {'name': '真实店铺', 'net': 2, 'ops': 1, 'live': 1},
    ]}, {})
    assert adapt_dashboard_viewport(html) == html

import pytest

from app.reporting.dashboard_shops import build_all_shops_query
from app.reporting.realtime_dashboard_syncer import build_realtime_sales_dashboard_html


def test_shop_query_uses_all_sales_snapshots_without_top_or_store_filters():
    sql = build_all_shops_query('WITH biao1 AS (SELECT 1), h1 AS (SELECT 1), '
                                'ss_sale_sata AS (SELECT 1) SELECT 1')
    assert 'ds_all.`实时销售数据汇总`' in sql
    assert '销售金额 > 0' in sql
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

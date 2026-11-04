"""技能包独立回归测试，不依赖 StaffDeck 源码或业务数据库。"""

import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('sales_skill', ROOT / 'scripts' / 'run.py')
skill = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(skill)
CONFIG = {
    'schema_version': 1, 'profile_id': 'sales-test-v1', 'title': '销售技能测试报告',
    'summary_template_id': 'summary', 'dabo_template_id': 'dabo',
    'summary_row_limit': 100, 'dabo_row_limit': 500, 'top_n': 5,
    'influencer_aliases': {'李佳琦-助播': '李佳琦', '李佳琦（助播）': '李佳琦'},
}


def summary(value=100):
    return {'template_id': 'summary', 'rows': [
        {'category': '大盘', 'item': '电商整体', '净销_万': value,
         '运营净销_万': 70, '达播净销_万': 30, '数据更新时间': '10-28 10:00'},
        {'category': '可复美', 'item': '<script>整体</script>', '净销_万': 60},
    ], 'row_count': 2}


def dabo():
    return {'template_id': 'dabo', 'rows': [
        {'统计维度': '3_达人TOP18', '达人': '李佳琦', '净销': 10000, '成交金额': 12000},
        {'统计维度': '3_达人TOP18', '达人': '李佳琦-助播', '净销': 5000, '成交金额': 6000},
        {'统计维度': '1_平台汇总', '达人': '汇总', '净销': 999999},
    ], 'row_count': 3}


def build(s=None, d=None, baseline=None, task_id='test'):
    return skill.build_snapshot(s or summary(), d or dabo(), CONFIG,
                                as_of='2026-10-28T10:00:00+08:00',
                                task_id=task_id, baseline=baseline)


def test_real_summary_and_no_fabricated_increment():
    snap = build()
    assert snap['kpis']['net'] == 100
    assert snap['kpis']['ops'] == 70
    assert snap['delta'] is None
    assert snap['push_status'] == 'disabled'
    assert snap['influencers'][0]['name'] == '李佳琦'
    assert snap['influencers'][0]['net'] == 1.5
    assert len(snap['influencers']) == 1


def test_negative_net_and_delta_are_preserved():
    old = build()
    new = build(summary(-5), baseline=old)
    assert new['kpis']['net'] == -5
    assert new['delta']['net'] == -105


@pytest.mark.parametrize('change', [
    {'date': '2026-10-27'}, {'task_id': 'other'}, {'profile_id': 'other'},
    {'generated_at': '2026-10-28T11:00:00+08:00'},
])
def test_incompatible_baseline_rejected(change):
    old = build()
    old.update(change)
    with pytest.raises(ValueError):
        build(baseline=old)


@pytest.mark.parametrize('bad', [float('nan'), float('inf'), 'not-a-number', None])
def test_invalid_primary_amount_fails(bad):
    with pytest.raises(ValueError):
        build(summary(bad))


def test_wrong_template_and_empty_data_fail():
    for payload in [{'template_id': 'wrong', 'rows': []},
                    {'template_id': 'summary', 'rows': [], 'row_count': 0},
                    {'success': False, 'error': 'failed'}]:
        with pytest.raises(ValueError):
            build(payload)


def test_truncation_and_missing_total_fail():
    payload = summary()
    payload['row_count'] = 100
    with pytest.raises(ValueError):
        build(payload)
    payload = summary()
    payload['rows'] = payload['rows'][1:]
    payload['row_count'] = 1
    with pytest.raises(ValueError):
        build(payload)


def test_report_escapes_data_and_marks_scope():
    html = skill.render_html(build(), (ROOT / 'assets' / 'dashboard.html').read_text('utf-8'))
    assert '<script>整体</script>' not in html
    assert '&lt;script&gt;整体&lt;/script&gt;' in html
    assert '无比较基准' in html
    assert '查询模板返回范围' in html


def test_input_is_confined_to_workspace(tmp_path):
    file = tmp_path / 'data.json'
    file.write_text(json.dumps(summary()), encoding='utf-8')
    assert skill.load_payload(tmp_path, 'data.json')['template_id'] == 'summary'
    with pytest.raises(ValueError):
        skill.load_payload(tmp_path, '../outside.json')
    with pytest.raises(ValueError):
        skill.load_payload(tmp_path, str(ROOT / 'config.json'))


def test_enveloped_tool_result_is_supported():
    snap = build({'success': True, 'data': summary()})
    assert snap['kpis']['net'] == 100


def test_historical_business_date_is_separate_from_generation():
    snap = skill.build_snapshot(summary(), dabo(), CONFIG,
                                as_of='2026-10-28T10:00:00+08:00',
                                task_id='history', query_date='2026-10-27')
    assert snap['date'] == '2026-10-27'
    assert snap['generated_at'].startswith('2026-10-28')


def test_source_date_must_match_selected_day():
    live = dabo()
    live['rows'][0]['日期'] = '2026-10-27~2026-10-27'
    with pytest.raises(ValueError):
        skill.build_snapshot(summary(), live, CONFIG,
                             as_of='2026-10-28T10:00:00+08:00',
                             task_id='test', query_date='2026-10-28')


def test_expanded_sections_preserve_source_scope_and_negative_values():
    payload = summary()
    payload['rows'] += [
        {'category': '可复美', 'item': '整体', '净销_万': 80},
        {'category': '可复美', 'item': '天猫', '净销_万': -2},
        {'category': '24小时环比', 'item': '09时', '净销_万': 100, '运营增量_万': 90},
        {'category': '24小时环比', 'item': '11时', '净销_万': 0, '运营增量_万': 95},
        {'category': '店铺负增量Top5', 'item': '测试店', '净销_万': 5, '环比增量_万': -3},
    ]
    payload['row_count'] = len(payload['rows'])
    snap = build(payload)
    assert snap['sections']['brands'][0]['name'] == '可复美'
    assert next(r for r in snap['sections']['channels'] if r['name'] == '天猫')['net'] == -2
    assert snap['sections']['hourly'][0]['today'] == 100
    assert snap['sections']['hourly'][1]['today'] is None
    assert snap['sections']['hourly'][1]['yesterday'] == 95
    assert snap['sections']['negative_shops'][0]['source_delta'] == -3


def test_successful_preview_baseline_has_explicit_label_and_time():
    old = build()
    snap = build(summary(90), baseline=old)
    html = skill.render_html(snap, (ROOT / 'assets/dashboard.html').read_text('utf-8'))
    assert snap['baseline_at'] == old['generated_at']
    assert '较上次成功预览' in html
    assert '-10.00' in html


def test_source_contract_change_cannot_be_compared():
    old = skill.build_snapshot(summary(), dabo(), CONFIG,
        as_of='2026-10-28T10:00:00+08:00', task_id='test', source_contract='sql-v1')
    with pytest.raises(ValueError):
        skill.build_snapshot(summary(), dabo(), CONFIG,
            as_of='2026-10-28T10:15:00+08:00', task_id='test',
            source_contract='sql-v2', baseline=old)


def test_future_source_hour_does_not_reintroduce_future_curve():
    payload = summary()
    payload['rows'][0]['数据更新时间'] = '10-28 23:50'
    payload['rows'].append({'category': '24小时环比', 'item': '11时',
                            '净销_万': 999, '运营增量_万': 100})
    payload['row_count'] = len(payload['rows'])
    assert build(payload)['sections']['hourly'][0]['today'] is None


def test_future_business_day_is_rejected():
    with pytest.raises(ValueError, match='未来'):
        skill.build_snapshot(summary(), dabo(), CONFIG,
            as_of='2026-10-28T10:00:00+08:00', task_id='test', query_date='2026-10-29')

"""适配器协议校验不触发真实数据库和外部消息。"""

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('sales_adapter', ROOT / 'adapters' / 'mcp_server.py')
adapter = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(adapter)


def test_discovery_exposes_only_preview_date():
    tool = adapter.dispatch('tools/list', {})['tools'][0]
    assert tool['name'] == 'ecommerce_sales_preview'
    assert tool['inputSchema']['additionalProperties'] is False
    assert list(tool['inputSchema']['properties']) == ['date']


@pytest.mark.parametrize('args', [{'date': '2026-10-28', 'notify': True},
                                {'date': '../escape'}, {}, {'date': '2026-02-30'}])
def test_bad_arguments_rejected_before_execution(args):
    with pytest.raises(ValueError):
        adapter.validate_arguments(args)


def test_valid_business_day():
    assert adapter.validate_arguments({'date': '2026-10-28'}) == '2026-10-28'


def test_effective_template_is_checked_before_execution():
    tool = {'type': 'http', 'config': {}, 'url': '/api/mock/data-query/expected'}
    assert adapter.checked_template_id(tool, 'expected') == 'expected'
    tool['config'] = {'source': 'data_query', 'template_id': 'redirected'}
    with pytest.raises(ValueError):
        adapter.checked_template_id(tool, 'expected')
    with pytest.raises(ValueError):
        adapter.checked_template_id({'type': 'http', 'config': {}, 'url': '/other'}, 'expected')


def test_disabled_template_is_rejected_before_publication():
    from types import SimpleNamespace
    template = SimpleNamespace(id='q', tenant_id='t', status='disabled', data_source_id='d',
                               query_content='select 1', params_json=[], max_rows=100)
    contract = {'id': 'q', 'source': 'd', 'sql': 'select 1', 'params': [], 'max_rows': 100}
    with pytest.raises(ValueError):
        adapter.validate_template_contract(template, contract, 't')


def _call(monkeypatch, preview):
    monkeypatch.setattr(adapter, 'preview', preview)
    return adapter.dispatch('tools/call', {'name': adapter.TOOL,
                                           'arguments': {'date': '2026-10-28'}})


def test_dispatch_returns_stage_timeout_before_mcp_deadline(monkeypatch):
    import time

    def slow(day, journal):
        journal.stage = 'summary_query'
        time.sleep(1.5)
        return {'status': 'success'}

    monkeypatch.setattr(adapter, 'PREVIEW_DEADLINE_SECONDS', 0.2)
    monkeypatch.setattr(adapter, 'PUBLISH_GRACE_SECONDS', 0.05)
    result = _call(monkeypatch, slow)
    payload = result['structuredContent']
    assert result['isError'] is False
    assert payload['status'] == 'timeout'
    assert payload['stage'] == 'summary_query'
    assert payload['retryable'] is True


def test_dispatch_reports_preview_failure_without_protocol_error(monkeypatch):
    def fail(day, journal):
        journal.stage = 'summary_query'
        raise adapter.PreviewFailure('summary_query', '汇总查询失败，请在平台检查数据源与查询模板')

    payload = _call(monkeypatch, fail)['structuredContent']
    assert payload['status'] == 'error'
    assert payload['stage'] == 'summary_query'
    assert payload['retryable'] is False
    assert '数据源' in payload['message']


def test_dispatch_rejects_bad_arguments_as_structured_result():
    payload = adapter.dispatch('tools/call', {'name': adapter.TOOL,
                                              'arguments': {}})['structuredContent']
    assert payload['status'] == 'invalid_arguments'
    assert payload['retryable'] is False


def test_dispatch_hides_unexpected_error_details(monkeypatch):
    def boom(day, journal):
        raise RuntimeError('mysql://user:secret@host/db')

    payload = _call(monkeypatch, boom)['structuredContent']
    assert payload['status'] == 'error'
    assert 'secret' not in payload['message']


def test_deadline_keeps_room_below_mcp_client_timeout():
    assert adapter.PREVIEW_DEADLINE_SECONDS + adapter.PUBLISH_GRACE_SECONDS < 240 - 15


def test_cancelled_journal_aborts_at_next_checkpoint():
    journal = adapter._Journal()
    journal.stage = 'script'
    journal.cancelled = True
    with pytest.raises(adapter.PreviewFailure) as exc:
        journal.check()
    assert exc.value.status == 'timeout'
    assert exc.value.stage == 'script'

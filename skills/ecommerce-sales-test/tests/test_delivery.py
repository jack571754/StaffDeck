"""交付复用必须保留查询完整性语义。"""

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('delivery', ROOT / 'adapters/platform_delivery.py')
delivery = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(delivery)


def test_incomplete_result_cannot_reuse_valid_input_fingerprint():
    valid = {'template_id': 'a', 'rows': [{'净销': 10}], 'row_count': 1}
    before = delivery.input_fingerprint(valid, valid)
    assert delivery.input_fingerprint({**valid, 'truncated': True}, valid) != before
    assert delivery.input_fingerprint({**valid, 'row_count': 2}, valid) != before
    assert delivery.input_fingerprint({**valid, 'success': False}, valid) != before


def test_query_timing_and_row_order_do_not_trigger_publication():
    valid = {'template_id': 'a', 'rows': [{'净销': 10}, {'净销': 20}], 'row_count': 2}
    changed = {**valid, 'rows': list(reversed(valid['rows'])), 'elapsed_ms': 999, 'cached': True}
    assert delivery.input_fingerprint(valid, valid) == delivery.input_fingerprint(changed, valid)


def test_reuse_rejects_expired_record_and_deleted_file(tmp_path, monkeypatch):
    import sys
    from datetime import UTC, datetime, timedelta
    from types import ModuleType, SimpleNamespace
    models = ModuleType('app.db.models')
    models.ArtifactShareLink = object
    owners = ModuleType('app.core.artifact_owners')
    file = tmp_path / 'report.html'
    file.write_text('ok')
    owners.resolve_artifact_owner = lambda *a, **kw: SimpleNamespace(
        workspace_root=tmp_path, path='report.html', sha256='2689367b205c16ce32ed4200942b8b8b1e262dfc70d9bc9fbc77c49699a4f1df')
    monkeypatch.setitem(sys.modules, 'app.db.models', models)
    monkeypatch.setitem(sys.modules, 'app.core.artifact_owners', owners)
    link = SimpleNamespace(is_revoked=False, tenant_id='t', session_id='s', owner_kind='harness_frame',
        owner_id='frame', path='report.html', expires_at=datetime.now(UTC) - timedelta(seconds=1))
    db = SimpleNamespace(get=lambda *a: link)
    record = {'tenant_id': 't', 'delivery_session_id': 's'}
    result = {'publication': {'share_link_id': 'id', 'expires_at': 9999999999}}
    assert delivery.reusable(db, record, result) is False
    link.expires_at = datetime.now(UTC) + timedelta(days=1)
    assert delivery.reusable(db, record, result) is True
    file.unlink()
    assert delivery.reusable(db, record, result) is False

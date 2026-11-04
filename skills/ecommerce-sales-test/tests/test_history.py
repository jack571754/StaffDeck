"""持久预览基准的隔离、失败与顺序测试。"""

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('preview_history', ROOT / 'adapters/history.py')
history = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(history)


def builder(value, seen):
    def make(baseline):
        seen.append(baseline)
        return {'kpis': {'net': value}}, {'status': 'success', 'value': value, 'published': True}
    return make


def test_success_is_persistent_and_scoped(tmp_path):
    file = tmp_path / 'history.sqlite'
    seen = []
    history.PreviewHistory(file).generate('task/date/contract', 'a', '10:00', builder(100, seen))
    history.PreviewHistory(file).generate('task/date/contract', 'b', '10:15', builder(90, seen))
    assert seen == [None, {'kpis': {'net': 100}}]
    history.PreviewHistory(file).generate('other/date/contract', 'b', '10:15', builder(80, seen))
    assert seen[-1] is None


def test_failure_does_not_advance_baseline(tmp_path):
    store = history.PreviewHistory(tmp_path / 'history.sqlite')
    seen = []
    store.generate('s', 'a', '10:00', builder(100, seen))
    def failed(baseline):
        raise RuntimeError('publish failed')
    with pytest.raises(RuntimeError):
        store.generate('s', 'b', '10:15', failed)
    store.generate('s', 'c', '10:30', builder(120, seen))
    assert seen[-1]['kpis']['net'] == 100


def test_identical_input_reuses_success_without_new_publication(tmp_path):
    store = history.PreviewHistory(tmp_path / 'history.sqlite')
    seen = []
    first = store.generate('s', 'a', '10:00', builder(100, seen))
    second = store.generate('s', 'a', '10:00', builder(100, seen))
    assert len(seen) == 1
    assert second['reused'] is True
    assert first['value'] == second['value']


def test_stale_response_does_not_replace_newer_baseline(tmp_path):
    store = history.PreviewHistory(tmp_path / 'history.sqlite')
    seen = []
    store.generate('s', 'b', '10:15', builder(100, seen))
    with pytest.raises(ValueError):
        store.generate('s', 'a', '10:00', builder(80, seen))
    store.generate('s', 'c', '10:30', builder(120, seen))
    assert seen[-1]['kpis']['net'] == 100


def test_unsuccessful_report_cannot_be_committed(tmp_path):
    store = history.PreviewHistory(tmp_path / 'history.sqlite')
    with pytest.raises(ValueError):
        store.generate('s', 'a', '10:00', lambda old: ({}, {'status': 'failed'}))
    seen = []
    store.generate('s', 'b', '10:15', builder(100, seen))
    assert seen == [None]


def test_concurrent_identical_input_publishes_only_once(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    store = history.PreviewHistory(tmp_path / 'history.sqlite')
    ready = Barrier(2)
    seen = []
    def call():
        ready.wait()
        return store.generate('same', 'a', '10:00', builder(100, seen))
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: call(), range(2)))
    assert len(seen) == 1
    assert sorted(r['reused'] for r in results) == [False, True]


def test_expired_link_regenerates_without_reusing_old_delivery(tmp_path):
    store = history.PreviewHistory(tmp_path / 'history.sqlite')
    seen = []
    store.generate('s', 'a', '10:00', builder(100, seen))
    result = store.generate('s', 'a', '10:00', builder(100, seen), reusable=lambda old: False)
    assert result['reused'] is False
    assert len(seen) == 2

"""Real Redis/Lua admission tests, enabled with an isolated ARCHIVE_TEST_REDIS."""
from concurrent.futures import ThreadPoolExecutor
import os
from types import SimpleNamespace

import pytest

pytest.importorskip('ckan')
redis_module = pytest.importorskip('redis')
from ckanext.c4w.archive import service


@pytest.fixture
def queue(monkeypatch):
    url = os.environ.get('ARCHIVE_TEST_REDIS')
    if not url:
        pytest.skip('Set ARCHIVE_TEST_REDIS to an isolated disposable Redis database')
    connection = redis_module.Redis.from_url(url)
    connection.flushdb()
    monkeypatch.setattr(service, 'redis', lambda: connection)
    monkeypatch.setattr(service, 'key', lambda job, kind='state': 'qa:%s:%s' % (kind, job))
    monkeypatch.setattr(service, 'root', lambda: '/unused')
    monkeypatch.setattr(service, 'open_manifest', lambda *args: (None, {
        'generation':'v3-0123456789abcdef', 'parameters':[{'code':'pH','units':['pH']}],
        'dimensions':{'countries':['France']}}))
    calls = []
    def enqueue(*args, **kwargs):
        calls.append(kwargs)
    monkeypatch.setattr('ckan.lib.jobs.enqueue', enqueue)
    yield connection, calls
    connection.flushdb()


def test_admission_is_atomic_under_simultaneous_distinct_requests(queue):
    connection, calls = queue
    with ThreadPoolExecutor(max_workers=30) as pool:
        results = list(pool.map(lambda n: service.submit({'from_year':1900+n}), range(35)))
    assert sum(state['status']=='pending' for _, state in results) == 20
    assert sum(state['status']=='error' for _, state in results) == 15
    assert connection.zcard('qa:slots:admission') == 20
    assert len(calls) == 20


def test_duplicate_requests_enqueue_once_and_long_wait_preserves_claim(queue, monkeypatch):
    connection, calls = queue
    with ThreadPoolExecutor(max_workers=20) as pool:
        results = list(pool.map(lambda _: service.submit({'from_year':2000}), range(30)))
    assert len(calls) == 1
    assert all(state['status']=='pending' for _, state in results)
    job_id = results[0][0]
    assert connection.ttl(service.key(job_id, 'lock')) >= 1400
    queued_at = results[0][1]['queued_at']
    monkeypatch.setattr(service.time, 'time', lambda: queued_at + 600)
    assert service.state(job_id)['status'] == 'pending'
    claim = calls[0]['kwargs']['claim']
    service.finish(job_id, claim, {'status':'ready','result':{'observations':5}})
    assert service.state(job_id)['status'] == 'ready'
    assert connection.zcard('qa:slots:admission') == 0


def test_failure_callback_releases_admission_and_stale_worker_cannot_overwrite(queue):
    connection, calls = queue
    job_id, _ = service.submit({})
    job = SimpleNamespace(kwargs=calls[0]['kwargs'])
    service.query_failed(job, connection, RuntimeError, RuntimeError(), None)
    assert service.state(job_id)['status'] == 'error'
    assert connection.zcard('qa:slots:admission') == 0
    service.finish(job_id, job.kwargs['claim'], {'status':'ready'})
    assert service.state(job_id)['status'] == 'error'

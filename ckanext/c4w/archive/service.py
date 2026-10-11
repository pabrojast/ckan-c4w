"""CKAN authorization and isolated, single-concurrency archive query queue."""
import hashlib
import json
import re
import time
import uuid

from .query import open_manifest, validate

WAIT_BUDGET = 1800  # 20 admitted jobs * 70-second RQ timeout, plus worker overhead.


def root():
    import ckan.plugins.toolkit as tk
    return tk.config.get('ckanext.c4w.archive_root', '/var/lib/ckan/gfqa')


def enabled(slug):
    import ckan.plugins.toolkit as tk
    return slug == 'gemstat' and tk.asbool(tk.config.get('ckanext.c4w.archive_enabled', False))


def redis():
    from ckan.lib.redis import connect_to_redis
    return connect_to_redis()


def key(job_id, kind='state'):
    import ckan.plugins.toolkit as tk
    site = hashlib.sha256(str(tk.config.get('ckan.site_id', 'default')).encode()).hexdigest()[:16]
    return 'ckan:c4w:archive:%s:%s:%s' % (site, kind, job_id)


def state(job_id):
    if not re.fullmatch('[a-f0-9]{64}', job_id):
        raise ValueError('Invalid query identifier')
    cached = redis().get(key(job_id))
    if not cached:
        return {'status': 'expired', 'message': 'This query expired. Please retry.'}
    result = json.loads(cached)
    if result['status'] == 'pending' and time.time() - result['queued_at'] > WAIT_BUDGET:
        return {'status': 'error', 'message': 'Archive processing is unavailable or exceeded its time limit. Please retry.'}
    return result


def submit(data):
    from ckan.lib.jobs import enqueue
    _, manifest = open_manifest(root())
    query = validate(data, manifest)
    encoded = json.dumps([manifest['generation'], query], sort_keys=True, ensure_ascii=False)
    job_id = hashlib.sha256(encoded.encode()).hexdigest()
    connection = redis()
    cached = state(job_id)
    if cached['status'] in ('ready', 'pending'):
        return job_id, cached
    claim = uuid.uuid4().hex
    # Reserve admission and claim together. Concurrent HTTP requests cannot
    # overrun the queue limit or enqueue a duplicate query.
    admitted = connection.eval(
        'redis.call("zremrangebyscore",KEYS[2],"-inf",ARGV[3]); '
        'if redis.call("exists",KEYS[1])==1 then return 0 end; '
        'if redis.call("zcard",KEYS[2])>=20 then return -1 end; '
        'redis.call("set",KEYS[1],ARGV[1],"EX",ARGV[2]); '
        'redis.call("zadd",KEYS[2],ARGV[4],ARGV[5]); return 1',
        2, key(job_id, 'lock'), key('admission', 'slots'), claim,
        WAIT_BUDGET, time.time(), time.time() + WAIT_BUDGET, job_id)
    if admitted == -1:
        return job_id, {'status': 'error', 'message': 'The archive is busy. Please retry shortly.'}
    if admitted == 0:
        pending = state(job_id)
        return job_id, (pending if pending['status'] != 'expired' else
                        {'status': 'pending', 'queued_at': time.time()})
    payload = {'status': 'pending', 'queued_at': time.time()}
    connection.setex(key(job_id), WAIT_BUDGET, json.dumps(payload))
    try:
        enqueue(execute_job, kwargs={'job_id': job_id, 'claim': claim,
                                    'query': query, 'generation': manifest['generation']},
                queue='c4w-archive', title='Query GFQA archive',
                rq_kwargs={'timeout': 70, 'result_ttl': 0, 'failure_ttl': 300,
                           'on_failure': query_failed})
    except Exception:
        finish(job_id, claim, {'status': 'error', 'message': 'Archive query queue is unavailable. Please retry.'}, 15)
        return job_id, state(job_id)
    return job_id, payload


def finish(job_id, claim, result, ttl=600):
    redis().eval('if redis.call("get",KEYS[1])==ARGV[1] then '
                 'redis.call("setex",KEYS[2],ARGV[2],ARGV[3]); '
                 'redis.call("zrem",KEYS[3],ARGV[4]); '
                 'return redis.call("del",KEYS[1]) else return 0 end',
                 3, key(job_id, 'lock'), key(job_id), key('admission', 'slots'), claim, ttl,
                 json.dumps(result, ensure_ascii=False, allow_nan=False), job_id)


def query_failed(job, connection, exc_type, exc_value, traceback):
    finish(job.kwargs['job_id'], job.kwargs['claim'],
           {'status': 'error', 'message': 'The archive query exceeded its time limit or was interrupted. Narrow the filters and retry.'}, 15)


def execute_job(job_id, claim, query, generation):
    from .query import execute
    try:
        result = execute(root(), query, generation)
        finish(job_id, claim, {'status': 'ready', 'result': result})
    except Exception:
        import logging
        logging.getLogger(__name__).exception('GFQA archive query failed')
        finish(job_id, claim, {'status': 'error', 'message': 'The archive query could not be completed. Narrow the filters or retry.'}, 15)


def worker(burst=False):
    from ckan.lib.jobs import Worker
    from rq import Worker as RQWorker
    class ArchiveWorker(Worker):
        def main_work_horse(self, job, queue):
            # CKAN CLI already loaded its environment; do not load plugins twice.
            return RQWorker.main_work_horse(self, job, queue)
    ArchiveWorker(['c4w-archive']).work(burst=burst)

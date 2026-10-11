"""CKAN authorization and isolated, single-concurrency archive query queue."""
import hashlib
import json
import re
import time
import uuid

from .query import open_manifest, validate


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
    if result['status'] == 'pending' and time.time() - result['queued_at'] > 120:
        return {'status': 'error', 'message': 'Archive processing is unavailable or exceeded its time limit. Please retry.'}
    return result


def submit(data):
    from ckan.lib.jobs import enqueue, get_queue
    _, manifest = open_manifest(root())
    query = validate(data, manifest)
    encoded = json.dumps([manifest['generation'], query], sort_keys=True, ensure_ascii=False)
    job_id = hashlib.sha256(encoded.encode()).hexdigest()
    connection = redis()
    cached = state(job_id)
    if cached['status'] in ('ready', 'pending'):
        return job_id, cached
    if len(get_queue('c4w-archive')) >= 20:
        return job_id, {'status': 'error', 'message': 'The archive is busy. Please retry shortly.'}
    claim = uuid.uuid4().hex
    if not connection.set(key(job_id, 'lock'), claim, nx=True, ex=120):
        return job_id, state(job_id)
    payload = {'status': 'pending', 'queued_at': time.time()}
    connection.setex(key(job_id), 180, json.dumps(payload))
    try:
        enqueue(execute_job, kwargs={'job_id': job_id, 'claim': claim,
                                    'query': query, 'generation': manifest['generation']},
                queue='c4w-archive', title='Query GFQA archive',
                rq_kwargs={'timeout': 70, 'result_ttl': 0, 'failure_ttl': 300})
    except Exception:
        finish(job_id, claim, {'status': 'error', 'message': 'Archive query queue is unavailable. Please retry.'}, 15)
        return job_id, state(job_id)
    return job_id, payload


def finish(job_id, claim, result, ttl=600):
    redis().eval('if redis.call("get",KEYS[1])==ARGV[1] then '
                 'redis.call("setex",KEYS[2],ARGV[2],ARGV[3]); '
                 'return redis.call("del",KEYS[1]) else return 0 end',
                 2, key(job_id, 'lock'), key(job_id), claim, ttl,
                 json.dumps(result, ensure_ascii=False, allow_nan=False))


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

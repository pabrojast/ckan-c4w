# encoding: utf-8
"""The bridge between the CKAN model and the pure pipeline.

This is the ONE module in the ``data`` package that imports CKAN. It loads
the dataset and its files, brings the raw files back from the object store,
runs ``pipeline.run`` and writes the bundle into ``c4w_dashboard_bundle``.

Three ways in, one function::

    dispatch(dataset_id)      small file -> inline; large -> job or 'queued'
    process_dataset_job(id)   the RQ entry point (module-level, picklable)
    process_dataset(id)       the work itself; also what the CLI calls

Generations: a run writes a complete new bundle under
``bundle_generation + 1`` and only then flips the dataset row to it, so a
dashboard being viewed during a re-run keeps reading a whole bundle.
"""
import datetime
import gzip
import hashlib
import logging
import shutil
import tempfile

import ckan.plugins.toolkit as tk

from ckanext.c4w import db
from ckanext.c4w.text import load_extras, dump_extras

log = logging.getLogger(__name__)

MB = 1024 * 1024
INTERNAL_ERROR = u'Processing failed because of an internal error. ' \
                 u'The Citizens4Water team has been notified.'


def _int(key, default):
    try:
        value = int(tk.config.get(key) or default)
    except (TypeError, ValueError):
        return default
    return value if value > 0 else default


def _limits():
    return {
        'max_rows': _int('ckanext.c4w.data_max_rows', 10000000),
        'max_sites': _int('ckanext.c4w.data_max_sites', 200000),
        'max_parameters': _int('ckanext.c4w.data_max_parameters', 300),
    }


def _options():
    return {
        'min_records': _int('ckanext.c4w.bundle_min_records', 20),
        'min_distinct': _int('ckanext.c4w.bundle_min_distinct', 5),
    }


def _data_files(session, dataset_id):
    return (session.query(db.C4wDatasetFile)
            .filter(db.C4wDatasetFile.dataset_id == dataset_id,
                    db.C4wDatasetFile.kind == u'data')
            .order_by(db.C4wDatasetFile.sort_order.asc(),
                      db.C4wDatasetFile.created.asc())
            .all())


def dispatch(dataset_id, force=False):
    """Run now, enqueue, or mark queued. Returns the resulting status."""
    from ckan.model.meta import Session

    db.ensure_mappers()
    row = Session.query(db.C4wDataset).filter(
        db.C4wDataset.id == dataset_id).first()
    if row is None:
        return None
    if row.processing_status == u'processing' and not force:
        return u'processing'
    files = _data_files(Session, dataset_id)
    total = sum(f.size_bytes or 0 for f in files)
    inline_max = _int('ckanext.c4w.data_inline_max_mb', 20) * MB
    if total <= inline_max:
        process_dataset(dataset_id, force=force)
        row = Session.query(db.C4wDataset).filter(
            db.C4wDataset.id == dataset_id).first()
        return row.processing_status if row else None

    row.processing_status = u'queued'
    row.processing_error = None
    Session.add(row)
    Session.commit()
    if tk.asbool(tk.config.get('ckanext.c4w.async_processing', False)):
        try:
            tk.enqueue_job(
                process_dataset_job, [dataset_id],
                title=u'c4w: process dataset %s' % dataset_id,
                queue=u'default',
                rq_kwargs={'timeout': _int('ckanext.c4w.job_timeout', 3600)})
        except Exception:
            # Stays 'queued': an operator or the CLI can still run it.
            log.error("ckanext-c4w: could not enqueue the processing job")
    return u'queued'


def process_dataset_job(dataset_id):
    """RQ entry point."""
    process_dataset(dataset_id)


def process_dataset(dataset_id, force=False, progress=None):
    """Run the pipeline for one dataset and store its bundle.

    Returns the final processing status. Never raises for a data problem:
    the dataset is marked ``failed`` with a message the uploader may read.
    """
    from ckan.model.meta import Session
    from ckanext.c4w.data import fetch, pipeline
    from ckanext.c4w.data.errors import DataError
    from ckanext.c4w.logic import uploads

    db.ensure_mappers()
    row = Session.query(db.C4wDataset).filter(
        db.C4wDataset.id == dataset_id).first()
    if row is None:
        return None
    files = _data_files(Session, dataset_id)
    spec = load_extras(row.mapping_json)
    if not files:
        return _fail(row, u'No data file has been uploaded.')
    if not spec:
        return _fail(row, u'The columns have not been mapped.')

    row.processing_status = u'processing'
    row.processing_error = None
    Session.add(row)
    Session.commit()

    workdir = tempfile.mkdtemp(prefix='c4w-job-')
    try:
        hosts = uploads.storage_hosts()
        paths = []
        for item in files:
            cap = max((item.size_bytes or 0) + MB,
                      _int('ckanext.c4w.data_max_upload_mb', 256) * MB)
            paths.append(fetch.fetch_to_temp(
                item.url, hosts, cap, workdir,
                allow_insecure=_allow_insecure(item.url)))
        meta = {
            'slug': row.slug,
            'title': row.title,
            'credit': row.publisher or row.author or u'',
            'source': row.source_url or u'',
            'license': row.license_id or u'',
            'generatedAt': datetime.datetime.utcnow().replace(
                microsecond=0).isoformat() + 'Z',
        }
        result = pipeline.run(paths, spec, meta, limits=_limits(),
                              options=_options(), workdir=workdir,
                              progress=progress)
        _store_bundle(row, result)
        return row.processing_status
    except DataError as exc:
        Session.rollback()
        return _fail(row, u'%s' % exc)
    except Exception:
        Session.rollback()
        log.exception("ckanext-c4w: processing dataset %s failed", dataset_id)
        return _fail(row, INTERNAL_ERROR)
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


def _allow_insecure(url):
    """Plain http is only tolerated for the site's own dev host."""
    site = tk.config.get('ckan.site_url') or u''
    return bool(url and url.startswith('http://')
                and site.startswith('http://'))


def _fail(row, message):
    from ckan.model.meta import Session
    row = Session.merge(row)
    row.processing_status = u'failed'
    row.processing_error = (message or INTERNAL_ERROR)[:2000]
    row.modified = db._utcnow()
    Session.add(row)
    Session.commit()
    return u'failed'


def _store_bundle(row, result):
    from ckan.model.meta import Session

    row = Session.merge(row)
    generation = (row.bundle_generation or 0) + 1
    max_blob = _int('ckanext.c4w.bundle_max_blob_mb', 25) * MB
    for name, raw in result.files.items():
        body = gzip.compress(raw, compresslevel=9)
        if len(body) > max_blob:
            from ckanext.c4w.data.errors import LimitExceeded
            raise LimitExceeded(
                u'The dashboard for %s is larger than the site allows '
                u'(%d MB). Try a coarser time grain.' % (name, max_blob // MB))
        Session.add(db.C4wDashboardBundle(
            dataset_id=row.id,
            generation=generation,
            name=name,
            content_type=u'application/json',
            etag=hashlib.sha256(raw).hexdigest(),
            raw_size=len(raw),
            gz_size=len(body),
            body=body,
        ))
    Session.flush()

    summary = result.summary
    row.bundle_generation = generation
    row.record_count = summary.get('record_count')
    row.site_count = summary.get('site_count')
    row.parameter_count = summary.get('parameter_count')
    bbox = summary.get('bbox')
    if bbox and len(bbox) == 4:
        row.bbox_west, row.bbox_south, row.bbox_east, row.bbox_north = [
            _num(v) for v in bbox]
    if not row.temporal_start and summary.get('temporal_start'):
        row.temporal_start = _date(summary['temporal_start'])
    if not row.temporal_end and summary.get('temporal_end'):
        row.temporal_end = _date(summary['temporal_end'])
    if summary.get('grain'):
        row.grain = summary['grain']
    extras = load_extras(row.extras)
    extras['processing_summary'] = {
        'records': summary.get('record_count'),
        'sites': summary.get('site_count'),
        'parameters': summary.get('parameter_count'),
        'rejected': sum((summary.get('rejected') or {}).values()),
        'dropped': len(summary.get('dropped') or []),
        'warnings': list(summary.get('warnings') or [])[:10],
        'generation': generation,
    }
    row.extras = dump_extras(extras)
    row.processing_status = u'ready'
    row.processing_error = None
    row.processed_at = db._utcnow()
    row.modified = db._utcnow()
    Session.add(row)
    (Session.query(db.C4wDashboardBundle)
     .filter(db.C4wDashboardBundle.dataset_id == row.id,
             db.C4wDashboardBundle.generation < generation)
     .delete(synchronize_session=False))
    Session.commit()


def _num(value):
    try:
        return round(float(value), 6)
    except (TypeError, ValueError):
        return None


def _date(value):
    try:
        return datetime.datetime.strptime(u'%s' % value, '%Y-%m-%d').date()
    except (TypeError, ValueError):
        return None

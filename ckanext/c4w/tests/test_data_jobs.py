# encoding: utf-8
"""The CKAN bridge of the pipeline, end to end on an in-memory database.

``jobs.process_dataset`` is exercised with the fetch monkeypatched to hand
over the fixture file, so this proves the wiring -- dataset row, file row,
mapping, bundle blobs, generations, write-back, failure path -- without
Azure, without a web server and without a site config. Skips without CKAN
and runs inside the ckan-dev container.
"""
import gzip
import json
import shutil
from pathlib import Path

import pytest

try:
    import sqlalchemy as sa
    import ckan  # noqa: F401
    from ckanext.c4w import db
    HAVE_CKAN = True
except Exception:  # pragma: no cover
    HAVE_CKAN = False

pytestmark = pytest.mark.skipif(
    not HAVE_CKAN, reason='requires CKAN (ckan.model + sqlalchemy)')

FIXTURES = Path(__file__).resolve().parent / 'fixtures'


@pytest.fixture
def session():
    engine = sa.create_engine('sqlite://')
    db.ensure_mappers()
    db.metadata.create_all(bind=engine, tables=list(db._ALL_TABLES))
    db.Session.remove()
    db.Session.configure(bind=engine)
    try:
        yield db.Session
    finally:
        db.Session.remove()
        engine.dispose()


def _seed(session, fixture_name):
    from ckanext.c4w.data import sniff

    fixture = FIXTURES / fixture_name
    raw = fixture.read_bytes()
    sniffed = sniff.sniff_bytes(raw[:256 * 1024], size_bytes=len(raw))
    dataset = db.C4wDataset(
        slug=u'fw', title=u'FreshWater sample',
        mapping_json=json.dumps(sniffed['proposal']),
        processing_status=u'mapped', publisher=u'Earthwatch')
    session.add(dataset)
    session.flush()
    session.add(db.C4wDatasetFile(
        dataset_id=dataset.id, kind=u'data', original_name=fixture_name,
        url=u'https://example.org/static/c4w_data/%s' % fixture_name,
        size_bytes=len(raw), sniff_json=json.dumps(sniffed)))
    session.commit()
    return dataset, fixture


@pytest.fixture
def fake_storage(monkeypatch):
    """Serve fixture files instead of the object store."""
    from ckanext.c4w.data import fetch
    from ckanext.c4w.logic import uploads

    def fetch_to_temp(url, allowed_hosts, max_bytes, dest_dir, timeout=600,
                      allow_insecure=False):
        assert 'example.org' in allowed_hosts
        target = Path(dest_dir) / url.rsplit('/', 1)[-1]
        shutil.copy(FIXTURES / target.name, target)
        return str(target)

    monkeypatch.setattr(fetch, 'fetch_to_temp', fetch_to_temp)
    monkeypatch.setattr(uploads, 'storage_hosts', lambda: {'example.org'})


def test_process_dataset_writes_a_bundle_and_bumps_the_generation(
        session, fake_storage):
    from ckanext.c4w.data import jobs

    dataset, _fixture = _seed(session, 'freshwater_wide_sample.csv')
    assert jobs.process_dataset(dataset.id) == u'ready'

    row = session.query(db.C4wDataset).one()
    assert row.processing_status == u'ready'
    assert row.processing_error is None
    assert row.bundle_generation == 1
    assert row.site_count == 8
    assert row.record_count > 0
    assert row.parameter_count >= 1
    assert row.temporal_start is not None and row.temporal_end is not None
    assert row.bbox_west is not None and row.bbox_west <= row.bbox_east
    assert row.processed_at is not None
    summary = json.loads(row.extras)['processing_summary']
    assert summary['sites'] == 8 and summary['generation'] == 1

    blobs = (session.query(db.C4wDashboardBundle)
             .filter_by(dataset_id=row.id, generation=1).all())
    names = {b.name for b in blobs}
    assert {'meta.json', 'sites.json', 'stats.json', 'p/0.json'} <= names
    meta_blob = next(b for b in blobs if b.name == 'meta.json')
    meta = json.loads(gzip.decompress(meta_blob.body))
    assert meta['schema'] == 1
    assert meta['dataset']['slug'] == u'fw'
    assert meta['dataset']['credit'] == u'Earthwatch'
    assert meta['siteCount'] == 8
    assert len(meta['parameters']) == row.parameter_count
    assert meta_blob.gz_size == len(meta_blob.body)
    assert meta_blob.raw_size == len(gzip.decompress(meta_blob.body))
    sites = json.loads(gzip.decompress(
        next(b for b in blobs if b.name == 'sites.json').body))
    assert len(sites['lat']) == 8

    # A re-run writes generation 2 and drops generation 1.
    assert jobs.process_dataset(dataset.id, force=True) == u'ready'
    row = session.query(db.C4wDataset).one()
    assert row.bundle_generation == 2
    generations = {g for (g,) in session.query(
        db.C4wDashboardBundle.generation).filter_by(dataset_id=row.id)}
    assert generations == {2}


def test_process_dataset_marks_failure_with_a_readable_message(
        session, fake_storage):
    from ckanext.c4w.data import jobs

    dataset, _fixture = _seed(session, 'freshwater_wide_sample.csv')
    dataset.mapping_json = json.dumps({
        'layout': 'wide', 'site': {'lat': 'nope', 'lon': 'lon'},
        'date': {'column': 'date', 'grain': 'year'}, 'parameters': []})
    session.commit()
    assert jobs.process_dataset(dataset.id) == u'failed'
    row = session.query(db.C4wDataset).one()
    assert row.processing_status == u'failed'
    assert row.processing_error
    assert 'Traceback' not in row.processing_error
    assert row.bundle_generation == 0


def test_dispatch_runs_small_files_inline_and_queues_large_ones(
        session, fake_storage, monkeypatch):
    import ckan.plugins.toolkit as tk
    from ckanext.c4w.data import jobs

    dataset, _fixture = _seed(session, 'freshwater_wide_sample.csv')
    assert jobs.dispatch(dataset.id) == u'ready'

    # Pretend the file is far larger than the inline cap.
    file_row = session.query(db.C4wDatasetFile).one()
    file_row.size_bytes = 500 * 1024 * 1024
    session.commit()
    monkeypatch.setattr(tk, 'enqueue_job',
                        lambda *a, **k: pytest.fail('must not enqueue'))
    assert jobs.dispatch(dataset.id, force=True) == u'queued'
    assert session.query(db.C4wDataset).one().processing_status == u'queued'

import csv
import json

from ckanext.c4w.data import references, pipeline, mapping
from ckanext.c4w.migrate.references import load


def make_snapshot(tmp_path, source):
    data = tmp_path / 'web/public/data'
    data.mkdir(parents=True)
    meta = {'source': 'https://example.org/source', 'credit': 'Source institution',
            'generatedAt': '2026-09-08', 'count': 80}
    if source == 'freshwater':
        path = data / 'freshwater-watch.csv'
        columns = ['id', 'site', 'country', 'date', 'lat', 'lon', 'nitrate',
                   'phosphate', 'turbidity', 'ph', 'temp', 'conductivity',
                   'oxygen', 'body', 'land_use', 'group', 'nitrate_cat', 'phosphate_cat']
        with path.open('w') as f:
            out = csv.writer(f); out.writerow(columns)
            for i in range(80):
                out.writerow([i, 'River', 'Chile', '2020-01-%02d' % (1+i%28),
                              -33, -70, i+1, i/10, i+2, 7+i/100, -10+i/10,
                              i+10, i/10, 'River', 'Forest', 'Group', '1-2', '0.1-0.2'])
    else:
        cache = tmp_path / 'scripts/cache'; cache.mkdir(parents=True)
        path = cache / 'arsenic.csv'
        meta['parameters'] = [{'code': 'As', 'labelEn': 'Arsenic', 'unit': 'mg/L'}]
        with path.open('w') as f:
            out = csv.writer(f)
            out.writerow(['id','Latitude','Longitude','time','Parameter Code','Value','Unit'])
            for i in range(80):
                out.writerow(['CHL001',-33,-70,'12/31/%d 12:00' % (1900+i),'As',i+1,'mg/l'])
    (data / 'meta.json').write_text(json.dumps(meta))
    return path


def test_freshwater_uses_sites_not_observation_ids_and_keeps_negative_temperature(tmp_path):
    make_snapshot(tmp_path, 'freshwater')
    p = references.profile('freshwater', tmp_path)
    assert p['mapping']['site']['id'] is None
    assert p['metadata']['license_id'] == 'notspecified'
    assert 'interval midpoints' in p['metadata']['data_quality_note']
    assert mapping.validate_mapping(p['mapping'], None) == {}
    result = pipeline.run(p['paths'], p['mapping'], p['metadata'], options=p['options'])
    assert result.summary['site_count'] == 1
    meta = json.loads(result.files['meta.json'])
    temp = next(i for i, x in enumerate(meta['parameters']) if x['key']=='temp')
    assert min(json.loads(result.files['p/%d.json' % temp])['value']) < 0


def test_gemstat_maps_explicit_parameters_and_preserves_unit_conversion(tmp_path):
    path = make_snapshot(tmp_path, 'gemswater')
    lines = path.read_text().splitlines()
    lines.insert(1, 'CHL001,-33,-70,12/31/1900 12:00,As,1,%')
    path.write_text('\n'.join(lines) + '\n')
    (tmp_path / 'scripts/cache/stations.csv').write_text(
        'GEMS Station Number,Country Name\nCHL001,Chile\n')
    p = references.profile('gemswater', tmp_path)
    assert p['mapping']['long']['discover'] is False
    assert len(p['paths']) == 2 and len(p['data_paths']) == 1
    assert p['files'][1]['kind'] == 'attachment'
    result = pipeline.run(p['data_paths'], p['mapping'], p['metadata'], options=p['options'])
    meta = json.loads(result.files['meta.json'])
    assert meta['parameters'][0]['unit'] == 'mg/L'
    assert result.summary['record_count'] == 80
    assert result.summary['rejected']['unit_mismatch:As:%'] == 1
    assert json.loads(result.files['p/0.json'])['value'][0] == 1


def test_dry_run_needs_no_ckan_and_hash_changes_with_source_bytes(tmp_path):
    path = make_snapshot(tmp_path, 'freshwater')
    first = load('freshwater', tmp_path, 'unused', dry_run=True)
    assert first['dry_run'] and first['uploaded'] == 0
    assert references.profile('freshwater', tmp_path)['fingerprint'] == first['fingerprint']
    with path.open('a') as f:
        f.write('\n')
    assert references.profile('freshwater', tmp_path)['fingerprint'] != first['fingerprint']


def test_reference_import_reuses_files_and_can_publish_ready_dataset(tmp_path, monkeypatch):
    import pytest
    pytest.importorskip('ckan')
    import sqlalchemy as sa
    from ckan import model
    from ckan.model.meta import Session
    from ckanext.c4w import db
    from ckanext.c4w.data import jobs
    from ckanext.c4w.logic import uploads
    make_snapshot(tmp_path, 'gemswater')
    (tmp_path / 'scripts/cache/stations.csv').write_text(
        'GEMS Station Number,Country Name\nCHL001,Chile\n')
    engine = sa.create_engine('sqlite://')
    db.ensure_mappers()
    db.metadata.create_all(bind=engine, tables=list(db._ALL_TABLES))
    user_table = sa.inspect(model.User).local_table
    monkeypatch.setattr(user_table.c.plugin_extras, 'type', sa.JSON())
    user_table.create(bind=engine)
    Session.remove(); Session.configure(bind=engine)
    stored = []
    def store(upload, dataset_id, kind):
        raw = upload.read()
        import hashlib
        stored.append((upload.filename, kind))
        return dict(kind=kind, original_name=upload.filename,
                    stored_name=upload.filename, url='https://example.org/'+upload.filename,
                    size_bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest(),
                    cleanup=lambda: None)
    monkeypatch.setattr(uploads, 'store_data_file', lambda f, d: store(f, d, 'data'))
    monkeypatch.setattr(uploads, 'store_attachment', lambda f, d: store(f, d, 'attachment'))
    def process(dataset_id):
        row = Session.query(db.C4wDataset).filter_by(id=dataset_id).one()
        row.processing_status = 'ready'; Session.commit()
        return 'ready'
    monkeypatch.setattr(jobs, 'process_dataset', process)
    try:
        Session.add(model.User(name='reference-admin', sysadmin=True, state='active'))
        Session.commit()
        first = load('gemswater', tmp_path, 'reference-admin', process=True)
        assert first['uploaded'] == 2 and not first['approved']
        second = load('gemswater', tmp_path, 'reference-admin', process=True, publish=True)
        assert second['unchanged'] and second['approved']
        assert second['dataset_id'] == first['dataset_id']
        assert len(stored) == 2
        assert Session.query(db.C4wDataset).count() == 1
        assert Session.query(db.C4wDatasetFile).filter_by(kind='attachment').count() == 1
        with (tmp_path / 'scripts/cache/stations.csv').open('a') as f:
            f.write('CHL002,Chile\n')
        third = load('gemswater', tmp_path, 'reference-admin', process=True, publish=True)
        assert third['uploaded'] == 1 and third['reused'] == 1
        assert Session.query(db.C4wDatasetFile).count() == 2
    finally:
        Session.remove(); engine.dispose()

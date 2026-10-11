"""Conservation and scientific semantics of the complete GFQA provider."""
import csv
import io
import json
from pathlib import Path
import zipfile

import pytest

pytest.importorskip('duckdb')
from ckanext.c4w.archive import importer, query


def csv_bytes(columns, rows):
    out = io.StringIO(newline='')
    writer = csv.writer(out)
    writer.writerow(columns)
    writer.writerows(rows)
    return out.getvalue().encode('cp1252')


@pytest.fixture
def archive(tmp_path):
    source = tmp_path / 'fixture.zip'
    station_columns = ['GEMS Station Number', 'Station Identifier', 'Country Name',
                       'Latitude', 'Longitude', 'Water Type']
    station = ['FRA00001', 'Rivière', 'France', '48', '2', 'River station']
    measurements = []
    # Repeated raw rows are real archive rows: keep both. With the third
    # station the raw median (1) differs from median of station medians (51).
    for station_id, value, flag, quality in [('FRA00001', '1', '', 'Fair'),
            ('FRA00001', '1', '', 'Fair'), ('FRA00002', '100', '', 'Good'),
            ('FRA00001', '0.2', '<', 'Fair'), ('FRA00001', '900', '', 'Suspect')]:
        measurements.append([station_id, '2020-01-01', '12:00', '0.5', 'pH', '0', flag,
                             value, '---', quality, '', '', 'https://creativecommons.org/licenses/by/4.0/'])
    measurements.extend([
        ['FRA00001','2021-01-01','00:00','','TEMP','0','','-2','°C','Fair','','',''],
        ['FRA00001','2021-01-01','00:00','','As','0','','1','mg/l','Fair','','',''],
        ['FRA00001','2021-01-01','00:00','','As','0','','1000','µg/l','Fair','','',''],
        ['FRA00001','2021-01-01','00:00','','As','0','','2','µg/g','Fair','','',''],
        ['MISSING','invalid','00:00','','EXTRA','0','','4','µg/l','Fair','','',''],
        ['FRA00001','2020-01-01','00:00','','pH','0','','8','---','Fair','Time Integration','',''],
    ])
    with zipfile.ZipFile(source, 'w', zipfile.ZIP_DEFLATED) as z:
        z.writestr('GEMStat_station_metadata.csv', csv_bytes(station_columns,
            [station, station, ['FRA00002','Deuxième','France','48.1','2.1','River station']]))
        z.writestr('GEMStat_parameter_metadata.csv', csv_bytes(
            ['Parameter Code','Parameter Long Name','Parameter Group'],
            [['pH','pH','Chemical'],['TEMP','Temperature','Physical'],['As','Arsenic','Chemical'],['EMPTY','No observations','Chemical']]))
        z.writestr('GEMStat_methods_metadata.csv', csv_bytes(
            ['Parameter Code','Parameter Long Name'], [['pH','pH'],['EXTRA','Extra variable']]))
        z.writestr('README_output_format.txt', b'CC BY 4.0 fixture')
        z.writestr('Samples.csv', csv_bytes(list(importer.COLUMNS), measurements))
    root = tmp_path / 'archive'
    manifest = importer.run(root, source, expected_md5=None, progress=lambda *a, **kw: None)
    return root, manifest


def test_conserves_every_raw_observation_and_deduplicates_only_dimension(archive):
    root, manifest = archive
    assert manifest['observations'] == 11
    assert manifest['dimensions']['station_metadata_rows'] == 3
    assert manifest['dimensions']['station_ids'] == 2
    assert manifest['dimensions']['identical_station_duplicates'] == 1
    assert manifest['observed_parameter_codes'] == 4
    assert {p['code'] for p in manifest['parameters']} == {'pH','TEMP','As','EXTRA','EMPTY'}
    directory, _ = query.open_manifest(root)
    assert (directory / 'original' / 'Samples.csv').read_bytes().count(b'FRA00001') == 9
    result = query.execute(root, {'parameter':'pH'})
    assert result['summary']['observations'] == 5  # No multiplication from metadata duplicates.
    assert result['summary']['eligible'] == 3
    assert result['summary']['censored'] == 1
    assert result['summary']['median'] == 1
    assert result['series'][0]['value'] == 1  # Not median of station medians.
    assert result['observations'][0]['station_name'] == 'Rivière'
    assert result['observations'][0]['local_time'] == '12:00'
    assert len({r['source_row'] for r in result['observations']}) == 5


def test_units_temperature_flags_and_integration_remain_distinct(archive):
    root, _ = archive
    assert query.execute(root, {'parameter':'TEMP'})['summary']['median'] == -2
    ars = query.execute(root, {'parameter':'As','unit':'µg/L'})
    assert ars['summary']['observations'] == 2
    assert ars['summary']['median'] == 1000
    assert query.execute(root, {'parameter':'As','unit':'µg/g'})['summary']['median'] == 2
    censored = query.execute(root, {'parameter':'pH','flag':'<'})
    assert censored['summary']['observations'] == 1
    assert censored['summary']['median'] is None
    assert censored['observations'][0]['reported_value'] == '0.2'
    integrated = query.execute(root, {'parameter':'pH','integration':'Time Integration'})
    assert integrated['summary']['median'] == 8
    assert query.execute(root, {'parameter':'EXTRA'})['summary']['observations'] == 0
    assert len(query.execute(root, {'parameter':'EXTRA','operation':'observations'})['observations']) == 1
    assert not query.execute(root, {'parameter':'EXTRA','operation':'observations','from_year':2020})['observations']


def test_empty_variables_remain_discoverable_and_import_is_idempotent(archive):
    root, before = archive
    after = importer.run(root, expected_md5=None, progress=lambda *a, **kw: None)
    assert after == before
    result = query.execute(root, {'parameter':'EMPTY'})
    assert result['empty'] and not result['observations']


@pytest.mark.parametrize('bad', [
    {'parameter':"pH'; DROP TABLE source; --"}, {'unit':'not a unit'},
    {'operation':'sql'}, {'path':'/etc/passwd'}, {'country':'invented'},
    {'page':-1}, {'page':0.5}, {'from_year':2024,'to_year':1906},
    {'bbox':[180,-90,-180,90]}, {'bbox':[float('nan'),0,1,1]},
    {'flag':'invalid'}, {'station':False},
])
def test_rejects_invalid_or_unbounded_queries(archive, bad):
    _, manifest = archive
    with pytest.raises(ValueError):
        query.validate(bad, manifest)


def test_derivatives_are_explicit_and_never_reaggregate_medians(archive, tmp_path):
    root, _ = archive
    output = tmp_path / 'derived.csv'
    query.export_derivative(root, 'pH', output)
    with output.open() as stream:
        rows = list(csv.DictReader(stream))
    assert 'station_year_median' in rows[0]
    assert 'measurement_count' in rows[0]
    assert sum(int(r['measurement_count']) for r in rows) == 4  # Includes separate integrated group.
    assert len(rows) == 3

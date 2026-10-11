"""Bounded, parameterized analytics over immutable archive generations."""
import csv
import io
import json
import math
from pathlib import Path
import re
import threading

from .importer import connect

GENERATION = re.compile(r'^v3-[a-f0-9]{16}$')


def open_manifest(root, generation=None):
    root = Path(root)
    if generation is None:
        generation = json.loads((root / 'active.json').read_text())['generation']
    if not GENERATION.fullmatch(generation):
        raise ValueError('Invalid archive generation')
    directory = root / 'generations' / generation
    manifest = json.loads((directory / 'manifest.json').read_text())
    if not manifest.get('complete'):
        raise ValueError('Archive generation is not ready')
    return directory, manifest


def validate(data, manifest):
    if not isinstance(data, dict):
        raise ValueError('A query object is required')
    allowed = {'operation', 'parameter', 'unit', 'country', 'station', 'from_year', 'to_year',
               'bbox', 'zoom', 'page', 'flag', 'integration'}
    if set(data) - allowed:
        raise ValueError('Unknown archive query fields')
    p = next((p for p in manifest['parameters'] if p['code'] == data.get('parameter', 'pH')), None)
    if p is None:
        raise ValueError('Unknown parameter')
    op = data.get('operation', 'dashboard')
    if op not in ('dashboard', 'summary', 'stations', 'series', 'observations', 'export'):
        raise ValueError('Unsupported operation')
    out = {'operation': op, 'parameter': p['code'], 'unit': data.get('unit', next(iter(p['units']), ''))}
    if p['units'] and out['unit'] not in p['units']:
        raise ValueError('Unit does not belong to this parameter')
    for key in ('country', 'station', 'integration'):
        value = data.get(key, '')
        if not isinstance(value, str) or len(value) > 160 or '\x00' in value:
            raise ValueError('Invalid ' + key)
        out[key] = value
    if out['country'] and out['country'] not in manifest['dimensions']['countries']:
        raise ValueError('Unknown country')
    out['flag'] = data.get('flag', 'all')
    if out['flag'] not in ('all', 'unflagged', '<', '>', '~'):
        raise ValueError('Unknown value flag')
    for key, default, lower, upper in [('from_year', 1900, 1800, 2200), ('to_year', 2024, 1800, 2200),
                                      ('page', 0, 0, 1000000), ('zoom', 2, 0, 20)]:
        value = data.get(key, default)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            raise ValueError('Invalid ' + key)
        if not lower <= value <= upper or (key != 'zoom' and int(value) != value):
            raise ValueError('Invalid ' + key)
        out[key] = value
    if out['from_year'] > out['to_year']:
        raise ValueError('The date range is reversed')
    bbox = data.get('bbox')
    if bbox is not None:
        if not isinstance(bbox, list) or len(bbox) != 4 or any(isinstance(x, bool) or not isinstance(x, (int, float)) or not math.isfinite(x) for x in bbox):
            raise ValueError('Invalid map bounds')
        if not (-180 <= bbox[0] <= bbox[2] <= 180 and -90 <= bbox[1] <= bbox[3] <= 90):
            raise ValueError('Invalid map bounds')
        out['bbox'] = bbox
    return out


def _rows(cursor):
    keys = [x[0] for x in cursor.description]
    def safe(value):
        if isinstance(value, float) and not math.isfinite(value):
            return None
        if hasattr(value, 'isoformat'):
            return value.isoformat()
        return value
    return [dict(zip(keys, (safe(v) for v in row))) for row in cursor.fetchall()]


def execute(root, data, generation=None):
    directory, manifest = open_manifest(root, generation)
    q = validate(data, manifest)
    parameter = next(p for p in manifest['parameters'] if p['code'] == q['parameter'])
    if not parameter['files']:
        return {'generation': manifest['generation'], 'query': q, 'empty': True,
                'summary': {'observations': 0, 'eligible': 0, 'stations': 0, 'median': None},
                'series': [], 'stations': [], 'histogram': [], 'observations': [], 'integrations': []}
    paths = [str(directory / 'parquet' / name) for name in parameter['files']]
    workdir = Path(root) / 'query-work'
    workdir.mkdir(exist_ok=True)
    conn = connect(workdir)
    timer = threading.Timer(45, conn.interrupt)
    timer.daemon = True
    timer.start()
    try:
        conn.execute('CREATE VIEW source AS SELECT * FROM read_parquet(' +
                     '[' + ','.join("'" + p.replace("'", "''") + "'" for p in paths) + '])')
        conn.execute('CREATE TABLE station_meta AS SELECT * FROM read_parquet(?)', [str(directory / 'stations.parquet')])
        filters = ['o.parameter_code=?', 'o.unit=?', '(o.sample_date IS NULL OR year(o.sample_date) BETWEEN ? AND ?)']
        args = [q['parameter'], q['unit'], q['from_year'], q['to_year']]
        if q['country']:
            filters.append('s.country=?'); args.append(q['country'])
        if q['station']:
            filters.append('o.station_id=?'); args.append(q['station'])
        if q['flag'] != 'all':
            filters.append('trim(o.value_flags)=?'); args.append('' if q['flag'] == 'unflagged' else q['flag'])
        # One integration class at a time. Empty means discrete observations.
        filters.append('o.integrated_value=?'); args.append(q['integration'])
        if q.get('bbox'):
            filters.extend(['s.longitude BETWEEN ? AND ?', 's.latitude BETWEEN ? AND ?'])
            w, south, e, north = q['bbox']; args.extend([w, e, south, north])
        conn.execute('CREATE TABLE filtered AS SELECT o.*, s.country, s.name AS station_name, '
                     's.latitude, s.longitude, s.water_type FROM source o LEFT JOIN station_meta s '
                     'USING(station_id) WHERE ' + ' AND '.join(filters), args)
        result = {'generation': manifest['generation'], 'query': q,
                  'statistics_policy': manifest['statistics_policy']}
        op = q['operation']
        if op in ('dashboard', 'summary'):
            result['summary'] = _rows(conn.execute("""SELECT count(*) AS observations,
                count(*) FILTER(WHERE eligible) AS eligible,
                count(*) FILTER(WHERE trim(value_flags) IN ('<','>')) AS censored,
                count(DISTINCT station_id) AS stations,
                median(value) FILTER(WHERE eligible) AS median,
                max(value) FILTER(WHERE eligible) AS maximum,
                count(*) FILTER(WHERE latitude IS NULL OR longitude IS NULL) AS unmapped
                FROM filtered"""))[0]
            result['integrations'] = [r[0] for r in conn.execute(
                'SELECT DISTINCT integrated_value FROM source WHERE parameter_code=? ORDER BY 1 LIMIT 50',
                [q['parameter']]).fetchall()]
        if op in ('dashboard', 'series'):
            result['series'] = _rows(conn.execute("""SELECT year(sample_date) AS year,
                median(value) AS value, count(*) AS samples FROM filtered
                WHERE eligible AND sample_date IS NOT NULL GROUP BY 1 ORDER BY 1"""))
        if op in ('dashboard', 'stations'):
            cell = max(0.002, 360 / (2 ** q['zoom'] * 8))
            result['stations'] = _rows(conn.execute("""SELECT floor(longitude/?) AS grid_x,
                floor(latitude/?) AS grid_y, avg(latitude) AS latitude, avg(longitude) AS longitude,
                median(value) AS value, count(*) AS samples, count(DISTINCT station_id) AS stations,
                CASE WHEN count(DISTINCT station_id)=1 THEN min(station_id) END AS station_id,
                CASE WHEN count(DISTINCT station_id)=1 THEN min(station_name) END AS station_name
                FROM filtered WHERE eligible AND latitude BETWEEN -90 AND 90 AND longitude BETWEEN -180 AND 180
                GROUP BY 1,2 ORDER BY samples DESC LIMIT 2001""", [cell, cell]))
            result['map_truncated'] = len(result['stations']) > 2000
            result['stations'] = result['stations'][:2000]
            result['grid_degrees'] = cell
        if op == 'dashboard':
            span = conn.execute('SELECT min(value), max(value) FROM filtered WHERE eligible').fetchone()
            if span[0] is not None:
                width = (span[1] - span[0]) / 20 or 1
                result['histogram'] = _rows(conn.execute('SELECT least(19, floor((value-?)/?))::INTEGER AS bin, count(*) AS count FROM filtered WHERE eligible GROUP BY 1 ORDER BY 1', [span[0], width]))
                result['histogram_min'] = span[0]; result['histogram_width'] = width
            else:
                result['histogram'] = []
        if op in ('dashboard', 'observations', 'export'):
            maximum = 10000 if op == 'export' else 100
            result['observations'] = _rows(conn.execute("""SELECT station_id, station_name, country,
                sample_date_raw AS date, sample_time AS local_time, depth_raw AS depth,
                parameter_code, value_raw AS reported_value, unit_raw AS reported_unit,
                value_flags, data_quality, integrated_value, method_code, remark,
                license_information, source_file, source_row FROM filtered
                ORDER BY source_file, source_row LIMIT ? OFFSET ?""", [maximum + 1, int(q['page']) * maximum]))
            result['has_more'] = len(result['observations']) > maximum
            result['observations'] = result['observations'][:maximum]
        return result
    finally:
        timer.cancel()
        conn.close()


def export_derivative(root, code, output, generation=None):
    """Explicit station-year medians; never substitute these for raw data."""
    directory, manifest = open_manifest(root, generation)
    parameter = next(p for p in manifest['parameters'] if p['code'] == code)
    conn = connect(Path(root) / 'work')
    try:
        paths = [str(directory / 'parquet' / p) for p in parameter['files']]
        rows = conn.execute("""SELECT o.station_id, s.name AS station_name, s.country,
            s.latitude, s.longitude, year(o.sample_date) AS year, o.parameter_code, o.unit,
            o.integrated_value, median(o.value) AS station_year_median,
            count(*) AS measurement_count, 'uncensored Good/Fair/Unknown or unspecified' AS quality_policy,
            'v3' AS archive_version, '10.5281/zenodo.18459694' AS source_doi
            FROM read_parquet(?) o LEFT JOIN read_parquet(?) s USING(station_id)
            WHERE o.parameter_code=? AND o.eligible AND o.sample_date IS NOT NULL
            GROUP BY ALL ORDER BY station_id, year, unit, integrated_value""",
            [paths, str(directory / 'stations.parquet'), code])
        with open(output, 'w', encoding='utf-8', newline='') as stream:
            writer = csv.writer(stream)
            writer.writerow([x[0] for x in rows.description])
            while True:
                batch = rows.fetchmany(10000)
                if not batch:
                    break
                writer.writerows(batch)
    finally:
        conn.close()

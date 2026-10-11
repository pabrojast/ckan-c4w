"""Lossless, restartable GFQA import. Run with ``python -m ...archive.importer``.

Original bytes remain authoritative. Every observation gets a source filename
and ordinal; repeated measurements are never deduplicated. DuckDB reads one
transcoded CSV at a time and writes bounded Parquet row groups, without pandas.
"""
import argparse
import collections
import csv
import datetime
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import urllib.request
import zipfile

SOURCE_URL = 'https://zenodo.org/records/18459694/files/GFQA_v3.zip?download=1'
DOI = '10.5281/zenodo.18459694'
SOURCE_MD5 = '00f3ea19ce529753977eb3aeb08fbc47'
VERSION = 'v3'
COLUMNS = {
    'GEMS Station Number': 'station_id', 'Sample Date': 'sample_date_raw',
    'Sample Time': 'sample_time', 'Depth': 'depth_raw',
    'Parameter Code': 'parameter_code', 'Analysis Method Code': 'method_code',
    'Value Flags': 'value_flags', 'Value': 'value_raw', 'Unit': 'unit_raw',
    'Data Quality': 'data_quality', 'Integrated Value': 'integrated_value',
    'Remark': 'remark', 'License Information': 'license_information',
}


def digest(path, algorithm='sha256'):
    h = hashlib.new(algorithm)
    with open(path, 'rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def write_json(path, data):
    path = Path(path)
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    os.replace(temporary, path)


def quote(name):
    return '"' + name.replace('"', '""') + '"'


def literal(value):
    """COPY filenames cannot use bound parameters in DuckDB 1.3."""
    return "'" + str(value).replace("'", "''") + "'"


def connect(workdir, memory='1GB'):
    import duckdb
    conn = duckdb.connect()
    conn.execute("SET memory_limit = ?", [memory])
    conn.execute('SET threads = 2')
    conn.execute('SET preserve_insertion_order = true')
    conn.execute('SET temp_directory = ?', [str(Path(workdir) / 'spill')])
    return conn


def metadata_rows(path):
    # V3's original encoding is Windows-1252. Never use errors='replace':
    # replacement characters would corrupt units and identifiers silently.
    with path.open(encoding='cp1252', newline='') as stream:
        return list(csv.DictReader(stream))


def build_dimensions(original, generation, conn):
    stations_raw = metadata_rows(original / 'GEMStat_station_metadata.csv')
    stations = {}
    for row in stations_raw:
        key = row['GEMS Station Number']
        if key in stations and stations[key] != row:
            raise ValueError('Conflicting station metadata: ' + key)
        stations[key] = row
    parameters = metadata_rows(original / 'GEMStat_parameter_metadata.csv')
    methods = metadata_rows(original / 'GEMStat_methods_metadata.csv')
    names = {r['Parameter Code']: r for r in parameters}
    for method in methods:
        code = method['Parameter Code']
        if code not in names:
            names[code] = {'Parameter Code': code,
                          'Parameter Long Name': method['Parameter Long Name'] or code,
                          'Parameter Group': '', 'metadata_incomplete': True}
    def number(value):
        try:
            return float(value)
        except (TypeError, ValueError):
            return None
    # Bulk-load the small dimension; executemany performs a transaction for
    # every station and is needlessly slow on persistent volumes.
    station_csv = generation / 'stations.csv'
    with station_csv.open('w', encoding='utf-8', newline='') as out:
        writer = csv.writer(out)
        writer.writerow(['station_id','name','country','latitude','longitude','water_type'])
        writer.writerows((key, r['Station Identifier'], r['Country Name'], number(r['Latitude']),
                          number(r['Longitude']), r['Water Type']) for key, r in stations.items())
    conn.execute('CREATE TABLE stations AS SELECT station_id, name, country, '
                 'try_cast(latitude AS DOUBLE) AS latitude, try_cast(longitude AS DOUBLE) AS longitude, water_type '
                 'FROM read_csv(?, header=true, all_varchar=true)', [str(station_csv)])
    conn.execute('COPY stations TO ' + literal(generation / 'stations.parquet') + ' (FORMAT PARQUET, COMPRESSION ZSTD)')
    write_json(generation / 'parameters.json', names)
    write_json(generation / 'methods.json', methods)
    return {'station_metadata_rows': len(stations_raw), 'station_ids': len(stations),
            'identical_station_duplicates': len(stations_raw) - len(stations),
            'parameter_metadata_codes': len(parameters),
            'method_parameter_codes': len({r['Parameter Code'] for r in methods}),
            'countries': sorted({r['Country Name'] for r in stations.values()})}, names


def import_file(source, target, conn, workdir):
    utf8 = Path(workdir) / 'current.csv'
    with source.open(encoding='cp1252', newline='') as inp, utf8.open('w', encoding='utf-8', newline='') as out:
        shutil.copyfileobj(inp, out, length=1024 * 1024)
    with utf8.open(encoding='utf-8', newline='') as inp:
        header = next(csv.reader(inp))
    if header != list(COLUMNS):
        raise ValueError('Unexpected GFQA columns in ' + source.name)
    select = ', '.join('coalesce(%s, \'\') AS %s' % (quote(a), quote(b)) for a, b in COLUMNS.items())
    # Keep the CSV as a streaming view. Materializing all 13 string columns of
    # a large pesticide file before COPY causes unnecessary disk spilling.
    conn.execute('CREATE OR REPLACE VIEW observations AS SELECT ' + select +
                 ', row_number() OVER ()::BIGINT AS source_row, ' + literal(source.name) + '::VARCHAR AS source_file '
                 'FROM read_csv(' + literal(utf8) + ", header=true, all_varchar=true, delim=',', quote='\"', escape='\"', parallel=false, strict_mode=true)")
    # Normalization is derived. Raw units/values/flags remain in Parquet.
    conn.execute("""CREATE OR REPLACE VIEW normalized AS SELECT *,
        try_cast(sample_date_raw AS DATE) AS sample_date,
        try_cast(depth_raw AS DOUBLE) AS depth,
        CASE lower(trim(unit_raw)) WHEN 'mg/l' THEN 1000 WHEN 'g/l' THEN 1000000
            WHEN 'ng/l' THEN 0.001 WHEN 'pg/l' THEN 0.000001
            WHEN 'ng/ml' THEN 1 WHEN 'mg/m³' THEN 1 ELSE 1 END
            * try_cast(value_raw AS DOUBLE) AS value,
        CASE WHEN lower(trim(unit_raw)) IN ('mg/l','g/l','ng/l','pg/l','ng/ml','mg/m³','µg/l','ug/l','μg/l')
            THEN 'µg/L' WHEN parameter_code='pH' AND unit_raw IN ('---','') THEN 'pH'
            ELSE unit_raw END AS unit,
        (trim(value_flags)='' AND data_quality IN ('Good','Fair','Unknown','')
            AND isfinite(try_cast(value_raw AS DOUBLE))) AS eligible
        FROM observations""")
    temporary = target.with_suffix('.tmp.parquet')
    conn.execute('COPY normalized TO ' + literal(temporary) + ' (FORMAT PARQUET, COMPRESSION ZSTD, ROW_GROUP_SIZE 122880)')
    # Validation is computed from the persisted representation, not assumed.
    original_rows = conn.execute('SELECT count(*) FROM observations').fetchone()[0]
    written_rows = conn.execute('SELECT count(*) FROM read_parquet(?)', [str(temporary)]).fetchone()[0]
    if original_rows != written_rows:
        raise ValueError('Observation conservation failed for ' + source.name)
    params = conn.execute("""SELECT parameter_code, unit, count(*), count(*) FILTER(WHERE eligible),
        min(year(sample_date)), max(year(sample_date)), count(*) FILTER(WHERE sample_date IS NULL)
        FROM read_parquet(?) GROUP BY parameter_code, unit ORDER BY parameter_code, unit""", [str(temporary)]).fetchall()
    os.replace(temporary, target)
    utf8.unlink()
    return {'rows': original_rows, 'parquet': target.name, 'parquet_sha256': digest(target),
            'parameters': [{'code': p[0], 'unit': p[1], 'rows': p[2], 'eligible': p[3],
                            'min_year': p[4], 'max_year': p[5], 'invalid_dates': p[6]} for p in params]}


def run(root, source_zip=None, expected_md5=SOURCE_MD5, activate=True, progress=print):
    root = Path(root).resolve()
    root.mkdir(parents=True, exist_ok=True)
    archive = root / 'GFQA_v3.zip'
    if source_zip and Path(source_zip).resolve() != archive:
        shutil.copyfile(source_zip, archive)
    if not archive.exists():
        progress('Downloading immutable GFQA v3 ZIP', flush=True)
        partial = archive.with_suffix('.zip.partial')
        with urllib.request.urlopen(SOURCE_URL, timeout=180) as response, partial.open('wb') as out:
            shutil.copyfileobj(response, out, length=1024 * 1024)
        os.replace(partial, archive)
    if expected_md5 and digest(archive, 'md5') != expected_md5:
        raise ValueError('GFQA ZIP checksum does not match the pinned publication')
    fingerprint = digest(archive)
    generation = root / 'generations' / ('v3-' + fingerprint[:16])
    generation.mkdir(parents=True, exist_ok=True)
    manifest_path = generation / 'manifest.json'
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text())
        if manifest.get('complete'):
            if activate:
                write_json(root / 'active.json', {'generation': generation.name})
            return manifest
    original = generation / 'original'
    original.mkdir(exist_ok=True)
    parquet = generation / 'parquet'
    parquet.mkdir(exist_ok=True)
    workdir = root / 'work'
    workdir.mkdir(exist_ok=True)
    inventory = []
    with zipfile.ZipFile(archive) as z:
        for info in z.infolist():
            if Path(info.filename).name != info.filename or info.is_dir():
                raise ValueError('Archive contains an unexpected member path')
            path = original / info.filename
            if not path.exists() or path.stat().st_size != info.file_size:
                partial = path.with_suffix(path.suffix + '.tmp')
                with z.open(info) as inp, partial.open('wb') as out:
                    shutil.copyfileobj(inp, out, length=1024 * 1024)
                os.replace(partial, path)
            inventory.append({'name': info.filename, 'bytes': info.file_size,
                              'sha256': digest(path), 'encoding': 'cp1252'})
    conn = connect(workdir)
    dimensions, names = build_dimensions(original, generation, conn)
    manifest = {'schema': 1, 'version': VERSION, 'generation': generation.name,
        'doi': DOI, 'source_url': SOURCE_URL, 'license': 'CC-BY-4.0',
        'zip_sha256': fingerprint, 'zip_md5': digest(archive, 'md5'),
        'zip_bytes': archive.stat().st_size, 'files': inventory,
        'dimensions': dimensions, 'complete': False, 'observation_files': [],
        'statistics_policy': 'Uncensored finite values with Good/Fair/Unknown or unspecified quality; incompatible units and integration types are never merged.',
        'published_counts': {'measurements_minimum': 50000000, 'parameters': 622, 'stations': 22982}}
    for item in inventory:
        name = item['name']
        if not name.endswith('.csv') or name.startswith('GEMStat_'):
            continue
        target = parquet / (Path(name).stem + '.parquet')
        checkpoint = parquet / (Path(name).stem + '.json')
        if checkpoint.exists() and target.exists():
            result = json.loads(checkpoint.read_text())
            if result.get('source_sha256') != item['sha256'] or digest(target) != result['parquet_sha256']:
                raise ValueError('Checkpoint integrity failed: ' + name)
        else:
            progress('Importing ' + name, flush=True)
            result = import_file(original / name, target, conn, workdir)
            result.update(source_sha256=item['sha256'], source=name)
            write_json(checkpoint, result)
        manifest['observation_files'].append(result)
        write_json(manifest_path, manifest)
    observed = collections.defaultdict(lambda: {'rows': 0, 'eligible': 0, 'units': set(), 'files': [], 'min_year': None, 'max_year': None})
    for item in manifest['observation_files']:
        for p in item['parameters']:
            info = observed[p['code']]
            info['rows'] += p['rows']; info['eligible'] += p['eligible']
            info['units'].add(p['unit'])
            if item['parquet'] not in info['files']:
                info['files'].append(item['parquet'])
            for key, fn in [('min_year', min), ('max_year', max)]:
                if p[key] is not None:
                    info[key] = p[key] if info[key] is None else fn(info[key], p[key])
    catalogue = []
    for code in sorted(set(observed) | set(names)):
        meta = names.get(code, {})
        info = observed[code]
        catalogue.append(dict(info, code=code, label=meta.get('Parameter Long Name') or code,
            family=meta.get('Parameter Group') or '', units=sorted(info['units']),
            metadata_incomplete=bool(not meta or meta.get('metadata_incomplete'))))
    manifest['parameters'] = catalogue
    manifest['observations'] = sum(x['rows'] for x in manifest['observation_files'])
    manifest['observed_parameter_codes'] = sum(bool(p['rows']) for p in catalogue)
    manifest['complete'] = True
    manifest['completed_at'] = datetime.datetime.now(datetime.timezone.utc).isoformat()
    write_json(manifest_path, manifest)
    conn.close()
    if activate:
        write_json(root / 'active.json', {'generation': generation.name})
    progress('Complete: %s observations in %s files' % (manifest['observations'], len(manifest['observation_files'])), flush=True)
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', required=True)
    parser.add_argument('--zip')
    parser.add_argument('--version', choices=['v3'], default='v3')
    parser.add_argument('--no-activate', action='store_true')
    args = parser.parse_args()
    run(args.root, args.zip, activate=not args.no_activate)


if __name__ == '__main__':
    main()

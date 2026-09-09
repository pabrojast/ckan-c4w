"""Reproducible reference datasets, loaded only through the operator CLI.

Keep the original CSVs downloadable. These profiles supply explicit mappings
to the same streaming pipeline used by citizen uploads, not prebuilt charts.
"""
import csv
import hashlib
import json
from pathlib import Path

from ckanext.c4w.data import mapping

SOURCES = ('gemswater', 'freshwater')
VERSION = 1


def file_digest(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def profile(source, directory):
    """Read a local explorer snapshot without modifying it or importing CKAN."""
    if source not in SOURCES:
        raise ValueError('Unknown reference source')
    root = Path(directory).resolve()
    meta_path = root / 'web/public/data/meta.json'
    meta = json.loads(meta_path.read_text(encoding='utf-8'))
    if source == 'gemswater':
        paths = sorted((root / 'scripts/cache').glob('*.csv'))
        if not paths:
            raise ValueError('The GEMStat CSV cache is missing')
        params = [{
            'source': p['code'], 'key': p['code'],
            'label': p.get('labelEn') or p['code'],
            'family': p.get('familyEn'), 'unit': p['unit'], 'include': True,
            'expected_unit': p['unit'],
        } for p in meta['parameters']]
        spec = {
            'layout': 'long',
            'site': {'id': 'id', 'lat': 'Latitude', 'lon': 'Longitude',
                     'country_kind': 'site_prefix3'},
            'date': {'column': 'time', 'format': '%m/%d/%Y %H:%M', 'grain': 'year'},
            'long': {'parameter': 'Parameter Code', 'value': 'Value',
                     'unit': 'Unit', 'discover': False},
            'parameters': params,
        }
        title = 'UNEP GEMS/Water Global Freshwater Quality Archive'
        slug = 'gemstat'
        description = ('Water-quality measurements from the GEMStat archive. '
                       'Explore stations, parameters, countries and annual trends.')
        methodology = ('The dashboard shows medians by station, year and parameter. '
                       'Mass concentrations are normalised to compatible units. '
                       'Sediment units, missing parameter codes and unusable measurements '
                       'are excluded and counted in the processing report. Parameters '
                       'follow the selection in the reference explorer snapshot.')
        quality = ('Colours describe the distribution of this parameter, not a '
                   'regulatory water-quality classification. The original CSV files '
                   'retain measurement flags and quality information.')
        options = {'min_records': 50, 'min_distinct': 8}
    else:
        paths = [root / 'web/public/data/freshwater-watch.csv']
        definitions = (
            ('nitrate', 'Nitrate (interval midpoint)', 'mg/L'),
            ('phosphate', 'Phosphate (interval midpoint)', 'mg/L'),
            ('turbidity', 'Turbidity', 'NTU'), ('ph', 'pH', 'pH'),
            ('temp', 'Water temperature', '°C'),
            ('conductivity', 'Conductivity', 'µS/cm'),
            ('oxygen', 'Dissolved oxygen', 'mg/L'),
        )
        spec = {
            'layout': 'wide',
            # The CSV id identifies an observation, not a monitoring site.
            'site': {'id': None, 'name': 'site', 'lat': 'lat', 'lon': 'lon',
                     'country': 'country', 'country_kind': 'name'},
            'date': {'column': 'date', 'format': '%Y-%m-%d', 'grain': 'day'},
            'parameters': [{'source': key, 'label': label, 'unit': unit,
                            'include': True} for key, label, unit in definitions],
            'dimensions': [{'column': col, 'label': label} for col, label in (
                ('body', 'Water body'), ('land_use', 'Land use'), ('group', 'Group'))],
            # Negative temperatures are legitimate; preserve the source values.
            'filters': {'drop_negative': False},
        }
        title, slug = 'FreshWater Watch', 'freshwater-watch'
        description = ('Citizen-science freshwater observations collected by '
                       'FreshWater Watch / Earthwatch Europe, with location, date '
                       'and water-quality measurements.')
        methodology = ('Sites are identified by name and coordinates, not observation ID. '
                       'Measurements are aggregated by site and day using the median. '
                       'Site-level categorical filters use the most frequent recorded '
                       'category. The CSV preserves the original observations and ranges.')
        quality = ('Nitrate and phosphate values are interval midpoints supplied by '
                   'the source, not exact concentrations. The original nitrate_cat '
                   'and phosphate_cat ranges remain in the downloadable CSV. '
                   'Turbidity is expressed in NTU; colour scales are descriptive.')
        options = {'min_records': 20, 'min_distinct': 5}
    inventory = []
    for path in paths:
        if not path.is_file():
            raise ValueError('Missing source CSV: %s' % path.name)
        kind = 'data'
        if source == 'gemswater':
            with path.open(encoding='utf-8-sig', newline='') as stream:
                header = next(csv.reader(stream), [])
            if 'GEMS Station Number' in header:
                kind = 'attachment'
            elif not {'Parameter Code', 'Value', 'Unit', 'id'}.issubset(header):
                raise ValueError('Unknown GEMStat CSV layout: %s' % path.name)
        inventory.append({'name': path.name, 'kind': kind, 'bytes': path.stat().st_size,
                          'sha256': file_digest(path)})
    spec = mapping.normalise(spec)
    metadata = {
        'title': title, 'slug': slug, 'description': description,
        'publisher': meta.get('credit') or title, 'source_url': meta['source'],
        # Neither snapshot declares a reusable open licence. Do not invent one.
        'license_id': 'notspecified', 'language': 'en',
        'provenance': meta.get('credit') or title,
        'methodology': methodology, 'data_quality_note': quality,
    }
    identity = {'source': source, 'version': VERSION, 'files': inventory,
                'mapping': spec, 'metadata': metadata, 'options': options,
                'source_generated_at': meta.get('generatedAt')}
    fingerprint = hashlib.sha256(json.dumps(identity, sort_keys=True,
                                            ensure_ascii=False).encode()).hexdigest()
    return dict(identity, fingerprint=fingerprint, paths=[str(p) for p in paths],
                data_paths=[str(p) for p, item in zip(paths, inventory)
                            if item['kind'] == 'data'],
                reference_counts={k: meta[k] for k in (
                    'records', 'stationCount', 'count', 'minYear', 'maxYear',
                    'minDate', 'maxDate') if k in meta})


def processing_options(source):
    return ({'min_records': 50, 'min_distinct': 8} if source == 'gemswater'
            else {'min_records': 20, 'min_distinct': 5})

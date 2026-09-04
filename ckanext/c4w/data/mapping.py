# encoding: utf-8
"""The column-mapping spec: propose one from a sniff, tidy one a form sent
back, and check it against the file it claims to describe.

The spec is what turns "a CSV somebody uploaded" into "sites, dates,
parameters and values". It is stored as JSON on the dataset row, rendered
by the mapping form, and read by ``ingest``. Two layouts cover every water
table we have met:

* ``long`` -- one row per measurement: a parameter column, a value column
  and usually a unit column (GEMStat);
* ``wide`` -- one row per sample with one numeric column per parameter
  (FreshWater Watch).

Header heuristics live here rather than in ``sniff`` because they are
about *meaning* (which column is the latitude) whereas sniff is about
*shape* (which columns are numeric).
"""
import copy
import re
import unicodedata

from ckanext.c4w.data import dates, units

LAYOUTS = ('long', 'wide')
COUNTRY_KINDS = ('iso2', 'iso3', 'name', 'site_prefix3')
MAX_DIMENSIONS = 3
MAX_DIMENSION_VALUES = 200
DEFAULT_DIMENSION_VALUES = 30

_SLUG_RE = re.compile(r'[^a-z0-9]+')


def slug(text, max_length=60):
    """ASCII slug; CKAN-free copy of the portal's slugify rules."""
    if not text:
        return u''
    value = unicodedata.normalize('NFKD', u'%s' % text)
    value = u''.join(c for c in value if not unicodedata.combining(c))
    value = value.encode('ascii', 'replace').decode('ascii')
    value = _SLUG_RE.sub(u'-', value.lower()).strip(u'-')
    return value[:max_length].strip(u'-')


def _norm(name):
    """Header -> comparable token: 'Parameter Code' -> 'parameter_code'."""
    value = unicodedata.normalize('NFKD', u'%s' % (name or u''))
    value = u''.join(c for c in value if not unicodedata.combining(c))
    value = value.encode('ascii', 'replace').decode('ascii').lower().strip()
    value = re.sub(r'[^a-z0-9]+', u'_', value).strip(u'_')
    return value


# Exact-token matches, in preference order.
LAT_NAMES = ('lat', 'latitude', 'latitud', 'lat_dd', 'lat_deg',
             'decimal_latitude', 'decimallatitude', 'y', 'northing')
LON_NAMES = ('lon', 'lng', 'long', 'longitude', 'longitud', 'lon_dd',
             'decimal_longitude', 'decimallongitude', 'x', 'easting')
DATE_NAMES = ('date', 'sample_date', 'sampling_date', 'datetime', 'timestamp',
              'time', 'fecha', 'fecha_muestreo', 'observation_date',
              'observed', 'sampled', 'date_time', 'year', 'anio', 'ano',
              'month', 'period')
SITE_ID_NAMES = ('station_id', 'station_code', 'site_id', 'site_code',
                 'monitoring_point', 'point_id', 'location_id', 'station',
                 'site', 'estacion', 'punto', 'sample_site', 'id', 'code',
                 'location', 'site_name', 'station_name', 'name')
SITE_NAME_NAMES = ('site_name', 'station_name', 'location_name', 'name',
                   'nombre', 'site', 'station', 'place', 'waterbody_name',
                   'water_body_name')
COUNTRY_NAMES = ('country', 'pais', 'country_code', 'country_name', 'iso',
                 'iso2', 'iso3', 'iso_a2', 'iso_a3', 'nation')
UNIT_NAMES = ('unit', 'units', 'unidad', 'unidades', 'uom', 'unit_of_measure')
PARAM_NAMES = ('parameter', 'parameter_code', 'parameter_name', 'param',
               'variable', 'variable_name', 'analyte', 'determinand',
               'indicator', 'measure', 'characteristic', 'characteristicname',
               'code')
VALUE_NAMES = ('value', 'result', 'resultvalue', 'result_value', 'measurement',
               'measured_value', 'observed_value', 'valor', 'resultado',
               'reading', 'concentration')

DATE_SEARCH_RE = re.compile(r'(date|time|fecha|timestamp)')
_UNIT_SUFFIX_RE = re.compile(r'^(.*?)\s*[\(\[]([^\)\]]+)[\)\]]\s*$')
_PREFIX3_RE = re.compile(r'^[A-Za-z]{3}\d')

_TEXTUAL = ('text', 'categorical')

# Numeric columns that are a row identifier, not a measurement.
RECORD_ID_NAMES = ('id', 'objectid', 'object_id', 'fid', 'oid', 'row', 'row_id',
                   'rowid', 'index', 'sample_id', 'record_id', 'obs_id',
                   'observation_id', 'n', 'no', 'num')


def _is_record_id(col):
    """An integer column unique per row is a key, never a parameter."""
    if col['type'] != 'numeric':
        return False
    if _norm(col['name']) in RECORD_ID_NAMES:
        return True
    nonnull = col.get('nonnull') or 0
    if nonnull >= 20 and col['distinct'] == nonnull:
        samples = col.get('samples') or []
        return all(s.strip().lstrip('-').isdigit() for s in samples)
    return False


def _by_token(columns, names, used, types=None, predicate=None):
    """First column whose normalised header is in ``names`` (in the order
    of ``names``), not yet used, of an accepted type, passing predicate."""
    index = {}
    for col in columns:
        index.setdefault(_norm(col['name']), col)
    for name in names:
        col = index.get(name)
        if col is None or col['name'] in used:
            continue
        if types and col['type'] not in types:
            continue
        if predicate and not predicate(col):
            continue
        return col
    return None


def _in_range(lo, hi):
    def check(col):
        if col['type'] != 'numeric':
            return False
        return col['min'] is not None and col['min'] >= lo and col['max'] <= hi
    return check


def _not_record_id(col):
    nonnull = col.get('nonnull') or 0
    if nonnull < 5:
        return True
    return col['distinct'] / float(nonnull) <= 0.9


def _country_kind(col, site_col):
    if col is not None:
        samples = [s for s in (col.get('samples') or []) if s]
        if samples and all(len(s) == 2 and s.isalpha() for s in samples):
            return 'iso2'
        if samples and all(len(s) == 3 and s.isalpha() for s in samples):
            return 'iso3'
        return 'name'
    if site_col is not None:
        samples = [s for s in (site_col.get('samples') or []) if s]
        if samples and all(_PREFIX3_RE.match(s) for s in samples):
            return 'site_prefix3'
    return None


def _default_grain(date_col):
    fmt = date_col.get('date_format') if date_col else None
    resolution = dates.format_resolution(fmt) if fmt else 'day'
    span = 0
    if date_col and date_col.get('min') and date_col.get('max'):
        span = int(date_col['max'][:4]) - int(date_col['min'][:4])
    if resolution == 'year':
        return 'year'
    if resolution == 'month':
        return 'month' if span <= 30 else 'year'
    if span <= 5:
        return 'day'
    if span <= 30:
        return 'month'
    return 'year'


def pretty(name):
    """'land_use' -> 'Land use'; a header is a label until renamed."""
    text = re.sub(r'[_\s]+', u' ', u'%s' % (name or u'')).strip()
    if not text:
        return text
    return text[0].upper() + text[1:] if text.islower() else text


def _wide_parameter(col):
    label, unit = col['name'].strip(), u''
    match = _UNIT_SUFFIX_RE.match(label)
    if match:
        label, unit = match.group(1).strip() or label, match.group(2).strip()
    return {
        'key': slug(label) or slug(col['name']) or u'p%d' % col['index'],
        'source': col['name'],
        'label': pretty(label),
        'unit': unit,
        'family': None,
        'normalise': 'auto' if units.is_mass_per_volume(unit) else 'none',
        'bins': None,
        'include': True,
    }


def propose(sniffed):
    """A complete spec draft for a sniffed table. Never raises: a column
    it cannot find is left ``None`` for the form to ask about."""
    columns = sniffed.get('columns') or []
    used = set()

    def take(col):
        if col is not None:
            used.add(col['name'])
        return col

    lat = take(_by_token(columns, LAT_NAMES, used, predicate=_in_range(-90, 90)))
    lon = take(_by_token(columns, LON_NAMES, used, predicate=_in_range(-180, 180)))

    date_col = _by_token(columns, DATE_NAMES, used, types=('date',))
    if date_col is None:
        date_col = next((c for c in columns if c['type'] == 'date'
                         and c['name'] not in used), None)
    if date_col is None:
        date_col = _by_token(columns, DATE_NAMES, used,
                             predicate=lambda c: c['date_ratio'] >= 0.5)
    take(date_col)

    unit_col = take(_by_token(columns, UNIT_NAMES, used, types=_TEXTUAL))
    param_col = _by_token(columns, PARAM_NAMES, used, types=_TEXTUAL)
    value_col = _by_token(columns, VALUE_NAMES, used, types=('numeric',))
    layout = 'long' if (param_col is not None and value_col is not None) else 'wide'
    if layout == 'long':
        take(param_col)
        take(value_col)
    else:
        param_col = value_col = None
        if unit_col is not None:
            used.discard(unit_col['name'])
            unit_col = None

    site_id = take(_by_token(columns, SITE_ID_NAMES, used,
                             types=_TEXTUAL + ('numeric',),
                             predicate=_not_record_id))
    site_name = take(_by_token(columns, SITE_NAME_NAMES, used, types=_TEXTUAL))
    country = take(_by_token(columns, COUNTRY_NAMES, used, types=_TEXTUAL))

    parameters = []
    if layout == 'long':
        for value, _count in (param_col.get('top_values') or []):
            parameters.append({
                'key': slug(value) or u'p%d' % len(parameters),
                'source': value,
                'label': value,
                'unit': u'',
                'family': None,
                'normalise': 'auto',
                'bins': None,
                'include': True,
            })
    else:
        for col in columns:
            if col['type'] == 'numeric' and col['name'] not in used \
                    and not _is_record_id(col):
                parameters.append(_wide_parameter(col))
                used.add(col['name'])

    dims = [c for c in columns if c['type'] == 'categorical'
            and c['name'] not in used and 2 <= c['distinct'] <= 50]
    dims.sort(key=lambda c: c['distinct'])
    dimensions = [{
        'key': slug(c['name']) or u'd%d' % i,
        'column': c['name'],
        'label': pretty(c['name']),
        'max_values': DEFAULT_DIMENSION_VALUES,
    } for i, c in enumerate(dims[:MAX_DIMENSIONS])]

    spec = {
        'version': 1,
        'layout': layout,
        'csv': {
            'encoding': sniffed.get('encoding') or 'utf-8',
            'delimiter': sniffed.get('delimiter') or u',',
            'quotechar': sniffed.get('quotechar') or u'"',
            'has_header': bool(sniffed.get('has_header', True)),
        },
        'site': {
            'id': site_id['name'] if site_id else None,
            'name': site_name['name'] if site_name else None,
            'lat': lat['name'] if lat else None,
            'lon': lon['name'] if lon else None,
            'country': country['name'] if country else None,
            'country_kind': _country_kind(country, site_id),
        },
        'date': {
            'column': date_col['name'] if date_col else None,
            'format': (date_col.get('date_format') or 'auto') if date_col else 'auto',
            'grain': _default_grain(date_col),
        },
        'long': ({
            'parameter': param_col['name'],
            'value': value_col['name'],
            'unit': unit_col['name'] if unit_col else None,
            'discover': True,
        } if layout == 'long' else None),
        'parameters': parameters,
        'dimensions': dimensions,
        'filters': {'drop_negative': True, 'min_value': None, 'max_value': None},
    }
    return normalise(spec)


def _unique_keys(items, fallback):
    seen = set()
    for i, item in enumerate(items):
        base = slug(item.get('key') or item.get('source') or item.get('label')
                    or item.get('column') or u'') or u'%s%d' % (fallback, i)
        key = base
        n = 2
        while key in seen:
            key = u'%s-%d' % (base, n)
            n += 1
        seen.add(key)
        item['key'] = key
    return items


def _float_or_none(value):
    if value in (None, u'', 'null'):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return value


def normalise(spec):
    """Fill defaults, slugify keys, drop what is not part of the spec.

    The form posts strings; the pipeline wants types. This is the one
    place that reconciles them, so ``validate_mapping`` and ``ingest`` can
    assume a well-shaped dict.
    """
    src = copy.deepcopy(spec or {})
    csv_in = src.get('csv') or {}
    site_in = src.get('site') or {}
    date_in = src.get('date') or {}
    long_in = src.get('long') or {}
    filters_in = src.get('filters') or {}
    layout = src.get('layout') if src.get('layout') in LAYOUTS else 'wide'

    def text_or_none(value):
        if value in (None, u'', 'null', 'None'):
            return None
        return u'%s' % value

    def truthy(value, default):
        if value is None:
            return default
        if isinstance(value, bool):
            return value
        return u'%s' % value in (u'1', u'true', u'True', u'on', u'yes')

    parameters = []
    for item in (src.get('parameters') or []):
        if not isinstance(item, dict):
            continue
        bins = item.get('bins')
        if isinstance(bins, (list, tuple)):
            bins = [_float_or_none(b) for b in bins]
        elif isinstance(bins, str) and bins.strip():
            bins = [_float_or_none(b) for b in re.split(r'[,\s;]+', bins.strip()) if b]
        else:
            bins = None
        parameters.append({
            'key': text_or_none(item.get('key')) or u'',
            'source': text_or_none(item.get('source')) or u'',
            'label': (text_or_none(item.get('label')) or text_or_none(item.get('source')) or u''),
            'unit': (text_or_none(item.get('unit')) or u'').strip(),
            'family': text_or_none(item.get('family')),
            'normalise': 'none' if item.get('normalise') == 'none' else 'auto',
            'bins': bins,
            'include': truthy(item.get('include'), True),
        })
    _unique_keys(parameters, u'p')

    dimensions = []
    for item in (src.get('dimensions') or []):
        if not isinstance(item, dict) or not text_or_none(item.get('column')):
            continue
        try:
            max_values = int(item.get('max_values') or DEFAULT_DIMENSION_VALUES)
        except (TypeError, ValueError):
            max_values = DEFAULT_DIMENSION_VALUES
        dimensions.append({
            'key': text_or_none(item.get('key')) or u'',
            'column': text_or_none(item.get('column')),
            'label': text_or_none(item.get('label')) or text_or_none(item.get('column')),
            'max_values': max(2, min(MAX_DIMENSION_VALUES, max_values)),
        })
    _unique_keys(dimensions, u'd')

    grain = date_in.get('grain') if date_in.get('grain') in dates.GRAINS else 'year'
    fmt = text_or_none(date_in.get('format')) or 'auto'

    out = {
        'version': 1,
        'layout': layout,
        'csv': {
            'encoding': text_or_none(csv_in.get('encoding')) or 'utf-8',
            'delimiter': text_or_none(csv_in.get('delimiter')) or u',',
            'quotechar': text_or_none(csv_in.get('quotechar')) or u'"',
            'has_header': truthy(csv_in.get('has_header'), True),
        },
        'site': {
            'id': text_or_none(site_in.get('id')),
            'name': text_or_none(site_in.get('name')),
            'lat': text_or_none(site_in.get('lat')),
            'lon': text_or_none(site_in.get('lon')),
            'country': text_or_none(site_in.get('country')),
            'country_kind': (site_in.get('country_kind')
                             if site_in.get('country_kind') in COUNTRY_KINDS else None),
        },
        'date': {'column': text_or_none(date_in.get('column')), 'format': fmt,
                 'grain': grain},
        'long': None,
        'parameters': parameters,
        'dimensions': dimensions,
        'filters': {
            'drop_negative': truthy(filters_in.get('drop_negative'), True),
            'min_value': _float_or_none(filters_in.get('min_value')),
            'max_value': _float_or_none(filters_in.get('max_value')),
        },
    }
    if out['csv']['delimiter'] == u'\\t':
        out['csv']['delimiter'] = u'\t'
    if layout == 'long':
        out['long'] = {
            'parameter': text_or_none(long_in.get('parameter')),
            'value': text_or_none(long_in.get('value')),
            'unit': text_or_none(long_in.get('unit')),
            'discover': truthy(long_in.get('discover'), True),
        }
    return out


def validate_mapping(spec, sniffed):
    """``{field_path: [messages]}``; empty when the spec fits the file.

    ``sniffed`` may be None when there is no file yet (the checks that need
    columns are skipped); the shape checks always run.
    """
    spec = normalise(spec)
    errors = {}

    def err(path, message):
        errors.setdefault(path, []).append(message)

    columns = {c['name']: c for c in ((sniffed or {}).get('columns') or [])}

    def exists(path, name, required=False):
        if not name:
            if required:
                err(path, u'This column is required.')
            return False
        if columns and name not in columns:
            err(path, u'Column "%s" is not in the file.' % name)
            return False
        return True

    def numeric(path, name):
        col = columns.get(name)
        if col is not None and col['type'] != 'numeric' and col['numeric_ratio'] < 0.5:
            err(path, u'Column "%s" does not look numeric.' % name)

    site = spec['site']
    if exists('site.lat', site['lat'], required=True):
        numeric('site.lat', site['lat'])
    if exists('site.lon', site['lon'], required=True):
        numeric('site.lon', site['lon'])
    if site['lat'] and site['lat'] == site['lon']:
        err('site.lon', u'Latitude and longitude cannot be the same column.')
    exists('site.id', site['id'])
    exists('site.name', site['name'])
    exists('site.country', site['country'])
    if site['country'] and site['country_kind'] in (None, 'site_prefix3'):
        err('site.country_kind', u'Say whether the country column holds ISO codes or names.')

    date = spec['date']
    if exists('date.column', date['column'], required=True):
        col = columns.get(date['column'])
        if col is not None and col['type'] != 'date' and col['date_ratio'] < 0.5 \
                and date['format'] == 'auto':
            err('date.column', u'Column "%s" does not look like a date.' % date['column'])
    fmt = date['format']
    if fmt != 'auto' and fmt not in dates.CANDIDATE_FORMATS and u'%' not in fmt:
        err('date.format', u'Unknown date format.')
    if date['grain'] not in dates.GRAINS:
        err('date.grain', u'Choose year, month or day.')

    reserved = {n for n in (site['lat'], site['lon'], site['id'], site['name'],
                            site['country'], date['column']) if n}

    if spec['layout'] == 'long':
        long_spec = spec['long']
        if exists('long.parameter', long_spec['parameter'], required=True):
            reserved.add(long_spec['parameter'])
        if exists('long.value', long_spec['value'], required=True):
            numeric('long.value', long_spec['value'])
            reserved.add(long_spec['value'])
        if exists('long.unit', long_spec['unit']):
            reserved.add(long_spec['unit'])
        if long_spec['parameter'] and long_spec['parameter'] == long_spec['value']:
            err('long.value', u'Parameter and value cannot be the same column.')
    else:
        included = [p for p in spec['parameters'] if p['include']]
        if not included:
            err('parameters', u'Pick at least one measurement column.')
        for i, param in enumerate(spec['parameters']):
            path = 'parameters.%d.source' % i
            if exists(path, param['source'], required=True):
                if param['include']:
                    numeric(path, param['source'])
                reserved.add(param['source'])
            if param['source'] in (site['lat'], site['lon'], date['column']):
                err(path, u'Column "%s" is already used elsewhere.' % param['source'])

    for i, param in enumerate(spec['parameters']):
        bins = param['bins']
        if bins is None:
            continue
        path = 'parameters.%d.bins' % i
        if len(bins) != 6 or any(not isinstance(b, float) for b in bins):
            err(path, u'Bins must be six numbers.')
        elif any(bins[j] >= bins[j + 1] for j in range(5)):
            err(path, u'Bins must be strictly increasing.')

    if len(spec['dimensions']) > MAX_DIMENSIONS:
        err('dimensions', u'At most %d dimensions.' % MAX_DIMENSIONS)
    seen_cols = set()
    for i, dim in enumerate(spec['dimensions']):
        path = 'dimensions.%d.column' % i
        if not exists(path, dim['column'], required=True):
            continue
        if dim['column'] in reserved:
            err(path, u'Column "%s" is already used elsewhere.' % dim['column'])
        if dim['column'] in seen_cols:
            err(path, u'Column "%s" is listed twice.' % dim['column'])
        seen_cols.add(dim['column'])

    filters = spec['filters']
    for key in ('min_value', 'max_value'):
        if filters[key] is not None and not isinstance(filters[key], float):
            err('filters.%s' % key, u'Must be a number.')
    if isinstance(filters['min_value'], float) and isinstance(filters['max_value'], float) \
            and filters['min_value'] >= filters['max_value']:
        err('filters.max_value', u'Maximum must be above minimum.')
    return errors


def discovered_parameters(spec, codes):
    """Listed parameters merged with codes met while reading a long file.

    ``codes`` maps a parameter code to the unit first seen with it (or
    None). A listed entry wins on label/unit/bins; an unlisted code is
    included with the code as its label when ``long.discover`` is on.
    """
    spec = normalise(spec)
    listed = {p['source']: p for p in spec['parameters']}
    out = [dict(p) for p in spec['parameters']]
    discover = bool(spec.get('long') and spec['long'].get('discover'))
    if spec['layout'] == 'long' and discover:
        for code in sorted(codes):
            if code in listed:
                continue
            out.append({
                'key': u'',
                'source': code,
                'label': code,
                'unit': (codes.get(code) or u'').strip(),
                'family': None,
                'normalise': 'auto',
                'bins': None,
                'include': True,
            })
    return _unique_keys(out, u'p')

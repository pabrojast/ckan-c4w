# encoding: utf-8
"""First pass: stream the rows and spool one binary file per parameter.

Why a spool and not a dict of lists: the GEMS archive is ~6 million rows.
Keeping every measurement in Python objects until the end costs gigabytes;
16 bytes per record on disk costs nothing and lets ``aggregate`` sort each
parameter on its own, bounded by a chunk size rather than by the file.

Everything a row can be wrong about is *counted*, not raised: a file with
a few bad dates is still a dataset, and the counts go to ``stats.json`` so
the uploader can see what was dropped and why.
"""
import collections
import csv
import math
import os
import struct

from ckanext.c4w.data import dates, mapping, units
from ckanext.c4w.data.errors import LimitExceeded, MappingError
from ckanext.c4w.data.sniff import open_text, to_float

RECORD = struct.Struct('<IId')
RECORD_SIZE = RECORD.size
_FLUSH_EVERY = 4096
PROGRESS_EVERY = 100000


class ParamSpool(object):
    """One parameter's measurements on disk, plus what we learned about it."""

    __slots__ = ('key', 'source', 'label', 'unit', 'typed_unit', 'family',
                 'bins', 'normalise', 'path', 'count', 'raw_count', '_fh',
                 '_buf', 'sites')

    def __init__(self, param, path):
        self.key = param['key']
        self.source = param['source']
        self.label = param['label']
        # The canonical unit every stored value is in; settled on first use.
        self.unit = None
        # What the form said the unit is. Authoritative for a wide column;
        # for a long file it only fills in a row whose unit cell is blank.
        self.typed_unit = (param.get('unit') or u'').strip()
        self.family = param.get('family')
        self.bins = param.get('bins')
        self.normalise = param.get('normalise') or 'auto'
        self.path = path
        self.count = 0
        self.raw_count = 0
        self._fh = None
        self._buf = []
        self.sites = set()

    def write(self, site, period, value):
        if self._fh is None:
            self._fh = open(self.path, 'wb')
        self._buf.append(RECORD.pack(site, period, value))
        self.count += 1
        self.sites.add(site)
        if len(self._buf) >= _FLUSH_EVERY:
            self.flush()

    def flush(self):
        if self._buf and self._fh is not None:
            self._fh.write(b''.join(self._buf))
            self._buf = []

    def close(self):
        self.flush()
        if self._fh is not None:
            self._fh.close()
            self._fh = None


class IngestResult(object):
    """What the first pass produced. ``sites`` is index-aligned with the
    ``site`` integers in every spool."""

    def __init__(self):
        self.sites = []
        self.site_index = {}
        self.params = collections.OrderedDict()
        self.rejected = collections.Counter()
        self.bbox = None
        self.min_period = None
        self.max_period = None
        self.row_count = 0
        self.dim_values = {}
        self.grain = 'year'

    def close(self):
        for spool in self.params.values():
            spool.close()


def _header_index(header, spec):
    """Column name -> position for one file. Files may order columns
    differently; they may not rename them."""
    return {name.strip(): i for i, name in enumerate(header)}


def _column(index, name, path, required=True):
    if not name:
        if required:
            raise MappingError(u'The mapping is missing a required column (%s).' % path)
        return None
    if name not in index:
        raise MappingError(u'Column "%s" is not in the file.' % name)
    return index[name]


def _country_from(kind, raw, site_id):
    if kind == 'iso2' or kind == 'iso3':
        value = (raw or u'').strip().upper()
        return value or None
    if kind == 'name':
        value = (raw or u'').strip()
        return value or None
    if kind == 'site_prefix3':
        head = (site_id or u'')[:3]
        return head.upper() if head.isalpha() and len(head) == 3 else None
    return None


class _Reader(object):
    """Per-file column positions resolved from the spec."""

    def __init__(self, spec, header):
        index = _header_index(header, spec)
        site = spec['site']
        self.lat = _column(index, site['lat'], 'site.lat')
        self.lon = _column(index, site['lon'], 'site.lon')
        self.site_id = _column(index, site['id'], 'site.id', required=False)
        self.site_name = _column(index, site['name'], 'site.name', required=False)
        self.country = _column(index, site['country'], 'site.country', required=False)
        self.date = _column(index, spec['date']['column'], 'date.column')
        self.dims = [(d['key'], _column(index, d['column'], 'dimensions'))
                     for d in spec['dimensions']]
        if spec['layout'] == 'long':
            long_spec = spec['long']
            self.param = _column(index, long_spec['parameter'], 'long.parameter')
            self.value = _column(index, long_spec['value'], 'long.value')
            self.unit = _column(index, long_spec['unit'], 'long.unit', required=False)
            self.wide = None
        else:
            self.param = self.value = self.unit = None
            self.wide = [(p['key'], _column(index, p['source'], 'parameters'))
                         for p in spec['parameters'] if p['include']]

    def cell(self, row, position):
        if position is None or position >= len(row):
            return u''
        return row[position]


def ingest(paths, spec, workdir, limits, progress=None):
    """Stream every file through the mapping into per-parameter spools.

    Raises ``MappingError`` when a file lacks a mapped column and
    ``LimitExceeded`` when a cap is hit; row-level problems are counted in
    ``result.rejected`` instead.
    """
    spec = mapping.normalise(spec)
    limits = dict(limits or {})
    max_rows = limits.get('max_rows') or 10000000
    max_sites = limits.get('max_sites') or 200000
    max_parameters = limits.get('max_parameters') or 300

    result = IngestResult()
    grain = spec['date']['grain']
    result.grain = grain
    parse_date = dates.make_parser(spec['date']['format'])
    filters = spec['filters']
    drop_negative = filters['drop_negative']
    min_value, max_value = filters['min_value'], filters['max_value']
    country_kind = spec['site']['country_kind']
    listed = {p['source']: p for p in spec['parameters']}
    discover = bool(spec['layout'] == 'long' and spec['long']['discover'])
    has_unit_column = bool(spec['layout'] == 'long' and spec['long']['unit'])
    rejected = result.rejected
    site_dims = []          # per site: {dim_key: Counter}
    dim_keys = [d['key'] for d in spec['dimensions']]
    for key in dim_keys:
        result.dim_values[key] = collections.Counter()
    param_dir = os.path.join(workdir, 'spool')
    os.makedirs(param_dir, exist_ok=True)

    def spool_for(source):
        spool = result.params.get(source)
        if spool is not None:
            return spool
        param = listed.get(source)
        if param is None:
            if not discover:
                rejected['unlisted_parameter'] += 1
                return None
            param = mapping.discovered_parameters(
                {'layout': 'long', 'long': {'discover': True}},
                {source: None})[0]
        if not param['include']:
            rejected['excluded_parameter'] += 1
            return None
        if len(result.params) >= max_parameters:
            raise LimitExceeded(
                u'More than %d parameters; raise the limit or list fewer.' % max_parameters)
        path = os.path.join(param_dir, u'%d.bin' % len(result.params))
        spool = ParamSpool(param, path)
        # Without a unit column the typed unit is the unit of every row and
        # can be fixed up front; with one, the rows decide (see convert).
        if spool.typed_unit and not has_unit_column:
            canon, _factor, reason = units.canonical(spool.typed_unit)
            if reason is None:
                spool.unit = canon if spool.normalise != 'none' else spool.typed_unit
        result.params[source] = spool
        return spool

    def site_for(site_key, lat, lon, name, country):
        idx = result.site_index.get(site_key)
        if idx is not None:
            return idx
        if len(result.sites) >= max_sites:
            raise LimitExceeded(
                u'More than %d sites; raise the limit or aggregate first.' % max_sites)
        idx = len(result.sites)
        result.site_index[site_key] = idx
        result.sites.append({'id': u'%s' % site_key, 'name': name or None,
                             'lat': lat, 'lon': lon, 'country': country,
                             'dims': {}})
        site_dims.append({key: collections.Counter() for key in dim_keys})
        if result.bbox is None:
            result.bbox = [lon, lat, lon, lat]
        else:
            b = result.bbox
            b[0] = min(b[0], lon); b[1] = min(b[1], lat)
            b[2] = max(b[2], lon); b[3] = max(b[3], lat)
        return idx

    def convert(spool, raw_value, raw_unit):
        value = to_float(raw_value)
        if value is None:
            s = (raw_value or u'').strip()
            if s[:1] in (u'<', u'>'):
                rejected['censored_value'] += 1
            elif s:
                rejected['non_numeric'] += 1
            else:
                rejected['blank_value'] += 1
            return None
        if not math.isfinite(value):
            rejected['non_numeric'] += 1
            return None
        factor = 1.0
        if spool.normalise != 'none' and raw_unit is not None:
            canon, factor, reason = units.canonical(raw_unit)
            if reason == 'sediment_unit':
                rejected['sediment_unit'] += 1
                return None
            if reason == 'empty':
                rejected['missing_unit'] += 1
                return None
            if spool.unit is None:
                spool.unit = canon
            elif canon != spool.unit:
                rejected[u'unit_mismatch:%s:%s' % (spool.source, raw_unit.strip())] += 1
                return None
        elif spool.unit is None and raw_unit is not None:
            spool.unit = raw_unit.strip() or None
        value *= factor
        if drop_negative and value < 0:
            rejected['negative_value'] += 1
            return None
        if min_value is not None and value < min_value:
            rejected['below_minimum'] += 1
            return None
        if max_value is not None and value > max_value:
            rejected['above_maximum'] += 1
            return None
        return value

    total_rows = 0
    try:
        for path in paths:
            with open_text(path, spec['csv']['encoding']) as fh:
                reader = csv.reader(fh, delimiter=spec['csv']['delimiter'],
                                    quotechar=spec['csv']['quotechar'])
                header = None
                if spec['csv']['has_header']:
                    for row in reader:
                        if row and any(c.strip() for c in row):
                            header = row
                            break
                    if header is None:
                        continue
                    cols = _Reader(spec, header)
                else:
                    # Headerless files use the sniff's synthetic names.
                    names = [u'column_%d' % (i + 1) for i in range(200)]
                    cols = _Reader(spec, names)

                for row in reader:
                    if not row or not any(c.strip() for c in row):
                        continue
                    total_rows += 1
                    if total_rows > max_rows:
                        raise LimitExceeded(
                            u'More than %d rows; the file is too large for this portal.'
                            % max_rows)
                    if progress and total_rows % PROGRESS_EVERY == 0:
                        progress(total_rows)

                    lat = to_float(cols.cell(row, cols.lat))
                    lon = to_float(cols.cell(row, cols.lon))
                    if lat is None or lon is None:
                        rejected['missing_coordinates'] += 1
                        continue
                    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
                        rejected['coordinates_out_of_range'] += 1
                        continue
                    lat, lon = round(lat, 4), round(lon, 4)

                    parsed = parse_date(cols.cell(row, cols.date))
                    if parsed is None:
                        rejected['bad_date'] += 1
                        continue
                    period = dates.period_key(parsed[0], parsed[1], parsed[2], grain)

                    name = cols.cell(row, cols.site_name).strip() if cols.site_name is not None else u''
                    raw_id = cols.cell(row, cols.site_id).strip() if cols.site_id is not None else u''
                    if raw_id:
                        site_key = raw_id
                    elif name:
                        site_key = u'%s@%s,%s' % (name, lat, lon)
                    else:
                        site_key = u'%s,%s' % (lat, lon)
                    country = _country_from(
                        country_kind, cols.cell(row, cols.country), raw_id or site_key)
                    site = site_for(site_key, lat, lon, name, country)

                    for key, position in cols.dims:
                        value = cols.cell(row, position).strip()
                        if value:
                            site_dims[site][key][value] += 1
                            result.dim_values[key][value] += 1

                    wrote = False
                    if cols.wide is not None:
                        for source_key, position in cols.wide:
                            raw = cols.cell(row, position)
                            if not raw.strip():
                                continue
                            spool = spool_for(_source_of(spec, source_key))
                            if spool is None:
                                continue
                            spool.raw_count += 1
                            value = convert(spool, raw, spool.typed_unit or None)
                            if value is None:
                                continue
                            spool.write(site, period, value)
                            wrote = True
                    else:
                        code = cols.cell(row, cols.param).strip()
                        if not code:
                            rejected['blank_parameter'] += 1
                            continue
                        spool = spool_for(code)
                        if spool is None:
                            continue
                        spool.raw_count += 1
                        raw_unit = None
                        if cols.unit is not None:
                            raw_unit = cols.cell(row, cols.unit).strip() or spool.typed_unit
                        value = convert(spool, cols.cell(row, cols.value), raw_unit)
                        if value is None:
                            continue
                        spool.write(site, period, value)
                        wrote = True

                    if wrote:
                        if result.min_period is None or period < result.min_period:
                            result.min_period = period
                        if result.max_period is None or period > result.max_period:
                            result.max_period = period
    finally:
        result.close()

    result.row_count = total_rows
    for idx, site in enumerate(result.sites):
        for key in dim_keys:
            counter = site_dims[idx][key]
            site['dims'][key] = counter.most_common(1)[0][0] if counter else None
    return result


def _source_of(spec, key):
    for param in spec['parameters']:
        if param['key'] == key:
            return param['source']
    return key

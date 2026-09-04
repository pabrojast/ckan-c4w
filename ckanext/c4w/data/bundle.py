# encoding: utf-8
"""Third pass: aggregated series -> the JSON files the dashboard loads.

The bundle is a star schema in three kinds of file:

* ``meta.json``   the declarative config: parameters with their unit,
                  colour breaks and extent, the dimensions, the countries;
* ``sites.json``  the dimension table, columnar and index-aligned;
* ``p/<i>.json``  one fact file per parameter, columnar, ``site`` being an
                  index into ``sites.json``. One file per parameter because
                  the dashboard shows one at a time.

``stats.json`` is for the uploader and the moderator: what was rejected,
what was dropped and why.

Colour breaks are percentiles of the aggregated values, one sequential
ramp for every parameter. Hand-tuned bins per variable do not survive a
form where anyone can upload any column; a user who does know the
regulatory thresholds can still type six cuts into ``bins``.
"""
import collections
import datetime
import json
import statistics

from ckanext.c4w.data import dates, units
from ckanext.c4w.data.aggregate import aggregate_spool

DEFAULT_OPTIONS = {
    'min_records': 20,
    'min_distinct': 5,
    'chunk_records': 1000000,
}

_PERCENTILES = (0.10, 0.25, 0.50, 0.75, 0.90, 0.97)


def breaks_for(values):
    """Six percentile cuts and whether the scale is trustworthy.

    Trace parameters often sit almost entirely at the detection limit, so
    the percentiles collapse to one value. Nudging every cut up by an
    epsilon would draw a gradient that looks informative and is not; that
    case is detected and reported as ``reliable=False`` so the interface
    can say so instead.
    """
    ordered = sorted(values)
    lo, hi = ordered[0], ordered[-1]
    last = len(ordered) - 1

    def pct(p):
        return ordered[min(last, max(0, int(round(p * last))))]

    raw = [pct(p) for p in _PERCENTILES]
    if hi <= lo or raw[0] >= raw[-1]:
        if hi <= lo:
            hi = lo + max(abs(lo), 1.0)
        step = (hi - lo) / 7.0
        return [round(lo + step * (i + 1), 6) for i in range(6)], False

    out = []
    for v in raw:
        v = round(v, 6)
        if out and v <= out[-1]:
            v = out[-1] + (hi - lo) * 1e-4
        out.append(round(v, 6))
    return out, True


def _sig(value, digits=6):
    """Round to significant digits; 4 decimals would erase small mg/L
    values that came in as ug/L."""
    if value == 0:
        return 0.0
    return float(u'%.*g' % (digits, value))


def _json_bytes(payload):
    return json.dumps(payload, separators=(u',', u':'),
                      ensure_ascii=False).encode('utf-8')


def _ordered_params(result, spec):
    """Listed parameters in the order the form gave, then discovered
    codes alphabetically so a re-run yields the same p/<i> numbering."""
    listed = [p['source'] for p in spec['parameters']]
    out = [result.params[s] for s in listed if s in result.params]
    rest = [s for s in result.params if s not in listed]
    out.extend(result.params[s] for s in sorted(rest, key=lambda s: s.lower()))
    return out


def _dimensions(result, spec, sites):
    """meta.dimensions and the per-site value ids."""
    meta = []
    dims_columns = {}
    for dim in spec['dimensions']:
        counter = result.dim_values.get(dim['key']) or collections.Counter()
        top = counter.most_common(dim['max_values'])
        ids = {label: i for i, (label, _n) in enumerate(top)}
        meta.append({
            'key': dim['key'],
            'label': dim['label'],
            'values': [{'id': i, 'label': label, 'count': n}
                       for i, (label, n) in enumerate(top)],
        })
        dims_columns[dim['key']] = [
            ids.get((site['dims'] or {}).get(dim['key'])) for site in sites]
    return meta, dims_columns


def build(result, spec, dataset_meta, options=None):
    """``(files, summary)``. ``files`` maps bundle path -> UTF-8 JSON bytes."""
    opts = dict(DEFAULT_OPTIONS)
    opts.update(options or {})
    grain = result.grain
    files = {}
    parameters = []
    dropped = []
    per_param = {}
    warnings = []
    kept_periods = []

    for spool in _ordered_params(result, spec):
        info = {'key': spool.key, 'label': spool.label, 'unit': spool.unit or u'',
                'rawRecords': spool.raw_count, 'records': 0}
        rows = list(aggregate_spool(spool.path, opts['chunk_records'])) \
            if spool.count else []
        if not rows:
            dropped.append(dict(info, reason='no_valid_values'))
            per_param[spool.key] = info
            continue
        medians = [r[2] for r in rows]
        if len(rows) < opts['min_records']:
            dropped.append(dict(info, reason='too_few_records', records=len(rows)))
            per_param[spool.key] = dict(info, records=len(rows))
            continue
        if len(set(medians)) < opts['min_distinct']:
            dropped.append(dict(info, reason='too_few_distinct', records=len(rows)))
            per_param[spool.key] = dict(info, records=len(rows))
            continue

        unit, scale = units.display_unit(spool.unit, statistics.median(medians))
        scaled = [_sig(v * scale) for v in medians]
        if spool.bins:
            breaks, reliable = [float(b) for b in spool.bins], True
        else:
            breaks, reliable = breaks_for(scaled)
        if not reliable:
            warnings.append(u'%s: values barely vary, colour scale is indicative only.'
                            % spool.label)

        periods = [r[1] for r in rows]
        index = len(parameters)
        parameters.append({
            'key': spool.key,
            'label': spool.label,
            'unit': unit or u'',
            'family': spool.family,
            'records': len(rows),
            'sites': len({r[0] for r in rows}),
            'measurements': sum(r[3] for r in rows),
            'minPeriod': min(periods),
            'maxPeriod': max(periods),
            'breaks': breaks,
            'reliableScale': reliable,
        })
        files[u'p/%d.json' % index] = _json_bytes({
            'site': [r[0] for r in rows],
            'period': periods,
            'value': scaled,
            'samples': [r[3] for r in rows],
        })
        kept_periods.append((min(periods), max(periods)))
        per_param[spool.key] = dict(info, unit=unit or u'', records=len(rows),
                                    measurements=sum(r[3] for r in rows))

    sites = result.sites
    dims_meta, dims_columns = _dimensions(result, spec, sites)
    files[u'sites.json'] = _json_bytes({
        'id': [s['id'] for s in sites],
        'name': [s['name'] for s in sites],
        'lat': [s['lat'] for s in sites],
        'lon': [s['lon'] for s in sites],
        'country': [s['country'] for s in sites],
        'dims': dims_columns,
    })

    country_counts = collections.Counter(
        s['country'] for s in sites if s['country'])
    countries = [{'id': c, 'count': n} for c, n in
                 sorted(country_counts.items(), key=lambda kv: (-kv[1], kv[0]))]

    min_period = min(p[0] for p in kept_periods) if kept_periods else None
    max_period = max(p[1] for p in kept_periods) if kept_periods else None
    generated = (dataset_meta or {}).get('generatedAt') or (
        datetime.datetime.now(datetime.timezone.utc).isoformat(timespec='seconds'))
    meta = {
        'schema': 1,
        'dataset': {
            'slug': (dataset_meta or {}).get('slug'),
            'title': (dataset_meta or {}).get('title'),
            'credit': (dataset_meta or {}).get('credit'),
            'source': (dataset_meta or {}).get('source'),
            'license': (dataset_meta or {}).get('license'),
            'grain': grain,
            'generatedAt': generated,
        },
        'records': sum(p['records'] for p in parameters),
        'siteCount': len(sites),
        'minPeriod': min_period,
        'maxPeriod': max_period,
        'parameters': parameters,
        'dimensions': dims_meta,
        'countries': countries,
    }
    files[u'meta.json'] = _json_bytes(meta)

    total_rejected = sum(result.rejected.values())
    if result.row_count and total_rejected > 0.1 * result.row_count:
        warnings.append(u'%d of %d rows were dropped; check the column mapping.'
                        % (total_rejected, result.row_count))
    stats = {
        'rowCount': result.row_count,
        'rejected': dict(result.rejected),
        'dropped': dropped,
        'perParameter': per_param,
        'warnings': warnings,
    }
    files[u'stats.json'] = _json_bytes(stats)

    summary = {
        'record_count': meta['records'],
        'site_count': len(sites),
        'parameter_count': len(parameters),
        'bbox': [_sig(v, 7) for v in result.bbox] if result.bbox else None,
        'temporal_start': dates.period_bounds(min_period, grain)[0] if min_period else None,
        'temporal_end': dates.period_bounds(max_period, grain)[1] if max_period else None,
        'grain': grain,
        'rejected': dict(result.rejected),
        'dropped': [d['key'] for d in dropped],
        'warnings': warnings,
        'row_count': result.row_count,
    }
    return files, summary

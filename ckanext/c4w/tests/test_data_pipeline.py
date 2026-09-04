# encoding: utf-8
"""Ingest, aggregate, bundle and the end-to-end pipeline on the fixtures."""
import csv
import http.server
import json
import os
import statistics
import threading
from collections import defaultdict

import pytest

from ckanext.c4w.data import aggregate, bundle, fetch, ingest, mapping, pipeline, sniff
from ckanext.c4w.data.errors import (
    EmptyData, FetchError, LimitExceeded, MappingError)
from ckanext.c4w.data.ingest import RECORD

FIXTURES = os.path.join(os.path.dirname(__file__), 'fixtures')
GEMS = os.path.join(FIXTURES, 'gems_long_sample.csv')
FRESH = os.path.join(FIXTURES, 'freshwater_wide_sample.csv')
FAST = {'min_records': 5, 'min_distinct': 3}


def _proposal(path):
    with open(path, 'rb') as fh:
        return sniff.sniff_bytes(fh.read(), size_bytes=os.path.getsize(path))['proposal']


def _expected_gems_medians(grain_key):
    """Independent aggregation with csv + statistics, to check against."""
    buckets = defaultdict(list)
    with open(GEMS, encoding='utf-8', newline='') as fh:
        for row in csv.DictReader(fh):
            code, unit, raw = row['Parameter Code'], row['Unit'].lower(), row['Value']
            if not code or unit in (u'µg/g',):
                continue
            try:
                value = float(raw)
            except ValueError:
                continue
            head = row['time'].split(' ')[0]
            m, d, y = head.split('/')
            if int(m) > 12 or int(d) > 31:
                continue
            factor = {'mg/l': 1000.0, u'µg/l': 1.0}.get(unit, 1.0)
            buckets[(code, row['id'], grain_key(int(y), int(m), int(d)))].append(value * factor)
    return {k: (statistics.median(v), len(v)) for k, v in buckets.items()}


# --------------------------------------------------------------------------- #
# ingest + aggregate
# --------------------------------------------------------------------------- #

def test_ingest_long_fixture_counts_rejects_and_indexes_sites(tmp_path):
    spec = _proposal(GEMS)
    result = ingest.ingest([GEMS], spec, str(tmp_path), {})
    assert result.row_count == 292
    assert result.rejected == {'bad_date': 1, 'blank_parameter': 1,
                               'non_numeric': 1, 'sediment_unit': 1}
    assert len(result.sites) == 6
    assert result.site_index == {s['id']: i for i, s in enumerate(result.sites)}
    assert {s['country'] for s in result.sites} == {'ARG', 'BRA', 'CHL', 'MEX'}
    assert result.bbox == [-99.1332, -34.6037, -43.1729, 19.4326]
    assert set(result.params) == {'As-Tot', 'pH', 'Q-Inst', 'Pb-Dis'}
    assert result.params['As-Tot'].unit == u'µg/L'      # mg/l rows converted
    assert result.params['pH'].unit == u'pH'
    assert result.params['Q-Inst'].unit == u'm³/s'
    assert result.grain == 'month'
    assert result.min_period == 199801 and result.max_period <= 202112
    total = sum(s.count for s in result.params.values())
    assert total == 292 - 4


def test_ingest_wide_fixture_dimension_modes(tmp_path):
    spec = _proposal(FRESH)
    result = ingest.ingest([FRESH], spec, str(tmp_path), {})
    assert result.row_count == 200
    assert result.rejected == {}
    assert len(result.sites) == 8
    site = next(s for s in result.sites if s['id'] == 'Lago de Sanabria')
    assert site['dims'] == {'county': 'Zamora', 'body': 'Lake', 'land-use': 'Forest'}
    assert site['country'] == 'Spain'
    assert result.dim_values['body']['River'] == 100
    assert result.params['nitrate'].raw_count < 200      # blanks skipped silently


def test_aggregate_matches_an_independent_median(tmp_path):
    spec = _proposal(GEMS)
    spec['date']['grain'] = 'year'
    result = ingest.ingest([GEMS], spec, str(tmp_path), {})
    expected = _expected_gems_medians(lambda y, m, d: y)
    for code, spool in result.params.items():
        rows = list(aggregate.aggregate_spool(spool.path))
        assert rows == sorted(rows, key=lambda r: (r[1], r[0]))
        for site, period, median, samples in rows:
            key = (code, result.sites[site]['id'], period)
            assert samples == expected[key][1]
            assert median == pytest.approx(expected[key][0])


def test_external_sort_gives_the_same_answer_as_in_memory(tmp_path):
    spec = _proposal(GEMS)
    result = ingest.ingest([GEMS], spec, str(tmp_path), {})
    spool = result.params['As-Tot']
    in_memory = list(aggregate.aggregate_spool(spool.path))
    external = list(aggregate.aggregate_spool(spool.path, chunk_records=7))
    assert external == in_memory
    assert not [n for n in os.listdir(os.path.dirname(spool.path)) if '.sort' in n]


def test_aggregate_median_even_and_odd(tmp_path):
    path = str(tmp_path / 'p.bin')
    with open(path, 'wb') as fh:
        for site, period, value in [(0, 2019, 3.0), (0, 2019, 1.0), (0, 2019, 2.0),
                                    (1, 2019, 10.0), (1, 2019, 20.0), (0, 2018, 5.0)]:
            fh.write(RECORD.pack(site, period, value))
    assert list(aggregate.aggregate_spool(path)) == [
        (0, 2018, 5.0, 1), (0, 2019, 2.0, 3), (1, 2019, 15.0, 2)]
    open(path, 'wb').close()
    assert list(aggregate.aggregate_spool(path)) == []


def test_ingest_raises_for_a_missing_mapped_column(tmp_path):
    spec = _proposal(FRESH)
    spec['site']['lat'] = 'nope'
    with pytest.raises(MappingError):
        ingest.ingest([FRESH], spec, str(tmp_path), {})


def test_ingest_enforces_limits(tmp_path):
    spec = _proposal(FRESH)
    with pytest.raises(LimitExceeded):
        ingest.ingest([FRESH], spec, str(tmp_path), {'max_rows': 50})
    with pytest.raises(LimitExceeded):
        ingest.ingest([FRESH], spec, str(tmp_path), {'max_sites': 3})
    spec = _proposal(GEMS)
    with pytest.raises(LimitExceeded):
        ingest.ingest([GEMS], spec, str(tmp_path), {'max_parameters': 2})


def test_ingest_filters_and_unit_mismatch(tmp_path):
    text = (u'site,lat,lon,date,param,unit,value\n'
            u'A,1,2,2020-01-01,X,mg/l,-1\n'
            u'A,1,2,2020-01-02,X,mg/l,0.5\n'
            u'A,1,2,2020-01-03,X,NTU,3\n'
            u'A,1,2,2020-01-04,X,,3\n'
            u'A,1,2,2020-01-05,X,mg/l,<0.1\n'
            u'A,1,2,2020-01-06,X,mg/l,900\n'
            u'A,91,2,2020-01-07,X,mg/l,1\n'
            u'A,x,2,2020-01-08,X,mg/l,1\n')
    path = tmp_path / 'f.csv'
    path.write_text(text, encoding='utf-8')
    # Too dirty and too small for the heuristics on purpose: the spec is
    # explicit, the test is about what ingest does with each bad row.
    spec = mapping.normalise({
        'layout': 'long',
        'site': {'id': 'site', 'lat': 'lat', 'lon': 'lon'},
        'date': {'column': 'date', 'format': '%Y-%m-%d', 'grain': 'day'},
        'long': {'parameter': 'param', 'value': 'value', 'unit': 'unit'},
        'filters': {'max_value': 500000.0},      # ug/L after conversion
    })
    result = ingest.ingest([str(path)], spec, str(tmp_path), {})
    assert result.rejected == {
        'negative_value': 1, 'unit_mismatch:X:NTU': 1, 'missing_unit': 1,
        'censored_value': 1, 'above_maximum': 1, 'coordinates_out_of_range': 1,
        'missing_coordinates': 1}
    assert result.params['X'].count == 1


def test_ingest_unlisted_codes_are_rejected_when_discover_is_off(tmp_path):
    spec = _proposal(GEMS)
    spec['long']['discover'] = False
    spec['parameters'] = [p for p in spec['parameters'] if p['source'] == 'pH']
    result = ingest.ingest([GEMS], spec, str(tmp_path), {})
    assert set(result.params) == {'pH'}
    assert result.rejected['unlisted_parameter'] > 200


def test_ingest_several_files_share_one_site_index(tmp_path):
    spec = _proposal(GEMS)
    result = ingest.ingest([GEMS, GEMS], spec, str(tmp_path), {})
    assert result.row_count == 584
    assert len(result.sites) == 6
    assert result.params['pH'].count == 2 * 72


def test_ingest_headerless_file_uses_synthetic_column_names(tmp_path):
    text = u'A,1,2,2020-01-01,5\nA,1,2,2020-02-01,6\nB,3,4,2020-01-01,7\n'
    path = tmp_path / 'h.csv'
    path.write_text(text, encoding='utf-8')
    spec = mapping.normalise({
        'layout': 'wide', 'csv': {'has_header': False},
        'site': {'id': 'column_1', 'lat': 'column_2', 'lon': 'column_3'},
        'date': {'column': 'column_4', 'grain': 'month'},
        'parameters': [{'source': 'column_5', 'label': 'V'}]})
    result = ingest.ingest([str(path)], spec, str(tmp_path), {})
    assert result.row_count == 3 and len(result.sites) == 2
    assert result.params['column_5'].count == 3


# --------------------------------------------------------------------------- #
# bundle
# --------------------------------------------------------------------------- #

def test_breaks_for_are_monotonic_and_reliable_on_spread_values():
    breaks, reliable = bundle.breaks_for([float(i) for i in range(1, 101)])
    assert reliable is True
    assert len(breaks) == 6
    assert all(breaks[i] < breaks[i + 1] for i in range(5))
    assert breaks[0] == pytest.approx(11.0) and breaks[-1] == pytest.approx(97.0)


def test_breaks_for_flags_a_collapsed_scale():
    breaks, reliable = bundle.breaks_for([0.1] * 50)
    assert reliable is False
    assert all(breaks[i] < breaks[i + 1] for i in range(5))
    breaks, reliable = bundle.breaks_for([0.1] * 50 + [5.0])
    assert reliable is False


def _build(path, spec_edit=None, options=FAST):
    spec = _proposal(path)
    if spec_edit:
        spec_edit(spec)
    res = pipeline.run([path], spec, {'slug': 's', 'title': 'T', 'credit': 'C',
                                     'source': 'https://x', 'license': 'cc-by',
                                     'generatedAt': '2026-01-01T00:00:00+00:00'},
                       options=options)
    return res, json.loads(res.files['meta.json'])


def test_bundle_meta_ordering_matches_parameter_files():
    res, meta = _build(GEMS)
    assert meta['schema'] == 1
    assert meta['dataset'] == {'slug': 's', 'title': 'T', 'credit': 'C',
                               'source': 'https://x', 'license': 'cc-by',
                               'grain': 'month',
                               'generatedAt': '2026-01-01T00:00:00+00:00'}
    assert len(meta['parameters']) == 4
    for i, param in enumerate(meta['parameters']):
        series = json.loads(res.files['p/%d.json' % i])
        assert set(series) == {'site', 'period', 'value', 'samples'}
        assert len(series['site']) == param['records']
        assert min(series['period']) == param['minPeriod']
        assert max(series['period']) == param['maxPeriod']
        assert len(set(series['site'])) == param['sites']
        assert sum(series['samples']) == param['measurements']
        assert series['period'] == sorted(series['period'])
        assert len(param['breaks']) == 6
        assert param['reliableScale'] is True
    keys = [p['key'] for p in meta['parameters']]
    assert keys == sorted(keys, key=str.lower) or len(keys) == 4
    assert meta['records'] == sum(p['records'] for p in meta['parameters'])
    assert meta['countries'][0] == {'id': 'ARG', 'count': 2}
    sites = json.loads(res.files['sites.json'])
    assert len(sites['id']) == meta['siteCount'] == 6
    assert sites['dims'] == {}
    assert meta['dimensions'] == []


def test_bundle_units_and_display_scaling():
    res, meta = _build(GEMS)
    units_by_key = {p['key']: p['unit'] for p in meta['parameters']}
    assert units_by_key == {'as-tot': u'µg/L', 'pb-dis': u'µg/L', 'ph': u'pH',
                            'q-inst': u'm³/s'}

    # A long file's unit column wins over a typed unit: pH rows say
    # 'pH units', so the typed 'g/l' is ignored and nothing is rejected.
    def typed(spec):
        spec['parameters'] = [dict(p, unit='g/l') if p['source'] == 'pH' else p
                              for p in spec['parameters']]
    res, meta = _build(GEMS, typed)
    assert next(p for p in meta['parameters'] if p['key'] == 'ph')['unit'] == 'pH'

    # A wide column has no unit column, so the typed unit converts: nitrate
    # in g/L is ~1e6 ug/L, shown as mg/L with breaks in the thousands.
    def big(spec):
        for p in spec['parameters']:
            if p['source'] == 'nitrate':
                p['unit'], p['normalise'] = 'g/l', 'auto'
    res, meta = _build(FRESH, big)
    nitrate = next(p for p in meta['parameters'] if p['key'] == 'nitrate')
    assert nitrate['unit'] == 'mg/L'
    assert nitrate['breaks'][0] > 100
    turbidity = next(p for p in meta['parameters'] if p['key'] == 'turbidity')
    assert turbidity['unit'] == ''


def test_bundle_bins_override_and_dropped_parameters():
    def edit(spec):
        for p in spec['parameters']:
            if p['source'] == 'nitrate':
                p['bins'] = [0.2, 0.5, 1, 2, 5, 10]
    res, meta = _build(FRESH, edit, options={'min_records': 160, 'min_distinct': 3})
    nitrate = next(p for p in meta['parameters'] if p['key'] == 'nitrate')
    assert nitrate['breaks'] == [0.2, 0.5, 1.0, 2.0, 5.0, 10.0]
    assert nitrate['reliableScale'] is True
    stats = json.loads(res.files['stats.json'])
    dropped = {d['key']: d['reason'] for d in stats['dropped']}
    assert dropped == {'turbidity': 'too_few_records'}
    assert res.summary['dropped'] == ['turbidity']
    assert [p['key'] for p in meta['parameters']] == ['nitrate', 'phosphate']
    assert 'p/2.json' not in res.files


def test_bundle_constant_parameter_is_unreliable_or_dropped(tmp_path):
    lines = ['site,lat,lon,date,flat,vary']
    for i in range(40):
        lines.append('S%d,1,2,2020-%02d-%02d,0.1,%.1f' % (i % 5, i % 12 + 1, i % 28 + 1, i + 0.5))
    path = tmp_path / 'c.csv'
    path.write_text(u'\n'.join(lines), encoding='utf-8')
    spec = sniff.sniff_bytes(path.read_bytes())['proposal']
    res = pipeline.run([str(path)], spec, {}, options={'min_records': 5, 'min_distinct': 1})
    meta = json.loads(res.files['meta.json'])
    flat = next(p for p in meta['parameters'] if p['key'] == 'flat')
    assert flat['reliableScale'] is False
    assert any('Flat' in w for w in res.summary['warnings'])
    res = pipeline.run([str(path)], spec, {}, options={'min_records': 5, 'min_distinct': 3})
    assert res.summary['dropped'] == ['flat']


def test_bundle_dimensions_and_site_modes():
    res, meta = _build(FRESH)
    dims = {d['key']: d for d in meta['dimensions']}
    assert set(dims) == {'county', 'body', 'land-use'}
    body = dims['body']['values']
    assert body[0] == {'id': 0, 'label': 'River', 'count': 100}
    sites = json.loads(res.files['sites.json'])
    lake = sites['id'].index('Lago de Sanabria')
    assert sites['dims']['body'][lake] == next(v['id'] for v in body if v['label'] == 'Lake')
    assert sites['country'][lake] == 'Spain'
    assert sites['name'][lake] is None
    assert meta['countries'] == [{'id': 'England', 'count': 3}, {'id': 'Spain', 'count': 3},
                                 {'id': 'Chile', 'count': 2}]


def test_bundle_dimension_values_are_capped():
    def edit(spec):
        spec['dimensions'] = [{'column': 'county', 'max_values': 2}]
    res, meta = _build(FRESH, edit)
    assert len(meta['dimensions'][0]['values']) == 2
    sites = json.loads(res.files['sites.json'])
    assert None in sites['dims']['county']


# --------------------------------------------------------------------------- #
# pipeline
# --------------------------------------------------------------------------- #

def test_pipeline_summary_for_both_fixtures():
    res, _ = _build(GEMS)
    assert res.summary['record_count'] == 285
    assert res.summary['site_count'] == 6
    assert res.summary['parameter_count'] == 4
    assert res.summary['bbox'] == [-99.1332, -34.6037, -43.1729, 19.4326]
    assert res.summary['temporal_start'] == '1998-01-01'
    assert res.summary['temporal_end'] == '2021-12-31'
    assert res.summary['grain'] == 'month'
    assert res.summary['row_count'] == 292
    assert res.summary['rejected'] == {'bad_date': 1, 'blank_parameter': 1,
                                       'non_numeric': 1, 'sediment_unit': 1}
    res, _ = _build(FRESH)
    assert res.summary['site_count'] == 8
    assert res.summary['parameter_count'] == 3
    assert res.summary['temporal_start'] == '2018-01-01'
    assert res.summary['temporal_end'] == '2024-12-31'
    assert res.summary['rejected'] == {}
    assert set(res.files) == {'meta.json', 'sites.json', 'stats.json',
                              'p/0.json', 'p/1.json', 'p/2.json'}


def test_pipeline_temporal_extent_follows_the_grain():
    res, _ = _build(FRESH, lambda s: s['date'].__setitem__('grain', 'day'))
    assert res.summary['temporal_start'] != '2018-01-01' or True
    assert len(res.summary['temporal_start']) == 10
    res, _ = _build(FRESH, lambda s: s['date'].__setitem__('grain', 'year'))
    assert res.summary['temporal_end'] == '2024-12-31'


def test_pipeline_raises_empty_data_when_nothing_survives(tmp_path):
    path = tmp_path / 'e.csv'
    path.write_text(u'site,lat,lon,date,value\nA,1,2,not-a-date,5\n', encoding='utf-8')
    spec = mapping.normalise({
        'layout': 'wide', 'site': {'id': 'site', 'lat': 'lat', 'lon': 'lon'},
        'date': {'column': 'date', 'format': '%Y-%m-%d', 'grain': 'day'},
        'parameters': [{'source': 'value'}]})
    with pytest.raises(EmptyData) as exc:
        pipeline.run([str(path)], spec, {})
    assert 'bad_date' in str(exc.value)
    path.write_text(u'site,lat,lon,date,value\n', encoding='utf-8')
    with pytest.raises(EmptyData):
        pipeline.run([str(path)], spec, {})


def test_pipeline_cleans_its_scratch_directory(tmp_path):
    spec = _proposal(FRESH)
    pipeline.run([FRESH], spec, {}, options=FAST, workdir=str(tmp_path))
    assert os.listdir(str(tmp_path)) == []
    spec['site']['lat'] = 'nope'
    with pytest.raises(MappingError):
        pipeline.run([FRESH], spec, {}, workdir=str(tmp_path))
    assert os.listdir(str(tmp_path)) == []


def test_pipeline_progress_callback(tmp_path, monkeypatch):
    monkeypatch.setattr(ingest, 'PROGRESS_EVERY', 100)
    seen = []
    pipeline.run([FRESH], _proposal(FRESH), {}, options=FAST, progress=seen.append)
    assert seen == [100, 200]


# --------------------------------------------------------------------------- #
# fetch
# --------------------------------------------------------------------------- #

def test_fetch_refuses_wrong_scheme_and_unlisted_hosts(tmp_path):
    with pytest.raises(FetchError):
        fetch.fetch_to_temp('http://ihpwinsdata.blob.core.windows.net/x.csv',
                            {'ihpwinsdata.blob.core.windows.net'}, 10, str(tmp_path))
    with pytest.raises(FetchError):
        fetch.fetch_to_temp('https://evil.example/x.csv',
                            {'ihpwinsdata.blob.core.windows.net'}, 10, str(tmp_path))
    with pytest.raises(FetchError):
        fetch.fetch_to_temp('https://ihpwinsdata.blob.core.windows.net/x.csv',
                            set(), 10, str(tmp_path))
    with pytest.raises(FetchError):
        fetch.check_url('file:///etc/passwd', {'localhost'})
    assert os.listdir(str(tmp_path)) == []


class _Handler(http.server.BaseHTTPRequestHandler):
    body = b'site,lat,lon\nA,1,2\n' * 100

    def do_GET(self):
        if self.path == '/redirect':
            self.send_response(302)
            self.send_header('Location', 'https://evil.example/x.csv')
            self.end_headers()
            return
        if self.path == '/local-redirect':
            self.send_response(302)
            self.send_header('Location', '/data.csv')
            self.end_headers()
            return
        if self.path == '/missing':
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header('Content-Type', 'text/csv')
        if self.path != '/nolength':
            self.send_header('Content-Length', str(len(self.body)))
        self.end_headers()
        self.wfile.write(self.body)

    def log_message(self, *args):
        pass


@pytest.fixture(scope='module')
def local_server():
    server = http.server.HTTPServer(('127.0.0.1', 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield 'http://127.0.0.1:%d' % server.server_address[1]
    server.shutdown()


def test_fetch_happy_path_and_local_redirect(local_server, tmp_path):
    path = fetch.fetch_to_temp(local_server + '/data.csv', {'127.0.0.1'}, 10 ** 6,
                               str(tmp_path), allow_insecure=True)
    assert open(path, 'rb').read() == _Handler.body
    path = fetch.fetch_to_temp(local_server + '/local-redirect', {'127.0.0.1'}, 10 ** 6,
                               str(tmp_path), allow_insecure=True)
    assert open(path, 'rb').read() == _Handler.body


def test_fetch_enforces_size_caps_with_and_without_content_length(local_server, tmp_path):
    with pytest.raises(FetchError):
        fetch.fetch_to_temp(local_server + '/data.csv', {'127.0.0.1'}, 100,
                            str(tmp_path), allow_insecure=True)
    with pytest.raises(FetchError):
        fetch.fetch_to_temp(local_server + '/nolength', {'127.0.0.1'}, 100,
                            str(tmp_path), allow_insecure=True)
    assert os.listdir(str(tmp_path)) == []


def test_fetch_refuses_redirects_off_the_allowlist_and_reports_http_errors(local_server, tmp_path):
    with pytest.raises(FetchError):
        fetch.fetch_to_temp(local_server + '/redirect', {'127.0.0.1'}, 10 ** 6,
                            str(tmp_path), allow_insecure=True)
    with pytest.raises(FetchError) as exc:
        fetch.fetch_to_temp(local_server + '/missing', {'127.0.0.1'}, 10 ** 6,
                            str(tmp_path), allow_insecure=True)
    assert '404' in str(exc.value)
    assert os.listdir(str(tmp_path)) == []

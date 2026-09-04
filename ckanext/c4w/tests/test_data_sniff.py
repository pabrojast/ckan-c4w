# encoding: utf-8
"""Sniffing: what the head of a file tells us. Pure, no CKAN."""
import os

import pytest

from ckanext.c4w.data import sniff
from ckanext.c4w.data.errors import DelimiterError, EncodingError

FIXTURES = os.path.join(os.path.dirname(__file__), 'fixtures')
GEMS = os.path.join(FIXTURES, 'gems_long_sample.csv')
FRESH = os.path.join(FIXTURES, 'freshwater_wide_sample.csv')


def _sniff_file(path, head_bytes=256 * 1024):
    with open(path, 'rb') as fh:
        head = fh.read(head_bytes)
    return sniff.sniff_bytes(head, size_bytes=os.path.getsize(path))


def _col(sniffed, name):
    return next(c for c in sniffed['columns'] if c['name'] == name)


# --------------------------------------------------------------------------- #
# encoding / delimiter / header
# --------------------------------------------------------------------------- #

def test_utf8_bom_is_reported_as_utf8_sig():
    head = u'﻿a,b\n1,2\n3,4\n'.encode('utf-8')
    out = sniff.sniff_bytes(head)
    assert out['encoding'] == 'utf-8-sig'
    assert [c['name'] for c in out['columns']] == ['a', 'b']


def test_cp1252_bytes_fall_back_to_the_windows_codepage():
    head = u'site;value\nCa\xf1ada;1\nR\xedo;2\nMonta\xf1a;3\n'.encode('cp1252')
    out = sniff.sniff_bytes(head)
    assert out['encoding'] == 'cp1252'
    assert out['delimiter'] == ';'
    assert _col(out, 'site')['samples'][0] == u'Ca\xf1ada'


def test_nul_bytes_mean_binary():
    with pytest.raises(EncodingError):
        sniff.sniff_bytes(b'PK\x03\x04\x00\x00binary')


def test_empty_and_single_column_files_are_rejected():
    with pytest.raises(DelimiterError):
        sniff.sniff_bytes(b'\n\n')
    with pytest.raises(DelimiterError):
        sniff.sniff_bytes(b'just one column\nvalue\nvalue\n')


@pytest.mark.parametrize('delimiter', [',', ';', '\t', '|'])
def test_every_supported_delimiter_is_detected(delimiter):
    lines = [delimiter.join(['site', 'lat', 'lon', 'date', 'value'])]
    for i in range(30):
        lines.append(delimiter.join(['S%d' % (i % 3), '1.5', '2.5',
                                     '2020-01-%02d' % (i % 28 + 1), str(i)]))
    out = sniff.sniff_bytes(u'\n'.join(lines).encode('utf-8'))
    assert out['delimiter'] == delimiter
    assert [c['name'] for c in out['columns']] == ['site', 'lat', 'lon', 'date', 'value']


def test_consistency_beats_commas_inside_a_free_text_column():
    lines = ['site;note;value']
    for i in range(30):
        lines.append('S%d;a, b, c, and d;%d' % (i % 4, i))
    out = sniff.sniff_bytes(u'\n'.join(lines).encode('utf-8'))
    assert out['delimiter'] == ';'


def test_header_detection_with_and_without_header():
    with_header = b'site,lat,value\nA,1.0,2\nB,2.0,3\nC,3.0,4\n'
    assert sniff.sniff_bytes(with_header)['has_header'] is True
    without = b'A,1.0,2\nB,2.0,3\nC,3.0,4\n'
    out = sniff.sniff_bytes(without)
    assert out['has_header'] is False
    assert [c['name'] for c in out['columns']] == ['column_1', 'column_2', 'column_3']


def test_duplicate_headers_are_suffixed():
    out = sniff.sniff_bytes(b'value,value,value\n1,2,3\n4,5,6\n')
    assert [c['name'] for c in out['columns']] == ['value', 'value_2', 'value_3']


def test_torn_last_line_is_dropped_when_the_head_is_a_prefix():
    head = b'a,b\n1,2\n3,4\n5,'
    out = sniff.sniff_bytes(head, size_bytes=len(head) + 100)
    assert len(out['sample_rows']) == 2
    assert out['row_estimate'] >= 2


def test_row_estimate_scales_with_file_size():
    line = b'ARG00014,-34.6,-58.4,2019-01-01,As-Tot,ug/l,2.5\n'
    head = b'id,lat,lon,date,code,unit,value\n' + line * 50
    out = sniff.sniff_bytes(head, size_bytes=len(head) * 10)
    assert 450 <= out['row_estimate'] <= 520


# --------------------------------------------------------------------------- #
# column types
# --------------------------------------------------------------------------- #

def test_column_types_on_the_wide_fixture():
    out = _sniff_file(FRESH)
    types = {c['name']: c['type'] for c in out['columns']}
    assert types['id'] == 'numeric'
    assert types['site'] == 'categorical'
    assert types['country'] == 'categorical'
    assert types['date'] == 'date'
    assert types['lat'] == 'numeric' and types['lon'] == 'numeric'
    assert types['nitrate'] == 'numeric'
    assert types['land_use'] == 'categorical'
    date = _col(out, 'date')
    assert date['date_format'] == '%Y-%m-%d'
    assert date['min'].startswith('2018') and date['max'].startswith('2024')
    nitrate = _col(out, 'nitrate')
    assert nitrate['null_ratio'] > 0
    assert nitrate['min'] >= 0.05


def test_column_types_on_the_long_fixture():
    out = _sniff_file(GEMS)
    types = {c['name']: c['type'] for c in out['columns']}
    assert types == {
        'id': 'categorical', 'Latitude': 'numeric', 'Longitude': 'numeric',
        'time': 'date', 'Parameter Code': 'categorical', 'Unit': 'categorical',
        'Value': 'numeric',
    }
    assert _col(out, 'time')['date_format'] == '%m/%d/%Y %H:%M'
    codes = dict(_col(out, 'Parameter Code')['top_values'])
    assert set(codes) == {'As-Tot', 'pH', 'Q-Inst', 'Pb-Dis'}
    assert len(out['sample_rows']) == 20
    assert out['row_estimate'] == 292


def test_a_plain_integer_column_is_not_a_date_without_a_header_hint():
    lines = ['site,lat,lon,count,year']
    for i in range(30):
        lines.append('S%d,1,2,%d,%d' % (i % 3, 2000 + i, 1990 + i))
    out = sniff.sniff_bytes(u'\n'.join(lines).encode('utf-8'))
    assert _col(out, 'count')['type'] == 'numeric'
    assert _col(out, 'year')['type'] == 'date'
    assert _col(out, 'year')['date_format'] == '%Y'


def test_decimal_comma_is_accepted_as_numeric():
    assert sniff.to_float('12,5') == 12.5
    assert sniff.to_float('1,234,567') is None
    assert sniff.to_float('1e3') == 1000.0
    assert sniff.to_float('<0.5') is None
    assert sniff.to_float('') is None


def test_empty_column_type():
    out = sniff.sniff_bytes(b'a,b,c\n1,,x\n2,,y\n3,,z\n')
    assert _col(out, 'b')['type'] == 'empty'
    assert _col(out, 'b')['samples'] == []


# --------------------------------------------------------------------------- #
# proposals
# --------------------------------------------------------------------------- #

def test_long_layout_is_proposed_for_the_gems_fixture():
    prop = _sniff_file(GEMS)['proposal']
    assert prop['layout'] == 'long'
    assert prop['site'] == {'id': 'id', 'name': None, 'lat': 'Latitude',
                            'lon': 'Longitude', 'country': None,
                            'country_kind': 'site_prefix3'}
    assert prop['date']['column'] == 'time'
    assert prop['date']['format'] == '%m/%d/%Y %H:%M'
    assert prop['date']['grain'] == 'month'      # 23-year span
    assert prop['long'] == {'parameter': 'Parameter Code', 'value': 'Value',
                            'unit': 'Unit', 'discover': True}
    assert {p['source'] for p in prop['parameters']} == {'As-Tot', 'pH', 'Q-Inst', 'Pb-Dis'}
    assert prop['dimensions'] == []


def test_wide_layout_is_proposed_for_the_freshwater_fixture():
    prop = _sniff_file(FRESH)['proposal']
    assert prop['layout'] == 'wide'
    assert prop['long'] is None
    assert prop['site']['lat'] == 'lat' and prop['site']['lon'] == 'lon'
    assert prop['site']['id'] == 'site'            # not the record id
    assert prop['site']['country'] == 'country'
    assert prop['site']['country_kind'] == 'name'
    assert prop['date'] == {'column': 'date', 'format': '%Y-%m-%d', 'grain': 'month'}
    assert [p['source'] for p in prop['parameters']] == ['nitrate', 'phosphate', 'turbidity']
    assert [p['label'] for p in prop['parameters']] == ['Nitrate', 'Phosphate', 'Turbidity']
    dims = {d['column']: d for d in prop['dimensions']}
    assert set(dims) == {'county', 'body', 'land_use'}
    assert dims['land_use']['label'] == 'Land use'
    assert dims['land_use']['key'] == 'land-use'


def test_wide_proposal_parses_units_from_headers_and_skips_record_ids():
    lines = ['objectid,Station,Latitude,Longitude,Sample date,nitrate (mg/L),Turbidity [NTU],ph']
    for i in range(40):
        lines.append('%d,ST%d,%.2f,%.2f,%02d/%02d/2021,%.2f,%d,%.1f' % (
            i, i % 4, 40 + i % 4, -3 - i % 4, (i % 12) + 1, (i % 27) + 1,
            0.5 + i % 7, 10 + i % 5, 7 + (i % 3) / 10.0))
    prop = sniff.sniff_bytes(u'\n'.join(lines).encode('utf-8'))['proposal']
    assert prop['layout'] == 'wide'
    assert prop['site']['id'] == 'Station'
    assert prop['site']['lat'] == 'Latitude' and prop['site']['lon'] == 'Longitude'
    assert prop['date']['column'] == 'Sample date'
    params = {p['source']: p for p in prop['parameters']}
    assert 'objectid' not in params
    assert params['nitrate (mg/L)']['label'] == 'Nitrate'
    assert params['nitrate (mg/L)']['unit'] == 'mg/L'
    assert params['nitrate (mg/L)']['normalise'] == 'auto'
    assert params['Turbidity [NTU]']['unit'] == 'NTU'
    assert params['Turbidity [NTU]']['key'] == 'turbidity'
    assert params['ph']['unit'] == ''


def test_proposal_leaves_unknown_columns_empty_rather_than_guessing():
    out = sniff.sniff_bytes(b'a,b,c\n1,2,3\n4,5,6\n7,8,9\n')
    prop = out['proposal']
    assert prop['site']['lat'] is None and prop['site']['lon'] is None
    assert prop['date']['column'] is None

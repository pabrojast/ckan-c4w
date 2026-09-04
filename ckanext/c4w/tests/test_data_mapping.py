# encoding: utf-8
"""The mapping spec: normalise, validate, merge discovered codes."""
import os

import pytest

from ckanext.c4w.data import mapping, sniff
from ckanext.c4w.data.errors import MappingError

FIXTURES = os.path.join(os.path.dirname(__file__), 'fixtures')


@pytest.fixture(scope='module')
def fresh_sniff():
    path = os.path.join(FIXTURES, 'freshwater_wide_sample.csv')
    with open(path, 'rb') as fh:
        return sniff.sniff_bytes(fh.read(), size_bytes=os.path.getsize(path))


@pytest.fixture(scope='module')
def gems_sniff():
    path = os.path.join(FIXTURES, 'gems_long_sample.csv')
    with open(path, 'rb') as fh:
        return sniff.sniff_bytes(fh.read(), size_bytes=os.path.getsize(path))


def test_slug():
    assert mapping.slug(u'As-Tot') == 'as-tot'
    assert mapping.slug(u'Nitrato (mg/L)') == 'nitrato-mg-l'
    assert mapping.slug(u'Ph  units') == 'ph-units'
    assert mapping.slug(u'') == ''


def test_pretty():
    assert mapping.pretty('land_use') == 'Land use'
    assert mapping.pretty('Sample Date') == 'Sample Date'
    assert mapping.pretty('') == ''


def test_proposals_validate_against_their_own_sniff(fresh_sniff, gems_sniff):
    assert mapping.validate_mapping(fresh_sniff['proposal'], fresh_sniff) == {}
    assert mapping.validate_mapping(gems_sniff['proposal'], gems_sniff) == {}


def test_normalise_fills_defaults_and_coerces_form_strings():
    spec = mapping.normalise({
        'layout': 'long',
        'csv': {'delimiter': '\\t', 'has_header': 'on'},
        'site': {'lat': 'Lat', 'lon': 'Lon', 'country_kind': 'bogus'},
        'date': {'column': 'd', 'grain': 'week'},
        'long': {'parameter': 'p', 'value': 'v', 'discover': 'false'},
        'parameters': [
            {'source': 'As Tot', 'bins': '1, 2, 3, 4, 5, 6', 'include': '0'},
            {'source': 'As Tot', 'label': u'Arsénico'},
            'garbage',
        ],
        'dimensions': [{'column': 'body', 'max_values': '9999'}, {'column': ''}],
        'filters': {'min_value': '0.5', 'max_value': ''},
        'unknown': 1,
    })
    assert 'unknown' not in spec
    assert spec['csv']['delimiter'] == '\t'
    assert spec['csv']['has_header'] is True
    assert spec['site']['country_kind'] is None
    assert spec['date']['grain'] == 'year'
    assert spec['date']['format'] == 'auto'
    assert spec['long']['discover'] is False
    assert [p['key'] for p in spec['parameters']] == ['as-tot', 'as-tot-2']
    assert spec['parameters'][0]['bins'] == [1.0, 2.0, 3.0, 4.0, 5.0, 6.0]
    assert spec['parameters'][0]['include'] is False
    assert spec['parameters'][1]['label'] == u'Arsénico'
    assert spec['dimensions'] == [{'key': 'body', 'column': 'body', 'label': 'body',
                                   'max_values': mapping.MAX_DIMENSION_VALUES}]
    assert spec['filters'] == {'drop_negative': True, 'min_value': 0.5, 'max_value': None}


def test_normalise_drops_long_block_for_wide():
    spec = mapping.normalise({'layout': 'wide', 'long': {'parameter': 'x'}})
    assert spec['long'] is None


def test_validate_requires_lat_lon_and_date(fresh_sniff):
    errors = mapping.validate_mapping({'layout': 'wide', 'parameters': [
        {'source': 'nitrate'}]}, fresh_sniff)
    assert 'site.lat' in errors and 'site.lon' in errors and 'date.column' in errors


def test_validate_flags_columns_missing_from_the_file(fresh_sniff):
    spec = dict(fresh_sniff['proposal'])
    spec['site'] = dict(spec['site'], lat='latitude_typo')
    errors = mapping.validate_mapping(spec, fresh_sniff)
    assert errors == {'site.lat': [u'Column "latitude_typo" is not in the file.']}


def test_validate_flags_non_numeric_measurement_and_same_lat_lon(fresh_sniff):
    spec = dict(fresh_sniff['proposal'])
    spec['site'] = dict(spec['site'], lon='lat')
    spec['parameters'] = [{'source': 'body', 'include': True}]
    errors = mapping.validate_mapping(spec, fresh_sniff)
    assert 'site.lon' in errors
    assert errors['parameters.0.source'] == [u'Column "body" does not look numeric.']


def test_validate_flags_duplicate_and_reused_dimension_columns(fresh_sniff):
    spec = dict(fresh_sniff['proposal'])
    spec['dimensions'] = [{'column': 'body'}, {'column': 'body'}, {'column': 'lat'}]
    errors = mapping.validate_mapping(spec, fresh_sniff)
    assert errors['dimensions.1.column'] == [u'Column "body" is listed twice.']
    assert errors['dimensions.2.column'] == [u'Column "lat" is already used elsewhere.']


def test_validate_limits_dimension_count(fresh_sniff):
    spec = dict(fresh_sniff['proposal'])
    spec['dimensions'] = [{'column': c} for c in ('body', 'county', 'land_use', 'site')]
    assert 'dimensions' in mapping.validate_mapping(spec, fresh_sniff)


def test_validate_bins(fresh_sniff):
    spec = dict(fresh_sniff['proposal'])
    spec['parameters'] = [dict(p) for p in spec['parameters']]
    spec['parameters'][0]['bins'] = [1, 2, 3]
    spec['parameters'][1]['bins'] = [1, 2, 2, 3, 4, 5]
    spec['parameters'][2]['bins'] = [0.1, 0.2, 0.5, 1, 2, 5]
    errors = mapping.validate_mapping(spec, fresh_sniff)
    assert errors['parameters.0.bins'] == [u'Bins must be six numbers.']
    assert errors['parameters.1.bins'] == [u'Bins must be strictly increasing.']
    assert 'parameters.2.bins' not in errors


def test_validate_wide_needs_at_least_one_included_parameter(fresh_sniff):
    spec = dict(fresh_sniff['proposal'])
    spec['parameters'] = [dict(p, include=False) for p in spec['parameters']]
    assert 'parameters' in mapping.validate_mapping(spec, fresh_sniff)


def test_validate_long_needs_parameter_and_value(gems_sniff):
    spec = dict(gems_sniff['proposal'])
    spec['long'] = {'parameter': 'Parameter Code', 'value': 'Parameter Code'}
    errors = mapping.validate_mapping(spec, gems_sniff)
    assert 'long.value' in errors
    spec['long'] = {'parameter': None, 'value': None}
    errors = mapping.validate_mapping(spec, gems_sniff)
    assert 'long.parameter' in errors and 'long.value' in errors


def test_validate_country_kind_and_date_format_and_filters(fresh_sniff):
    spec = dict(fresh_sniff['proposal'])
    spec['site'] = dict(spec['site'], country_kind=None)
    spec['date'] = dict(spec['date'], format='DDMMYYYY')
    spec['filters'] = {'min_value': 5, 'max_value': 1}
    errors = mapping.validate_mapping(spec, fresh_sniff)
    assert 'site.country_kind' in errors
    assert errors['date.format'] == [u'Unknown date format.']
    assert 'filters.max_value' in errors


def test_validate_without_a_sniff_only_checks_shape():
    errors = mapping.validate_mapping({'layout': 'wide', 'site': {'lat': 'a', 'lon': 'b'},
                                       'date': {'column': 'c'},
                                       'parameters': [{'source': 'v'}]}, None)
    assert errors == {}


def test_discovered_parameters_merges_listed_and_new_codes():
    spec = {'layout': 'long', 'long': {'parameter': 'p', 'value': 'v', 'discover': True},
            'parameters': [{'source': 'As-Tot', 'label': 'Arsenic', 'unit': 'mg/L'}]}
    out = mapping.discovered_parameters(spec, {'As-Tot': 'ug/l', 'pH': 'pH units', 'Zn': None})
    assert [(p['source'], p['label'], p['unit']) for p in out] == [
        ('As-Tot', 'Arsenic', 'mg/L'), ('Zn', 'Zn', ''), ('pH', 'pH', 'pH units')]
    assert [p['key'] for p in out] == ['as-tot', 'zn', 'ph']


def test_discovered_parameters_respects_discover_off():
    spec = {'layout': 'long', 'long': {'parameter': 'p', 'value': 'v', 'discover': False},
            'parameters': [{'source': 'As-Tot'}]}
    assert [p['source'] for p in mapping.discovered_parameters(spec, {'pH': None})] == ['As-Tot']


def test_mapping_error_is_a_data_error():
    from ckanext.c4w.data.errors import DataError
    assert issubclass(MappingError, DataError)

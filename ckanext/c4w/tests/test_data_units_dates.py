# encoding: utf-8
"""Unit normalisation and date parsing: pure, no CKAN."""
import pytest

from ckanext.c4w.data import dates, units


# --------------------------------------------------------------------------- #
# dates
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize('fmt,raw,expected', [
    ('%Y-%m-%d', '2019-03-05', (2019, 3, 5)),
    ('%Y-%m-%dT%H:%M:%S', '2019-03-05T10:11:12', (2019, 3, 5)),
    ('%Y-%m-%d %H:%M:%S', '2019-03-05 10:11:12', (2019, 3, 5)),
    ('%Y-%m-%d %H:%M', '2019-03-05 10:11', (2019, 3, 5)),
    ('%Y-%m-%dT%H:%M', '2019-03-05T10:11', (2019, 3, 5)),
    ('%Y/%m/%d', '2019/03/05', (2019, 3, 5)),
    ('%m/%d/%Y %H:%M', '03/05/2019 10:11', (2019, 3, 5)),
    ('%m/%d/%Y', '03/05/2019', (2019, 3, 5)),
    ('%d/%m/%Y %H:%M', '05/03/2019 10:11', (2019, 3, 5)),
    ('%d/%m/%Y', '05/03/2019', (2019, 3, 5)),
    ('%d-%m-%Y', '05-03-2019', (2019, 3, 5)),
    ('%d.%m.%Y', '05.03.2019', (2019, 3, 5)),
    ('%Y-%m', '2019-03', (2019, 3, 1)),
    ('%Y', '2019', (2019, 1, 1)),
    ('epoch_s', '1551780672', (2019, 3, 5)),
    ('epoch_ms', '1551780672000', (2019, 3, 5)),
])
def test_every_candidate_format_parses(fmt, raw, expected):
    assert fmt in dates.CANDIDATE_FORMATS
    assert dates.make_parser(fmt)(raw) == expected


@pytest.mark.parametrize('raw', ['', '   ', 'nope', '2019-13-01', '2019-02-30',
                                 '13/45/2010 10:00', '31/12/17'])
def test_bad_dates_return_none_rather_than_raising(raw):
    assert dates.make_parser('auto')(raw) is None
    assert dates.make_parser('%Y-%m-%d')(raw) is None


def test_auto_parser_sticks_to_the_format_that_worked():
    parse = dates.make_parser(None)
    assert parse('05/13/2020') == (2020, 5, 13)      # only m/d fits
    assert parse('05/03/2020') == (2020, 5, 3)       # ambiguous: keeps m/d
    assert parse('2020-01-02') == (2020, 1, 2)       # re-probes on a miss


def test_strptime_fallback_for_an_unknown_format():
    assert dates.make_parser('%d %b %Y')('5 Mar 2019') == (2019, 3, 5)
    assert dates.make_parser('%d %b %Y')('garbage') is None


def test_detect_format_uses_a_day_above_twelve_to_settle_the_order():
    assert dates.detect_format(['05/13/2020', '06/01/2020'])[0] == '%m/%d/%Y'
    assert dates.detect_format(['13/05/2020', '01/06/2020'])[0] == '%d/%m/%Y'
    # All days at or below 12: tie, the US shape (as GEMStat) wins.
    assert dates.detect_format(['05/03/2020', '06/01/2020'])[0] == '%m/%d/%Y'


def test_detect_format_reports_the_time_variant_only_when_values_carry_one():
    assert dates.detect_format(['2020-01-02T10:00:00'])[0] == '%Y-%m-%dT%H:%M:%S'
    assert dates.detect_format(['2020-01-02'])[0] == '%Y-%m-%d'
    assert dates.detect_format(['05/13/2020 10:00'])[0] == '%m/%d/%Y %H:%M'


def test_year_and_epoch_need_a_header_hint():
    assert dates.detect_format(['2019', '2020']) == (None, 0.0)
    assert dates.detect_format(['2019', '2020'], header_hint=True)[0] == '%Y'
    assert dates.detect_format(['1551780672'], header_hint=True)[0] == 'epoch_s'


def test_detect_format_ratio_counts_misses():
    fmt, ratio = dates.detect_format(['2019-01-01', 'x', '2019-01-03', ''])
    assert fmt == '%Y-%m-%d'
    assert ratio == pytest.approx(2 / 3.0)


@pytest.mark.parametrize('grain,key,iso,bounds', [
    ('year', 2019, '2019', ('2019-01-01', '2019-12-31')),
    ('month', 201902, '2019-02', ('2019-02-01', '2019-02-28')),
    ('day', 20190305, '2019-03-05', ('2019-03-05', '2019-03-05')),
])
def test_period_keys_round_trip(grain, key, iso, bounds):
    assert dates.period_key(2019, 3 if grain == 'day' else 2, 5, grain) == (
        key if grain != 'year' else 2019)
    assert dates.period_to_iso(key, grain) == iso
    assert dates.period_bounds(key, grain) == bounds


def test_period_key_rejects_unknown_grain():
    with pytest.raises(ValueError):
        dates.period_key(2019, 1, 1, 'week')


def test_format_resolution():
    assert dates.format_resolution('%Y') == 'year'
    assert dates.format_resolution('%Y-%m') == 'month'
    assert dates.format_resolution('%d/%m/%Y') == 'day'


# --------------------------------------------------------------------------- #
# units
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize('raw,expected', [
    ('mg/l', (u'µg/L', 1000.0, None)),
    ('MG/L', (u'µg/L', 1000.0, None)),
    (u'µg/l', (u'µg/L', 1.0, None)),
    ('ug/l', (u'µg/L', 1.0, None)),
    ('ng/l', (u'µg/L', 0.001, None)),
    ('m3/s', (u'm³/s', 1.0, None)),
    ('pH units', (u'pH', 1.0, None)),
    ('NTU', (u'NTU', 1.0, None)),
    ('cfu/100mL', (u'cfu/100 mL', 1.0, None)),
    ('bananas', (u'bananas', 1.0, None)),      # unknown: kept, not rejected
    (u'µg/g', (None, None, 'sediment_unit')),
    ('mg/kg', (None, None, 'sediment_unit')),
    ('', (None, None, 'empty')),
    (None, (None, None, 'empty')),
])
def test_canonical_units(raw, expected):
    assert units.canonical(raw) == expected


def test_display_unit_switches_to_mg_per_litre_for_large_medians():
    assert units.display_unit(u'µg/L', 999) == (u'µg/L', 1.0)
    assert units.display_unit(u'µg/L', 1000) == (u'mg/L', 0.001)
    assert units.display_unit(u'NTU', 5000) == (u'NTU', 1.0)
    assert units.display_unit(u'µg/L', None) == (u'µg/L', 1.0)


def test_is_mass_per_volume():
    assert units.is_mass_per_volume('mg/L')
    assert not units.is_mass_per_volume('NTU')

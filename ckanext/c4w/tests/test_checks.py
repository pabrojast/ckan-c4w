# encoding: utf-8
"""The CKAN-free value rules behind the navl validators."""
import datetime

import pytest

from ckanext.c4w.logic import checks, forms, ratelimit


# --- checks ---------------------------------------------------------------- #

def test_as_list_accepts_scalar_list_and_csv():
    assert checks.as_list(None) == []
    assert checks.as_list(u'') == []
    assert checks.as_list(u'a, b ,,c') == [u'a', u'b', u'c']
    assert checks.as_list([u'a', u'', None, u'b']) == [u'a', u'b']


def test_country_codes_are_upper_cased_deduplicated_and_capped():
    assert checks.check_country_code(u' cl ') == u'CL'
    assert checks.check_country_codes([u'cl', u'CL', u'ar']) == [u'CL', u'AR']
    with pytest.raises(ValueError):
        checks.check_country_code(u'CHL')
    with pytest.raises(ValueError):
        checks.check_country_codes([u'C%d' % i for i in range(10)] + ['XX'],
                                   limit=3)


def test_language_code():
    assert checks.check_language_code(u'EN') == u'en'
    with pytest.raises(ValueError):
        checks.check_language_code(u'english')


def test_doi_accepts_bare_and_prefixed_forms():
    assert checks.check_doi(u'10.1234/abc.def') == u'10.1234/abc.def'
    assert checks.check_doi(u'https://doi.org/10.1234/abc') == u'10.1234/abc'
    assert checks.check_doi(u'doi:10.1234/abc') == u'10.1234/abc'
    with pytest.raises(ValueError):
        checks.check_doi(u'not-a-doi')


def test_url_requires_http_and_refuses_javascript():
    assert checks.check_url(u'example.org/data') == u'https://example.org/data'
    assert checks.check_url(u'') == u''
    with pytest.raises(ValueError):
        checks.check_url(u'javascript:alert(1)')
    with pytest.raises(ValueError):
        checks.check_url(u'mailto:x@y.z')
    assert checks.check_urls(u'a.org, b.org, a.org') == [
        u'https://a.org', u'https://b.org']


def test_email():
    assert checks.check_email(u' a@b.co ') == u'a@b.co'
    with pytest.raises(ValueError):
        checks.check_email(u'nope')


def test_vocabulary_terms_closed_list_min_and_max():
    assert checks.check_vocabulary_term('water_type', 'groundwater') \
        == 'groundwater'
    with pytest.raises(ValueError):
        checks.check_vocabulary_term('water_type', 'lava')
    got = checks.check_vocabulary_terms(
        'topic', [u'water', u'climate', u'water'])
    assert got == [u'water', u'climate']
    with pytest.raises(ValueError):
        checks.check_vocabulary_terms('topic', [], min_items=1)
    with pytest.raises(ValueError):
        checks.check_vocabulary_terms('topic', [u'water', u'climate'],
                                      max_items=1)
    with pytest.raises(ValueError):
        checks.check_vocabulary_terms('keyword', [u'x'])   # open vocabulary


def test_dates_and_ordering():
    assert checks.check_date(u'2024-03-05') == datetime.date(2024, 3, 5)
    assert checks.check_date(u'') is None
    assert checks.check_date(u'2024-03-05T10:00:00') == \
        datetime.date(2024, 3, 5)
    with pytest.raises(ValueError):
        checks.check_date(u'05/03/2024')
    with pytest.raises(ValueError):
        checks.check_end_after(datetime.date(2024, 1, 2),
                               datetime.date(2024, 1, 1))


def test_password_rules():
    assert checks.check_password(u'longenough', u'longenough') == u'longenough'
    with pytest.raises(ValueError):
        checks.check_password(u'short', u'short')
    with pytest.raises(ValueError):
        checks.check_password(u'longenough', u'different!')


def test_truthiness_and_choice():
    assert checks.check_true(u'on') is True
    assert checks.check_true([u'', u'true']) is True
    with pytest.raises(ValueError):
        checks.check_true(u'')
    assert checks.check_choice(u'year', ('year', 'month')) == u'year'
    with pytest.raises(ValueError):
        checks.check_choice(u'week', ('year', 'month'))


def test_bins_need_six_increasing_numbers():
    assert checks.check_bins(u'1, 2, 3, 4, 5, 6') == [1, 2, 3, 4, 5, 6]
    assert checks.check_bins(u'') is None
    with pytest.raises(ValueError):
        checks.check_bins(u'1 2 3')
    with pytest.raises(ValueError):
        checks.check_bins([1, 2, 2, 4, 5, 6])
    with pytest.raises(ValueError):
        checks.check_bins(u'a b c d e f')


def test_clean_text_caps_length_and_collapses_whitespace():
    assert checks.clean_text(u'  a   b  ') == u'a b'
    with pytest.raises(ValueError):
        checks.clean_text(u'x' * 11, max_length=10)
    with pytest.raises(ValueError):
        checks.clean_text(u'   ', required=True)


# --- forms ----------------------------------------------------------------- #

class _MultiDict(object):
    """The three methods of werkzeug's MultiDict that parse_form_data uses."""

    def __init__(self, pairs):
        self._pairs = list(pairs)

    def keys(self):
        seen = []
        for k, _v in self._pairs:
            if k not in seen:
                seen.append(k)
        return seen

    def getlist(self, key):
        return [v for k, v in self._pairs if k == key]


class _Upload(object):
    def __init__(self, filename):
        self.filename = filename


def test_parse_form_data_scalars_lists_and_declared_multi():
    form = _MultiDict([('title', u'T'), ('topic', u'water'),
                       ('country', u'CL'), ('country', u'AR'),
                       ('empty', u'')])
    out = forms.parse_form_data(form, multi=('topic', 'water_type'))
    assert out['title'] == u'T'
    assert out['topic'] == [u'water']           # declared multi stays a list
    assert out['country'] == [u'CL', u'AR']
    assert out['water_type'] == []              # declared but absent
    assert out['empty'] == u''


def test_parse_form_data_merges_real_uploads_only():
    form = _MultiDict([('title', u'T')])
    files = _MultiDict([('data_file', _Upload(u'x.csv')),
                        ('attachments', _Upload(u'')),
                        ('attachments', _Upload(u'a.pdf'))])
    out = forms.parse_form_data(form, files=files, multi=('attachments',))
    assert out['data_file'].filename == u'x.csv'
    assert [f.filename for f in out['attachments']] == [u'a.pdf']


def test_errors_for_template_flattens_navl_keys():
    got = forms.errors_for_template({
        ('title',): [u'Missing value'],
        'country': u'Bad',
        ('nested', 0, 'x'): [u'No'],
    })
    assert got == {u'title': [u'Missing value'], u'country': [u'Bad'],
                   u'nested__0__x': [u'No']}


def test_first_error_step_follows_the_form_steps():
    assert forms.first_error_step({}) is None
    assert forms.first_error_step({('contact_email',): [u'x']}) == 5
    assert forms.first_error_step({('license_id',): [u'x'],
                                   ('title',): [u'y']}) == 1
    assert forms.first_error_step({('unknown_field',): [u'x']}) == 1


def test_echo_values_never_returns_passwords_or_files():
    out = forms.echo_values({'title': u'T', 'password': u'p',
                             'data_file': _Upload(u'x.csv'),
                             'attachments': [_Upload(u'a.pdf')]})
    assert out == {'title': u'T'}


# --- rate limiter ---------------------------------------------------------- #

def test_sliding_window_limiter_blocks_and_releases():
    limiter = ratelimit.SlidingWindowLimiter()
    now = 1000.0
    for _ in range(3):
        assert limiter.consume('k', 3, 60, now=now) is None
    wait = limiter.consume('k', 3, 60, now=now + 10)
    assert wait == 50
    # A blocked attempt does not extend the window.
    assert limiter.consume('k', 3, 60, now=now + 61) is None
    # Other keys are independent.
    assert limiter.consume('other', 3, 60, now=now + 10) is None

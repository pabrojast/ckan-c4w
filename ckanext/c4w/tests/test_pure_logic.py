# encoding: utf-8
"""Behavioural tests for the CKAN-free logic.

Everything exercised here is importable without CKAN and without a database,
which is what keeps this file fast and what lets it guard the migration
mapping later on.
"""
import pytest

from ckanext.c4w import constants
from ckanext.c4w.logic import sanitize
from ckanext.c4w.text import (
    dump_extras, load_extras, normalise_term, slugify, split_free_terms,
)


# --------------------------------------------------------------------------- #
# slugify
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize('raw,expected', [
    (u'Be Resilient', u'be-resilient'),
    (u'  Spaced   out  ', u'spaced-out'),
    (u'Snow & ice', u'snow-ice'),
    (u'White Paper / Green Paper', u'white-paper-green-paper'),
    (u'--leading and trailing--', u'leading-and-trailing'),
    (u'', u''),
    (None, u''),
])
def test_slugify(raw, expected):
    assert slugify(raw) == expected


def test_slugify_folds_accents_rather_than_dropping_them():
    """Dropping the accented character would lose a letter, not just a mark."""
    assert slugify(u'Andalucía') == u'andalucia'
    assert slugify(u'Côte d’Ivoire') == u'cote-d-ivoire'


def test_slugify_never_ends_on_a_separator_after_truncation():
    """A cut that lands on the separator would leave a trailing dash."""
    assert not slugify(u'a' * 40 + u' ' + u'b' * 60, max_length=41).endswith(u'-')


def test_normalise_term_allows_longer_values_than_a_url_slug():
    long_term = u'Download software for distributed computing'
    assert normalise_term(long_term) == u'download-software-for-distributed-computing'


# --------------------------------------------------------------------------- #
# free-text tag fields
# --------------------------------------------------------------------------- #

def test_split_free_terms_trims_dedupes_and_keeps_order():
    got = split_free_terms(u' river , Water quality ,river,  ')
    assert got == [(u'river', u'river'), (u'water-quality', u'Water quality')]


def test_split_free_terms_dedupes_case_insensitively_keeping_first_label():
    got = split_free_terms(u'River, RIVER')
    assert got == [(u'river', u'River')]


def test_split_free_terms_caps_the_count():
    """A paste accident must not write thousands of link rows."""
    got = split_free_terms(u','.join(u'term%d' % i for i in range(500)))
    assert len(got) == 30


def test_split_free_terms_accepts_a_list():
    assert split_free_terms([u'A', u'B']) == [(u'a', u'A'), (u'b', u'B')]


def test_split_free_terms_of_nothing_is_empty():
    assert split_free_terms(None) == []
    assert split_free_terms(u'') == []
    assert split_free_terms(u' , , ') == []


# --------------------------------------------------------------------------- #
# extras
# --------------------------------------------------------------------------- #

def test_load_extras_degrades_instead_of_raising():
    """One malformed row must not take down a whole listing page."""
    assert load_extras(u'{not json') == {}
    assert load_extras(u'[1,2,3]') == {}      # valid JSON, wrong shape
    assert load_extras(None) == {}
    assert load_extras(u'{"a": 1}') == {'a': 1}


def test_dump_extras_serialises_dates():
    import datetime
    out = dump_extras({'d': datetime.date(2026, 8, 28)})
    assert '"2026-08-28"' in out


def test_extras_round_trip():
    payload = {'target_group': u'Schools', 'uses_ai': True, 'n': 12}
    assert load_extras(dump_extras(payload)) == payload


# --------------------------------------------------------------------------- #
# vocabularies
# --------------------------------------------------------------------------- #

def test_vocabulary_terms_returns_none_for_open_vocabularies():
    """None means 'not closed' -- the caller accepts the value as given."""
    assert constants.vocabulary_terms('keyword') is None
    assert constants.vocabulary_terms('nonexistent') is None
    assert 'snow-ice' in constants.vocabulary_terms('water_type')


def test_label_for_falls_back_to_the_term():
    """A term that predates a vocabulary edit still renders."""
    assert constants.label_for('water_type', 'snow-ice') == u'Snow & ice'
    assert constants.label_for('water_type', 'retired-term') == 'retired-term'


def test_org_types_include_the_value_production_actually_uses():
    """The Django fixture seeds six types; production serves a seventh.

    UNESCO IHP, UNESCO MAB and IHE Delft are all 'Intergovernmental', added
    through the Django admin after the fixture was written. Dropping it would
    silently retype three organisations on import.
    """
    assert 'intergovernmental' in constants.vocabulary_terms('org_type')


# --------------------------------------------------------------------------- #
# sanitisation
# --------------------------------------------------------------------------- #

def test_script_bodies_never_survive_as_visible_text():
    """bleach's strip=True drops the tag but keeps its text.

    That is inert, but it dumps script source into the page as copy, so the
    element is removed whole beforehand.
    """
    out = sanitize.sanitize_html(u'<script>alert(1)</script>hello')
    assert 'alert(1)' not in out
    assert 'hello' in out


def test_the_two_allowlists_are_separate_functions():
    """Not one function with an allowlist= argument.

    A parameter is an open invitation for a future caller to hand the wide
    list to a restrictive path.
    """
    assert callable(sanitize.sanitize_html)
    assert callable(sanitize.sanitize_rich_html)
    assert sanitize.ALLOWED_TAGS != sanitize.RICH_ALLOWED_TAGS


def test_restrictive_list_refuses_tables_and_images():
    assert 'table' not in sanitize.ALLOWED_TAGS
    assert 'img' not in sanitize.ALLOWED_TAGS
    assert 'img' not in sanitize.RICH_ALLOWED_TAGS


def test_rich_list_allows_tables_but_still_no_embeds():
    assert 'table' in sanitize.RICH_ALLOWED_TAGS
    for tag in ('iframe', 'script', 'style', 'object', 'h1', 'h2'):
        assert tag not in sanitize.RICH_ALLOWED_TAGS


def test_only_safe_link_protocols_are_allowed():
    assert set(sanitize.ALLOWED_PROTOCOLS) == {'http', 'https', 'mailto'}


def test_falsy_input_passes_through_unchanged():
    assert sanitize.sanitize_html(u'') == u''
    assert sanitize.sanitize_html(None) is None


def test_sanitizer_fails_closed_without_bleach(monkeypatch):
    """No bleach must mean NO markup reaches storage, not unfiltered markup."""
    import builtins
    real_import = builtins.__import__

    def _no_bleach(name, *args, **kwargs):
        if name == 'bleach':
            raise ImportError('bleach disabled for this test')
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, '__import__', _no_bleach)
    out = sanitize.sanitize_html(u'<b>bold</b> <a href="http://x">link</a>')
    assert '<' not in out
    assert 'bold' in out and 'link' in out

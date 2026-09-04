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
    """Descriptions, abstracts and rejection reasons get the narrow list."""
    for tag in ('table', 'img', 'iframe', 'figure'):
        assert tag not in sanitize.ALLOWED_TAGS


def test_rich_list_allows_tables_images_and_players_only():
    """img and iframe are admitted; script, style, object and h1/h2 are not.

    The news corpus carries 18 in-body images and 2 YouTube embeds, and
    sanitisation runs before storage, so refusing those tags would destroy the
    content rather than merely hide it. What keeps that safe is that the src
    VALUE is validated -- see test_image_src_allowlist.
    """
    for tag in ('table', 'img', 'iframe'):
        assert tag in sanitize.RICH_ALLOWED_TAGS
    for tag in ('script', 'style', 'object', 'embed', 'h1', 'h2'):
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


# --------------------------------------------------------------------------- #
# Vocabulary registry consistency
# --------------------------------------------------------------------------- #

def test_geographic_extent_resolves_from_either_registry():
    """It is many-valued on a project and single-valued on a platform.

    It therefore lives in VOCABULARIES while being filtered as a native
    column, and a lookup that consulted only COLUMN_VOCABULARIES returned an
    empty option list -- which made the whole platform facet vanish.
    """
    assert constants.vocabulary_terms('geographic_extent') is not None
    assert 'macro-regional' in constants.vocabulary_terms('geographic_extent')


def test_post_statuses_have_one_definition():
    """The literal 'published' used to be spelled out in three modules."""
    assert constants.POST_STATUS_PUBLISHED == 'published'
    assert constants.POST_STATUS_DRAFT == 'draft'
    terms = constants.vocabulary_terms('post_status')
    assert terms == {'draft', 'published'}


def test_moderate_error_rejects_unknown_pairs_before_sql():
    """The URL contract is a closed list. Anything else is a 404, not SQL."""
    assert constants.moderate_error('project', 'approve') is None
    assert constants.moderate_error('project', 'hide') is None
    assert constants.moderate_error('project', 'feature') is None
    assert constants.moderate_error('nope', 'approve') == 'unknown_entity'
    assert constants.moderate_error('project', 'delete') == 'unknown_op'
    assert constants.moderate_error('post', 'approve') == 'not_moderated'
    assert constants.moderate_error('event', 'hide') == 'no_hidden'
    assert constants.moderate_error('organisation', 'feature') == 'no_featured'


def test_detail_endpoints_cover_every_entity_type():
    assert set(constants.DETAIL_ENDPOINTS) == set(constants.ENTITY_TYPES)


def test_submit_choices_are_moderated_types():
    keys = [key for key, _title, _hint in constants.SUBMIT_CHOICES]
    assert set(keys) <= set(constants.MODERATED_ENTITY_TYPES)
    assert 'project' in keys


def test_subscripts_survive_sanitisation():
    """Project 36 is KdUINO, about the coefficient K_d.

    Stripping <sub> flattens 'K<sub>d</sub>' to the meaningless 'Kd', and the
    loss is permanent because sanitisation happens before storage.
    """
    assert 'sub' in sanitize.ALLOWED_TAGS
    assert 'sup' in sanitize.ALLOWED_TAGS


# --------------------------------------------------------------------------- #
# Rich-content media allowlist
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize('src,ok', [
    (u'/uploads/c4w/photo.png', True),      # a re-hosted image
    (u'/citizens4water/media/images/x.jpg', True),
    (u'https://tracker.example/pixel.gif', False),
    (u'//tracker.example/pixel.gif', False),  # protocol-relative
    (u'data:image/png;base64,AAAA', False),
    (u'/uploads/c4w/../../etc/passwd', False),
    (u'', False),
    (None, False),
])
def test_image_src_allowlist(src, ok):
    """An unvalidated <img src> is a tracking pixel that fires for every reader.

    So the tag is allowed but the VALUE is what decides: a site-relative path
    (what a re-hosted image is) or an explicitly configured media host.
    """
    assert sanitize.image_src_ok(src) is ok


@pytest.mark.parametrize('src,ok', [
    (u'https://www.youtube-nocookie.com/embed/abc', True),
    (u'https://player.vimeo.com/video/123', True),
    (u'https://evil.example/embed/abc', False),
    (u'//www.youtube.com/embed/abc', False),   # no scheme
    (u'/local/embed', False),
    (u'', False),
])
def test_embed_src_allowlist(src, ok):
    assert sanitize.embed_src_ok(src) is ok


def test_embed_defaults_put_the_no_cookie_host_first():
    """The portal should not set a third-party cookie on someone who scrolled."""
    assert sanitize.DEFAULT_EMBED_HOSTS[0] == 'www.youtube-nocookie.com'


def test_images_and_embeds_are_rich_only():
    """The restrictive list must never admit them.

    It covers descriptions, abstracts and rejection reasons -- none of which
    has any business carrying an embed.
    """
    for tag in ('img', 'iframe', 'figure'):
        assert tag not in sanitize.ALLOWED_TAGS
        assert tag in sanitize.RICH_ALLOWED_TAGS


# --------------------------------------------------------------------------- #
# Sanitiser end-to-end, with the real bleach
# --------------------------------------------------------------------------- #

bleach_only = pytest.mark.skipif(
    __import__('importlib').util.find_spec('bleach') is None,
    reason='needs the real bleach; the fail-closed path is tested separately')


@bleach_only
def test_a_rehosted_image_survives_but_a_third_party_one_does_not():
    """The tag is allowed; the src decides."""
    out = sanitize.sanitize_rich_html(
        u'<p><img src="/uploads/c4w/a.png" alt="A">'
        u'<img src="https://tracker.example/p.gif"></p>')
    assert '/uploads/c4w/a.png' in out
    assert 'tracker.example' not in out


@bleach_only
def test_an_allowlisted_player_survives_and_others_do_not():
    out = sanitize.sanitize_rich_html(
        u'<iframe src="https://www.youtube-nocookie.com/embed/x"></iframe>'
        u'<iframe src="https://evil.example/x"></iframe>')
    assert 'youtube-nocookie.com/embed/x' in out
    assert 'evil.example' not in out


@bleach_only
def test_a_subscript_survives_the_restrictive_list():
    """'K<sub>d</sub>' must not flatten to the meaningless 'Kd'."""
    assert '<sub>' in sanitize.sanitize_html(u'K<sub>d</sub>')


@bleach_only
def test_list_structure_survives_the_restrictive_list():
    """The fail-closed path fuses list items into one run of words.

    That is acceptable as a runtime safety net and unacceptable for a one-way
    migration, which is why the importer refuses to start without bleach.
    """
    out = sanitize.sanitize_html(u'<ul><li>one</li><li>two</li></ul>')
    assert '<li>' in out


@bleach_only
def test_a_script_tag_leaves_nothing_behind_in_either_list():
    for clean in (sanitize.sanitize_html, sanitize.sanitize_rich_html):
        out = clean(u'<script>alert(1)</script><p>ok</p>')
        assert 'alert' not in out and 'ok' in out


@bleach_only
def test_an_image_keeps_only_the_attributes_we_named():
    out = sanitize.sanitize_rich_html(
        u'<img src="/uploads/c4w/a.png" alt="A" onerror="x()" class="evil">')
    assert 'onerror' not in out and 'class' not in out
    assert 'alt="A"' in out


# --------------------------------------------------------------------------- #
# The vocabulary invariant
# --------------------------------------------------------------------------- #

# Django stores a CODE rather than a label for these three, so the term is not
# derived from the label.
_CODE_BACKED = ('event_type', 'lead_partner_type', 'post_status')


def test_every_lookup_backed_term_is_the_slug_of_its_own_label():
    """The importer can only reach a term by slugifying the Django label.

    A hand-abbreviated term therefore lands OUTSIDE its own vocabulary, and
    the failure is silent: facet_group.html hides any option absent from the
    counts, so the value simply never appears. This caught fifteen real
    mismatches -- 'Not yet started' had been shortened to 'not-started', which
    would have put five projects in a Status facet that could never match
    them, and three of the four training levels had the same defect.
    """
    offenders = []
    registries = (list(constants.VOCABULARIES.items())
                  + list(constants.COLUMN_VOCABULARIES.items()))
    for vocabulary, pairs in registries:
        if vocabulary in _CODE_BACKED:
            continue
        for term, label in pairs:
            expected = normalise_term(label)
            if term != expected:
                offenders.append('%s: %r should be %r (label %r)'
                                 % (vocabulary, term, expected, label))
    assert not offenders, offenders


# --------------------------------------------------------------------------- #
# URL handling a browser will not agree with
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize('src', [
    u'/\t/evil.example/x.png',
    u'/\n/evil.example/x.png',
    u'/\r/evil.example/x.png',
    u'/\x00/evil.example/x.png',
])
def test_url_whitespace_cannot_smuggle_a_protocol_relative_image(src):
    """A browser DELETES tab, LF and CR from a URL before resolving it.

    '/<TAB>/evil.example/x.png' therefore becomes '//evil.example/x.png' --
    protocol-relative to a third party, past a check that only read the first
    two characters.
    """
    assert sanitize.image_src_ok(src) is False


@pytest.mark.parametrize('url', [
    u'javascript:alert(1)',
    u'JavaScript:alert(1)',
    u'  javascript:alert(1)  ',
    u'java\tscript:alert(1)',      # a browser strips the tab and runs it
    u'java\nscript:alert(1)',
    u'data:text/html,<script>alert(1)</script>',
    u'vbscript:msgbox(1)',
])
def test_a_dangerous_scheme_never_reaches_an_href(url):
    """These values are rendered straight into an href.

    A template cannot sanitise an attribute, so the refusal has to happen
    here or it is a stored XSS on a page anyone can reach.
    """
    from ckanext.c4w.text import ensure_scheme
    assert ensure_scheme(url) is None


@pytest.mark.parametrize('url,expected', [
    (u'https://example.org/a', u'https://example.org/a'),
    (u'www.example.org', u'https://www.example.org'),
    (u'mailto:someone@example.org', u'mailto:someone@example.org'),
    (u'//cdn.example.org/a', u'https://cdn.example.org/a'),
    (u'/relative/path', u'/relative/path'),
])
def test_a_safe_url_is_left_usable(url, expected):
    from ckanext.c4w.text import ensure_scheme
    assert ensure_scheme(url) == expected

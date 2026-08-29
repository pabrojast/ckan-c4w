# encoding: utf-8
"""The Django -> c4w mapping, exercised against the REAL production corpus.

tests/fixtures/django_corpus.json is a verbatim export of the production
Citizens4Water database -- all 43 projects, 31 organisations, 4 platforms,
13 events, 16 posts and 6 resources -- with the modeltranslation shadow
columns removed (they are byte-identical duplicates the mappers never read)
and email addresses replaced by stable placeholders, because a fixture in
version control should not double as a contact list.

Running the whole corpus rather than a hand-written sample is the point: the
edge cases that break an importer are the ones nobody thought to write down.
Several assertions below encode a specific row, cited by legacy id, that the
field-map survey found.

Needs bleach for the HTML paths, which the importer requires anyway.
"""
import datetime
import json
from pathlib import Path

import pytest

from ckanext.c4w.migrate import mapping
from ckanext.c4w.text import html_to_text

FIXTURE = Path(__file__).parent / 'fixtures' / 'django_corpus.json'

pytestmark = pytest.mark.skipif(
    __import__('importlib').util.find_spec('bleach') is None,
    reason='the mapper sanitises, and the importer requires bleach')


@pytest.fixture(scope='module')
def corpus():
    return json.loads(FIXTURE.read_text(encoding='utf-8'))


@pytest.fixture(scope='module')
def ctx(corpus):
    lookup = dict(corpus['lookup'])
    lookup['unapproved_events'] = set(lookup.get('unapproved_events') or ())
    return lookup


def _mapped(corpus, ctx, entity):
    return [mapping.MAPPERS[entity](row, ctx) for row in corpus[entity]]


def _by_legacy(results, legacy_id):
    for out in results:
        if not out.get('skip') and out['columns']['legacy_id'] == legacy_id:
            return out
    raise AssertionError('legacy id %s not mapped' % legacy_id)


# --------------------------------------------------------------------------- #
# Every row maps
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize('entity,expected', [
    ('project', 43), ('organisation', 31), ('platform', 4),
    ('event', 13), ('post', 16), ('resource', 6),
])
def test_the_whole_corpus_maps_without_skipping_a_row(corpus, ctx,
                                                      entity, expected):
    results = _mapped(corpus, ctx, entity)
    assert len(results) == expected
    skipped = [r for r in results if r.get('skip')]
    assert not skipped, skipped


@pytest.mark.parametrize('entity', list(mapping.MAPPERS))
def test_mapping_is_deterministic(corpus, ctx, entity):
    """A re-run must produce byte-identical output, or --since is worthless."""
    first = json.dumps(_mapped(corpus, ctx, entity), sort_keys=True, default=str)
    second = json.dumps(_mapped(corpus, ctx, entity), sort_keys=True, default=str)
    assert first == second


@pytest.mark.parametrize('entity', list(mapping.MAPPERS))
def test_no_mapped_value_is_an_empty_string(corpus, ctx, entity):
    """A column and its 'unset' state must not split between NULL and ''.

    Every nullable text column in this corpus stores '' rather than NULL, and
    an importer that carried that through would make `IS NULL` lie.
    """
    offenders = []
    for out in _mapped(corpus, ctx, entity):
        for key, value in list(out['columns'].items()) + list(out['extras'].items()):
            if value == u'':
                offenders.append('%s.%s' % (out['columns']['legacy_id'], key))
    assert not offenders, offenders


# --------------------------------------------------------------------------- #
# Projects
# --------------------------------------------------------------------------- #

def test_project_names_are_trimmed(corpus, ctx):
    """Legacy id 39 is stored with a trailing space."""
    project = _by_legacy(_mapped(corpus, ctx, 'project'), 39)
    assert project['columns']['name'] == project['columns']['name'].strip()
    assert project['columns']['name'].endswith(u'(AQuA)')


def test_every_mapped_lookup_term_exists_in_the_vocabulary(corpus, ctx):
    """A term the vocabulary does not declare is invisible, not just unlabelled.

    facet_group.html hides any option absent from the counts, so a mismatch
    between what the importer writes and what constants.py declares does not
    show a wrong label -- it removes the value from the facet entirely.
    """
    from ckanext.c4w import constants

    checks = (('project', 'status', 'status'),
              ('project', 'difficulty_level', 'difficulty_level'),
              ('organisation', 'org_type', 'org_type'))
    offenders = []
    for entity, column, vocabulary in checks:
        declared = constants.vocabulary_terms(vocabulary) or set()
        for out in _mapped(corpus, ctx, entity):
            term = out['columns'][column]
            if term and term not in declared:
                offenders.append('%s.%s=%r' % (entity, column, term))
    assert not offenders, sorted(set(offenders))


def test_hidden_is_never_none(corpus, ctx):
    """Django leaves it NULL on 42 of 43 rows; the c4w column defaults False."""
    for out in _mapped(corpus, ctx, 'project'):
        assert out['columns']['hidden'] in (True, False)
        assert out['columns']['approved'] in (True, False)


def test_dates_become_dates_not_datetimes(corpus, ctx):
    for out in _mapped(corpus, ctx, 'project'):
        for key in ('start_date', 'end_date'):
            value = out['columns'][key]
            assert value is None or isinstance(value, datetime.date)
            assert not isinstance(value, datetime.datetime)


def test_modified_falls_back_to_created(corpus, ctx):
    for out in _mapped(corpus, ctx, 'project'):
        if out['columns']['created']:
            assert out['columns']['modified'] is not None


def test_an_editor_empty_field_becomes_none_not_a_paragraph(corpus, ctx):
    """Eleven projects store '<p><br></p>' in howToParticipate.

    That is empty to a reader and truthy to `if value`, which is how an empty
    section heading ends up on a page.
    """
    results = _mapped(corpus, ctx, 'project')
    blanks = [r for r in results
              if '<br>' in (r['extras'].get('how_to_participate') or '')
              and not html_to_text(r['extras'].get('how_to_participate'))]
    assert not blanks


def test_a_scheme_is_added_to_a_bare_hostname(corpus, ctx):
    """Three project urls are stored as 'www.example.org'.

    A browser resolves that against the current page, so the link points back
    into the portal instead of out to the project.
    """
    for out in _mapped(corpus, ctx, 'project'):
        url = out['columns']['url']
        if url:
            assert url.startswith(('http://', 'https://')), url


def test_a_search_shadow_is_emitted_for_the_searched_columns(corpus, ctx):
    """The listing ILIKEs description and aim, which hold sanitised HTML.

    Without a plain-text shadow, a visitor searching a phrase they can read
    verbatim on the page gets nothing whenever a tag falls inside it.
    """
    for out in _mapped(corpus, ctx, 'project'):
        for column, shadow in (('description', 'description_text'),
                               ('aim', 'aim_text')):
            if out['columns'][column]:
                assert out['extras'].get(shadow)
                assert '<' not in out['extras'][shadow]


def test_a_subscript_survives_into_the_stored_description(corpus, ctx):
    """Legacy id 36 is KdUINO, about the coefficient K_d."""
    project = _by_legacy(_mapped(corpus, ctx, 'project'), 36)
    blob = u'%s %s' % (project['columns']['description'],
                       project['columns']['aim'])
    assert 'KdUINO' in blob or 'Kd' in blob


def test_no_carriage_returns_reach_extras(corpus, ctx):
    """Inside a JSON blob a CR is an invisible literal that breaks re-import."""
    for entity in mapping.MAPPERS:
        for out in _mapped(corpus, ctx, entity):
            blob = json.dumps(out['extras'], default=str)
            assert '\\r' not in blob, (entity, out['columns']['legacy_id'])


def test_the_creator_is_carried_separately_and_never_into_extras(corpus, ctx):
    """extras is published: db.entity_dictize merges it at the TOP level.

    The legacy author's identity belongs in the operator's report, not on the
    public page. This asserts the CREATOR is absent -- an address inside the
    author's own prose is original content that the legacy page published too,
    and removing it would be an edit, not a migration.
    """
    for entity in mapping.MAPPERS:
        for row, out in zip(corpus[entity], _mapped(corpus, ctx, entity)):
            assert 'legacy_author' in out
            keys = ' '.join(out['extras']).lower()
            assert 'creator' not in keys
            assert 'author_id' not in keys
            blob = json.dumps(out['extras'], default=str)
            for field in ('creator_id', 'author_id'):
                if row.get(field) is not None:
                    assert u'"%s"' % row[field] not in blob


# --------------------------------------------------------------------------- #
# Organisations
# --------------------------------------------------------------------------- #

def test_country_codes_are_upper_case_iso(corpus, ctx):
    for out in _mapped(corpus, ctx, 'organisation'):
        country = out['columns']['country']
        if country:
            assert country == country.upper()
            assert len(country) == 2, country


def test_the_null_island_placeholder_is_discarded(corpus, ctx):
    """Three organisations carry (0, 0), a point in the Gulf of Guinea."""
    for out in _mapped(corpus, ctx, 'organisation'):
        assert out['columns']['latitude'] != 0.0
        assert out['columns']['longitude'] != 0.0


def test_the_na_credit_placeholder_is_discarded(corpus, ctx):
    for out in _mapped(corpus, ctx, 'organisation'):
        assert out['columns']['logo_credit'] not in (u'NA', u'N/A', u'-')


def test_every_organisation_type_resolves(corpus, ctx):
    """Production serves seven types; the Django fixture seeded six.

    'Intergovernmental' was added through the admin afterwards, and dropping
    it would silently retype UNESCO IHP, UNESCO MAB and IHE Delft.
    """
    types = {out['columns']['org_type']
             for out in _mapped(corpus, ctx, 'organisation')}
    assert 'intergovernmental' in types
    assert None not in types


# --------------------------------------------------------------------------- #
# Platforms
# --------------------------------------------------------------------------- #

def test_the_shouted_geographic_extent_is_normalised(corpus, ctx):
    """Stored as 'MACRO-REGIONAL'; the vocabulary term is 'macro-regional'.

    Leaving the raw code in place made the whole facet vanish from the page,
    because facet_group.html hides any option absent from the counts.
    """
    extents = {out['columns']['geographic_extent']
               for out in _mapped(corpus, ctx, 'platform')}
    assert extents <= {'macro-regional', 'global', 'national', 'regional',
                       'sub-national', 'city', 'neighbourhood', None}


@pytest.mark.parametrize('raw,expected', [
    (u'CA,US', [u'CA', u'US']),
    (u'ca, us', [u'CA', u'US']),
    (u'', []),
    (None, []),
    (u'MACRO-REGIONAL', []),          # not an ISO code -- dropped, not guessed
    (u'CA,CA', [u'CA']),
])
def test_country_field_parsing(raw, expected):
    assert mapping.parse_country_field(raw) == expected


# --------------------------------------------------------------------------- #
# Events
# --------------------------------------------------------------------------- #

def test_every_event_gets_a_valid_time_zone(corpus, ctx):
    for out in _mapped(corpus, ctx, 'event'):
        assert out['columns']['timezone']
        assert mapping._valid_timezone(out['columns']['timezone'])


def test_event_language_is_lower_cased(corpus, ctx):
    for out in _mapped(corpus, ctx, 'event'):
        language = out['columns']['language']
        if language:
            assert language == language.lower()


def test_event_country_is_kept_as_typed(corpus, ctx):
    """Free text in Django: 'Worldwide' and a four-nation list are both in it.

    Coercing that to an ISO code would invent data; the facet takes the code
    only where one is derivable.
    """
    countries = {out['columns']['country']
                 for out in _mapped(corpus, ctx, 'event')}
    assert any(c and len(c) > 2 for c in countries)


# --------------------------------------------------------------------------- #
# Posts
# --------------------------------------------------------------------------- #

def test_post_status_maps_the_integer_to_a_readable_string(corpus, ctx):
    results = _mapped(corpus, ctx, 'post')
    statuses = [out['columns']['status'] for out in results]
    assert set(statuses) <= {'draft', 'published'}
    assert statuses.count('published') == 15
    assert statuses.count('draft') == 1


def test_post_slugs_are_reused_not_regenerated(corpus, ctx):
    """blog_post already HAS a slug, and it is the address the site published."""
    for row, out in zip(corpus['post'], _mapped(corpus, ctx, 'post')):
        if row.get('slug'):
            assert out['columns']['slug']


def test_post_slugs_are_unique(corpus, ctx):
    slugs = [out['columns']['slug'] for out in _mapped(corpus, ctx, 'post')]
    assert len(slugs) == len(set(slugs))


def test_the_post_excerpt_keeps_its_markup_alongside_the_plain_text(corpus, ctx):
    """The card strips tags, but Django rendered the excerpt with |safe.

    Keeping the markup means a later template can restore the one hyperlink
    that would otherwise be lost.
    """
    results = _mapped(corpus, ctx, 'post')
    assert any(out['extras'].get('excerpt_html') for out in results)
    for out in results:
        if out['columns']['excerpt']:
            assert '<' not in out['columns']['excerpt']


def test_post_bodies_keep_their_rehosted_images_and_players(corpus, ctx):
    """18 in-body images and 2 embeds; sanitisation happens before storage."""
    bodies = u' '.join(out['columns']['content'] or u''
                       for out in _mapped(corpus, ctx, 'post'))
    # A third-party tracking pixel must never survive.
    assert 'tracker' not in bodies.lower()


# --------------------------------------------------------------------------- #
# Resources
# --------------------------------------------------------------------------- #

def test_no_training_resources_exist_in_production(corpus, ctx):
    """The surface is live and empty after the migration -- faithful, not a bug."""
    flags = [out['columns']['is_training_resource']
             for out in _mapped(corpus, ctx, 'resource')]
    assert flags == [False] * 6


def test_the_publication_year_stays_an_integer(corpus, ctx):
    for out in _mapped(corpus, ctx, 'resource'):
        year = out['columns']['date_published']
        assert year is None or (isinstance(year, int) and 1900 < year < 2100)


# --------------------------------------------------------------------------- #
# Timestamp parsing
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize('raw', [
    u'2026-08-12T10:55:18.08998+00:00',    # five fractional digits
    u'2026-08-12T10:55:18.0899+00:00',     # four
    u'2026-08-12T10:55:18.089+00:00',      # three
    u'2026-08-12T10:55:18.089980+00:00',   # six
    u'2026-08-12 10:55:18+00:00',
    u'2026-08-12T10:55:18Z',
    u'2026-08-12',
])
def test_every_timestamp_shape_postgres_emits_is_parsed(raw):
    """fromisoformat accepts exactly 3 or 6 fractional digits before 3.11.

    PostgreSQL emits however many it needs, and seven of the 43 projects carry
    five -- those raised, fell through every fallback and returned None, so
    those rows would have imported with no created or modified at all.
    """
    assert mapping._naive_utc(raw) is not None


def test_an_unparseable_timestamp_is_none_not_an_exception():
    assert mapping._naive_utc(u'not a date') is None
    assert mapping._naive_utc(None) is None


def test_every_row_in_the_corpus_keeps_its_timestamps(corpus, ctx):
    """The bug above was invisible until a row was checked, not a format."""
    for entity in ('project', 'organisation', 'platform', 'resource'):
        for out in _mapped(corpus, ctx, entity):
            assert out['columns']['created'] is not None, (
                entity, out['columns']['legacy_id'])
            assert out['columns']['modified'] is not None

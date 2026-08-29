# encoding: utf-8
"""The importer end to end, against the real corpus.

The runner is driven by a FAKE reader backed by tests/fixtures/django_corpus.json
-- a verbatim export of the production database -- writing into a fresh
in-memory SQLite schema. That exercises the parts no unit test reaches: upsert
by legacy id, slug stability across runs, link rewriting, relation resolution
between entities, and idempotency.

The one thing deliberately not exercised is the media upload, which needs a
configured object store; ``media_root=None`` makes every file "missing", which
is itself the behaviour to check -- a referenced file that is gone must be
reported and skipped, never fatal.
"""
import json
from pathlib import Path

import pytest

try:
    import sqlalchemy as sa
    import ckan  # noqa: F401
    from ckanext.c4w import db
    from ckanext.c4w.migrate import runner as runner_module
    HAVE_CKAN = True
except Exception:  # pragma: no cover
    HAVE_CKAN = False

pytestmark = [
    pytest.mark.skipif(not HAVE_CKAN, reason='requires CKAN'),
    pytest.mark.skipif(
        __import__('importlib').util.find_spec('bleach') is None,
        reason='the importer requires bleach'),
]

FIXTURE = Path(__file__).parent / 'fixtures' / 'django_corpus.json'


class FakeReader(object):
    """Serves the corpus fixture through the source.Reader interface."""

    def __init__(self, corpus):
        self.corpus = corpus
        self.joins = corpus['joins']

    def lookups(self):
        merged = dict(self.corpus['lookup'])
        merged.update(self.joins['lookup_full'])
        return merged

    def entity_rows(self, entity):
        return list(self.corpus[entity])

    def users(self):
        return list(self.corpus['users'])

    def categories(self):
        return list(self.joins['categories'])

    def term_links(self, entity):
        from ckanext.c4w.migrate import source

        out = {}
        for table, _fk, _target, vocabulary in source.TERM_JOINS.get(entity, ()):
            key = table.strip('"').replace('projects_project_', 'project_') \
                       .replace('resources_resource_', 'resource_')
            for subject, target in self.joins['term_joins'].get(key, []):
                out.setdefault(subject, {}).setdefault(
                    vocabulary, []).append(target)
        return out

    def relation_links(self, entity):
        from ckanext.c4w.migrate import source

        out = {}
        for table, _fk, _t, predicate, object_type in (
                source.RELATION_JOINS.get(entity, ())):
            key = (table.strip('"')
                   .replace('projects_project_', 'project_')
                   .replace('organisations_organisation_', 'organisation_')
                   .replace('resources_resource_', 'resource_')
                   .replace('platforms_platform_', 'platform_')
                   .replace('events_event_', 'event_'))
            for subject, target in self.joins['relations'].get(key, []):
                out.setdefault(subject, []).append(
                    (predicate, object_type, target))
        return out

    def project_countries(self):
        out = {}
        for project_id, code, lat, lon in self.joins['project_countries']:
            if not code:
                continue
            out.setdefault(project_id, []).append({
                'code': code.strip().upper(),
                'lat': float(lat) if lat is not None else None,
                'lon': float(lon) if lon is not None else None,
            })
        return out

    def rows(self, sql, params=None):
        # Only used for the optional easy_thumbnails lookup.
        raise RuntimeError('no such table')


class FakeResolver(object):
    """Resolves nothing, exactly like a portal with no matching accounts.

    That is the real situation for the most prolific legacy author, so it is
    the case worth defaulting to in the tests.
    """

    def __init__(self):
        self.unresolved = {}

    def resolve(self, django_id):
        if django_id is not None:
            self.unresolved[django_id] = 'no CKAN account'
        return None

    def report(self):
        return [{'django_id': k, 'reason': v}
                for k, v in sorted(self.unresolved.items())]


@pytest.fixture(scope='module')
def corpus():
    return json.loads(FIXTURE.read_text(encoding='utf-8'))


@pytest.fixture
def session():
    engine = sa.create_engine('sqlite://')
    db.ensure_mappers()
    db.metadata.create_all(bind=engine, tables=list(db._ALL_TABLES))
    db.Session.remove()
    db.Session.configure(bind=engine)
    try:
        yield db.Session
    finally:
        db.Session.remove()
        engine.dispose()


def _run(corpus, **kwargs):
    engine = runner_module.Runner(dsn=None, media_root=None, **kwargs)
    # ensure_tables() would look for CKAN's own engine; the fixture already
    # created the schema on the throwaway one.
    engine_run = engine.run
    original = db.ensure_tables
    db.ensure_tables = lambda: None
    try:
        return engine_run(reader=FakeReader(corpus), resolver=FakeResolver())
    finally:
        db.ensure_tables = original


# --------------------------------------------------------------------------- #
# A full run
# --------------------------------------------------------------------------- #

def test_the_whole_corpus_imports(session, corpus):
    report = _run(corpus)
    assert report['entities']['organisation']['imported'] == 31
    assert report['entities']['project']['imported'] == 43
    assert report['entities']['platform']['imported'] == 4
    assert report['entities']['resource']['imported'] == 6
    assert report['entities']['event']['imported'] == 13
    assert report['entities']['post']['imported'] == 16
    assert report['entities']['category']['imported'] == 20

    assert session.query(db.C4wProject).count() == 43
    assert session.query(db.C4wOrganisation).count() == 31
    assert not report['skipped']


def test_no_term_lands_outside_its_vocabulary(session, corpus):
    """The check that would have caught the fifteen abbreviated slugs.

    A term production holds that constants.py does not declare is INVISIBLE on
    the site: a facet hides any option absent from its counts.
    """
    report = _run(corpus)
    assert not report['terms_outside_vocabulary'], \
        report['terms_outside_vocabulary']


def test_every_row_gets_a_unique_slug(session, corpus):
    _run(corpus)
    for model_cls in (db.C4wProject, db.C4wOrganisation, db.C4wResource,
                      db.C4wPlatform, db.C4wEvent, db.C4wPost):
        slugs = [row.slug for row in session.query(model_cls)]
        assert all(slugs), model_cls.__name__
        assert len(slugs) == len(set(slugs)), model_cls.__name__


def test_the_visible_twin_wins_the_unsuffixed_slug(session, corpus):
    """Six project names are duplicated in the corpus.

    Slugs are assigned approved-first, so the canonical URL belongs to the row
    a visitor can actually reach rather than to a hidden draft.
    """
    _run(corpus)
    suffixed = [row for row in session.query(db.C4wProject)
                if row.slug and row.slug[-2:-1] == '-' and row.slug[-1].isdigit()]
    for row in suffixed:
        base = row.slug.rsplit('-', 1)[0]
        twin = (session.query(db.C4wProject)
                .filter(db.C4wProject.slug == base).first())
        if twin is not None and row.approved != twin.approved:
            assert twin.approved, (twin.slug, row.slug)


# --------------------------------------------------------------------------- #
# Idempotency
# --------------------------------------------------------------------------- #

def test_a_second_run_changes_nothing(session, corpus):
    """--since exists to make a delta run cheap; it is worthless if a re-run
    duplicates rows or moves a URL."""
    _run(corpus)
    first = {row.legacy_id: (row.id, row.slug)
             for row in session.query(db.C4wProject)}
    links_before = session.query(db.C4wTermLink).count()
    relations_before = session.query(db.C4wRelation).count()

    _run(corpus)
    second = {row.legacy_id: (row.id, row.slug)
              for row in session.query(db.C4wProject)}

    assert first == second
    assert session.query(db.C4wProject).count() == 43
    assert session.query(db.C4wTermLink).count() == links_before
    assert session.query(db.C4wRelation).count() == relations_before


# --------------------------------------------------------------------------- #
# Links
# --------------------------------------------------------------------------- #

def test_projects_carry_their_vocabulary_terms(session, corpus):
    _run(corpus)
    vocabularies = {row.vocabulary for row in session.query(db.C4wTermLink)
                    .filter(db.C4wTermLink.entity_type == 'project')}
    assert {'topic', 'water_type', 'country', 'keyword'} <= vocabularies


def test_country_terms_are_two_letter_codes(session, corpus):
    _run(corpus)
    codes = {row.term for row in session.query(db.C4wTermLink)
             .filter(db.C4wTermLink.vocabulary == 'country')}
    assert codes
    assert all(len(code) == 2 and code.isupper() for code in codes)


def test_the_absent_country_coordinates_are_reported_not_invented(session,
                                                                  corpus):
    """Every projects_projectcountry row in production has a NULL latitude.

    The update_countries_coordinates management command was never run there.
    So country_points is legitimately empty, and the run says so rather than
    leaving a silent gap for whoever tries to draw the map.
    """
    report = _run(corpus)
    assert any('coordinates' in note for note in report['notes'])
    for row in session.query(db.C4wProject):
        assert 'country_points' not in db.load_extras(row.extras)


def test_a_projects_main_organisation_resolves_to_a_c4w_row(session, corpus):
    """The legacy foreign key is an integer; the column holds a c4w uuid."""
    _run(corpus)
    linked = [row for row in session.query(db.C4wProject)
              if row.main_organisation_id]
    assert linked
    org_ids = {row.id for row in session.query(db.C4wOrganisation)}
    for row in linked:
        assert row.main_organisation_id in org_ids


def test_resources_link_to_their_projects_and_organisations(session, corpus):
    _run(corpus)
    predicates = {row.predicate for row in session.query(db.C4wRelation)
                  .filter(db.C4wRelation.subject_type == 'resource')}
    assert 'organisation' in predicates


def test_an_unresolvable_editor_is_dropped_not_written_as_a_legacy_id(session,
                                                                     corpus):
    """A relation to a CKAN user that does not exist would be a dangling id."""
    _run(corpus)
    editors = list(session.query(db.C4wRelation)
                   .filter(db.C4wRelation.predicate == 'editor'))
    assert editors == []


def test_no_row_claims_an_author_the_portal_does_not_have(session, corpus):
    """The importer never creates CKAN accounts, so unresolved means NULL."""
    _run(corpus)
    for model_cls in (db.C4wProject, db.C4wOrganisation, db.C4wPlatform):
        assert all(row.created_by is None for row in session.query(model_cls))
    assert all(row.author_id is None for row in session.query(db.C4wPost))


# --------------------------------------------------------------------------- #
# Categories
# --------------------------------------------------------------------------- #

def test_the_category_tree_gets_its_paths(session, corpus):
    """`path` is what turns "everything under Text" into one indexed LIKE."""
    _run(corpus)
    children = [row for row in session.query(db.C4wCategory)
                if row.parent_id]
    assert children
    for row in children:
        assert row.depth == 1
        assert ' : ' in row.path


def test_resources_point_at_a_category_row(session, corpus):
    _run(corpus)
    linked = [row for row in session.query(db.C4wResource) if row.category_id]
    category_ids = {row.id for row in session.query(db.C4wCategory)}
    assert linked
    for row in linked:
        assert row.category_id in category_ids


# --------------------------------------------------------------------------- #
# Partial runs and media
# --------------------------------------------------------------------------- #

def test_only_imports_just_that_entity(session, corpus):
    _run(corpus, only=['organisation'])
    assert session.query(db.C4wOrganisation).count() == 31
    assert session.query(db.C4wProject).count() == 0


def test_a_missing_media_root_is_reported_not_fatal(session, corpus):
    """Some rows reference files that are simply gone.

    Aborting a migration over one of them would be the wrong trade.
    """
    report = _run(corpus)
    assert report['media']['uploaded'] == 0
    assert report['media']['missing']
    assert session.query(db.C4wProject).count() == 43

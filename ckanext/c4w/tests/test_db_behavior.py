# encoding: utf-8
"""ORM behaviour against a real SQLAlchemy engine.

No web stack and no Postgres: each test builds a FRESH in-memory SQLite
database, creates only the plugin's ``c4w_*`` tables on CKAN's shared metadata
and rebinds the module-level ``Session`` to it. That proves the classic
``Table`` + ``mapper`` wiring, the column defaults, the unique constraints and
the dictize helpers produce a working ORM -- not merely that the modules
import.

Skips cleanly where CKAN is absent, but it MUST run and pass inside the
ckan-dev container.
"""
import pytest

try:
    import sqlalchemy as sa
    from sqlalchemy.exc import IntegrityError
    import ckan  # noqa: F401
    from ckanext.c4w import db
    HAVE_CKAN = True
except Exception:  # pragma: no cover - environment without CKAN
    HAVE_CKAN = False

pytestmark = pytest.mark.skipif(
    not HAVE_CKAN, reason='requires CKAN (ckan.model + sqlalchemy)')


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


# --------------------------------------------------------------------------- #
# Schema
# --------------------------------------------------------------------------- #

def test_every_table_is_created(session):
    inspector = sa.inspect(session.get_bind())
    created = set(inspector.get_table_names())
    for table in db._ALL_TABLES:
        assert table.name in created, table.name


def test_every_declared_index_is_created(session):
    """create_all builds indexes only with the table it creates.

    _ensure_indexes exists because of that; this asserts the declarations it
    walks are real and buildable in the first place.
    """
    inspector = sa.inspect(session.get_bind())
    for table in db._ALL_TABLES:
        built = {i['name'] for i in inspector.get_indexes(table.name)}
        for index in table.indexes:
            assert index.name in built, '%s.%s' % (table.name, index.name)


def test_mapped_classes_round_trip(session):
    """Every mapped class inserts and reads back."""
    rows = [
        db.C4wProject(slug=u'p', name=u'P'),
        db.C4wOrganisation(slug=u'o', name=u'O'),
        db.C4wResource(slug=u'r', name=u'R'),
        db.C4wPlatform(slug=u'pl', name=u'PL'),
        db.C4wEvent(slug=u'e', title=u'E'),
        db.C4wPost(slug=u'b', title=u'B'),
        db.C4wCategory(text=u'Text'),
        db.C4wMediaMap(legacy_path=u'images/x.jpg'),
    ]
    for row in rows:
        session.add(row)
    session.commit()
    for row in rows:
        assert row.id, type(row).__name__


# --------------------------------------------------------------------------- #
# Column defaults
# --------------------------------------------------------------------------- #

def test_moderation_defaults_match_the_legacy_behaviour(session):
    """Projects arrive unapproved; organisations and platforms do not.

    Django never moderated organisations or platforms (``approved`` defaulted
    to True). Flipping that on import would hide the whole directory behind a
    review queue nobody knew existed.
    """
    project = db.C4wProject(slug=u'p', name=u'P')
    organisation = db.C4wOrganisation(slug=u'o', name=u'O')
    platform = db.C4wPlatform(slug=u'pl', name=u'PL')
    session.add_all([project, organisation, platform])
    session.commit()

    assert project.approved is False
    assert project.hidden is False
    assert project.total_accesses == 0
    assert organisation.approved is True
    assert platform.approved is True


def test_post_status_defaults_to_draft(session):
    post = db.C4wPost(slug=u'b', title=u'B')
    session.add(post)
    session.commit()
    assert post.status == u'draft'


# --------------------------------------------------------------------------- #
# Link tables
# --------------------------------------------------------------------------- #

def test_a_term_link_cannot_be_duplicated(session):
    """The constraint is what makes the importer's delete-and-reinsert safe."""
    for _ in range(2):
        session.add(db.C4wTermLink(
            entity_type=u'project', entity_id=u'x',
            vocabulary=u'topic', term=u'water'))
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()


def test_the_same_term_may_belong_to_two_vocabularies(session):
    """'other' is a legitimate member of most of them."""
    session.add(db.C4wTermLink(entity_type=u'project', entity_id=u'x',
                               vocabulary=u'topic', term=u'other'))
    session.add(db.C4wTermLink(entity_type=u'project', entity_id=u'x',
                               vocabulary=u'water_type', term=u'other'))
    session.commit()
    assert session.query(db.C4wTermLink).count() == 2


def test_a_relation_cannot_be_duplicated(session):
    for _ in range(2):
        session.add(db.C4wRelation(
            subject_type=u'project', subject_id=u'x', predicate=u'editor',
            object_type=u'user', object_id=u'u1'))
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()


# --------------------------------------------------------------------------- #
# Dictization
# --------------------------------------------------------------------------- #

def test_extras_are_flattened_to_the_top_level(session):
    """A template reads project.target_group without knowing where it lives."""
    project = db.C4wProject(
        slug=u'p', name=u'P',
        extras=db.dump_extras({'target_group': u'Schools', 'uses_ai': True}))
    session.add(project)
    session.commit()

    out = db.entity_dictize('project', project)
    assert out['target_group'] == u'Schools'
    assert out['uses_ai'] is True
    # The raw blob is not part of the contract.
    assert 'extras' not in out


def test_a_native_column_wins_over_a_same_named_extra(session):
    """The column is the schema; the blob is the overflow."""
    project = db.C4wProject(slug=u'p', name=u'Real',
                            extras=db.dump_extras({'name': u'Stale'}))
    session.add(project)
    session.commit()
    assert db.entity_dictize('project', project)['name'] == u'Real'


def test_malformed_extras_do_not_break_dictize(session):
    """One bad row must not take down a listing page."""
    project = db.C4wProject(slug=u'p', name=u'P', extras=u'{not json')
    session.add(project)
    session.commit()
    assert db.entity_dictize('project', project)['name'] == u'P'


def test_dictize_carries_terms_and_relations(session):
    project = db.C4wProject(slug=u'p', name=u'P')
    session.add(project)
    session.commit()
    session.add_all([
        db.C4wTermLink(entity_type=u'project', entity_id=project.id,
                       vocabulary=u'topic', term=u'water', sort_order=1),
        db.C4wTermLink(entity_type=u'project', entity_id=project.id,
                       vocabulary=u'topic', term=u'climate', sort_order=0),
        db.C4wRelation(subject_type=u'project', subject_id=project.id,
                       predicate=u'editor', object_type=u'user',
                       object_id=u'u1'),
    ])
    session.commit()

    out = db.entity_dictize('project', project)
    # sort_order, not alphabetical: it preserves the order the author typed.
    assert out['terms']['topic'] == [u'climate', u'water']
    assert out['relations']['editor'] == [u'u1']


def test_list_dictize_does_not_issue_a_query_per_row(session):
    """Terms for a whole page must cost one round trip, not one per card."""
    projects = []
    for i in range(5):
        project = db.C4wProject(slug=u'p%d' % i, name=u'P%d' % i)
        session.add(project)
        projects.append(project)
    session.commit()
    for project in projects:
        session.add(db.C4wTermLink(
            entity_type=u'project', entity_id=project.id,
            vocabulary=u'topic', term=u'water'))
    session.commit()

    engine = session.get_bind()
    statements = []

    def _record(conn, cursor, statement, *args):
        statements.append(statement)

    sa.event.listen(engine, 'before_cursor_execute', _record)
    try:
        out = db.list_dictize('project', projects)
    finally:
        sa.event.remove(engine, 'before_cursor_execute', _record)

    assert len(out) == 5
    assert all(o['terms']['topic'] == [u'water'] for o in out)
    selects = [s for s in statements if s.lstrip().upper().startswith('SELECT')]
    # One for the terms, one for the relations. Never one per row.
    assert len(selects) == 2, selects


def test_dictize_of_none_is_none():
    assert db.entity_dictize('project', None) is None


# --------------------------------------------------------------------------- #
# Slugs
# --------------------------------------------------------------------------- #

def test_unique_slug_suffixes_on_collision(session):
    session.add(db.C4wProject(slug=u'be-resilient', name=u'Be Resilient'))
    session.commit()
    assert db.unique_slug(db.C4wProject, u'Be Resilient') == u'be-resilient-2'


def test_unique_slug_ignores_the_row_being_edited(session):
    """Re-saving an entity must not renumber its own URL."""
    project = db.C4wProject(slug=u'be-resilient', name=u'Be Resilient')
    session.add(project)
    session.commit()
    assert db.unique_slug(db.C4wProject, u'Be Resilient',
                          exclude_id=project.id) == u'be-resilient'


def test_unique_slug_never_returns_empty(session):
    """A name of pure punctuation still needs a URL."""
    assert db.unique_slug(db.C4wProject, u'!!!') == u'item'


# --------------------------------------------------------------------------- #
# Bootstrap
# --------------------------------------------------------------------------- #

def test_ensure_indexes_is_idempotent(session):
    """It runs on every startup, so a second pass must be a no-op."""
    db._ensure_indexes(session.get_bind())
    db._ensure_indexes(session.get_bind())


def test_ensure_columns_is_a_noop_while_the_whitelist_is_empty(session):
    db._ensure_columns(session.get_bind())
    assert db._AUTO_HEAL_COLUMNS == []


# --------------------------------------------------------------------------- #
# Term labels
# --------------------------------------------------------------------------- #

def test_dictize_carries_both_the_slug_and_the_stored_label(session):
    """The label column was write-only until this was wired up.

    For a CLOSED vocabulary a template can resolve the slug through
    constants.py. For an OPEN one -- keyword, funding_body, author -- the label
    recorded at import time is the only record of the string the author typed,
    and without it the page shows a slug.
    """
    project = db.C4wProject(slug=u'p', name=u'P')
    session.add(project)
    session.commit()
    session.add(db.C4wTermLink(
        entity_type=u'project', entity_id=project.id, vocabulary=u'keyword',
        term=u'water-quality', label=u'Water Quality'))
    session.commit()

    out = db.entity_dictize('project', project)
    assert out['terms']['keyword'] == [u'water-quality']
    assert out['term_labels']['keyword'] == [
        {'term': u'water-quality', 'label': u'Water Quality'}]


def test_a_link_with_no_stored_label_falls_back_to_its_term(session):
    project = db.C4wProject(slug=u'p', name=u'P')
    session.add(project)
    session.commit()
    session.add(db.C4wTermLink(
        entity_type=u'project', entity_id=project.id,
        vocabulary=u'keyword', term=u'rivers'))
    session.commit()

    out = db.entity_dictize('project', project)
    assert out['term_labels']['keyword'] == [
        {'term': u'rivers', 'label': u'rivers'}]


def test_list_dictize_also_carries_labels(session):
    """A card renders chips too, so the listing path needs them as well."""
    project = db.C4wProject(slug=u'p', name=u'P')
    session.add(project)
    session.commit()
    session.add(db.C4wTermLink(
        entity_type=u'project', entity_id=project.id, vocabulary=u'topic',
        term=u'water', label=u'Water'))
    session.commit()

    out = db.list_dictize('project', [project])
    assert out[0]['term_labels']['topic'][0]['label'] == u'Water'


# --------------------------------------------------------------------------- #
# Contact addresses
# --------------------------------------------------------------------------- #

def test_an_address_is_absent_from_a_dictized_row_by_default(session):
    """The detail TEMPLATE hides these from a logged-out visitor, but the
    action behind it is reachable through CKAN's public API where no template
    runs. So the decision lives on the data and callers opt in.
    """
    project = db.C4wProject(slug=u'p', name=u'P',
                            author=u'A Person',
                            author_email=u'person@example.org')
    session.add(project)
    session.commit()

    public = db.entity_dictize('project', project)
    assert 'author_email' not in public
    assert public['author'] == u'A Person'      # the name is not the address

    private = db.entity_dictize('project', project, include_contact=True)
    assert private['author_email'] == u'person@example.org'


def test_a_listing_page_never_carries_addresses(session):
    """A page of 18 cards must not double as a mailing list."""
    for index in range(3):
        session.add(db.C4wOrganisation(
            slug=u'o%d' % index, name=u'O%d' % index,
            contact_point_email=u'o%d@example.org' % index))
    session.commit()

    rows = db.list_dictize('organisation',
                           session.query(db.C4wOrganisation).all())
    assert rows
    assert all('contact_point_email' not in row for row in rows)


def test_a_long_published_slug_is_preserved(session):
    """blog_post.slug is the address the site published; the longest is 146
    characters, and slugify's 90-char cap truncated it mid-word."""
    long_slug = u'-'.join([u'segment%d' % i for i in range(20)])
    assert len(long_slug) > 90
    assert db.unique_slug(db.C4wPost, long_slug) == long_slug


def test_a_non_slug_base_is_still_slugified(session):
    assert db.unique_slug(db.C4wPost, u'Some Title!') == u'some-title'

# encoding: utf-8
"""Behaviour of the listing engine against a real SQLAlchemy engine.

The facet semantics here are not arbitrary -- they are ported from the Django
original (src/projects/views.py:940): OR within a facet, AND across facets.
These tests pin that down, because getting it backwards produces a listing
that looks plausible and silently returns the wrong rows.
"""
import pytest

try:
    import sqlalchemy as sa
    import ckan  # noqa: F401
    from ckanext.c4w import db
    from ckanext.c4w.logic import query as q
    HAVE_CKAN = True
except Exception:  # pragma: no cover
    HAVE_CKAN = False

pytestmark = pytest.mark.skipif(
    not HAVE_CKAN, reason='requires CKAN (ckan.model + sqlalchemy)')


PROJECT_SPEC = None


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


@pytest.fixture
def spec():
    return q.ListingSpec(
        entity_type='project',
        model_cls=db.C4wProject,
        search_columns=('name', 'description'),
        native_filters={'status': 'status'},
        bool_filters={'featured': 'featured'},
        term_facets=('topic', 'water_type'),
        orderings={
            'modified': lambda m: [m.modified.desc()],
            'name': lambda m: [m.name.asc()],
            'accesses': lambda m: [m.total_accesses.desc()],
        },
        default_order='modified',
        page_size=2,
        defer_columns=('geom_geojson',),
    )


def _project(session, name, approved=True, hidden=False, status=u'active',
             topics=(), waters=(), accesses=0, featured=False,
             description=u''):
    project = db.C4wProject(
        slug=db.slugify(name), name=name, approved=approved, hidden=hidden,
        status=status, total_accesses=accesses, featured=featured,
        description=description)
    session.add(project)
    session.commit()
    for vocabulary, terms in (('topic', topics), ('water_type', waters)):
        for order, term in enumerate(terms):
            session.add(db.C4wTermLink(
                entity_type=u'project', entity_id=project.id,
                vocabulary=vocabulary, term=term, label=term,
                sort_order=order))
    session.commit()
    return project


def _names(result):
    return sorted(r['name'] for r in result['results'])


# --------------------------------------------------------------------------- #
# Visibility
# --------------------------------------------------------------------------- #

def test_unapproved_and_hidden_rows_never_reach_the_public_listing(session, spec):
    _project(session, u'Public')
    _project(session, u'Pending', approved=False)
    _project(session, u'Hidden', hidden=True)

    result = q.build_listing(spec, {})
    assert _names(result) == [u'Public']
    assert result['count'] == 1


def test_include_private_is_an_argument_not_a_request_parameter(session, spec):
    """It used to be read from data_dict, and every listing action is exposed
    through CKAN's public API -- which made ?include_private=true an anonymous
    read of every unapproved, hidden and draft row. A caller must not be able
    to grant itself this; only an action that has checked authorisation may.
    """
    _project(session, u'Public')
    _project(session, u'Pending', approved=False)

    # What a request can say is ignored...
    assert _names(q.build_listing(spec, {'include_private': True})) == [u'Public']
    assert _names(q.build_listing(spec, {'include_private': 'true'})) == [u'Public']
    # ...and only the caller's own argument lifts the filter.
    assert _names(q.build_listing(spec, {}, include_private=True)) == [
        u'Pending', u'Public']


# --------------------------------------------------------------------------- #
# Untrusted pagination input
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize('page', [u'abc', u'', u'-1', u'0', None, u'1e9'])
def test_a_junk_page_value_does_not_500(session, spec, page):
    """These arrive from a query string on a public page."""
    _project(session, u'A')
    result = q.build_listing(spec, {'page': page})
    assert result['page'] >= 1
    assert result['results']


@pytest.mark.parametrize('size', [u'0', u'-1', u'abc', u''])
def test_a_junk_page_size_does_not_500(session, spec, size):
    """page_size=0 was a ZeroDivisionError and -1 a database error."""
    _project(session, u'A')
    result = q.build_listing(spec, {'page_size': size})
    assert result['page_size'] >= 1
    assert result['pages'] >= 1


# --------------------------------------------------------------------------- #
# Facet semantics
# --------------------------------------------------------------------------- #

def test_two_values_in_one_facet_are_ORed(session, spec):
    _project(session, u'Water only', topics=[u'water'])
    _project(session, u'Climate only', topics=[u'climate'])
    _project(session, u'Ocean only', topics=[u'ocean'])

    result = q.build_listing(spec, {'topic': [u'water', u'climate']})
    assert _names(result) == [u'Climate only', u'Water only']


def test_two_different_facets_are_ANDed(session, spec):
    _project(session, u'Both', topics=[u'water'], waters=[u'groundwater'])
    _project(session, u'Topic only', topics=[u'water'])
    _project(session, u'Water only', waters=[u'groundwater'])

    result = q.build_listing(
        spec, {'topic': [u'water'], 'water_type': [u'groundwater']})
    assert _names(result) == [u'Both']


def test_a_native_filter_ANDs_with_a_term_facet(session, spec):
    _project(session, u'Match', status=u'active', topics=[u'water'])
    _project(session, u'Wrong status', status=u'completed', topics=[u'water'])

    result = q.build_listing(spec, {'status': [u'active'], 'topic': [u'water']})
    assert _names(result) == [u'Match']


def test_an_undeclared_facet_is_ignored_not_trusted(session, spec):
    """A facet name reaches a query, so it is validated against the spec."""
    _project(session, u'Only one', topics=[u'water'])
    result = q.build_listing(spec, {'not_a_facet': [u'anything']})
    assert _names(result) == [u'Only one']


def test_a_single_string_value_works_like_a_one_item_list(session, spec):
    """Flask hands over a bare string when a parameter appears once."""
    _project(session, u'Water', topics=[u'water'])
    _project(session, u'Climate', topics=[u'climate'])
    assert _names(q.build_listing(spec, {'topic': u'water'})) == [u'Water']


def test_empty_facet_values_do_not_filter(session, spec):
    """An untouched <select> posts an empty string; it must not match nothing."""
    _project(session, u'A', topics=[u'water'])
    assert _names(q.build_listing(spec, {'topic': u''})) == [u'A']
    assert _names(q.build_listing(spec, {'topic': []})) == [u'A']


# --------------------------------------------------------------------------- #
# Facet counts
# --------------------------------------------------------------------------- #

def test_facet_counts_reflect_the_visible_rows(session, spec):
    _project(session, u'A', topics=[u'water'])
    _project(session, u'B', topics=[u'water'])
    _project(session, u'C', topics=[u'climate'])
    _project(session, u'Hidden', topics=[u'water'], hidden=True)

    facets = q.build_listing(spec, {})['facets']
    assert facets['topic'] == {u'water': 2, u'climate': 1}


def test_a_facet_does_not_narrow_its_OWN_counts(session, spec):
    """Otherwise every sibling reads zero the moment one value is picked.

    The facet then becomes a dead end: the visitor cannot see that another
    value would have returned anything, and can only escape with the back
    button.
    """
    _project(session, u'A', topics=[u'water'])
    _project(session, u'B', topics=[u'climate'])

    facets = q.build_listing(spec, {'topic': [u'water']})['facets']
    assert facets['topic'] == {u'water': 1, u'climate': 1}


def test_a_facet_IS_narrowed_by_the_other_facets(session, spec):
    _project(session, u'A', topics=[u'water'], waters=[u'groundwater'])
    _project(session, u'B', topics=[u'climate'], waters=[u'surface-water'])

    facets = q.build_listing(spec, {'water_type': [u'groundwater']})['facets']
    assert facets['topic'] == {u'water': 1}


def test_a_native_facet_does_not_narrow_its_own_counts_either(session, spec):
    _project(session, u'A', status=u'active')
    _project(session, u'B', status=u'completed')

    facets = q.build_listing(spec, {'status': [u'active']})['facets']
    assert facets['status'] == {u'active': 1, u'completed': 1}


def test_a_bool_filter_narrows_every_count(session, spec):
    """It is not a counted facet, so it applies everywhere without exception."""
    _project(session, u'A', topics=[u'water'], featured=True)
    _project(session, u'B', topics=[u'water'], featured=False)

    facets = q.build_listing(spec, {'featured': u'1'})['facets']
    assert facets['topic'] == {u'water': 1}


def test_search_narrows_the_counts_too(session, spec):
    _project(session, u'Rivers of Spain', topics=[u'water'])
    _project(session, u'Glaciers', topics=[u'climate'])

    facets = q.build_listing(spec, {'q': u'river'})['facets']
    assert facets['topic'] == {u'water': 1}


# --------------------------------------------------------------------------- #
# Search
# --------------------------------------------------------------------------- #

def test_search_is_case_insensitive_and_spans_the_declared_columns(session, spec):
    _project(session, u'Watermonsters')
    _project(session, u'Other', description=u'about WATER quality')
    _project(session, u'Unrelated')

    assert _names(q.build_listing(spec, {'q': u'water'})) == [
        u'Other', u'Watermonsters']


def test_search_ignores_surrounding_whitespace(session, spec):
    _project(session, u'Watermonsters')
    assert _names(q.build_listing(spec, {'q': u'  water  '})) == [
        u'Watermonsters']


# --------------------------------------------------------------------------- #
# Ordering and pagination
# --------------------------------------------------------------------------- #

def test_each_declared_ordering_works(session, spec):
    _project(session, u'Beta', accesses=5)
    _project(session, u'Alpha', accesses=1)

    by_name = q.build_listing(spec, {'order': 'name'})
    assert [r['name'] for r in by_name['results']] == [u'Alpha', u'Beta']

    by_accesses = q.build_listing(spec, {'order': 'accesses'})
    assert [r['name'] for r in by_accesses['results']] == [u'Beta', u'Alpha']


def test_an_unknown_ordering_falls_back_to_the_default(session, spec):
    """The key indexes a dict of expressions; it is never interpolated."""
    _project(session, u'A')
    result = q.build_listing(spec, {'order': 'total_likes; DROP TABLE'})
    assert result['order'] == 'modified'


def test_pagination_reports_a_consistent_page_count(session, spec):
    for i in range(5):
        _project(session, u'P%d' % i)

    first = q.build_listing(spec, {})
    assert first['count'] == 5
    assert first['page_size'] == 2
    assert first['pages'] == 3
    assert len(first['results']) == 2

    last = q.build_listing(spec, {'page': 3})
    assert len(last['results']) == 1


def test_a_page_beyond_the_end_clamps_instead_of_returning_nothing(session, spec):
    """A stale bookmark should land on the last page, not an empty one."""
    _project(session, u'A')
    result = q.build_listing(spec, {'page': 99})
    assert result['page'] == 1
    assert len(result['results']) == 1


def test_page_size_is_capped(session, spec):
    """A listing never returns an unbounded page, whatever the URL asks for."""
    result = q.build_listing(spec, {'page_size': 100000})
    assert result['page_size'] == q.MAX_PAGE_SIZE


def test_an_empty_listing_is_coherent(session, spec):
    """Zero rows must still give a renderable page, not a division by zero."""
    result = q.build_listing(spec, {})
    assert result['count'] == 0
    assert result['pages'] == 1
    assert result['page'] == 1
    assert result['results'] == []


# --------------------------------------------------------------------------- #
# Dictization inside a listing
# --------------------------------------------------------------------------- #

def test_results_carry_their_terms(session, spec):
    _project(session, u'A', topics=[u'water', u'climate'])
    result = q.build_listing(spec, {})
    assert result['results'][0]['terms']['topic'] == [u'water', u'climate']


def test_posts_are_filtered_by_published_status(session):
    """C4wPost has no `approved` column; its visibility is the status."""
    session.add(db.C4wPost(slug=u'a', title=u'Draft', status=u'draft'))
    session.add(db.C4wPost(slug=u'b', title=u'Live', status=u'published'))
    session.commit()

    post_spec = q.ListingSpec(
        entity_type='post', model_cls=db.C4wPost,
        orderings={'created': lambda m: [m.created_on.desc()]},
        default_order='created')
    result = q.build_listing(post_spec, {})
    assert [r['title'] for r in result['results']] == [u'Live']

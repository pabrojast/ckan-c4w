# encoding: utf-8
"""The listing engine: filters, facet counts, ordering and pagination.

One builder serves every entity. Each listing declares a spec -- which columns
are searched, which are filtered natively, which vocabularies are faceted, how
it may be ordered -- and this module turns a request's query string into
``(count, rows, facet_counts)``.

Facet semantics, ported from the Django original (src/projects/views.py:940):
**OR within a facet, AND across facets.** Picking Water and Climate under
Topic widens; adding a Status narrows. Django expressed this as
``filter(water_type__name__in=[...])`` per facet, chained -- the chain is the
AND, the ``__in`` is the OR.

Why the counts need a subquery rather than a second pass: a facet's counts are
computed over the rows that survive *every other* filter, so the query that
produces them has to reference the filtered id set. Doing that in Python means
loading the whole corpus on every request, which is what forced
ckanext-csunesco to cap its scan at 1000 rows and give up on counts entirely.

SAFETY: no user-supplied string ever reaches SQL as an identifier. Ordering
keys index into a dict of pre-built expressions; facet names are checked
against the spec; filter values are always bound parameters.
"""
import logging

import sqlalchemy as sa
from sqlalchemy import func, orm

from ckan.model.meta import Session

from ckanext.c4w import db

log = logging.getLogger(__name__)

# A listing never returns an unbounded page, whatever the request asks for.
MAX_PAGE_SIZE = 100


class ListingSpec(object):
    """Everything a listing needs to know about one entity.

    Declared once per entity next to its actions, so the filters a URL accepts
    and the facets a page renders come from the same place and cannot drift.
    """

    def __init__(self, entity_type, model_cls, search_columns=(),
                 native_filters=None, bool_filters=None, term_facets=(),
                 orderings=None, default_order=None, page_size=18,
                 defer_columns=()):
        self.entity_type = entity_type
        self.model_cls = model_cls
        self.search_columns = tuple(search_columns)
        # Query parameter -> column name, for exact-match scalar filters.
        self.native_filters = dict(native_filters or {})
        self.bool_filters = dict(bool_filters or {})
        self.term_facets = tuple(term_facets)
        # Ordering key -> callable(model_cls) -> list of SQLAlchemy expressions.
        self.orderings = dict(orderings or {})
        self.default_order = default_order
        self.page_size = page_size
        # Columns too large to load in a listing (e.g. stored GeoJSON).
        self.defer_columns = tuple(defer_columns)


def _public_filter(query, model_cls):
    """Restrict to what an anonymous visitor may see.

    Applied to the DATA rather than expressed in the auth function: the auth
    layer runs before the row is loaded, so it cannot say "yes, but only the
    approved ones".
    """
    if hasattr(model_cls, 'approved'):
        query = query.filter(model_cls.approved.is_(True))
    if hasattr(model_cls, 'hidden'):
        query = query.filter(model_cls.hidden.isnot(True))
    if hasattr(model_cls, 'status') and model_cls is db.C4wPost:
        query = query.filter(model_cls.status == u'published')
    return query


def _as_list(value):
    """Normalise a query-string value that may arrive once or many times."""
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        return [v for v in value if v not in (None, u'', '')]
    if value in (u'', ''):
        return []
    return [value]


def _apply_search(query, model_cls, spec, text):
    """Case-insensitive OR across the spec's search columns."""
    if not text or not spec.search_columns:
        return query
    pattern = u'%%%s%%' % text.strip()
    clauses = [
        getattr(model_cls, name).ilike(pattern)
        for name in spec.search_columns
        if hasattr(model_cls, name)
    ]
    return query.filter(sa.or_(*clauses)) if clauses else query


def _apply_native_filters(query, model_cls, spec, data_dict):
    for param, column_name in spec.native_filters.items():
        values = _as_list(data_dict.get(param))
        if not values:
            continue
        column = getattr(model_cls, column_name, None)
        if column is None:
            continue
        # OR within the facet, exactly as Django's __in did.
        query = query.filter(column.in_(values))
    return _apply_bool_filters(query, model_cls, spec, data_dict)


def _apply_bool_filters(query, model_cls, spec, data_dict):
    for param, column_name in spec.bool_filters.items():
        raw = data_dict.get(param)
        if raw in (None, u'', ''):
            continue
        column = getattr(model_cls, column_name, None)
        if column is None:
            continue
        wanted = u'%s' % raw in (u'1', u'true', u'True', u'yes', u'on')
        query = query.filter(
            column.is_(True) if wanted else column.isnot(True))
    return query


def _term_subquery(entity_type, vocabulary, terms):
    """Ids of entities carrying ANY of ``terms`` in ``vocabulary``."""
    return (
        Session.query(db.C4wTermLink.entity_id)
        .filter(db.C4wTermLink.entity_type == entity_type)
        .filter(db.C4wTermLink.vocabulary == vocabulary)
        .filter(db.C4wTermLink.term.in_(terms))
        .scalar_subquery()
    )


def _apply_term_facets(query, model_cls, spec, selected):
    """One IN-subquery per selected facet: OR inside, AND between."""
    for vocabulary, terms in selected.items():
        query = query.filter(model_cls.id.in_(
            _term_subquery(spec.entity_type, vocabulary, terms)))
    return query


def _selected_facets(spec, data_dict):
    """Facet values from the request, restricted to declared vocabularies.

    An undeclared name is ignored rather than trusted -- the facet name reaches
    a query, so it is validated against the spec and never taken as given.
    """
    selected = {}
    for vocabulary in spec.term_facets:
        values = _as_list(data_dict.get(vocabulary))
        if values:
            selected[vocabulary] = values
    return selected


def _id_subquery(query, model_cls):
    """Project a Query over the mapped class down to its id column.

    ``IN (subquery)`` accepts exactly one column, and ``Session.query(Model)``
    selects them all -- so the projection is required, not stylistic.
    """
    return query.with_entities(model_cls.id).scalar_subquery()


def _count_base(spec, base_query, data_dict, selected, exclude):
    """Rows a facet should count over: every filter EXCEPT its own.

    A facet that narrows its own counts makes every sibling value read zero the
    moment one is picked, turning the facet into a dead end the visitor can
    only escape with the back button. ``exclude`` names the facet being
    counted -- it is the one filter left off.
    """
    query = base_query
    # Boolean filters are never faceted with counts of their own, so they are
    # always applied -- leaving them out would inflate every other count
    # whenever one is active.
    query = _apply_bool_filters(query, spec.model_cls, spec, data_dict)
    for param, column_name in spec.native_filters.items():
        if param == exclude:
            continue
        values = _as_list(data_dict.get(param))
        if values:
            query = query.filter(
                getattr(spec.model_cls, column_name).in_(values))
    for vocabulary, terms in selected.items():
        if vocabulary == exclude:
            continue
        query = query.filter(spec.model_cls.id.in_(
            _term_subquery(spec.entity_type, vocabulary, terms)))
    return query


def _facet_counts(spec, base_query, data_dict, selected):
    """Per-term counts for every vocabulary facet."""
    counts = {}
    for vocabulary in spec.term_facets:
        query = _count_base(spec, base_query, data_dict, selected, vocabulary)
        rows = (
            Session.query(db.C4wTermLink.term,
                          func.count(func.distinct(db.C4wTermLink.entity_id)))
            .filter(db.C4wTermLink.entity_type == spec.entity_type)
            .filter(db.C4wTermLink.vocabulary == vocabulary)
            .filter(db.C4wTermLink.entity_id.in_(
                _id_subquery(query, spec.model_cls)))
            .group_by(db.C4wTermLink.term)
            .all()
        )
        counts[vocabulary] = {term: count for term, count in rows}
    return counts


def _native_facet_counts(spec, base_query, data_dict, selected):
    """The same, for facets that live in a native column rather than a link."""
    counts = {}
    for param, column_name in spec.native_filters.items():
        column = getattr(spec.model_cls, column_name, None)
        if column is None:
            continue
        query = _count_base(spec, base_query, data_dict, selected, param)
        rows = (
            query.with_entities(column, func.count(spec.model_cls.id))
            .group_by(column).all()
        )
        counts[param] = {value: count for value, count in rows if value}
    return counts


def build_listing(spec, data_dict):
    """Run a listing.

    Returns ``{'count', 'results', 'facets', 'page', 'pages', 'page_size',
    'order', 'selected'}`` -- ``results`` already dictized.
    """
    db.ensure_mappers()
    model_cls = spec.model_cls

    # The base carries only visibility and free-text search -- NO facet
    # filters. Both the row query and the counts derive from it, which is what
    # lets each facet leave its own selection out of its own counts.
    base = Session.query(model_cls)
    if not data_dict.get('include_private'):
        base = _public_filter(base, model_cls)
    base = _apply_search(base, model_cls, spec, data_dict.get('q'))

    selected = _selected_facets(spec, data_dict)

    query = _apply_native_filters(base, model_cls, spec, data_dict)
    query = _apply_term_facets(query, model_cls, spec, selected)

    count = query.with_entities(func.count(model_cls.id)).scalar() or 0

    facets = {}
    try:
        facets.update(_facet_counts(spec, base, data_dict, selected))
        facets.update(_native_facet_counts(spec, base, data_dict, selected))
    except Exception:
        # A listing that renders without its counts is far better than a 500.
        log.error('ckanext-c4w: could not compute facet counts')

    order = data_dict.get('order') or spec.default_order
    if order not in spec.orderings:
        order = spec.default_order
    if order:
        query = query.order_by(*spec.orderings[order](model_cls))

    page_size = min(int(data_dict.get('page_size') or spec.page_size),
                    MAX_PAGE_SIZE)
    page = max(1, int(data_dict.get('page') or 1))
    pages = max(1, -(-count // page_size))     # ceiling division
    page = min(page, pages)

    for column_name in spec.defer_columns:
        query = query.options(orm.defer(getattr(model_cls, column_name)))

    rows = query.offset((page - 1) * page_size).limit(page_size).all()

    return {
        'count': count,
        'results': db.list_dictize(spec.entity_type, rows),
        'facets': facets,
        'selected': selected,
        'order': order,
        'page': page,
        'pages': pages,
        'page_size': page_size,
    }

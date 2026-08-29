# encoding: utf-8
"""Shared machinery for the read surfaces.

Six entities differ in their fields and facets but not in their shape: fetch
one by slug, list many with facets, describe the facets. Writing that out six
times would mean six places to fix the next visibility bug, so the behaviour
lives here once and each entity module contributes only what is actually
different -- its listing spec and its detail enrichment.

The actions are still registered under explicit, greppable names
(``c4w_organisation_show`` and so on); only their bodies are shared.
"""
import ckan.plugins.toolkit as tk

from ckanext.c4w import constants, db


def get_by_reference(model_cls, reference):
    """Fetch by slug, falling back to the legacy Django id, then the uuid.

    The slug is the canonical address. The legacy id is resolved too so an API
    caller holding an old integer still works, matching the 301 the web view
    serves for the same URL.
    """
    db.ensure_mappers()
    from ckan.model.meta import Session

    if not reference:
        return None
    row = (Session.query(model_cls)
           .filter(model_cls.slug == reference).first())
    if row is not None:
        return row
    try:
        legacy_id = int(reference)
    except (TypeError, ValueError):
        pass
    else:
        row = (Session.query(model_cls)
               .filter(model_cls.legacy_id == legacy_id).first())
        if row is not None:
            return row
    return Session.query(model_cls).filter(model_cls.id == reference).first()


def is_visible(entity_type, row, context):
    """Whether the requester may see a row the public listing would hide.

    Kept in one place because the answer has to match ``_public_filter`` in
    logic/query.py exactly -- a detail page that shows what its own listing
    filters out is how unapproved content leaks.
    """
    published = True
    if hasattr(row, 'approved'):
        published = bool(row.approved)
    if hasattr(row, 'hidden') and row.hidden:
        published = False
    if hasattr(row, 'status') and entity_type == 'post':
        published = row.status == constants.POST_STATUS_PUBLISHED
    if published:
        return True

    user = context.get('auth_user_obj')
    if user is None:
        return False
    if getattr(user, 'sysadmin', False):
        return True
    if getattr(row, 'created_by', None) and row.created_by == user.id:
        return True
    if getattr(row, 'author_id', None) and row.author_id == user.id:
        return True

    from ckan.model.meta import Session
    return Session.query(db.C4wRelation.id).filter(
        db.C4wRelation.subject_type == entity_type,
        db.C4wRelation.subject_id == row.id,
        db.C4wRelation.predicate == u'editor',
        db.C4wRelation.object_type == u'user',
        db.C4wRelation.object_id == user.id,
    ).first() is not None


def make_show(entity_type, model_cls, action_name, enrich=None):
    """Build the ``c4w_<entity>_show`` action.

    A row the requester may not see raises NotFound, NOT NotAuthorized -- a
    403, or a redirect to the login page, tells an anonymous visitor that the
    thing exists.
    """
    @tk.side_effect_free
    def show(context, data_dict):
        tk.check_access(action_name, context, data_dict)
        row = get_by_reference(
            model_cls, data_dict.get('id') or data_dict.get('slug'))
        if row is None or not is_visible(entity_type, row, context):
            raise tk.ObjectNotFound(tk._('Not found'))
        out = db.entity_dictize(entity_type, row)
        return enrich(out) if enrich else out

    show.__name__ = str(action_name)
    return show


def make_list(spec_factory, action_name):
    """Build the ``c4w_<entity>_list`` action."""
    from ckanext.c4w.logic import query as q

    @tk.side_effect_free
    def listing(context, data_dict):
        tk.check_access(action_name, context, data_dict)
        return q.build_listing(spec_factory(), data_dict or {})

    listing.__name__ = str(action_name)
    return listing


def make_facets(spec_factory, action_name, orderings=None):
    """Build the ``c4w_<entity>_facets`` action.

    Returns the facet definitions a sidebar renders, so the template never
    hard-codes a vocabulary list that could drift from the one the listing
    actually filters on.
    """
    @tk.side_effect_free
    def facets(context, data_dict):
        tk.check_access(action_name, context, data_dict)
        spec = spec_factory()
        out = []
        for vocabulary in spec.term_facets:
            out.append({
                'name': vocabulary,
                'kind': 'term',
                'options': [{'term': t, 'label': l} for t, l in
                            (constants.VOCABULARIES.get(vocabulary) or ())],
            })
        for param in spec.native_filters:
            # A vocabulary can be registered either way round: it is in
            # COLUMN_VOCABULARIES when it is single-valued everywhere, and in
            # VOCABULARIES when some other entity uses it many-valued.
            # geographic_extent is exactly that -- many-valued on a project,
            # single-valued on a platform -- and looking in only one map
            # silently returned an empty option list for the platform facet.
            pairs = (constants.COLUMN_VOCABULARIES.get(param)
                     or constants.VOCABULARIES.get(param) or ())
            out.append({
                'name': param,
                'kind': 'column',
                'options': [{'term': t, 'label': l} for t, l in pairs],
            })
        return {
            'facets': out,
            'orderings': [{'value': v, 'label': l}
                          for v, l in (orderings
                                       or constants.DEFAULT_ORDERINGS)],
        }

    facets.__name__ = str(action_name)
    return facets


def public_read_auth(*names):
    """Anonymous-readable auth functions for a set of action names.

    Every read on this portal is public; what a visitor may SEE is decided in
    the action, on the data. Auth runs before the row is loaded, so it could
    not express "yes, but only the approved ones" even if we wanted it to.
    """
    @tk.auth_allow_anonymous_access
    def allow(context, data_dict):
        return {'success': True}

    return {name: allow for name in names}

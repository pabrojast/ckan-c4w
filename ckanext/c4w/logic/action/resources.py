# encoding: utf-8
"""Resource library read actions.

Two public surfaces share one table: /resources and /training_resources. They
are separate sections of the site rather than one list with a filter, so the
split is a forced predicate on ``is_training_resource`` rather than something
a visitor can toggle.

NOTE ON THE DATA: production currently holds SIX resources and ZERO training
resources, and both lookup tables behind the training-only vocabularies
(education level, learning resource type) are empty. The training surface is
therefore live but empty after the migration -- that is faithful to
production, not a bug in the import.
"""
import ckan.plugins.toolkit as tk

from ckanext.c4w import constants, db
from ckanext.c4w.logic import query as q
from ckanext.c4w.logic.action import _common


def _spec(training):
    db.ensure_mappers()
    facets = ['theme', 'audience']
    if training:
        # Only meaningful on the training surface, and only once the lookup
        # tables stop being empty.
        facets += ['education_level', 'learning_resource_type']
    return q.ListingSpec(
        entity_type='resource',
        model_cls=db.C4wResource,
        search_columns=('name', 'abstract', 'publisher'),
        native_filters={
            'in_language': 'in_language',
            'category': 'category_id',
        },
        bool_filters={'training': 'is_training_resource'},
        term_facets=tuple(facets),
        orderings={
            'modified': lambda m: [m.modified.desc().nullslast(), m.name.asc()],
            'created': lambda m: [m.created.desc().nullslast(), m.name.asc()],
            'name': lambda m: [m.name.asc()],
            'featured': lambda m: [m.featured.desc(),
                                   m.modified.desc().nullslast()],
        },
        default_order='modified',
        page_size=constants.PAGE_SIZE,
    )


def spec():
    return _spec(training=False)


def training_spec():
    return _spec(training=True)


def _enrich(out):
    """Attach the category path and the linked organisations and projects."""
    from ckan.model.meta import Session

    out['category'] = None
    if out.get('category_id'):
        row = (Session.query(db.C4wCategory)
               .filter(db.C4wCategory.id == out['category_id']).first())
        if row is not None:
            out['category'] = {'id': row.id, 'text': row.text,
                               'path': row.path, 'depth': row.depth}

    relations = out.get('relations', {})
    for key, model_cls, entity_type in (
            ('organisations', db.C4wOrganisation, 'organisation'),
            ('projects', db.C4wProject, 'project')):
        ids = relations.get(entity_type, [])
        out[key] = []
        if ids:
            rows = (Session.query(model_cls)
                    .filter(model_cls.id.in_(ids))
                    .order_by(model_cls.name.asc()).all())
            out[key] = db.list_dictize(entity_type, rows)
    return out


def _listing(training, action_name):
    @tk.side_effect_free
    def listing(context, data_dict):
        tk.check_access(action_name, context, data_dict)
        data_dict = dict(data_dict or {})
        # Forced, not user-supplied: the two surfaces are separate sections.
        data_dict['training'] = u'1' if training else u'0'
        return q.build_listing(
            training_spec() if training else spec(), data_dict,
            include_private=_common.is_sysadmin(context))

    listing.__name__ = str(action_name)
    return listing


@tk.side_effect_free
def c4w_category_tree(context, data_dict):
    """The DCMI category tree, parents with their children.

    Feeds the two-level dependent select the resource filter uses; returned as
    a tree rather than a flat list so the template never has to reconstruct
    the parent/child relationship itself.
    """
    tk.check_access('c4w_category_tree', context, data_dict)
    from ckan.model.meta import Session

    db.ensure_mappers()
    rows = (Session.query(db.C4wCategory)
            .order_by(db.C4wCategory.sort_order, db.C4wCategory.text).all())
    by_id = {row.id: {'id': row.id, 'text': row.text, 'path': row.path,
                      'children': []} for row in rows}
    roots = []
    for row in rows:
        node = by_id[row.id]
        parent = by_id.get(row.parent_id) if row.parent_id else None
        (parent['children'] if parent else roots).append(node)
    return {'categories': roots}


c4w_resource_show = _common.make_show(
    'resource', db.C4wResource, 'c4w_resource_show', enrich=_enrich)
c4w_resource_list = _listing(False, 'c4w_resource_list')
c4w_training_resource_list = _listing(True, 'c4w_training_resource_list')
c4w_resource_facets = _common.make_facets(spec, 'c4w_resource_facets')


def get_actions():
    return {
        'c4w_resource_show': c4w_resource_show,
        'c4w_resource_list': c4w_resource_list,
        'c4w_training_resource_list': c4w_training_resource_list,
        'c4w_resource_facets': c4w_resource_facets,
        'c4w_category_tree': c4w_category_tree,
    }


def get_auth_functions():
    return _common.public_read_auth(
        'c4w_resource_show', 'c4w_resource_list',
        'c4w_training_resource_list', 'c4w_resource_facets',
        'c4w_category_tree')

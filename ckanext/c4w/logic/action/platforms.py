# encoding: utf-8
"""Platform and repository directory read actions.

A "platform" here is a citizen-science platform or data repository --
SciStarter, DataStream, the European Citizen Science platform -- rather than a
project. Four of them exist in production.
"""
import ckan.plugins.toolkit as tk

from ckanext.c4w import constants, db
from ckanext.c4w.logic import query as q
from ckanext.c4w.logic.action import _common


def spec():
    db.ensure_mappers()
    return q.ListingSpec(
        entity_type='platform',
        model_cls=db.C4wPlatform,
        search_columns=('name', 'description'),
        native_filters={'geographic_extent': 'geographic_extent'},
        # A platform can span several countries (django_countries multiple),
        # so unlike an organisation its countries DO need link rows.
        term_facets=('country',),
        orderings={
            'modified': lambda m: [m.modified.desc().nullslast(), m.name.asc()],
            'created': lambda m: [m.created.desc().nullslast(), m.name.asc()],
            'name': lambda m: [m.name.asc()],
        },
        default_order='modified',
        page_size=constants.PAGE_SIZE,
    )


def _enrich(out):
    from ckan.model.meta import Session

    ids = out.get('relations', {}).get('organisation', [])
    out['organisations'] = []
    if ids:
        rows = (Session.query(db.C4wOrganisation)
                .filter(db.C4wOrganisation.id.in_(ids))
                .order_by(db.C4wOrganisation.name.asc()).all())
        out['organisations'] = db.list_dictize(
            'organisation', _common.public_only('organisation', rows))
    return out


c4w_platform_show = _common.make_show(
    'platform', db.C4wPlatform, 'c4w_platform_show', enrich=_enrich)
c4w_platform_list = _common.make_list(spec, 'c4w_platform_list')
c4w_platform_facets = _common.make_facets(spec, 'c4w_platform_facets')


def get_actions():
    return {
        'c4w_platform_show': c4w_platform_show,
        'c4w_platform_list': c4w_platform_list,
        'c4w_platform_facets': c4w_platform_facets,
    }


def get_auth_functions():
    return _common.public_read_auth(
        'c4w_platform_show', 'c4w_platform_list', 'c4w_platform_facets')

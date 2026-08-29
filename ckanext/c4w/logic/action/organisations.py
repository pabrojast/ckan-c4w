# encoding: utf-8
"""Organisation directory read actions.

The directory holds third-party organisations working on citizen science and
water -- most of them do not publish datasets on the portal, which is why they
live in their own table rather than becoming CKAN organizations. The optional
``ckan_org_id`` links the ones that DO exist on the portal, and is resolved
here rather than in a template so a deleted CKAN organization degrades to
nothing instead of raising.
"""
import logging

import sqlalchemy as sa

import ckan.plugins.toolkit as tk

from ckanext.c4w import constants, db
from ckanext.c4w.logic import query as q
from ckanext.c4w.logic.action import _common

log = logging.getLogger(__name__)


def spec():
    db.ensure_mappers()
    return q.ListingSpec(
        entity_type='organisation',
        model_cls=db.C4wOrganisation,
        search_columns=('name', 'description'),
        native_filters={'org_type': 'org_type', 'country': 'country'},
        # Countries live in a native column here, not in c4w_term_link: an
        # organisation has exactly one, so a link row would buy nothing.
        term_facets=(),
        orderings={
            'modified': lambda m: [m.modified.desc().nullslast(), m.name.asc()],
            'created': lambda m: [m.created.desc().nullslast(), m.name.asc()],
            'name': lambda m: [m.name.asc()],
        },
        default_order='modified',
        page_size=constants.PAGE_SIZE,
    )


def _enrich(out):
    """Attach the linked CKAN organization and this organisation's projects."""
    from ckan.model.meta import Session

    out['ckan_organization'] = None
    if out.get('ckan_org_id'):
        try:
            out['ckan_organization'] = tk.get_action('organization_show')(
                {'ignore_auth': True}, {'id': out['ckan_org_id']})
        except Exception:
            # The link is not a foreign key precisely so a deleted CKAN
            # organization cannot break this row. Degrade, do not raise.
            log.debug('ckanext-c4w: linked CKAN organization is gone',
                      exc_info=True)

    # Projects coordinated by, or partnered with, this organisation.
    partner_ids = [
        r.subject_id for r in Session.query(db.C4wRelation)
        .filter(db.C4wRelation.subject_type == u'project',
                db.C4wRelation.predicate == u'organisation',
                db.C4wRelation.object_type == u'organisation',
                db.C4wRelation.object_id == out['id']).all()
    ]
    query = Session.query(db.C4wProject).filter(
        db.C4wProject.approved.is_(True),
        db.C4wProject.hidden.isnot(True))
    if partner_ids:
        query = query.filter(sa.or_(
            db.C4wProject.main_organisation_id == out['id'],
            db.C4wProject.id.in_(partner_ids)))
    else:
        query = query.filter(db.C4wProject.main_organisation_id == out['id'])
    out['projects'] = db.list_dictize(
        'project', query.order_by(db.C4wProject.name.asc()).all())
    return out


c4w_organisation_show = _common.make_show(
    'organisation', db.C4wOrganisation, 'c4w_organisation_show', enrich=_enrich)
c4w_organisation_list = _common.make_list(spec, 'c4w_organisation_list')
c4w_organisation_facets = _common.make_facets(spec, 'c4w_organisation_facets')


def get_actions():
    return {
        'c4w_organisation_show': c4w_organisation_show,
        'c4w_organisation_list': c4w_organisation_list,
        'c4w_organisation_facets': c4w_organisation_facets,
    }


def get_auth_functions():
    return _common.public_read_auth(
        'c4w_organisation_show',
        'c4w_organisation_list',
        'c4w_organisation_facets',
    )

# encoding: utf-8
"""Event listing read actions.

Events read chronologically rather than by relevance: upcoming first, then
past in reverse. The Django site offered no filters at all here, so this
listing keeps a search box and an ordering and nothing else.
"""
import datetime

import ckan.plugins.toolkit as tk

from ckanext.c4w import constants, db
from ckanext.c4w.logic import query as q
from ckanext.c4w.logic.action import _common


def spec():
    db.ensure_mappers()
    return q.ListingSpec(
        entity_type='event',
        model_cls=db.C4wEvent,
        search_columns=('title', 'description', 'place'),
        native_filters={'event_type': 'event_type'},
        term_facets=(),
        orderings={
            # Soonest first is the useful default for a list people scan to
            # decide what to attend.
            'start': lambda m: [m.start_date.asc().nullslast()],
            'start_desc': lambda m: [m.start_date.desc().nullslast()],
            'title': lambda m: [m.title.asc()],
        },
        default_order='start',
        page_size=constants.PAGE_SIZE_CHRONOLOGICAL,
    )


def _enrich(out):
    from ckan.model.meta import Session

    out['project'] = None
    if out.get('project_id'):
        row = (Session.query(db.C4wProject)
               .filter(db.C4wProject.id == out['project_id']).first())
        out['project'] = db.entity_dictize('project', row) if row else None

    out['main_organisation'] = None
    if out.get('main_organisation_id'):
        row = (Session.query(db.C4wOrganisation)
               .filter(db.C4wOrganisation.id == out['main_organisation_id'])
               .first())
        out['main_organisation'] = (
            db.entity_dictize('organisation', row) if row else None)
    return out


@tk.side_effect_free
def c4w_event_list(context, data_dict):
    """The events listing, split into upcoming and past.

    The split is done here rather than in the template because "upcoming"
    depends on the clock, and a template that computed it would recompute it
    per card and could disagree with itself across a midnight boundary.
    """
    tk.check_access('c4w_event_list', context, data_dict)
    data_dict = dict(data_dict or {})

    listing = q.build_listing(spec(), data_dict,
                              include_private=_common.is_sysadmin(context))
    now = datetime.datetime.utcnow().isoformat()
    upcoming, past = [], []
    for event in listing['results']:
        end = event.get('end_date') or event.get('start_date') or ''
        (upcoming if end >= now else past).append(event)
    listing['upcoming'] = upcoming
    listing['past'] = past
    return listing


c4w_event_show = _common.make_show(
    'event', db.C4wEvent, 'c4w_event_show', enrich=_enrich)
c4w_event_facets = _common.make_facets(spec, 'c4w_event_facets')


def get_actions():
    return {
        'c4w_event_show': c4w_event_show,
        'c4w_event_list': c4w_event_list,
        'c4w_event_facets': c4w_event_facets,
    }


def get_auth_functions():
    return _common.public_read_auth(
        'c4w_event_show', 'c4w_event_list', 'c4w_event_facets')

# encoding: utf-8
"""Event listing read actions.

Events read chronologically rather than by relevance: upcoming first, then
past in reverse. The Django site offered no filters at all here, so this
listing keeps a search box and an ordering and nothing else.
"""
import datetime
import sqlalchemy as sa

import ckan.plugins.toolkit as tk

from ckanext.c4w import constants, db
from ckanext.c4w.logic import query as q
from ckanext.c4w.logic import event_dates
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
        # Filtered: resolving by id bypasses the listing's visibility rule,
        # and this page is public.
        rows = _common.public_only('project', [row] if row else [])
        out['project'] = db.entity_dictize('project', rows[0]) if rows else None

    out['main_organisation'] = None
    if out.get('main_organisation_id'):
        row = (Session.query(db.C4wOrganisation)
               .filter(db.C4wOrganisation.id == out['main_organisation_id'])
               .first())
        rows = _common.public_only('organisation', [row] if row else [])
        out['main_organisation'] = (
            db.entity_dictize('organisation', rows[0]) if rows else None)
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

    period = data_dict.get('period') or 'all'
    if period not in ('all', 'upcoming', 'past'):
        raise tk.ValidationError({'period': [tk._('Choose all, upcoming or past events.')]})
    from ckan.model.meta import Session
    listing_spec = spec()
    model = db.C4wEvent
    # Read only the calendar fields, not descriptions, before pagination.
    query = Session.query(model.id, model.start_date, model.end_date, model.timezone)
    query = q._public_filter(query, model)
    query = q._apply_search(query, model, listing_spec, data_dict.get('q'))
    query = q._apply_native_filters(query, model, listing_spec, data_dict)
    now = datetime.datetime.now(datetime.timezone.utc)
    statuses = {row.id: event_dates.status(row, now) for row in query.all()}
    upcoming_ids = [id_ for id_, status in statuses.items() if status in ('upcoming', 'ongoing')]
    past_ids = [id_ for id_, status in statuses.items() if status == 'past']
    filters = () if period == 'all' else (model.id.in_(upcoming_ids if period == 'upcoming' else past_ids),)
    # Keep current events first on the complete calendar, followed by the
    # most recent history. A future event cannot disappear behind old pages.
    listing_spec.orderings['start'] = lambda m: [
        sa.case((m.id.in_(upcoming_ids), 0), (m.id.in_(past_ids), 1), else_=2),
        sa.case((m.id.in_(upcoming_ids), m.start_date), else_=None).asc(),
        m.start_date.desc().nullslast(), m.id.asc(),
    ]
    listing = q.build_listing(listing_spec, data_dict, include_private=False, filters=filters)
    for event in listing['results']:
        event['calendar_status'] = statuses[event['id']]
    listing['upcoming'] = [e for e in listing['results'] if e['calendar_status'] in ('upcoming', 'ongoing')]
    listing['past'] = [e for e in listing['results'] if e['calendar_status'] == 'past']
    listing['undated'] = [e for e in listing['results'] if e['calendar_status'] == 'undated']
    listing['upcoming_total'] = len(upcoming_ids)
    listing['past_total'] = len(past_ids)
    listing['period'] = period
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

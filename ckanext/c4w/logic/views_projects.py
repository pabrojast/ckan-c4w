# encoding: utf-8
"""Views for the project catalogue.

Orchestration only: read the request, call an action, render a template. No
ORM access here -- that is what keeps authorisation and visibility filtering
in one place instead of two.
"""
import json

from flask import Response, redirect, request

import ckan.plugins.toolkit as tk

from ckanext.c4w import constants
from ckanext.c4w.logic import helpers as c4w_helpers


# Query-string keys the listing understands. Anything else in the URL is
# ignored rather than forwarded -- the action validates facet names against
# its spec, and this keeps an arbitrary key from reaching it at all.
_LISTING_PARAMS = (
    ('q', False), ('order', False), ('page', False),
    ('status', True), ('difficulty_level', True), ('featured', False),
    ('country', True), ('topic', True), ('has_tag', True),
    ('participation_task', True), ('water_type', True),
    ('water_data_type', True), ('stakeholder_type', True),
    ('community_impact_type', True),
)


def _listing_params():
    """Pull the declared parameters out of the query string.

    Multi-valued facets use getlist, so ?topic=water&topic=climate arrives as
    two values rather than the last one silently winning.
    """
    out = {}
    for name, multi in _LISTING_PARAMS:
        if multi:
            values = [v for v in request.args.getlist(name) if v]
            if values:
                out[name] = values
        else:
            value = request.args.get(name)
            if value:
                out[name] = value
    return out


def project_list():
    listing = tk.get_action('c4w_project_list')({}, _listing_params())
    return tk.render('c4w/project_list.html', extra_vars={
        'listing': listing,
        'orderings': constants.PROJECT_ORDERINGS,
        'params': _listing_params(),
    })


def project_detail(slug):
    try:
        project = tk.get_action('c4w_project_show')({}, {'id': slug})
    except tk.ObjectNotFound:
        return tk.abort(404, tk._('Project not found'))

    # The counter is the one thing an anonymous visitor writes. A failure here
    # must never cost the reader the page.
    try:
        tk.get_action('c4w_project_record_view')({}, {'id': project['id']})
    except Exception:
        pass

    return tk.render('c4w/project_detail.html', extra_vars={
        'project': project,
    })


def project_legacy(legacy_id):
    """Permanent redirect from the Django integer URL to the slug.

    The Django ids do not survive the migration, but every inbound link and
    every search-engine result still carries them. A 301 -- not a 302 --
    because the move is permanent and we want the equity to transfer.
    """
    try:
        project = tk.get_action('c4w_project_show')({}, {'id': str(legacy_id)})
    except tk.ObjectNotFound:
        return tk.abort(404, tk._('Project not found'))
    return redirect(
        c4w_helpers.c4w_url('project_detail', slug=project['slug']), code=301)


def project_geojson(slug):
    """The project's region, served separately from the page.

    Stored GeoJSON is deferred everywhere else precisely so it can be fetched
    here instead of inflating every listing and detail render.
    """
    try:
        project = tk.get_action('c4w_project_show')({}, {'id': slug})
    except tk.ObjectNotFound:
        return tk.abort(404, tk._('Project not found'))

    raw = project.get('geom_geojson')
    if not raw:
        # No content, rather than an empty FeatureCollection: the caller can
        # tell "this project has no region" from the status alone.
        return Response(status=204)
    try:
        geometry = json.loads(raw)
    except (TypeError, ValueError):
        return Response(status=204)

    payload = {
        'type': 'Feature',
        'geometry': geometry,
        'properties': {'name': project.get('name'),
                       'slug': project.get('slug')},
    }
    return Response(json.dumps(payload), mimetype='application/geo+json')

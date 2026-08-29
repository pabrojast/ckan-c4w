# encoding: utf-8
"""Views for the organisation directory."""
from flask import redirect, request

import ckan.plugins.toolkit as tk

from ckanext.c4w import constants
from ckanext.c4w.logic import helpers as c4w_helpers

_LISTING_PARAMS = (
    ('q', False), ('order', False), ('page', False),
    ('org_type', True), ('country', True),
)


def _listing_params():
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


def organisation_list():
    listing = tk.get_action('c4w_organisation_list')({}, _listing_params())
    return tk.render('c4w/organisation_list.html', extra_vars={
        'listing': listing,
        'orderings': constants.DEFAULT_ORDERINGS,
        'params': _listing_params(),
    })


def organisation_detail(slug):
    try:
        organisation = tk.get_action('c4w_organisation_show')({}, {'id': slug})
    except tk.ObjectNotFound:
        return tk.abort(404, tk._('Organisation not found'))
    return tk.render('c4w/organisation_detail.html', extra_vars={
        'organisation': organisation,
    })


def organisation_legacy(legacy_id):
    """Permanent redirect from the Django integer URL to the slug."""
    try:
        organisation = tk.get_action('c4w_organisation_show')(
            {}, {'id': str(legacy_id)})
    except tk.ObjectNotFound:
        return tk.abort(404, tk._('Organisation not found'))
    return redirect(
        c4w_helpers.c4w_url('organisation_detail', slug=organisation['slug']),
        code=301)

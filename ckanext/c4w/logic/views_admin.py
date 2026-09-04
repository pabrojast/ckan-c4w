# encoding: utf-8
"""Sysadmin moderation queue."""
from flask import redirect, request

import ckan.plugins.toolkit as tk

from ckanext.c4w import constants
from ckanext.c4w.logic import helpers as c4w_helpers
from ckanext.c4w.logic.access import require_sysadmin


def admin_index():
    _user, bounced = require_sysadmin()
    if bounced is not None:
        return bounced
    listing = tk.get_action('c4w_moderation_list')({}, {})
    return tk.render('c4w/admin.html', extra_vars={
        'groups': listing.get('groups') or [],
    })


def admin_moderate(entity, item_id, operation):
    """POST endpoint. GET is a 405 from the blueprint."""
    _user, bounced = require_sysadmin()
    if bounced is not None:
        return bounced
    if constants.moderate_error(entity, operation):
        return tk.abort(404, tk._('Not found'))
    try:
        tk.get_action('c4w_entity_moderate')({}, {
            'entity_type': entity,
            'id': item_id,
            'operation': operation,
        })
    except tk.ObjectNotFound:
        return tk.abort(404, tk._('Not found'))
    except tk.NotAuthorized:
        return tk.abort(403, tk._('Not authorized'))
    # Stay on the queue, not the public detail: a hide/unapprove would 404
    # the visitor if we sent them there.
    target = request.form.get('next') or c4w_helpers.c4w_url('admin_index')
    if not (target.startswith('/') and not target.startswith('//')):
        target = c4w_helpers.c4w_url('admin_index')
    return redirect(target)

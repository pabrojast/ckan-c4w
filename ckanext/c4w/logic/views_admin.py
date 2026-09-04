# encoding: utf-8
"""Sysadmin moderation queue and the manager account requests."""
from flask import redirect, request

import ckan.plugins.toolkit as tk

from ckanext.c4w import constants
from ckanext.c4w.logic import helpers as c4w_helpers
from ckanext.c4w.logic.access import require_sysadmin, safe_next


def admin_index():
    _user, bounced = require_sysadmin()
    if bounced is not None:
        return bounced
    listing = tk.get_action('c4w_moderation_list')({}, {})
    try:
        managers = tk.get_action('c4w_manager_list')({}, {}).get('requests')
    except Exception:
        managers = []
    return tk.render('c4w/admin.html', extra_vars={
        'groups': listing.get('groups') or [],
        'managers': managers or [],
    })


def admin_moderate(entity, item_id, operation):
    """POST endpoint. GET is a 405 from the blueprint."""
    _user, bounced = require_sysadmin()
    if bounced is not None:
        return bounced
    if constants.moderate_error(entity, operation):
        return tk.abort(404, tk._('Not found'))
    try:
        result = tk.get_action('c4w_entity_moderate')({}, {
            'entity_type': entity,
            'id': item_id,
            'operation': operation,
        })
    except tk.ObjectNotFound:
        return tk.abort(404, tk._('Not found'))
    except tk.NotAuthorized:
        return tk.abort(403, tk._('Not authorized'))
    if operation == 'process':
        status = result.get('processing_status')
        if status == 'ready':
            tk.h.flash_success(tk._('Processing finished.'))
        elif status == 'failed':
            tk.h.flash_error(tk._('Processing failed: %s')
                             % (result.get('processing_error') or u''))
        else:
            tk.h.flash_notice(tk._('Queued for processing.'))
    # Stay on the queue, not the public detail: a hide/unapprove would 404
    # the visitor if we sent them there.
    return redirect(safe_next(request.form.get('next'),
                              c4w_helpers.c4w_url('admin_index')))


def admin_manager(user_id, operation):
    """POST endpoint: approve or reject a project-manager request."""
    _user, bounced = require_sysadmin()
    if bounced is not None:
        return bounced
    if operation not in ('approve', 'reject'):
        return tk.abort(404, tk._('Not found'))
    action = 'c4w_manager_%s' % operation
    try:
        result = tk.get_action(action)({}, {
            'id': user_id, 'note': request.form.get('note') or u''})
    except tk.ObjectNotFound:
        return tk.abort(404, tk._('Not found'))
    except tk.NotAuthorized:
        return tk.abort(403, tk._('Not authorized'))
    except tk.ValidationError as exc:
        messages = [m for v in exc.error_dict.values()
                    for m in (v if isinstance(v, list) else [v])]
        tk.h.flash_error(u' '.join(u'%s' % m for m in messages))
    else:
        if operation == 'approve':
            tk.h.flash_success(tk._('%s approved as %s of %s.') % (
                result.get('fullname') or result.get('name'),
                result.get('capacity'), result.get('organisation_title')))
        else:
            tk.h.flash_success(tk._('Request rejected.'))
    return redirect(safe_next(request.form.get('next'),
                              c4w_helpers.c4w_url('admin_index')))

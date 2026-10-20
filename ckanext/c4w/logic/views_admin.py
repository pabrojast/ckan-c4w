# encoding: utf-8
"""Sysadmin moderation queue and the manager account requests."""
from flask import redirect, request

import ckan.plugins.toolkit as tk

from ckanext.c4w import constants
from ckanext.c4w.logic import helpers as c4w_helpers
from ckanext.c4w.logic.access import require_sysadmin, require_content_admin, safe_next


def admin_index():
    _user, bounced = require_content_admin()
    if bounced is not None:
        return bounced
    listing = tk.get_action('c4w_moderation_list')({}, {})
    try:
        managers = tk.get_action('c4w_manager_list')({}, {}).get('requests')
    except Exception:
        managers = []
    return tk.render('c4w/admin.html', extra_vars={
        'sections': tk.get_action('c4w_admin_summary')({}, {})['sections'],
        'groups': listing.get('groups') or [],
        'managers': managers or [],
        'project_requests': listing.get('project_requests') or [],
        'content_requests': listing.get('content_requests') or [],
    })


def admin_projects():
    return admin_inventory('projects')


def admin_platforms():
    return admin_inventory('platforms')


def admin_event_edit(event_id=None):
    _, bounced = require_content_admin()
    if bounced is not None:
        return bounced
    from ckan.model.meta import Session
    from ckanext.c4w.logic import forms, views_submit_content
    draft = views_submit_content._load('event', event_id) if event_id else None
    if request.method == 'GET':
        return views_submit_content._render('event', draft, admin_mode=True)
    data = forms.parse_form_data(request.form)
    args = dict(data)
    if event_id:
        args['id'] = event_id
    else:
        args.pop('id', None)
    try:
        saved = tk.get_action('c4w_event_admin_save')({}, args)
    except tk.ValidationError as exc:
        Session.rollback()
        # Do not silently refresh the version and allow a second click to
        # overwrite changes made in another window.
        if draft:
            draft['version'] = data.get('version', '')
        return views_submit_content._render('event', draft, data, exc.error_dict, admin_mode=True)
    tk.h.flash_success(tk._('Event changes published. Its visibility setting is unchanged.')
                      if data.get('mode') == 'publish' else tk._('Draft saved.'))
    return redirect(c4w_helpers.c4w_url('admin_event_edit', event_id=saved['id']))


def admin_inventory(section):
    _user, bounced = require_content_admin()
    if bounced is not None:
        return bounced
    entry = next((entry for entry in constants.ADMIN_SECTIONS if entry[0] == section), None)
    if entry is None:
        return tk.abort(404, tk._('Not found'))
    params = {key: request.args.get(key) for key in ('q', 'page', 'visibility')
              if request.args.get(key)}
    try:
        listing = tk.get_action('c4w_admin_entity_list')({}, dict(params, section=section))
    except tk.ValidationError:
        return tk.abort(400, tk._('Invalid visibility filter.'))
    return tk.render('c4w/admin_inventory.html', extra_vars={
        'listing': listing, 'params': params, 'section': section,
        'cleanup': tk.get_action('c4w_deletion_list')({}, {})['results'],
        'entity_type': entry[1], 'section_label': entry[2],
        'sections': tk.get_action('c4w_admin_summary')({}, {})['sections'],
        'return_url': c4w_helpers.c4w_url(
            'admin_inventory', section=section, q=params.get('q', ''),
            visibility=params.get('visibility', 'all'), page=listing['page']),
    })


def admin_delete(section, item_id):
    _, bounced = require_content_admin()
    if bounced is not None:
        return bounced
    entry = next((e for e in constants.ADMIN_SECTIONS if e[0] == section), None)
    if entry is None:
        tk.abort(404, tk._('Not found'))
    args = {'entity_type': entry[1], 'id': item_id}
    default = c4w_helpers.c4w_url('admin_inventory', section=section, visibility='hidden')
    return_url = safe_next(request.values.get('next'), default)
    errors = []
    if request.method == 'POST':
        args.update(version=request.form.get('version'), confirm_slug=request.form.get('confirm_slug'))
        try:
            result = tk.get_action('c4w_entity_delete')({}, args)
        except tk.ValidationError as exc:
            errors = exc.error_dict.get('message', [])
        except tk.ObjectNotFound:
            tk.abort(404, tk._('Not found'))
        else:
            tk.h.flash_success(tk._('Item permanently deleted.'))
            if result['cleanup_pending']:
                tk.h.flash_notice(tk._('Some stored files still need cleanup. You can retry from the inventory.'))
            return redirect(return_url)
    try:
        preview = tk.get_action('c4w_entity_delete_preview')({}, args)
    except tk.ObjectNotFound:
        tk.abort(404, tk._('Not found'))
    return tk.render('c4w/admin_delete.html', extra_vars={
        'preview': preview, 'section': section, 'return_url': return_url, 'errors': errors})


def admin_cleanup(deletion_id):
    _, bounced = require_content_admin()
    if bounced is not None:
        return bounced
    result = tk.get_action('c4w_deletion_cleanup')({}, {'id': deletion_id})
    if result['cleanup_pending']:
        tk.h.flash_error(tk._('Storage cleanup is still pending. Please try again later.'))
    else:
        tk.h.flash_success(tk._('Stored files cleaned up.'))
    return redirect(safe_next(request.form.get('next'), c4w_helpers.c4w_url('admin_index')))


def admin_moderate(entity, item_id, operation):
    """POST endpoint. GET is a 405 from the blueprint."""
    _user, bounced = require_content_admin()
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
    except tk.ValidationError:
        tk.h.flash_error(tk._('Open the proposal review to approve its changes.'))
        return redirect(c4w_helpers.c4w_url('admin_index'))
    if operation == 'hide':
        if result.get('hidden'):
            tk.h.flash_success(tk._('Hidden. This item is not visible to the public.'))
        elif c4w_helpers.c4w_is_public(entity, result):
            tk.h.flash_success(tk._('Visible to the public.'))
        else:
            tk.h.flash_success(tk._('Unhidden. This item still needs approval before it is public.'))
    elif operation == 'process':
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

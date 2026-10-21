# encoding: utf-8
"""Sysadmin moderation queue and the manager account requests."""
from flask import redirect, request

import ckan.plugins.toolkit as tk

from ckanext.c4w import constants
from ckanext.c4w.logic import helpers as c4w_helpers
from ckanext.c4w.logic.access import require_sysadmin, require_content_admin, safe_next


def admin_moderation():
    _user, bounced = require_content_admin()
    if bounced is not None:
        return bounced
    from ckanext.c4w.logic import mail
    listing = tk.get_action('c4w_moderation_list')({}, {})
    try:
        managers = tk.get_action('c4w_manager_list')({}, {}).get('requests')
    except Exception:
        managers = []
    return tk.render('c4w/admin.html', extra_vars={
        'notification_warning': not mail.moderation_recipients(),
        'sections': tk.get_action('c4w_admin_summary')({}, {})['sections'],
        'groups': listing.get('groups') or [],
        'managers': managers or [],
        'project_requests': listing.get('project_requests') or [],
        'content_requests': listing.get('content_requests') or [],
    })


def admin_index():
    _, bounced = require_content_admin()
    if bounced is not None:
        return bounced
    listing = tk.get_action('c4w_moderation_list')({}, {})
    count = len(listing.get('project_requests', [])) + len(listing.get('content_requests', [])) + sum(len(g['rows']) for g in listing.get('groups', []))
    return tk.render('c4w/admin_home.html', extra_vars=dict(
        sections=tk.get_action('c4w_admin_summary')({}, {})['sections'], pending_count=count))


def admin_edit(section, item_id):
    _, bounced = require_content_admin()
    if bounced is not None:
        return bounced
    from ckanext.c4w.logic import admin_fields, forms
    from ckanext.c4w.logic.sanitize import sanitize_html, sanitize_rich_html
    from ckan.model.meta import Session
    args = dict(section=section, id=item_id)
    try:
        item = tk.get_action('c4w_admin_entity_edit_show')({}, args)
    except tk.ObjectNotFound:
        tk.abort(404)
    fields = admin_fields.fields(item['entity_type'])
    errors, data, version = {}, dict(item['payload']), item['version']
    if request.method == 'POST':
        multi = [f['name'] for f in fields if f['kind'] in ('multi', 'countries', 'languages', 'organisations')]
        posted = forms.parse_form_data(request.form, request.files, multi=multi)
        # Unchecked booleans are deliberately cleared, while omitted API fields
        # retain their stored value in the action.
        for f in fields:
            if f['kind'] == 'bool':
                posted.setdefault(f['name'], False)
        try:
            tk.get_action('c4w_admin_entity_update')({}, dict(posted, **args))
        except tk.ValidationError as exc:
            Session.rollback()
            errors = exc.error_dict
            data.update(forms.echo_values(posted))
            version = posted.get('version', '')
        else:
            tk.h.flash_success(tk._('Changes saved. The visibility setting is unchanged.'))
            return redirect(c4w_helpers.c4w_url('admin_edit', **dict(section=section, item_id=item_id)))
    for f in fields:
        name = f['name']
        if f['kind'] in ('rich', 'news'):
            data[name] = (sanitize_rich_html if f['kind'] == 'news' else sanitize_html)(str(data.get(name) or ''))
        elif f['kind'] in ('terms', 'urls', 'editors') and isinstance(data.get(name), list):
            if f['kind'] == 'editors':
                import ckan.model as model
                data[name] = [u.name for u in (model.User.get(v) for v in data[name]) if u]
            data[name] = ', '.join(data[name])
    entity = item['entity_type']
    review_url = (c4w_helpers.c4w_url('project_review', project_id=item_id) if entity == 'project' else
                  c4w_helpers.c4w_url('content_review', entity=entity, content_id=item_id) if entity in ('post', 'event') else
                  c4w_helpers.c4w_url('admin_review', section=section, item_id=item_id))
    return tk.render('c4w/admin_edit.html', extra_vars=dict(item=item, section=section,
        groups=admin_fields.groups(entity), data=data, errors=errors, version=version,
        review_url=review_url, image_limit=tk.config.get('ckan.max_image_size') or 2,
        map_style=tk.config.get('ckanext.c4w.basemap_style') or 'https://tiles.openfreemap.org/styles/positron'))


def admin_review(section, item_id):
    _, bounced = require_content_admin()
    if bounced is not None:
        return bounced
    from ckanext.c4w.logic import admin_fields
    from ckan.model.meta import Session
    args = dict(section=section, id=item_id)
    try:
        item = tk.get_action('c4w_admin_entity_edit_show')({}, args)
    except tk.ObjectNotFound:
        tk.abort(404)
    if item['revision_status'] and item['entity_type'] in ('project', 'post', 'event'):
        return redirect(c4w_helpers.c4w_url('project_review', project_id=item_id) if item['entity_type'] == 'project' else
                        c4w_helpers.c4w_url('content_review', entity=item['entity_type'], content_id=item_id))
    errors = {}
    if request.method == 'POST':
        try:
            tk.get_action('c4w_admin_entity_review')({}, dict(request.form, **args))
        except tk.ValidationError as exc:
            Session.rollback(); errors = exc.error_dict
        else:
            tk.h.flash_success(tk._('Review saved.'))
            return redirect(c4w_helpers.c4w_url('admin_moderation'))
    from ckanext.c4w.logic.sanitize import sanitize_html, sanitize_rich_html
    data = dict(item['payload'])
    for f in admin_fields.fields(item['entity_type']):
        if f['kind'] in ('rich', 'news'):
            data[f['name']] = (sanitize_rich_html if f['kind'] == 'news' else sanitize_html)(str(data.get(f['name']) or ''))
    return tk.render('c4w/admin_review.html', extra_vars=dict(item=item, data=data,
        steps=admin_fields.groups(item['entity_type']), errors=errors, version=request.form.get('version', item['version'])))


def admin_projects():
    return admin_inventory('projects')


def admin_platforms():
    return admin_inventory('platforms')


def admin_event_edit(event_id=None):
    if event_id:
        return admin_edit('events', event_id)
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

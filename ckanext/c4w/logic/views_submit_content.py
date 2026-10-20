"""Short news/event forms and revision previews in the C4W portal."""
from flask import request, redirect
import ckan.plugins.toolkit as tk
from ckan.model.meta import Session
from ckanext.c4w.logic import forms, helpers
from ckanext.c4w.logic.access import require_user, require_sysadmin
from ckanext.c4w.logic.action import content_write


def choose():
    _, bounced = require_user()
    return bounced if bounced is not None else tk.render('c4w/content_choose.html')


def _load(entity, content_id):
    try:
        return tk.get_action('c4w_content_draft_show')({}, {'entity_type': entity, 'id': content_id})
    except tk.ObjectNotFound:
        tk.abort(404, tk._('Contribution not found.'))


def _render(entity, draft=None, data=None, errors=None, review=False, admin_mode=False):
    data = dict(data if data is not None else (draft or {}).get('payload', {}))
    # Invalid submissions may be echoed into a contenteditable: sanitize that echo too.
    for name in ('content', 'description'):
        if name in data:
            sanitizer = content_write.sanitize_rich_html if name == 'content' else content_write.sanitize_html
            data[name] = sanitizer(str(data[name] or ''))
    return tk.render('c4w/content_form.html', extra_vars=dict(entity=entity, draft=draft,
        data=data, errors=forms.errors_for_template(errors or {}), review=review, admin_mode=admin_mode))


def edit(entity, content_id=None):
    _, bounced = require_user()
    if bounced is not None:
        return bounced
    if entity not in content_write.FIELDS:
        tk.abort(404, tk._('Unknown contribution type.'))
    draft = _load(entity, content_id) if content_id else None
    if request.method == 'GET':
        return _render(entity, draft)
    data = forms.parse_form_data(request.form)
    try:
        args = dict(data, entity_type=entity)
        if draft:
            args['id'] = draft['id']
        draft = tk.get_action('c4w_content_update' if draft else 'c4w_content_create')({}, args)
        upload = request.files.get('image')
        if upload and upload.filename:
            draft = tk.get_action('c4w_content_image_upload')({}, dict(entity_type=entity,
                id=draft['id'], version=draft['version'], upload=upload))
        if data.get('action') == 'submit':
            draft = tk.get_action('c4w_content_submit')({}, dict(entity_type=entity,
                id=draft['id'], version=draft['version']))
            tk.h.flash_success(tk._('Sent for review. You can follow its status in My submissions.'))
            return redirect(helpers.c4w_url('content_review', entity=entity, content_id=draft['id']))
    except tk.ValidationError as exc:
        Session.rollback()
        if draft:
            draft = _load(entity, draft['id'])
        return _render(entity, draft, dict((draft or {}).get('payload', {}), **data), exc.error_dict)
    except tk.NotAuthorized:
        Session.rollback()
        return _render(entity, draft, data, {'message': [tk._('Confirm your email before contributing.')]})
    tk.h.flash_success(tk._('Draft saved.'))
    return redirect(helpers.c4w_url('content_edit', entity=entity, content_id=draft['id']))


def review(entity, content_id):
    _, bounced = require_sysadmin() if request.method == 'POST' else require_user()
    if bounced is not None:
        return bounced
    draft = _load(entity, content_id)
    errors = {}
    if request.method == 'POST':
        try:
            tk.get_action('c4w_content_review')({}, dict(request.form, entity_type=entity, id=content_id))
        except tk.ValidationError as exc:
            Session.rollback(); errors = exc.error_dict
            draft = _load(entity, content_id)
        else:
            tk.h.flash_success(tk._('Review saved.'))
            return redirect(helpers.c4w_url('admin_index'))
    return _render(entity, draft, errors=errors, review=True)

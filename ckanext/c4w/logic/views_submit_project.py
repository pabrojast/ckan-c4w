"""HTML orchestration for project drafts, preview and moderation."""
from flask import redirect, request, jsonify
import ckan.plugins.toolkit as tk

from ckanext.c4w.logic import forms, helpers, project_fields
from ckanext.c4w.logic.access import require_user, require_content_admin


def lookup(kind):
    try:
        return jsonify(tk.get_action('c4w_lookup')({}, dict(kind=kind,
            q=request.args.get('q', ''), ids=request.args.get('ids', ''))))
    except tk.ObjectNotFound:
        tk.abort(404)


def _load(project_id):
    try:
        return tk.get_action('c4w_project_draft_show')({}, {'id': project_id})
    except tk.ObjectNotFound:
        tk.abort(404, tk._('Project not found'))


def _display(data):
    data = dict(data)
    from ckanext.c4w.logic.sanitize import sanitize_html
    for name, field in project_fields.FIELDS.items():
        if field['kind'] == 'rich':
            data[name] = sanitize_html(str(data.get(name) or ''))
    for name in ('keywords', 'funding_body'):
        if isinstance(data.get(name), list):
            data[name] = ', '.join(data[name])
    if isinstance(data.get('editors'), list):
        import ckan.model as model
        data['editors'] = ', '.join(user.name for user in
            (model.User.get(v) for v in data['editors']) if user)
    return data


def _render(step, draft=None, data=None, errors=None, review=False):
    return tk.render('c4w/project_form.html', extra_vars={
        'steps': project_fields.STEPS, 'step_number': step,
        'step': project_fields.STEPS[step - 1], 'draft': draft,
        'data': _display(data if data is not None else (draft or {}).get('payload', {})),
        'errors': forms.errors_for_template(errors or {}), 'review': review,
        'return_dataset': request.values.get('return_dataset', ''),
        'image_limit': tk.config.get('ckan.max_image_size') or 2,
        'map_style': tk.config.get('ckanext.c4w.basemap_style') or 'https://tiles.openfreemap.org/styles/positron',
    })


def start():
    _, bounced = require_user()
    if bounced is not None:
        return bounced
    if request.method == 'GET':
        return _render(1)
    data = forms.parse_form_data(request.form, files=request.files, multi=project_fields.MULTI)
    try:
        draft = tk.get_action('c4w_project_create')({}, data)
    except tk.ValidationError as exc:
        return _render(1, data=data, errors=exc.error_dict)
    except tk.NotAuthorized:
        tk.abort(403, tk._('Confirm your email before contributing.'))
    # This target is only a form. The dataset action checks ownership again
    # when its owner saves the selected project.
    return_dataset = request.form.get('return_dataset')
    if return_dataset:
        dataset = tk.get_action('c4w_dataset_show')({}, {'id': return_dataset})
        if dataset.get('can_edit'):
            return redirect(helpers.c4w_url('submit_data_step', dataset_id=dataset['id'],
                                           step=1, project_id=draft['id']))
    return redirect(helpers.c4w_url('account') if data.get('action') == 'exit' else
                    helpers.c4w_url('submit_project_step', project_id=draft['id'], step=2))


def step(project_id, step):
    _, bounced = require_user()
    if bounced is not None:
        return bounced
    if step not in range(1, 7):
        tk.abort(404)
    draft = _load(project_id)
    if request.method == 'GET':
        return _render(step, draft)
    data = forms.parse_form_data(request.form, files=request.files, multi=project_fields.MULTI)
    data.update(id=project_id, step=step)
    try:
        draft = tk.get_action('c4w_project_update')({}, data)
        if data.get('action') == 'submit':
            tk.h.flash_success(tk._('Project sent for review. You can add data whenever you are ready.'))
            return redirect(helpers.c4w_url('project_review', project_id=project_id))
    except tk.ValidationError as exc:
        # A failed save is atomic. Keep the submitted version so a retry
        # cannot silently overwrite a concurrent editor's changes.
        latest = _load(project_id)
        latest['version'] = data.get('version', latest['version'])
        if data.get('action') == 'submit':
            step = next((group['step'] for group in project_fields.STEPS
                         if any(f['name'] in exc.error_dict for f in group['fields'])), step)
        echo = {key: value for key, value in data.items() if key in request.form}
        return _render(step, latest, dict(latest['payload'], **echo), exc.error_dict)
    except tk.NotAuthorized:
        tk.abort(403, tk._('Confirm your email before contributing.'))
    tk.h.flash_success(tk._('Draft saved.'))
    if data.get('action') == 'exit':
        return redirect(helpers.c4w_url('account'))
    return redirect(helpers.c4w_url('submit_project_step', project_id=project_id,
                    step=min(step + 1, 6) if data.get('action') == 'next' else step))


def review(project_id):
    _, bounced = require_content_admin() if request.method == 'POST' else require_user()
    if bounced is not None:
        return bounced
    draft = _load(project_id)
    errors = {}
    if request.method == 'POST':
        try:
            tk.get_action('c4w_project_review')({}, dict(request.form, id=project_id))
        except tk.ValidationError as exc:
            errors = exc.error_dict
        else:
            tk.h.flash_success(tk._('Review saved.'))
            return redirect(helpers.c4w_url('admin_moderation'))
    return _render(6, draft, errors=errors, review=True)

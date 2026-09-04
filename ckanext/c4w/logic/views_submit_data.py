# encoding: utf-8
"""The data wizard: five steps over one draft row.

The draft is created on the first POST and every later step saves through
an action, so a visitor can leave and come back to exactly where they were
(``wizard_step``), and nothing lives in the session. Files are uploaded by
their own POST (``file``) because a multipart form cannot also carry the
mapping table, and the mapping page needs the sniff of the uploaded file.
"""
import logging

from flask import redirect, request

import ckan.plugins.toolkit as tk

from ckanext.c4w import constants
from ckanext.c4w.logic import forms
from ckanext.c4w.logic import helpers as c4w_helpers
from ckanext.c4w.logic.access import require_user

log = logging.getLogger(__name__)

STEPS = constants.DATASET_FORM_STEPS
LAST_STEP = STEPS[-1]['step']

# Checkbox groups and multi-selects: always lists (see forms.parse_form_data).
_MULTI = ('topic', 'water_type', 'water_data_type', 'technology_used',
          'country', 'attachments')


def _context():
    return {}


def _step_url(dataset_id, step):
    return c4w_helpers.c4w_url('submit_data_step', dataset_id=dataset_id,
                               step=step)


def _load(dataset_id):
    """The dataset for the wizard, or a 404 response.

    ``c4w_dataset_show`` already hides what the visitor may not see; the
    ``can_edit`` flag is what separates "may look" from "may change".
    """
    try:
        dataset = tk.get_action('c4w_dataset_show')(_context(),
                                                    {'id': dataset_id})
    except tk.ObjectNotFound:
        return None
    if not dataset.get('can_edit'):
        return None
    return dataset


def _reachable(dataset):
    """Highest step the visitor may open by link."""
    step = int(dataset.get('wizard_step') or 1)
    if not dataset.get('data_files'):
        step = min(step, 2)
    elif not dataset.get('mapping'):
        step = min(step, 3)
    return max(1, min(step, LAST_STEP))


def _options(action, extra):
    """``[(id, name)]`` for a select of visible rows, fail-soft."""
    try:
        listing = tk.get_action(action)(_context(), dict(
            {'page_size': 100, 'order': 'name'}, **extra))
        return [(row['id'], row.get('name') or row.get('title') or row['id'])
                for row in listing.get('results') or []]
    except Exception:
        log.debug("ckanext-c4w: could not list options for %s", action,
                  exc_info=True)
        return []


def _render(step, dataset, data, errors, extra=None):
    """Render one step with everything the templates share."""
    spec = next(s for s in STEPS if s['step'] == step)
    extra_vars = {
        'step': spec,
        'step_number': step,
        'dataset': dataset,
        'data': data or {},
        'errors': forms.errors_for_template(errors),
        'reachable': _reachable(dataset) if dataset else 1,
        'is_new': dataset is None,
    }
    if step == 1:
        extra_vars['project_options'] = _options('c4w_project_list', {})
        extra_vars['organisation_options'] = _options(
            'c4w_organisation_list', {})
    extra_vars.update(extra or {})
    return tk.render('c4w/dataset_step_%d.html' % step, extra_vars=extra_vars)


def _dataset_as_form(dataset):
    """The stored values in the shape the step templates read."""
    data = dict(dataset)
    terms = dataset.get('terms') or {}
    labels = dataset.get('term_labels') or {}
    data['keywords'] = u', '.join(
        item.get('label') or item['term'] for item in labels.get('keyword', []))
    for vocabulary in ('topic', 'water_type', 'water_data_type',
                       'technology_used', 'country'):
        data[vocabulary] = list(terms.get(vocabulary, []))
    data['related_urls'] = u'\n'.join(dataset.get('related_urls') or [])
    data['terms_accepted'] = bool(dataset.get('terms_accepted_at'))
    data['licence_confirm'] = bool(dataset.get('terms_accepted_at'))
    if not data.get('license_id'):
        data['license_id'] = constants.DATASET_DEFAULT_LICENSE
    if not data.get('language'):
        data['language'] = u'en'
    return data


def _prefill_contact(data):
    """Suggest the signed-in person as the contact on an empty step 5."""
    userobj = getattr(tk.g, 'userobj', None)
    if userobj is None or getattr(userobj, 'is_anonymous', False):
        return data
    if not data.get('contact_name'):
        data['contact_name'] = getattr(userobj, 'fullname', None) or \
            getattr(userobj, 'name', u'')
    if not data.get('contact_email'):
        data['contact_email'] = getattr(userobj, 'email', None) or u''
    return data


def _form_data():
    data = forms.parse_form_data(request.form, request.files, multi=_MULTI)
    if isinstance(data.get('related_urls'), str):
        data['related_urls'] = [line.strip() for line in
                                data['related_urls'].splitlines()
                                if line.strip()]
    return data


# --------------------------------------------------------------------------- #
# Views
# --------------------------------------------------------------------------- #

def start():
    """Step 1 for a new dataset; the POST creates the draft."""
    _user, bounced = require_user()
    if bounced is not None:
        return bounced
    if request.method == 'GET':
        return _render(1, None, {'language': u'en'}, {})
    data = _form_data()
    try:
        dataset = tk.get_action('c4w_dataset_create')(_context(), data)
    except tk.ValidationError as exc:
        return _render(1, None, forms.echo_values(data), exc.error_dict)
    tk.h.flash_success(tk._('Draft saved. Now add the file.'))
    return redirect(_step_url(dataset['id'], 2))


def step(dataset_id, step):
    _user, bounced = require_user()
    if bounced is not None:
        return bounced
    dataset = _load(dataset_id)
    if dataset is None:
        return tk.abort(404, tk._('Dataset not found'))
    if step < 1 or step > LAST_STEP:
        return tk.abort(404, tk._('Not found'))
    if step > _reachable(dataset):
        tk.h.flash_notice(tk._('Finish the previous step first.'))
        return redirect(_step_url(dataset['id'], _reachable(dataset)))

    handler = {1: _step_navl, 2: _step_files, 3: _step_columns,
               4: _step_navl, 5: _step_review}[step]
    return handler(dataset, step)


def _step_navl(dataset, step):
    if request.method == 'GET':
        return _render(step, dataset, _dataset_as_form(dataset), {})
    data = _form_data()
    data.update({'id': dataset['id'], 'step': step})
    try:
        dataset = tk.get_action('c4w_dataset_update')(_context(), data)
    except tk.ValidationError as exc:
        return _render(step, dataset, forms.echo_values(data), exc.error_dict)
    tk.h.flash_success(tk._('Saved.'))
    if request.form.get('action') == 'save':
        return redirect(_step_url(dataset['id'], step))
    return redirect(_step_url(dataset['id'], step + 1))


def _step_files(dataset, step):
    primary = (dataset.get('data_files') or [None])[0]
    return _render(2, dataset, _dataset_as_form(dataset), {}, extra={
        'primary': primary,
        'max_upload_mb': int(tk.config.get('ckanext.c4w.data_max_upload_mb')
                             or 256),
        'max_attachment_mb': int(
            tk.config.get('ckanext.c4w.attachment_max_upload_mb') or 25),
    })


def file(dataset_id):
    """POST: upload or delete one file, then back to step 2."""
    _user, bounced = require_user()
    if bounced is not None:
        return bounced
    dataset = _load(dataset_id)
    if dataset is None:
        return tk.abort(404, tk._('Dataset not found'))
    op = request.form.get('op') or 'upload'
    try:
        if op == 'delete':
            tk.get_action('c4w_dataset_file_delete')(_context(), {
                'id': dataset['id'], 'file_id': request.form.get('file_id')})
            tk.h.flash_success(tk._('File removed.'))
        else:
            kind = request.form.get('kind') or 'data'
            uploads = [f for f in request.files.getlist('upload')
                       if f and f.filename]
            if not uploads:
                tk.h.flash_error(tk._('Choose a file first.'))
                return redirect(_step_url(dataset['id'], 2))
            for upload in uploads:
                tk.get_action('c4w_dataset_file_upload')(_context(), {
                    'id': dataset['id'], 'kind': kind, 'upload': upload})
            tk.h.flash_success(
                tk._('File stored.') if kind == 'data'
                else tk._('Attachment stored.'))
    except tk.ValidationError as exc:
        messages = forms.errors_for_template(exc.error_dict)
        tk.h.flash_error(u' '.join(
            m for msgs in messages.values() for m in msgs))
    except tk.ObjectNotFound:
        return tk.abort(404, tk._('Not found'))
    return redirect(_step_url(dataset['id'], 2))


# --- step 3: the mapping table -------------------------------------------- #

def _mapping_from_form(form, base):
    """Rebuild the mapping spec from the posted table."""
    spec = {
        'version': 1,
        'layout': form.get('layout') or base.get('layout'),
        'csv': dict(base.get('csv') or {}),
        'site': {
            'id': form.get('site__id') or None,
            'name': form.get('site__name') or None,
            'lat': form.get('site__lat') or None,
            'lon': form.get('site__lon') or None,
            'country': form.get('site__country') or None,
            'country_kind': form.get('site__country_kind') or None,
        },
        'date': {
            'column': form.get('date__column') or None,
            'format': form.get('date__format') or 'auto',
            'grain': form.get('date__grain') or 'year',
        },
        'long': None,
        'parameters': [],
        'dimensions': [],
        'filters': {
            'drop_negative': bool(form.get('filters__drop_negative')),
            'min_value': form.get('filters__min_value') or None,
            'max_value': form.get('filters__max_value') or None,
        },
    }
    if spec['layout'] == 'long':
        spec['long'] = {
            'parameter': form.get('long__parameter') or None,
            'value': form.get('long__value') or None,
            'unit': form.get('long__unit') or None,
            'discover': bool(form.get('long__discover')),
        }
    index = 0
    while form.get('param__%d__source' % index) is not None:
        prefix = 'param__%d__' % index
        spec['parameters'].append({
            'key': form.get(prefix + 'key') or u'',
            'source': form.get(prefix + 'source') or u'',
            'label': form.get(prefix + 'label') or u'',
            'unit': form.get(prefix + 'unit') or u'',
            'family': form.get(prefix + 'family') or None,
            'normalise': form.get(prefix + 'normalise') or 'auto',
            'bins': form.get(prefix + 'bins') or None,
            'include': bool(form.get(prefix + 'include')),
        })
        index += 1
    for index in range(3):
        column = form.get('dim__%d__column' % index)
        if column:
            spec['dimensions'].append({
                'key': form.get('dim__%d__key' % index) or u'',
                'column': column,
                'label': form.get('dim__%d__label' % index) or column,
                'max_values': form.get('dim__%d__max_values' % index) or 30,
            })
    return spec


def _mapping_errors(errors):
    """Mapping error paths ('site.lat') as form field names ('site__lat')."""
    out = {}
    for key, messages in (errors or {}).items():
        name = (u'%s' % key).replace(u'.', u'__')
        name = name.replace(u'parameters__', u'param__') \
            .replace(u'dimensions__', u'dim__')
        out[name] = messages if isinstance(messages, list) else [messages]
    return out


def _step_columns(dataset, step):
    from ckanext.c4w.data import dates

    try:
        proposal = tk.get_action('c4w_dataset_mapping_propose')(
            _context(), {'id': dataset['id']})
    except tk.ValidationError:
        tk.h.flash_notice(tk._('Upload a data file first.'))
        return redirect(_step_url(dataset['id'], 2))

    sniff = proposal.get('sniff') or {}
    spec = proposal.get('mapping') or {}
    if request.method == 'GET' and request.args.get('reset') and \
            sniff.get('proposal'):
        spec = sniff['proposal']
    errors = {}
    unit_note = dataset.get('unit_note') or u''

    if request.method == 'POST':
        spec = _mapping_from_form(request.form, spec)
        unit_note = request.form.get('unit_note') or u''
        try:
            tk.get_action('c4w_dataset_mapping_update')(_context(), {
                'id': dataset['id'], 'mapping': spec,
                'unit_note': unit_note})
        except tk.ValidationError as exc:
            errors = _mapping_errors(exc.error_dict)
        else:
            tk.h.flash_success(tk._('Columns mapped.'))
            if request.form.get('action') == 'save':
                return redirect(_step_url(dataset['id'], 3))
            return redirect(_step_url(dataset['id'], 4))
        from ckanext.c4w.data import mapping as mapper
        spec = mapper.normalise(spec)

    columns = sniff.get('columns') or []
    return _render(3, dataset, {'unit_note': unit_note}, errors, extra={
        'sniff': sniff,
        'spec': spec,
        'columns': columns,
        'column_names': [c['name'] for c in columns],
        'file': proposal.get('file'),
        'date_formats': ['auto'] + list(dates.CANDIDATE_FORMATS),
        'country_kinds': (('', tk._('Not given')),
                          ('iso2', tk._('2-letter code (CL)')),
                          ('iso3', tk._('3-letter code (CHL)')),
                          ('name', tk._('Country name')),
                          ('site_prefix3', tk._('First 3 letters of the site id'))),
        'stored': proposal.get('stored'),
    })


# --- step 5: contact + review --------------------------------------------- #

def _step_review(dataset, step):
    if request.method == 'GET':
        data = _prefill_contact(_dataset_as_form(dataset))
        return _render(5, dataset, data, {}, extra=_review_vars(dataset))
    data = _form_data()
    data.update({'id': dataset['id'], 'step': 5})
    try:
        dataset = tk.get_action('c4w_dataset_update')(_context(), data)
    except tk.ValidationError as exc:
        return _render(5, dataset, forms.echo_values(data), exc.error_dict,
                       extra=_review_vars(dataset))
    if request.form.get('action') != 'submit':
        tk.h.flash_success(tk._('Saved.'))
        return redirect(_step_url(dataset['id'], 5))
    try:
        dataset = tk.get_action('c4w_dataset_submit')(_context(),
                                                      {'id': dataset['id']})
    except tk.ValidationError as exc:
        tk.h.flash_error(tk._('Some steps are still incomplete.'))
        return _render(5, dataset, _dataset_as_form(dataset), {},
                       extra=_review_vars(dataset, exc.error_dict))
    status = dataset.get('processing_status')
    if status == 'ready':
        tk.h.flash_success(tk._(
            'Thank you. Your dataset is processed and waits for a reviewer.'))
    elif status == 'failed':
        tk.h.flash_error(tk._('Submitted, but processing failed: %s')
                         % (dataset.get('processing_error') or u''))
    else:
        tk.h.flash_success(tk._(
            'Thank you. Your dataset is queued for processing and waits '
            'for a reviewer.'))
    return redirect(c4w_helpers.c4w_url('dataset_detail',
                                        slug=dataset['slug']))


def _review_vars(dataset, completeness=None):
    if completeness is None:
        try:
            completeness = tk.get_action('c4w_dataset_completeness')(
                _context(), {'id': dataset['id']}).get('errors') or {}
        except Exception:
            completeness = {}
    missing = forms.errors_for_template(completeness)
    by_step = {}
    for name in missing:
        target = forms.first_error_step({name: missing[name]})
        if name == 'data_file':
            target = 2
        if name == 'mapping_json':
            target = 3
        by_step.setdefault(target, []).append(name)
    return {
        'missing': missing,
        'missing_by_step': sorted(by_step.items()),
        'steps': STEPS,
    }


def process(dataset_id):
    _user, bounced = require_user()
    if bounced is not None:
        return bounced
    dataset = _load(dataset_id)
    if dataset is None:
        return tk.abort(404, tk._('Dataset not found'))
    try:
        result = tk.get_action('c4w_dataset_process')(
            _context(), {'id': dataset['id'],
                         'force': request.form.get('force')})
    except tk.ValidationError as exc:
        messages = forms.errors_for_template(exc.error_dict)
        tk.h.flash_error(u' '.join(
            m for msgs in messages.values() for m in msgs))
        return redirect(_step_url(dataset['id'], 5))
    status = result.get('processing_status')
    if status == 'ready':
        tk.h.flash_success(tk._('Processing finished.'))
    elif status == 'failed':
        tk.h.flash_error(tk._('Processing failed: %s')
                         % (result.get('processing_error') or u''))
    else:
        tk.h.flash_notice(tk._('Queued for processing.'))
    target = request.form.get('next')
    from ckanext.c4w.logic.access import safe_next
    return redirect(safe_next(target, _step_url(dataset['id'], 5)))


def edit(slug):
    _user, bounced = require_user()
    if bounced is not None:
        return bounced
    dataset = _load(slug)
    if dataset is None:
        return tk.abort(404, tk._('Dataset not found'))
    return redirect(_step_url(dataset['id'], 1))


def delete(slug):
    _user, bounced = require_user()
    if bounced is not None:
        return bounced
    dataset = _load(slug)
    if dataset is None:
        return tk.abort(404, tk._('Dataset not found'))
    try:
        tk.get_action('c4w_dataset_delete')(_context(), {'id': dataset['id']})
    except tk.NotAuthorized:
        return tk.abort(403, tk._('Not authorized'))
    tk.h.flash_success(tk._('Dataset removed.'))
    return redirect(c4w_helpers.c4w_url('account'))

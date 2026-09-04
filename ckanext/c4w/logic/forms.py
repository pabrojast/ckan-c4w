# encoding: utf-8
"""Form plumbing shared by every C4W form view.

Three small jobs: turn a Werkzeug ``MultiDict`` into the flat dict navl
expects, turn navl's error dict into something a template can render next
to a field, and decide which wizard step a visitor should be sent back to.
"""
from ckanext.c4w import constants


def parse_form_data(form, files=None, multi=()):
    """A plain dict from ``request.form`` (+ ``request.files``).

    A key that arrives once is a string; one that arrives many times is a
    list. Keys named in ``multi`` are ALWAYS lists, so a checkbox group with
    one box ticked is not mistaken for a scalar. Empty strings are kept --
    navl's ``ignore_empty`` decides what an empty field means per schema.
    """
    out = {}
    for key in form.keys():
        values = form.getlist(key)
        if key in multi or len(values) > 1:
            out[key] = [v for v in values]
        else:
            out[key] = values[0] if values else u''
    for key in multi:
        out.setdefault(key, [])
    if files is not None:
        for key in files.keys():
            uploads = [f for f in files.getlist(key) if _has_file(f)]
            if not uploads:
                continue
            out[key] = uploads if (key in multi or len(uploads) > 1) \
                else uploads[0]
    return out


def _has_file(value):
    return bool(value is not None and getattr(value, 'filename', None))


def errors_for_template(errors):
    """``{field: [message, ...]}`` from a navl error dict.

    navl keys can be tuples for nested fields; they are joined with ``__``
    so a template can still look them up by a string.
    """
    out = {}
    for key, messages in (errors or {}).items():
        name = u'__'.join(u'%s' % part for part in key) \
            if isinstance(key, (tuple, list)) else u'%s' % key
        if isinstance(messages, dict):
            flat = []
            for sub in messages.values():
                flat.extend(sub if isinstance(sub, list) else [sub])
            messages = flat
        elif not isinstance(messages, list):
            messages = [messages]
        out[name] = [u'%s' % m for m in messages if m]
    return out


def first_error_step(errors, steps=None):
    """The number of the first wizard step holding an error, or None."""
    steps = steps or constants.DATASET_FORM_STEPS
    names = set(errors_for_template(errors))
    if not names:
        return None
    for step in steps:
        if names & set(step['fields']):
            return step['step']
    return steps[0]['step']


def step_fields(step, steps=None):
    """The field names of one wizard step, or () for an unknown step."""
    steps = steps or constants.DATASET_FORM_STEPS
    for item in steps:
        if item['step'] == step:
            return tuple(item['fields'])
    return ()


def echo_values(data, exclude=('password', 'password_confirm', 'upload',
                               'data_file', 'attachments')):
    """The submitted values a form re-renders after a validation error.

    Passwords and file objects are never echoed back into the page.
    """
    out = {}
    for key, value in (data or {}).items():
        if key in exclude or _has_file(value):
            continue
        if isinstance(value, list) and any(_has_file(v) for v in value):
            continue
        out[key] = value
    return out

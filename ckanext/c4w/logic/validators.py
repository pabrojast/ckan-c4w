# encoding: utf-8
"""navl validators for ckanext-c4w.

Each validator is a thin wrapper around a rule in ``logic/checks.py`` --
the rule is CKAN-free and unit-tested there; this module only translates a
``ValueError`` into the ``Invalid`` navl expects.

Vocabulary validators are built by FACTORIES that close over the vocabulary
name and read ``constants``, so a vocabulary is never spelled out twice:
adding a term to constants.py is the whole change. The factories are used
directly by ``logic/schema.py``; ``get_validators`` registers the plain ones
for other plugins and templates.
"""
import ckan.plugins.toolkit as tk

from ckanext.c4w import constants
from ckanext.c4w.logic import checks


def _wrap(fn, *args, **kwargs):
    """A ``(value)`` navl validator from a checks function."""
    def validator(value):
        try:
            return fn(value, *args, **kwargs)
        except ValueError as exc:
            raise tk.Invalid(tk._(u'%s' % exc))
    validator.__name__ = str(getattr(fn, '__name__', 'validator'))
    return validator


# --- factories ------------------------------------------------------------- #

def vocabulary(name):
    """One term of a closed vocabulary."""
    def validator(value):
        try:
            return checks.check_vocabulary_term(name, value)
        except ValueError as exc:
            raise tk.Invalid(tk._(u'%s' % exc))
    return validator


def vocabulary_list(name, max_items=None, min_items=0):
    """Many terms of a closed vocabulary, as a de-duplicated list."""
    def validator(value):
        try:
            return checks.check_vocabulary_terms(
                name, value, max_items=max_items, min_items=min_items)
        except ValueError as exc:
            raise tk.Invalid(tk._(u'%s' % exc))
    return validator


def choice(choices):
    def validator(value):
        try:
            return checks.check_choice(value, choices)
        except ValueError as exc:
            raise tk.Invalid(tk._(u'%s' % exc))
    return validator


def max_length(n):
    def validator(value):
        try:
            return checks.clean_text(value, max_length=n)
        except ValueError as exc:
            raise tk.Invalid(tk._(u'%s' % exc))
    return validator


def free_terms(limit=30):
    return _wrap(checks.check_free_terms, limit=limit)


def country_code_list(limit=20, min_items=0):
    def validator(value):
        try:
            codes = checks.check_country_codes(value, limit=limit)
        except ValueError as exc:
            raise tk.Invalid(tk._(u'%s' % exc))
        if len(codes) < min_items:
            raise tk.Invalid(tk._(u'Choose at least one country'))
        return codes
    return validator


def url_list(limit=5):
    return _wrap(checks.check_urls, limit=limit)


def end_after(start_key):
    """A ``(key, data, errors, context)`` validator: end >= start."""
    def validator(key, data, errors, context):
        end = data.get(key)
        start = data.get((start_key,))
        try:
            checks.check_end_after(start, end)
        except ValueError as exc:
            errors[key].append(tk._(u'%s' % exc))
    return validator


def passwords_match(confirm_key='password_confirm'):
    """A ``(key, data, errors, context)`` validator over two fields."""
    def validator(key, data, errors, context):
        try:
            data[key] = checks.check_password(
                data.get(key), data.get((confirm_key,)))
        except ValueError as exc:
            errors[key].append(tk._(u'%s' % exc))
    return validator


def license_id():
    """A licence the portal offers. Labels come from CKAN; ids from us."""
    return choice(constants.DATASET_LICENSES)


def existing(entity_type):
    """The id (or slug) of a visible row of ``entity_type``, or empty."""
    def validator(value, context):
        reference = (u'%s' % (value or u'')).strip()
        if not reference:
            return u''
        from ckanext.c4w import db
        from ckanext.c4w.logic.action import _common
        model_cls = db.ENTITY_CLASSES.get(entity_type)
        row = _common.get_by_reference(model_cls, reference) \
            if model_cls else None
        if row is None or not _common.is_visible(entity_type, row, context):
            raise tk.Invalid(tk._(u'Not found'))
        return row.id
    return validator


# --- plain validators ------------------------------------------------------ #

c4w_country_code = _wrap(checks.check_country_code)
c4w_language_code = _wrap(checks.check_language_code)
c4w_doi = _wrap(checks.check_doi)
c4w_safe_url = _wrap(checks.check_url)
c4w_email = _wrap(checks.check_email)
c4w_date = _wrap(checks.check_date)
c4w_must_be_true = _wrap(checks.check_true)
c4w_bins = _wrap(checks.check_bins)
c4w_slug = _wrap(checks.check_slug)


def c4w_sanitized_html(value):
    """Strip a description down to the restrictive allowlist."""
    from ckanext.c4w.logic import sanitize
    from ckanext.c4w.text import is_blank_html
    if value is None:
        return u''
    cleaned = sanitize.sanitize_html(u'%s' % value) or u''
    return u'' if is_blank_html(cleaned) else cleaned


def c4w_plain_text(value):
    """A multi-line plain-text value with no markup at all."""
    from ckanext.c4w.text import html_to_text, normalise_text
    if value is None:
        return u''
    return (normalise_text(html_to_text(u'%s' % value) or u'') or u'').strip()


def get_validators():
    return {
        'c4w_country_code': c4w_country_code,
        'c4w_language_code': c4w_language_code,
        'c4w_doi': c4w_doi,
        'c4w_safe_url': c4w_safe_url,
        'c4w_email': c4w_email,
        'c4w_date': c4w_date,
        'c4w_must_be_true': c4w_must_be_true,
        'c4w_bins': c4w_bins,
        'c4w_slug': c4w_slug,
        'c4w_sanitized_html': c4w_sanitized_html,
        'c4w_plain_text': c4w_plain_text,
    }

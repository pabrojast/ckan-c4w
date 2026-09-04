# encoding: utf-8
"""navl schemas for the C4W forms.

One function per form (or per wizard step), each returning a plain
``{field: [validators]}`` dict, built from CKAN's core validators and the
factories in ``logic/validators.py``. Field names follow
``constants.DATASET_FORM_STEPS`` so the scaffold test can assert every
declared field has a rule.
"""
import ckan.plugins.toolkit as tk

from ckanext.c4w import constants
from ckanext.c4w.logic import validators as v


def _core(name):
    return tk.get_validator(name)


def _defaults():
    return {
        'not_empty': _core('not_empty'),
        'ignore_missing': _core('ignore_missing'),
        'ignore_empty': _core('ignore_empty'),
        'unicode_safe': _core('unicode_safe'),
    }


# --- the data wizard ------------------------------------------------------- #

def dataset_step_schema(step):
    """The schema of one wizard step (1, 4 or 5; 2 and 3 are not navl)."""
    c = _defaults()
    if step == 1:
        return {
            'title': [c['not_empty'], c['unicode_safe'], v.max_length(200)],
            'description': [c['not_empty'], v.c4w_sanitized_html,
                            c['not_empty']],
            'keywords': [c['ignore_missing'], v.free_terms(30)],
            'topic': [v.vocabulary_list('topic', min_items=1)],
            'water_type': [v.vocabulary_list('water_type', min_items=1)],
            'water_data_type': [
                v.vocabulary_list('water_data_type', min_items=1)],
            'language': [_core('default')(u'en'), v.c4w_language_code],
            'project_id': [c['ignore_empty'], v.existing('project')],
            'organisation_id': [c['ignore_empty'],
                                v.existing('organisation')],
        }
    if step == 3:
        return {
            'layout': [v.choice([t for t, _l in constants.DATASET_LAYOUTS])],
            'grain': [v.choice([t for t, _l in constants.DATASET_GRAINS])],
            'unit_note': [c['ignore_missing'], v.c4w_plain_text,
                          v.max_length(1000)],
        }
    if step == 4:
        return {
            'license_id': [c['not_empty'], v.license_id()],
            'frequency': [c['ignore_empty'], v.vocabulary('frequency')],
            'temporal_start': [c['ignore_empty'], v.c4w_date],
            'temporal_end': [c['ignore_empty'], v.c4w_date,
                             v.end_after('temporal_start')],
            'country': [v.country_code_list(20, min_items=1)],
            'geographic_extent': [c['ignore_empty'],
                                  v.vocabulary('geographic_extent')],
            'source_url': [c['ignore_empty'], v.c4w_safe_url],
            'doi': [c['ignore_empty'], v.c4w_doi],
            'citation': [c['ignore_missing'], v.c4w_plain_text,
                         v.max_length(500)],
            'provenance': [c['not_empty'], v.c4w_sanitized_html,
                           c['not_empty']],
            'methodology': [c['ignore_missing'], v.c4w_sanitized_html],
            'technology_used': [c['ignore_missing'],
                                v.vocabulary_list('technology_used')],
            'data_quality_note': [c['ignore_missing'],
                                  v.c4w_sanitized_html],
            'related_urls': [c['ignore_missing'], v.url_list(5)],
        }
    if step == 5:
        return {
            'contact_name': [c['not_empty'], c['unicode_safe'],
                             v.max_length(200)],
            'contact_email': [c['not_empty'], v.c4w_email],
            'contact_url': [c['ignore_empty'], v.c4w_safe_url],
            'author': [c['ignore_missing'], c['unicode_safe'],
                       v.max_length(300)],
            'author_email': [c['ignore_empty'], v.c4w_email],
            'publisher': [c['ignore_missing'], c['unicode_safe'],
                          v.max_length(300)],
            'attribution_text': [c['ignore_missing'], v.c4w_plain_text,
                                 v.max_length(1000)],
            'terms_accepted': [v.c4w_must_be_true],
            'licence_confirm': [v.c4w_must_be_true],
        }
    return {}


def dataset_full_schema():
    """Everything the navl steps check, for the submit-time re-validation."""
    schema = {}
    for step in (1, 3, 4, 5):
        schema.update(dataset_step_schema(step))
    return schema


# --- registration and login ------------------------------------------------ #

def registration_citizen_schema():
    c = _defaults()
    return {
        'fullname': [c['not_empty'], c['unicode_safe'], v.max_length(200)],
        'email': [c['not_empty'], v.c4w_email],
        'username': [c['ignore_empty'], c['unicode_safe'],
                     v.max_length(100)],
        'password': [c['not_empty'],
                     v.passwords_match('password_confirm')],
        'password_confirm': [c['ignore_missing']],
        'country': [c['ignore_empty'], v.c4w_country_code],
        'organisation_text': [c['ignore_missing'], c['unicode_safe'],
                              v.max_length(300)],
        'terms': [v.c4w_must_be_true],
        'recaptcha_response': [c['ignore_missing']],
    }


def registration_manager_schema():
    c = _defaults()
    schema = registration_citizen_schema()
    schema.update({
        'org_choice': [v.choice(('existing', 'new'))],
        'ckan_org_id': [c['ignore_empty'], c['unicode_safe'],
                        v.max_length(100)],
        'org_name_requested': [c['ignore_missing'], c['unicode_safe'],
                               v.max_length(200)],
        'org_type': [c['ignore_empty'], v.vocabulary('org_type')],
        'org_url': [c['ignore_empty'], v.c4w_safe_url],
        'job_title': [c['ignore_missing'], c['unicode_safe'],
                      v.max_length(200)],
        'responsibilities': [v.c4w_must_be_true],
    })
    return schema


def login_schema():
    c = _defaults()
    return {
        'login': [c['not_empty'], c['unicode_safe'], v.max_length(254)],
        'password': [c['not_empty'], c['unicode_safe']],
        'remember': [c['ignore_missing']],
        'came_from': [c['ignore_missing']],
    }


def validate(data, schema, context=None):
    """``(data, errors)`` -- a thin alias so views import one name."""
    return tk.navl_validate(dict(data or {}), schema, context or {})

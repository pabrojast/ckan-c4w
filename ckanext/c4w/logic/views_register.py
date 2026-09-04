# encoding: utf-8
"""Registration, verification and resend, in the portal's chrome."""
import logging

from flask import request, Response

import ckan.plugins.toolkit as tk

from ckanext.c4w import constants
from ckanext.c4w.logic import forms, ratelimit
from ckanext.c4w.logic import helpers as c4w_helpers
from ckanext.c4w.logic.access import current_userobj

log = logging.getLogger(__name__)

_MULTI = ()


def _too_many(retry):
    response = Response(
        tk._(u'Too many attempts. Please try again in a moment.'),
        status=429, mimetype='text/plain')
    response.headers['Retry-After'] = str(retry)
    return response


def _organisation_options():
    """``[(id, title)]`` of CKAN organisations, for the manager form."""
    try:
        rows = tk.get_action('organization_list')(
            {'ignore_auth': True}, {'all_fields': True, 'limit': 1000})
    except Exception:
        return []
    return sorted(((r['id'], r.get('title') or r['name']) for r in rows),
                  key=lambda pair: pair[1].casefold())


def _render(kind, data, errors, pending=False, mail_sent=True):
    from ckanext.c4w.logic.action import registration as reg
    extra_vars = {
        'kind': kind,
        'data': data or {},
        'errors': forms.errors_for_template(errors),
        'pending_verification': pending,
        'mail_sent': mail_sent,
        'recaptcha_publickey': (tk.config.get('ckan.recaptcha.publickey')
                                if reg.recaptcha_configured() else None),
        'country_options': c4w_helpers.c4w_country_options(),
        'org_type_options': c4w_helpers.c4w_option_list('org_type'),
        'organisation_options': (_organisation_options()
                                 if kind == 'manager' else []),
    }
    return tk.render('c4w/register.html', extra_vars=extra_vars)


def choose():
    return tk.render('c4w/register_choose.html', extra_vars={
        'user': current_userobj(),
    })


def register(kind):
    if current_userobj() is not None:
        tk.h.flash_notice(tk._('You are already signed in.'))
        from flask import redirect
        return redirect(c4w_helpers.c4w_url('account'))
    if request.method == 'GET':
        return _render(kind, {'org_choice': 'existing'}, {})
    retry = ratelimit.retry_after('register')
    if retry:
        return _too_many(retry)
    data = forms.parse_form_data(request.form, multi=_MULTI)
    action = ('c4w_register_manager' if kind == 'manager'
              else 'c4w_register_citizen')
    try:
        result = tk.get_action(action)({}, data)
    except tk.ValidationError as exc:
        return _render(kind, forms.echo_values(data), exc.error_dict)
    except tk.NotAuthorized:
        return _render(kind, forms.echo_values(data),
                       {'message': [tk._('Registration is closed.')]})
    return _render(kind, {'email': data.get('email'),
                          'fullname': data.get('fullname')}, {},
                   pending=True, mail_sent=result.get('mail_sent', True))


def verify(token):
    result = tk.get_action('c4w_verify_email')({}, {'token': token})
    return tk.render('c4w/verify_result.html', extra_vars={
        'state': result.get('state'),
        'profile_type': result.get('profile_type'),
    })


def resend():
    if request.method == 'GET':
        return tk.render('c4w/verify_resend.html', extra_vars={
            'sent': False, 'email': request.args.get('email') or u''})
    retry = ratelimit.retry_after('resend')
    if retry:
        return _too_many(retry)
    email = (request.form.get('email') or u'').strip()
    try:
        tk.get_action('c4w_verification_resend')({}, {'email': email})
    except Exception:
        log.warning("ckanext-c4w: verification resend failed", exc_info=True)
    return tk.render('c4w/verify_resend.html', extra_vars={
        'sent': True, 'email': email})

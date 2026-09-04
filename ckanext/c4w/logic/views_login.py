# encoding: utf-8
"""The portal's own login page.

CKAN's ``user.login`` renders in the IHP-WINS chrome, which is exactly the
page a Citizens4Water visitor should never land on. This view does what
``ckan.views.user.login`` does -- the IAuthenticator loop, the
authenticator, ``login_user``, the CSRF token rotation -- inside the
portal's template. Logout stays with CKAN: it honours ``came_from``.
"""
import logging

from flask import redirect, request

import ckan.plugins as plugins
import ckan.plugins.toolkit as tk

from ckanext.c4w.logic import forms, ratelimit
from ckanext.c4w.logic import helpers as c4w_helpers
from ckanext.c4w.logic.access import current_userobj, safe_next

log = logging.getLogger(__name__)


def _render(data, errors):
    return tk.render('c4w/login.html', extra_vars={
        'data': data or {},
        'errors': forms.errors_for_template(errors),
        'came_from': safe_next(request.values.get('came_from'),
                               c4w_helpers.c4w_url('account')),
    })


def login():
    # An SSO plugin that answers login() wins, exactly as in core.
    for item in plugins.PluginImplementations(plugins.IAuthenticator):
        response = item.login()
        if response:
            return response

    default_target = c4w_helpers.c4w_url('account')
    if current_userobj() is not None:
        return redirect(safe_next(request.values.get('came_from'),
                                  default_target))
    if request.method == 'GET':
        return _render({}, {})

    from ckanext.c4w.logic import schema as schemas
    data = forms.parse_form_data(request.form)
    validated, errors = schemas.validate(data, schemas.login_schema(), {})
    if errors:
        return _render(forms.echo_values(data), errors)

    retry = ratelimit.retry_after(
        'login', key=u'%s|%s' % (request.remote_addr or u'',
                                 validated['login'].lower()[:120]))
    if retry:
        tk.h.flash_error(tk._(u'Too many attempts. Please wait a moment.'))
        return _render(forms.echo_values(data), {})

    from ckan.lib import authenticator
    user_obj = authenticator.ckan_authenticator({
        'login': validated['login'], 'password': validated['password']})
    if not user_obj:
        tk.h.flash_error(tk._(u'Login failed. Bad username or password.'))
        return _render(forms.echo_values(data), {})

    if validated.get('remember'):
        from datetime import timedelta
        tk.login_user(user_obj, remember=True, duration=timedelta(days=30))
    else:
        tk.login_user(user_obj)
    try:
        from ckan.views.user import rotate_token
        rotate_token()
    except Exception:
        log.debug("ckanext-c4w: could not rotate the CSRF token",
                  exc_info=True)
    return redirect(safe_next(validated.get('came_from'), default_target))

# encoding: utf-8
"""Auth gates for the C4W account and admin views.

Views call these instead of reading ``g.userobj`` inline so the login
redirect (anonymous) and the 403 (signed in, not a sysadmin) stay one
decision, not two copies that can drift.
"""
from flask import redirect, request

import ckan.plugins.toolkit as tk

from ckanext.c4w.logic import helpers as c4w_helpers


def current_userobj():
    """The CKAN user object, or None for an anonymous visitor."""
    return (getattr(tk.g, 'userobj', None)
            or getattr(tk.c, 'userobj', None))


def require_user():
    """Redirect to CKAN login, or return the user object.

    ``came_from`` is the page they asked for, so after signing in they land
    back inside C4W rather than on the IHP-WINS home.
    """
    user = current_userobj()
    if user is not None:
        return user, None
    came_from = request.full_path or request.path or c4w_helpers.c4w_url('index')
    if came_from.endswith('?'):
        came_from = came_from[:-1]
    return None, redirect(tk.url_for('user.login', came_from=came_from))


def require_sysadmin():
    """Login redirect if anonymous; 403 if signed in but not a sysadmin.

    A 403 for a non-sysadmin is deliberate: the moderation queue is not a
    public existence oracle, and a redirect-to-login would imply they only
    need an account.
    """
    user, bounced = require_user()
    if bounced is not None:
        return None, bounced
    if not getattr(user, 'sysadmin', False):
        tk.abort(403, tk._('Not authorized'))
    return user, None

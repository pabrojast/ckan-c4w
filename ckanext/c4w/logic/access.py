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
    """The real CKAN user, or None for an anonymous visitor.

    CKAN 2.10 puts an AnonymousUser on ``g.userobj``. It is not None and
    is truthy, so a bare ``if userobj`` treats a stranger as signed in
    and the next ``get_action`` 500s on NotAuthorized.
    """
    user = (getattr(tk.g, 'userobj', None)
            or getattr(tk.c, 'userobj', None))
    if user is None:
        return None
    if getattr(user, 'is_anonymous', False):
        return None
    if not getattr(user, 'id', None):
        return None
    return user


def safe_next(target, default=None):
    """A local redirect target, or ``default``.

    Only a path that starts with a single ``/`` is honoured: ``//evil`` is a
    protocol-relative URL to a browser, and an absolute URL would turn every
    form on the portal into an open redirect.
    """
    default = default or c4w_helpers.c4w_url('index')
    if not target:
        return default
    target = u'%s' % target
    if not target.startswith('/') or target.startswith('//') \
            or target.startswith('/\\'):
        return default
    if any(ch in target for ch in ('\r', '\n', '\x00')):
        return default
    return target


def require_user():
    """Redirect to the C4W login, or return the user object.

    ``came_from`` is the page they asked for, so after signing in they land
    back inside C4W rather than on the IHP-WINS home.
    """
    user = current_userobj()
    if user is not None:
        return user, None
    came_from = request.full_path or request.path or c4w_helpers.c4w_url('index')
    if came_from.endswith('?'):
        came_from = came_from[:-1]
    return None, redirect(c4w_helpers.c4w_login_url(came_from))


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

# encoding: utf-8
"""Signed-in surfaces: my submissions and the submit chooser."""
import ckan.plugins.toolkit as tk

from ckanext.c4w import constants
from ckanext.c4w.logic.access import require_user


def account():
    user, bounced = require_user()
    if bounced is not None:
        return bounced
    try:
        listing = tk.get_action('c4w_my_submissions')({}, {})
    except tk.NotAuthorized:
        tk.abort(403, tk._('Not authorized'))
    return tk.render('c4w/account.html', extra_vars={
        'groups': listing.get('groups') or [],
        'user': user,
    })


def submit():
    user, bounced = require_user()
    if bounced is not None:
        return bounced
    return tk.render('c4w/submit.html', extra_vars={
        'choices': constants.SUBMIT_CHOICES,
        'endpoints': constants.SUBMIT_ENDPOINTS,
        'user': user,
    })

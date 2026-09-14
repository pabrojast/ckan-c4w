# encoding: utf-8
"""Signed-in surfaces: my submissions and the submit chooser."""
import ckan.plugins.toolkit as tk
from flask import request, redirect

from ckanext.c4w import constants
from ckanext.c4w.logic.access import require_user


def account():
    user, bounced = require_user()
    if bounced is not None:
        return bounced
    if request.method == 'POST':
        tk.get_action('c4w_profile_preferences_update')({}, dict(request.form))
        tk.h.flash_success(tk._('Profile preference saved.'))
        from ckanext.c4w.logic.helpers import c4w_url
        return redirect(c4w_url('account'))
    try:
        listing = tk.get_action('c4w_my_submissions')({}, {})
    except tk.NotAuthorized:
        tk.abort(403, tk._('Not authorized'))
    return tk.render('c4w/account.html', extra_vars={
        'groups': listing.get('groups') or [],
        'user': user,
        'profile': tk.get_action('c4w_user_profile_show')({}, {}),
    })


def submit():
    user, bounced = require_user()
    if bounced is not None:
        return bounced
    return tk.render('c4w/submit.html', extra_vars={
        'choices': [row for key in ('project', 'dataset', 'post', 'event') for row in constants.SUBMIT_CHOICES if row[0] == key],
        'endpoints': constants.SUBMIT_ENDPOINTS,
        'user': user,
    })

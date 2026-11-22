# encoding: utf-8
"""The public "Claim this project" form.

Orchestration only: the action validates, records and notifies. Reviewing
happens in the ckanext-colab inbox, not here.
"""
from flask import redirect, request

import ckan.plugins.toolkit as tk

from ckanext.c4w.logic import helpers as c4w_helpers
from ckanext.c4w.logic.access import require_user

FIELDS = ('role_in_project', 'organisation', 'contact_email',
          'evidence_url', 'reason')


def project_claim(slug):
    user, response = require_user()
    if response is not None:
        return response
    try:
        project = tk.get_action('c4w_project_show')({}, {'id': slug})
    except tk.ObjectNotFound:
        return tk.abort(404, tk._('Project not found'))
    detail = c4w_helpers.c4w_url('project_detail', slug=project['slug'])
    if project.get('can_edit'):
        tk.h.flash_notice(tk._('You can already edit this project.'))
        return redirect(detail)

    mine = tk.get_action('c4w_project_claim_mine')({}, {
        'project': project['id']})
    values = {'contact_email': user.email or u'',
              'organisation': u'', 'role_in_project': u''}
    errors = {}
    if request.method == 'POST':
        values = {key: request.form.get(key, u'') for key in FIELDS}
        try:
            tk.get_action('c4w_project_claim_create')({}, dict(
                values, project=project['id']))
        except tk.ValidationError as exc:
            errors = exc.error_dict
        else:
            tk.h.flash_success(tk._(
                'Thank you. Your request was sent to the Citizens4Water '
                'team, who will reply by e-mail.'))
            return redirect(detail)
    return tk.render('c4w/project_claim.html', extra_vars={
        'project': project, 'values': values, 'errors': errors,
        'mine': mine,
    })

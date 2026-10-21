"""Search existing accounts and manage their C4W administrator role."""
from flask import redirect, request
import ckan.plugins.toolkit as tk
from ckanext.c4w import db
from ckanext.c4w.logic import helpers
from ckanext.c4w.logic.access import require_sysadmin


def admin_users():
    user, bounced = require_sysadmin()
    if bounced is not None:
        return bounced
    if user.state != 'active':
        tk.abort(403, tk._('Not authorized'))
    q = str(request.values.get('q') or '').strip()[:100]
    errors = {}
    if request.method == 'POST':
        try:
            result = tk.get_action('c4w_admin_user_update')({}, {
                'id': request.form.get('id'), 'operation': request.form.get('operation')})
        except tk.ValidationError as exc:
            db.Session.rollback()
            errors = exc.error_dict
        else:
            tk.h.flash_success(tk._('C4W administrator added.') if result['assigned']
                               else tk._('C4W administrator removed.'))
            return redirect(helpers.c4w_url('admin_users', q=q) if q else helpers.c4w_url('admin_users'))
    listing = tk.get_action('c4w_admin_user_list')({}, {'q': q})
    return tk.render('c4w/admin_users.html', extra_vars=dict(listing=listing, errors=errors))

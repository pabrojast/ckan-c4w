"""Explicit C4W administrator assignments, managed by system administrators."""
import logging

import sqlalchemy as sa
import ckan.model as model
import ckan.plugins.toolkit as tk
from ckanext.c4w import db
from ckanext.c4w.logic import integrity

log = logging.getLogger(__name__)


def _user(user, role=None):
    active = bool(user and user.state == 'active')
    email = (user.email or '').strip() if user else ''
    return dict(id=user.id if user else role.user_id,
                name=user.name if user else '',
                fullname=user.fullname if user else '', email=email,
                active=active, sysadmin=bool(user and user.sysadmin),
                assigned=role is not None, receives_notifications=role is not None and active and bool(email),
                granted_by=role.granted_by if role else None,
                created=role.created.isoformat() if role and role.created else None)


@tk.side_effect_free
def c4w_admin_user_list(context, data_dict):
    tk.check_access('c4w_admin_user_list', context, data_dict)
    db.ensure_mappers()
    assigned = db.Session.query(db.C4wPortalAdmin, model.User).outerjoin(
        model.User, model.User.id == db.C4wPortalAdmin.user_id).order_by(model.User.name).all()
    admins = [_user(user, role) for role, user in assigned]
    q = str(data_dict.get('q') or '').strip()[:100]
    results, more = [], False
    if len(q) >= 2:
        escaped = q.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')
        pattern = '%' + escaped + '%'
        rows = db.Session.query(model.User).filter(model.User.state == 'active', sa.or_(
            model.User.name.ilike(pattern, escape='\\'),
            model.User.fullname.ilike(pattern, escape='\\'),
            model.User.email.ilike(pattern, escape='\\'))).order_by(model.User.name).limit(21).all()
        roles = {role.user_id: role for role, _ in assigned}
        results = [_user(user, roles.get(user.id)) for user in rows[:20]]
        more = len(rows) > 20
    return dict(administrators=admins, results=results, q=q, more=more,
                notification_count=sum(user['receives_notifications'] for user in admins))


def c4w_admin_user_update(context, data_dict):
    tk.check_access('c4w_admin_user_update', context, data_dict)
    db.ensure_mappers()
    operation = data_dict.get('operation')
    if operation not in ('grant', 'revoke'):
        raise tk.ValidationError({'operation': [tk._('Choose Add administrator or Remove administrator.')]})
    user_id = str(data_dict.get('id') or '')
    if not user_id or len(user_id) > 100:
        raise tk.ValidationError({'id': [tk._('Choose an existing account.')]})
    # Serialize repeated grants and explicit revocations, including API calls.
    integrity.lock(db.Session)
    user = db.Session.query(model.User).filter_by(id=user_id).populate_existing().with_for_update().first()
    role = db.Session.query(db.C4wPortalAdmin).filter_by(user_id=user_id).with_for_update().first()
    if operation == 'grant' and (not user or user.state != 'active'):
        raise tk.ValidationError({'id': [tk._('Only an active existing account can be added.')]})
    if not user and not role:
        raise tk.ValidationError({'id': [tk._('This account no longer exists.')]})
    changed = False
    actor = context['auth_user_obj']
    if operation == 'grant' and role is None:
        role = db.C4wPortalAdmin(user_id=user_id, granted_by=actor.id)
        db.Session.add(role)
        changed = True
    elif operation == 'revoke' and role is not None:
        db.Session.delete(role)
        changed = True
    db.Session.commit()
    if changed:
        log.info('C4W administrator %s: actor=%s target=%s', operation, actor.id, user_id)
    return dict(id=user_id, operation=operation, assigned=operation == 'grant', changed=changed)


@tk.auth_sysadmins_check
@tk.auth_allow_anonymous_access
def _auth(context, data_dict):
    user = context.get('auth_user_obj')
    return {'success': bool(user and getattr(user, 'id', None)
                            and not getattr(user, 'is_anonymous', False)
                            and getattr(user, 'sysadmin', False)
                            and getattr(user, 'state', None) == 'active')}


def get_actions():
    return {fn.__name__: fn for fn in (c4w_admin_user_list, c4w_admin_user_update)}


def get_auth_functions():
    return {name: _auth for name in get_actions()}

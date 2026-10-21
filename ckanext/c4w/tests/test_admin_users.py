"""C4W role assignment, private account search and immediate permission changes."""
import pytest
pytest.importorskip('ckan')
import ckan.model as model
import ckan.plugins.toolkit as tk
from flask import Flask
from ckanext.c4w import db
from ckanext.c4w.logic import mail, permissions, auth
from ckanext.c4w.logic.action import admins
from ckanext.c4w.tests.test_project_proposals import env as base_env


@pytest.fixture
def env(base_env, monkeypatch):
    def access(name, context, data=None):
        if not auth.get_auth_functions()[name](context, data or {})['success']:
            raise tk.NotAuthorized('Denied')
    monkeypatch.setattr(tk, 'check_access', access)
    return base_env


def change(env, user='owner', operation='grant', **extra):
    return admins.c4w_admin_user_update(env.context['admin'], dict(id=user, operation=operation, **extra))


@pytest.mark.parametrize('role', ['anonymous', 'member', 'c4w', 'inactive_sysadmin'])
def test_only_active_system_admin_can_list_search_or_assign(env, role):
    context = {} if role == 'anonymous' else env.context['owner']
    if role == 'c4w':
        env.session.add(db.C4wPortalAdmin(user_id='owner', granted_by='admin'))
        env.session.commit()
    if role == 'inactive_sysadmin':
        context = env.context['admin']; env.users['admin'].state = 'deleted'
    for action, data in ((admins.c4w_admin_user_list, {'q':'other'}),
                         (admins.c4w_admin_user_update, {'id':'other', 'operation':'grant'}),
                         (admins.c4w_admin_user_update, {'id':'owner', 'operation':'revoke'})):
        with pytest.raises(tk.NotAuthorized): action(context, data)
    assert not env.session.query(db.C4wPortalAdmin).filter_by(user_id='other').first()


def test_grant_and_revoke_change_content_permissions_and_notification_recipients_only(env):
    owner=env.users['owner']; before=(owner.password, owner.email, owner.state, owner.sysadmin)
    assert not permissions.is_content_admin(owner)
    change(env, sysadmin=True, email='replaced@example.org')
    assert permissions.is_content_admin(owner)
    assert mail.moderation_recipients()==[('owner','owner@example.org')]
    role=env.session.query(db.C4wPortalAdmin).one()
    assert role.granted_by=='admin' and role.created
    assert (owner.password,owner.email,owner.state,owner.sysadmin)==before
    assert change(env)['changed'] is False
    change(env, operation='revoke')
    assert not permissions.is_content_admin(owner) and not mail.moderation_recipients()
    assert change(env, operation='revoke')['changed'] is False
    assert (owner.password,owner.email,owner.state,owner.sysadmin)==before


def test_system_admin_can_opt_in_to_notifications_and_keeps_global_role_on_removal(env):
    change(env, 'admin')
    assert mail.moderation_recipients()==[('admin','admin@example.org')]
    change(env, 'admin', 'revoke')
    assert permissions.is_content_admin(env.users['admin']) and env.users['admin'].sysadmin
    assert not mail.moderation_recipients()


def test_search_matches_names_and_email_filters_inactive_and_bounds_results(env):
    env.users['owner'].fullname='River Team'
    env.users['other'].email='river@example.org';env.users['editor'].state='deleted'
    env.session.add_all([model.User(id='result-%s'%i,name='result-%02d'%i,state='active',email='result-%s@example.org'%i) for i in range(25)])
    env.session.commit()
    search=lambda q:admins.c4w_admin_user_list(env.context['admin'], {'q':q})
    assert {u['id'] for u in search('RIVER')['results']}=={'owner','other'}
    assert [u['id'] for u in search('own')['results']]==['owner']
    assert not search('editor')['results']
    assert not search('')['results'] and not search('%_')['results']
    assert len(search('result')['results'])==20 and search('result')['more']
    assert set(search('own')['results'][0])=={'id','name','fullname','email','active','sysadmin','assigned','receives_notifications','granted_by','created'}


def test_inactive_or_missing_target_cannot_be_granted_and_inactive_role_can_be_removed(env):
    change(env)
    env.users['owner'].state='deleted';env.session.commit()
    for target in ('owner','missing',''):
        with pytest.raises(tk.ValidationError):change(env,target)
    assert not mail.moderation_recipients()
    listing=admins.c4w_admin_user_list(env.context['admin'], {})
    assert not listing['administrators'][0]['active']
    change(env,operation='revoke')
    assert env.session.query(db.C4wPortalAdmin).count()==0


def test_missing_email_is_visible_and_does_not_receive_notifications(env):
    env.users['owner'].email=None;env.session.commit();change(env)
    listing=admins.c4w_admin_user_list(env.context['admin'], {'q':'owner'})
    assert listing['results'][0]['assigned']
    assert listing['notification_count']==0 and not mail.moderation_recipients()
    assert not listing['administrators'][0]['receives_notifications']
    with pytest.raises(tk.ValidationError):change(env,operation='toggle')


def test_administrator_route_is_separate_from_content_inventory():
    from ckanext.c4w import blueprint
    app=Flask(__name__)
    for bp in blueprint.get_blueprints():app.register_blueprint(bp)
    adapter=app.url_map.bind('example.org')
    for method in ('GET','POST'):
        endpoint,_=adapter.match('/citizens4water/admin/administrators',method=method)
        assert endpoint=='c4w.admin_users'

"""Permanent deletion, dependency safety and C4W-only administration."""
import json
from types import SimpleNamespace

import pytest
pytest.importorskip('ckan')
import ckan.plugins.toolkit as tk
from ckanext.c4w import constants, db
from ckanext.c4w.logic import permissions, auth
from ckanext.c4w.logic.action import deletion, moderation, registration, datasets
from ckanext.c4w.tests.test_admin_platforms import env
from ckanext.c4w.tests.test_admin_inventory import item


def args(context, entity, row):
    preview = deletion.c4w_entity_delete_preview(context, {'entity_type': entity, 'id': row.id})
    return dict(entity_type=entity, id=row.id, version=preview['version'], confirm_slug=row.slug)


def portal_admin(env):
    user = SimpleNamespace(id='local-admin', state='active', sysadmin=False)
    env.session.add(db.C4wPortalAdmin(user_id=user.id, granted_by='admin'))
    env.session.commit()
    return {'auth_user_obj': user}


@pytest.mark.parametrize('section,entity,label', constants.ADMIN_SECTIONS)
def test_permanent_delete_all_surfaces_leaves_audit(env, section, entity, label):
    context = portal_admin(env)
    _, row = item(env, section, 'hidden-item', hidden=True)
    _, other = item(env, section, 'keep-me')
    target = row.id
    env.session.add(db.C4wTermLink(entity_type=entity, entity_id=target, vocabulary='topic', term='water'))
    env.session.add(db.C4wRelation(subject_type=entity, subject_id=target, predicate='editor', object_type='user', object_id='owner'))
    if entity == 'project':
        env.session.add(db.C4wProjectRevision(project_id=target, created_by='owner'))
    if entity in ('post', 'event'):
        env.session.add(db.C4wContentRevision(entity_type=entity, entity_id=target, created_by='owner'))
    env.session.commit()
    result = deletion.c4w_entity_delete(context, args(context, entity, row))
    assert result['deleted'] and result['cleanup_pending'] == 0
    assert env.session.query(type(row)).filter_by(id=target).first() is None
    assert env.session.query(type(other)).filter_by(id=other.id).one()
    assert env.session.query(db.C4wTermLink).filter_by(entity_id=target).count() == 0
    assert env.session.query(db.C4wRelation).filter_by(subject_id=target).count() == 0
    assert env.session.query(db.C4wDeletion).one().deleted_by == 'local-admin'
    assert env.session.query(db.C4wProjectRevision).filter_by(project_id=target).count() == 0
    assert env.session.query(db.C4wContentRevision).filter_by(entity_id=target).count() == 0


def test_preview_never_deletes_and_requires_admin(env):
    entity, row = item(env, 'projects', 'keep', hidden=True)
    for role in ('anonymous', 'member'):
        for fn in (deletion.c4w_entity_delete_preview, deletion.c4w_entity_delete):
            with pytest.raises(tk.NotAuthorized): fn(env.contexts[role], {'entity_type': entity, 'id': row.id})
    for _ in range(2):
        assert deletion.c4w_entity_delete_preview(env.contexts['admin'], {'entity_type':entity,'id':row.id})['can_delete']
    assert env.session.query(type(row)).count() == 1


@pytest.mark.parametrize('change', ['visible', 'wrong_slug', 'edited', 'new_revision', 'processing', 'queued'])
def test_invalid_or_stale_confirmation_is_blocked(env, change):
    context = env.contexts['admin']
    entity, row = item(env, 'data' if change in ('processing', 'queued') else 'projects', 'hidden', hidden=True)
    data = args(context, entity, row)
    if change == 'visible': row.hidden = False
    elif change == 'wrong_slug': data['confirm_slug'] = 'other'
    elif change == 'edited': row.name = 'changed'
    elif change == 'new_revision': env.session.add(db.C4wProjectRevision(project_id=row.id, created_by='owner'))
    else: row.processing_status = change
    env.session.commit()
    with pytest.raises(tk.ValidationError): deletion.c4w_entity_delete(context, data)
    assert env.session.query(type(row)).filter_by(id=row.id).one()
    assert env.session.query(db.C4wDeletion).count() == 0


@pytest.mark.parametrize('kind', ['relation', 'direct', 'draft', 'profile'])
def test_dependencies_prevent_deleting_parent_without_touching_child(env, kind):
    context = env.contexts['admin']
    entity, row = item(env, 'organisations', 'parent', hidden=True)
    _, child = item(env, 'projects', 'child')
    data = args(context, entity, row)
    if kind == 'relation': env.session.add(db.C4wRelation(subject_type='project', subject_id=child.id, predicate='organisation', object_type=entity, object_id=row.id))
    elif kind == 'direct': child.main_organisation_id = row.id
    elif kind == 'draft': env.session.add(db.C4wProjectRevision(project_id=child.id,created_by='owner',status='pending',payload=json.dumps({'organisation':[row.id]})))
    else: env.session.add(db.C4wUserProfile(user_id='owner',c4w_organisation_id=row.id))
    env.session.commit()
    preview = deletion.c4w_entity_delete_preview(context, data)
    assert not preview['can_delete'] and len(preview['dependencies']) == 1
    with pytest.raises(tk.ValidationError): deletion.c4w_entity_delete(context, data)
    assert env.session.query(type(child)).filter_by(id=child.id).one()


def test_dataset_files_cleanup_can_be_retried_and_shared_files_survive(env, monkeypatch):
    from ckanext.c4w.logic import uploads
    context = env.contexts['admin']
    entity, row = item(env, 'data', 'hidden', hidden=True)
    _, other = item(env, 'data', 'shared')
    for parent, name in [(row,'exclusive.csv'),(row,'shared.csv'),(other,'shared.csv')]:
        env.session.add(db.C4wDatasetFile(dataset_id=parent.id, stored_name=name, url='https://example.org/'+name, original_name=name))
    env.session.add(db.C4wDashboardBundle(dataset_id=row.id, generation=1, name='meta',body=b'{}'))
    env.session.commit()
    attempts=[]
    monkeypatch.setattr(uploads,'delete_stored',lambda name: attempts.append(name) or False)
    result=deletion.c4w_entity_delete(context,args(context,entity,row))
    assert result['cleanup_pending']==1 and attempts==['exclusive.csv']
    assert env.session.query(db.C4wDatasetFile).count()==1
    assert env.session.query(db.C4wDashboardBundle).count()==0
    monkeypatch.setattr(uploads,'delete_stored',lambda name: attempts.append(name) or True)
    assert deletion.c4w_deletion_cleanup(context,{'id':result['deletion_id']})['cleanup_pending']==0
    assert attempts==['exclusive.csv','exclusive.csv']


def test_content_admin_is_not_global_admin_or_organisation_reviewer(env):
    context=portal_admin(env)
    user=context['auth_user_obj']
    assert permissions.is_content_admin(user) and user.sysadmin is False
    assert moderation.c4w_moderation_list_auth(context,{})['success']
    assert not registration._sysadmin(context,{})['success']
    assert not auth._is_sysadmin(context)
    user.state='deleted'
    assert not permissions.is_content_admin(user)


def test_deleted_target_cannot_be_relinked(env):
    context=env.contexts['admin']
    entity, row=item(env,'organisations','removed',hidden=True)
    target=row.id
    deletion.c4w_entity_delete(context,args(context,entity,row))
    _, child=item(env,'projects','child')
    child.main_organisation_id=target
    with pytest.raises(tk.ValidationError): env.session.commit()
    env.session.rollback()


def test_old_dataset_api_cannot_bypass_admin_confirmation(env):
    context=portal_admin(env)
    _, row=item(env,'data','visible')
    with pytest.raises(tk.ValidationError): datasets.c4w_dataset_delete(context,{'id':row.id})
    assert env.session.query(db.C4wDataset).filter_by(id=row.id).one()


def test_event_editor_and_project_review_use_portal_role(env):
    from ckanext.c4w.logic.action import content_write, project_write, _common
    context=portal_admin(env)
    assert project_write._admin_auth(context,{})['success']
    result=content_write.c4w_event_admin_save(context,{'mode':'draft','title':'Portal admin event'})
    row=env.session.query(db.C4wEvent).filter_by(id=result['id']).one()
    assert _common.can_edit('event',row,context)
    assert row.created_by=='local-admin' and not row.approved


def test_delete_views_enforce_confirmation_methods():
    from flask import Flask
    from ckanext.c4w.blueprint import get_blueprints
    app=Flask(__name__)
    for bp in get_blueprints(): app.register_blueprint(bp)
    adapter=app.url_map.bind('example.org')
    for method in ('GET','POST'):
        endpoint,values=adapter.match('/citizens4water/admin/projects/123/delete',method=method)
        assert endpoint=='c4w.admin_delete'
        assert values=={'section':'projects','item_id':'123'}
    from werkzeug.exceptions import MethodNotAllowed
    with pytest.raises(MethodNotAllowed): adapter.match('/citizens4water/admin/deletions/123/retry',method='GET')

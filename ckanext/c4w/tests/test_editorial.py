"""End-to-end action contracts for direct editing and imported project revisions."""
import datetime
from io import BytesIO

import pytest
pytest.importorskip('ckan')
import ckan.plugins.toolkit as tk
from werkzeug.datastructures import FileStorage
from ckanext.c4w import db, constants
from ckanext.c4w.logic import project_fields, mail, legacy, helpers
from ckanext.c4w.logic.action import editorial, project_write, moderation, content_write
from ckanext.c4w.tests.test_project_proposals import env as base_env, create, save, submit, approve


@pytest.fixture
def env(base_env, monkeypatch):
    original = tk.check_access
    def access(name, context, data=None):
        if name in editorial.get_auth_functions():
            if not editorial.get_auth_functions()[name](context, data or {})['success']:
                raise tk.NotAuthorized('Denied')
        else:
            original(name, context, data)
    monkeypatch.setattr(tk, 'check_access', access)
    base_env.session.add(db.C4wPortalAdmin(user_id='other', granted_by='admin'))
    base_env.session.commit()
    return base_env


def row_for(env, section, entity):
    cls = db.ENTITY_CLASSES[entity]
    values = dict(id=section, slug=section, hidden=True, legacy_id=11 if section == 'training_resources' else 10, extras='{"retained":"original"}')
    values['name' if hasattr(cls, 'name') else 'title'] = 'Original'
    if entity == 'post':
        values.update(status='published', content='<p>Original article</p>', author_id='owner')
    else:
        values.update(approved=True, created_by='owner')
    if entity == 'project':
        values.update(status='active', description='Description', aim='Aim', author='Retired contact', author_email='retired@example.org')
    elif entity == 'event':
        values.update(description='Description', start_date=datetime.datetime(2026, 10, 21))
    elif entity == 'resource':
        values.update(is_training_resource=section == 'training_resources')
    elif entity == 'dataset':
        values.update(description='Description', processing_status='ready', bundle_generation=7, mapping_json='{"layout":"wide"}', record_count=99)
    row = cls(**values); env.session.add(row); env.session.commit()
    return row


def edit(env, section, row, **values):
    args = dict(section=section, id=row.id)
    state = editorial.c4w_admin_entity_edit_show(env.context['other'], args)
    return editorial.c4w_admin_entity_update(env.context['other'], dict(args, version=state['version'], **values))


@pytest.mark.parametrize('section,entity,label', constants.ADMIN_SECTIONS)
def test_all_eight_editors_preserve_visibility_identity_and_operational_fields(env, section, entity, label):
    row = row_for(env, section, entity)
    before = editorial.snapshot(entity, row)
    key = 'name' if hasattr(row, 'name') else 'title'
    result = edit(env, section, row, **{key:'Changed', 'hidden':False, 'approved':False,
        'created_by':'other', 'slug':'changed-url', 'processing_status':'failed', 'bundle_generation':0})
    assert getattr(row, key) == 'Changed'
    assert row.slug == section and row.hidden
    assert (row.status == 'published' if entity == 'post' else row.approved)
    assert (row.author_id if entity == 'post' else row.created_by) == 'owner'
    assert db.load_extras(row.extras)['retained'] == 'original'
    assert result['version']
    if entity == 'dataset':
        assert row.bundle_generation == 7 and row.processing_status == 'ready' and row.record_count == 99
        assert row.mapping_json == '{"layout":"wide"}'
    assert not env.users['other'].sysadmin


def test_admin_api_authorization_and_stale_form(env):
    row = row_for(env, 'platforms', 'platform')
    args = dict(section='platforms', id=row.id)
    for context in ({}, env.context['owner']):
        with pytest.raises(tk.NotAuthorized): editorial.c4w_admin_entity_edit_show(context, args)
        with pytest.raises(tk.NotAuthorized): editorial.c4w_admin_entity_update(context, args)
    state = editorial.c4w_admin_entity_edit_show(env.context['other'], args)
    edit(env, 'platforms', row, name='Current')
    with pytest.raises(tk.ValidationError):
        editorial.c4w_admin_entity_update(env.context['other'], dict(args, version=state['version'], name='Stale'))
    assert row.name == 'Current'


def test_pending_and_other_authors_drafts_block_direct_publication(env):
    draft = approve(env, submit(env, create(env)))
    draft = save(env, draft, 1, description='Proposed description')
    row = env.session.query(db.C4wProject).filter_by(id=draft['id']).one()
    for pending in (False, True):
        if pending: draft = submit(env, draft)
        state = editorial.c4w_admin_entity_edit_show(env.context['other'], dict(section='projects', id=row.id))
        assert state['blocked']
        with pytest.raises(tk.ValidationError): edit(env, 'projects', row, name='Accidental overwrite')
        env.session.rollback()
        assert row.name == 'River Watch' and row.description == '<p>Community water monitoring</p>'
        assert project_write.c4w_project_draft_show(env.context['owner'], {'id':row.id})['payload']['description'] == 'Proposed description'


def test_imported_participants_languages_and_long_text_survive_image_only_edit(env):
    row = row_for(env, 'projects', 'project')
    row.extras = db.dump_extras(dict(number_of_participants='51-100', water_parameters='x'*16000, target_group='y'*1300))
    env.session.add_all([db.C4wTermLink(entity_type='project',entity_id=row.id,vocabulary='language',term=t,label=t) for t in ['german','english','argentinian-spanish']])
    env.session.commit()
    result = edit(env, 'projects', row, image1_credit='New credit')
    assert result['payload']['number_of_participants'] == '51-100'
    assert set(result['payload']['language']) == {'de','en','argentinian-spanish'}
    assert result['payload']['water_parameters'] == 'x'*16000
    assert result['payload']['target_group'] == 'y'*1300
    with pytest.raises(tk.ValidationError): edit(env, 'projects', row, language=['Invented new language'])
    env.session.rollback()
    with pytest.raises(tk.ValidationError): edit(env, 'projects', row, water_parameters='z'*16000)


def test_contact_removal_and_safe_links(env):
    row = row_for(env, 'projects', 'project')
    result = edit(env, 'projects', row, author='', author_email='', achievements='<p><a href="https://example.org">Details</a><script>bad()</script></p>')
    assert row.author is None and row.author_email is None
    assert '<a href="https://example.org">' in result['payload']['achievements']
    assert 'bad()' not in result['payload']['achievements']
    assert not project_write._snapshot(row)['author']


def test_failed_upload_never_commits_removal_or_other_changes(env, monkeypatch):
    from ckanext.c4w.logic import project_images
    row = row_for(env, 'projects', 'project'); row.image1_url='/original.jpg';env.session.commit()
    def fail(*a, **kw): raise tk.ValidationError({'image1':['Invalid image']})
    monkeypatch.setattr(project_images,'store_image', fail)
    upload=FileStorage(BytesIO(b'bad'),filename='bad.png')
    with pytest.raises(tk.ValidationError): edit(env,'projects',row,name='Must not persist',remove_image1='true',image1=upload)
    assert row.name=='Original' and row.image1_url=='/original.jpg'
    draft=project_write.c4w_project_draft_show(env.context['other'],{'id':row.id})
    with pytest.raises(tk.ValidationError):
        project_write.c4w_project_update(env.context['other'],dict(id=row.id,step=6,version=draft['version'],remove_image1='true',image1=upload))
    env.session.rollback()
    assert project_write.c4w_project_draft_show(env.context['other'],{'id':row.id})['payload']['image1_url']=='/original.jpg'


def test_reject_requires_reason_preserves_public_and_author_can_resubmit(env, monkeypatch):
    draft=approve(env,submit(env,create(env)))
    draft=submit(env,save(env,draft,1,name='Proposed title'))
    args=dict(id=draft['id'],version=draft['version'],decision='rejected')
    with pytest.raises(tk.ValidationError):project_write.c4w_project_review(env.context['admin'],args)
    result=project_write.c4w_project_review(env.context['admin'],dict(args,note='Explain the change'))
    assert result['status']=='rejected'
    assert env.session.query(db.C4wProject).filter_by(id=draft['id']).one().name=='River Watch'
    assert not moderation.c4w_moderation_list(env.context['admin'],{})['project_requests']
    assert submit(env,save(env,result,1,name='Corrected title'))['status']=='pending'


def test_my_submissions_includes_admin_changes_to_someone_elses_project(env):
    row=row_for(env,'projects','project')
    edit(env,'projects',row,author='')
    groups=moderation.c4w_my_submissions(env.context['other'],{})['groups']
    assert any(item['id']==row.id for group in groups for item in group['rows'])


def test_hidden_published_content_is_not_a_review_request(env):
    for section,entity,label in constants.ADMIN_SECTIONS: row_for(env,section,entity)
    listing=moderation.c4w_moderation_list(env.context['other'],{})
    assert not any(g['rows'] for g in listing['groups'])


def test_explicit_active_c4w_email_recipients_only(env):
    env.users['other'].email='moderator@example.org'
    env.users['admin'].email='global@example.org'
    env.session.commit()
    assert mail.moderation_recipients()==[('other','moderator@example.org')]
    env.users['other'].state='deleted';env.session.commit()
    assert mail.moderation_recipients()==[]


def test_imported_legacy_rejection_and_approval(env):
    row=row_for(env,'resources','resource');row.approved=False;env.session.commit()
    args=dict(section='resources',id=row.id)
    state=editorial.c4w_admin_entity_edit_show(env.context['other'],args)
    editorial.c4w_admin_entity_review(env.context['other'],dict(args,version=state['version'],decision='rejected',note='Missing context'))
    assert not row.approved
    assert not any(g['rows'] for g in moderation.c4w_moderation_list(env.context['admin'],{})['groups'])


def test_legacy_coordinate_does_not_block_unrelated_edit_but_new_value_is_validated(env):
    row = row_for(env, 'events', 'event'); row.longitude = 200; env.session.commit()
    edit(env, 'events', row, title='Updated title')
    assert row.longitude == 200
    with pytest.raises(tk.ValidationError): edit(env, 'events', row, longitude='201')


def test_project_notification_failure_keeps_pending_request(env, monkeypatch):
    monkeypatch.setattr(mail, 'notify_moderators', lambda *a, **kw: False)
    draft = submit(env, create(env))
    assert draft['status'] == 'pending' and draft['notification_status'] == 'failed'
    assert moderation.c4w_moderation_list(env.context['other'], {})['project_requests'][0]['id'] == draft['id']


def test_revision_notifications_go_to_submitter_not_legacy_owner(env, monkeypatch):
    row = row_for(env, 'projects', 'project')
    draft = project_write.c4w_project_update(env.context['other'], dict(id=row.id,version=0,step=6,image2_credit='Changed'))
    draft = project_write.c4w_project_submit(env.context['other'], dict(id=row.id,version=draft['version']))
    delivered=[]
    monkeypatch.setattr(mail,'_send',lambda name,email,*rest: delivered.append(email) or True)
    project_write.c4w_project_review(env.context['admin'],dict(id=row.id,version=draft['version'],decision='rejected',note='Please clarify'))
    assert delivered==['other@example.org']


def test_contact_only_edit_preserves_all_other_imported_columns_extras_and_terms(env):
    import sqlalchemy as sa
    row=row_for(env,'projects','project')
    row.extras=db.dump_extras({'number_of_participants':'51-100','untouched':'value'})
    env.session.add(db.C4wTermLink(entity_type='project',entity_id=row.id,vocabulary='language',term='german',label='German'))
    env.session.commit()
    columns=[c.name for c in sa.inspect(type(row)).local_table.c if c.name not in ('author','author_email','modified','search_text')]
    before={name:getattr(row,name) for name in columns}
    edit(env,'projects',row,author='',author_email='')
    assert {name:getattr(row,name) for name in columns}==before
    assert env.session.query(db.C4wTermLink).filter_by(entity_id=row.id,vocabulary='language').one().term=='german'


def test_rejected_revision_is_resolved_and_allows_direct_admin_edit(env):
    draft=approve(env,submit(env,create(env)))
    draft=submit(env,save(env,draft,1,name='Rejected proposal'))
    project_write.c4w_project_review(env.context['admin'],dict(id=draft['id'],version=draft['version'],decision='rejected',note='Incorrect title'))
    row=env.session.query(db.C4wProject).filter_by(id=draft['id']).one()
    edit(env,'projects',row,name='Administrative correction')
    assert row.name=='Administrative correction'

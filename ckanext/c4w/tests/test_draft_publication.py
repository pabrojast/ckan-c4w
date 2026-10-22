"""Administrators can resolve saved contributions without erasing drafts."""
import pytest

pytest.importorskip('ckan')
import ckan.plugins.toolkit as tk
from ckanext.c4w import db
from ckanext.c4w.logic import review
from ckanext.c4w.logic.action import editorial, project_write, content_write
from ckanext.c4w.tests.test_editorial import env as editorial_env
from ckanext.c4w.tests.test_project_proposals import env as base_env
from ckanext.c4w.tests.test_project_proposals import create, save, submit, approve
from ckanext.c4w.tests.test_content_proposals import create as create_content, args


@pytest.fixture
def env(editorial_env, monkeypatch):
    previous = tk.check_access
    auth = content_write.get_auth_functions()
    def check(name, context, data=None):
        if name in auth:
            if not auth[name](context, data or {})['success']:
                raise tk.NotAuthorized('Denied')
        else:
            previous(name, context, data)
    monkeypatch.setattr(tk, 'check_access', check)
    return editorial_env


@pytest.mark.parametrize('actor', ['admin', 'other'])
def test_admin_can_resume_someone_elses_draft_and_publish_without_submission(env, actor):
    draft = approve(env, submit(env, create(env)))
    draft = save(env, draft, 5, author='', author_email='', achievements='Saved work')
    row = env.session.query(db.C4wProject).filter_by(id=draft['id']).one()
    row.hidden = True
    env.session.commit()
    before = editorial.c4w_admin_entity_edit_show(env.context[actor], dict(section='projects', id=row.id))
    assert before['blocked']
    draft = project_write.c4w_project_update(env.context[actor], dict(id=row.id,
        version=draft['version'], step=1, name='Administrative correction', action='review'))
    assert row.name == 'River Watch'
    assert draft['payload']['achievements'] == 'Saved work'
    assert editorial.c4w_admin_entity_edit_show(env.context[actor], dict(section='projects', id=row.id))['blocked']
    result = project_write.c4w_project_review(env.context[actor], dict(id=row.id,
        version=draft['version'], decision='publish_draft'))
    assert result['status'] == 'approved' and row.name == 'Administrative correction'
    assert row.author is None and row.author_email is None and row.hidden
    rev = project_write._revision(row)
    assert rev.created_by == 'owner' and rev.reviewed_by == actor
    assert not editorial.c4w_admin_entity_edit_show(env.context[actor], dict(section='projects', id=row.id))['blocked']


def test_draft_publication_requires_admin_explicit_decision_and_latest_version(env):
    draft = create(env)
    data = dict(id=draft['id'], version=draft['version'], decision='publish_draft')
    for context in ({}, env.context['owner'], env.context['editor']):
        with pytest.raises(tk.NotAuthorized):
            project_write.c4w_project_review(context, data)
    with pytest.raises(tk.ValidationError):
        project_write.c4w_project_review(env.context['other'], dict(data, decision='approve'))
    current = save(env, draft, 1, name='Most recent')
    with pytest.raises(tk.ValidationError):
        project_write.c4w_project_review(env.context['other'], data)
    assert not env.session.query(db.C4wProject).one().approved
    pending = submit(env, current)
    with pytest.raises(tk.ValidationError):
        project_write.c4w_project_review(env.context['other'], dict(data, version=pending['version']))


def test_incomplete_draft_is_preserved_on_failed_publication(env):
    draft = create(env, complete=False)
    before = project_write._revision(env.session.query(db.C4wProject).one()).payload
    with pytest.raises(tk.ValidationError) as error:
        project_write.c4w_project_review(env.context['other'], dict(id=draft['id'],
            version=draft['version'], decision='publish_draft'))
    assert 'description' in error.value.error_dict
    env.session.rollback()
    row = env.session.query(db.C4wProject).one()
    assert not row.approved and project_write._revision(row).payload == before
    assert project_write._revision(row).status == 'draft'


@pytest.mark.parametrize('entity', ['post', 'event'])
def test_content_admin_draft_publication_and_stale_guard(env, entity):
    draft = create_content(env, entity)
    with pytest.raises(tk.NotAuthorized):
        content_write.c4w_content_review(env.context['owner'], args(draft, decision='publish_draft'))
    changed = content_write.c4w_content_update(env.context['other'], args(draft, title='Saved correction'))
    with pytest.raises(tk.ValidationError):
        content_write.c4w_content_review(env.context['other'], args(draft, decision='publish_draft'))
    result = content_write.c4w_content_review(env.context['other'], args(changed, decision='publish_draft'))
    assert result['status'] == 'approved' and result['published']
    assert result['title'] == 'Saved correction'
    with pytest.raises(tk.ValidationError):
        content_write.c4w_content_review(env.context['other'], args(result, decision='publish_draft'))


@pytest.mark.parametrize('kind', ['multi', 'countries', 'languages', 'terms', 'organisations', 'editors'])
def test_comparison_ignores_multiselect_order_but_retains_removals(kind):
    fields = [dict(name='selected', kind=kind, label='Selection'), dict(name='author', kind='text', label='Contact')]
    before = dict(selected=['water', 'climate'], author='Retired contact')
    changes = review.changes(before, dict(selected=['climate', 'water'], author=''), fields)
    assert [c['label'] for c in changes] == ['Contact']
    assert review.changes(before, dict(selected=['water'], author='Retired contact'), fields)[0]['label'] == 'Selection'


@pytest.mark.parametrize('status', ['draft', 'changes_requested'])
def test_explicit_draft_decision(status):
    assert review.decision_for(status, 'publish_draft') == 'approve'
    with pytest.raises(tk.ValidationError):
        review.decision_for(status, 'approve')


@pytest.mark.parametrize('status', ['pending', 'approved', 'rejected'])
def test_draft_publication_cannot_republish_resolved_or_submitted_revision(status):
    with pytest.raises(tk.ValidationError):
        review.decision_for(status, 'publish_draft')

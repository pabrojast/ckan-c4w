"""Project inventory, public visibility and moderation return navigation."""
from types import SimpleNamespace

import pytest

pytest.importorskip('ckan')
import sqlalchemy as sa
from flask import Flask
import ckan.plugins.toolkit as tk
from ckanext.c4w import db
from ckanext.c4w.logic import auth, views_admin
from ckanext.c4w.logic.action import moderation, projects, stats


@pytest.fixture
def env(monkeypatch):
    engine = sa.create_engine('sqlite://')
    db.ensure_mappers()
    db.metadata.create_all(bind=engine, tables=list(db._ALL_TABLES))
    db.Session.remove()
    db.Session.configure(bind=engine)
    monkeypatch.setattr(tk, '_', lambda text: text)
    permissions = auth.get_auth_functions()

    def check_access(name, context, data=None):
        if not permissions[name](context, data or {})['success']:
            raise tk.NotAuthorized('Denied')

    monkeypatch.setattr(tk, 'check_access', check_access)
    contexts = {
        'anonymous': {},
        'member': {'auth_user_obj': SimpleNamespace(id='member', sysadmin=False)},
        'admin': {'auth_user_obj': SimpleNamespace(id='admin', sysadmin=True)},
    }
    yield SimpleNamespace(session=db.Session, contexts=contexts)
    db.Session.remove()
    engine.dispose()


def project(env, name, **values):
    row = db.C4wProject(name=name, slug=db.slugify(name),
                       approved=values.pop('approved', True), **values)
    env.session.add(row)
    env.session.commit()
    return row


@pytest.mark.parametrize('role', ['anonymous', 'member', 'admin'])
def test_public_catalogue_counts_facets_and_featured_exclude_private(env, role):
    project(env, 'Published', status='active', featured=True)
    project(env, 'Hidden', status='completed', hidden=True, featured=True)
    project(env, 'Pending', status='planned', approved=False, featured=True)
    context = env.contexts[role]
    for params in ({}, {'include_private': True}, {'featured': 'true'}):
        listing = projects.c4w_project_list(context, params)
        assert listing['count'] == 1
        assert [row['name'] for row in listing['results']] == ['Published']
        assert listing['facets']['status'] == {'active': 1}
    assert projects.c4w_project_list(context, {'q': 'Hidden'})['count'] == 0
    assert stats.c4w_stats(context, {})['projects'] == 1


def test_inventory_pages_include_published_hidden_and_pending_without_duplicates(env):
    for index in range(45):
        project(env, 'Project %02d' % index, approved=index < 36, hidden=index == 44)
    context = env.contexts['admin']
    pages = [moderation.c4w_admin_project_list(context, {'page': page})
             for page in (1, 2)]
    assert [page['count'] for page in pages] == [45, 45]
    assert [len(page['results']) for page in pages] == [25, 20]
    rows = [row for page in pages for row in page['results']]
    assert len({row['id'] for row in rows}) == 45
    assert sum(row['approved'] for row in rows) == 36
    assert sum(row['hidden'] for row in rows) == 1
    queue = moderation.c4w_moderation_list(context, {})
    assert len(next(g['rows'] for g in queue['groups']
                    if g['entity_type'] == 'project')) == 9


def test_search_finds_published_karen_and_bounds_pagination(env):
    project(env, 'Test project Karen')
    project(env, 'Other')
    context = env.contexts['admin']
    listing = moderation.c4w_admin_project_list(context, {'q': 'karen', 'page': 99})
    assert listing['count'] == 1 and listing['page'] == 1
    assert listing['results'][0]['name'] == 'Test project Karen'
    assert moderation.c4w_admin_project_list(context, {'q': 'absent'})['count'] == 0
    assert moderation.c4w_admin_project_list(context, {'page_size': 10000})['page_size'] == 100
    assert moderation.c4w_admin_project_list(context, {'page': 'invalid'})['page'] == 1


@pytest.mark.parametrize('role', ['anonymous', 'member'])
def test_inventory_and_hide_require_sysadmin(env, role):
    row = project(env, 'Published')
    context = env.contexts[role]
    with pytest.raises(tk.NotAuthorized):
        moderation.c4w_admin_project_list(context, {'include_private': True})
    with pytest.raises(tk.NotAuthorized):
        moderation.c4w_entity_moderate(context, {
            'entity_type': 'project', 'id': row.id, 'operation': 'hide'})
    assert row.hidden is False


@pytest.mark.parametrize('approved', [True, False])
def test_hide_unhide_preserves_approval_and_inventory_position(env, approved):
    row = project(env, 'A project', approved=approved)
    project(env, 'B project')
    context = env.contexts['admin']
    original = moderation.c4w_admin_project_list(context, {'page_size': 1})['results'][0]['id']
    for hidden in (True, False):
        result = moderation.c4w_entity_moderate(context, {
            'entity_type': 'project', 'id': row.id, 'operation': 'hide'})
        assert result['hidden'] is hidden
        assert result['approved'] is approved
        inventory = moderation.c4w_admin_project_list(context, {'page_size': 1})
        assert inventory['count'] == 2 and inventory['results'][0]['id'] == original
        for public_context in env.contexts.values():
            public = projects.c4w_project_list(public_context, {})
            assert (row.id in [p['id'] for p in public['results']]) == (approved and not hidden)
        if hidden or not approved:
            with pytest.raises(tk.ObjectNotFound):
                projects.c4w_project_show({}, {'id': row.slug})
        assert projects.c4w_project_show(context, {'id': row.slug})['can_edit']


@pytest.mark.parametrize('approved', [True, False])
def test_moderation_redirect_preserves_search_page_and_explains_visibility(env, monkeypatch, approved):
    row = project(env, 'Test project', approved=approved)
    notices = []
    monkeypatch.setattr(views_admin, 'require_content_admin', lambda: (env.contexts['admin']['auth_user_obj'], None))
    monkeypatch.setattr(views_admin.c4w_helpers, 'c4w_url', lambda endpoint, **kw: '/citizens4water/admin')
    monkeypatch.setattr(tk, 'h', SimpleNamespace(flash_success=notices.append))
    monkeypatch.setattr(tk, 'get_action', lambda name: lambda _context, data:
                        moderation.c4w_entity_moderate(env.contexts['admin'], data))
    app = Flask(__name__)
    target = '/citizens4water/admin/projects?q=Test&page=2'
    for hidden in (True, False):
        with app.test_request_context(method='POST', data={'next': target}):
            response = views_admin.admin_moderate('project', row.id, 'hide')
        assert response.location == target
        assert row.hidden is hidden
    assert 'not visible to the public' in notices[0]
    assert notices[1] == ('Visible to the public.' if approved else
                         'Unhidden. This item still needs approval before it is public.')
    with app.test_request_context(method='POST', data={'next': '//external.example/'}):
        response = views_admin.admin_moderate('project', row.id, 'hide')
    assert response.location == '/citizens4water/admin'

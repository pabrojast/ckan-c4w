"""Platform inventory, reversible visibility and existing-schema upgrades."""
from types import SimpleNamespace

import pytest

pytest.importorskip('ckan')
import sqlalchemy as sa
from flask import Flask
import ckan.plugins.toolkit as tk
from ckanext.c4w import constants, db
from ckanext.c4w.logic import auth, views_admin
from ckanext.c4w.logic.action import moderation, platforms, stats


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


def platform(env, slug, **values):
    row = db.C4wPlatform(slug=slug, name=values.pop('name', slug), **values)
    env.session.add(row)
    env.session.commit()
    return row


@pytest.mark.parametrize('role', ['anonymous', 'member', 'admin'])
def test_public_platforms_counts_search_and_facets_exclude_private(env, role):
    platform(env, 'visible', geographic_extent='local')
    platform(env, 'hidden', hidden=True, geographic_extent='national')
    platform(env, 'pending', approved=False, geographic_extent='regional')
    for params in ({}, {'include_private': True}, {'hidden': True}):
        result = platforms.c4w_platform_list(env.contexts[role], params)
        assert result['count'] == 1
        assert [p['slug'] for p in result['results']] == ['visible']
        assert result['facets']['geographic_extent'] == {'local': 1}
    assert platforms.c4w_platform_list(env.contexts[role], {'q': 'hidden'})['count'] == 0
    assert stats.c4w_stats(env.contexts[role], {})['platforms'] == 1


def test_inventory_distinguishes_duplicate_names_and_has_stable_pagination(env):
    for index in range(31):
        platform(env, 'test-%02d' % index, name='test',
                 approved=index < 26, hidden=index == 30, url='https://example.org')
    context = env.contexts['admin']
    pages = [moderation.c4w_admin_platform_list(context, {'page': page})
             for page in (1, 2)]
    assert [len(p['results']) for p in pages] == [25, 6]
    rows = [r for p in pages for r in p['results']]
    assert len({r['id'] for r in rows}) == 31
    assert len({r['slug'] for r in rows}) == 31
    assert all(r['name'] == 'test' and r['url'] == 'https://example.org' for r in rows)
    assert [r['id'] for r in rows] == sorted(r['id'] for r in rows)
    result = moderation.c4w_admin_platform_list(context, {'q': 'test-30', 'page': 99})
    assert result['count'] == 1 and result['page'] == 1
    assert result['results'][0]['hidden'] is True
    assert moderation.c4w_admin_platform_list(context, {'q': 'absent'})['count'] == 0
    assert moderation.c4w_admin_platform_list(context, {'page_size': 999})['page_size'] == 100
    assert moderation.c4w_admin_platform_list(context, {'page': 'invalid'})['page'] == 1
    queue = moderation.c4w_moderation_list(context, {})
    group = next(g for g in queue['groups'] if g['entity_type'] == 'platform')
    assert group['can_hide'] and len(group['rows']) == 5


@pytest.mark.parametrize('role', ['anonymous', 'member'])
def test_inventory_and_hide_require_sysadmin(env, role):
    row = platform(env, 'visible')
    for action, params in (
        (moderation.c4w_admin_platform_list, {'include_private': True}),
        (moderation.c4w_entity_moderate,
         {'entity_type': 'platform', 'id': row.id, 'operation': 'hide'}),
    ):
        with pytest.raises(tk.NotAuthorized):
            action(env.contexts[role], params)
    assert row.hidden is False


@pytest.mark.parametrize('approved', [True, False])
def test_hide_unhide_preserves_approval_navigation_and_detail_permissions(env, monkeypatch, approved):
    row = platform(env, 'test', approved=approved, legacy_id=1)
    platform(env, 'test-2', name='test')
    context = env.contexts['admin']
    original_order = [r['id'] for r in moderation.c4w_admin_platform_list(context, {})['results']]
    notices = []
    monkeypatch.setattr(views_admin, 'require_sysadmin', lambda: (context['auth_user_obj'], None))
    monkeypatch.setattr(views_admin.c4w_helpers, 'c4w_url', lambda endpoint, **kw: '/citizens4water/admin')
    monkeypatch.setattr(tk, 'h', SimpleNamespace(flash_success=notices.append))
    monkeypatch.setattr(tk, 'get_action', lambda name: lambda _context, data:
                        moderation.c4w_entity_moderate(context, data))
    target = '/citizens4water/admin/platforms?q=test&page=2'
    app = Flask(__name__)
    assert constants.moderate_error('platform', 'hide') is None
    for hidden in (True, False):
        with app.test_request_context(method='POST', data={'next': target}):
            response = views_admin.admin_moderate('platform', row.id, 'hide')
        assert response.location == target
        assert row.hidden is hidden and row.approved is approved
        assert [r['id'] for r in moderation.c4w_admin_platform_list(context, {})['results']] == original_order
        for role in env.contexts:
            result = platforms.c4w_platform_list(env.contexts[role], {})
            assert (row.id in [p['id'] for p in result['results']]) == (approved and not hidden)
        for reference in (row.id, row.slug, str(row.legacy_id)):
            if hidden or not approved:
                for role in ('anonymous', 'member'):
                    with pytest.raises(tk.ObjectNotFound):
                        platforms.c4w_platform_show(env.contexts[role], {'id': reference})
            assert platforms.c4w_platform_show(context, {'id': reference})['hidden'] is hidden
    assert 'not visible to the public' in notices[0]
    assert notices[1] == ('Visible to the public.' if approved else
                         'Unhidden. This item still needs approval before it is public.')
    with app.test_request_context(method='POST', data={'next': '//external.example/'}):
        assert views_admin.admin_moderate('platform', row.id, 'hide').location == '/citizens4water/admin'


def test_platform_schema_upgrade_preserves_rows_and_existing_hidden_values():
    engine = sa.create_engine('sqlite://')
    with engine.begin() as conn:
        conn.execute(sa.text('CREATE TABLE c4w_platform (id TEXT, slug TEXT, approved BOOLEAN)'))
        conn.execute(sa.text("INSERT INTO c4w_platform VALUES ('one', 'test', TRUE), ('two', 'test-2', FALSE)"))
    db._ensure_columns(engine)
    with engine.begin() as conn:
        assert conn.execute(sa.text('SELECT id, approved, hidden FROM c4w_platform ORDER BY id')).fetchall() == [
            ('one', 1, 0), ('two', 0, 0)]
        conn.execute(sa.text("UPDATE c4w_platform SET hidden=TRUE WHERE id='one'"))
    db._ensure_columns(engine)
    with engine.begin() as conn:
        assert conn.execute(sa.text("SELECT hidden FROM c4w_platform WHERE id='one'")).scalar() == 1
        conn.execute(sa.text("INSERT INTO c4w_platform (id, slug, approved) VALUES ('three', 'new', TRUE)"))
        assert conn.execute(sa.text("SELECT hidden FROM c4w_platform WHERE id='three'")).scalar() == 0
    hidden = next(c for c in sa.inspect(engine).get_columns('c4w_platform') if c['name'] == 'hidden')
    assert hidden['nullable'] is False
    engine.dispose()

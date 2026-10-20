"""Visibility invariants across all eight public surfaces and data endpoints."""
import datetime
import gzip
from types import SimpleNamespace

import pytest
pytest.importorskip('ckan')
import sqlalchemy as sa
from flask import Flask
import ckan.plugins.toolkit as tk
from ckanext.c4w import constants, db
from ckanext.c4w.logic import actions, views_datasets
from ckanext.c4w.logic.action import moderation, datasets, lookup, stats, _common
from ckanext.c4w.tests.test_admin_platforms import env


def item(env, section, slug, published=True, **values):
    entity = next(e for s, e, _label in constants.ADMIN_SECTIONS if s == section)
    model = dict(db.ENTITY_CLASSES)[entity]
    values.update(slug=slug, hidden=values.get('hidden', False))
    values['name' if hasattr(model, 'name') else 'title'] = slug
    values['status' if entity == 'post' else 'approved'] = ('published' if published else 'draft') if entity == 'post' else published
    values['author_id' if entity == 'post' else 'created_by'] = 'owner'
    if entity == 'resource':
        values['is_training_resource'] = section == 'training_resources'
    if entity == 'event':
        values['start_date'] = datetime.datetime.utcnow() + datetime.timedelta(days=1)
    row = model(**values)
    env.session.add(row); env.session.commit()
    return entity, row


@pytest.mark.parametrize('section,entity,label', constants.ADMIN_SECTIONS)
def test_every_surface_visibility_and_permissions(env, section, entity, label):
    registry = actions.get_actions()
    _entity, row = item(env, section, 'test-visible')
    item(env, section, 'test-pending', published=False)
    list_name = 'c4w_training_resource_list' if section == 'training_resources' else 'c4w_%s_list' % entity
    admin = env.contexts['admin']
    env.contexts['owner'] = {'auth_user_obj': SimpleNamespace(id='owner', sysadmin=False)}
    env.contexts['editor'] = {'auth_user_obj': SimpleNamespace(id='editor', sysadmin=False)}
    env.session.add(db.C4wRelation(subject_type=entity, subject_id=row.id, predicate='editor', object_type='user', object_id='editor'))
    env.session.commit()
    for hidden in (True, False):
        moderation.c4w_entity_moderate(admin, {'entity_type': entity, 'id': row.id, 'operation': 'hide'})
        assert row.hidden is hidden
        assert (row.status == 'published') if entity == 'post' else row.approved
        for role, context in env.contexts.items():
            public = registry[list_name](context, {'include_private': True, 'q': 'test'})
            assert public['count'] == int(not hidden)
            show = registry['c4w_%s_show' % entity]
            if hidden and role in ('anonymous', 'member'):
                with pytest.raises(tk.ObjectNotFound): show(context, {'id': row.slug})
            else:
                assert show(context, {'id': row.slug})['hidden'] is hidden
            if role != 'admin':
                for action, data in [(moderation.c4w_admin_entity_list, {'section': section}),
                                     (moderation.c4w_admin_summary, {}),
                                     (moderation.c4w_entity_moderate, {'entity_type': entity, 'id': row.id, 'operation': 'hide'})]:
                    with pytest.raises(tk.NotAuthorized): action(context, data)
        inventory = moderation.c4w_admin_entity_list(admin, {'section': section})
        assert inventory['count'] == 2
        assert moderation.c4w_admin_entity_list(admin, {'section': section, 'visibility': 'hidden'})['count'] == int(hidden)
        assert moderation.c4w_admin_entity_list(admin, {'section': section, 'visibility': 'published'})['count'] == int(not hidden)
        assert moderation.c4w_admin_entity_list(admin, {'section': section, 'visibility': 'unpublished'})['count'] == 1
        summary = next(s for s in moderation.c4w_admin_summary(admin, {})['sections'] if s['section'] == section)
        assert (summary['total'], summary['published'], summary['hidden']) == (2, int(not hidden), int(hidden))
        public_key = 'datasets' if entity == 'dataset' else 'posts' if entity == 'post' else section
        assert stats.c4w_stats({}, {})[public_key] == int(not hidden)


@pytest.mark.parametrize('section,entity,label', constants.ADMIN_SECTIONS)
def test_approval_or_unhide_never_publishes_hidden_or_pending_content(env, section, entity, label):
    _entity, row = item(env, section, 'pending', published=False, hidden=True)
    admin = env.contexts['admin']
    if entity != 'post':
        moderation.c4w_entity_moderate(admin, {'entity_type': entity, 'id': row.id, 'operation': 'approve'})
        assert row.hidden and not _common.is_visible(entity, row, {})
        row.approved = False; env.session.commit()
    moderation.c4w_entity_moderate(admin, {'entity_type': entity, 'id': row.id, 'operation': 'hide'})
    assert not row.hidden and not _common.is_visible(entity, row, {})


def test_hidden_parents_do_not_hide_children_or_leak_in_relations(env):
    _, org = item(env, 'organisations', 'hidden-org', hidden=True)
    _, project = item(env, 'projects', 'hidden-project', hidden=True)
    _, dataset = item(env, 'data', 'visible-data', project_id=project.id, organisation_id=org.id)
    result = datasets.c4w_dataset_show({}, {'id': dataset.id})
    assert result['project'] is None and result['organisation'] is None
    assert lookup.c4w_lookup({}, {'kind': 'organisation', 'q': 'hidden-org'})['results'] == []
    assert lookup.c4w_lookup({}, {'kind': 'project', 'q': 'hidden-project'})['results'] == []


def test_data_bundle_etag_and_all_portal_routes_check_visibility_first(env, monkeypatch):
    _, row = item(env, 'data', 'data', bundle_generation=1, processing_status='ready')
    env.session.add(db.C4wDashboardBundle(dataset_id=row.id, generation=1, name='meta.json', etag='etag', body=gzip.compress(b'{}')))
    env.session.commit()
    registry = actions.get_actions()
    monkeypatch.setattr(tk, 'get_action', lambda name: lambda _context, data: registry[name]({}, data))
    app = Flask(__name__)
    with app.test_request_context(headers={'If-None-Match': '"etag"'}):
        response = views_datasets.dataset_bundle(row.slug, 'meta.json')
        assert response.status_code == 304 and response.headers['Cache-Control'] == 'private, no-cache'
    row.hidden = True; env.session.commit()
    def abort(code, message):
        raise tk.ObjectNotFound(str(code))
    monkeypatch.setattr(tk, 'abort', abort)
    for view, args in [(views_datasets.dataset_detail, (row.slug,)), (views_datasets.dataset_dashboard, (row.slug,)),
                       (views_datasets.dataset_embed, (row.slug,)), (views_datasets.dataset_download, (row.slug, 'file')),
                       (views_datasets.dataset_bundle, (row.slug, 'meta.json'))]:
        with app.test_request_context(headers={'If-None-Match': '"etag"'}):
            with pytest.raises(tk.ObjectNotFound): view(*args)


@pytest.mark.parametrize('table', ['c4w_organisation', 'c4w_event', 'c4w_post'])
def test_additive_schema_preserves_old_rows_and_repeat_upgrade(table):
    engine = sa.create_engine('sqlite://')
    with engine.begin() as conn:
        conn.execute(sa.text('CREATE TABLE %s (id TEXT, slug TEXT)' % table))
        conn.execute(sa.text("INSERT INTO %s VALUES ('one', 'legacy')" % table))
    db._ensure_columns(engine)
    with engine.begin() as conn:
        assert conn.execute(sa.text('SELECT hidden FROM %s' % table)).scalar() == 0
        conn.execute(sa.text('UPDATE %s SET hidden=TRUE' % table))
    db._ensure_columns(engine)
    with engine.begin() as conn:
        assert conn.execute(sa.text('SELECT id, slug, hidden FROM %s' % table)).fetchone() == ('one', 'legacy', 1)
    engine.dispose()

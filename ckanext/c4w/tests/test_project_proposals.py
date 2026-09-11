"""Exercise draft isolation, review, permissions and field persistence on a real ORM."""
import datetime
import json
from types import SimpleNamespace

import pytest

pytest.importorskip('ckan')
import sqlalchemy as sa
import ckan.model as model
import ckan.plugins.toolkit as tk
from ckanext.c4w import db
from ckanext.c4w.logic import project_fields, helpers, mail
from ckanext.c4w.logic.action import project_write as actions, lookup, moderation, registration, projects


@pytest.fixture
def env(monkeypatch):
    engine = sa.create_engine('sqlite://')
    db.ensure_mappers()
    user_table = sa.inspect(model.User).local_table
    monkeypatch.setattr(user_table.c.plugin_extras, 'type', sa.JSON())
    db.metadata.create_all(bind=engine, tables=db._ALL_TABLES + [sa.inspect(model.User).local_table])
    db.Session.remove()
    db.Session.configure(bind=engine)
    users = {key: model.User(id=key, name=key, fullname=key, email=key+'@example.org',
             state='active', sysadmin=key == 'admin') for key in ('owner', 'other', 'editor', 'admin')}
    db.Session.add_all(list(users.values()))
    db.Session.commit()
    monkeypatch.setattr(model.User, 'get', staticmethod(lambda key: users.get(key)))
    monkeypatch.setattr(tk, '_', lambda s: s)
    monkeypatch.setattr(helpers, 'c4w_url', lambda *a, **kw: '/citizens4water/test')
    monkeypatch.setattr(mail, 'notify_moderators', lambda *a, **kw: True)
    monkeypatch.setattr(mail, '_send', lambda *a, **kw: True)
    auth = dict(actions.get_auth_functions(), **lookup.get_auth_functions())
    auth.update(moderation.get_auth_functions())
    auth.update(registration.get_auth_functions())

    def check_access(name, context, data=None):
        if name in auth and not auth[name](context, data or {})['success']:
            raise tk.NotAuthorized('Denied')
    monkeypatch.setattr(tk, 'check_access', check_access)
    context = {key: {'auth_user_obj': user} for key, user in users.items()}
    yield SimpleNamespace(session=db.Session, users=users, context=context)
    db.Session.remove()
    engine.dispose()


def create(env, complete=True):
    data = dict(name='River Watch')
    if complete:
        data.update(status='active', description='<p>Community water monitoring</p>',
                    aim='Understand our river', keywords='river, water')
    return actions.c4w_project_create(env.context['owner'], data)


def save(env, draft, step, **values):
    return actions.c4w_project_update(env.context['owner'], dict(id=draft['id'], version=draft['version'], step=step, **values))


def submit(env, draft):
    return actions.c4w_project_submit(env.context['owner'], {'id': draft['id'], 'version': draft['version']})


def approve(env, draft):
    return actions.c4w_project_review(env.context['admin'], {'id': draft['id'], 'version': draft['version'], 'decision': 'approve'})


def test_partial_draft_is_saved_but_cannot_be_submitted(env):
    draft = create(env, complete=False)
    assert draft['status'] == 'draft'
    with pytest.raises(tk.ValidationError) as error:
        submit(env, draft)
    assert {'description', 'aim', 'status', 'keywords'} <= set(error.value.error_dict)
    assert env.session.query(db.C4wProject).one().approved is False
    assert not moderation.c4w_moderation_list(env.context['admin'], {})['project_requests']


def test_lookup_preserves_all_saved_partners_beyond_first_search_page(env, monkeypatch):
    env.session.add_all([db.C4wOrganisation(id='org-%02d' % i, name='Organisation %02d' % i,
        slug='org-%02d' % i, approved=True) for i in range(65)])
    env.session.commit()
    monkeypatch.setattr(tk, 'get_action', lambda name: lookup.c4w_lookup)
    selected = ['org-%02d' % i for i in range(40, 65)]
    options = helpers.c4w_lookup_options('organisation', selected)
    assert set(selected) <= {row['id'] for row in options}
    assert len({row['id'] for row in options}) == len(options)


def test_submission_approval_and_revision_keep_public_content_until_approved(env):
    draft = create(env)
    draft = approve(env, submit(env, draft))
    public = env.session.query(db.C4wProject).one()
    slug = public.slug
    assert public.approved and public.name == 'River Watch'
    changed = save(env, draft, 1, name='New public title', description='New description')
    assert public.name == 'River Watch' and public.description == '<p>Community water monitoring</p>'
    assert changed['payload']['description'] == 'New description'
    pending = submit(env, changed)
    assert public.name == 'River Watch'
    queue = moderation.c4w_moderation_list(env.context['admin'], {})['project_requests']
    assert len(queue) == 1 and queue[0]['published']
    approve(env, pending)
    assert public.name == 'New public title' and public.slug == slug


def test_corrections_require_reason_and_can_be_resubmitted(env):
    draft = submit(env, create(env))
    decision = dict(id=draft['id'], version=draft['version'], decision='changes_requested')
    with pytest.raises(tk.ValidationError):
        actions.c4w_project_review(env.context['admin'], decision)
    changed = actions.c4w_project_review(env.context['admin'], dict(decision, note='Explain the sampling method'))
    assert changed['status'] == 'changes_requested'
    assert 'sampling' in changed['review_note']
    corrected = save(env, changed, 1, description='Monthly samples using the agreed protocol')
    assert submit(env, corrected)['status'] == 'pending'


def test_unverified_or_unrelated_users_cannot_write_or_read_drafts(env):
    draft = create(env)
    with pytest.raises(tk.ObjectNotFound):
        actions.c4w_project_draft_show(env.context['other'], {'id': draft['id']})
    with pytest.raises(tk.NotAuthorized):
        actions.c4w_project_create({}, {'name': 'Anonymous'})
    env.session.add(db.C4wUserProfile(user_id='other', email_verified=False))
    env.session.commit()
    with pytest.raises(tk.NotAuthorized):
        actions.c4w_project_create(env.context['other'], {'name': 'Unverified'})
    assert not lookup.c4w_lookup(env.context['other'], {'kind': 'project'})['results']
    assert lookup.c4w_lookup(env.context['owner'], {'kind': 'project'})['results'][0]['id'] == draft['id']
    with pytest.raises(tk.NotAuthorized):
        actions.c4w_project_review(env.context['owner'], {'id': draft['id'], 'decision': 'approve'})


def test_stale_save_and_stale_review_are_rejected(env):
    draft = create(env)
    current = save(env, draft, 2, start_date='2026-01-01')
    with pytest.raises(tk.ValidationError):
        save(env, draft, 2, start_date='2020-01-01')
    pending = submit(env, current)
    edited = save(env, pending, 1, name='Updated after submit')
    with pytest.raises(tk.ValidationError):
        approve(env, pending)
    assert edited['status'] == 'draft'


def test_all_steps_round_trip_and_publish_without_granting_org_permissions(env):
    org = db.C4wOrganisation(id='org', name='Water group', slug='water-group', approved=True)
    env.session.add(org); env.session.commit()
    draft = create(env)
    draft = save(env, draft, 2, topic=['water'], has_tag=['suitable-for-children'], start_date='2020-01-01', end_date='2028-12-31')
    draft = save(env, draft, 3, water_type=['surface-water'], language=['en', 'es'], open_participation='true',
                 number_of_participants='125', how_to_participate='<p>Join the monthly survey</p>', water_parameters='Nitrate')
    geo = {'type': 'Polygon', 'coordinates': [[[0, 0], [1, 0], [1, 1], [0, 0]]]}
    draft = save(env, draft, 4, country=['CL', 'PE'], geographic_extent=['national'], geom_geojson=json.dumps(geo))
    draft = save(env, draft, 5, main_organisation_id='org', organisation=['org'], editors='editor',
                 uses_ai='true', ai_description='Classifies observations', achievements='Improved coverage', funding_body='Water fund')
    draft = save(env, draft, 6, image1_credit='Community')
    approve(env, submit(env, draft))
    row = env.session.query(db.C4wProject).one()
    data = db.entity_dictize('project', row)
    assert data['terms']['country'] == ['CL', 'PE']
    assert data['terms']['keyword'] == ['river', 'water']
    assert data['terms']['funding_body'] == ['water-fund']
    assert data['number_of_participants'] == 125
    assert data['main_organisation_id'] == 'org' and data['uses_ai'] is True
    assert json.loads(data['geom_geojson'])['type'] == 'MultiPolygon'
    assert data['relations']['editor'] == ['editor']
    assert not env.session.query(db.C4wRelation).filter_by(subject_type='organisation').count()
    assert actions.c4w_project_draft_show(env.context['editor'], {'id': row.id})['id'] == row.id


def test_generic_moderation_cannot_bypass_revision_validation(env):
    draft = create(env, complete=False)
    with pytest.raises(tk.ValidationError):
        moderation.c4w_entity_moderate(env.context['admin'], dict(entity_type='project', id=draft['id'], operation='approve'))


def test_project_does_not_expose_private_datasets_or_unapproved_revision(env):
    draft = approve(env, submit(env, create(env)))
    env.session.add_all([db.C4wDataset(title='Private sample', slug='private', project_id=draft['id'], created_by='owner'),
                         db.C4wDataset(title='Public sample', slug='public', project_id=draft['id'], approved=True)])
    env.session.commit()
    save(env, draft, 1, name='Unreviewed title')
    out = projects.c4w_project_show({}, {'id': draft['id']})
    assert out['name'] == 'River Watch'
    assert [v['title'] for v in out['datasets']] == ['Public sample']
    assert 'contributor' not in out


def test_markup_and_invalid_geometry_cannot_enter_published_payload(env):
    draft = create(env)
    draft = save(env, draft, 1, description='<script>alert(1)</script><p>Safe</p>')
    assert '<script' not in draft['payload']['description']
    with pytest.raises(tk.ValidationError):
        save(env, draft, 4, geom_geojson='{"type":"Point","coordinates":[0,0]}')
    with pytest.raises(tk.ValidationError):
        save(env, draft, 2, end_date='1900-01-01', start_date='2020-01-01')


def test_manager_email_confirmation_activates_ckan_without_org_grant(env):
    owner = env.users['owner']; owner.state = 'pending'
    owner.activate = lambda: setattr(owner, 'state', 'active')
    owner.is_deleted = lambda: False
    profile = db.C4wUserProfile(user_id='owner', profile_type='manager', email_verified=False,
        verification_token_hash=registration._hash('token'), token_created=datetime.datetime.utcnow())
    env.session.add(profile); env.session.commit()
    result = registration.c4w_verify_email({}, {'token': 'token'})
    assert result['state'] == 'manager_pending'
    assert owner.state == 'active' and profile.email_verified
    assert profile.manager_decision is None and not profile.ckan_org_id
    assert actions.verified(env.context['owner'])


def test_registration_uses_ckan_user_create_once_and_saves_only_profile_extras(env, monkeypatch):
    calls = []
    def core_create(context, data):
        calls.append(data)
        user = model.User(id='new-user', name=data['name'], fullname=data['fullname'],
                          email=data['email'], password=data['password'], state='active')
        env.users['new-user'] = user
        env.session.add(user); env.session.commit()
        return {'id': user.id, 'name': user.name}
    monkeypatch.setattr(tk, 'get_action', lambda name: core_create if name == 'user_create' else None)
    monkeypatch.setattr(registration, 'recaptcha_configured', lambda: False)
    monkeypatch.setattr(mail, 'send_verification', lambda *args: True)
    result = registration.c4w_register_citizen({}, dict(given_name='Ana', surname='Rojas',
        username='ana-rojas', email='ana@example.org', password='Strong-password-2026',
        password_confirm='Strong-password-2026', terms='true', profile_visible='true'))
    assert len(calls) == 1 and calls[0]['fullname'] == 'Ana Rojas'
    assert result['id'] == 'new-user'
    profile = env.session.query(db.C4wUserProfile).filter_by(user_id='new-user').one()
    assert profile.given_name == 'Ana' and profile.surname == 'Rojas'
    assert profile.profile_visible is True and profile.email_verified is False
    assert env.users['new-user'].state == 'pending'


def test_invalid_image_upload_cannot_replace_saved_image(env):
    from io import BytesIO
    from werkzeug.datastructures import FileStorage
    draft = create(env)
    with pytest.raises(tk.ValidationError):
        actions.c4w_project_image_upload(env.context['owner'], dict(id=draft['id'], version=draft['version'],
            field='image1', upload=FileStorage(BytesIO(b'<script>not an image</script>'), filename='fake.png')))
    current = actions.c4w_project_draft_show(env.context['owner'], {'id': draft['id']})
    assert current['version'] == draft['version'] and not current['payload'].get('image1_url')


def test_visibility_preference_controls_contributor_details(env):
    draft = approve(env, submit(env, create(env)))
    assert 'contributor' not in projects.c4w_project_show({}, {'id': draft['id']})
    registration.c4w_profile_preferences_update(env.context['owner'], {'profile_visible': 'true'})
    out = projects.c4w_project_show({}, {'id': draft['id']})
    assert out['contributor']['name'] == 'owner'
    assert 'email' not in out['contributor']
    registration.c4w_profile_preferences_update(env.context['owner'], {})
    assert 'contributor' not in projects.c4w_project_show({}, {'id': draft['id']})


@pytest.mark.parametrize('query,title', [('universite', 'Université de Lyon'), ('WATER', 'Water Research'), ('river monitoring', 'River Quality Monitoring')])
def test_search_handles_accents_case_and_multiple_words(query, title):
    assert lookup.rank(query, title, 'example') is not None


@pytest.mark.parametrize('geometry', [None, '', '{bad', '{"type":"Polygon","coordinates":[]}', '{"type":"Polygon","coordinates":[[[181,0],[0,0],[1,1],[181,0]]]}'])
def test_geometry_checks(geometry):
    if not geometry:
        assert project_fields.geometry(geometry) == ''
    else:
        with pytest.raises(ValueError):
            project_fields.geometry(geometry)

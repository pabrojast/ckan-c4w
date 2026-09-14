"""Real ORM coverage for news/events, review isolation and contribution return."""
import datetime
from io import BytesIO
from types import SimpleNamespace
import pytest
pytest.importorskip('ckan')
import ckan.plugins.toolkit as tk
from werkzeug.datastructures import FileStorage
from ckanext.c4w import db
from ckanext.c4w.logic import access, helpers
from ckanext.c4w.logic.action import content_write as content, moderation, posts, events, registration
from ckanext.c4w.tests.test_project_proposals import env as project_env


@pytest.fixture
def env(project_env, monkeypatch):
    previous = tk.check_access
    auth = content.get_auth_functions()
    def check(name, context, data=None):
        if name in auth:
            if not auth[name](context, data or {})['success']:
                raise tk.NotAuthorized('Denied')
        else:
            previous(name, context, data)
    monkeypatch.setattr(tk, 'check_access', check)
    return project_env


def create(env, entity='post', **values):
    return content.c4w_content_create(env.context['owner'], dict(entity_type=entity,
        title='River community update', content='<p>Our water monitoring results.</p>',
        description='<p>Community workshop.</p>', start_date='2026-10-01', **values))


def args(draft, **values):
    return dict(entity_type=draft['entity_type'], id=draft['id'], version=draft['version'], **values)


@pytest.mark.parametrize('entity', ['post', 'event'])
def test_draft_review_corrections_and_public_revision_isolation(env, entity):
    draft = create(env, entity)
    show = posts.c4w_post_show if entity == 'post' else events.c4w_event_show
    with pytest.raises(tk.ObjectNotFound):show({}, {'id': draft['id']})
    assert moderation.c4w_moderation_list(env.context['admin'], {})['content_requests'] == []
    draft = content.c4w_content_submit(env.context['owner'], args(draft))
    queue = moderation.c4w_moderation_list(env.context['admin'], {})
    assert queue['content_requests'][0]['id'] == draft['id']
    assert not any(r['id']==draft['id'] for group in queue['groups'] for r in group['rows'])
    with pytest.raises(tk.ValidationError):
        content.c4w_content_review(env.context['admin'], args(draft, decision='changes_requested'))
    draft = content.c4w_content_review(env.context['admin'], args(draft, decision='changes_requested', note='Add context.'))
    listing = moderation.c4w_my_submissions(env.context['owner'], {})
    item = next(r for g in listing['groups'] for r in g['rows'] if r['id']==draft['id'])
    assert item['proposal']['review_note'] == 'Add context.'
    draft = content.c4w_content_update(env.context['owner'], args(draft, title='First approved title'))
    draft = content.c4w_content_submit(env.context['owner'], args(draft))
    draft = content.c4w_content_review(env.context['admin'], args(draft, decision='approve'))
    assert show({}, {'id':draft['id']})['title'] == 'First approved title'
    draft = content.c4w_content_update(env.context['owner'], args(draft, title='Replacement title'))
    assert show({}, {'id':draft['id']})['title'] == 'First approved title'
    draft = content.c4w_content_submit(env.context['owner'], args(draft))
    draft = content.c4w_content_review(env.context['admin'], args(draft, decision='approve'))
    assert show({}, {'id':draft['id']})['title'] == 'Replacement title'


@pytest.mark.parametrize('entity', ['post', 'event'])
def test_permissions_versions_and_approval_bypass(env, entity):
    with pytest.raises(tk.NotAuthorized):content.c4w_content_create({}, dict(entity_type=entity,title='Anonymous'))
    env.users['other'].state='pending'
    with pytest.raises(tk.NotAuthorized):content.c4w_content_create(env.context['other'],dict(entity_type=entity,title='Unverified'))
    draft = create(env,entity)
    with pytest.raises(tk.ObjectNotFound):content.c4w_content_draft_show(env.context['other'],args(draft))
    with pytest.raises(tk.NotAuthorized):content.c4w_content_review(env.context['owner'],args(draft,decision='approve'))
    saved = content.c4w_content_update(env.context['owner'],args(draft,title='Fresh version'))
    with pytest.raises(tk.ValidationError):content.c4w_content_update(env.context['owner'],args(draft,title='Stale title'))
    current=content.c4w_content_draft_show(env.context['owner'],args(saved))
    assert current['payload']['title']=='Fresh version'
    pending=content.c4w_content_submit(env.context['owner'],args(saved))
    with pytest.raises(tk.ValidationError):content.c4w_content_review(env.context['admin'],args(saved,decision='approve'))
    if entity=='event':
        with pytest.raises(tk.ValidationError):moderation.c4w_entity_moderate(env.context['admin'],dict(entity_type=entity,id=draft['id'],operation='approve'))
    assert content.c4w_content_draft_show(env.context['owner'],args(pending))['status']=='pending'


def test_required_fields_sanitization_and_dates(env):
    draft=content.c4w_content_create(env.context['owner'],dict(entity_type='post',title='Incomplete'))
    with pytest.raises(tk.ValidationError):content.c4w_content_submit(env.context['owner'],args(draft))
    draft=content.c4w_content_update(env.context['owner'],args(draft,content='<script>alert(1)</script><p>Safe</p><img src="https://evil.example/x" onerror="alert(1)">'))
    assert '<script' not in draft['payload']['content'] and 'onerror' not in draft['payload']['content'] and 'evil.example' not in draft['payload']['content']
    for values in [dict(end_date='2020-01-01'),dict(timezone='Made/Up'),dict(url='javascript:alert(1)'),dict(project_id='missing'),dict(main_organisation_id='missing')]:
        with pytest.raises(tk.ValidationError):create(env,'event',**values)
    with pytest.raises(tk.ObjectNotFound):content.c4w_content_create(env.context['owner'],dict(entity_type='user',title='Wrong type'))


def test_unverified_profile_and_untrusted_image_are_rejected(env):
    env.session.add(db.C4wUserProfile(user_id='owner',profile_type='citizen',email_verified=False));env.session.commit()
    with pytest.raises(tk.NotAuthorized):create(env)
    profile=env.session.query(db.C4wUserProfile).one();profile.email_verified=True;env.session.commit()
    draft=create(env)
    with pytest.raises(tk.ValidationError):
        content.c4w_content_image_upload(env.context['owner'],args(draft,upload=FileStorage(BytesIO(b'<script>bad</script>'),filename='fake.png')))
    assert not content.c4w_content_draft_show(env.context['owner'],args(draft))['payload'].get('image_url')


def test_image_upload_stays_private_until_review_and_cleans_up_failure(env, monkeypatch):
    from ckanext.c4w.logic import project_images
    cleanup=[]
    monkeypatch.setattr(project_images,'store_image',lambda *a:('/uploads/news.jpg',lambda:cleanup.append(True)))
    draft=create(env)
    draft=content.c4w_content_image_upload(env.context['owner'],args(draft,upload=object()))
    assert env.session.query(db.C4wPost).one().image_url is None
    draft=content.c4w_content_submit(env.context['owner'],args(draft))
    draft=content.c4w_content_review(env.context['admin'],args(draft,decision='approve'))
    assert env.session.query(db.C4wPost).one().image_url=='/uploads/news.jpg'
    monkeypatch.setattr(env.session,'commit',lambda:(_ for _ in ()).throw(RuntimeError('storage failed')))
    with pytest.raises(RuntimeError):content.c4w_content_image_upload(env.context['owner'],args(draft,upload=object()))
    assert cleanup==[True]


@pytest.mark.parametrize('target', ['https://evil.example','//evil.example','/\\evil.example','/\t/evil.example','/%2f/evil.example','/citizens4water/submit/../../user/logout','/user/logout','/citizens4water/submit%0a'])
def test_registration_rejects_external_or_unrelated_destinations(env, monkeypatch,target):
    monkeypatch.setattr(helpers,'c4w_url',lambda *a,**kw:'/citizens4water/submit')
    assert access.contribution_next(target)=='/citizens4water/submit'


@pytest.mark.parametrize('target',['/citizens4water/submit/project','/citizens4water/submit/data?project_id=abc','/citizens4water/submit/news-events'])
def test_email_confirmation_recovers_original_destination_without_browser_session(env,target):
    owner=env.users['owner'];owner.state='pending'
    owner.activate=lambda:setattr(owner,'state','active');owner.is_deleted=lambda:False
    profile=db.C4wUserProfile(user_id=owner.id,profile_type='citizen',email_verified=False,
        verification_token_hash=registration._hash('return-token'),token_created=datetime.datetime.utcnow(),registration_next=target)
    env.session.add(profile);env.session.commit()
    result=registration.c4w_verify_email({},dict(token='return-token'))
    assert result['came_from']==target and owner.state=='active'
    assert registration.c4w_verify_email({},dict(token='return-token'))['state']=='invalid'


def test_real_image_bytes_use_the_image_upload_namespace(env, monkeypatch):
    from PIL import Image
    from ckanext.c4w.logic import uploads, project_images
    stream=BytesIO(); Image.new('RGB',(20,20),'blue').save(stream,format='PNG');stream.seek(0)
    stored=[]
    def store(upload,name,mime,limit,upload_to):
        image=Image.open(upload.stream)
        assert image.format=='JPEG' and image.size==(1100,400)
        stored.append(upload_to)
        return '/uploads/c4w/news.jpg',name,lambda:None
    monkeypatch.setattr(uploads,'_store',store)
    result=project_images.store_image(FileStorage(stream,filename='news.png'),'image3',{})
    assert result[0]=='/uploads/c4w/news.jpg' and stored==['c4w']

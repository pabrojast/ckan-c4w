"""Missing information: the list of gaps and filling them in."""
import pytest

pytest.importorskip('ckan')
import ckan.plugins.toolkit as tk
from ckanext.c4w import db
from ckanext.c4w.logic.action import editorial, gaps, project_write
from ckanext.c4w.migrate import sheet
from ckanext.c4w.tests.test_project_proposals import env as base_env  # noqa: F401


@pytest.fixture
def env(base_env, monkeypatch):
    previous = tk.check_access
    auth = dict(editorial.get_auth_functions(), **gaps.get_auth_functions())

    def check(name, context, data=None):
        if name in auth:
            if not auth[name](context, data or {})['success']:
                raise tk.NotAuthorized('Denied')
        else:
            previous(name, context, data)
    monkeypatch.setattr(tk, 'check_access', check)
    base_env.session.add(db.C4wPortalAdmin(user_id='other', granted_by='admin'))
    base_env.project = add(base_env, 'river', extras={
        'source': gaps.IMPORT_SOURCE, 'claimable': True,
        'status_unverified': True,
        'suggested_website': 'https://river.example.org/',
        'suggestion_evidence': 'Found on the agency site.'})
    add(base_env, 'lake', url='https://lake.example.org/',
        author_email='lake@example.org', data_url='https://data.example.org/',
        image1_url='lake.jpg', country=True,
        extras={'source': gaps.IMPORT_SOURCE})
    add(base_env, 'portal-own', extras={})
    return base_env


def add(env, slug, extras=None, country=False, **columns):
    project = db.C4wProject(
        slug=slug, name=slug.title(), approved=True, moderated=True,
        hidden=False, status='active', description='<p>Monitoring</p>',
        aim='<p>Clean water</p>', extras=db.dump_extras(extras or {}),
        **columns)
    env.session.add(project)
    env.session.flush()
    env.session.add(db.C4wTermLink(
        entity_type='project', entity_id=project.id, vocabulary='keyword',
        term='water', label='Water', sort_order=0))
    if country:
        env.session.add(db.C4wTermLink(
            entity_type='project', entity_id=project.id, vocabulary='country',
            term='US', label='US', sort_order=0))
    env.session.commit()
    return project


def listing(env, user='other', **params):
    return gaps.c4w_project_gaps(env.context[user], params)


def fill(env, **values):
    row = next(r for r in listing(env, q='river')['results'] if r['slug'] == 'river')
    data = dict(id=env.project.id, version=row['version'])
    data.update(values)
    return gaps.c4w_project_gap_fill(env.context['other'], data)


def test_only_content_admins_see_and_fill_gaps(env):
    for user in ('owner', 'editor'):
        with pytest.raises(tk.NotAuthorized):
            listing(env, user=user)
        with pytest.raises(tk.NotAuthorized):
            gaps.c4w_project_gap_fill(env.context[user], dict(id='river'))
    assert listing(env, user='admin')['count'] == 1


def test_list_counts_imported_projects_and_filters(env):
    result = listing(env)
    assert result['total'] == 2 and result['complete'] == 1
    assert [r['slug'] for r in result['results']] == ['river']
    river = result['results'][0]
    assert river['open'] == ['website', 'contact', 'data', 'image', 'status',
                             'country']
    assert river['suggested_website'] == 'https://river.example.org/'
    assert river['version'] and not river['blocked']
    assert result['counts']['website'] == 1 and result['counts']['description'] == 0
    assert listing(env, scope='all')['total'] == 3
    assert listing(env, gap='description')['count'] == 0
    assert listing(env, page='all')['results'][0].get('version') is None
    with pytest.raises(tk.ValidationError):
        listing(env, gap='nonsense')


def test_filling_writes_only_what_was_typed_and_clears_flags(env):
    result = fill(env, url='https://river.example.org/', author='',
                  author_email='team@river.example.org', status='completed')
    project = env.project
    db.Session.refresh(project)
    assert project.url == 'https://river.example.org/'
    assert project.author_email == 'team@river.example.org'
    assert project.status == 'completed' and project.author is None
    extras = db.load_extras(project.extras)
    assert 'status_unverified' not in extras
    assert 'suggested_website' not in extras and 'suggestion_evidence' not in extras
    assert extras['gap_fills'][-1]['by'] == 'other'
    assert set(extras['gap_fills'][-1]['filled']) == {'url', 'author_email', 'status'}
    assert result['open'] == ['data', 'image', 'country']
    assert project_write._revision(project).status == 'approved'


def test_confirming_the_shown_status_counts(env):
    result = fill(env, status='active')
    assert 'status' not in result['open']
    assert env.project.status == 'active'


def test_invalid_values_and_stale_pages_are_refused(env):
    with pytest.raises(tk.ValidationError) as raised:
        fill(env, author_email='not an email')
    assert 'author_email' in raised.value.error_dict
    db.Session.rollback()
    with pytest.raises(tk.ValidationError):
        gaps.c4w_project_gap_fill(env.context['other'], dict(
            id=env.project.id, version='old', url='https://x.example.org/'))
    db.Session.rollback()
    with pytest.raises(tk.ValidationError):
        gaps.c4w_project_gap_fill(env.context['other'], dict(
            id=env.project.id, version='old', dismiss=['website']))
    db.Session.rollback()
    with pytest.raises(tk.ValidationError):
        fill(env)
    db.Session.rollback()
    with pytest.raises(tk.ValidationError):
        fill(env, dismiss=['everything'])


def test_not_available_takes_a_gap_off_the_list_until_restored(env):
    result = fill(env, dismiss=['website', 'data'])
    assert result['dismissed'] == ['website', 'data']
    assert 'website' not in result['open']
    assert env.project.url is None
    assert listing(env, gap='website')['count'] == 0
    assert listing(env, gap='website', dismissed='1')['count'] == 1
    result = fill(env, restore=['website'])
    assert result['dismissed'] == ['data'] and 'website' in result['open']


def test_an_uploaded_image_fills_the_image_gap(env, monkeypatch):
    from io import BytesIO
    from werkzeug.datastructures import FileStorage
    from ckanext.c4w.logic import project_images
    seen = {}

    def store(upload, box, crop):
        seen.update(box=box, crop=crop)
        return '/uploads/c4w/river.jpg', lambda: None
    monkeypatch.setattr(project_images, 'store_image', store)
    result = fill(env, image1=FileStorage(BytesIO(b'img'), filename='r.png'),
                  image1_credit='Agency')
    assert env.project.image1_url == '/uploads/c4w/river.jpg'
    assert env.project.image1_credit == 'Agency'
    assert seen['box'] == 'image1' and 'image' not in result['open']
    audit = db.load_extras(env.project.extras)['gap_fills'][-1]
    assert audit['filled'] == ['image1', 'image1_credit']


def test_plain_text_becomes_paragraphs(env):
    env.project.description = ''
    db.Session.commit()
    fill(env, description='First line.\n\nSecond part.')
    assert env.project.description == '<p>First line.</p><p>Second part.</p>'
    assert gaps._as_html('A & <b>B</b>\n\nC') == '<p>A &amp; &lt;b&gt;B&lt;/b&gt;</p><p>C</p>'
    assert gaps._as_html('<p>Pasted</p>') == '<p>Pasted</p>'


def test_an_open_draft_is_never_overwritten(env):
    db.Session.add(db.C4wProjectRevision(
        project_id=env.project.id, created_by='editor', status='draft',
        payload='{}', version=1))
    db.Session.commit()
    row = listing(env, q='river')['results'][0]
    assert row['blocked']
    with pytest.raises(tk.ValidationError):
        gaps.c4w_project_gap_fill(env.context['other'], dict(
            id=env.project.id, version=row['version'], url='https://x.example.org/'))


def test_a_filled_row_is_protected_from_the_next_import(env):
    import datetime
    earlier = db._utcnow() - datetime.timedelta(hours=1)
    extras = db.load_extras(env.project.extras)
    extras['imported_at'] = earlier.strftime('%Y-%m-%dT%H:%M:%S')
    env.project.extras = db.dump_extras(extras)
    env.project.modified = earlier
    db.Session.commit()
    assert sheet.SheetImporter._protected(env.project) is None
    fill(env, dismiss=['image'])
    assert sheet.SheetImporter._protected(env.project) == 'edited'


def test_import_stores_web_search_suggestions_only_for_empty_fields():
    from ckanext.c4w.tests.test_sheet_import import row
    found = {'suggestion': {'website': 'maybe.example.org',
                            'data_url': 'https://maybe.example.org/data',
                            'evidence': 'Partner page.'}}
    paper = sheet.map_row(row(website='https://www.mdpi.com/x'), found)
    assert paper['columns']['url'] is None
    assert paper['extras']['suggested_website'] == 'https://maybe.example.org'
    assert paper['extras']['suggested_data_url'] == 'https://maybe.example.org/data'
    assert paper['extras']['suggestion_evidence'] == 'Partner page.'
    has_site = sheet.map_row(row(data_portal='https://d.example.org'), found)
    assert 'suggested_website' not in has_site['extras']
    assert 'suggestion_evidence' not in has_site['extras']

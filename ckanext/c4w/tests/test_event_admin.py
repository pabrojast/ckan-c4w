"""Imported-event editing, atomic publication and calendar-day boundaries."""
import datetime as dt
from urllib.parse import urlsplit, parse_qs
import pytest
pytest.importorskip('ckan')
from flask import Flask, Blueprint, url_for
import ckan.plugins.toolkit as tk
from ckanext.c4w import db
from ckanext.c4w.logic import event_dates, helpers
from ckanext.c4w.logic.action import content_write as content, events
from ckanext.c4w.tests.test_content_proposals import env, project_env


def test_imported_event_drafts_publication_versions_and_visibility(env):
    row = db.C4wEvent(title='Imported fair', slug='imported-fair', approved=True, hidden=True,
        description='Original', country='Belgium', hour=dt.time(11), timezone='Europe/Brussels',
        start_date=dt.datetime(2025,6,18), end_date=dt.datetime(2925,6,18), created_by=None)
    env.session.add(row);env.session.commit()
    admin=env.context['admin'];params=dict(entity_type='event',id=row.id)
    draft=content.c4w_content_draft_show(admin,params)
    assert draft['payload']['country']=='BE' and draft['payload']['hour']=='11:00:00'
    saved=content.c4w_event_admin_save(admin,dict(id=row.id,version=0,mode='draft',end_date='2025-06-18',title='Corrected fair'))
    assert row.title=='Imported fair' and row.end_date.year==2925
    with pytest.raises(tk.ValidationError):
        content.c4w_event_admin_save(admin,dict(id=row.id,version=0,mode='publish'))
    published=content.c4w_event_admin_save(admin,dict(id=row.id,version=saved['version'],mode='publish'))
    assert published['status']=='approved' and published['version']==2
    assert row.title=='Corrected fair' and row.end_date.year==2025 and row.country=='BE'
    assert row.hour==dt.time(11) and row.timezone=='Europe/Brussels'
    assert row.hidden and row.approved and row.created_by is None and row.slug=='imported-fair'
    with pytest.raises(tk.ObjectNotFound):events.c4w_event_show({},dict(id=row.id))


def test_admin_create_validation_and_permissions(env):
    for role in ('owner','other'):
        with pytest.raises(tk.NotAuthorized):content.c4w_event_admin_save(env.context[role],dict(title='No',mode='publish'))
    with pytest.raises(tk.NotAuthorized):content.c4w_event_admin_save({},dict(title='No',mode='draft'))
    for values in [dict(mode='publish'),dict(mode='invalid'),dict(mode='draft',hour='25:61'),dict(mode='draft',country='Made up')]:
        with pytest.raises(tk.ValidationError):content.c4w_event_admin_save(env.context['admin'],dict(title='Invalid',**values))
        env.session.rollback()
        assert env.session.query(db.C4wEvent).count()==0
    draft=content.c4w_event_admin_save(env.context['admin'],dict(title='New event',mode='draft'))
    row=env.session.query(db.C4wEvent).one()
    assert not row.approved
    with pytest.raises(tk.ValidationError):content.c4w_event_admin_save(env.context['admin'],dict(id=row.id,version=draft['version'],mode='publish',title='Invalid replacement'))
    env.session.rollback()
    assert row.title=='New event' and not row.approved
    result=content.c4w_event_admin_save(env.context['admin'],dict(id=row.id,version=draft['version'],mode='publish',description='Workshop',start_date='2026-10-20',hour='09:30',timezone='Europe/Brussels'))
    assert result['published'] and events.c4w_event_show({},dict(id=row.id))['title']=='New event'


def test_unknown_legacy_country_can_be_retained_but_not_introduced(env):
    row=db.C4wEvent(title='Legacy',slug='legacy',description='Legacy event',country='River basin region',start_date=dt.datetime(2025,6,18),hour=dt.time(11))
    env.session.add(row);env.session.commit()
    saved=content.c4w_event_admin_save(env.context['admin'],dict(id=row.id,version=0,mode='publish'))
    assert row.country=='River basin region' and row.hour==dt.time(11)
    with pytest.raises(tk.ValidationError):content.c4w_event_admin_save(env.context['admin'],dict(id=row.id,version=saved['version'],mode='publish',country='Another unknown region'))


@pytest.mark.parametrize('event,expected',[
    ({'start_date':'2026-10-20','timezone':'UTC'},'upcoming'),
    ({'start_date':'2026-10-20','timezone':'Pacific/Honolulu'},'upcoming'),
    ({'start_date':'2026-10-20','timezone':'Pacific/Kiritimati'},'past'),
    ({'start_date':'2026-10-19','end_date':'2026-10-20','timezone':'UTC'},'ongoing'),
    ({'start_date':'2026-10-19','end_date':None},'past'),
    ({'start_date':'2026-10-20','timezone':'Invalid/Zone'},'upcoming'),
    ({'start_date':None},'undated'),
])
def test_local_calendar_days(event,expected):
    assert event_dates.status(event,dt.datetime(2026,10,20,23,30,tzinfo=dt.timezone.utc))==expected


def test_upcoming_filter_precedes_pagination_and_counts_share_rules(env):
    today=dt.datetime.now(dt.timezone.utc).date()
    for i in range(25):
        env.session.add(db.C4wEvent(title='History',slug='history-'+str(i),approved=True,start_date=dt.datetime(2025,1,1)+dt.timedelta(days=i)))
    for slug,start,end,hidden in [('today',today,None,False),('ongoing',today-dt.timedelta(days=1),today,False),('future',today+dt.timedelta(days=10),None,False),('hidden',today,None,True)]:
        env.session.add(db.C4wEvent(title='Workshop',slug=slug,approved=True,hidden=hidden,timezone='UTC',start_date=dt.datetime.combine(start,dt.time()),end_date=dt.datetime.combine(end,dt.time()) if end else None))
    env.session.commit()
    result=events.c4w_event_list({},dict(period='upcoming',page_size=3))
    assert result['count']==3 and result['upcoming_total']==3 and len(result['upcoming'])==3 and result['pages']==1
    assert [x['slug'] for x in result['results']]==['ongoing','today','future']
    all_=events.c4w_event_list({},dict(page_size=3))
    assert all_['count']==28 and len(all_['upcoming'])==3
    past=events.c4w_event_list({},dict(period='past',q='History',page_size=10,page=2))
    assert past['count']==25 and past['upcoming_total']==0 and len(past['past'])==10
    assert events.c4w_event_list({},dict(q='Workshop'))['upcoming_total']==3
    with pytest.raises(tk.ValidationError):events.c4w_event_list({},dict(period='bad'))


@pytest.mark.parametrize('blueprint,prefix',[('c4w','/citizens4water'),('c4w_preview','/c4w-preview')])
def test_pagination_preserves_bound_route_filters_and_mount(monkeypatch,blueprint,prefix):
    app=Flask(__name__);bp=Blueprint(blueprint,__name__,url_prefix=prefix)
    bp.add_url_rule('/admin/<section>','admin_inventory',lambda section: '')
    bp.add_url_rule('/admin/projects','admin_projects',lambda: '')
    app.register_blueprint(bp);monkeypatch.setattr(tk,'url_for',url_for)
    for path in ('/admin/news','/admin/projects'):
        with app.test_request_context(prefix+path+'?q=test&visibility=hidden&topic=water&topic=climate&section=evil&_scheme=x&page=1'):
            url=helpers.c4w_page_url(2);parts=urlsplit(url);query=parse_qs(parts.query)
            assert parts.path==prefix+path and query['page']==['2'] and query['q']==['test']
            assert query['topic']==['water','climate'] and query['visibility']==['hidden']
            assert '_scheme' not in query
            if path=='/admin/news':assert 'section' not in query

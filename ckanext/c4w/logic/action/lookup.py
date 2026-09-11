"""Small public lookup payloads, with private projects scoped to their editors."""
import re
import unicodedata

import ckan.plugins.toolkit as tk
from ckan.model.meta import Session
from sqlalchemy import or_
from ckanext.c4w import db
from ckanext.c4w.logic.action import _common


def normalize(value):
    text = ''.join(c for c in unicodedata.normalize('NFKD', str(value or ''))
                   if not unicodedata.combining(c)).casefold()
    return re.sub(r'[^\w]+', ' ', text).replace('_', ' ').strip()


def rank(query, title, slug):
    q, title, slug = normalize(query), normalize(title), normalize(slug)
    if not q:
        return 0
    if not all(part in title + ' ' + slug for part in q.split()):
        return None
    return (1000 if title == q else 900 if title.startswith(q) else
            800 if any(w.startswith(q) for w in title.split()) else
            700 if q in title else 600 if q in slug else 200) - min(len(title), 80)


@tk.side_effect_free
def c4w_lookup(context, data_dict):
    tk.check_access('c4w_lookup', context, data_dict)
    db.ensure_mappers()
    kind = data_dict.get('kind')
    if kind == 'ckan_organisation':
        import ckan.model as model
        rows = Session.query(model.Group.id, model.Group.title, model.Group.name).filter(
            model.Group.type == 'organization', model.Group.state == 'active').all()
    elif kind in ('organisation', 'project'):
        cls = db.C4wOrganisation if kind == 'organisation' else db.C4wProject
        query = Session.query(cls.id, cls.name, cls.slug)
        public = cls.approved.is_(True)
        if kind == 'project':
            public = public & cls.hidden.isnot(True)
            user = context.get('auth_user_obj')
            if user:
                if not _common.is_sysadmin(context):
                    editors = Session.query(db.C4wRelation.subject_id).filter_by(
                        subject_type='project', predicate='editor', object_type='user', object_id=user.id)
                    query = query.filter(or_(public, cls.created_by == user.id, cls.id.in_(editors)))
            else:
                query = query.filter(public)
        else:
            query = query.filter(public)
        rows = query.all()
    else:
        raise tk.ObjectNotFound(tk._('Unknown lookup.'))
    ids = set(str(data_dict.get('ids') or '').split(','))
    q = str(data_dict.get('q') or '')[:200]
    results = []
    for id_, title, slug in rows:
        if data_dict.get('ids') and id_ not in ids:
            continue
        score = rank(q, title, slug)
        if score is not None:
            results.append((score, dict(id=id_, title=title or slug, name=slug)))
    results.sort(key=lambda x: (-x[0], normalize(x[1]['title']), x[1]['id']))
    return {'results': [row for _, row in results[:20]], 'more': len(results) > 20}


def get_actions():
    return {'c4w_lookup': c4w_lookup}


@tk.auth_allow_anonymous_access
def lookup_auth(context, data_dict):
    return {'success': True}


def get_auth_functions():
    return {'c4w_lookup': lookup_auth}

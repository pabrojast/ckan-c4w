# encoding: utf-8
"""Moderation and 'my submissions' write/read actions.

The public catalogue never writes. These three actions are the whole of
what a signed-in visitor and a sysadmin can change from the C4W chrome
until the submission forms land.
"""
import logging

import ckan.plugins.toolkit as tk

from ckanext.c4w import constants, db
from ckanext.c4w.logic.action import _common

log = logging.getLogger(__name__)


def _models():
    """entity_type -> mapped class. Built after mappers exist."""
    db.ensure_mappers()
    return {
        'project': db.C4wProject,
        'organisation': db.C4wOrganisation,
        'resource': db.C4wResource,
        'platform': db.C4wPlatform,
        'event': db.C4wEvent,
        'post': db.C4wPost,
    }


def _title(row):
    return getattr(row, 'name', None) or getattr(row, 'title', None) or u''


def _queue_clause(model_cls):
    """Rows a reviewer still has to look at: not public, or hidden."""
    parts = []
    if hasattr(model_cls, 'approved'):
        parts.append(model_cls.approved.isnot(True))
    if hasattr(model_cls, 'hidden'):
        parts.append(model_cls.hidden.is_(True))
    return parts


@tk.side_effect_free
def c4w_my_submissions(context, data_dict):
    """Every C4W row the requester owns or edits.

    Grouped by entity type so the account page can render one table per
    surface. An empty group is omitted rather than shown as a zero-row table.
    """
    tk.check_access('c4w_my_submissions', context, data_dict)
    user = context.get('auth_user_obj')
    if user is None or not getattr(user, 'id', None):
        raise tk.NotAuthorized(tk._('Not authorized'))

    from ckan.model.meta import Session
    from sqlalchemy import or_

    models = _models()
    out = []
    for entity_type, model_cls in models.items():
        clauses = []
        if hasattr(model_cls, 'created_by'):
            clauses.append(model_cls.created_by == user.id)
        if hasattr(model_cls, 'author_id'):
            clauses.append(model_cls.author_id == user.id)
        editor_ids = (
            Session.query(db.C4wRelation.subject_id)
            .filter(db.C4wRelation.subject_type == entity_type,
                    db.C4wRelation.predicate == u'editor',
                    db.C4wRelation.object_type == u'user',
                    db.C4wRelation.object_id == user.id)
        )
        clauses.append(model_cls.id.in_(editor_ids))
        rows = (Session.query(model_cls)
                .filter(or_(*clauses))
                .order_by(model_cls.modified.desc().nullslast())
                .all())
        if not rows:
            continue
        items = []
        for row in rows:
            item = db.entity_dictize(entity_type, row)
            item['title'] = _title(row)
            items.append(item)
        # 'rows', never 'items': Jinja treats dict.items as the method.
        out.append({'entity_type': entity_type, 'rows': items})
    return {'groups': out}


@tk.side_effect_free
def c4w_moderation_list(context, data_dict):
    """The reviewer's queue: unapproved or hidden rows, grouped by type."""
    tk.check_access('c4w_moderation_list', context, data_dict)

    from ckan.model.meta import Session
    from sqlalchemy import or_

    models = _models()
    out = []
    for entity_type in constants.MODERATED_ENTITY_TYPES:
        model_cls = models[entity_type]
        parts = _queue_clause(model_cls)
        if not parts:
            continue
        rows = (Session.query(model_cls)
                .filter(or_(*parts))
                .order_by(model_cls.modified.desc().nullslast())
                .all())
        items = []
        for row in rows:
            item = db.entity_dictize(entity_type, row)
            item['title'] = _title(row)
            items.append(item)
        out.append({
            'entity_type': entity_type,
            'rows': items,
            'can_hide': entity_type in constants.ENTITY_HAS_HIDDEN,
            'can_feature': entity_type in constants.ENTITY_HAS_FEATURED,
        })
    return {'groups': out}


def c4w_entity_moderate(context, data_dict):
    """approve / hide / feature one row.

    ``entity_type`` is validated against the closed lists in constants.py
    before any mapper is touched. An unknown type is NotFound, never a SQL
    identifier.
    """
    tk.check_access('c4w_entity_moderate', context, data_dict)
    data_dict = dict(data_dict or {})
    entity_type = u'%s' % (data_dict.get('entity_type') or u'')
    operation = u'%s' % (data_dict.get('operation') or u'')
    error = constants.moderate_error(entity_type, operation)
    if error:
        raise tk.ObjectNotFound(tk._('Not found'))

    row = _common.get_by_reference(
        _models()[entity_type],
        data_dict.get('id') or data_dict.get('slug'))
    if row is None:
        raise tk.ObjectNotFound(tk._('Not found'))

    from ckan.model.meta import Session

    if operation == 'approve':
        row.approved = True
        if hasattr(row, 'moderated'):
            row.moderated = True
        if hasattr(row, 'hidden'):
            row.hidden = False
    elif operation == 'hide':
        row.hidden = not bool(row.hidden)
    elif operation == 'feature':
        row.featured = not bool(row.featured)

    if hasattr(row, 'modified'):
        row.modified = db._utcnow()
    Session.add(row)
    Session.commit()
    return db.entity_dictize(entity_type, row)


def get_actions():
    return {
        'c4w_my_submissions': c4w_my_submissions,
        'c4w_moderation_list': c4w_moderation_list,
        'c4w_entity_moderate': c4w_entity_moderate,
    }


def c4w_my_submissions_auth(context, data_dict):
    if _common.is_authenticated(context):
        return {'success': True}
    return {'success': False, 'msg': tk._('Not authorized')}


def c4w_moderation_list_auth(context, data_dict):
    if _common.is_sysadmin(context):
        return {'success': True}
    return {'success': False, 'msg': tk._('Not authorized')}


def c4w_entity_moderate_auth(context, data_dict):
    if _common.is_sysadmin(context):
        return {'success': True}
    return {'success': False, 'msg': tk._('Not authorized')}


def get_auth_functions():
    return {
        'c4w_my_submissions': c4w_my_submissions_auth,
        'c4w_moderation_list': c4w_moderation_list_auth,
        'c4w_entity_moderate': c4w_entity_moderate_auth,
    }

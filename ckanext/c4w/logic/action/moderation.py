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
    """entity_type -> mapped class. Built after mappers exist.

    Read from ``db.ENTITY_CLASSES`` rather than spelled out again: a new
    entity registered there appears in the account page and the reviewer's
    queue without a second edit here.
    """
    db.ensure_mappers()
    return dict(db.ENTITY_CLASSES)


def _title(row):
    return getattr(row, 'name', None) or getattr(row, 'title', None) or u''


def _queue_clause(model_cls):
    """Unpublished content; hiding a published item never requests a review."""
    parts = []
    if hasattr(model_cls, 'approved'):
        parts.append(model_cls.approved.isnot(True))
    if hasattr(model_cls, 'status') and not hasattr(model_cls, 'approved'):
        parts.append(model_cls.status != constants.POST_STATUS_PUBLISHED)
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
        revision_model = db.C4wProjectRevision if entity_type == 'project' else db.C4wContentRevision
        revision_query = Session.query(revision_model.project_id if entity_type == 'project' else revision_model.entity_id).filter(
            or_(revision_model.created_by == user.id, revision_model.modified_by == user.id, revision_model.submitted_by == user.id))
        if entity_type != 'project':
            revision_query = revision_query.filter(revision_model.entity_type == entity_type)
        clauses.append(model_cls.id.in_(revision_query))
        rows = (Session.query(model_cls)
                .filter(or_(*clauses))
                .order_by(model_cls.modified.desc().nullslast())
                .all())
        if not rows:
            continue
        items = []
        for row in rows:
            item = db.entity_dictize(entity_type, row, include_private=True)
            item['title'] = _title(row)
            if entity_type not in ('project', 'post', 'event'):
                from ckanext.c4w.logic.action.editorial import revision
                rev = revision(entity_type, row)
                if rev:
                    item['proposal'] = dict(status=rev.status, review_note=rev.review_note)
            if entity_type == 'project':
                from ckanext.c4w.logic.action.project_write import _revision, _dict
                item['proposal'] = _dict(row, _revision(row))
                item['modified'] = item['proposal']['modified'] or item.get('modified')
            if entity_type in ('post', 'event'):
                from ckanext.c4w.logic.action import content_write
                item['proposal'] = content_write.dictize(entity_type, row, content_write.revision(entity_type, row))
                item['modified'] = item['proposal']['modified'] or item.get('modified')
            items.append(item)
        # 'rows', never 'items': Jinja treats dict.items as the method.
        out.append({'entity_type': entity_type, 'rows': items})
    return {'groups': out}


def _inventory(section):
    from ckanext.c4w.logic import query
    entry = next((entry for entry in constants.ADMIN_SECTIONS if entry[0] == section), None)
    if entry is None:
        raise tk.ObjectNotFound(tk._('Not found'))
    _section, entity, label = entry
    model = _models()[entity]
    title = 'name' if hasattr(model, 'name') else 'title'
    spec = query.ListingSpec(
        entity_type=entity, model_cls=model,
        search_columns=tuple(c for c in (title, 'slug', 'search_text') if hasattr(model, c)),
        orderings={'name': lambda m: [getattr(m, title).asc(), m.id.asc()]},
        default_order='name', page_size=25,
        defer_columns=tuple(c for c in ('geom_geojson', 'mapping_json') if hasattr(model, c)),
    )
    filters = ([model.is_training_resource.is_(section == 'training_resources')]
               if entity == 'resource' else [])
    return spec, filters, label


@tk.side_effect_free
def c4w_admin_entity_list(context, data_dict):
    """Complete, searchable inventories; never used by a public catalogue."""
    tk.check_access('c4w_admin_entity_list', context, data_dict)
    from ckanext.c4w.logic import query
    from ckanext.c4w.logic.visibility import is_public
    from sqlalchemy import and_
    data = dict(data_dict or {})
    spec, filters, _label = _inventory(data.get('section'))
    model = spec.model_cls
    approved = (model.status == constants.POST_STATUS_PUBLISHED
                if spec.entity_type == 'post' else model.approved.is_(True))
    visibility = data.get('visibility', 'all')
    if visibility == 'published':
        filters.append(and_(approved, model.hidden.isnot(True)))
    elif visibility == 'hidden':
        filters.append(model.hidden.is_(True))
    elif visibility == 'unpublished':
        filters.append(model.approved.isnot(True) if spec.entity_type != 'post'
                       else (model.status != constants.POST_STATUS_PUBLISHED) | model.status.is_(None))
    elif visibility != 'all':
        raise tk.ValidationError({'visibility': [tk._('Invalid visibility filter.')]})
    result = query.build_listing(spec, data, include_private=True, filters=filters)
    for item in result['results']:
        item['public_visible'] = is_public(spec.entity_type, item)
    return result


@tk.side_effect_free
def c4w_admin_summary(context, data_dict):
    tk.check_access('c4w_admin_summary', context, data_dict)
    from ckan.model.meta import Session
    from ckanext.c4w.logic import query
    sections = []
    for section, entity, label in constants.ADMIN_SECTIONS:
        spec, filters, _label = _inventory(section)
        base = Session.query(spec.model_cls).filter(*filters)
        sections.append(dict(section=section, entity_type=entity, label=label,
                             total=base.count(),
                             published=query._public_filter(base, spec.model_cls).count(),
                             hidden=base.filter(spec.model_cls.hidden.is_(True)).count()))
    return {'sections': sections}


@tk.side_effect_free
def c4w_admin_project_list(context, data_dict):
    tk.check_access('c4w_admin_project_list', context, data_dict)
    return c4w_admin_entity_list(context, dict(data_dict or {}, section='projects'))


@tk.side_effect_free
def c4w_admin_platform_list(context, data_dict):
    tk.check_access('c4w_admin_platform_list', context, data_dict)
    return c4w_admin_entity_list(context, dict(data_dict or {}, section='platforms'))


@tk.side_effect_free
def c4w_moderation_list(context, data_dict):
    """The reviewer's queue: unapproved or hidden rows, grouped by type."""
    tk.check_access('c4w_moderation_list', context, data_dict)

    from ckan.model.meta import Session
    from sqlalchemy import or_

    models = _models()
    out = []
    revisions = {r.project_id: r for r in Session.query(db.C4wProjectRevision).all()}
    from ckanext.c4w.logic.action.project_write import _dict
    from ckanext.c4w.logic.action import content_write
    content_revisions = Session.query(db.C4wContentRevision).all()
    content_keys = {(r.entity_type, r.entity_id) for r in content_revisions
                    if r.entity_type in content_write.FIELDS or r.status != 'pending'}
    content_requests = []
    for rev in content_revisions:
        if rev.status == 'pending' and rev.entity_type in content_write.FIELDS:
            row = Session.query(models[rev.entity_type]).filter_by(id=rev.entity_id).first()
            if row is not None:
                content_requests.append(content_write.dictize(rev.entity_type, row, rev))
    project_requests = [_dict(p, revisions[p.id]) for p in Session.query(db.C4wProject).filter(
        db.C4wProject.id.in_([key for key, r in revisions.items() if r.status == 'pending'])).all()]
    import ckan.model as model
    for proposal in project_requests + content_requests:
        author = model.User.get(proposal.get('submitted_by'))
        proposal['submitted_name'] = (author.fullname or author.name) if author else tk._('Previous contributor')
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
            if (entity_type, row.id) in content_keys:
                continue
            if entity_type == 'project' and row.id in revisions:
                continue
            if entity_type == 'dataset' and not row.submitted_at and not any(r.entity_type == entity_type and r.entity_id == row.id and r.status == 'pending' for r in content_revisions):
                continue
            item = db.entity_dictize(entity_type, row, include_private=True)
            item['title'] = _title(row)
            items.append(item)
        out.append({
            'entity_type': entity_type,
            'rows': items,
            'can_hide': entity_type in constants.ENTITY_HAS_HIDDEN,
            'can_feature': entity_type in constants.ENTITY_HAS_FEATURED,
            'can_process': entity_type in constants.ENTITY_HAS_PROCESS,
        })
    return {'groups': out, 'project_requests': project_requests, 'content_requests': content_requests}


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
        if entity_type != 'project' and Session.query(db.C4wContentRevision).filter_by(entity_type=entity_type, entity_id=row.id).first():
            raise tk.ValidationError({'message': [tk._('Review the submitted contribution before approving it.')]})
        if entity_type == 'project' and Session.query(db.C4wProjectRevision).filter_by(project_id=row.id).first():
            raise tk.ValidationError({'message': [tk._('Review and approve the submitted project revision.')]})
        row.approved = True
        if hasattr(row, 'moderated'):
            row.moderated = True
    elif operation == 'hide':
        row.hidden = not bool(row.hidden)
    elif operation == 'feature':
        row.featured = not bool(row.featured)
    elif operation == 'process':
        # Re-run the pipeline. Imported lazily: the data package is only
        # needed by the one entity that has a pipeline.
        from ckanext.c4w.data import jobs
        Session.add(row)
        Session.commit()
        jobs.dispatch(row.id, force=True)
        row = _common.get_by_reference(_models()[entity_type], row.id)
        return db.entity_dictize(entity_type, row, include_private=True)

    if hasattr(row, 'modified'):
        row.modified = db._utcnow()
    Session.add(row)
    Session.commit()
    return db.entity_dictize(entity_type, row)


def get_actions():
    return {
        'c4w_my_submissions': c4w_my_submissions,
        'c4w_admin_entity_list': c4w_admin_entity_list,
        'c4w_admin_summary': c4w_admin_summary,
        'c4w_admin_project_list': c4w_admin_project_list,
        'c4w_admin_platform_list': c4w_admin_platform_list,
        'c4w_moderation_list': c4w_moderation_list,
        'c4w_entity_moderate': c4w_entity_moderate,
    }


@tk.auth_allow_anonymous_access
def c4w_my_submissions_auth(context, data_dict):
    # Allow the auth function to run for strangers so the view can redirect
    # to login instead of CKAN turning "not logged in" into a 500.
    if _common.is_authenticated(context):
        return {'success': True}
    return {'success': False, 'msg': tk._('Not authorized')}


@tk.auth_allow_anonymous_access
def c4w_moderation_list_auth(context, data_dict):
    if _common.is_content_admin(context):
        return {'success': True}
    return {'success': False, 'msg': tk._('Not authorized')}


@tk.auth_allow_anonymous_access
def c4w_entity_moderate_auth(context, data_dict):
    if _common.is_content_admin(context):
        return {'success': True}
    return {'success': False, 'msg': tk._('Not authorized')}


def get_auth_functions():
    return {
        'c4w_my_submissions': c4w_my_submissions_auth,
        'c4w_admin_entity_list': c4w_moderation_list_auth,
        'c4w_admin_summary': c4w_moderation_list_auth,
        'c4w_admin_project_list': c4w_moderation_list_auth,
        'c4w_admin_platform_list': c4w_moderation_list_auth,
        'c4w_moderation_list': c4w_moderation_list_auth,
        'c4w_entity_moderate': c4w_entity_moderate_auth,
    }

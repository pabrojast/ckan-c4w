"""Confirmed permanent deletion of hidden C4W content and durable cleanup."""
import hashlib
import json

import ckan.plugins.toolkit as tk
from sqlalchemy import or_, inspect
from ckanext.c4w import db
from ckanext.c4w.logic import integrity
from ckanext.c4w.logic.action import _common


def _error(message):
    raise tk.ValidationError({'message': [tk._(message)]})


def _row(data, locked=False):
    db.ensure_mappers()
    if locked:
        integrity.lock(db.Session)
    entity = data.get('entity_type')
    cls = dict(db.ENTITY_CLASSES).get(entity)
    if cls is None:
        raise tk.ObjectNotFound(tk._('Not found'))
    query = db.Session.query(cls).filter_by(id=data.get('id'))
    row = (query.populate_existing().with_for_update() if locked else query).first()
    if row is None:
        raise tk.ObjectNotFound(tk._('Not found'))
    return entity, row


def dependencies(entity, row):
    session = db.Session
    models = dict(db.ENTITY_CLASSES)
    found = {}

    def add(kind, reference, reason):
        if (kind, reference) == (entity, row.id):
            return
        cls = models.get(kind)
        target = session.query(cls).filter_by(id=reference).first() if cls else None
        found[(kind, reference)] = dict(
            entity_type=kind, id=reference, slug=getattr(target, 'slug', None),
            name=(getattr(target, 'name', None) or getattr(target, 'title', None) or reference),
            reason=reason, is_training_resource=getattr(target, 'is_training_resource', False))

    for rel in session.query(db.C4wRelation).filter_by(object_type=entity, object_id=row.id):
        add(rel.subject_type, rel.subject_id, tk._('Linked content'))
    for kind, fields in integrity.DIRECT.items():
        cls = models[kind]
        for field, target in fields.items():
            if target == entity:
                for other in session.query(cls.id).filter(getattr(cls, field) == row.id):
                    add(kind, other.id, tk._('Linked content'))
    if entity == 'organisation':
        for profile in session.query(db.C4wUserProfile).filter_by(c4w_organisation_id=row.id):
            add('user', profile.user_id, tk._('Linked organisation account'))
    for cls in (db.C4wProjectRevision, db.C4wContentRevision):
        for rev in session.query(cls).filter(cls.status.in_(('draft', 'pending'))):
            kind = 'project' if cls is db.C4wProjectRevision else rev.entity_type
            reference = rev.project_id if cls is db.C4wProjectRevision else rev.entity_id
            if (entity, row.id) in integrity.payload_refs(kind, db.load_extras(rev.payload)):
                add(kind, reference, tk._('Draft or pending proposal'))
    return sorted(found.values(), key=lambda d: (d['entity_type'], d['name'], d['id']))


def _revision_rows(entity, row):
    if entity == 'project':
        return db.Session.query(db.C4wProjectRevision).filter_by(project_id=row.id).all()
    return db.Session.query(db.C4wContentRevision).filter_by(entity_type=entity, entity_id=row.id).all()


def _preview(entity, row):
    # Include revisions and files: editing a draft must invalidate confirmation.
    columns = {c.name: getattr(row, c.name) for c in inspect(type(row)).columns
               if c.name not in ('total_accesses', 'total_downloads')}
    revisions = _revision_rows(entity, row)
    files = db.Session.query(db.C4wDatasetFile).filter_by(dataset_id=row.id).all() if entity == 'dataset' else []
    related = dependencies(entity, row)
    fingerprint = [columns, sorted((r.id, r.version, r.payload) for r in revisions),
                   sorted((f.id, f.stored_name) for f in files), related]
    version = hashlib.sha256(json.dumps(fingerprint, sort_keys=True, default=str).encode()).hexdigest()
    blocked = []
    if not row.hidden:
        blocked.append(tk._('Hide this item before deleting it.'))
    if related:
        blocked.append(tk._('Remove the links from the related items before deleting this item.'))
    if entity == 'dataset' and row.processing_status in ('processing', 'queued'):
        blocked.append(tk._('Wait for dataset processing to finish before deleting it.'))
    return dict(entity_type=entity, id=row.id, slug=row.slug,
                title=getattr(row, 'name', None) or getattr(row, 'title', None),
                hidden=bool(row.hidden), version=version, dependencies=related,
                files=[dict(name=f.original_name, size_bytes=f.size_bytes) for f in files],
                revisions=len(revisions), blocked=blocked, can_delete=not blocked)


@tk.side_effect_free
def c4w_entity_delete_preview(context, data_dict):
    tk.check_access('c4w_entity_delete_preview', context, data_dict)
    return _preview(*_row(data_dict))


def delete_row(context, entity, row):
    """Caller holds the C4W lock and has checked authorization/dependencies."""
    session = db.Session
    stored = []
    if entity == 'dataset':
        files = session.query(db.C4wDatasetFile).filter_by(dataset_id=row.id).all()
        for item in files:
            # A blob referenced by another dataset is shared, not ours to erase.
            shared = session.query(db.C4wDatasetFile.id).filter(
                db.C4wDatasetFile.dataset_id != row.id,
                or_(db.C4wDatasetFile.stored_name == item.stored_name,
                    db.C4wDatasetFile.url == item.url)).first()
            if item.stored_name and not shared:
                stored.append(item.stored_name)
        session.query(db.C4wDatasetFile).filter_by(dataset_id=row.id).delete(synchronize_session=False)
        session.query(db.C4wDashboardBundle).filter_by(dataset_id=row.id).delete(synchronize_session=False)
    for rev in _revision_rows(entity, row):
        session.delete(rev)
    session.query(db.C4wTermLink).filter_by(entity_type=entity, entity_id=row.id).delete(synchronize_session=False)
    session.query(db.C4wRelation).filter(or_(
        (db.C4wRelation.subject_type == entity) & (db.C4wRelation.subject_id == row.id),
        (db.C4wRelation.object_type == entity) & (db.C4wRelation.object_id == row.id)
    )).delete(synchronize_session=False)
    audit = db.C4wDeletion(entity_type=entity, entity_id=row.id, legacy_id=row.legacy_id,
                          slug=row.slug, deleted_by=context['auth_user_obj'].id,
                          pending_files=json.dumps(sorted(set(stored))))
    session.add(audit)
    result = dict(id=row.id, deleted=True)
    session.delete(row)
    session.commit()
    result.update(_cleanup(audit.id))
    return result


def c4w_entity_delete(context, data_dict):
    tk.check_access('c4w_entity_delete', context, data_dict)
    try:
        entity, row = _row(data_dict, locked=True)
        preview = _preview(entity, row)
        if preview['blocked']:
            _error(preview['blocked'][0])
        if data_dict.get('version') != preview['version']:
            _error('This item changed. Review the updated confirmation before deleting it.')
        if data_dict.get('confirm_slug') != row.slug:
            _error('Type the exact identifier to confirm deletion.')
        return delete_row(context, entity, row)
    except Exception:
        db.Session.rollback()
        raise


def _cleanup(deletion_id):
    from ckanext.c4w.logic import uploads
    integrity.lock(db.Session)
    audit = db.Session.query(db.C4wDeletion).filter_by(id=deletion_id).with_for_update().one()
    pending = json.loads(audit.pending_files)
    remaining = []
    for name in pending:
        if not uploads.delete_stored(name):
            remaining.append(name)
    audit.pending_files = json.dumps(remaining)
    db.Session.commit()
    return dict(deletion_id=deletion_id, cleanup_pending=len(remaining))


def c4w_deletion_cleanup(context, data_dict):
    tk.check_access('c4w_deletion_cleanup', context, data_dict)
    if not db.Session.query(db.C4wDeletion.id).filter_by(id=data_dict.get('id')).first():
        raise tk.ObjectNotFound(tk._('Not found'))
    return _cleanup(data_dict['id'])


@tk.side_effect_free
def c4w_deletion_list(context, data_dict):
    tk.check_access('c4w_deletion_list', context, data_dict)
    rows = db.Session.query(db.C4wDeletion).filter(db.C4wDeletion.pending_files != '[]').order_by(db.C4wDeletion.created)
    return {'results': [dict(id=r.id, slug=r.slug, entity_type=r.entity_type,
                             cleanup_pending=len(json.loads(r.pending_files))) for r in rows]}


@tk.auth_allow_anonymous_access
def _admin(context, data_dict):
    return {'success': _common.is_content_admin(context)}


def get_actions():
    return {fn.__name__: fn for fn in (c4w_entity_delete_preview, c4w_entity_delete,
                                       c4w_deletion_cleanup, c4w_deletion_list)}


def get_auth_functions():
    return {name: _admin for name in get_actions()}

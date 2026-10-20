"""Serialize C4W writes with deletion and validate changed references.

The imported generic relations have no SQL foreign keys. A transaction-level
advisory lock and a before-flush check cover forms, API actions and workers.
Legacy references left unchanged are preserved, but cannot be newly attached
to a deleted item. SQLite tests use the same validation without the PG lock.
"""
import sqlalchemy as sa
from ckanext.c4w import db

_installed = False
LOCK_KEY = 42740913
DIRECT = {
    'project': {'main_organisation_id': 'organisation'},
    'event': {'project_id': 'project', 'main_organisation_id': 'organisation'},
    'dataset': {'project_id': 'project', 'organisation_id': 'organisation'},
}
PAYLOAD = {
    'project': {'main_organisation_id': 'organisation', 'organisation': 'organisation'},
    'event': {'project_id': 'project', 'main_organisation_id': 'organisation'},
}


def lock(session):
    if session.get_bind().dialect.name == 'postgresql':
        session.execute(sa.text('SELECT pg_advisory_xact_lock(:key)'), {'key': LOCK_KEY})


def payload_refs(entity, payload):
    for field, target in PAYLOAD.get(entity, {}).items():
        values = payload.get(field) or []
        for value in values if isinstance(values, list) else [values]:
            yield target, value


def _before_flush(session, flush_context, instances):
    objects = [o for o in set(session.new) | set(session.dirty) | set(session.deleted)
               if getattr(o, '__module__', '') == db.__name__]
    if not objects:
        return
    lock(session)
    models = dict(db.ENTITY_CLASSES)
    reverse = {cls: entity for entity, cls in models.items()}

    def require(entity, reference):
        if not reference or entity not in models:
            return
        cls = models[entity]
        if any(isinstance(o, cls) and o.id == reference for o in session.deleted):
            exists = False
        elif any(isinstance(o, cls) and o.id == reference for o in session.new):
            exists = True
        else:
            exists = session.query(cls.id).filter_by(id=reference).first() is not None
        if not exists:
            import ckan.plugins.toolkit as tk
            raise tk.ValidationError({'message': [tk._('A linked item was removed. Reload and choose an available item.')]})

    for obj in objects:
        if obj in session.deleted:
            continue
        state = sa.inspect(obj)
        changed = lambda field: obj in session.new or state.attrs[field].history.has_changes()
        entity = reverse.get(type(obj))
        for field, target in DIRECT.get(entity, {}).items():
            if changed(field):
                require(target, getattr(obj, field))
        if isinstance(obj, db.C4wRelation):
            for side in ('subject', 'object'):
                if changed(side + '_id') or changed(side + '_type'):
                    require(getattr(obj, side + '_type'), getattr(obj, side + '_id'))
        if isinstance(obj, db.C4wTermLink) and (changed('entity_id') or changed('entity_type')):
            require(obj.entity_type, obj.entity_id)
        if isinstance(obj, (db.C4wDatasetFile, db.C4wDashboardBundle)) and changed('dataset_id'):
            require('dataset', obj.dataset_id)
        if isinstance(obj, db.C4wUserProfile) and changed('c4w_organisation_id'):
            require('organisation', obj.c4w_organisation_id)
        if isinstance(obj, (db.C4wProjectRevision, db.C4wContentRevision)):
            kind = 'project' if isinstance(obj, db.C4wProjectRevision) else obj.entity_type
            field = 'project_id' if kind == 'project' else 'entity_id'
            if changed(field):
                require(kind, getattr(obj, field))
            if changed('payload') and obj.status in ('draft', 'pending'):
                for target, reference in payload_refs(kind, db.load_extras(obj.payload)):
                    require(target, reference)


def install():
    global _installed
    if not _installed:
        from sqlalchemy.orm import Session
        sa.event.listen(Session, 'before_flush', _before_flush)
        _installed = True

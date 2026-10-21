"""Versioned, atomic C4W-only administration of existing catalogue content."""
import datetime
import hashlib
import json

import ckan.plugins.toolkit as tk
import sqlalchemy as sa
from ckanext.c4w import constants, db
from ckanext.c4w.logic import admin_fields, checks, project_fields, legacy
from ckanext.c4w.logic.action import project_write, content_write
from ckanext.c4w.text import html_to_text


def _load(data, lock=False):
    db.ensure_mappers()
    entry = next((e for e in constants.ADMIN_SECTIONS if e[0] == data.get('section')), None)
    if entry is None:
        raise tk.ObjectNotFound('Unknown content section.')
    entity = entry[1]
    query = db.Session.query(db.ENTITY_CLASSES[entity]).filter_by(id=data.get('id'))
    row = (query.populate_existing().with_for_update() if lock else query).first()
    if row is None or (entity == 'resource' and bool(row.is_training_resource) != (entry[0] == 'training_resources')):
        raise tk.ObjectNotFound('Content not found.')
    return entity, row


def revision(entity, row, lock=False):
    if entity == 'project':
        return project_write._revision(row, lock)
    return content_write.revision(entity, row, lock)


def snapshot(entity, row):
    if entity == 'project':
        return project_write._snapshot(row)
    data = db.entity_dictize(entity, row, include_contact=True)
    out = {f['name']: data.get(f['name']) for f in admin_fields.fields(entity)}
    for f in admin_fields.fields(entity):
        name, kind = f['name'], f['kind']
        if kind == 'image':
            out[name + '_url'] = data.get(name + '_url') or ''
        elif name in admin_fields.term_fields(entity):
            vocabulary = admin_fields.term_fields(entity)[name]
            out[name] = ([v['label'] for v in data['term_labels'].get(vocabulary, [])]
                         if kind == 'terms' else data['terms'].get(vocabulary, []))
        elif kind == 'decimal' and out[name] is not None:
            out[name] = float(out[name])
        elif kind == 'date' and out[name]:
            out[name] = out[name][:10]
        elif kind == 'language' and out[name]:
            out[name] = legacy.language(out[name])
        elif kind == 'country' and out[name]:
            out[name] = content_write.country_code(out[name])
    return out


def version(entity, row, rev):
    # Include all persisted metadata and links, but not view counters. A
    # submitted/edited draft also invalidates an already open admin form.
    values = snapshot(entity, row)
    values.update(modified=str(row.modified), approved=getattr(row, 'approved', None),
                  hidden=row.hidden, status=getattr(row, 'status', None),
                  revision=(rev.version, rev.status) if rev else None)
    return hashlib.sha256(json.dumps(values, sort_keys=True, default=str).encode()).hexdigest()


def _blocked(rev, context):
    if not rev or rev.status in ('approved', 'rejected'):
        return False
    return rev.status == 'pending' or (rev.modified_by or rev.created_by) != context['auth_user_obj'].id


def _dict(entity, row, rev, context):
    published = row.status == constants.POST_STATUS_PUBLISHED if entity == 'post' else bool(row.approved)
    data = snapshot(entity, row)
    return dict(id=row.id, slug=row.slug, entity_type=entity,
                title=getattr(row, 'name', None) or getattr(row, 'title', ''),
                payload=data, published=published, hidden=bool(row.hidden),
                version=version(entity, row, rev), blocked=_blocked(rev, context),
                revision_status=rev.status if rev else None,
                review_note=rev.review_note if rev else '',
                legacy=bool(getattr(row, 'legacy_id', None)))


@tk.side_effect_free
def c4w_admin_entity_edit_show(context, data_dict):
    tk.check_access('c4w_admin_entity_edit_show', context, data_dict)
    entity, row = _load(data_dict)
    return _dict(entity, row, revision(entity, row), context)


def _validate(entity, row, payload, context):
    baseline = snapshot(entity, row) if getattr(row, 'legacy_id', None) else None
    values, errors = project_fields.validate(payload, complete=True, context=context,
        baseline=baseline, field_specs=admin_fields.fields(entity))
    if entity == 'dataset':
        if values.get('temporal_start') and values.get('temporal_end') and values['temporal_start'] > values['temporal_end']:
            errors['temporal_end'] = [tk._('End date must be after the start date.')]
        if not legacy.same(values.get('license_id'), row.license_id):
            from ckanext.c4w.logic.validators import license_id
            try:
                values['license_id'] = license_id()(values.get('license_id'), context)
            except (ValueError, tk.Invalid) as exc:
                errors['license_id'] = [str(exc)]
    if errors:
        raise tk.ValidationError(errors)
    return dict(payload, **values)


def _apply(entity, row, payload, changed=None):
    if entity == 'project':
        approved, moderated = row.approved, row.moderated
        project_write._publish(row, payload, changed=changed)
        row.approved, row.moderated = approved, moderated
        return
    columns = sa.inspect(type(row)).local_table.c
    extras = db.load_extras(row.extras)
    terms = admin_fields.term_fields(entity)
    for f in admin_fields.fields(entity):
        name, kind = f['name'], f['kind']
        if changed is not None and (name + '_url' if kind == 'image' else name) not in changed:
            continue
        if kind == 'image':
            setattr(row, name + '_url', payload.get(name + '_url') or None)
            continue
        value = payload.get(name)
        if name in terms:
            vocabulary = terms[name]
            db.Session.query(db.C4wTermLink).filter_by(entity_type=entity, entity_id=row.id,
                vocabulary=vocabulary).delete(synchronize_session=False)
            pairs = checks.check_free_terms(value) if kind == 'terms' else [
                (v, constants.label_for(vocabulary, v)) for v in (value or [])]
            for order, (term, label) in enumerate(pairs):
                db.Session.add(db.C4wTermLink(entity_type=entity, entity_id=row.id,
                    vocabulary=vocabulary, term=term, label=label, sort_order=order))
        elif name in columns:
            if kind == 'date':
                value = checks.check_date(value) if value else None
                if value and isinstance(columns[name].type, sa.DateTime):
                    value = datetime.datetime.combine(value, datetime.time())
            elif kind == 'time':
                value = datetime.time.fromisoformat(value) if value else None
            setattr(row, name, None if value == '' else value)
        else:
            extras[name] = value
    row.extras = db.dump_extras(extras)
    row.search_text = html_to_text(' '.join(str(v or '') for v in payload.values()))
    row.modified = db._utcnow()
    if entity == 'post':
        row.updated_on = db._utcnow()


def c4w_admin_entity_update(context, data_dict):
    tk.check_access('c4w_admin_entity_update', context, data_dict)
    entity, row = _load(data_dict, lock=True)
    rev = revision(entity, row, lock=True)
    if data_dict.get('version') != version(entity, row, rev):
        raise tk.ValidationError({'message': [tk._('This content changed in another window. Reload before saving.')]})
    if _blocked(rev, context):
        raise tk.ValidationError({'message': [tk._('Resolve the existing contribution before editing directly. No draft has been overwritten.')]})
    # Only allow declared fields, never arbitrary extras, flags, ownership or pipeline state.
    baseline = snapshot(entity, row)
    payload = dict(baseline)
    payload.update({f['name']: data_dict[f['name']] for f in admin_fields.fields(entity)
                    if f['kind'] != 'image' and f['name'] in data_dict})
    payload = _validate(entity, row, payload, context)
    changed = {f['name'] for f in admin_fields.fields(entity) if f['kind'] != 'image'
               and f['name'] in data_dict and not legacy.same(payload.get(f['name']), baseline.get(f['name']))}
    cleanups = []
    try:
        from ckanext.c4w.logic.project_images import store_image
        for f in admin_fields.fields(entity):
            if f['kind'] != 'image':
                continue
            name = f['name']
            if checks.is_truthy(data_dict.get('remove_' + name)):
                payload[name + '_url'] = ''
                changed.add(name + '_url')
            upload = data_dict.get(name)
            if getattr(upload, 'filename', None):
                box = name if name in constants.PROJECT_IMAGE_BOXES else ('image3' if name in ('image', 'profile_image') else 'image2')
                try:
                    url, cleanup = store_image(upload, box, dict(
                        crop_x=data_dict.get(name + '_crop_x', .5), crop_y=data_dict.get(name + '_crop_y', .5)))
                except tk.ValidationError as exc:
                    raise tk.ValidationError({name: next(iter(exc.error_dict.values()))})
                cleanups.append(cleanup)
                payload[name + '_url'] = url
                changed.add(name + '_url')
        _apply(entity, row, payload, changed=changed)
        if rev is None:
            args = dict(created_by=context['auth_user_obj'].id, version=0)
            rev = (db.C4wProjectRevision(project_id=row.id, **args) if entity == 'project' else
                   db.C4wContentRevision(entity_type=entity, entity_id=row.id, **args))
            db.Session.add(rev)
        rev.payload = db.dump_extras(payload)
        rev.version += 1
        rev.status = 'approved' if (row.status == constants.POST_STATUS_PUBLISHED if entity == 'post' else row.approved) else 'pending'
        if rev.status == 'pending':
            rev.submitted_by = context['auth_user_obj'].id
            rev.submitted_at = db._utcnow()
        rev.modified_by = context['auth_user_obj'].id
        rev.modified = db._utcnow()
        rev.reviewed_by = context['auth_user_obj'].id
        rev.reviewed_at = db._utcnow()
        rev.review_note = ''
        db.Session.commit()
    except Exception:
        db.Session.rollback()
        for cleanup in cleanups:
            cleanup()
        raise
    return _dict(entity, row, rev, context)


def c4w_admin_entity_review(context, data_dict):
    """Review imported/unversioned requests with the same stale-write guard."""
    tk.check_access('c4w_admin_entity_review', context, data_dict)
    entity, row = _load(data_dict, lock=True)
    rev = revision(entity, row, lock=True)
    if data_dict.get('version') != version(entity, row, rev):
        raise tk.ValidationError({'message': [tk._('This content changed. Reload before reviewing.')]})
    if rev and entity in ('project', 'post', 'event'):
        raise tk.ValidationError({'message': [tk._('Review the submitted contribution using its proposal preview.')]})
    published = row.status == constants.POST_STATUS_PUBLISHED if entity == 'post' else row.approved
    if published or (rev and rev.status != 'pending'):
        raise tk.ValidationError({'message': [tk._('Only pending content can be reviewed.')]})
    decision = data_dict.get('decision')
    if decision not in ('approve', 'changes_requested', 'rejected'):
        raise tk.ValidationError({'decision': [tk._('Invalid decision.')]})
    try:
        note = checks.clean_text(data_dict.get('note'), max_length=2000)
    except ValueError as exc:
        raise tk.ValidationError({'note': [str(exc)]})
    if decision != 'approve' and not note:
        raise tk.ValidationError({'note': [tk._('Explain the decision to the author.')]})
    payload = snapshot(entity, row)
    if decision == 'approve':
        payload = _validate(entity, row, payload, context)
        if entity == 'dataset' and row.processing_status != 'ready':
            raise tk.ValidationError({'message': [tk._('Finish processing the data before approval.')]})
        _apply(entity, row, payload)
        if entity == 'post':
            row.status = constants.POST_STATUS_PUBLISHED
        else:
            row.approved = True
        if hasattr(row, 'moderated'):
            row.moderated = True
    if rev is None:
        owner = getattr(row, 'created_by', None) or getattr(row, 'author_id', None) or context['auth_user_obj'].id
        args = dict(created_by=owner, version=0)
        rev = (db.C4wProjectRevision(project_id=row.id, **args) if entity == 'project' else
               db.C4wContentRevision(entity_type=entity, entity_id=row.id, **args))
        db.Session.add(rev)
    rev.payload = db.dump_extras(payload)
    rev.version += 1
    rev.status = 'approved' if decision == 'approve' else decision
    rev.reviewed_by = context['auth_user_obj'].id
    rev.reviewed_at = rev.modified = db._utcnow()
    rev.review_note = note
    db.Session.commit()
    from ckanext.c4w.logic import mail, helpers
    mail.notify_review(rev.submitted_by or rev.created_by, decision, note,
        helpers.c4w_url('account', qualified=True))
    return _dict(entity, row, rev, context)


def get_actions():
    return {f.__name__: f for f in (c4w_admin_entity_edit_show, c4w_admin_entity_update, c4w_admin_entity_review)}


def get_auth_functions():
    return {name: project_write._admin_auth for name in get_actions()}

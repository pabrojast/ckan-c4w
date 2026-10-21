"""Resumable proposals. Public project rows change only on approval."""
import json

import ckan.plugins.toolkit as tk
from ckan.model.meta import Session

from ckanext.c4w import db, constants
from ckanext.c4w.logic import checks, project_fields as fields
from ckanext.c4w.logic.action import _common


def verified(context):
    user = context.get('auth_user_obj')
    if not user or getattr(user, 'state', None) != 'active':
        return False
    db.ensure_mappers()
    profile = Session.query(db.C4wUserProfile).filter_by(user_id=user.id).first()
    return profile is None or bool(profile.email_verified)


def _project(context, reference):
    db.ensure_mappers()
    row = _common.get_by_reference(db.C4wProject, reference)
    if row is None or not _common.can_edit('project', row, context):
        raise tk.ObjectNotFound(tk._('Project not found'))
    return row


def _revision(project, lock=False):
    query = Session.query(db.C4wProjectRevision).filter_by(project_id=project.id)
    return (query.populate_existing().with_for_update() if lock else query).first()


def _snapshot(project):
    data = db.entity_dictize('project', project, include_contact=True)
    out = {key: data.get(key, '') for key in fields.FIELDS}
    for name, vocabulary in fields.TERM_FIELDS.items():
        if name in ('keywords', 'funding_body'):
            out[name] = [v['label'] for v in data['term_labels'].get(vocabulary, [])]
        else:
            out[name] = data['terms'].get(vocabulary, [])
    out['organisation'] = data['relations'].get('organisation', [])
    from ckanext.c4w.logic import legacy
    out['language'] = legacy.languages(out.get('language'))
    out['country'] = [v.upper() for v in out.get('country', [])]
    out['editors'] = data['relations'].get('editor', [])
    for name in ('image1', 'image2', 'image3'):
        out[name + '_url'] = data.get(name + '_url') or ''
    return out


def _dict(project, revision):
    payload = db.load_extras(revision.payload) if revision else _snapshot(project)
    from ckanext.c4w.logic.review import changes
    return dict(changes=changes(_snapshot(project), payload, list(fields.FIELDS.values())) if project.approved else [], id=project.id, slug=project.slug, name=payload.get('name') or project.name,
                published=bool(project.approved), payload=payload,
                status=revision.status if revision else ('approved' if project.approved else 'draft'),
                wizard_step=revision.wizard_step if revision else 1,
                version=revision.version if revision else 0,
                review_note=revision.review_note if revision else '',
                submitted_by=(revision.submitted_by or revision.created_by) if revision else None,
                submitted_at=revision.submitted_at.isoformat() if revision and revision.submitted_at else None,
                notification_status=revision.notification_status if revision else None,
                modified=(revision.modified or db._utcnow()).isoformat() if revision else None)


def _editable_revision(project, context, data):
    # Serialize creation too: a missing revision cannot itself be row-locked.
    Session.query(db.C4wProject.id).filter_by(id=project.id).with_for_update().one()
    revision = _revision(project, lock=True)
    expected = data.get('version')
    actual = revision.version if revision else 0
    if str(expected) != str(actual):
        raise tk.ValidationError({'message': [tk._('This proposal changed in another window. Reload before saving.')]})
    if revision is None:
        revision = db.C4wProjectRevision(project_id=project.id,
            created_by=context['auth_user_obj'].id, payload=db.dump_extras(_snapshot(project)),
            status='draft', wizard_step=1, version=0)
        Session.add(revision)
    return revision


def _touch(revision, status='draft'):
    revision.version += 1
    revision.status = status
    revision.modified = db._utcnow()


@tk.side_effect_free
def c4w_project_draft_show(context, data_dict):
    tk.check_access('c4w_project_draft_show', context, data_dict)
    project = _project(context, data_dict.get('id'))
    return _dict(project, _revision(project))


def c4w_project_create(context, data_dict):
    tk.check_access('c4w_project_create', context, data_dict)
    data, errors = fields.validate(data_dict, step=1, context=context)
    if not data.get('name'):
        errors['name'] = [tk._('Enter a project name.')]
    if errors:
        raise tk.ValidationError(errors)
    db.ensure_mappers()
    row = db.C4wProject(id=db.make_uuid(), name=data['name'],
        slug=db.unique_slug(db.C4wProject, data['name']), created_by=context['auth_user_obj'].id)
    revision = db.C4wProjectRevision(project_id=row.id,
        created_by=context['auth_user_obj'].id, payload=db.dump_extras(data),
        status='draft', wizard_step=2, version=1)
    Session.add_all([row, revision])
    Session.commit()
    return _dict(row, revision)


def c4w_project_update(context, data_dict):
    tk.check_access('c4w_project_update', context, data_dict)
    project = _project(context, data_dict.get('id'))
    try:
        step = int(data_dict.get('step'))
    except (TypeError, ValueError):
        step = 0
    if step not in range(1, 7):
        raise tk.ValidationError({'step': [tk._('Invalid step.')]})
    revision = _editable_revision(project, context, data_dict)
    payload = db.load_extras(revision.payload)
    updates, errors = fields.validate(dict(payload, **data_dict), step=step, context=context, baseline=_snapshot(project) if project.legacy_id else None)
    if errors:
        raise tk.ValidationError(errors)
    payload.update(updates)
    for name in ('image1', 'image2', 'image3'):
        if step == 6 and checks.is_truthy(data_dict.get('remove_' + name)):
            payload[name + '_url'] = ''
    submitting = data_dict.get('action') == 'submit'
    if submitting:
        normalized, errors = fields.validate(payload, complete=True, context=context,
            baseline=_snapshot(project) if project.legacy_id else None)
        if errors:
            raise tk.ValidationError(errors)
        payload.update(normalized)
    cleanups = []
    try:
        from ckanext.c4w.logic.project_images import store_image
        for name in ('image1', 'image2', 'image3'):
            upload = data_dict.get(name)
            if step == 6 and getattr(upload, 'filename', None):
                url, cleanup = store_image(upload, name, {
                    'crop_x': data_dict.get(name + '_crop_x', .5),
                    'crop_y': data_dict.get(name + '_crop_y', .5)})
                cleanups.append(cleanup)
                payload[name + '_url'] = url
        revision.payload = db.dump_extras(payload)
        revision.wizard_step = max(revision.wizard_step or 1, min(step + 1, 6))
        _touch(revision, 'pending' if submitting else 'draft')
        revision.modified_by = context['auth_user_obj'].id
        if submitting:
            revision.submitted_by = context['auth_user_obj'].id
            revision.submitted_at = db._utcnow()
        if not project.approved:
            project.name = payload.get('name') or project.name
        Session.commit()
    except Exception:
        Session.rollback()
        for cleanup in cleanups:
            cleanup()
        raise
    if submitting:
        _notify_submission(project, revision)
    return _dict(project, revision)


def _notify_submission(project, revision):
    from ckanext.c4w.logic import mail, helpers
    sent = mail.notify_moderators(tk._('Citizens4Water: project submitted for review'),
        helpers.c4w_url('project_review', project_id=project.id, qualified=True))
    revision.notification_status = 'sent' if sent else 'failed'
    Session.commit()


@tk.side_effect_free
def c4w_project_completeness(context, data_dict):
    tk.check_access('c4w_project_completeness', context, data_dict)
    project = _project(context, data_dict.get('id'))
    revision = _revision(project)
    payload = db.load_extras(revision.payload) if revision else _snapshot(project)
    _, errors = fields.validate(payload, complete=True, context=context, baseline=_snapshot(project) if project.legacy_id else None)
    return dict(complete=not errors, errors=errors)


def c4w_project_submit(context, data_dict):
    tk.check_access('c4w_project_submit', context, data_dict)
    project = _project(context, data_dict.get('id'))
    revision = _editable_revision(project, context, data_dict)
    _, errors = fields.validate(db.load_extras(revision.payload), complete=True, context=context, baseline=_snapshot(project) if project.legacy_id else None)
    if errors:
        raise tk.ValidationError(errors)
    _touch(revision, 'pending')
    revision.submitted_at = db._utcnow()
    revision.submitted_by = context['auth_user_obj'].id
    Session.commit()
    _notify_submission(project, revision)
    return _dict(project, revision)


def _publish(project, payload, changed=None):
    """Replace the editable fields only. Keep slug, counters and moderation flags."""
    columns = set(db.c4w_project_table.c.keys())
    extras = db.load_extras(project.extras)
    for name, f in fields.FIELDS.items():
        if changed is not None and name not in changed:
            continue
        if name in fields.TERM_FIELDS or name in ('organisation', 'editors') or f['kind'] == 'image':
            continue
        value = payload.get(name)
        if name in columns:
            if f['kind'] == 'date':
                value = checks.check_date(value)
            setattr(project, name, value or None)
        else:
            extras[name] = value
    for name in ('image1', 'image2', 'image3'):
        if changed is not None and name + '_url' not in changed:
            continue
        setattr(project, name + '_url', payload.get(name + '_url') or None)
    project.extras = db.dump_extras(extras)
    for name, vocabulary in fields.TERM_FIELDS.items():
        if changed is not None and name not in changed:
            continue
        saved_labels = {r.term: r.label for r in Session.query(db.C4wTermLink).filter_by(
            entity_type='project', entity_id=project.id, vocabulary=vocabulary).all()}
        Session.query(db.C4wTermLink).filter_by(entity_type='project', entity_id=project.id,
            vocabulary=vocabulary).delete(synchronize_session=False)
        values = payload.get(name) or []
        pairs = checks.check_free_terms(values) if name in ('keywords', 'funding_body') else [
            (v, saved_labels.get(v) or constants.label_for(vocabulary, v)) for v in values]
        for order, (term, label) in enumerate(pairs):
            Session.add(db.C4wTermLink(entity_type='project', entity_id=project.id,
                vocabulary=vocabulary, term=term, label=label, sort_order=order))
    for name, predicate, entity in [('organisation', 'organisation', 'organisation'), ('editors', 'editor', 'user')]:
        if changed is not None and name not in changed:
            continue
        Session.query(db.C4wRelation).filter_by(subject_type='project', subject_id=project.id,
            predicate=predicate).delete(synchronize_session=False)
        for order, value in enumerate(payload.get(name) or []):
            Session.add(db.C4wRelation(subject_type='project', subject_id=project.id,
                predicate=predicate, object_type=entity, object_id=value, sort_order=order))
    from ckanext.c4w.text import html_to_text
    project.search_text = html_to_text(' '.join(str(payload.get(k) or '') for k in ('name', 'description', 'aim', 'keywords')))
    project.approved = True
    project.moderated = True
    project.modified = db._utcnow()


def c4w_project_review(context, data_dict):
    tk.check_access('c4w_project_review', context, data_dict)
    project = _project(context, data_dict.get('id'))
    Session.query(db.C4wProject.id).filter_by(id=project.id).with_for_update().one()
    revision = _revision(project, lock=True)
    if revision is None or revision.status != 'pending':
        raise tk.ValidationError({'message': [tk._('Only submitted proposals can be reviewed.')]})
    if str(data_dict.get('version')) != str(revision.version):
        raise tk.ValidationError({'message': [tk._('The proposal has changed. Reload and review the latest version.')]})
    decision = data_dict.get('decision')
    if decision not in ('approve', 'changes_requested', 'rejected'):
        raise tk.ValidationError({'decision': [tk._('Invalid decision.')]})
    note = checks.clean_text(data_dict.get('note'), max_length=2000)
    if decision != 'approve' and not note:
        raise tk.ValidationError({'note': [tk._('Explain which changes are needed.')]})
    if decision == 'approve':
        payload = db.load_extras(revision.payload)
        validated, errors = fields.validate(payload, complete=True, context=context, baseline=_snapshot(project) if project.legacy_id else None)
        if errors:
            raise tk.ValidationError(errors)
        payload.update(validated)
        _publish(project, payload)
    _touch(revision, 'approved' if decision == 'approve' else decision)
    revision.reviewed_at = db._utcnow()
    revision.reviewed_by = context['auth_user_obj'].id
    revision.review_note = note
    Session.commit()
    from ckanext.c4w.logic import mail, helpers
    import ckan.model as model
    owner = model.User.get(revision.submitted_by or revision.created_by or project.created_by)
    if owner:
        mail._send(owner.fullname, owner.email, tk._('Citizens4Water: project review'),
            {'approve': 'Approved', 'changes_requested': 'Changes requested', 'rejected': 'Rejected'}[decision] + '\n' + note + '\n' +
            helpers.c4w_url('submit_project_step', project_id=project.id, step=1, qualified=True))
    return _dict(project, revision)


def c4w_project_image_upload(context, data_dict):
    tk.check_access('c4w_project_image_upload', context, data_dict)
    project = _project(context, data_dict.get('id'))
    revision = _editable_revision(project, context, data_dict)
    name = data_dict.get('field')
    if name not in constants.PROJECT_IMAGE_BOXES:
        raise tk.ValidationError({'image': [tk._('Unknown image field.')]})
    from ckanext.c4w.logic.project_images import store_image
    url, cleanup = store_image(data_dict.get('upload'), name, data_dict)
    try:
        payload = db.load_extras(revision.payload)
        payload[name + '_url'] = url
        revision.payload = db.dump_extras(payload)
        _touch(revision)
        Session.commit()
    except Exception:
        Session.rollback()
        cleanup()
        raise
    return _dict(project, revision)


WRITE_ACTIONS = [c4w_project_create, c4w_project_update, c4w_project_submit,
                 c4w_project_image_upload]
READ_ACTIONS = [c4w_project_draft_show, c4w_project_completeness]


def get_actions():
    return {f.__name__: f for f in WRITE_ACTIONS + READ_ACTIONS + [c4w_project_review]}


@tk.auth_allow_anonymous_access
def _verified_auth(context, data_dict):
    return {'success': verified(context)}


@tk.auth_allow_anonymous_access
def _read_auth(context, data_dict):
    return {'success': _common.is_authenticated(context)}


@tk.auth_allow_anonymous_access
def _admin_auth(context, data_dict):
    return {'success': _common.is_content_admin(context)}


def get_auth_functions():
    out = {f.__name__: _verified_auth for f in WRITE_ACTIONS}
    out.update({f.__name__: _read_auth for f in READ_ACTIONS})
    out['c4w_project_review'] = _admin_auth
    return out

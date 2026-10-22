"""News and event proposals; only reviewed revisions change public content."""
import datetime
import pytz

import ckan.plugins.toolkit as tk
from ckan.model.meta import Session
from ckanext.c4w import db, constants
from ckanext.c4w.logic import checks
from ckanext.c4w.logic.sanitize import sanitize_html, sanitize_rich_html
from ckanext.c4w.logic.action import _common, project_write
from ckanext.c4w.text import html_to_text

FIELDS = {
    'post': ('title', 'content', 'excerpt'),
    'event': ('title', 'description', 'start_date', 'end_date', 'place',
              'country', 'hour', 'timezone', 'language', 'event_type', 'url',
              'project_id', 'main_organisation_id'),
}


def kind(data):
    value = data.get('entity_type')
    if value not in FIELDS:
        raise tk.ObjectNotFound(tk._('Unknown contribution type.'))
    return value


def country_code(value):
    """Normalize recognized legacy English country names without losing others."""
    from babel import Locale
    value = str(value or '').strip()
    for code, name in Locale.parse('en').territories.items():
        if len(code) == 2 and code.isalpha() and value.casefold() == name.casefold():
            return code
    return value


def validate(entity, data, context, complete=False, legacy_country=None):
    out, errors = {}, {}
    for name in FIELDS[entity]:
        value = data.get(name) or ''
        try:
            if name in ('content', 'description'):
                if len(str(value)) > 100000:
                    raise ValueError('Content is too long (maximum 100,000 characters).')
                value = (sanitize_rich_html if entity == 'post' else sanitize_html)(str(value))
            elif name in ('start_date', 'end_date'):
                value = checks.check_date(value).isoformat() if value else ''
            elif name == 'hour' and value:
                value = datetime.time.fromisoformat(str(value))
                if value.tzinfo or value.microsecond:
                    raise ValueError('Enter a local time as HH:MM or HH:MM:SS.')
                value = value.isoformat()
            elif name == 'url':
                value = checks.check_url(value)
            elif name == 'timezone' and value:
                if value not in pytz.all_timezones_set:
                    raise ValueError('Choose a valid time zone, such as Europe/Paris.')
            elif name == 'language' and value:
                value = checks.check_language_code(value)
            elif name == 'country' and value:
                value = country_code(value)
                if value != legacy_country:
                    value = checks.check_country_code(value)
            elif name == 'event_type' and value:
                if value not in dict(constants.EVENT_TYPES):
                    raise ValueError('Choose an event type from the list.')
            elif name in ('project_id', 'main_organisation_id') and value:
                target = 'project' if name == 'project_id' else 'organisation'
                cls = db.C4wProject if target == 'project' else db.C4wOrganisation
                row = Session.query(cls).filter_by(id=str(value)).first()
                if row is None or not _common.is_visible(target, row, context):
                    raise ValueError('Choose an available project or organisation.')
                value = row.id
            else:
                value = checks.clean_text(value, max_length=1000 if name == 'excerpt' else 300)
            out[name] = value
        except (ValueError, TypeError) as exc:
            errors[name] = [tk._(str(exc))]
    required = ['title']
    if complete:
        required += ['content'] if entity == 'post' else ['description', 'start_date']
    for name in required:
        if not (html_to_text(str(out.get(name) or '')) or '').strip():
            errors[name] = [tk._('This field is required.')]
    if out.get('start_date') and out.get('end_date') and out['end_date'] < out['start_date']:
        errors['end_date'] = [tk._('End date must not be before the start date.')]
    return out, errors


def _row(context, data, lock=False):
    entity = kind(data)
    db.ensure_mappers()
    cls = db.C4wPost if entity == 'post' else db.C4wEvent
    query = Session.query(cls).filter_by(id=data.get('id'))
    row = (query.populate_existing().with_for_update() if lock else query).first()
    if row is None or not _common.can_edit(entity, row, context):
        raise tk.ObjectNotFound(tk._('Contribution not found.'))
    return entity, row


def revision(entity, row, lock=False):
    query = Session.query(db.C4wContentRevision).filter_by(entity_type=entity, entity_id=row.id)
    return (query.populate_existing().with_for_update() if lock else query).first()


def _snapshot(entity, row):
    out = {}
    for name in FIELDS[entity]:
        value = getattr(row, name, '') or ''
        if isinstance(value, datetime.datetime):
            value = value.date().isoformat()
        elif isinstance(value, datetime.time):
            value = value.isoformat()
        out[name] = country_code(value) if name == 'country' else value
    if entity == 'post':
        out['image_url'] = row.image_url or ''
    return out


def dictize(entity, row, rev):
    payload = _snapshot(entity, row)
    if rev:
        payload.update(db.load_extras(rev.payload))
    if entity == 'event':
        payload['country'] = country_code(payload.get('country'))
    published = row.status == constants.POST_STATUS_PUBLISHED if entity == 'post' else bool(row.approved)
    from ckanext.c4w.logic import review, admin_fields
    return dict(changes=review.changes(_snapshot(entity, row), payload, admin_fields.fields(entity)) if published else [],
                submitted_by=(rev.submitted_by or rev.created_by) if rev else None,
                submitted_at=rev.submitted_at.isoformat() if rev and rev.submitted_at else None,
                notification_status=rev.notification_status if rev else None,
                id=row.id, slug=row.slug, entity_type=entity, title=payload.get('title'),
                payload=payload, published=published, status=rev.status if rev else ('approved' if published else 'draft'),
                version=rev.version if rev else 0, review_note=rev.review_note if rev else '',
                modified=(rev.modified or db._utcnow()).isoformat() if rev else None)


def _working(context, data, create=True):
    entity, row = _row(context, data, lock=True)
    rev = revision(entity, row, lock=True)
    if rev is None and not create:
        raise tk.ValidationError({'message': [tk._('Only submitted proposals can be reviewed.')]})
    if str(data.get('version')) != str(rev.version if rev else 0):
        raise tk.ValidationError({'message': [tk._('This proposal changed in another window. Reload before saving.')]})
    if rev is None:
        rev = db.C4wContentRevision(entity_type=entity, entity_id=row.id,
            created_by=context['auth_user_obj'].id, payload=db.dump_extras(_snapshot(entity, row)),
            status='draft', version=0)
        Session.add(rev)
    return entity, row, rev


def _touch(rev, status='draft'):
    rev.version += 1
    rev.status = status
    rev.modified = db._utcnow()


@tk.side_effect_free
def c4w_content_draft_show(context, data_dict):
    tk.check_access('c4w_content_draft_show', context, data_dict)
    entity, row = _row(context, data_dict)
    return dictize(entity, row, revision(entity, row))


def c4w_content_create(context, data_dict):
    tk.check_access('c4w_content_create', context, data_dict)
    entity = kind(data_dict)
    payload, errors = validate(entity, data_dict, context)
    if errors:
        raise tk.ValidationError(errors)
    cls = db.C4wPost if entity == 'post' else db.C4wEvent
    owner = {'author_id' if entity == 'post' else 'created_by': context['auth_user_obj'].id}
    row = cls(id=db.make_uuid(), slug=db.unique_slug(cls, payload['title']), title=payload['title'], **owner)
    rev = db.C4wContentRevision(entity_type=entity, entity_id=row.id,
        created_by=context['auth_user_obj'].id, payload=db.dump_extras(payload), status='draft', version=1)
    Session.add_all([row, rev]); Session.commit()
    return dictize(entity, row, rev)


def c4w_content_update(context, data_dict):
    tk.check_access('c4w_content_update', context, data_dict)
    entity, row, rev = _working(context, data_dict)
    payload = dict(_snapshot(entity, row), **db.load_extras(rev.payload))
    updates, errors = validate(entity, dict(payload, **data_dict), context, complete=data_dict.get('action') == 'submit',
                               legacy_country=getattr(row, 'country', None))
    if errors:
        raise tk.ValidationError(errors)
    payload.update(updates)
    if checks.is_truthy(data_dict.get('remove_image')):
        payload['image_url'] = ''
    cleanup = None
    try:
        upload = data_dict.get('image')
        if entity == 'post' and getattr(upload, 'filename', None):
            from ckanext.c4w.logic.project_images import store_image
            try:
                url, cleanup = store_image(upload, 'image3', dict(
                    crop_x=data_dict.get('image_crop_x', .5), crop_y=data_dict.get('image_crop_y', .5)))
            except tk.ValidationError as exc:
                raise tk.ValidationError({'image': next(iter(exc.error_dict.values()))})
            payload['image_url'] = url
        rev.payload = db.dump_extras(payload)
        _touch(rev); rev.modified_by = context['auth_user_obj'].id
        Session.commit()
    except Exception:
        Session.rollback()
        if cleanup:
            cleanup()
        raise
    return dictize(entity, row, rev)


def c4w_content_image_upload(context, data_dict):
    tk.check_access('c4w_content_image_upload', context, data_dict)
    entity, row, rev = _working(context, data_dict)
    if entity != 'post':
        raise tk.ValidationError({'image': [tk._('Images are supported for news.')]})
    from ckanext.c4w.logic.project_images import store_image
    try:
        url, cleanup = store_image(data_dict.get('upload'), 'image3', data_dict)
    except tk.ValidationError as exc:
        raise tk.ValidationError({'image': exc.error_dict.get('image3', [tk._('Invalid image.')])})
    try:
        payload = db.load_extras(rev.payload); payload['image_url'] = url
        rev.payload = db.dump_extras(payload); _touch(rev); Session.commit()
    except Exception:
        Session.rollback(); cleanup(); raise
    return dictize(entity, row, rev)


def c4w_content_submit(context, data_dict):
    tk.check_access('c4w_content_submit', context, data_dict)
    entity, row, rev = _working(context, data_dict)
    _, errors = validate(entity, db.load_extras(rev.payload), context, complete=True,
                         legacy_country=getattr(row, 'country', None))
    if errors:
        raise tk.ValidationError(errors)
    _touch(rev, 'pending'); rev.submitted_at = db._utcnow()
    rev.submitted_by = context['auth_user_obj'].id
    Session.commit()
    from ckanext.c4w.logic import mail, helpers
    sent = mail.notify_moderators(tk._('Citizens4Water: contribution submitted for review'),
        helpers.c4w_url('content_review', entity=entity, content_id=row.id, qualified=True))
    rev.notification_status = 'sent' if sent else 'failed'
    Session.commit()
    return dictize(entity, row, rev)


def c4w_content_review(context, data_dict):
    tk.check_access('c4w_content_review', context, data_dict)
    entity, row, rev = _working(context, data_dict, create=False)
    from ckanext.c4w.logic.review import decision_for
    decision = decision_for(rev.status, data_dict.get('decision'))
    try:
        note = checks.clean_text(data_dict.get('note'), max_length=2000)
    except ValueError as exc:
        raise tk.ValidationError({'note': [tk._(str(exc))]})
    if decision != 'approve' and not note:
        raise tk.ValidationError({'note': [tk._('Explain which changes are needed.')]})
    if decision == 'approve':
        payload = dict(_snapshot(entity, row), **db.load_extras(rev.payload))
        # Validate references as the author, not using the reviewer's wider permissions.
        import ckan.model as model
        author = model.User.get(getattr(row, 'author_id', None) or getattr(row, 'created_by', None))
        values, errors = validate(entity, payload, {'auth_user_obj': author}, complete=True,
                                  legacy_country=getattr(row, 'country', None))
        if errors:
            raise tk.ValidationError(errors)
        _publish(entity, row, values, payload)
    _touch(rev, 'approved' if decision == 'approve' else decision)
    rev.review_note = note; rev.reviewed_at = db._utcnow(); rev.reviewed_by = context['auth_user_obj'].id
    Session.commit()
    from ckanext.c4w.logic import mail, helpers
    mail.notify_review(rev.submitted_by or rev.created_by, decision, note,
        helpers.c4w_url('content_review', entity=entity, content_id=row.id, qualified=True))
    return dictize(entity, row, rev)


def _publish(entity, row, values, payload):
    for key, value in values.items():
        if key in ('start_date', 'end_date'):
            value = datetime.datetime.combine(checks.check_date(value), datetime.time()) if value else None
        elif key == 'hour':
            value = datetime.time.fromisoformat(value) if value else None
        setattr(row, key, value or None)
    if entity == 'post':
        row.image_url = payload.get('image_url') or None
        row.status = constants.POST_STATUS_PUBLISHED
        row.created_on = row.created_on or db._utcnow()
        row.updated_on = db._utcnow()
    else:
        row.approved = True
    # Publication and visibility are independent: never reset hidden here.
    row.search_text = html_to_text(' '.join(str(v or '') for v in values.values()))
    row.modified = db._utcnow()


def c4w_event_admin_save(context, data_dict):
    """Save or publish an event revision atomically, only as a sysadmin."""
    tk.check_access('c4w_event_admin_save', context, data_dict)
    mode = data_dict.get('mode')
    if mode not in ('draft', 'publish'):
        raise tk.ValidationError({'mode': [tk._('Choose save draft or publish changes.')]})
    db.ensure_mappers()
    if data_dict.get('id'):
        _, row, rev = _working(context, dict(data_dict, entity_type='event'))
        # This legacy API resumes the administrator's own draft payload. The
        # general direct editor instead starts from the published snapshot.
        if rev.status == 'pending' or (rev.status not in ('approved', 'rejected')
                and (rev.modified_by or rev.created_by) != context['auth_user_obj'].id):
            raise tk.ValidationError({'message': [tk._('Resolve the existing contribution before editing directly.')]})
        payload = dict(_snapshot('event', row), **db.load_extras(rev.payload))
    else:
        row = rev = None
        payload = {}
    values, errors = validate('event', dict(payload, **data_dict), context,
                              complete=mode == 'publish',
                              legacy_country=getattr(row, 'country', None))
    if errors:
        raise tk.ValidationError(errors)
    if row is None:
        row = db.C4wEvent(id=db.make_uuid(), slug=db.unique_slug(db.C4wEvent, values['title']),
                          title=values['title'], created_by=context['auth_user_obj'].id)
        rev = db.C4wContentRevision(entity_type='event', entity_id=row.id,
            created_by=context['auth_user_obj'].id, status='draft', version=0)
        Session.add_all([row, rev])
    rev.payload = db.dump_extras(values)
    _touch(rev, 'approved' if mode == 'publish' else 'draft')
    rev.review_note = ''
    rev.modified_by = context['auth_user_obj'].id
    if mode == 'publish':
        _publish('event', row, values, values)
        rev.reviewed_at = db._utcnow()
        rev.reviewed_by = context['auth_user_obj'].id
    Session.commit()
    return dictize('event', row, rev)


WRITE = [c4w_content_create, c4w_content_update, c4w_content_image_upload, c4w_content_submit]


def get_actions():
    return {f.__name__: f for f in WRITE + [c4w_content_draft_show, c4w_content_review, c4w_event_admin_save]}


def get_auth_functions():
    out = {f.__name__: project_write._verified_auth for f in WRITE}
    out.update(c4w_content_draft_show=project_write._read_auth, c4w_content_review=project_write._admin_auth,
               c4w_event_admin_save=project_write._admin_auth)
    return out

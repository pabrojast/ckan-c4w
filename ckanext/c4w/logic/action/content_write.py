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
              'country', 'timezone', 'language', 'event_type', 'url',
              'project_id', 'main_organisation_id'),
}


def kind(data):
    value = data.get('entity_type')
    if value not in FIELDS:
        raise tk.ObjectNotFound(tk._('Unknown contribution type.'))
    return value


def validate(entity, data, context, complete=False):
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
            elif name == 'url':
                value = checks.check_url(value)
            elif name == 'timezone' and value:
                if value not in pytz.all_timezones_set:
                    raise ValueError('Choose a valid time zone, such as Europe/Paris.')
            elif name == 'language' and value:
                value = checks.check_language_code(value)
            elif name == 'country' and value:
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
        out[name] = value.date().isoformat() if isinstance(value, datetime.datetime) else value
    if entity == 'post':
        out['image_url'] = row.image_url or ''
    return out


def dictize(entity, row, rev):
    payload = db.load_extras(rev.payload) if rev else _snapshot(entity, row)
    published = row.status == constants.POST_STATUS_PUBLISHED if entity == 'post' else bool(row.approved)
    return dict(id=row.id, slug=row.slug, entity_type=entity, title=payload.get('title'),
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
    payload = db.load_extras(rev.payload)
    updates, errors = validate(entity, dict(payload, **data_dict), context)
    if errors:
        raise tk.ValidationError(errors)
    payload.update(updates)
    if checks.is_truthy(data_dict.get('remove_image')):
        payload['image_url'] = ''
    rev.payload = db.dump_extras(payload)
    _touch(rev); Session.commit()
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
    _, errors = validate(entity, db.load_extras(rev.payload), context, complete=True)
    if errors:
        raise tk.ValidationError(errors)
    _touch(rev, 'pending'); rev.submitted_at = db._utcnow(); Session.commit()
    return dictize(entity, row, rev)


def c4w_content_review(context, data_dict):
    tk.check_access('c4w_content_review', context, data_dict)
    entity, row, rev = _working(context, data_dict, create=False)
    if rev.status != 'pending':
        raise tk.ValidationError({'message': [tk._('Only submitted proposals can be reviewed.')]})
    decision = data_dict.get('decision')
    if decision not in ('approve', 'changes_requested'):
        raise tk.ValidationError({'decision': [tk._('Invalid decision.')]})
    try:
        note = checks.clean_text(data_dict.get('note'), max_length=2000)
    except ValueError as exc:
        raise tk.ValidationError({'note': [tk._(str(exc))]})
    if decision == 'changes_requested' and not note:
        raise tk.ValidationError({'note': [tk._('Explain which changes are needed.')]})
    if decision == 'approve':
        payload = db.load_extras(rev.payload)
        # Validate references as the author, not using the reviewer's wider permissions.
        import ckan.model as model
        author = model.User.get(getattr(row, 'author_id', None) or getattr(row, 'created_by', None))
        values, errors = validate(entity, payload, {'auth_user_obj': author}, complete=True)
        if errors:
            raise tk.ValidationError(errors)
        for key, value in values.items():
            if key in ('start_date', 'end_date'):
                value = datetime.datetime.combine(checks.check_date(value), datetime.time()) if value else None
            setattr(row, key, value or None)
        if entity == 'post':
            row.image_url = payload.get('image_url') or None
            row.status = constants.POST_STATUS_PUBLISHED
            row.created_on = row.created_on or db._utcnow()
            row.updated_on = db._utcnow()
        else:
            row.approved = True
        row.search_text = html_to_text(' '.join(str(v or '') for v in values.values()))
        row.modified = db._utcnow()
    _touch(rev, 'approved' if decision == 'approve' else 'changes_requested')
    rev.review_note = note; rev.reviewed_at = db._utcnow(); rev.reviewed_by = context['auth_user_obj'].id
    Session.commit()
    return dictize(entity, row, rev)


WRITE = [c4w_content_create, c4w_content_update, c4w_content_image_upload, c4w_content_submit]


def get_actions():
    return {f.__name__: f for f in WRITE + [c4w_content_draft_show, c4w_content_review]}


def get_auth_functions():
    out = {f.__name__: project_write._verified_auth for f in WRITE}
    out.update(c4w_content_draft_show=project_write._read_auth, c4w_content_review=project_write._admin_auth)
    return out

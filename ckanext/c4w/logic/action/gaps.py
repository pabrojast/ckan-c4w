# encoding: utf-8
"""What is still missing on a project page, and a quick way to fill it in.

Built for the projects imported from the UNESCO list, which arrived with
only part of their information. Content administrators get one screen that
lists, for each project, what is missing (website, contact, data link,
image, a status nobody has confirmed, …) with inputs for just those fields.

Saving goes through ``c4w_admin_entity_update``, the same versioned write
as the full editor. The checks, the stale-window guard and the revision
record are therefore the same, and an open draft or contribution is never
overwritten. A gap can also be marked "not available" (a one-off study has
no website), which takes it off the list without inventing a value.

Any save here marks the row as edited by a person, so a later run of the
sheet import leaves it alone.
"""
import datetime
import re

import ckan.plugins.toolkit as tk

from ckanext.c4w import db
from ckanext.c4w.logic.action import _common, editorial

# key, label, the form fields that fill it. Order is the order on screen.
GAPS = (
    ('website', 'Website', ('url',)),
    ('contact', 'Contact e-mail', ('author', 'author_email')),
    ('data', 'Data link', ('data_url',)),
    ('image', 'Image', ('image1', 'image1_credit')),
    ('status', 'Status to confirm', ('status',)),
    ('description', 'Description', ('description',)),
    ('aim', 'Objectives', ('aim',)),
    ('country', 'Country', ()),
)
GAP_KEYS = tuple(key for key, _label, _fields in GAPS)
LABELS = {key: label for key, label, _fields in GAPS}
TEXT_FIELDS = ('url', 'data_url', 'author', 'author_email', 'status',
               'description', 'aim', 'image1_credit')
IMPORT_SOURCE = 'koen-cs-list-2026-11'
PAGE_SIZE = 25
AUDIT_LIMIT = 50


def _now():
    return datetime.datetime.utcnow().replace(microsecond=0)


def _blank(html):
    from ckanext.c4w.text import html_to_text
    return not (html_to_text(html or '') or '').strip()


def _placed(project_ids):
    """Projects that have a country or a geographic extent."""
    if not project_ids:
        return set()
    rows = (db.Session.query(db.C4wTermLink.entity_id)
            .filter(db.C4wTermLink.entity_type == 'project',
                    db.C4wTermLink.vocabulary.in_(('country', 'geographic_extent')),
                    db.C4wTermLink.entity_id.in_(list(project_ids)))
            .distinct().all())
    return {row[0] for row in rows}


def missing(project, extras=None, placed=None):
    """Gap keys for one project, in screen order, dismissed ones included."""
    extras = db.load_extras(project.extras) if extras is None else extras
    if placed is None:
        placed = project.id in _placed([project.id])
    found = {
        'website': not project.url,
        'contact': not project.author_email,
        'data': not project.data_url,
        'image': not project.image1_url,
        'status': bool(extras.get('status_unverified')),
        'description': _blank(project.description),
        'aim': _blank(project.aim),
        'country': not placed,
    }
    return [key for key in GAP_KEYS if found[key]]


def open_gaps(project):
    """Gaps nobody has filled or marked as not available."""
    extras = db.load_extras(project.extras)
    dismissed = set(extras.get('gaps_dismissed') or ())
    return [key for key in missing(project, extras) if key not in dismissed]


def _row(project, extras, placed):
    gaps = missing(project, extras, placed)
    dismissed = [k for k in (extras.get('gaps_dismissed') or ()) if k in gaps]
    return dict(
        id=project.id, slug=project.slug, name=project.name,
        hidden=bool(project.hidden), approved=bool(project.approved),
        url=project.url or '', data_url=project.data_url or '',
        author=project.author or '', author_email=project.author_email or '',
        status=project.status or '', image1_credit=project.image1_credit or '',
        claimable=bool(extras.get('claimable')),
        imported=extras.get('source') == IMPORT_SOURCE,
        reference_url=extras.get('reference_url') or '',
        contact_raw=extras.get('contact_raw') or '',
        country_raw=extras.get('country_raw') or '',
        suggested_website=extras.get('suggested_website') or '',
        suggested_data_url=extras.get('suggested_data_url') or '',
        suggestion_evidence=extras.get('suggestion_evidence') or '',
        missing=gaps, dismissed=dismissed,
        open=[k for k in gaps if k not in dismissed])


@tk.side_effect_free
def c4w_project_gaps(context, data_dict):
    """Projects with missing information, for the administrators' screen.

    ``scope``: ``imported`` (default, the UNESCO list) or ``all``.
    ``gap``: one gap key, to list only the projects missing it.
    ``dismissed``: true to include gaps marked as not available.
    ``q``: words in the project name or slug.
    ``page``: 1-based; ``all`` returns every row (for the CSV export).
    """
    tk.check_access('c4w_project_gaps', context, data_dict)
    db.ensure_mappers()
    data = dict(data_dict or {})
    scope = data.get('scope') or 'imported'
    if scope not in ('imported', 'all'):
        raise tk.ValidationError({'scope': [tk._('Invalid scope.')]})
    gap = data.get('gap') or ''
    if gap and gap not in GAP_KEYS:
        raise tk.ValidationError({'gap': [tk._('Invalid filter.')]})
    with_dismissed = str(data.get('dismissed', '')).lower() in ('1', 'true', 'yes', 'on')
    words = [w for w in re.split(r'\s+', (data.get('q') or '').strip().lower()) if w]

    query = db.Session.query(db.C4wProject)
    projects = query.order_by(db.C4wProject.name.asc(), db.C4wProject.id.asc()).all()
    rows = []
    for project in projects:
        extras = db.load_extras(project.extras)
        if scope == 'imported' and extras.get('source') != IMPORT_SOURCE:
            continue
        rows.append((project, extras))
    placed = _placed({project.id for project, _extras in rows})
    listed = [_row(project, extras, project.id in placed) for project, extras in rows]

    counts = {key: 0 for key in GAP_KEYS}
    for item in listed:
        for key in item['open']:
            counts[key] += 1
    shown = 'missing' if with_dismissed else 'open'
    results = [item for item in listed if item[shown]]
    if gap:
        results = [item for item in results if gap in item[shown]]
    if words:
        results = [item for item in results if all(
            w in (item['name'] or '').lower() or w in item['slug'] for w in words)]
    count = len(results)

    if data.get('page') == 'all':
        page, pages = 1, 1
    else:
        try:
            page = max(1, int(data.get('page') or 1))
        except (TypeError, ValueError):
            page = 1
        pages = max(1, (count + PAGE_SIZE - 1) // PAGE_SIZE)
        page = min(page, pages)
        results = results[(page - 1) * PAGE_SIZE:page * PAGE_SIZE]
        # The edit version only for the rows on screen: it costs a dictize.
        by_id = {project.id: project for project, _extras in rows}
        for item in results:
            project = by_id[item['id']]
            rev = editorial.revision('project', project)
            item['version'] = editorial.version('project', project, rev)
            item['blocked'] = editorial._blocked(rev)
    return dict(count=count, page=page, pages=pages, results=results,
                counts=counts, total=len(listed),
                complete=sum(1 for item in listed if not item['open']),
                gaps=[dict(key=k, label=l) for k, l, _f in GAPS])


def _as_html(text):
    """Plain text from a textarea becomes paragraphs. Pasted HTML (text
    that starts with a tag) is left for the editor's sanitiser."""
    text = (text or '').strip()
    if not text or re.match(r'<[a-zA-Z][^>]*>', text):
        return text
    from markupsafe import escape
    return u''.join(u'<p>%s</p>' % escape(part).replace('\n', '<br>')
                    for part in re.split(r'\n\s*\n', text) if part.strip())


def c4w_project_gap_fill(context, data_dict):
    """Fill some of a project's missing fields, or mark gaps as not available.

    Only non-empty values are written; an empty input never clears a field.
    ``status`` confirms the activity status even when the value is
    unchanged. ``dismiss`` lists gap keys to mark as not available and
    ``restore`` lists keys to put back on the list.
    """
    tk.check_access('c4w_project_gap_fill', context, data_dict)
    db.ensure_mappers()
    project = _common.get_by_reference(db.C4wProject, data_dict.get('id'))
    if project is None:
        raise tk.ObjectNotFound(tk._('Project not found'))

    def keys(name):
        value = data_dict.get(name) or []
        value = [value] if isinstance(value, str) else list(value)
        unknown = [v for v in value if v not in GAP_KEYS]
        if unknown:
            raise tk.ValidationError({name: [tk._('Unknown item: %s') % ', '.join(unknown)]})
        return value

    dismiss, restore = keys('dismiss'), keys('restore')
    values = {}
    for name in TEXT_FIELDS:
        value = data_dict.get(name)
        if isinstance(value, str) and value.strip():
            values[name] = value.strip()
    for name in ('description', 'aim'):
        if name in values:
            values[name] = _as_html(values[name])
    upload = data_dict.get('image1')
    if getattr(upload, 'filename', None):
        values['image1'] = upload
        for axis in ('x', 'y'):
            values['image1_crop_%s' % axis] = data_dict.get('image1_crop_%s' % axis, .5)
    if not values and not dismiss and not restore:
        raise tk.ValidationError({'message': [tk._('Nothing to save: fill in a field or mark it as not available.')]})

    filled = []
    if values:
        stored = {name: getattr(project, name) for name in TEXT_FIELDS}
        editorial.c4w_admin_entity_update(context, dict(
            values, section='projects', id=project.id,
            version=data_dict.get('version')))
        db.Session.refresh(project)
        filled = sorted(name for name in values
                        if name in ('image1', 'status') or (
                            name in TEXT_FIELDS
                            and getattr(project, name) != stored.get(name)))
    else:
        # Marking a gap is not an edit of the content, but it still must not
        # be based on a page that is out of date.
        rev = editorial.revision('project', project, lock=True)
        if data_dict.get('version') != editorial.version('project', project, rev):
            raise tk.ValidationError({'message': [tk._('This content changed in another window. Reload before saving.')]})

    extras = db.load_extras(project.extras)
    if 'status' in values:
        extras.pop('status_unverified', None)
    if 'url' in values:
        extras.pop('suggested_website', None)
    if 'data_url' in values:
        extras.pop('suggested_data_url', None)
    if not extras.get('suggested_website') and not extras.get('suggested_data_url'):
        extras.pop('suggestion_evidence', None)
    dismissed = [k for k in (extras.get('gaps_dismissed') or []) if k not in restore]
    dismissed += [k for k in dismiss if k not in dismissed]
    if dismissed:
        extras['gaps_dismissed'] = dismissed
    else:
        extras.pop('gaps_dismissed', None)
    user = context.get('auth_user_obj')
    audit = list(extras.get('gap_fills') or [])
    audit.append(dict(at=_now().isoformat(), by=getattr(user, 'name', None),
                      filled=filled, dismissed=dismiss, restored=restore))
    extras['gap_fills'] = audit[-AUDIT_LIMIT:]
    project.extras = db.dump_extras(extras)
    # A person reviewed this row: the sheet import must not rewrite it.
    project.modified = db._utcnow()
    db.Session.commit()
    return dict(id=project.id, slug=project.slug, filled=filled,
                missing=missing(project, extras), dismissed=dismissed,
                open=open_gaps(project))


def get_actions():
    return {f.__name__: f for f in (c4w_project_gaps, c4w_project_gap_fill)}


def _admin_auth(context, data_dict):
    return {'success': _common.is_content_admin(context)}


def get_auth_functions():
    return {name: _admin_auth for name in get_actions()}

"""Safe, human-readable revision comparisons shared by review pages."""
import json
import ckan.plugins.toolkit as tk
from markupsafe import Markup
from ckanext.c4w.logic.sanitize import sanitize_html, sanitize_rich_html


def decision_for(status, decision):
    """Publishing an unsubmitted draft requires an explicit admin decision.

    Callers enforce content-admin authorization and the revision version first.
    Ordinary approval remains restricted to submitted proposals.
    """
    if decision == 'publish_draft':
        if status not in ('draft', 'changes_requested'):
            raise tk.ValidationError({'message': [tk._('This draft changed status. Reload before publishing.')]})
        return 'approve'
    if status != 'pending':
        raise tk.ValidationError({'message': [tk._('Only submitted proposals can be reviewed.')]})
    if decision not in ('approve', 'changes_requested', 'rejected'):
        raise tk.ValidationError({'decision': [tk._('Invalid decision.')]})
    return decision


def changes(before, after, fields):
    out = []
    for f in fields:
        key = f['name'] + ('_url' if f['kind'] == 'image' else '')
        old, new = before.get(key), after.get(key)
        if old == new or (not old and not new):
            continue
        if f['kind'] in ('multi', 'countries', 'languages', 'terms', 'organisations', 'editors'):
            if isinstance(old, list) and isinstance(new, list) and set(old) == set(new):
                continue
        def display(value):
            if f['kind'] in ('rich', 'news'):
                return Markup((sanitize_rich_html if f['kind'] == 'news' else sanitize_html)(str(value or '')))
            if isinstance(value, list):
                return ', '.join(str(v) for v in value)
            if isinstance(value, dict):
                return json.dumps(value, ensure_ascii=False)
            return str(value) if value is not None else '—'
        out.append(dict(label=f['label'], before=display(old), after=display(new)))
    return out

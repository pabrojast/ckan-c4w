"""Safe, human-readable revision comparisons shared by review pages."""
import json
from markupsafe import Markup
from ckanext.c4w.logic.sanitize import sanitize_html, sanitize_rich_html


def changes(before, after, fields):
    out = []
    for f in fields:
        key = f['name'] + ('_url' if f['kind'] == 'image' else '')
        old, new = before.get(key), after.get(key)
        if old == new or (not old and not new):
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

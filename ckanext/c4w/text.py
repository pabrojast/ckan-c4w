# encoding: utf-8
"""Pure text and JSON helpers.

CKAN-free on purpose, and it must stay that way. Two consumers depend on it:

* ``migrate/mapping.py``, which turns legacy Django rows into c4w dicts and is
  unit-tested against a JSON fixture with nothing but the standard library --
  that test covers most of the migration risk for the price of a fixture;
* ``db.py``, which needs the same slug rules the importer used.

Anything here that grew a CKAN import would take both of those with it.
"""
import datetime
import json
import re
import unicodedata

_SLUG_STRIP_RE = re.compile(r'[^a-z0-9]+')


def slugify(text, max_length=90):
    """Lowercase ASCII slug.

    Two kinds of non-ASCII character are handled differently on purpose:

    * COMBINING MARKS are deleted, so 'Andalucía' folds to 'andalucia' rather
      than losing the letter and becoming 'andaluca';
    * every other non-ASCII character becomes a SEPARATOR, not nothing. A
      typographic apostrophe is the case that matters: deleting it made
      'Côte d’Ivoire' slug to 'cote-divoire' while the straight-quote spelling
      of the same name gave 'cote-d-ivoire'. One name, two URLs, and the
      duplicate check that relies on the slug never fires.
    """
    if not text:
        return u''
    value = unicodedata.normalize('NFKD', u'%s' % text)
    value = u''.join(c for c in value if not unicodedata.combining(c))
    # 'replace' -- not 'ignore' -- so a dropped character still separates.
    value = value.encode('ascii', 'replace').decode('ascii')
    value = _SLUG_STRIP_RE.sub(u'-', value.lower()).strip(u'-')
    return value[:max_length].strip(u'-')


def normalise_term(text):
    """Slug form of a vocabulary term -- the value stored in ``term``.

    Longer cap than a slug: a term is not a URL segment, and truncating
    'download-software-for-distributed-computing' would collide with nothing
    but would read as a different concept.
    """
    return slugify(text, max_length=120)


def split_free_terms(raw, limit=30):
    """Parse a comma-separated free-text tag field.

    Trims, drops empties, de-duplicates case-insensitively while keeping the
    author's order and their original capitalisation for the label, and caps
    the count so a paste accident cannot write thousands of link rows.

    Returns ``[(term, label), ...]``.
    """
    if raw is None:
        return []
    if isinstance(raw, (list, tuple)):
        parts = list(raw)
    else:
        parts = u'%s' % raw
        parts = parts.split(u',')
    out = []
    seen = set()
    for part in parts:
        label = (u'%s' % part).strip()
        if not label:
            continue
        term = normalise_term(label)
        if not term or term in seen:
            continue
        seen.add(term)
        out.append((term, label))
        if len(out) >= limit:
            break
    return out


def load_extras(raw):
    """Parse an ``extras`` column into a dict, never raising.

    Malformed JSON in one row must not take down a listing page, so a bad
    value degrades to an empty dict.
    """
    if not raw:
        return {}
    if isinstance(raw, dict):
        return raw
    try:
        value = json.loads(raw)
    except (ValueError, TypeError):
        return {}
    return value if isinstance(value, dict) else {}


def dump_extras(value):
    """Serialise an extras dict, coercing dates and times to ISO strings."""
    def _default(obj):
        if isinstance(obj, (datetime.datetime, datetime.date, datetime.time)):
            return obj.isoformat()
        return u'%s' % obj

    return json.dumps(value or {}, default=_default, sort_keys=True)

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


# --------------------------------------------------------------------------- #
# Normalising legacy text
# --------------------------------------------------------------------------- #
#
# Every rule below exists because of something measured in the production
# corpus, not because it seemed tidy. The citations are to the field-map
# survey in docs/migration-field-map.json.

# Characters that occupy no width but survive every sanitiser and every
# .strip(): CKEditor and Word paste them freely. A field holding only these
# looks empty to a reader and non-empty to `if value`.
_INVISIBLE = u'\u200b\u200c\u200d\ufeff'

# Space-like characters that are not ASCII space. NBSP is the one that
# matters: two organisation descriptions use it as an ordinary word separator,
# so a visitor searching a phrase they can read on screen gets no results
# because the stored bytes hold U+00A0 where they typed U+0020.
_SPACEY = u'\u00a0\u202f\u2009\u2007'

_TAG_RE = re.compile(r'<[^>]*>')
_WS_RE = re.compile(r'[ \t]+')


def normalise_text(value):
    """Canonicalise a legacy free-text value.

    Normalises CRLF to LF, deletes zero-width characters and maps the
    non-breaking spaces to ordinary ones, then trims. Returns None for
    anything that ends up empty, so a column and its "unset" state agree
    instead of splitting between NULL and ''.

    CRLF matters more than it looks: 18 of the 43 projects carry it, and once
    it is inside a JSON extras blob it is a literal backslash-r that is
    invisible in every inspection tool and makes a re-import produce a
    different byte string for identical input.
    """
    if value is None:
        return None
    text = u'%s' % value
    text = text.replace(u'\r\n', u'\n').replace(u'\r', u'\n')
    for char in _INVISIBLE:
        text = text.replace(char, u'')
    for char in _SPACEY:
        text = text.replace(char, u' ')
    text = _WS_RE.sub(u' ', text).strip()
    return text or None


def html_to_text(value):
    """Plain-text shadow of an HTML value, for searching.

    The listing searches its columns with a raw ILIKE, and the stored value is
    sanitised HTML -- so a visitor searching a phrase they can read verbatim on
    the page gets nothing whenever a tag or an entity falls inside it.
    'E. coli levels' misses because the stored text is 'E. coli</i> levels'.

    Entities are decoded BEFORE the tags are stripped, so '&lt;' survives as a
    literal '<' rather than becoming the start of a phantom tag.
    """
    if value is None:
        return None
    import html as _html

    text = u'%s' % value
    # A block boundary is a word boundary: without this, '<p>a</p><p>b</p>'
    # collapses to 'ab' and neither word is findable.
    text = re.sub(r'(?i)<(br|/p|/div|/li|/h[1-6]|/tr)\b[^>]*>', u'\n', text)
    text = _TAG_RE.sub(u' ', text)
    text = _html.unescape(text)
    return normalise_text(text)


def is_blank_html(value):
    """Whether an HTML value carries no readable content.

    A WYSIWYG editor stores '<p><br></p>' or '<p>&nbsp;</p>' for a field the
    author opened and left alone -- eleven of the 43 projects have exactly that
    in howToParticipate. Those are empty to a reader and truthy to `if value`,
    which is how an empty section heading ends up on a page.
    """
    return not html_to_text(value)


# Schemes that may appear in a stored link. Everything else -- javascript:,
# data:, vbscript:, file: -- is refused outright: these values are rendered
# straight into an href, and the templates do not sanitise an attribute.
SAFE_SCHEMES = (u'http', u'https', u'mailto', u'ftp')

_SCHEME_RE = re.compile(r'^([A-Za-z][A-Za-z0-9+.-]*):')
_URL_WHITESPACE_RE = re.compile(r'[\t\n\r\x00\x0b\x0c\u2028\u2029]')


def ensure_scheme(url, default=u'https'):
    """Add a scheme to a bare host, and refuse a dangerous one.

    Three project URLs are stored as 'www.example.org' with no scheme; a
    browser resolves that against the current page, so the link points back
    into the portal instead of out to the project.

    Returns None for a scheme that is not in SAFE_SCHEMES. These values go
    straight into an href, and a template cannot sanitise an attribute -- so
    'javascript:...' would be a stored XSS on a page anyone can reach.
    """
    # The URL-whitespace strip runs FIRST, on the raw value: normalise_text
    # turns a tab into a SPACE, and 'java<TAB>script:x' would then read as
    # 'java script:x' -- no longer a scheme to this parser, but a browser
    # deletes the tab and executes it.
    if url is None:
        return None
    value = normalise_text(_URL_WHITESPACE_RE.sub(u'', u'%s' % url))
    if not value:
        return None
    match = _SCHEME_RE.match(value)
    if match:
        return value if match.group(1).lower() in SAFE_SCHEMES else None
    if value.startswith(u'//'):
        # Protocol-relative: keep it same-scheme rather than guessing.
        return u'%s:%s' % (default, value)
    if value.startswith(u'/'):
        return value
    # Only add a scheme to something that actually looks like a hostname.
    if re.match(r'^[A-Za-z0-9][A-Za-z0-9.-]*\.[A-Za-z]{2,}(/|$|\?|#)', value):
        return u'%s://%s' % (default, value)
    return value

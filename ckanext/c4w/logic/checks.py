# encoding: utf-8
"""CKAN-free value checks behind the navl validators.

Every rule a form applies lives here as a plain function that returns the
normalised value or raises ``ValueError`` with a message a visitor may read.
``logic/validators.py`` wraps each one in a navl validator; keeping the rule
itself CKAN-free is what lets ``tests/test_checks.py`` run in a bare
checkout, and what lets the CLI reuse the same rules without a site.
"""
import datetime
import re

from ckanext.c4w import constants
from ckanext.c4w.text import ensure_scheme, normalise_text, split_free_terms

_COUNTRY_RE = re.compile(r'^[A-Z]{2}$')
_LANGUAGE_RE = re.compile(r'^[a-z]{2,3}$')
_DOI_RE = re.compile(r'^10\.\d{4,9}/\S+$')
_EMAIL_RE = re.compile(r'^[^@\s]+@[^@\s]+\.[^@\s]+$')
_SLUG_RE = re.compile(r'^[a-z0-9]+(?:-[a-z0-9]+)*$')

MIN_PASSWORD_LENGTH = 8


def as_list(value):
    """A list from a form value that may arrive once, many times or as CSV."""
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        return [u'%s' % v for v in value if v not in (None, u'', '')]
    text = u'%s' % value
    if not text.strip():
        return []
    return [part.strip() for part in text.split(u',') if part.strip()]


def is_truthy(value):
    if value is True:
        return True
    if isinstance(value, (list, tuple)):
        return any(is_truthy(v) for v in value)
    return (u'%s' % (value or u'')).strip().lower() in (
        u'1', u'true', u'on', u'yes')


def clean_text(value, max_length=None, required=False):
    """Strip and normalise a single-line value; enforce a length cap."""
    text = (normalise_text(u'%s' % value) or u'') if value is not None \
        else u''
    text = u' '.join(text.split())
    if required and not text:
        raise ValueError(u'Missing value')
    if max_length and len(text) > max_length:
        raise ValueError(u'Must be at most %d characters' % max_length)
    return text


def check_country_code(value):
    code = (u'%s' % (value or u'')).strip().upper()
    if not _COUNTRY_RE.match(code):
        raise ValueError(u'Not an ISO 3166-1 alpha-2 country code')
    return code


def check_country_codes(value, limit=20):
    codes = []
    for raw in as_list(value):
        code = check_country_code(raw)
        if code not in codes:
            codes.append(code)
    if len(codes) > limit:
        raise ValueError(u'At most %d countries' % limit)
    return codes


def check_language_code(value):
    code = (u'%s' % (value or u'')).strip().lower()
    if not _LANGUAGE_RE.match(code):
        raise ValueError(u'Not an ISO 639 language code')
    return code


def check_doi(value):
    text = (u'%s' % (value or u'')).strip()
    for prefix in (u'https://doi.org/', u'http://doi.org/', u'doi:'):
        if text.lower().startswith(prefix):
            text = text[len(prefix):]
    if not _DOI_RE.match(text):
        raise ValueError(u'Not a DOI (expected 10.xxxx/...)')
    return text


def check_url(value):
    """An http(s) URL with a scheme. Refuses javascript: and friends."""
    text = (u'%s' % (value or u'')).strip()
    if not text:
        return u''
    url = ensure_scheme(text)
    if not url or not url.lower().startswith((u'http://', u'https://')):
        raise ValueError(u'Not a web address')
    if len(url) > 2000:
        raise ValueError(u'Address too long')
    return url


def check_urls(value, limit=5):
    urls = []
    for raw in as_list(value):
        url = check_url(raw)
        if url and url not in urls:
            urls.append(url)
    if len(urls) > limit:
        raise ValueError(u'At most %d links' % limit)
    return urls


def check_email(value):
    text = (u'%s' % (value or u'')).strip()
    if not _EMAIL_RE.match(text) or len(text) > 254:
        raise ValueError(u'Not an e-mail address')
    return text


def check_vocabulary_term(name, value):
    """One term of a closed vocabulary."""
    term = (u'%s' % (value or u'')).strip()
    allowed = constants.vocabulary_terms(name)
    if allowed is None:
        raise ValueError(u'Unknown vocabulary %s' % name)
    if term not in allowed:
        raise ValueError(u'Not a valid choice')
    return term


def check_vocabulary_terms(name, value, max_items=None, min_items=0):
    """Many terms of a closed vocabulary, de-duplicated, order kept."""
    allowed = constants.vocabulary_terms(name)
    if allowed is None:
        raise ValueError(u'Unknown vocabulary %s' % name)
    terms = []
    for raw in as_list(value):
        term = (u'%s' % raw).strip()
        if term not in allowed:
            raise ValueError(u'Not a valid choice: %s' % term)
        if term not in terms:
            terms.append(term)
    if len(terms) < min_items:
        raise ValueError(u'Choose at least %d' % min_items)
    if max_items and len(terms) > max_items:
        raise ValueError(u'Choose at most %d' % max_items)
    return terms


def check_free_terms(value, limit=30):
    """Free keywords as ``[(term, label), ...]``."""
    return split_free_terms(value, limit=limit)


def check_date(value):
    """An ISO date (``YYYY-MM-DD``) as a ``datetime.date``."""
    if isinstance(value, datetime.datetime):
        return value.date()
    if isinstance(value, datetime.date):
        return value
    text = (u'%s' % (value or u'')).strip()
    if not text:
        return None
    try:
        return datetime.datetime.strptime(text[:10], '%Y-%m-%d').date()
    except ValueError:
        raise ValueError(u'Not a date (expected YYYY-MM-DD)')


def check_end_after(start, end):
    if start and end and end < start:
        raise ValueError(u'End date is before the start date')
    return end


def check_password(password, confirm):
    password = u'%s' % (password or u'')
    if len(password) < MIN_PASSWORD_LENGTH:
        raise ValueError(u'Password must be at least %d characters'
                         % MIN_PASSWORD_LENGTH)
    if password != (u'%s' % (confirm or u'')):
        raise ValueError(u'The two passwords do not match')
    return password


def check_true(value):
    if not is_truthy(value):
        raise ValueError(u'Must be accepted')
    return True


def check_choice(value, choices):
    text = (u'%s' % (value or u'')).strip()
    if text not in set(choices):
        raise ValueError(u'Not a valid choice')
    return text


def check_bins(value):
    """Six strictly increasing numbers, or None."""
    if value in (None, u'', []):
        return None
    if isinstance(value, str):
        parts = [p for p in re.split(r'[,\s]+', value.strip()) if p]
    else:
        parts = list(value)
    try:
        numbers = [float(p) for p in parts]
    except (TypeError, ValueError):
        raise ValueError(u'Bins must be numbers')
    if len(numbers) != 6:
        raise ValueError(u'Exactly six cut points are needed')
    if any(b <= a for a, b in zip(numbers, numbers[1:])):
        raise ValueError(u'Cut points must increase')
    return numbers


def check_slug(value):
    text = (u'%s' % (value or u'')).strip().lower()
    if not _SLUG_RE.match(text):
        raise ValueError(u'Not a slug')
    return text

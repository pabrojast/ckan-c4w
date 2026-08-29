# encoding: utf-8
"""Suggesting links between the C4W directory and CKAN organizations.

``c4w_organisation.ckan_org_id`` is optional and deliberately not a foreign
key. Filling it wrongly is worse than leaving it empty: the organisation
detail page offers a "Datasets on IHP-WINS" link, so a bad match sends readers
to another institution's catalogue under this one's name.

So this runs as a SEPARATE, opt-in step and prints a table for a human to read
before anything is written. Scoring is deterministic and CKAN-free apart from
the caller supplying the candidate list, which keeps it unit-testable.
"""
import difflib
import re

# Words that carry no distinguishing weight in an institution's name.
_STOPWORDS = frozenset((
    'the', 'of', 'for', 'and', 'a', 'an', 'de', 'la', 'le', 'el',
    'institute', 'institut', 'university', 'universidad', 'universite',
    'centre', 'center', 'foundation', 'fundacion', 'association',
))

_PUNCT_RE = re.compile(r'[^a-z0-9 ]+')
_WS_RE = re.compile(r'\s+')


def normalise_name(value):
    """Fold a name to its distinguishing words, for comparison only."""
    if not value:
        return u''
    import unicodedata
    text = unicodedata.normalize('NFKD', u'%s' % value)
    text = text.encode('ascii', 'ignore').decode('ascii').lower()
    text = _PUNCT_RE.sub(u' ', text)
    words = [w for w in _WS_RE.split(text) if w and w not in _STOPWORDS]
    return u' '.join(words)


def domain_of(url):
    """The registrable-ish host of a URL, without a leading www."""
    if not url:
        return u''
    try:
        from urllib.parse import urlparse
    except ImportError:                      # pragma: no cover - py2
        from urlparse import urlparse
    value = u'%s' % url
    if u'://' not in value:
        value = u'http://' + value
    host = (urlparse(value).hostname or u'').lower()
    return host[4:] if host.startswith(u'www.') else host


def score(c4w_org, ckan_org):
    """How confident we are that these are the same institution, 0..1.

    Returns ``(score, reason)``. The reason is printed for the human review,
    because a bare number is not something anyone can check.
    """
    c4w_slug = (c4w_org.get('slug') or u'').lower()
    ckan_name = (ckan_org.get('name') or u'').lower()
    if c4w_slug and c4w_slug == ckan_name:
        return 1.0, 'slug matches the CKAN name exactly'

    c4w_domain = domain_of(c4w_org.get('url'))
    ckan_domain = domain_of(ckan_org.get('url')
                            or (ckan_org.get('extras') or {}).get('url'))
    if c4w_domain and c4w_domain == ckan_domain:
        return 0.95, 'same website domain (%s)' % c4w_domain

    left = normalise_name(c4w_org.get('name'))
    right = normalise_name(ckan_org.get('title') or ckan_org.get('name'))
    if not left or not right:
        return 0.0, 'no comparable name'
    ratio = difflib.SequenceMatcher(None, left, right).ratio()
    return ratio, 'name similarity %.2f (%r vs %r)' % (ratio, left, right)


def propose(c4w_orgs, ckan_orgs, threshold=0.85):
    """Suggest a CKAN organization for each unlinked C4W organisation.

    A suggestion is made only when the best candidate clears the threshold AND
    is the only one that does. A tie means two institutions look equally like
    the answer, and picking either would be a coin flip written into the
    database.
    """
    proposals = []
    for c4w_org in c4w_orgs:
        if c4w_org.get('ckan_org_id'):
            continue
        scored = []
        for ckan_org in ckan_orgs:
            value, reason = score(c4w_org, ckan_org)
            if value >= threshold:
                scored.append((value, ckan_org, reason))
        scored.sort(key=lambda item: item[0], reverse=True)
        if not scored:
            proposals.append({'c4w_slug': c4w_org.get('slug'),
                              'c4w_name': c4w_org.get('name'),
                              'ckan_name': None, 'score': 0.0,
                              'reason': 'no candidate above the threshold'})
            continue
        if len(scored) > 1 and abs(scored[0][0] - scored[1][0]) < 1e-9:
            proposals.append({
                'c4w_slug': c4w_org.get('slug'),
                'c4w_name': c4w_org.get('name'),
                'ckan_name': None, 'score': scored[0][0],
                'reason': 'tie between %s and %s -- not proposed'
                          % (scored[0][1].get('name'), scored[1][1].get('name')),
            })
            continue
        value, ckan_org, reason = scored[0]
        proposals.append({'c4w_slug': c4w_org.get('slug'),
                          'c4w_name': c4w_org.get('name'),
                          'ckan_name': ckan_org.get('name'),
                          'ckan_id': ckan_org.get('id'),
                          'score': round(value, 3), 'reason': reason})
    return proposals

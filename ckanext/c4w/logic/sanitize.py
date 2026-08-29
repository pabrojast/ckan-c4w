# encoding: utf-8
"""THE HTML sanitizers for ckanext-c4w -- two allowlists, no more.

Every piece of user-supplied HTML that gets stored and later rendered with
``| safe`` passes through one of exactly two functions here, so every render
path shares an audited allowlist and none of them can drift:

* :func:`sanitize_html` -- the RESTRICTIVE list, for the descriptions and
  abstracts of projects, organisations, resources, platforms and events, and
  for the rejection ``reason`` shown back to authors.
* :func:`sanitize_rich_html` -- the WIDER list, for blog post bodies, where an
  editor legitimately needs headings and tables.

They are deliberately two functions with two frozen constant sets rather than
one function taking an ``allowlist=`` argument: a parameter is an open
invitation for some future caller to hand the wide list to the news path.

``bleach`` is imported LAZILY so byte-compilation and the CKAN-free verification
never require it. When bleach is not installed both functions FAIL CLOSED: every
tag is stripped so no markup at all reaches the database (and thus never the
``| safe`` render path).
"""
import re

# RESTRICTIVE allowlist: inline emphasis, links, lists, small headings and
# blockquotes only. Deliberately NO images, tables, styles, iframes or scripts.
ALLOWED_TAGS = [
    'b', 'i', 'em', 'strong', 'u', 'a', 'p', 'ul', 'ol', 'li', 'br',
    'h3', 'h4', 'blockquote',
    # sub/sup are here because the corpus needs them: project 36 is KdUINO,
    # about the diffuse attenuation coefficient K_d, and stripping the tag
    # flattens 'K<sub>d</sub>' into the meaningless 'Kd'. They take no
    # attributes and open no script surface, so the restrictive list gives up
    # nothing by keeping them.
    'sub', 'sup',
]
# Only anchors keep attributes, and only these three.
ALLOWED_ATTRS = {'a': ['href', 'title', 'rel']}
# Link protocols we trust; anything else (javascript:, data:, ...) is dropped.
ALLOWED_PROTOCOLS = ['http', 'https', 'mailto']

# Fallback tag stripper used only when bleach is unavailable.
_TAG_RE = re.compile(r'<[^>]*>')

# Elements whose CONTENT must go with them. bleach's ``strip=True`` removes a
# disallowed tag but KEEPS its text, so ``<script>alert(1)</script>`` survives
# as the literal text ``alert(1)``. That is inert -- the tag is gone -- but it
# dumps script/style source into the page as visible copy. These are the only
# elements whose body is never prose, so we drop them whole beforehand.
# Anything this misses is still neutralised by bleach afterwards.
_DROP_ELEMENT_RE = re.compile(
    r'<\s*(script|style|template|noscript)\b[^>]*>.*?<\s*/\s*\1\s*>',
    re.IGNORECASE | re.DOTALL)


# --------------------------------------------------------------------------- #
# Wider allowlist: blog post bodies.                                          #
# --------------------------------------------------------------------------- #
# An editor writing a news post needs sub-headings and the occasional table.
# What it still refuses, and why:
#   * NO <img>  -- a post has a dedicated header image whose URL we validate
#                  and render with our own loading/referrer attributes. A free
#                  <img src> would be a second, unvalidated image ingress with
#                  no host control: a tracking-pixel vector on a public page.
#   * NO <iframe>/<script>/<style>/<object> -- nothing on this portal needs an
#                  author-supplied embed.
#   * NO <h1>/<h2> -- the post title already renders as the page <h1>;
#                  author-level h2 would break the heading outline.
#   * NO class/style/id attributes -- the portal CSS owns presentation.
RICH_ALLOWED_TAGS = [
    'b', 'i', 'em', 'strong', 'u', 's', 'sub', 'sup', 'code', 'a',
    'p', 'ul', 'ol', 'li', 'br', 'hr',
    'h3', 'h4', 'h5', 'blockquote', 'pre',
    'table', 'thead', 'tbody', 'tfoot', 'tr', 'th', 'td', 'caption',
    # Images and embeds are allowed ONLY in the rich list, and only with a
    # validated src -- see _rich_attribute_ok. The migrated news corpus holds
    # 18 in-body images and 2 YouTube embeds, and sanitisation runs BEFORE
    # storage, so refusing the tags here would destroy that content
    # irrecoverably rather than merely hide it.
    'img', 'figure', 'figcaption', 'iframe',
]
# ``scope`` is the one attribute that carries meaning we cannot re-derive: it
# is what makes a data table readable to a screen reader.
#
# The whole dict is wrapped in a CALLABLE (see _rich_attribute_ok) rather than
# being a plain allowlist, because for img and iframe the attribute NAME is not
# the question -- the src VALUE is. A bare {'img': ['src']} would admit
# <img src="https://tracker.example/pixel.gif">, which is an unvalidated image
# ingress on a public page: a tracking pixel that fires for every reader, with
# no host control at all.
RICH_ALLOWED_ATTRS = {
    'a': ['href', 'title', 'rel'],
    'th': ['scope'],
    'img': ['src', 'alt', 'title', 'width', 'height', 'loading'],
    'iframe': ['src', 'title', 'width', 'height', 'allow',
               'allowfullscreen', 'loading', 'referrerpolicy'],
}
RICH_ALLOWED_PROTOCOLS = ['http', 'https', 'mailto']

# Hosts whose iframes may be embedded. Video platforms only, and the
# no-cookie variant first, because the portal should not set a third-party
# tracking cookie on a reader who merely scrolled past a post.
DEFAULT_EMBED_HOSTS = (
    'www.youtube-nocookie.com', 'youtube-nocookie.com',
    'www.youtube.com', 'youtube.com',
    'player.vimeo.com', 'vimeo.com',
)


def _config(key, default=u''):
    """Read a config value without importing CKAN at module import time."""
    try:
        import ckan.plugins.toolkit as tk
        return tk.config.get(key) or default
    except Exception:
        return default


def _trusted_media_hosts():
    return tuple(h for h in _config(
        'ckanext.c4w.trusted_media_hosts').split() if h)


def _trusted_embed_hosts():
    configured = tuple(h for h in _config(
        'ckanext.c4w.trusted_embed_hosts').split() if h)
    return configured or DEFAULT_EMBED_HOSTS


# The characters a browser removes from a URL before resolving it.
_URL_WHITESPACE = u'\t\n\r\x00\x0b\x0c\u2028\u2029'


def _strip_url_whitespace(value):
    """Normalise a URL the way a browser will, before judging it."""
    text = u'%s' % (value or u'')
    for char in _URL_WHITESPACE:
        text = text.replace(char, u'')
    return text.strip()


def _host_of(url):
    try:
        from urllib.parse import urlparse
    except ImportError:                      # pragma: no cover - py2
        from urlparse import urlparse
    return (urlparse(url).hostname or u'').lower()


def image_src_ok(value):
    """Whether an ``<img src>`` may be kept.

    Accepts a SITE-RELATIVE path -- which is what a re-hosted image is -- and
    any host explicitly configured as a media origin (the object store this
    portal uploads to). Everything else is refused, including
    protocol-relative URLs and data: payloads.
    """
    if not value:
        return False
    # Browsers DELETE tab, LF and CR from a URL before resolving it, so
    # '/<TAB>/evil.example/x.png' becomes '//evil.example/x.png' -- protocol
    # relative to a third party, past a check that only read the first two
    # characters. Strip them the same way the browser will, then decide.
    value = _strip_url_whitespace(value)
    if not value:
        return False
    if value.startswith('//') or '\\' in value:
        return False
    if value.startswith('/'):
        return '..' not in value
    host = _host_of(value)
    return bool(host) and host in _trusted_media_hosts()


def embed_src_ok(value):
    """Whether an ``<iframe src>`` may be kept: allowlisted video hosts only."""
    if not value:
        return False
    value = _strip_url_whitespace(value)
    if not value.lower().startswith(('http://', 'https://')):
        return False
    return _host_of(value) in _trusted_embed_hosts()


def _rich_attribute_ok(tag, name, value):
    """bleach attribute filter for the rich allowlist."""
    allowed = RICH_ALLOWED_ATTRS.get(tag)
    if not allowed or name not in allowed:
        return False
    if tag == 'img' and name == 'src':
        return image_src_ok(value)
    if tag == 'iframe' and name == 'src':
        return embed_src_ok(value)
    return True


def sanitize_html(html):
    """Strip ``html`` down to the RESTRICTIVE allowlist BEFORE it is stored.

    Returns the cleaned string. Falsy input is returned unchanged. When bleach is
    not installed we fail closed by removing every tag with a plain regex so no
    markup survives to storage.
    """
    return _clean(html, ALLOWED_TAGS, ALLOWED_ATTRS, ALLOWED_PROTOCOLS)


def sanitize_rich_html(html):
    """Strip ``html`` down to the WIDER project-page allowlist before storage.

    Same fail-closed contract as :func:`sanitize_html`. Only the blog post
    body may use this -- never descriptions, abstracts or rejection reasons.
    """
    return _clean(html, RICH_ALLOWED_TAGS, _rich_attribute_ok,
                  RICH_ALLOWED_PROTOCOLS)


def _clean(html, tags, attributes, protocols):
    if not html:
        return html
    # Drop script/style bodies BEFORE bleach so their source never survives as
    # visible text (see _DROP_ELEMENT_RE), then let bleach do the real work.
    html = _DROP_ELEMENT_RE.sub('', html)
    try:
        import bleach
    except ImportError:
        return _TAG_RE.sub('', html)
    return bleach.clean(
        html,
        tags=tags,
        attributes=attributes,
        protocols=protocols,
        strip=True,
    )

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
]
# ``scope`` is the one attribute that carries meaning we cannot re-derive: it
# is what makes a data table readable to a screen reader.
RICH_ALLOWED_ATTRS = {'a': ['href', 'title', 'rel'], 'th': ['scope']}
RICH_ALLOWED_PROTOCOLS = ['http', 'https', 'mailto']


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
    return _clean(html, RICH_ALLOWED_TAGS, RICH_ALLOWED_ATTRS,
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

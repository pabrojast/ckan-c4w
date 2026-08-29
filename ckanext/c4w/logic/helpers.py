# encoding: utf-8
"""Template helpers for ckanext-c4w.

Two rules.

**A helper never touches the database directly** -- it goes through the action
layer, so authorisation and visibility filtering cannot be bypassed by
rendering a template.

**Helpers on public surfaces fail soft.** The home page and the entity landing
pages must never return a 500 because a counter query failed; they degrade to
a zero or an empty list. A visitor seeing "0 projects" for a minute is a far
better failure than an error page on the portal's front door.
"""
import logging

import ckan.plugins.toolkit as tk

from ckanext.c4w import __version__, constants

log = logging.getLogger(__name__)


def c4w_url(endpoint, **kwargs):
    """Build a URL inside whichever C4W blueprint is serving this request.

    The portal can be mounted twice: at /citizens4water and, before the
    cutover, at a preview prefix as well. Templates must not hard-code
    ``'c4w.<endpoint>'`` or every link on a preview page would jump the
    visitor back into the live Django site.
    """
    blueprint = 'c4w'
    try:
        from flask import request
        if request.blueprint in ('c4w', 'c4w_preview'):
            blueprint = request.blueprint
    except Exception:
        # No request context (CLI, tests): the canonical mount is right.
        pass
    return tk.url_for('%s.%s' % (blueprint, endpoint), **kwargs)


# The portal's own navigation, in display order. Endpoints are added to the
# blueprint increment by increment, and ``c4w_nav`` skips any that is not
# registered yet, so the brand band grows on its own instead of needing a
# template edit (and a broken link) each time.
_NAV = (
    ('project_list', u'Projects'),
    ('resource_list', u'Resources'),
    ('training_resource_list', u'Training resources'),
    ('organisation_list', u'Organisations'),
    ('platform_list', u'Platforms'),
    ('event_list', u'Events'),
    ('post_list', u'News'),
)


def c4w_nav():
    """Resolvable entries of the portal sub-navigation.

    Returns ``[{'endpoint':…, 'url':…, 'label':…, 'active': bool}]``. An entry
    whose endpoint does not exist yet is omitted rather than rendered as a
    dead link.
    """
    current = None
    try:
        from flask import request
        current = request.endpoint
    except Exception:
        pass
    out = []
    for endpoint, label in _NAV:
        try:
            url = c4w_url(endpoint)
        except Exception:
            continue
        out.append({
            'endpoint': endpoint,
            'url': url,
            'label': label,
            'active': bool(current and current.endswith('.' + endpoint)),
        })
    return out


def c4w_option_list(vocabulary):
    """``[{'term':…, 'label':…}]`` for a vocabulary, for building a select.

    Unknown or free vocabularies return an empty list -- the caller renders a
    text input for those, not a dropdown.
    """
    pairs = (constants.VOCABULARIES.get(vocabulary)
             or constants.COLUMN_VOCABULARIES.get(vocabulary)
             or ())
    return [{'term': term, 'label': label} for term, label in pairs]


def c4w_term_label(vocabulary, term):
    """Display label for a stored term, falling back to the term itself."""
    return constants.label_for(vocabulary, term)


def c4w_form_steps():
    """The six stages of the project form, from the single definition."""
    return constants.PROJECT_FORM_STEPS


def c4w_stats():
    """Headline counts for the home page. Fail-soft: ``{}`` on any error."""
    try:
        return tk.get_action('c4w_stats')({}, {})
    except Exception:
        log.debug("ckanext-c4w: stats unavailable", exc_info=True)
        return {}


def c4w_country_name(code):
    """Territory name for an ISO 3166-1 alpha-2 code, in the active locale.

    Babel already ships with CKAN and carries the CLDR territory names, so a
    250-entry table does not need to live in this repository -- and it comes
    translated for free. An unknown code renders as itself rather than blank.
    """
    if not code:
        return u''
    code = u'%s' % code
    try:
        from babel import Locale
        locale = Locale.parse(tk.request.environ.get('CKAN_LANG') or 'en')
        return locale.territories.get(code.upper()) or code
    except Exception:
        return code


def c4w_facet_toggle_url(name, value):
    """The current URL with one facet value flipped on or off.

    Facets are LINKS, not a checkbox form with an Apply button: a link works
    with no JavaScript at all, needs no hidden inputs to carry the other
    parameters, and gives every facet value its own shareable URL.

    Paging is dropped on every toggle -- page 4 of the old result set is
    almost never page 4 of the new one.
    """
    from flask import request

    args = request.args.to_dict(flat=False)
    current = [v for v in args.get(name, []) if v]
    value = u'%s' % value
    if value in current:
        current = [v for v in current if v != value]
    else:
        current = current + [value]
    if current:
        args[name] = current
    else:
        args.pop(name, None)
    args.pop('page', None)

    endpoint = (request.endpoint or '').split('.')[-1]
    flat = {}
    for key, values in args.items():
        flat[key] = values[0] if len(values) == 1 else values
    return c4w_url(endpoint, **flat)


def c4w_facet_active(name, value):
    """Whether a facet value is currently selected."""
    try:
        from flask import request
        return u'%s' % value in request.args.getlist(name)
    except Exception:
        return False


def c4w_any_facet_active(names):
    """Whether any of the given facets has a selection, for a 'clear' link."""
    try:
        from flask import request
        return any(any(request.args.getlist(n)) for n in names)
    except Exception:
        return False


def c4w_image_url(entity, field='image1_url'):
    """The URL of an entity image, or None.

    Returns None rather than a placeholder path so the template decides what
    an image-less card looks like.
    """
    if not entity:
        return None
    return entity.get(field) or None


def get_helpers():
    return {
        'c4w_version': lambda: __version__,
        'c4w_url': c4w_url,
        'c4w_nav': c4w_nav,
        'c4w_option_list': c4w_option_list,
        'c4w_term_label': c4w_term_label,
        'c4w_form_steps': c4w_form_steps,
        'c4w_stats': c4w_stats,
        'c4w_country_name': c4w_country_name,
        'c4w_facet_toggle_url': c4w_facet_toggle_url,
        'c4w_facet_active': c4w_facet_active,
        'c4w_any_facet_active': c4w_any_facet_active,
        'c4w_image_url': c4w_image_url,
    }

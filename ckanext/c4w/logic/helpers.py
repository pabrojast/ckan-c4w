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


def get_helpers():
    return {
        'c4w_version': lambda: __version__,
        'c4w_url': c4w_url,
        'c4w_nav': c4w_nav,
        'c4w_option_list': c4w_option_list,
        'c4w_term_label': c4w_term_label,
        'c4w_form_steps': c4w_form_steps,
        'c4w_stats': c4w_stats,
    }

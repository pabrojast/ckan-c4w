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
    ('project_list', u'Projects', 'projects'),
    ('dataset_list', u'Data', 'datasets'),
    ('resource_list', u'Resources', 'resources'),
    ('training_resource_list', u'Training', 'training_resources'),
    ('organisation_list', u'Organisations', 'organisations'),
    ('platform_list', u'Platforms', 'platforms'),
    ('event_list', u'Events', 'events'),
    ('post_list', u'News', 'posts'),
)

_LISTING_ENDPOINTS = frozenset(item[0] for item in _NAV)


def c4w_nav():
    """Resolvable entries of the portal navigation.

    Returns ``[{'endpoint','url','label','stat','active'}]``. An entry
    whose endpoint does not exist yet is omitted rather than rendered as a
    dead link. ``stat`` is the key in ``c4w_stats()``.
    """
    current = None
    try:
        from flask import request
        current = request.endpoint
    except Exception:
        pass
    out = []
    for endpoint, label, stat in _NAV:
        try:
            url = c4w_url(endpoint)
        except Exception:
            continue
        out.append({
            'endpoint': endpoint,
            'url': url,
            'label': label,
            'stat': stat,
            'active': bool(current and current.endswith('.' + endpoint)),
        })
    return out


def _current_path():
    """Path + query for came_from, without a trailing bare ``?``."""
    try:
        from flask import request
        path = request.full_path or request.path or c4w_url('index')
    except Exception:
        return c4w_url('index')
    if path.endswith('?'):
        path = path[:-1]
    return path


def c4w_login_url(came_from=None):
    """The portal's own login page, returning to the page the visitor is on.

    Falls back to CKAN's login while the C4W route is not registered, so a
    half-deployed increment still lets people sign in.
    """
    came_from = came_from or _current_path()
    try:
        return c4w_url('login', came_from=came_from)
    except Exception:
        return tk.url_for('user.login', came_from=came_from)


def c4w_logout_url():
    """CKAN logout, then back to the C4W home rather than IHP-WINS."""
    try:
        return tk.url_for('user.logout', came_from=c4w_url('index'))
    except Exception:
        return tk.url_for('user.logout')


def c4w_register_url():
    """The portal's own registration chooser, not CKAN's built-in signup."""
    try:
        return c4w_url('register_choose')
    except Exception:
        return '/colab'


def c4w_profile_url():
    """CKAN user page, or None when nobody is signed in."""
    user = getattr(tk.g, 'user', None) or getattr(tk.c, 'user', None)
    if not user:
        return None
    try:
        return tk.url_for('user.read', id=user)
    except Exception:
        return None


def c4w_is_sysadmin():
    """True when the current visitor is a CKAN sysadmin."""
    from ckanext.c4w.logic.access import current_userobj
    userobj = current_userobj()
    return bool(userobj is not None and getattr(userobj, 'sysadmin', False))


def c4w_search_endpoint():
    """Listing the header search should submit to.

    On a catalogue listing, search that listing. Everywhere else, projects
    -- they are the inventory the portal exists to hold.
    """
    try:
        from flask import request
        current = (request.endpoint or '').split('.')[-1]
    except Exception:
        current = ''
    if current in _LISTING_ENDPOINTS:
        return current
    return 'project_list'


def c4w_detail_url(entity):
    """Public URL of a dictized entity, or None if it has no slug."""
    if not entity:
        return None
    endpoint = constants.DETAIL_ENDPOINTS.get(entity.get('entity_type'))
    slug = entity.get('slug')
    if not endpoint or not slug:
        return None
    try:
        return c4w_url(endpoint, slug=slug)
    except Exception:
        return None


def c4w_entity_title(entity):
    """Display title: events and posts use ``title``, everything else ``name``."""
    if not entity:
        return u''
    return entity.get('name') or entity.get('title') or u''


def c4w_option_list(vocabulary):
    """``[{'term':…, 'label':…}]`` for a vocabulary, for building a select.

    Unknown or free vocabularies return an empty list -- the caller renders a
    text input for those, not a dropdown.
    """
    # Checked in both registries: geographic_extent is many-valued on a
    # project and single-valued on a platform, so it lives in VOCABULARIES yet
    # is filtered as a column.
    pairs = (constants.VOCABULARIES.get(vocabulary)
             or constants.COLUMN_VOCABULARIES.get(vocabulary)
             or ())
    return [{'term': term, 'label': label} for term, label in pairs]


def c4w_term_label(vocabulary, term, stored=None):
    """Display label for a stored term.

    Three sources, in order: the closed vocabulary in constants.py, the label
    recorded on the link row at import time, and finally the slug itself.

    The middle one matters more than it looks. A closed vocabulary can always
    be resolved from constants, but ``keyword``, ``funding_body`` and
    ``author`` are open -- the stored label is the only record of the string
    the author typed. And ``country`` needs its own path entirely, or a detail
    page shows 'ca' while its own sidebar says 'Canada'.
    """
    if vocabulary == 'country':
        return c4w_country_name(term)
    if vocabulary == 'language':
        return c4w_language_name(term)
    resolved = constants.label_for(vocabulary, term)
    if resolved != term:
        return resolved
    return stored or term


def c4w_language_name(code):
    """Language name for an ISO 639-1 code, in the active locale.

    Same reasoning as c4w_country_name: Babel ships with CKAN and carries the
    CLDR language names, so the table is not duplicated here and arrives
    translated.
    """
    if not code:
        return u''
    code = u'%s' % code
    try:
        from babel import Locale
        locale = Locale.parse(tk.request.environ.get('CKAN_LANG') or 'en')
        return locale.languages.get(code.lower()) or code
    except Exception:
        return code


def c4w_terms(entity, vocabulary):
    """``[{'term','label'}]`` for one of an entity's vocabularies.

    Templates use this instead of walking ``entity.terms`` so the label
    recorded at import time is never lost on the way to the page.
    """
    if not entity:
        return []
    items = (entity.get('term_labels') or {}).get(vocabulary)
    if items:
        return [{'term': i['term'],
                 'label': c4w_term_label(vocabulary, i['term'], i.get('label'))}
                for i in items]
    return [{'term': t, 'label': c4w_term_label(vocabulary, t)}
            for t in (entity.get('terms') or {}).get(vocabulary, [])]


def c4w_form_steps():
    """The six stages of the project form, from the single definition."""
    return constants.PROJECT_FORM_STEPS


def c4w_dataset_form_steps():
    """The five stages of the data wizard, from the single definition."""
    return constants.DATASET_FORM_STEPS


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
        # url_for reads a leading underscore as a routing directive, so
        # forwarding ?_scheme=x or ?_method=PUT was a 500 on every listing.
        if key.startswith('_'):
            continue
        flat[key] = values[0] if len(values) == 1 else values
    return c4w_url(endpoint, **flat)


def c4w_page_url(page):
    """The current URL with the page number changed, keeping every filter.

    Built from ``to_dict(flat=False)``: the flat form keeps only the FIRST
    value of a repeated key, so a visitor who had selected two topics would
    have found page 2 filtered by one of them -- a different result set, with
    nothing on the page to say so.

    Undeclared keys are dropped rather than forwarded. url_for treats a
    leading underscore as a routing directive, so ``?_scheme=x`` splatted
    into it was a 500 on every listing.
    """
    from flask import request

    args = {}
    for key, values in request.args.to_dict(flat=False).items():
        if key.startswith('_') or key == 'page':
            continue
        kept = [v for v in values if v]
        if kept:
            args[key] = kept[0] if len(kept) == 1 else kept
    args['page'] = page
    endpoint = (request.endpoint or '').split('.')[-1]
    return c4w_url(endpoint, **args)


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


_DASHBOARD_STAMP = None


def c4w_dashboard_asset(name):
    """URL of a built dashboard asset, with a cache-busting build stamp.

    The stamp comes from BUILD.json written by ``npm run build``; without it
    (a checkout that never built) the bare URL is returned and the page
    degrades to "dashboard not available".
    """
    global _DASHBOARD_STAMP
    if _DASHBOARD_STAMP is None:
        stamp = u''
        try:
            import json
            import os
            here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            path = os.path.join(here, 'public', 'c4w', 'dashboard',
                                'BUILD.json')
            with open(path, 'r', encoding='utf-8') as handle:
                info = json.load(handle)
            stamp = u'%s' % (info.get('builtAt') or info.get('version')
                             or u'')
        except Exception:
            stamp = u''
        _DASHBOARD_STAMP = u''.join(ch for ch in stamp if ch.isalnum())[:20]
    url = tk.h.url_for_static('/c4w/dashboard/%s' % name)
    if _DASHBOARD_STAMP:
        url += u'?v=' + _DASHBOARD_STAMP
    return url


def c4w_status_badge(status):
    """A status badge for a processing / moderation state, as markup."""
    status = u'%s' % (status or u'draft')
    label = constants.label_for('processing_status', status)
    return tk.h.literal(
        u'<span class="c4w-badge c4w-badge--%s">%s</span>'
        % (tk.h.escape(status), tk.h.escape(tk._(label))))


def c4w_number(value):
    """A thousands-separated integer, or an en dash when unknown."""
    if value in (None, u''):
        return u'\u2013'
    try:
        return u'{:,}'.format(int(value)).replace(u',', u'\u202f')
    except (TypeError, ValueError):
        return u'%s' % value


def c4w_bytes(value):
    """Human-readable file size."""
    try:
        size = float(value or 0)
    except (TypeError, ValueError):
        return u''
    for unit in (u'B', u'KB', u'MB', u'GB'):
        if size < 1024 or unit == u'GB':
            return (u'%d %s' % (size, unit) if unit == u'B'
                    else u'%.1f %s' % (size, unit))
        size /= 1024.0
    return u''


def c4w_license_title(license_id):
    """Title of a CKAN licence id, falling back to the id."""
    if not license_id:
        return u''
    try:
        for item in tk.get_action('license_list')({}, {}):
            if item.get('id') == license_id:
                return item.get('title') or license_id
    except Exception:
        pass
    return license_id


def c4w_license_options():
    """``[(id, title)]`` for the licences the wizard offers."""
    titles = {}
    try:
        for item in tk.get_action('license_list')({}, {}):
            titles[item.get('id')] = item.get('title') or item.get('id')
    except Exception:
        pass
    return [(lid, titles.get(lid, lid)) for lid in constants.DATASET_LICENSES]


def c4w_language_options():
    """``[(code, name)]`` for the metadata language select."""
    return [(code, c4w_language_name(code))
            for code in constants.DATASET_LANGUAGES]


def c4w_country_options():
    """Every ISO 3166-1 territory as ``[(code, name)]``, sorted by name."""
    try:
        from babel import Locale
        locale = Locale.parse(tk.request.environ.get('CKAN_LANG') or 'en')
        pairs = [(code, name) for code, name in locale.territories.items()
                 if len(code) == 2 and code.isalpha() and code.isupper()]
    except Exception:
        return []
    return sorted(pairs, key=lambda pair: pair[1].casefold())


def get_helpers():
    return {
        'c4w_dashboard_asset': c4w_dashboard_asset,
        'c4w_status_badge': c4w_status_badge,
        'c4w_number': c4w_number,
        'c4w_bytes': c4w_bytes,
        'c4w_license_title': c4w_license_title,
        'c4w_license_options': c4w_license_options,
        'c4w_language_options': c4w_language_options,
        'c4w_country_options': c4w_country_options,
        'c4w_version': lambda: __version__,
        'c4w_url': c4w_url,
        'c4w_nav': c4w_nav,
        'c4w_option_list': c4w_option_list,
        'c4w_term_label': c4w_term_label,
        'c4w_language_name': c4w_language_name,
        'c4w_terms': c4w_terms,
        'c4w_form_steps': c4w_form_steps,
        'c4w_dataset_form_steps': c4w_dataset_form_steps,
        'c4w_stats': c4w_stats,
        'c4w_country_name': c4w_country_name,
        'c4w_facet_toggle_url': c4w_facet_toggle_url,
        'c4w_page_url': c4w_page_url,
        'c4w_facet_active': c4w_facet_active,
        'c4w_any_facet_active': c4w_any_facet_active,
        'c4w_image_url': c4w_image_url,
        'c4w_login_url': c4w_login_url,
        'c4w_logout_url': c4w_logout_url,
        'c4w_register_url': c4w_register_url,
        'c4w_profile_url': c4w_profile_url,
        'c4w_is_sysadmin': c4w_is_sysadmin,
        'c4w_search_endpoint': c4w_search_endpoint,
        'c4w_detail_url': c4w_detail_url,
        'c4w_entity_title': c4w_entity_title,
    }

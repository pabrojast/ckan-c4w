# encoding: utf-8
"""Views for the platform, resource, event and news surfaces.

These five listings differ in their parameters and their template and in
nothing else, so the request handling lives here once. Projects and
organisations keep their own modules because their detail pages do real
extra work.
"""
from flask import redirect, request

import ckan.plugins.toolkit as tk

from ckanext.c4w import constants
from ckanext.c4w.logic import helpers as c4w_helpers


def _params(declared):
    """Pull declared parameters out of the query string.

    Only declared names are forwarded. An undeclared key is dropped here
    rather than passed on -- the action validates facet names against its
    spec, and this keeps an arbitrary key from reaching it at all.
    """
    out = {}
    for name, multi in declared:
        if multi:
            values = [v for v in request.args.getlist(name) if v]
            if values:
                out[name] = values
        else:
            value = request.args.get(name)
            if value:
                out[name] = value
    return out


_COMMON = (('q', False), ('order', False), ('page', False))

SURFACES = {
    'platform': {
        'list_action': 'c4w_platform_list',
        'show_action': 'c4w_platform_show',
        'list_template': 'c4w/platform_list.html',
        'detail_template': 'c4w/platform_detail.html',
        'detail_endpoint': 'platform_detail',
        'params': _COMMON + (('geographic_extent', True), ('country', True)),
        'orderings': constants.DEFAULT_ORDERINGS,
        'not_found': u'Platform not found',
    },
    'resource': {
        'list_action': 'c4w_resource_list',
        'show_action': 'c4w_resource_show',
        'list_template': 'c4w/resource_list.html',
        'detail_template': 'c4w/resource_detail.html',
        'detail_endpoint': 'resource_detail',
        'params': _COMMON + (('theme', True), ('audience', True),
                             ('in_language', True), ('category', True)),
        'orderings': constants.DEFAULT_ORDERINGS,
        'not_found': u'Resource not found',
    },
    'training_resource': {
        'list_action': 'c4w_training_resource_list',
        'show_action': 'c4w_resource_show',
        'list_template': 'c4w/training_resource_list.html',
        'detail_template': 'c4w/resource_detail.html',
        'detail_endpoint': 'resource_detail',
        'params': _COMMON + (('theme', True), ('audience', True),
                             ('in_language', True), ('category', True),
                             ('education_level', True),
                             ('learning_resource_type', True)),
        'orderings': constants.DEFAULT_ORDERINGS,
        'not_found': u'Training resource not found',
    },
    'event': {
        'list_action': 'c4w_event_list',
        'show_action': 'c4w_event_show',
        'list_template': 'c4w/event_list.html',
        'detail_template': 'c4w/event_detail.html',
        'detail_endpoint': 'event_detail',
        'params': _COMMON + (('event_type', True),),
        'orderings': None,
        'not_found': u'Event not found',
    },
    'post': {
        'list_action': 'c4w_post_list',
        'show_action': 'c4w_post_show',
        'list_template': 'c4w/post_list.html',
        'detail_template': 'c4w/post_detail.html',
        'detail_endpoint': 'post_detail',
        'params': _COMMON,
        'orderings': None,
        'not_found': u'Article not found',
    },
}

# The template variable each detail template expects. Resources and training
# resources share one template, hence one entry per surface rather than a
# guess from the name.
_DETAIL_VAR = {
    'platform': 'platform',
    'resource': 'resource',
    'training_resource': 'resource',
    'event': 'event',
    'post': 'post',
}


def listing(surface):
    config = SURFACES[surface]
    params = _params(config['params'])
    result = tk.get_action(config['list_action'])({}, params)
    return tk.render(config['list_template'], extra_vars={
        'listing': result,
        'orderings': config['orderings'],
        'params': params,
    })


def detail(surface, slug):
    config = SURFACES[surface]
    try:
        row = tk.get_action(config['show_action'])({}, {'id': slug})
    except tk.ObjectNotFound:
        return tk.abort(404, tk._(config['not_found']))
    return tk.render(config['detail_template'],
                     extra_vars={_DETAIL_VAR[surface]: row})


def legacy(surface, legacy_id):
    """Permanent redirect from the Django integer URL to the slug.

    301 rather than 302: the move is permanent, and every inbound link and
    search result still carries the old integer.
    """
    config = SURFACES[surface]
    try:
        row = tk.get_action(config['show_action'])({}, {'id': str(legacy_id)})
    except tk.ObjectNotFound:
        return tk.abort(404, tk._(config['not_found']))
    return redirect(
        c4w_helpers.c4w_url(config['detail_endpoint'], slug=row['slug']),
        code=301)


def redirect_to_post(slug):
    """301 from a dated blog permalink to the flat one."""
    try:
        post = tk.get_action('c4w_post_show')({}, {'id': slug})
    except tk.ObjectNotFound:
        return tk.abort(404, tk._('Article not found'))
    return redirect(
        c4w_helpers.c4w_url('post_detail', slug=post['slug']), code=301)

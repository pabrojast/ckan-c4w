# encoding: utf-8
"""Views for the public data catalogue: listing, detail, dashboard, bundle.

Orchestration only, like the other view modules. The bundle route is the
one that returns bytes: it streams a gzip blob the action already
visibility-checked, with the caching headers a same-origin fetch expects.
"""
import gzip
import re

from flask import Response, redirect, request

import ckan.plugins.toolkit as tk

from ckanext.c4w import constants
from ckanext.c4w.logic import helpers as c4w_helpers

_LISTING_PARAMS = (
    ('q', False), ('order', False), ('page', False),
    ('featured', False), ('grain', True), ('frequency', True),
    ('country', True), ('topic', True), ('water_type', True),
    ('water_data_type', True), ('technology_used', True),
)

_BUNDLE_NAME_RE = re.compile(r'^(?:(?:meta|sites|stats)\.json|p/\d{1,4}\.json)$')


def _listing_params():
    out = {}
    for name, multi in _LISTING_PARAMS:
        if multi:
            values = [v for v in request.args.getlist(name) if v]
            if values:
                out[name] = values
        else:
            value = request.args.get(name)
            if value:
                out[name] = value
    return out


def dataset_list():
    params = _listing_params()
    listing = tk.get_action('c4w_dataset_list')({}, params)
    return tk.render('c4w/dataset_list.html', extra_vars={
        'listing': listing,
        'orderings': constants.DATASET_ORDERINGS,
        'params': params,
    })


def _show(slug):
    try:
        return tk.get_action('c4w_dataset_show')({}, {'id': slug})
    except tk.ObjectNotFound:
        return tk.abort(404, tk._('Dataset not found'))


def dataset_detail(slug):
    dataset = _show(slug)
    try:
        tk.get_action('c4w_dataset_record_view')({}, {'id': dataset['id']})
    except Exception:
        pass
    return tk.render('c4w/dataset_detail.html', extra_vars={
        'dataset': dataset,
    })


def _dashboard_vars(dataset):
    return {
        'dataset': dataset,
        'bundle_base': c4w_helpers.c4w_url(
            'dataset_bundle', slug=dataset['slug'], name='meta.json')[
                :-len('meta.json')],
        'basemap_style': tk.config.get('ckanext.c4w.basemap_style')
        or 'https://tiles.openfreemap.org/styles/positron',
        'lang': tk.h.lang(),
    }


def dataset_dashboard(slug):
    dataset = _show(slug)
    return tk.render('c4w/dataset_dashboard.html',
                     extra_vars=_dashboard_vars(dataset))


def dataset_embed(slug):
    dataset = _show(slug)
    body = tk.render('c4w/dataset_embed.html',
                     extra_vars=_dashboard_vars(dataset))
    response = Response(body, mimetype='text/html')
    # Meant to be framed by other sites; the portal itself sets nothing
    # stricter, and the page carries no session-bound action.
    response.headers['X-Frame-Options'] = 'ALLOWALL'
    return response


def dataset_bundle(slug, name):
    if not _BUNDLE_NAME_RE.match(name or u''):
        return tk.abort(404, tk._('Not found'))
    try:
        blob = tk.get_action('c4w_dataset_bundle_show')(
            {}, {'id': slug, 'name': name})
    except tk.ObjectNotFound:
        return tk.abort(404, tk._('Not found'))
    except tk.ValidationError:
        return tk.abort(404, tk._('Not found'))

    etag = u'"%s"' % blob['etag']
    if request.headers.get('If-None-Match') == etag:
        response = Response(status=304)
    else:
        accepts_gzip = 'gzip' in (request.headers.get('Accept-Encoding')
                                  or u'').lower()
        if accepts_gzip:
            response = Response(blob['body'], mimetype=blob['content_type'])
            response.headers['Content-Encoding'] = 'gzip'
            response.headers['Content-Length'] = str(len(blob['body']))
        else:
            raw = gzip.decompress(blob['body'])
            response = Response(raw, mimetype=blob['content_type'])
            response.headers['Content-Length'] = str(len(raw))
    response.headers['ETag'] = etag
    response.headers['Vary'] = 'Accept-Encoding'
    response.headers['Cache-Control'] = (
        'public, max-age=300' if blob.get('public') else 'private, no-store')
    return response


def dataset_download(slug, file_id):
    dataset = _show(slug)
    wanted = None
    for item in dataset.get('files') or []:
        if item['id'] == file_id:
            wanted = item
            break
    if wanted is None or not wanted.get('url'):
        return tk.abort(404, tk._('File not found'))
    try:
        from ckan.model.meta import Session
        from ckanext.c4w import db
        db.ensure_mappers()
        row = Session.query(db.C4wDataset).filter(
            db.C4wDataset.id == dataset['id']).first()
        if row is not None:
            row.total_downloads = (row.total_downloads or 0) + 1
            Session.add(row)
            Session.commit()
    except Exception:
        pass
    # 302, not 301: the object-store URL may be re-issued.
    return redirect(wanted['url'], code=302)

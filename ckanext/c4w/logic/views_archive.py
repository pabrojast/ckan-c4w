"""Read-only GFQA endpoints; all requests retain C4W dataset visibility checks."""
import json
from flask import jsonify, request
import ckan.plugins.toolkit as tk

from ckanext.c4w.archive import service
from ckanext.c4w.archive.query import open_manifest
from ckanext.c4w.logic import helpers


def authorize(slug):
    if not service.enabled(slug):
        tk.abort(404)
    try:
        return tk.get_action('c4w_dataset_show')({}, {'id': slug})
    except (tk.ObjectNotFound, tk.NotAuthorized):
        tk.abort(404)


def response(payload, status=200):
    result = jsonify(payload)
    result.status_code = status
    result.headers['Cache-Control'] = 'private, no-store'
    result.headers['X-Content-Type-Options'] = 'nosniff'
    return result


def meta(slug):
    authorize(slug)
    try:
        _, manifest = open_manifest(service.root())
    except (OSError, ValueError):
        return response({'status': 'error', 'message': 'The GFQA archive is not ready.'}, 503)
    # No local filesystem paths are exposed by the public manifest.
    return response(manifest)


def query(slug):
    authorize(slug)
    if not request.is_json or (request.content_length or 0) > 8192:
        return response({'status': 'error', 'message': 'A JSON query under 8 KiB is required.'}, 400)
    try:
        raw = request.stream.read(8193)
        if len(raw) > 8192:
            raise ValueError('A JSON query under 8 KiB is required.')
        identifier, result = service.submit(json.loads(raw))
        result = dict(result, status_url=helpers.c4w_url('archive_job', slug=slug, job_id=identifier))
        return response(result, 202 if result['status'] == 'pending' else 400 if result['status'] == 'error' else 200)
    except ValueError as exc:
        return response({'status': 'error', 'message': str(exc)}, 400)
    except (OSError, KeyError):
        return response({'status': 'error', 'message': 'The GFQA archive is not ready.'}, 503)


def job(slug, job_id):
    authorize(slug)
    try:
        result = service.state(job_id)
    except ValueError:
        tk.abort(404)
    return response(result, 202 if result['status'] == 'pending' else 410 if result['status'] == 'expired' else 400 if result['status'] == 'error' else 200)

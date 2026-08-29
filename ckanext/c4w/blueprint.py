# encoding: utf-8
"""URL map for the Citizens4Water portal.

Two conventions, both deliberate.

**Views here are two-line wrappers.** Every one imports its ``logic.views_*``
module lazily and delegates. Orchestration lives in the view modules, business
logic in ``logic/action/*``, and nothing in this file touches the ORM. What
this file buys is a route map you can read top to bottom.

**Rules are registered with add_url_rule at the bottom**, not with decorators
scattered through the module, for the same reason.

The preview blueprint
---------------------
``get_blueprints`` returns a SECOND blueprint over the same view functions,
mounted at a different prefix, when ``ckanext.c4w.preview_prefix`` is set.
That is what lets the finished portal be QA'd in production while the Django
app still serves /citizens4water. It costs nothing when the option is unset,
and it is why every template must build links with ``h.c4w_url`` rather than
``h.url_for('c4w.…')`` -- the helper resolves against whichever blueprint is
serving the current request, so a preview page never links back out into the
live site.
"""
from flask import Blueprint

import ckan.plugins.toolkit as tk


# --------------------------------------------------------------------------- #
# Views
# --------------------------------------------------------------------------- #

def index():
    from ckanext.c4w.logic import views_home
    return views_home.index()


def project_list():
    from ckanext.c4w.logic import views_projects
    return views_projects.project_list()


def project_detail(slug):
    from ckanext.c4w.logic import views_projects
    return views_projects.project_detail(slug)


def project_geojson(slug):
    from ckanext.c4w.logic import views_projects
    return views_projects.project_geojson(slug)


def project_legacy(legacy_id):
    from ckanext.c4w.logic import views_projects
    return views_projects.project_legacy(legacy_id)


def organisation_list():
    from ckanext.c4w.logic import views_organisations
    return views_organisations.organisation_list()


def organisation_detail(slug):
    from ckanext.c4w.logic import views_organisations
    return views_organisations.organisation_detail(slug)


def organisation_legacy(legacy_id):
    from ckanext.c4w.logic import views_organisations
    return views_organisations.organisation_legacy(legacy_id)


# --------------------------------------------------------------------------- #
# Route registration
# --------------------------------------------------------------------------- #

def _register(bp):
    """Attach every rule to a blueprint.

    Called once per blueprint so the live and preview mounts can never drift.
    """
    bp.add_url_rule('/', 'index', index, methods=['GET'])

    # Projects. The int rule is registered BEFORE the slug rule: Flask's int
    # converter is strict so they cannot actually collide, but the order makes
    # the precedence obvious to a reader.
    bp.add_url_rule('/projects', 'project_list', project_list,
                    methods=['GET'])
    bp.add_url_rule('/project/<int:legacy_id>', 'project_legacy',
                    project_legacy, methods=['GET'])
    bp.add_url_rule('/project/<slug>', 'project_detail', project_detail,
                    methods=['GET'])
    bp.add_url_rule('/project/<slug>/geojson', 'project_geojson',
                    project_geojson, methods=['GET'])

    # Organisations.
    bp.add_url_rule('/organisations', 'organisation_list', organisation_list,
                    methods=['GET'])
    bp.add_url_rule('/organisation/<int:legacy_id>', 'organisation_legacy',
                    organisation_legacy, methods=['GET'])
    bp.add_url_rule('/organisation/<slug>', 'organisation_detail',
                    organisation_detail, methods=['GET'])
    return bp


def get_blueprints():
    blueprints = [_register(
        Blueprint('c4w', __name__, url_prefix='/citizens4water'))]

    # Optional parallel mount for pre-cutover QA. Absent by default.
    preview_prefix = tk.config.get('ckanext.c4w.preview_prefix')
    if preview_prefix:
        preview_prefix = '/' + str(preview_prefix).strip('/')
        if preview_prefix != '/citizens4water':
            blueprints.append(_register(
                Blueprint('c4w_preview', __name__,
                          url_prefix=preview_prefix)))
    return blueprints

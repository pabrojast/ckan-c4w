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


def about():
    from ckanext.c4w.logic import views_home
    return views_home.about()


def account():
    from ckanext.c4w.logic import views_account
    return views_account.account()


def submit():
    from ckanext.c4w.logic import views_account
    return views_account.submit()


def admin_index():
    from ckanext.c4w.logic import views_admin
    return views_admin.admin_index()


def admin_moderate(entity, item_id, operation):
    from ckanext.c4w.logic import views_admin
    return views_admin.admin_moderate(entity, item_id, operation)


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


def media_redirect(filename):
    from ckanext.c4w.logic import views_catalogue
    return views_catalogue.media_redirect(filename)


def organisation_list():
    from ckanext.c4w.logic import views_organisations
    return views_organisations.organisation_list()


def organisation_detail(slug):
    from ckanext.c4w.logic import views_organisations
    return views_organisations.organisation_detail(slug)


def organisation_legacy(legacy_id):
    from ckanext.c4w.logic import views_organisations
    return views_organisations.organisation_legacy(legacy_id)


# The remaining five surfaces share one view module; each rule binds its
# surface name so the blueprint stays a readable map rather than a dispatcher.
def _catalogue(fn, surface):
    def view(**kwargs):
        from ckanext.c4w.logic import views_catalogue
        return getattr(views_catalogue, fn)(surface, **kwargs)
    view.__name__ = str('%s_%s' % (surface, fn))
    return view


def post_legacy_dated(year, month, day, slug):
    """301 from the Django dated blog URL to the flat one.

    Django served /blog/<year>/<month>/<day>/<slug>/ and the date carried no
    information the slug does not -- blog_post.slug is already unique. The
    date segments are accepted and discarded.
    """
    from ckanext.c4w.logic import views_catalogue
    return views_catalogue.redirect_to_post(slug)


# Datasets: the public catalogue, the dashboard and its bundle.
def dataset_list():
    from ckanext.c4w.logic import views_datasets
    return views_datasets.dataset_list()


def dataset_detail(slug):
    from ckanext.c4w.logic import views_datasets
    return views_datasets.dataset_detail(slug)


def dataset_dashboard(slug):
    from ckanext.c4w.logic import views_datasets
    return views_datasets.dataset_dashboard(slug)


def dataset_embed(slug):
    from ckanext.c4w.logic import views_datasets
    return views_datasets.dataset_embed(slug)


def dataset_bundle(slug, name):
    from ckanext.c4w.logic import views_datasets
    return views_datasets.dataset_bundle(slug, name)


def dataset_download(slug, file_id):
    from ckanext.c4w.logic import views_datasets
    return views_datasets.dataset_download(slug, file_id)


# The data wizard.
def submit_data_start():
    from ckanext.c4w.logic import views_submit_data
    return views_submit_data.start()


def submit_data_step(dataset_id, step):
    from ckanext.c4w.logic import views_submit_data
    return views_submit_data.step(dataset_id, step)


def submit_data_file(dataset_id):
    from ckanext.c4w.logic import views_submit_data
    return views_submit_data.file(dataset_id)


def submit_data_process(dataset_id):
    from ckanext.c4w.logic import views_submit_data
    return views_submit_data.process(dataset_id)


def dataset_edit(slug):
    from ckanext.c4w.logic import views_submit_data
    return views_submit_data.edit(slug)


def dataset_delete(slug):
    from ckanext.c4w.logic import views_submit_data
    return views_submit_data.delete(slug)


# Registration, verification and login, in the portal's own chrome.
def register_choose():
    from ckanext.c4w.logic import views_register
    return views_register.choose()


def register_citizen():
    from ckanext.c4w.logic import views_register
    return views_register.register('citizen')


def register_manager():
    from ckanext.c4w.logic import views_register
    return views_register.register('manager')


def verify_email(token):
    from ckanext.c4w.logic import views_register
    return views_register.verify(token)


def verify_resend():
    from ckanext.c4w.logic import views_register
    return views_register.resend()


def login():
    from ckanext.c4w.logic import views_login
    return views_login.login()


def admin_manager(user_id, operation):
    from ckanext.c4w.logic import views_admin
    return views_admin.admin_manager(user_id, operation)


# --------------------------------------------------------------------------- #
# Route registration
# --------------------------------------------------------------------------- #

def _register(bp):
    """Attach every rule to a blueprint.

    Called once per blueprint so the live and preview mounts can never drift.
    """
    bp.add_url_rule('/', 'index', index, methods=['GET'])
    bp.add_url_rule('/about', 'about', about, methods=['GET'],
                    strict_slashes=False)
    bp.add_url_rule('/account', 'account', account, methods=['GET'],
                    strict_slashes=False)
    bp.add_url_rule('/submit', 'submit', submit, methods=['GET'],
                    strict_slashes=False)
    bp.add_url_rule('/admin', 'admin_index', admin_index, methods=['GET'],
                    strict_slashes=False)
    bp.add_url_rule(
        '/admin/managers/<user_id>/<operation>',
        'admin_manager', admin_manager, methods=['POST'])
    bp.add_url_rule(
        '/admin/<entity>/<item_id>/<operation>',
        'admin_moderate', admin_moderate, methods=['POST'])

    # Account: registration, verification, login. /verify/resend is a
    # static rule registered BEFORE /verify/<token> so Flask never reads
    # "resend" as a token.
    bp.add_url_rule('/register', 'register_choose', register_choose,
                    methods=['GET'], strict_slashes=False)
    bp.add_url_rule('/register/citizen', 'register_citizen',
                    register_citizen, methods=['GET', 'POST'])
    bp.add_url_rule('/register/manager', 'register_manager',
                    register_manager, methods=['GET', 'POST'])
    bp.add_url_rule('/verify/resend', 'verify_resend', verify_resend,
                    methods=['GET', 'POST'])
    bp.add_url_rule('/verify/<token>', 'verify_email', verify_email,
                    methods=['GET'])
    bp.add_url_rule('/login', 'login', login, methods=['GET', 'POST'],
                    strict_slashes=False)

    # Data: the wizard first (static prefix), then the public catalogue.
    bp.add_url_rule('/submit/data', 'submit_data_start', submit_data_start,
                    methods=['GET', 'POST'], strict_slashes=False)
    bp.add_url_rule('/submit/data/<dataset_id>/file', 'submit_data_file',
                    submit_data_file, methods=['POST'])
    bp.add_url_rule('/submit/data/<dataset_id>/process',
                    'submit_data_process', submit_data_process,
                    methods=['POST'])
    bp.add_url_rule('/submit/data/<dataset_id>/<int:step>',
                    'submit_data_step', submit_data_step,
                    methods=['GET', 'POST'])
    bp.add_url_rule('/data', 'dataset_list', dataset_list, methods=['GET'],
                    strict_slashes=False)
    bp.add_url_rule('/data/<slug>', 'dataset_detail', dataset_detail,
                    methods=['GET'], strict_slashes=False)
    bp.add_url_rule('/data/<slug>/dashboard', 'dataset_dashboard',
                    dataset_dashboard, methods=['GET'], strict_slashes=False)
    bp.add_url_rule('/data/<slug>/embed', 'dataset_embed', dataset_embed,
                    methods=['GET'], strict_slashes=False)
    bp.add_url_rule('/data/<slug>/bundle/<path:name>', 'dataset_bundle',
                    dataset_bundle, methods=['GET'])
    bp.add_url_rule('/data/<slug>/download/<file_id>', 'dataset_download',
                    dataset_download, methods=['GET'])
    bp.add_url_rule('/data/<slug>/edit', 'dataset_edit', dataset_edit,
                    methods=['GET'])
    bp.add_url_rule('/data/<slug>/delete', 'dataset_delete', dataset_delete,
                    methods=['POST'])

    # Legacy media. Load-bearing: every <img src> inside a migrated blog body
    # still points at this shape, as does every inbound link to an old image.
    bp.add_url_rule('/media/<path:filename>', 'media_redirect',
                    media_redirect, methods=['GET'])

    # Projects. The int rule is registered BEFORE the slug rule: Flask's int
    # converter is strict so they cannot actually collide, but the order makes
    # the precedence obvious to a reader.
    bp.add_url_rule('/projects', 'project_list', project_list,
                    methods=['GET'], strict_slashes=False)
    bp.add_url_rule('/project/<int:legacy_id>', 'project_legacy',
                    project_legacy, methods=['GET'])
    bp.add_url_rule('/project/<slug>', 'project_detail', project_detail,
                    methods=['GET'])
    bp.add_url_rule('/project/<slug>/geojson', 'project_geojson',
                    project_geojson, methods=['GET'])

    # Organisations.
    bp.add_url_rule('/organisations', 'organisation_list', organisation_list,
                    methods=['GET'], strict_slashes=False)
    bp.add_url_rule('/organisation/<int:legacy_id>', 'organisation_legacy',
                    organisation_legacy, methods=['GET'])
    bp.add_url_rule('/organisation/<slug>', 'organisation_detail',
                    organisation_detail, methods=['GET'])

    # Platforms, resources, training resources, events and news.
    for surface, list_path, item_path, list_endpoint, detail_endpoint in (
            ('platform', '/platforms', '/platform',
             'platform_list', 'platform_detail'),
            ('resource', '/resources', '/resource',
             'resource_list', 'resource_detail'),
            ('training_resource', '/training_resources', None,
             'training_resource_list', None),
            ('event', '/events', '/event', 'event_list', 'event_detail'),
            ('post', '/blog', '/blog', 'post_list', 'post_detail')):
        # Django served /events/ and /platforms/ WITH a trailing slash, and
        # /resources and /blog without. strict_slashes=False accepts both, so
        # no live URL 404s after the cutover.
        bp.add_url_rule(list_path, list_endpoint,
                        _catalogue('listing', surface), methods=['GET'],
                        strict_slashes=False)
        if not item_path:
            # Training resources share the resource detail URL: they are the
            # same rows, so giving them a second address would split the
            # inbound links to one document across two canonical URLs.
            continue
        # Registered for every surface, including the blog where item_path
        # equals list_path. Without it, get_by_reference's legacy_id fallback
        # made /blog/7 resolve 200 as a SECOND uncanonicalised address for the
        # same post. Werkzeug prefers the int rule over <slug> for a numeric
        # path, so this turns that duplicate into the 301 it should be.
        bp.add_url_rule('%s/<int:legacy_id>' % item_path,
                        '%s_legacy' % surface,
                        _catalogue('legacy', surface), methods=['GET'])
        bp.add_url_rule('%s/<slug>' % item_path, detail_endpoint,
                        _catalogue('detail', surface), methods=['GET'],
                        strict_slashes=False)

    # The dated blog permalink Django published.
    bp.add_url_rule(
        '/blog/<int:year>/<int:month>/<int:day>/<slug>',
        'post_legacy_dated', post_legacy_dated, methods=['GET'],
        strict_slashes=False)
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

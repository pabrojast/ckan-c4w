# encoding: utf-8
"""Render the real C4W templates with stub helpers, for visual review.

    python3 scripts/render_preview.py [--out build/preview]
    python3 -m http.server 8765 --bind 127.0.0.1 --directory build/preview

Writes static pages that link the real c4w.css and the real public assets,
so the stylesheet can be iterated on and screenshotted (e.g. with
``google-chrome --headless=new --screenshot``) without a CKAN site. The
helpers are stubs with sample data; anything that needs the database is
faked here, so this is a design tool, not a test.

Needs only jinja2 and markupsafe (both ship with CKAN).
"""
import argparse
import os
import shutil
import sys
from types import SimpleNamespace

from jinja2 import Environment, FileSystemLoader, ChoiceLoader, DictLoader, nodes
from jinja2.ext import Extension
from markupsafe import Markup

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TPL = os.path.join(REPO, 'ckanext', 'c4w', 'templates')
PUBLIC = os.path.join(REPO, 'ckanext', 'c4w', 'public', 'c4w')
CSS = os.path.join(REPO, 'ckanext', 'c4w', 'assets', 'css', 'c4w.css')
_args = argparse.ArgumentParser(description=__doc__.split('\n')[0])
_args.add_argument('--out', default=os.path.join(REPO, 'build', 'preview'))
OUT = _args.parse_args().out
os.makedirs(OUT, exist_ok=True)
if os.path.isdir(os.path.join(OUT, 'c4w')):
    shutil.rmtree(os.path.join(OUT, 'c4w'))
shutil.copytree(PUBLIC, os.path.join(OUT, 'c4w'))
shutil.copy(CSS, os.path.join(OUT, 'c4w.css'))
sys.path.insert(0, REPO)
from ckanext.c4w import constants  # noqa: E402

PAGE = '''<!DOCTYPE html>
{% block htmltag %}<html lang="en">{% endblock %}
<head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{% block title %}{% endblock %}</title>
<link rel="stylesheet" href="/c4w.css">
{% block styles %}{% endblock %}
</head>
<body>
{% block skip %}{% endblock %}
{% block header %}{% endblock %}
{% block content %}{% endblock %}
{% block footer %}{% endblock %}
{% block scripts %}{% endblock %}
</body></html>'''


class SnippetExtension(Extension):
    tags = {'snippet'}

    def parse(self, parser):
        lineno = next(parser.stream).lineno
        args = [parser.parse_expression()]
        kwargs = []
        while parser.stream.current.type != 'block_end':
            if parser.stream.current.type == 'comma':
                next(parser.stream)
                continue
            name = parser.stream.expect('name').value
            parser.stream.expect('assign')
            kwargs.append(nodes.Keyword(name, parser.parse_expression()))
        return nodes.Output([self.call_method('_render', args, kwargs)]).set_lineno(lineno)

    def _render(self, path, **kw):
        ctx = dict(BASE_CTX)
        ctx.update(kw)
        return Markup(self.environment.get_template(path).render(**ctx))


class SkipTag(Extension):
    tags = {'asset', 'resource', 'ckan_extends'}

    def parse(self, parser):
        tag = next(parser.stream)
        while parser.stream.current.type != 'block_end':
            next(parser.stream)
        return nodes.Output([nodes.Const('')]).set_lineno(tag.lineno)


env = Environment(
    loader=ChoiceLoader([DictLoader({'page.html': PAGE}), FileSystemLoader(TPL)]),
    extensions=[SnippetExtension, SkipTag, 'jinja2.ext.i18n', 'jinja2.ext.do'],
    autoescape=True,
)
env.install_null_translations(newstyle=True)

# --- sample data ------------------------------------------------------------ #

HERO = '/c4w/img/hero-pc.webp'
TERMS = lambda **kw: {k: [{'term': t, 'label': l} for t, l in v] for k, v in kw.items()}  # noqa: E731


def project(i, image=True, **kw):
    row = {
        'id': 'p%d' % i, 'slug': 'project-%d' % i, 'entity_type': 'project',
        'name': ['Riverwatch Flanders', 'Lake Guardians Chile', 'Mekong Sentinels',
                 'Andean Springs Monitoring', 'Danube Plastic Watch', 'Ganga Prahari'][i % 6],
        'status': ['active', 'periodically-active', 'completed'][i % 3],
        'description': '<p>Volunteers measure nitrate, phosphate and turbidity at fixed sites every month and share the readings with the river authority. The project trains schools and angling clubs.</p>',
        'image1_url': HERO if image else None, 'image3_url': HERO if image else None,
        'image3_credit': 'Photo: UNESCO IHP', 'start_date': '2021-03-01', 'locality': 'Leuven region',
        'total_accesses': 1240 + i, 'url': 'https://example.org', 'data_url': 'https://example.org/data',
        'aim': '<p>Give citizens a role in the surveillance of small rivers.</p>',
        'how_to_participate': '<p>Join a monthly sampling walk. A kit is lent to every group.</p>',
        'difficulty_level': 'easy', 'number_of_participants': '120', 'funding_programme': 'IHP-IX',
        'author': 'Marie Peeters', 'author_email': None,
        'term_labels': TERMS(country=[('be', 'Belgium'), ('nl', 'Netherlands')],
                             topic=[('water', 'Water'), ('biodiversity', 'Biodiversity'), ('education', 'Education')],
                             water_type=[('surface-water', 'Surface water')],
                             water_data_type=[('chemical-water-quality', 'Chemical water quality')],
                             participation_task=[('measurement', 'Measurement'), ('observation', 'Observation')],
                             keyword=[('nitrate', 'nitrate'), ('school', 'school monitoring')]),
        'main_organisation': {'name': 'Scivil', 'slug': 'scivil'}, 'organisations': [{'name': 'VMM', 'slug': 'vmm'}],
    }
    row['terms'] = {k: [t['term'] for t in v] for k, v in row['term_labels'].items()}
    row.update(kw)
    return row


def event(i):
    return {'id': 'e%d' % i, 'slug': 'event-%d' % i, 'title': ['Citizen Science Fair 2026', 'World Water Day sampling blitz'][i % 2],
            'start_date': ['2026-06-18', '2026-03-22'][i % 2], 'place': ['Brussels', 'Online'][i % 2],
            'event_type': ['face-to-face', 'online'][i % 2],
            'description': 'On June 18 we celebrate citizen science with the Citizen Science Fair, organised by Scivil, ECS and ECSA. A prelude to the cluster event.',
            'url': 'https://example.org'}


def post(i, image=False):
    return {'id': 'n%d' % i, 'slug': 'post-%d' % i, 'title': ['New dashboards for uploaded data', 'Ten years of FreshWater Watch'][i % 2],
            'created_on': '2026-08-2%d' % i, 'sticky': i == 0, 'image_url': HERO if image else None,
            'excerpt': 'Every dataset shared on Citizens4Water now comes with an interactive map and trend charts, built automatically from the uploaded table.'}


def organisation(i, logo=False):
    return {'id': 'o%d' % i, 'slug': 'org-%d' % i, 'name': ['Scivil', 'Earthwatch Europe', 'CAZALAC'][i % 3],
            'org_type': ['non-governmental', 'academic', 'intergovernmental'][i % 3], 'country': ['BE', 'GB', 'CL'][i % 3],
            'logo_url': '/c4w/img/Citizens4Water_Horizontal.svg' if logo else None,
            'description': 'The Flemish knowledge centre for citizen science, coordinating Citizens4Water.'}


DATASET = {
    'id': 'd1', 'slug': 'nitrate-maipo', 'title': 'Nitrate and phosphate in the Maipo river, 2021–2024',
    'entity_type': 'dataset', 'approved': True, 'hidden': False, 'can_edit': True, 'bundle_ready': True,
    'processing_status': 'ready', 'publisher': 'Universidad de Chile', 'temporal_start': '2021-04-02',
    'temporal_end': '2024-11-30', 'total_accesses': 88, 'record_count': 4210, 'site_count': 18,
    'parameter_count': 3, 'grain': 'month', 'description': '<p>Monthly samples taken by school groups along the Maipo river, analysed with field kits and cross-checked against the regional laboratory.</p>',
    'provenance': '<p>Samples were taken at 18 fixed sites with a Hach nitrate strip and a Secchi tube. Each reading is the median of three strips.</p>',
    'data_files': [{'id': 'f1', 'original_name': 'maipo-2021-2024.csv', 'size_bytes': 1834000}],
    'attachments': [{'id': 'f2', 'original_name': 'protocol-v3.pdf', 'size_bytes': 402000}],
    'project': {'name': 'Lake Guardians Chile', 'slug': 'project-1'}, 'organisation': None,
    'license_id': 'cc-by', 'frequency': 'monthly', 'language': 'es', 'source_url': None, 'doi': '10.1234/maipo.2024',
    'citation': None, 'author': 'Colegio San Ignacio', 'attribution_text': 'Funded by the Chilean Ministry of Science.',
    'related_urls': [], 'contact_name': 'Ana Rojas', 'contact_email': 'ana@example.org', 'processed_at': '2026-09-04T12:00:00',
    'term_labels': TERMS(country=[('cl', 'Chile')], water_data_type=[('chemical-water-quality', 'Chemical water quality')],
                         water_type=[('surface-water', 'Surface water')], topic=[('water', 'Water')],
                         technology_used=[('conventional-methods-manual-measurements', 'Conventional methods / manual measurements')],
                         keyword=[('nitrate', 'nitrate'), ('phosphate', 'phosphate')]),
    'mapping': {'parameters': [{'include': True}, {'include': True}, {'include': False}]},
    'processing_summary': {'rejected': 12, 'dropped': 1}, 'submitted_at': '2026-09-01', 'wizard_step': 6,
}
DATASET['terms'] = {k: [t['term'] for t in v] for k, v in DATASET['term_labels'].items()}

NAV = [{'endpoint': e, 'url': '/%s.html' % e, 'label': l, 'stat': s, 'active': e == 'project_list'}
       for e, l, s in (('project_list', 'Projects', 'projects'), ('dataset_list', 'Data', 'datasets'),
                       ('resource_list', 'Resources', 'resources'), ('training_resource_list', 'Training', 'training_resources'),
                       ('organisation_list', 'Organisations', 'organisations'), ('platform_list', 'Platforms', 'platforms'),
                       ('event_list', 'Events', 'events'), ('post_list', 'News', 'posts'))]
STATS = {'projects': 42, 'datasets': 7, 'resources': 118, 'training_resources': 23, 'organisations': 61,
         'platforms': 9, 'events': 14, 'posts': 31}
LABELS = {}
for name in list(constants.VOCABULARIES) + list(constants.COLUMN_VOCABULARIES):
    for term, label in (constants.VOCABULARIES.get(name) or constants.COLUMN_VOCABULARIES.get(name)):
        LABELS[(name, term)] = label


def c4w_terms(entity, vocabulary):
    items = (entity.get('term_labels') or {}).get(vocabulary) or []
    return [{'term': i['term'], 'label': i.get('label') or i['term']} for i in items]


H = SimpleNamespace(
    c4w_url=lambda endpoint, **kw: '/%s.html' % endpoint,
    c4w_nav=lambda: NAV,
    c4w_stats=lambda: STATS,
    c4w_terms=c4w_terms,
    c4w_term_label=lambda v, t, stored=None: LABELS.get((v, t), stored or t),
    c4w_image_url=lambda e, field='image1_url': (e or {}).get(field),
    c4w_country_name=lambda code: {'BE': 'Belgium', 'GB': 'United Kingdom', 'CL': 'Chile', 'be': 'Belgium', 'nl': 'Netherlands', 'cl': 'Chile'}.get(code, code),
    c4w_language_name=lambda c: {'es': 'Spanish', 'en': 'English'}.get(c, c),
    c4w_option_list=lambda name: [{'term': t, 'label': l} for t, l in (constants.VOCABULARIES.get(name) or constants.COLUMN_VOCABULARIES.get(name) or ())],
    url_for_static=lambda p: p,
    url_for=lambda *a, **k: '#',
    csrf_input=lambda: Markup('<input type="hidden" name="_csrf_token" value="x">'),
    get_available_locales=lambda: [SimpleNamespace(short_name='en'), SimpleNamespace(short_name='es'), SimpleNamespace(short_name='fr')],
    current_url=lambda: '/',
    c4w_login_url=lambda *a, **k: '/login.html',
    c4w_register_url=lambda: '/register_choose.html',
    c4w_logout_url=lambda: '#',
    c4w_profile_url=lambda: '#',
    c4w_search_endpoint=lambda: 'project_list',
    c4w_is_sysadmin=lambda: True,
    c4w_number=lambda v: '–' if v in (None, '') else '{:,}'.format(int(v)).replace(',', ' '),
    c4w_bytes=lambda v: '%.1f MB' % (v / 1048576.0) if v and v > 1048576 else '%d KB' % ((v or 0) // 1024),
    c4w_entity_icon=lambda t: {'projects': 'drop', 'project': 'drop', 'datasets': 'database', 'dataset': 'database', 'resources': 'book', 'resource': 'book', 'training_resources': 'school', 'training_resource': 'school', 'organisations': 'building', 'organisation': 'building', 'platforms': 'layers', 'platform': 'layers', 'events': 'calendar', 'event': 'calendar', 'posts': 'newspaper', 'post': 'newspaper'}.get(t, 'drop'),
    c4w_avatar_initial=lambda: 'A',
    c4w_month_name=lambda m: ['', 'Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'][int(m)],
    c4w_status_badge=lambda s: Markup('<span class="c4w-badge c4w-badge--%s">%s</span>' % (s, LABELS.get(('processing_status', s), s))),
    c4w_facet_active=lambda name, value: name == 'water_type' and value == 'surface-water',
    c4w_facet_toggle_url=lambda name, value: '#',
    c4w_page_url=lambda page: '#',
    c4w_dataset_form_steps=lambda: constants.DATASET_FORM_STEPS,
    c4w_license_options=lambda: [('cc-by', 'Creative Commons Attribution'), ('cc-by-sa', 'Creative Commons Attribution Share-Alike'), ('cc-zero', 'Creative Commons CCZero')],
    c4w_language_options=lambda: [('en', 'English'), ('es', 'Spanish'), ('fr', 'French')],
    c4w_country_options=lambda: [('BE', 'Belgium'), ('CL', 'Chile'), ('GB', 'United Kingdom')],
    c4w_license_title=lambda lid: 'Creative Commons Attribution',
    c4w_dashboard_asset=lambda name: '/c4w/dashboard/' + name,
    c4w_detail_url=lambda item: '#',
    c4w_entity_title=lambda item: item.get('name') or item.get('title'),
    get_flashed_messages=lambda with_categories=False: [],
    literal=Markup,
    lang=lambda: 'en',
)

ANON = SimpleNamespace(userobj=None, user=None)
USER = SimpleNamespace(userobj=SimpleNamespace(display_name='Ana Rojas', fullname='Ana Rojas', name='ana'), user='ana')
REQUEST = SimpleNamespace(args={}, environ={'CKAN_LANG': 'en'}, full_path='/', path='/')

BASE_CTX = {'h': H, 'c': ANON, 'g': ANON, 'request': REQUEST, 'ungettext': lambda s, p, n: (s if n == 1 else p),
            'today': '2026-09-04'}


def render(name, out, **ctx):
    full = dict(BASE_CTX)
    full.update(ctx)
    html = env.get_template(name).render(**full)
    with open(os.path.join(OUT, out), 'w', encoding='utf-8') as fh:
        fh.write(html)
    print('wrote', out, len(html))


LISTING = {'results': [project(0), project(1, image=False), project(2), project(3, image=False), project(4), project(5, image=False)],
           'count': 42, 'page': 1, 'pages': 3, 'order': 'modified',
           'facets': {'country': {'be': 12, 'cl': 9, 'nl': 4}, 'status': {'active': 30, 'completed': 12},
                      'topic': {'water': 40, 'biodiversity': 12, 'education': 8}, 'water_type': {'surface-water': 35, 'groundwater': 7},
                      'water_data_type': {'chemical-water-quality': 20, 'biological-water-quality': 11}}}

render('c4w/home.html', 'index.html', stats=STATS, featured_project=project(0), latest_projects=[project(1, image=False), project(2), project(3, image=False)],
       latest_posts=[post(0), post(1, image=True)], upcoming_events=[event(0), event(1)])
render('c4w/project_list.html', 'project_list.html', listing=LISTING, orderings=constants.PROJECT_ORDERINGS, params={'q': ''})
render('c4w/project_detail.html', 'project_detail.html', project=project(0))
render('c4w/dataset_detail.html', 'dataset_detail.html', dataset=DATASET, c=USER)
render('c4w/organisation_list.html', 'organisation_list.html', listing={'results': [organisation(0, logo=True), organisation(1), organisation(2)], 'count': 61, 'page': 1, 'pages': 1, 'order': 'name', 'facets': {'org_type': {'academic': 20, 'non-governmental': 30}, 'country': {'BE': 12, 'CL': 4}}}, orderings=constants.DEFAULT_ORDERINGS, params={})
render('c4w/dataset_step_1.html', 'wizard1.html', step=constants.DATASET_FORM_STEPS[0], step_number=1, dataset=None, data={'language': 'en'}, errors={}, reachable=1, is_new=True, project_options=[('p1', 'Lake Guardians Chile')], organisation_options=[('o1', 'Scivil')], c=USER)
render('c4w/dataset_step_5.html', 'wizard5.html', step=constants.DATASET_FORM_STEPS[4], step_number=5, dataset=DATASET, data={'contact_name': 'Ana Rojas', 'contact_email': 'ana@example.org', 'terms_accepted': True}, errors={'contact_url': ['Not a web address']}, reachable=5, is_new=False, missing=[], missing_by_step=[], steps=constants.DATASET_FORM_STEPS, c=USER)
render('c4w/login.html', 'login.html', data={}, errors={}, came_from='/')
render('c4w/register_choose.html', 'register_choose.html', user=None)
render('c4w/admin.html', 'admin.html', groups=[{'entity_type': 'dataset', 'rows': [dict(DATASET, approved=False, mapping_json='{}')], 'can_hide': True, 'can_feature': True, 'can_process': True}], managers=[{'user_id': 'u2', 'fullname': 'Pedro Silva', 'name': 'pedro', 'email': 'pedro@example.org', 'job_title': 'Coordinator', 'country': 'CL', 'org_choice': 'new', 'org_name_requested': 'Río Vivo', 'org_type': 'non-governmental', 'org_url': 'https://riovivo.cl', 'created': '2026-09-02'}], c=USER)
render('c4w/post_list.html', 'post_list_empty.html', listing={'results': [], 'count': 0, 'page': 1, 'pages': 1, 'order': 'modified', 'facets': {}}, orderings=None, params={})
render('c4w/event_list.html', 'event_list_single.html', listing={'upcoming': [event(0)], 'past': [event(1)], 'upcoming_total': 1, 'page': 1, 'pages': 1}, params={})
render('c4w/event_list.html', 'event_list.html', listing={'upcoming': [event(0), event(1)], 'past': [event(0)], 'upcoming_total': 2, 'page': 1, 'pages': 1}, params={})

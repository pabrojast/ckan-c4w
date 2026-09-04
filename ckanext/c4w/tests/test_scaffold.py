# encoding: utf-8
"""Structural checks that need neither CKAN nor a database.

These assert the things that are invisible until a visitor hits them: an
entry point that does not resolve, a template override that silently stops
overriding, a vocabulary term that is not a slug, a form step naming a field
no input renders.

Deliberately stdlib-only (``ast`` + ``pathlib``) so they also run in a bare
checkout, before anyone has a CKAN environment.
"""
import ast
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
PKG = ROOT / 'ckanext' / 'c4w'


def _read(*parts):
    return (PKG.joinpath(*parts)).read_text(encoding='utf-8')


# --------------------------------------------------------------------------- #
# Packaging
# --------------------------------------------------------------------------- #

def test_entry_point_names_the_plugin_class():
    source = (ROOT / 'setup.py').read_text(encoding='utf-8')
    assert 'c4w=ckanext.c4w.plugin:C4wPlugin' in source
    # Without this, `pip install -e .` cannot resolve sibling ckanext.*
    # distributions and the plugin will not import.
    assert "namespace_packages=['ckanext']" in source


def test_namespace_package_uses_declare_namespace():
    source = (ROOT / 'ckanext' / '__init__.py').read_text(encoding='utf-8')
    assert 'declare_namespace' in source


def test_plugin_implements_every_interface_it_needs():
    tree = ast.parse(_read('plugin.py'))
    implemented = {
        node.args[0].attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and getattr(node.func, 'attr', None) == 'implements'
        and node.args and isinstance(node.args[0], ast.Attribute)
    }
    assert implemented == {
        'IConfigurer', 'IConfigurable', 'IBlueprint', 'IActions',
        'IAuthFunctions', 'IValidators', 'ITemplateHelpers', 'IClick',
    }


# --------------------------------------------------------------------------- #
# Templates
# --------------------------------------------------------------------------- #

def test_no_ihp_wins_header_override():
    """C4W is its own portal. It must not inject a tab into the IHP-WINS
    masthead -- a root-level header.html would do that for the WHOLE site.
    """
    assert not (PKG / 'templates' / 'header.html').exists()


def test_every_template_lives_under_the_c4w_namespace_or_is_an_override():
    """Only deliberate overrides may sit at the template root.

    A stray file at the root silently overrides a core CKAN template of the
    same name for the WHOLE portal, not just this section.
    """
    root_templates = {
        p.name for p in (PKG / 'templates').glob('*.html')
    }
    assert root_templates == set()


# --------------------------------------------------------------------------- #
# Database
# --------------------------------------------------------------------------- #

def _table_names():
    tree = ast.parse(_read('db.py'))
    return [
        node.args[0].value
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and getattr(node.func, 'id', None) == 'Table'
        and node.args and isinstance(node.args[0], ast.Constant)
    ]


def test_all_tables_are_namespaced():
    """Every table we own must be prefixed, so it is obvious whose it is."""
    names = _table_names()
    assert names, 'no Table() definitions found in db.py'
    assert all(n.startswith('c4w_') for n in names), names


def test_table_count_matches_the_registry():
    """_ALL_TABLES drives create_all and the index auto-heal.

    A table defined but left out of that list is never created on a fresh
    install and never gets its indexes on an existing one.
    """
    tree = ast.parse(_read('db.py'))
    registry = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.Assign)
        and any(getattr(t, 'id', None) == '_ALL_TABLES' for t in node.targets)
    ]
    assert len(registry) == 1
    listed = {e.id for e in registry[0].value.elts}
    assert len(listed) == len(_table_names())


def test_entity_classes_cover_the_declared_entity_types():
    """constants.ENTITY_TYPES and db.ENTITY_CLASSES must agree.

    ENTITY_TYPES validates the <entity> path segment of the moderation routes;
    ENTITY_CLASSES is what resolves it to a table. A name in one and not the
    other is either a 404 on a real entity or a KeyError on a valid route.
    """
    from ckanext.c4w import constants

    tree = ast.parse(_read('db.py'))
    mapping = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.Assign)
        and any(getattr(t, 'id', None) == 'ENTITY_CLASSES' for t in node.targets)
    ]
    assert len(mapping) == 1
    keys = {k.value for k in mapping[0].value.keys}
    assert keys == set(constants.ENTITY_TYPES)


# --------------------------------------------------------------------------- #
# Vocabularies and the project form
# --------------------------------------------------------------------------- #

SLUG_RE = re.compile(r'^[a-z0-9]+(?:-[a-z0-9]+)*$')


def test_every_closed_vocabulary_term_is_a_slug():
    """Terms are stored values and appear in URLs; labels carry the prose."""
    from ckanext.c4w import constants

    offenders = []
    for name, pairs in list(constants.VOCABULARIES.items()) + \
            list(constants.COLUMN_VOCABULARIES.items()):
        for term, _label in pairs:
            if not SLUG_RE.match(term):
                offenders.append('%s:%s' % (name, term))
    assert not offenders, offenders


def test_terms_are_unique_within_a_vocabulary():
    from ckanext.c4w import constants

    for name, pairs in list(constants.VOCABULARIES.items()) + \
            list(constants.COLUMN_VOCABULARIES.items()):
        terms = [t for t, _ in pairs]
        assert len(terms) == len(set(terms)), name


def test_free_and_closed_vocabularies_do_not_overlap():
    """A vocabulary is either validated against a list or it is not."""
    from ckanext.c4w import constants

    assert not (set(constants.FREE_VOCABULARIES) & set(constants.VOCABULARIES))


def test_project_form_steps_are_numbered_and_disjoint():
    from ckanext.c4w import constants

    steps = constants.PROJECT_FORM_STEPS
    assert [s['step'] for s in steps] == list(range(1, len(steps) + 1))

    seen = [f for s in steps for f in s['fields']]
    duplicates = sorted({f for f in seen if seen.count(f) > 1})
    assert not duplicates, duplicates

    assert all(s['key'] and s['title'] and s['fields'] for s in steps)


def test_project_image_boxes_cover_the_form_image_fields():
    """Each image input needs a target box, or the server cannot fit it."""
    from ckanext.c4w import constants

    fields = {f for s in constants.PROJECT_FORM_STEPS for f in s['fields']}
    for name in constants.PROJECT_IMAGE_BOXES:
        assert name in fields, name


# --------------------------------------------------------------------------- #
# Portal chrome (header, footer, CSS scope)
# --------------------------------------------------------------------------- #

def test_portal_header_uses_the_nav_helper_and_aria_current():
    """The masthead must come from h.c4w_nav() -- the single nav source -- and
    the active item must carry aria-current for assistive tech."""
    header = _read('templates', 'c4w', 'snippets', 'portal_header.html')
    assert 'h.c4w_nav()' in header
    assert 'aria-current' in header
    assert 'h.c4w_login_url()' in header


def test_portal_base_replaces_ihp_wins_chrome():
    """C4W pages must not render the UNESCO masthead or site footer."""
    base = _read('templates', 'c4w', 'base.html')
    assert 'class="c4w-portal"' in base
    assert 'block header' in base
    assert 'portal_header.html' in base
    assert 'portal_footer.html' in base
    assert 'block content' in base


def test_access_treats_anonymous_user_as_logged_out():
    """CKAN 2.10's AnonymousUser is not None; a bare truthiness check 500s."""
    source = _read('logic', 'access.py')
    assert 'is_anonymous' in source
    assert 'current_userobj' in source


def test_flash_uses_the_ckan_210_helper():
    """h.flash is Flask's flash() in 2.10; pop_messages is a 500."""
    base = _read('templates', 'c4w', 'base.html')
    assert 'h.get_flashed_messages' in base
    assert 'h.flash.pop_messages' not in base


def test_credits_strip_keeps_the_contractual_attribution():
    """The funding attribution is a contractual obligation, not decoration.
    A restyle may move it; it may never drop it."""
    footer = _read('templates', 'c4w', 'snippets', 'portal_footer.html')
    assert 'Scivil' in footer
    assert 'Flemish Government' in footer
    assert 'creativecommons.org/licenses/by-sa/4.0' in footer


def test_moderation_payload_does_not_use_the_items_key():
    """Jinja treats dict.items as the method, so a list named items never
    renders. The queue and account tables must iterate ``rows``."""
    source = _read('logic', 'action', 'moderation.py')
    assert "'rows': items" in source
    admin = _read('templates', 'c4w', 'admin.html')
    account = _read('templates', 'c4w', 'account.html')
    assert 'group.rows' in admin
    assert 'group.rows' in account
    assert 'group.items' not in admin
    assert 'group.items' not in account


def test_css_is_scoped_not_global():
    """Every token lives under html.c4w-portal: nothing from this portal may
    leak into the rest of IHP-WINS. :root is the leak that would do it."""
    css = _read('assets', 'css', 'c4w.css')
    assert ':root' not in css  # the selector, not the word in a comment
    assert 'html.c4w-portal' in css


# --------------------------------------------------------------------------- #
# Datasets: the data wizard and the processing package
# --------------------------------------------------------------------------- #

def test_dataset_form_steps_are_numbered_and_disjoint():
    from ckanext.c4w import constants

    steps = constants.DATASET_FORM_STEPS
    assert [s['step'] for s in steps] == list(range(1, len(steps) + 1))
    seen = [f for s in steps for f in s['fields']]
    duplicates = sorted({f for f in seen if seen.count(f) > 1})
    assert not duplicates, duplicates
    assert all(s['key'] and s['title'] and s['fields'] for s in steps)


def test_dataset_extra_fields_are_form_fields():
    """An extras key nobody can type is dead storage."""
    from ckanext.c4w import constants

    fields = {f for s in constants.DATASET_FORM_STEPS for f in s['fields']}
    for name in constants.DATASET_EXTRA_FIELDS:
        if name == 'terms_accepted_at':
            continue   # derived from the terms_accepted checkbox
        assert name in fields, name


def test_dataset_is_a_moderated_entity_with_a_pipeline():
    from ckanext.c4w import constants

    assert 'dataset' in constants.MODERATED_ENTITY_TYPES
    assert 'dataset' in constants.ENTITY_HAS_HIDDEN
    assert 'dataset' in constants.ENTITY_HAS_FEATURED
    assert constants.ENTITY_HAS_PROCESS == ('dataset',)
    assert constants.moderate_error('dataset', 'process') is None
    assert constants.moderate_error('project', 'process') == 'no_process'
    assert constants.DETAIL_ENDPOINTS['dataset'] == 'dataset_detail'


def test_moderation_reads_the_entity_class_registry():
    """A hand-written model map in moderation.py is how a new entity silently
    disappears from the account page and the reviewer's queue."""
    source = _read('logic', 'action', 'moderation.py')
    assert 'dict(db.ENTITY_CLASSES)' in source


def test_submit_chooser_links_every_open_choice():
    from ckanext.c4w import constants

    keys = {k for k, _t, _h in constants.SUBMIT_CHOICES}
    assert set(constants.SUBMIT_ENDPOINTS) <= keys
    assert 'dataset' in constants.SUBMIT_ENDPOINTS
    template = _read('templates', 'c4w', 'submit.html')
    assert 'endpoints.get' in template


def test_data_package_imports_no_ckan():
    """ckanext/c4w/data/ is the CKAN-free boundary: it must run in a bare
    checkout and in a CLI with no site. jobs.py is the one bridge."""
    package = PKG / 'data'
    if not package.is_dir():
        pytest.skip('data package not present yet')
    offenders = []
    for path in sorted(package.glob('*.py')):
        if path.name == 'jobs.py':
            continue
        tree = ast.parse(path.read_text(encoding='utf-8'))
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            for name in names:
                if name == 'ckan' or name.startswith('ckan.') \
                        or name.startswith('ckanext.c4w.logic') \
                        or name == 'ckanext.c4w.db':
                    offenders.append('%s: %s' % (path.name, name))
    assert not offenders, offenders


# --------------------------------------------------------------------------- #
# Form infrastructure
# --------------------------------------------------------------------------- #

def test_every_navl_wizard_field_has_a_schema_rule():
    """Steps 1, 3, 4 and 5 are navl-validated; a field declared in the step
    but absent from schema.py would be silently dropped by navl."""
    from ckanext.c4w import constants

    source = _read('logic', 'schema.py')
    for step in constants.DATASET_FORM_STEPS:
        if step['key'] in ('files',):
            continue
        for field in step['fields']:
            if field == 'mapping_json':
                continue   # validated by data/mapping.py, not navl
            assert "'%s'" % field in source, field


def test_uploads_module_exists_as_setup_py_promises():
    assert (PKG / 'logic' / 'uploads.py').is_file()


def test_form_macros_and_snippets_exist():
    assert (PKG / 'templates' / 'c4w' / 'macros' / 'form.html').is_file()
    assert (PKG / 'templates' / 'c4w' / 'snippets'
            / 'form_errors.html').is_file()
    assert (PKG / 'templates' / 'c4w' / 'snippets'
            / 'wizard_steps.html').is_file()


def test_config_declaration_covers_the_processing_options():
    text = (PKG / 'config_declaration.yaml').read_text(encoding='utf-8')
    for key in ('ckanext.c4w.data_max_upload_mb',
                'ckanext.c4w.data_inline_max_mb',
                'ckanext.c4w.async_processing',
                'ckanext.c4w.basemap_style',
                'ckanext.c4w.verification_ttl_hours',
                'ckan.upload.c4w_data.types'):
        assert key in text, key

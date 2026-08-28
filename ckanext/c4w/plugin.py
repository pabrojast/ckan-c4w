# encoding: utf-8
"""Main plugin for ckanext-c4w (Citizens4Water).

Every interface hook imports its module LAZILY. The plugin object is built
while CKAN is still assembling its own config, so a module-level import of
anything that reaches back into CKAN internals creates an import cycle that
surfaces later as an unrelated-looking failure. Keeping the imports inside the
methods also means a broken domain module degrades to one failing surface
rather than a portal that will not boot.
"""
import logging

import ckan.plugins as p
import ckan.plugins.toolkit as tk

from ckanext.c4w import __version__

log = logging.getLogger(__name__)

# Module-level guard so the (idempotent) table bootstrap effectively runs once
# per process even if ``configure`` is invoked more than once.
_tables_ensured = False


@tk.blanket.config_declarations
class C4wPlugin(p.SingletonPlugin):
    """Citizens4Water portal plugin."""

    p.implements(p.IConfigurer)
    p.implements(p.IConfigurable, inherit=True)
    p.implements(p.IBlueprint)
    p.implements(p.IActions)
    p.implements(p.IAuthFunctions)
    p.implements(p.IValidators)
    p.implements(p.ITemplateHelpers)
    p.implements(p.IClick)

    # IConfigurer

    def update_config(self, config):
        tk.add_template_directory(config, 'templates')
        tk.add_resource('assets', 'c4w')
        tk.add_public_directory(config, 'public')

    # IConfigurable

    def configure(self, config):
        """Bootstrap the database tables once, and never break startup.

        The broad except logs a GENERIC message: a database error here would
        otherwise put connection details into the log of a public portal.
        """
        global _tables_ensured
        if _tables_ensured:
            return
        try:
            from ckanext.c4w import db
            db.ensure_tables()
            _tables_ensured = True
        except Exception:
            log.error("ckanext-c4w: could not initialize database tables")

    # IBlueprint

    def get_blueprint(self):
        from ckanext.c4w import blueprint
        return blueprint.get_blueprints()

    # IActions

    def get_actions(self):
        from ckanext.c4w.logic import actions
        return actions.get_actions()

    # IAuthFunctions

    def get_auth_functions(self):
        from ckanext.c4w.logic import auth
        return auth.get_auth_functions()

    # IValidators

    def get_validators(self):
        from ckanext.c4w.logic import validators
        return validators.get_validators()

    # ITemplateHelpers

    def get_helpers(self):
        from ckanext.c4w.logic import helpers
        return helpers.get_helpers()

    # IClick

    def get_commands(self):
        from ckanext.c4w import cli
        return [cli.c4w]

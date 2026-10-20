# encoding: utf-8
"""Action registry for ckanext-c4w.

Aggregates the per-domain modules under ``logic/action/`` into the single
dict CKAN asks for. Splitting by domain keeps each module readable; this file
exists so ``plugin.py`` never has to know how many there are.

INVARIANT: every name returned here MUST have a matching entry in
``logic/auth.py::get_auth_functions()``. CKAN's ``check_access`` raises
``ValueError: Authorization function not found`` for an unregistered name,
which surfaces to a visitor as a 500 rather than a 403. The smoke phase of
scripts/run-ckan-tests.sh asserts both directions of that mapping.
"""

from ckanext.c4w.logic.action import (
    datasets, events, moderation, organisations, platforms, posts, projects,
    registration, resources, stats, project_write, lookup, content_write, deletion)


def get_actions():
    actions = {}
    # Domain modules land here as the increments arrive. The loop shape is
    # deliberate: adding a module is one import and one tuple entry.
    for module in (datasets, events, moderation, organisations, platforms,
                   posts, projects, registration, resources, stats, project_write, lookup, content_write, deletion):
        actions.update(module.get_actions())
    # Use the same lock before taking row locks, including nested actions.
    from functools import wraps
    from ckanext.c4w.logic import integrity
    from ckanext.c4w import db
    import ckan.plugins.toolkit as tk

    def guarded(name, fn):
        @wraps(fn)
        def write(context, data_dict):
            tk.check_access(name, context, data_dict)
            integrity.lock(db.Session)
            try:
                return fn(context, data_dict)
            except Exception:
                db.Session.rollback()
                raise
        return write

    return {name: fn if getattr(fn, 'side_effect_free', False) else guarded(name, fn)
            for name, fn in actions.items()}

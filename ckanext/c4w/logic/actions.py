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
    events, organisations, platforms, posts, projects, resources, stats)


def get_actions():
    actions = {}
    # Domain modules land here as the increments arrive. The loop shape is
    # deliberate: adding a module is one import and one tuple entry.
    for module in (events, organisations, platforms, posts,
                   projects, resources, stats):
        actions.update(module.get_actions())
    return actions

# encoding: utf-8
"""Authorisation functions for ckanext-c4w.

Three rules shape this module.

**Every registered action needs an entry here**, even a trivial one -- see the
invariant in ``logic/actions.py``.

**Reads are anonymous; visibility is enforced on the data, not on the auth.**
The public listings must serve visitors who are not logged in, so the auth
function says yes and the action filters out unapproved and hidden rows. An
auth function that tried to express "yes, but only the approved ones" would
have to load the row it is authorising, and CKAN calls it before that.

**A hidden or unapproved item is a 404, never a 403.** A 403 (or a redirect to
the login page) tells an anonymous visitor that the thing exists, which is an
existence oracle over content that is deliberately not public yet.
"""
import ckan.plugins.toolkit as tk


def _is_sysadmin(context):
    """True when the requesting user is a CKAN sysadmin."""
    user = context.get('auth_user_obj')
    return bool(user is not None and getattr(user, 'sysadmin', False))


@tk.auth_allow_anonymous_access
def c4w_stats(context, data_dict):
    """Public counts, readable by anyone -- they describe the public listings."""
    return {'success': True}


@tk.auth_allow_anonymous_access
def c4w_project_show(context, data_dict):
    """Anyone may ask; the action decides what they get.

    Visibility of an unapproved or hidden project is settled in the action,
    which raises NotFound rather than NotAuthorized -- see the module
    docstring on the existence oracle.
    """
    return {'success': True}


@tk.auth_allow_anonymous_access
def c4w_project_list(context, data_dict):
    return {'success': True}


@tk.auth_allow_anonymous_access
def c4w_project_facets(context, data_dict):
    return {'success': True}


@tk.auth_allow_anonymous_access
def c4w_project_record_view(context, data_dict):
    """Counting a page view must not require an account.

    The counter is the one thing on this portal an anonymous visitor writes,
    and it is why the listing can offer "Total Accesses" as an ordering.
    """
    return {'success': True}


def get_auth_functions():
    return {
        'c4w_stats': c4w_stats,
        'c4w_project_show': c4w_project_show,
        'c4w_project_list': c4w_project_list,
        'c4w_project_facets': c4w_project_facets,
        'c4w_project_record_view': c4w_project_record_view,
    }

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


def get_auth_functions():
    return {
        'c4w_stats': c4w_stats,
    }

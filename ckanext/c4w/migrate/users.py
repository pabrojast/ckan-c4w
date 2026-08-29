# encoding: utf-8
"""Resolving legacy Django accounts to CKAN users.

The Django app already authenticated against CKAN
(themes/citizens4water/backends/ckanlogin.py), creating each local account
with ``name`` set to the CKAN username -- so the username is the join key, and
matching on it is a lookup rather than a guess.

THE IMPORTER NEVER CREATES A CKAN USER. Creating accounts for people who have
not asked for one, from a legacy table, is not a migration decision an
importer gets to make on its own. Anything unresolved is left NULL and
reported, so the operator can decide once and fix it with a single UPDATE.

Nothing about a legacy account is written to a c4w row's ``extras``: those are
merged into the top level of every dictized row and are therefore published.
The reconciliation table goes to the operator's report only.
"""
import logging

log = logging.getLogger(__name__)


class UserResolver(object):
    """Maps a Django user id to a CKAN user id, remembering what it could not.

    Loads the whole CKAN user table once. Calling ``user_show`` per row would
    be a query per entity, and the corpus has 113 of them.
    """

    def __init__(self, django_users, fallback_user_id=None):
        # {django_id: {'name', 'email', ...}}
        self._django = {u['id']: u for u in django_users}
        self._fallback = fallback_user_id
        self._by_name = {}
        self._by_email = {}
        self._ambiguous_emails = set()
        self.unresolved = {}      # django_id -> reason
        self.resolved = {}        # django_id -> ckan user id
        self._load_ckan_users()

    def _load_ckan_users(self):
        from ckan import model
        from ckan.model.meta import Session

        for user in Session.query(model.User).filter(
                model.User.state == 'active'):
            if user.name:
                self._by_name[user.name.lower()] = user.id
            email = (user.email or u'').strip().lower()
            if not email:
                continue
            if email in self._by_email and self._by_email[email] != user.id:
                # Two accounts share an address: matching on it would pick one
                # arbitrarily, so neither is used.
                self._ambiguous_emails.add(email)
            self._by_email[email] = user.id

    def resolve(self, django_id):
        """CKAN user id for a Django user id, or the fallback, or None."""
        if django_id is None:
            return None
        if django_id in self.resolved:
            return self.resolved[django_id]

        account = self._django.get(django_id)
        if account is None:
            self.unresolved[django_id] = 'no such Django account'
            return self._fallback

        name = (account.get('name') or u'').strip().lower()
        if name and name in self._by_name:
            self.resolved[django_id] = self._by_name[name]
            return self.resolved[django_id]

        email = (account.get('email') or u'').strip().lower()
        if email and email in self._by_email:
            if email in self._ambiguous_emails:
                self.unresolved[django_id] = (
                    'email %s matches more than one CKAN account' % email)
                return self._fallback
            self.resolved[django_id] = self._by_email[email]
            return self.resolved[django_id]

        self.unresolved[django_id] = 'no CKAN account with that name or email'
        return self._fallback

    def report(self):
        """The reconciliation table for the operator.

        Includes the name and address so a human can decide who each
        unresolved account belongs to. This is the ONLY place that identity
        travels -- never into a c4w row.
        """
        rows = []
        for django_id, reason in sorted(self.unresolved.items()):
            account = self._django.get(django_id) or {}
            rows.append({
                'django_id': django_id,
                'name': account.get('name'),
                'email': account.get('email'),
                'reason': reason,
            })
        return rows

# encoding: utf-8
"""Claiming a project: asking to become one of its editors.

Built for the projects imported from the UNESCO list, which arrive with no
owner: the people who run them ask for edit rights, a reviewer checks that
they really are the project's managers, and approval adds an ``editor``
relation -- the same relation the project form's "Editors" field writes, so
there is one way to be an editor, not two.

Who reviews: CKAN sysadmins and C4W content administrators. The review
screen lives in ckanext-colab's inbox (``/colab/admin/citizen-science``);
everything that decides -- records, permissions, the grant itself, the mail
-- lives here, so the inbox never has to know how a C4W permission works.

A request row is never deleted. Approve and reject both append to
``history``, which is the audit trail.
"""
import datetime
import json
import logging
import re

import ckan.plugins.toolkit as tk

from ckanext.c4w import db
from ckanext.c4w.logic.action import _common

log = logging.getLogger(__name__)

STATUSES = ('pending', 'approved', 'rejected', 'cancelled')
REASON_MIN = 20
REJECT_REASON_MIN = 10
EMAIL_RE = re.compile(r'^[^@\s]+@[^@\s]+\.[^@\s]+$')
PAGE_LIMIT = 100


def _now():
    return datetime.datetime.utcnow().replace(microsecond=0)


def _user(context):
    user = context.get('auth_user_obj')
    if user is None or not getattr(user, 'id', None):
        raise tk.NotAuthorized(tk._('Sign in to claim a project.'))
    return user


def _text(data_dict, key, limit, required=False, minimum=0):
    value = u' '.join((u'%s' % (data_dict.get(key) or u'')).split()) \
        if key != 'reason' else (u'%s' % (data_dict.get(key) or u'')).strip()
    if required and len(value) < max(minimum, 1):
        if minimum:
            raise tk.ValidationError({key: [tk._(
                u'Please write at least %d characters.') % minimum]})
        raise tk.ValidationError({key: [tk._(u'Missing value')]})
    if len(value) > limit:
        raise tk.ValidationError({key: [tk._(
            u'Must be at most %d characters') % limit]})
    return value or None


def _history(claim, actor_id, action, note=None):
    try:
        items = json.loads(claim.history or u'[]')
    except ValueError:
        items = []
    items.append({'at': _now().isoformat(), 'by': actor_id,
                  'action': action, 'note': note})
    claim.history = json.dumps(items)


def _project(reference):
    project = _common.get_by_reference(db.C4wProject, reference)
    if project is None or not project.approved or project.hidden:
        raise tk.ObjectNotFound(tk._(u'Project not found'))
    return project


def claim_dict(claim, project=None, user=None, reviewer=None):
    import ckan.model as model

    if project is None:
        project = db.Session.query(db.C4wProject).filter_by(
            id=claim.project_id).first()
    if user is None:
        user = model.User.get(claim.user_id)
    if reviewer is None and claim.reviewed_by:
        reviewer = model.User.get(claim.reviewed_by)
    try:
        history = json.loads(claim.history or u'[]')
    except ValueError:
        history = []
    return {
        'id': claim.id,
        'status': claim.status,
        'project': {'id': project.id, 'slug': project.slug,
                    'name': project.name,
                    'claimable': bool(db.load_extras(project.extras)
                                      .get('claimable'))}
        if project is not None else None,
        'user': {'id': user.id, 'name': user.name,
                 'fullname': user.fullname, 'email': user.email}
        if user is not None else {'id': claim.user_id},
        'role_in_project': claim.role_in_project,
        'organisation': claim.organisation,
        'contact_email': claim.contact_email,
        'reason': claim.reason,
        'evidence_url': claim.evidence_url,
        'rejection_reason': claim.rejection_reason,
        'reviewed_by': {'id': reviewer.id, 'name': reviewer.name,
                        'fullname': reviewer.fullname}
        if reviewer is not None else None,
        'reviewed_at': claim.reviewed_at.isoformat()
        if claim.reviewed_at else None,
        'created': claim.created.isoformat() if claim.created else None,
        'history': history,
    }


# --------------------------------------------------------------------------- #
# Actions
# --------------------------------------------------------------------------- #

def c4w_project_claim_create(context, data_dict):
    """Ask to become an editor of a published project.

    :param project: slug or id
    :param reason: why you should edit it -- your role in the project
        (at least 20 characters)
    :param role_in_project: e.g. "Project coordinator" (optional)
    :param organisation: your organisation (optional)
    :param contact_email: an address the reviewers can verify (optional;
        defaults to the account's)
    :param evidence_url: a page that shows your role (optional)
    """
    tk.check_access('c4w_project_claim_create', context, data_dict)
    db.ensure_mappers()
    user = _user(context)
    project = _project(data_dict.get('project') or data_dict.get('id'))
    if _common.can_edit('project', project, context):
        raise tk.ValidationError({'project': [tk._(
            u'You can already edit this project.')]})
    existing = db.Session.query(db.C4wProjectClaim).filter_by(
        project_id=project.id, user_id=user.id, status=u'pending').first()
    if existing is not None:
        raise tk.ValidationError({'project': [tk._(
            u'You already have a pending request for this project.')]})

    reason = _text(data_dict, 'reason', 4000, required=True,
                   minimum=REASON_MIN)
    email = _text(data_dict, 'contact_email', 200) or (user.email or None)
    if email and not EMAIL_RE.match(email):
        raise tk.ValidationError({'contact_email': [tk._(
            u'Enter a valid e-mail address.')]})
    evidence = _text(data_dict, 'evidence_url', 1000)
    if evidence:
        from ckanext.c4w.text import ensure_scheme
        evidence = ensure_scheme(evidence)
        if not evidence:
            raise tk.ValidationError({'evidence_url': [tk._(
                u'Enter a web address starting with http:// or https://')]})

    claim = db.C4wProjectClaim(
        project_id=project.id, user_id=user.id, status=u'pending',
        role_in_project=_text(data_dict, 'role_in_project', 200),
        organisation=_text(data_dict, 'organisation', 300),
        contact_email=email, reason=reason, evidence_url=evidence,
        created=_now(), history=u'[]')
    _history(claim, user.id, u'requested')
    db.Session.add(claim)
    db.Session.commit()
    log.info('C4W project claim requested: project=%s user=%s',
             project.id, user.id)

    from ckanext.c4w.logic import mail
    mail.notify_claim_reviewers(project, user, claim)
    return claim_dict(claim, project=project, user=user)


@tk.side_effect_free
def c4w_project_claim_list(context, data_dict):
    """Claims for reviewers, newest first.

    :param status: pending (default), approved, rejected, cancelled or all
    :param limit: at most 100 (default 20)
    :param offset: default 0
    """
    tk.check_access('c4w_project_claim_list', context, data_dict)
    db.ensure_mappers()
    status = data_dict.get('status') or u'pending'
    if status != u'all' and status not in STATUSES:
        raise tk.ValidationError({'status': [tk._(u'Unknown status')]})
    try:
        limit = max(1, min(PAGE_LIMIT, int(data_dict.get('limit') or 20)))
        offset = max(0, int(data_dict.get('offset') or 0))
    except (TypeError, ValueError):
        raise tk.ValidationError({'limit': [tk._(u'Must be a number')]})
    query = db.Session.query(db.C4wProjectClaim)
    if status != u'all':
        query = query.filter(db.C4wProjectClaim.status == status)
    count = query.count()
    order = (db.C4wProjectClaim.created.asc() if status == u'pending'
             else db.C4wProjectClaim.created.desc())
    rows = query.order_by(order).offset(offset).limit(limit).all()
    return {'count': count, 'results': [claim_dict(row) for row in rows]}


@tk.side_effect_free
def c4w_project_claim_pending_count(context, data_dict):
    tk.check_access('c4w_project_claim_pending_count', context, data_dict)
    db.ensure_mappers()
    return db.Session.query(db.C4wProjectClaim).filter_by(
        status=u'pending').count()


@tk.side_effect_free
def c4w_project_claim_mine(context, data_dict):
    """The signed-in user's own claim on one project, or None."""
    db.ensure_mappers()
    user = context.get('auth_user_obj')
    if user is None or not getattr(user, 'id', None):
        return None
    project = _common.get_by_reference(db.C4wProject,
                                       data_dict.get('project'))
    if project is None:
        return None
    claim = (db.Session.query(db.C4wProjectClaim)
             .filter_by(project_id=project.id, user_id=user.id)
             .order_by(db.C4wProjectClaim.created.desc()).first())
    return claim_dict(claim, project=project, user=user) if claim else None


def c4w_project_claim_decide(context, data_dict):
    """Approve or reject a pending claim.

    :param id: claim id
    :param decision: approve or reject
    :param reason: required to reject (at least 10 characters); sent to
        the person who asked
    """
    tk.check_access('c4w_project_claim_decide', context, data_dict)
    db.ensure_mappers()
    import ckan.model as model

    decision = data_dict.get('decision')
    if decision not in ('approve', 'reject'):
        raise tk.ValidationError({'decision': [tk._(
            u'Choose approve or reject.')]})
    claim = (db.Session.query(db.C4wProjectClaim)
             .filter_by(id=u'%s' % (data_dict.get('id') or u''))
             .with_for_update().first())
    if claim is None:
        raise tk.ObjectNotFound(tk._(u'Request not found'))
    if claim.status != u'pending':
        raise tk.ValidationError({'id': [tk._(
            u'This request was already decided.')]})
    note = (u'%s' % (data_dict.get('reason') or u'')).strip()[:2000] or None
    if decision == 'reject' and len(note or u'') < REJECT_REASON_MIN:
        raise tk.ValidationError({'reason': [tk._(
            u'Explain the decision in at least %d characters.')
            % REJECT_REASON_MIN]})

    actor = context['auth_user_obj']
    project = db.Session.query(db.C4wProject).filter_by(
        id=claim.project_id).first()
    user = model.User.get(claim.user_id)
    if decision == 'approve':
        if project is None or user is None or user.state != 'active':
            raise tk.ValidationError({'id': [tk._(
                u'The project or the account no longer exists.')]})
        _grant(project, user)
        claim.status = u'approved'
    else:
        claim.status = u'rejected'
        claim.rejection_reason = note
    claim.reviewed_by = actor.id
    claim.reviewed_at = _now()
    _history(claim, actor.id, claim.status, note)
    db.Session.add(claim)
    db.Session.commit()
    log.info('C4W project claim %s: claim=%s actor=%s', claim.status,
             claim.id, actor.id)

    from ckanext.c4w.logic import mail
    mail.send_claim_decision(user, project, claim)
    return claim_dict(claim, project=project, user=user, reviewer=actor)


def _grant(project, user):
    """Editor on the project; admin on its linked CKAN organisation."""
    from ckanext.c4w.logic.action.registration import (
        _grant_editor, _site_context)

    _grant_editor(u'project', project.id, user.id)
    extras = db.load_extras(project.extras)
    extras['claimable'] = False
    claimed = extras.get('claimed_by') or []
    if user.id not in claimed:
        claimed.append(user.id)
    extras['claimed_by'] = claimed
    project.extras = db.dump_extras(extras)
    db.Session.add(project)

    organisation = None
    if project.main_organisation_id:
        organisation = db.Session.query(db.C4wOrganisation).filter_by(
            id=project.main_organisation_id).first()
    if organisation is None or not organisation.ckan_org_id:
        return
    site = _site_context()
    try:
        org = tk.get_action('organization_show')(dict(site), {
            'id': organisation.ckan_org_id, 'include_users': True})
    except tk.ObjectNotFound:
        return
    roles = {u['id']: u.get('capacity') for u in org.get('users') or []}
    if roles.get(user.id) == u'admin':
        return
    tk.get_action('organization_member_create')(dict(site), {
        'id': organisation.ckan_org_id, 'username': user.name,
        'role': u'admin'})


# --------------------------------------------------------------------------- #
# Auth
# --------------------------------------------------------------------------- #

def _signed_in(context, data_dict):
    user = context.get('auth_user_obj')
    return {'success': bool(user is not None and getattr(user, 'id', None)
                            and getattr(user, 'state', 'active') == 'active')}


@tk.auth_allow_anonymous_access
def _mine(context, data_dict):
    return {'success': True}


def _reviewer(context, data_dict):
    return {'success': _common.is_content_admin(context)}


def get_actions():
    return {fn.__name__: fn for fn in (
        c4w_project_claim_create, c4w_project_claim_list,
        c4w_project_claim_pending_count, c4w_project_claim_mine,
        c4w_project_claim_decide)}


def get_auth_functions():
    return {
        'c4w_project_claim_create': _signed_in,
        'c4w_project_claim_mine': _mine,
        'c4w_project_claim_list': _reviewer,
        'c4w_project_claim_pending_count': _reviewer,
        'c4w_project_claim_decide': _reviewer,
    }

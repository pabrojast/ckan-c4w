# encoding: utf-8
"""Outbound e-mail for the C4W flows.

Every function here is best-effort: it returns True when the mail was
handed to the SMTP server and False otherwise, and never raises. A
registration must not fail because the mail relay is down -- the visitor can
ask for the verification to be re-sent.
"""
import logging

import ckan.plugins.toolkit as tk

log = logging.getLogger(__name__)


def _site_title():
    return tk.config.get('ckan.site_title') or u'IHP-WINS'


def _send(name, email, subject, body):
    if not email:
        return False
    try:
        tk.mail_recipient(name or email, email, subject, body)
        return True
    except Exception:
        # Broad on purpose: CKAN's mailer can surface a raw SMTP exception
        # from its own ``finally: quit()``.
        log.warning("ckanext-c4w: could not send '%s'", subject,
                    exc_info=True)
        return False


def send_verification(fullname, email, url, profile_type='citizen'):
    subject = tk._(u'Confirm your Citizens4Water account')
    lines = [
        tk._(u'Hello %s,') % (fullname or email),
        u'',
        tk._(u'Thank you for registering with Citizens4Water on %s.')
        % _site_title(),
        tk._(u'Please confirm your e-mail address by opening this link:'),
        u'',
        url,
        u'',
    ]
    if profile_type == 'manager':
        lines.append(tk._(
            u'Confirm your email to start contributing. The Citizens4Water '
            u'team reviews your organisation permissions separately and '
            u'will email you their decision.'))
    else:
        lines.append(tk._(
            u'Once confirmed you can sign in, propose projects, share water data, news and events.'))
    lines += [u'', tk._(u'If you did not register, ignore this message.')]
    return _send(fullname, email, subject, u'\n'.join(lines))


def send_manager_decision(fullname, email, approved, note=None,
                          login_url=None):
    if approved:
        subject = tk._(u'Your Citizens4Water manager account is approved')
        lines = [
            tk._(u'Hello %s,') % (fullname or email),
            u'',
            tk._(u'Your organisation permissions have been approved. '
                 u'You can manage your organisation after signing in:'),
            u'',
            login_url or u'',
        ]
    else:
        subject = tk._(u'About your Citizens4Water manager request')
        lines = [
            tk._(u'Hello %s,') % (fullname or email),
            u'',
            tk._(u'The Citizens4Water team could not approve your project '
                 u'manager request at this time.'),
        ]
    if note:
        lines += [u'', tk._(u'Message from the reviewers:'), note]
    return _send(fullname, email, subject, u'\n'.join(lines))


def notify_moderators(subject, body):
    """Notify explicitly assigned C4W administrators, without a global admin blast."""
    recipients = moderation_recipients()
    results = [_send(name, email, subject, body) for name, email in recipients]
    return bool(results) and all(results)


def moderation_recipients():
    from ckanext.c4w import db
    import ckan.model as model
    db.ensure_mappers()
    users = db.Session.query(model.User).join(
        db.C4wPortalAdmin, db.C4wPortalAdmin.user_id == model.User.id).filter(
        model.User.state == 'active').all()
    recipients = {}
    for user in users:
        if user.email:
            recipients[user.email.strip().lower()] = user.fullname or user.name
    return [(name, email) for email, name in sorted(recipients.items())]


def notify_review(user_id, decision, note, url):
    import ckan.model as model
    user = model.User.get(user_id) if user_id else None
    if not user:
        return False
    label = {'approve': 'Approved', 'changes_requested': 'Changes requested', 'rejected': 'Rejected'}[decision]
    return _send(user.fullname, user.email, tk._('Citizens4Water: contribution review'),
                 label + '\n' + (note or '') + '\n' + url)


def _claim_review_url():
    """The Colab inbox tab where claims are decided; '' if not routable."""
    try:
        return tk.url_for('colab.show_admin', tab='claims', qualified=True)
    except Exception:
        return u''


def _project_url(project, endpoint='project_detail'):
    from ckanext.c4w.logic import helpers as c4w_helpers
    try:
        return c4w_helpers.c4w_url(endpoint, slug=project.slug,
                                   qualified=True)
    except Exception:
        return u''


def claim_reviewers():
    """Content administrators AND sysadmins: both may decide a claim."""
    import ckan.model as model
    recipients = dict((email, name) for name, email in
                      moderation_recipients())
    for user in model.Session.query(model.User).filter(
            model.User.sysadmin.is_(True),
            model.User.state == 'active').all():
        if user.email and user.name not in ('default',):
            recipients.setdefault(user.email.strip().lower(),
                                  user.fullname or user.name)
    return [(name, email) for email, name in sorted(recipients.items())]


def notify_claim_reviewers(project, user, claim):
    subject = tk._(u'Citizens4Water: request to manage "%s"') % project.name
    lines = [
        tk._(u'%s asked to become an editor of a Citizens4Water project.')
        % (user.fullname or user.name),
        u'',
        tk._(u'Project: %s') % project.name,
        _project_url(project),
        tk._(u'Account: %s (%s)') % (user.name, user.email or u'-'),
    ]
    if claim.role_in_project:
        lines.append(tk._(u'Role: %s') % claim.role_in_project)
    if claim.organisation:
        lines.append(tk._(u'Organisation: %s') % claim.organisation)
    if claim.contact_email:
        lines.append(tk._(u'Contact: %s') % claim.contact_email)
    if claim.evidence_url:
        lines.append(tk._(u'Evidence: %s') % claim.evidence_url)
    lines += [u'', claim.reason or u'', u'',
              tk._(u'Review it here:'), _claim_review_url()]
    body = u'\n'.join(lines)
    results = [_send(name, email, subject, body)
               for name, email in claim_reviewers()]
    return bool(results) and all(results)


def send_claim_decision(user, project, claim):
    if user is None:
        return False
    name = project.name if project is not None else u''
    if claim.status == u'approved':
        subject = tk._(u'You can now edit "%s" on Citizens4Water') % name
        lines = [
            tk._(u'Hello %s,') % (user.fullname or user.name),
            u'',
            tk._(u'Your request was approved: you are now an editor of '
                 u'this project. Sign in to update its page and add data:'),
            u'',
            _project_url(project) if project is not None else u'',
        ]
    else:
        subject = tk._(u'About your request to manage "%s"') % name
        lines = [
            tk._(u'Hello %s,') % (user.fullname or user.name),
            u'',
            tk._(u'The Citizens4Water team could not approve your request '
                 u'to edit this project.'),
        ]
        if claim.rejection_reason:
            lines += [u'', tk._(u'Message from the reviewers:'),
                      claim.rejection_reason]
    return _send(user.fullname or user.name,
                 user.email or claim.contact_email, subject,
                 u'\n'.join(lines))

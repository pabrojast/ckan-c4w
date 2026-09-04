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
            u'Because you asked for a project manager account, the '
            u'Citizens4Water team will review your request once your '
            u'e-mail is confirmed. You will receive a second message with '
            u'their decision.'))
    else:
        lines.append(tk._(
            u'Once confirmed you can sign in and share your data.'))
    lines += [u'', tk._(u'If you did not register, ignore this message.')]
    return _send(fullname, email, subject, u'\n'.join(lines))


def send_manager_decision(fullname, email, approved, note=None,
                          login_url=None):
    if approved:
        subject = tk._(u'Your Citizens4Water manager account is approved')
        lines = [
            tk._(u'Hello %s,') % (fullname or email),
            u'',
            tk._(u'Your project manager account has been approved. '
                 u'You can now sign in:'),
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
    """Tell the configured moderation inbox something needs a look."""
    email = (tk.config.get('ckanext.c4w.moderation_notify_email')
             or u'').strip()
    if not email:
        return False
    return _send(u'Citizens4Water moderators', email, subject, body)

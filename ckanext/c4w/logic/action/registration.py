# encoding: utf-8
"""Registration, e-mail verification and the project-manager approval.

Ported from ckanext-csunesco and adapted to the portal's own tables.

Three rules carry the security of this module:

* **One generic error for every creation failure.** A duplicate username,
  a duplicate e-mail, a weak password, a failed CAPTCHA -- all render the
  same message, so the form cannot be used to enumerate accounts.
* **The account is created PENDING and activated by the e-mail link.** The
  token is random, single-use, time-limited and stored only as a hash.
* **A manager gets nothing at signup.** The organisation membership, the
  C4W organisation link and the editor relations are granted by a sysadmin
  in ``c4w_manager_approve``, after the e-mail is verified.
"""
import datetime
import hashlib
import logging
import re
import secrets
import unicodedata

import ckan.plugins.toolkit as tk

from ckanext.c4w import constants, db
from ckanext.c4w.logic.action import _common
from ckanext.c4w.text import slugify

log = logging.getLogger(__name__)

GENERIC_ERROR = u'Registration data invalid, please review your details.'
RECAPTCHA_URL = 'https://www.google.com/recaptcha/api/siteverify'


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

def _now():
    return datetime.datetime.utcnow()


def _hash(token):
    return hashlib.sha256((u'%s' % token).encode('utf-8')).hexdigest()


def _ttl_hours():
    try:
        return int(tk.config.get('ckanext.c4w.verification_ttl_hours') or 48)
    except (TypeError, ValueError):
        return 48


def _anon_context():
    import ckan.model as model
    return {'model': model, 'session': model.Session, 'user': u''}


def _site_context():
    import ckan.model as model
    site_user = tk.get_action('get_site_user')({'ignore_auth': True}, {})
    return {'model': model, 'session': model.Session,
            'user': site_user['name'], 'ignore_auth': True}


def generate_username(fullname, email=None, exists=None):
    """An available CKAN username derived from a person's name.

    Slugifies the name (fallback: the e-mail local part), then probes for
    availability appending ``-2``, ``-3`` ...; a random suffix guarantees
    termination. ``exists`` is the probe (defaults to ``model.User.get``)
    so the rule is testable without a database.
    """
    if exists is None:
        import ckan.model as model
        exists = lambda name: model.User.get(name) is not None   # noqa: E731
    source = (fullname or u'').strip() or (email or u'').split(u'@')[0]
    source = unicodedata.normalize('NFKD', source)
    source = source.encode('ascii', 'ignore').decode('ascii')
    base = re.sub(r'[^a-z0-9_-]+', '-', source.lower()).strip('-_')
    base = re.sub(r'-{2,}', '-', base)[:80]
    if len(base) < 2:
        base = u'citizen'
    candidate = base
    for suffix in range(2, 200):
        if not exists(candidate):
            return candidate
        candidate = u'%s-%d' % (base, suffix)
    return u'%s-%s' % (base, secrets.token_hex(4))


def recaptcha_configured():
    return bool(tk.config.get('ckan.recaptcha.publickey')
                and tk.config.get('ckan.recaptcha.privatekey'))


def _verify_recaptcha(token):
    if not token:
        return False
    try:
        import requests
        response = requests.post(RECAPTCHA_URL, data={
            'secret': tk.config.get('ckan.recaptcha.privatekey'),
            'response': token,
        }, timeout=10)
        result = response.json()
    except Exception:
        log.warning("ckanext-c4w: reCAPTCHA verification failed to complete")
        return False
    return bool(result.get('success')) and result.get('score', 0) > 0.5


def _generic():
    return tk.ValidationError({'message': [tk._(GENERIC_ERROR)]})


def _profile_by_user(user_id):
    from ckan.model.meta import Session
    db.ensure_mappers()
    return (Session.query(db.C4wUserProfile)
            .filter(db.C4wUserProfile.user_id == user_id).first())


def _profile_dict(profile, user=None):
    if user is None:
        import ckan.model as model
        user = model.User.get(profile.user_id)
    out = {
        'user_id': profile.user_id,
        'name': getattr(user, 'name', None),
        'fullname': getattr(user, 'fullname', None),
        'email': getattr(user, 'email', None),
        'state': getattr(user, 'state', None),
        'profile_type': profile.profile_type,
        'country': profile.country,
        'organisation_text': profile.organisation_text,
        'email_verified': bool(profile.email_verified),
        'verified_at': profile.verified_at.isoformat()
        if profile.verified_at else None,
        'org_choice': profile.org_choice,
        'ckan_org_id': profile.ckan_org_id,
        'org_name_requested': profile.org_name_requested,
        'org_type': profile.org_type,
        'org_url': profile.org_url,
        'job_title': profile.job_title,
        'c4w_organisation_id': profile.c4w_organisation_id,
        'manager_decision': profile.manager_decision,
        'manager_reviewed_at': profile.manager_reviewed_at.isoformat()
        if profile.manager_reviewed_at else None,
        'manager_note': profile.manager_note,
        'created': profile.created.isoformat() if profile.created else None,
    }
    return out


def _organization_choices():
    """``{id_or_name: id}`` of every CKAN organisation, for validation."""
    out = {}
    try:
        rows = tk.get_action('organization_list')(
            {'ignore_auth': True}, {'all_fields': True, 'limit': 1000})
    except Exception:
        return out
    for row in rows:
        out[row['id']] = row['id']
        out[row['name']] = row['id']
    return out


# --------------------------------------------------------------------------- #
# Registration
# --------------------------------------------------------------------------- #

def _register(context, data_dict, profile_type):
    from ckan.model.meta import Session
    from ckanext.c4w.logic import mail
    from ckanext.c4w.logic import schema as schemas
    from ckanext.c4w.logic import helpers as c4w_helpers
    import ckan.model as model

    schema = (schemas.registration_manager_schema()
              if profile_type == 'manager'
              else schemas.registration_citizen_schema())
    data, errors = schemas.validate(data_dict, schema, context)
    if errors:
        raise tk.ValidationError(errors)

    if recaptcha_configured() and not _verify_recaptcha(
            data.get('recaptcha_response')):
        raise _generic()

    org_id = None
    if profile_type == 'manager':
        if data.get('org_choice') == 'existing':
            choices = _organization_choices()
            org_id = choices.get((data.get('ckan_org_id') or u'').strip())
            if not org_id:
                raise tk.ValidationError(
                    {'ckan_org_id': [tk._(u'Choose an organisation')]})
        elif not (data.get('org_name_requested') or u'').strip():
            raise tk.ValidationError(
                {'org_name_requested': [tk._(u'Name the organisation')]})

    anon = _anon_context()
    try:
        tk.check_access('user_create', anon, {})
    except tk.NotAuthorized:
        raise _generic()

    username = (data.get('username') or u'').strip().lower() \
        or generate_username(data.get('fullname'), data.get('email'))
    try:
        created = tk.get_action('user_create')(anon, {
            'name': username,
            'email': data['email'],
            'password': data['password'],
            'fullname': data['fullname'],
        })
    except tk.ValidationError:
        log.info("ckanext-c4w: account creation rejected by user_create")
        raise _generic()
    except Exception:
        log.exception("ckanext-c4w: account creation failed")
        Session.rollback()
        raise _generic()

    user_obj = model.User.get(created['id'])
    user_obj.set_pending()
    token = secrets.token_urlsafe(32)
    now = _now()
    db.ensure_mappers()
    profile = db.C4wUserProfile(
        user_id=created['id'],
        profile_type=profile_type,
        country=data.get('country') or None,
        organisation_text=data.get('organisation_text') or None,
        terms_accepted_at=now,
        email_verified=False,
        verification_token_hash=_hash(token),
        token_created=now,
        org_choice=data.get('org_choice') if profile_type == 'manager'
        else None,
        ckan_org_id=org_id,
        org_name_requested=(data.get('org_name_requested') or None)
        if data.get('org_choice') == 'new' else None,
        org_type=data.get('org_type') or None,
        org_url=data.get('org_url') or None,
        job_title=data.get('job_title') or None,
    )
    Session.add(user_obj)
    Session.add(profile)
    Session.commit()

    url = c4w_helpers.c4w_url('verify_email', token=token, qualified=True)
    sent = mail.send_verification(data['fullname'], data['email'], url,
                                  profile_type)
    return {'id': created['id'], 'name': username,
            'profile_type': profile_type, 'mail_sent': sent}


def c4w_register_citizen(context, data_dict):
    """Create a pending citizen-scientist account and e-mail the link."""
    tk.check_access('c4w_register_citizen', context, data_dict)
    return _register(context, data_dict, 'citizen')


def c4w_register_manager(context, data_dict):
    """Create a pending project-manager account with an organisation
    request; a sysadmin approves it after the e-mail is verified."""
    tk.check_access('c4w_register_manager', context, data_dict)
    return _register(context, data_dict, 'manager')


# --------------------------------------------------------------------------- #
# Verification
# --------------------------------------------------------------------------- #

def c4w_verify_email(context, data_dict):
    """Consume a verification token.

    Returns ``{'state': ok | manager_pending | expired | invalid}``. A
    citizen is activated on the spot; a manager only becomes
    ``email_verified`` and waits for the reviewers.
    """
    tk.check_access('c4w_verify_email', context, data_dict)
    from ckan.model.meta import Session
    from ckanext.c4w.logic import mail
    import ckan.model as model

    token = (u'%s' % (data_dict.get('token') or u'')).strip()
    if not token or len(token) > 200:
        return {'state': 'invalid'}
    db.ensure_mappers()
    profile = (Session.query(db.C4wUserProfile)
               .filter(db.C4wUserProfile.verification_token_hash
                       == _hash(token)).first())
    if profile is None:
        return {'state': 'invalid'}
    if profile.token_created and (
            _now() - profile.token_created
            > datetime.timedelta(hours=_ttl_hours())):
        return {'state': 'expired'}
    user = model.User.get(profile.user_id)
    if user is None or user.is_deleted():
        return {'state': 'invalid'}

    profile.email_verified = True
    profile.verified_at = _now()
    profile.verification_token_hash = None
    profile.modified = _now()
    state = 'ok'
    if profile.profile_type == 'manager':
        if profile.manager_decision == 'approved':
            user.activate()
        else:
            state = 'manager_pending'
    else:
        user.activate()
    Session.add(profile)
    Session.add(user)
    Session.commit()

    if state == 'manager_pending':
        try:
            url = tk.h.url_for('c4w.admin_index', qualified=True)
        except Exception:
            url = u''
        mail.notify_moderators(
            tk._(u'[Citizens4Water] Project manager request: %s')
            % (user.fullname or user.name),
            u'%s <%s>\n%s' % (user.fullname or user.name, user.email, url))
    return {'state': state, 'profile_type': profile.profile_type,
            'name': user.name}


def c4w_verification_resend(context, data_dict):
    """Rotate and re-send the token for a pending, unverified account.

    Always returns the same answer whether or not the address is known.
    """
    tk.check_access('c4w_verification_resend', context, data_dict)
    from ckan.model.meta import Session
    from ckanext.c4w.logic import mail
    from ckanext.c4w.logic import helpers as c4w_helpers
    import ckan.model as model

    email = (u'%s' % (data_dict.get('email') or u'')).strip()
    if not email:
        return {'ok': True}
    user = model.User.by_email(email)
    if user is None or not user.is_pending():
        return {'ok': True}
    profile = _profile_by_user(user.id)
    if profile is None or profile.email_verified:
        return {'ok': True}
    token = secrets.token_urlsafe(32)
    profile.verification_token_hash = _hash(token)
    profile.token_created = _now()
    profile.modified = _now()
    Session.add(profile)
    Session.commit()
    url = c4w_helpers.c4w_url('verify_email', token=token, qualified=True)
    mail.send_verification(user.fullname, user.email, url,
                           profile.profile_type)
    return {'ok': True}


@tk.side_effect_free
def c4w_user_profile_show(context, data_dict):
    """The portal profile of a user: their own, or any for a sysadmin."""
    tk.check_access('c4w_user_profile_show', context, data_dict)
    import ckan.model as model
    requester = context.get('auth_user_obj')
    reference = data_dict.get('id') or getattr(requester, 'id', None)
    user = model.User.get(reference)
    if user is None:
        raise tk.ObjectNotFound(tk._('User not found'))
    if not (_common.is_sysadmin(context) or user.id == requester.id):
        raise tk.ObjectNotFound(tk._('User not found'))
    profile = _profile_by_user(user.id)
    if profile is None:
        return {'user_id': user.id, 'name': user.name, 'profile_type': None}
    return _profile_dict(profile, user)


# --------------------------------------------------------------------------- #
# Manager approval
# --------------------------------------------------------------------------- #

@tk.side_effect_free
def c4w_manager_list(context, data_dict):
    """Verified manager requests awaiting a decision, oldest first."""
    tk.check_access('c4w_manager_list', context, data_dict)
    from ckan.model.meta import Session
    import ckan.model as model

    db.ensure_mappers()
    query = (Session.query(db.C4wUserProfile)
             .filter(db.C4wUserProfile.profile_type == u'manager',
                     db.C4wUserProfile.manager_decision.is_(None))
             .order_by(db.C4wUserProfile.created.asc()))
    out = []
    for profile in query.all():
        user = model.User.get(profile.user_id)
        if user is None or user.is_deleted():
            continue
        item = _profile_dict(profile, user)
        if profile.ckan_org_id:
            try:
                org = tk.get_action('organization_show')(
                    {'ignore_auth': True}, {'id': profile.ckan_org_id})
                item['organisation_title'] = org.get('title') or org.get(
                    'name')
            except Exception:
                item['organisation_title'] = profile.ckan_org_id
        out.append(item)
    return {'requests': out}


def _resolve_manager(data_dict):
    import ckan.model as model
    user = model.User.get(data_dict.get('id') or data_dict.get('user_id'))
    if user is None:
        raise tk.ObjectNotFound(tk._('User not found'))
    profile = _profile_by_user(user.id)
    if profile is None or profile.profile_type != u'manager':
        raise tk.ObjectNotFound(tk._('No manager request for this user'))
    return user, profile


def _unique_org_name(base):
    """A CKAN organisation name that does not exist yet."""
    base = slugify(base)[:90] or u'organisation'
    candidate = base
    suffix = 1
    while True:
        try:
            tk.get_action('organization_show')({'ignore_auth': True},
                                               {'id': candidate})
        except tk.ObjectNotFound:
            return candidate
        suffix += 1
        candidate = u'%s-%d' % (base, suffix)


def _c4w_organisation_for(ckan_org_id, title, profile, user):
    """The c4w_organisation linked to a CKAN organisation, created if
    absent, so the manager's data links to something on the portal."""
    from ckan.model.meta import Session
    row = (Session.query(db.C4wOrganisation)
           .filter(db.C4wOrganisation.ckan_org_id == ckan_org_id).first())
    if row is not None:
        return row
    row = db.C4wOrganisation(
        slug=db.unique_slug(db.C4wOrganisation, title),
        name=title,
        url=profile.org_url,
        org_type=profile.org_type,
        approved=True,
        ckan_org_id=ckan_org_id,
        created_by=user.id,
        search_text=title,
    )
    Session.add(row)
    Session.flush()
    return row


def _grant_editor(subject_type, subject_id, user_id):
    from ckan.model.meta import Session
    exists = Session.query(db.C4wRelation.id).filter(
        db.C4wRelation.subject_type == subject_type,
        db.C4wRelation.subject_id == subject_id,
        db.C4wRelation.predicate == u'editor',
        db.C4wRelation.object_type == u'user',
        db.C4wRelation.object_id == user_id).first()
    if exists is None:
        Session.add(db.C4wRelation(
            subject_type=subject_type, subject_id=subject_id,
            predicate=u'editor', object_type=u'user', object_id=user_id))


def c4w_manager_approve(context, data_dict):
    """Grant a verified manager their organisation and activate them.

    A NEW organisation makes them its admin; an existing one, an editor.
    The organisation is created before the account is activated, so a
    failure leaves the request pending and the whole action retryable.
    """
    tk.check_access('c4w_manager_approve', context, data_dict)
    from ckan.model.meta import Session
    from ckanext.c4w.logic import mail
    from ckanext.c4w.logic import helpers as c4w_helpers

    user, profile = _resolve_manager(data_dict)
    if profile.manager_decision == u'approved':
        return dict(_profile_dict(profile, user), existed=True)
    if not profile.email_verified:
        raise tk.ValidationError(
            {'email_verified': [tk._('The e-mail is not verified yet')]})

    site = _site_context()
    org_id = profile.ckan_org_id
    if profile.org_choice == u'new' or not org_id:
        title = (profile.org_name_requested or user.fullname
                 or user.name).strip()
        org = tk.get_action('organization_create')(dict(site), {
            'name': _unique_org_name(title),
            'title': title,
            'description': u'',
            'extras': [{'key': 'c4w_org_type',
                        'value': profile.org_type or u''}],
        })
        org_id = org['id']
        org_title = title
        capacity = u'admin'
    else:
        org = tk.get_action('organization_show')(dict(site), {'id': org_id})
        org_title = org.get('title') or org.get('name')
        capacity = u'editor'
    tk.get_action('organization_member_create')(dict(site), {
        'id': org_id, 'username': user.name, 'role': capacity})

    db.ensure_mappers()
    c4w_org = _c4w_organisation_for(org_id, org_title, profile, user)
    _grant_editor(u'organisation', c4w_org.id, user.id)
    for project_id, in (Session.query(db.C4wProject.id)
                        .filter(db.C4wProject.main_organisation_id
                                == c4w_org.id).all()):
        _grant_editor(u'project', project_id, user.id)

    user.activate()
    profile.ckan_org_id = org_id
    profile.c4w_organisation_id = c4w_org.id
    profile.manager_decision = u'approved'
    profile.manager_reviewed_by = _common_user_id(context)
    profile.manager_reviewed_at = _now()
    profile.manager_note = (data_dict.get('note') or u'')[:2000] or None
    profile.modified = _now()
    Session.add(user)
    Session.add(profile)
    Session.commit()

    try:
        login_url = c4w_helpers.c4w_url('login', qualified=True)
    except Exception:
        login_url = None
    mail.send_manager_decision(user.fullname, user.email, True,
                               note=profile.manager_note,
                               login_url=login_url)
    return dict(_profile_dict(profile, user), organisation_title=org_title,
                capacity=capacity)


def c4w_manager_reject(context, data_dict):
    """Record a rejection. The account stays pending; reversible."""
    tk.check_access('c4w_manager_reject', context, data_dict)
    from ckan.model.meta import Session
    from ckanext.c4w.logic import mail

    user, profile = _resolve_manager(data_dict)
    profile.manager_decision = u'rejected'
    profile.manager_reviewed_by = _common_user_id(context)
    profile.manager_reviewed_at = _now()
    profile.manager_note = (data_dict.get('note') or u'')[:2000] or None
    profile.modified = _now()
    Session.add(profile)
    Session.commit()
    mail.send_manager_decision(user.fullname, user.email, False,
                               note=profile.manager_note)
    return _profile_dict(profile, user)


def _common_user_id(context):
    user = context.get('auth_user_obj')
    return getattr(user, 'id', None)


# --------------------------------------------------------------------------- #
# Registries
# --------------------------------------------------------------------------- #

def get_actions():
    return {
        'c4w_register_citizen': c4w_register_citizen,
        'c4w_register_manager': c4w_register_manager,
        'c4w_verify_email': c4w_verify_email,
        'c4w_verification_resend': c4w_verification_resend,
        'c4w_user_profile_show': c4w_user_profile_show,
        'c4w_manager_list': c4w_manager_list,
        'c4w_manager_approve': c4w_manager_approve,
        'c4w_manager_reject': c4w_manager_reject,
    }


@tk.auth_allow_anonymous_access
def _anyone(context, data_dict):
    """The public forms; ``user_create`` is re-checked in the action."""
    return {'success': True}


@tk.auth_allow_anonymous_access
def _signed_in(context, data_dict):
    if _common.is_authenticated(context):
        return {'success': True}
    return {'success': False, 'msg': tk._('Not authorized')}


@tk.auth_allow_anonymous_access
def _sysadmin(context, data_dict):
    if _common.is_sysadmin(context):
        return {'success': True}
    return {'success': False, 'msg': tk._('Not authorized')}


def get_auth_functions():
    return {
        'c4w_register_citizen': _anyone,
        'c4w_register_manager': _anyone,
        'c4w_verify_email': _anyone,
        'c4w_verification_resend': _anyone,
        'c4w_user_profile_show': _signed_in,
        'c4w_manager_list': _sysadmin,
        'c4w_manager_approve': _sysadmin,
        'c4w_manager_reject': _sysadmin,
    }

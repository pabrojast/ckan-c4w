"""Claiming a project: request, review permissions, grant and audit trail."""
import pytest

pytest.importorskip('ckan')
import ckan.plugins.toolkit as tk
from ckanext.c4w import db
from ckanext.c4w.logic import mail
from ckanext.c4w.logic.action import _common, claims
from ckanext.c4w.tests.test_project_proposals import env as base_env  # noqa: F401

REASON = 'I coordinate this monitoring programme since 2019.'


@pytest.fixture
def env(base_env, monkeypatch):
    previous = tk.check_access
    auth = claims.get_auth_functions()

    def check(name, context, data=None):
        if name in auth:
            if not auth[name](context, data or {})['success']:
                raise tk.NotAuthorized('Denied')
        else:
            previous(name, context, data)
    monkeypatch.setattr(tk, 'check_access', check)
    sent = []
    monkeypatch.setattr(mail, 'notify_claim_reviewers',
                        lambda *a: sent.append(('review', a[1].id)) or True)
    monkeypatch.setattr(mail, 'send_claim_decision',
                        lambda user, project, claim:
                        sent.append((claim.status, user.id)) or True)
    project = db.C4wProject(slug='river', name='River', approved=True,
                            hidden=False, extras='{"claimable": true}')
    db.Session.add(project)
    db.Session.commit()
    base_env.project = project
    base_env.sent = sent
    return base_env


def request(env, user='owner', **extra):
    data = dict(project='river', reason=REASON, role_in_project='Coordinator')
    data.update(extra)
    return claims.c4w_project_claim_create(env.context[user], data)


def test_signed_in_user_can_request_once_and_reviewers_are_told(env):
    with pytest.raises(tk.NotAuthorized):
        claims.c4w_project_claim_create({}, dict(project='river', reason=REASON))
    claim = request(env)
    assert claim['status'] == 'pending' and claim['contact_email'] == 'owner@example.org'
    assert claim['history'][0]['action'] == 'requested'
    assert env.sent == [('review', 'owner')]
    with pytest.raises(tk.ValidationError):
        request(env)


@pytest.mark.parametrize('values, field', [
    (dict(reason='too short'), 'reason'),
    (dict(contact_email='not-an-email'), 'contact_email'),
    (dict(evidence_url='javascript:alert(1)'), 'evidence_url'),
    (dict(project='missing'), None),
])
def test_invalid_requests_are_refused(env, values, field):
    error = tk.ObjectNotFound if field is None else tk.ValidationError
    with pytest.raises(error) as raised:
        request(env, **values)
    if field:
        assert field in raised.value.error_dict


def test_hidden_projects_and_existing_editors_cannot_be_claimed(env):
    env.project.hidden = True
    db.Session.commit()
    with pytest.raises(tk.ObjectNotFound):
        request(env)
    env.project.hidden = False
    env.project.created_by = 'owner'
    db.Session.commit()
    with pytest.raises(tk.ValidationError):
        request(env)


@pytest.mark.parametrize('reviewer', ['owner', 'other'])
def test_only_sysadmins_and_content_admins_review(env, reviewer):
    claim = request(env, user='editor')
    for action, data in ((claims.c4w_project_claim_decide,
                          dict(id=claim['id'], decision='approve')),):
        with pytest.raises(tk.NotAuthorized):
            action(env.context[reviewer], data)
    assert not claims._reviewer(env.context[reviewer], {})['success']
    assert claims._reviewer(env.context['admin'], {})['success']
    db.Session.add(db.C4wPortalAdmin(user_id=reviewer, granted_by='admin'))
    db.Session.commit()
    assert claims._reviewer(env.context[reviewer], {})['success']
    result = claims.c4w_project_claim_decide(
        env.context[reviewer], dict(id=claim['id'], decision='approve'))
    assert result['status'] == 'approved'


def test_approval_makes_the_user_an_editor_and_is_final(env):
    claim = request(env, user='editor')
    assert not _common.can_edit('project', env.project, env.context['editor'])
    result = claims.c4w_project_claim_decide(
        env.context['admin'], dict(id=claim['id'], decision='approve'))
    assert result['status'] == 'approved' and result['reviewed_by']['id'] == 'admin'
    assert _common.can_edit('project', env.project, env.context['editor'])
    extras = db.load_extras(env.project.extras)
    assert extras['claimable'] is False and extras['claimed_by'] == ['editor']
    assert ('approved', 'editor') in env.sent
    with pytest.raises(tk.ValidationError):
        claims.c4w_project_claim_decide(
            env.context['admin'], dict(id=claim['id'], decision='reject',
                                       reason='Changed my mind entirely'))
    listing = claims.c4w_project_claim_list(env.context['admin'],
                                            {'status': 'all'})
    assert listing['count'] == 1 and listing['results'][0]['status'] == 'approved'


def test_rejection_needs_a_reason_and_grants_nothing(env):
    claim = request(env, user='editor')
    with pytest.raises(tk.ValidationError):
        claims.c4w_project_claim_decide(
            env.context['admin'], dict(id=claim['id'], decision='reject',
                                       reason='no'))
    result = claims.c4w_project_claim_decide(
        env.context['admin'], dict(id=claim['id'], decision='reject',
                                   reason='We could not verify your role.'))
    assert result['status'] == 'rejected'
    assert result['rejection_reason'] == 'We could not verify your role.'
    assert not _common.can_edit('project', env.project, env.context['editor'])
    assert db.load_extras(env.project.extras)['claimable'] is True
    assert claims.c4w_project_claim_pending_count(env.context['admin'], {}) == 0
    again = request(env, user='editor')
    assert again['status'] == 'pending'


def test_two_people_can_be_approved_for_one_project(env):
    first, second = request(env, user='editor'), request(env, user='other')
    for claim in (first, second):
        claims.c4w_project_claim_decide(
            env.context['admin'], dict(id=claim['id'], decision='approve'))
    assert db.load_extras(env.project.extras)['claimed_by'] == ['editor', 'other']
    assert db.Session.query(db.C4wRelation).filter_by(
        subject_id=env.project.id, predicate='editor').count() == 2

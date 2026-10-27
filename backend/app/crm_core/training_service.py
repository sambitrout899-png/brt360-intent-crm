"""Training policies, assessment tokens and idempotent in-app automation."""
import datetime as dt
import hashlib
import secrets
from fastapi import HTTPException
from sqlalchemy import update
from app.licensing import require_module, enabled
from app.models.auth import User, UserRole
from app.models.org import Department
from app.models.training import *
from app.timeutils import utcnow

INTENTS = [
    {'code': 'TRN-01', 'label': 'Create training program', 'action': 'programs'},
    {'code': 'TRN-02', 'label': 'Publish curriculum revision', 'action': 'revisions'},
    {'code': 'TRN-03', 'label': 'Schedule training session', 'action': 'sessions'},
    {'code': 'TRN-04', 'label': 'Register participant', 'action': 'registrations'},
    {'code': 'TRN-05', 'label': 'Record attendance', 'action': 'attendance'},
    {'code': 'TRN-06', 'label': 'Issue assessment invitations', 'action': 'invitations'},
]

def owned(db, model, tenant_id, record_id):
    row = db.query(model).filter(model.id == record_id, model.tenant_id == tenant_id).first()
    if row is None:
        raise HTTPException(404, 'Record not found')
    return row


def role(db, user):
    require_module(db, user.tenant_id, 'training_management')
    if user.role == UserRole.admin:
        return 'admin'
    member = db.query(TrainingMember).filter_by(tenant_id=user.tenant_id, user_id=user.id).first()
    return member.role if member else 'participant'


def permit(db, user, *roles):
    current = role(db, user)
    if current not in roles:
        raise HTTPException(403, 'Your training role does not allow this action')
    return current


def department(db, tenant_id, department_id):
    if department_id is not None:
        owned(db, Department, tenant_id, department_id)


def session_access(db, user, session_id, write=False):
    session = owned(db, TrainingSession, user.tenant_id, session_id)
    current = role(db, user)
    if current in ('admin', 'hr') or (not write and current == 'executive'):
        from app.crm_core import training_policy
        if current != 'admin' and not training_policy.in_scope(training_policy.scope(db, user), session.location_id, session.department_id):
            raise HTTPException(404, 'Record not found')  # ORG-006: outside the scope, as if absent
        return session
    if current == 'facilitator' and session.facilitator_id == user.id:
        return session
    if not write and current == 'participant' and db.query(TrainingRegistration).filter_by(session_id=session.id, user_id=user.id).first():
        return session
    raise HTTPException(403, 'This session is outside your training access')


def issue(db, registration, phase):
    from app.crm_core import training_assess
    template_based = training_assess.instance(db, registration.session_id, phase)
    if template_based is not None:
        # a session on a version with assessment templates runs the BRD flow
        return training_assess.issue_pre(db, registration) if phase == 'pre' else training_assess._issue(db, registration, template_based)
    prior = db.query(TrainingInvitation).filter_by(registration_id=registration.id, phase=phase).first()
    if prior:
        return prior
    session = owned(db, TrainingSession, registration.tenant_id, registration.session_id)
    token = secrets.token_urlsafe(32)
    invitation = TrainingInvitation(tenant_id=registration.tenant_id, registration_id=registration.id,
        phase=phase, token_hash=hashlib.sha256(token.encode()).hexdigest(),
        expires_at=session.starts_at if phase == 'pre' else session.ends_at + dt.timedelta(days=7))
    db.add(invitation)
    db.flush()
    db.add(TrainingDelivery(tenant_id=registration.tenant_id, invitation_id=invitation.id,
        recipient_id=registration.user_id, kind='invitation', link=f'/assessment/{token}'))
    return invitation


def automate(db, tenant_id):
    require_module(db, tenant_id, 'training_management')
    now = utcnow()
    from app.models.training import TrainingInstance
    template_based = {i.session_id for i in db.query(TrainingInstance).filter_by(tenant_id=tenant_id)}
    for registration in db.query(TrainingRegistration).filter_by(tenant_id=tenant_id).all():
        if registration.attendance == 'cancelled' or registration.session_id in template_based:
            continue  # template-based sessions are run by training_notify.tick
        session = owned(db, TrainingSession, tenant_id, registration.session_id)
        if session.status == 'cancelled':
            continue
        if now < session.starts_at:
            issue(db, registration, 'pre')
        if session.ends_at <= now < session.ends_at + dt.timedelta(days=7) and registration.attendance == 'present':
            issue(db, registration, 'post')
    db.flush()
    for invitation in db.query(TrainingInvitation).filter_by(tenant_id=tenant_id, consumed_at=None, last_reminded_at=None, instance_id=None).all():
        if not now < invitation.expires_at <= now + dt.timedelta(days=1):
            continue
        registration = owned(db, TrainingRegistration, tenant_id, invitation.registration_id)
        session = owned(db, TrainingSession, tenant_id, registration.session_id)
        if registration.attendance == 'cancelled' or session.status == 'cancelled':
            continue
        delivery = db.query(TrainingDelivery).filter_by(invitation_id=invitation.id, kind='invitation').first()
        db.add(TrainingDelivery(tenant_id=tenant_id, invitation_id=invitation.id,
            recipient_id=registration.user_id, kind='reminder', link=delivery.link))
        invitation.last_reminded_at = now
    db.flush()


def resolve_token(db, token):
    invitation = db.query(TrainingInvitation).filter_by(token_hash=hashlib.sha256(token.encode()).hexdigest()).first()
    if invitation is None or invitation.consumed_at is not None or invitation.expires_at <= utcnow():
        raise HTTPException(410, 'Assessment link has expired or was already used')
    if invitation.instance_id is not None:
        raise HTTPException(410, 'Open this assessment from its /p/a/ link')
    require_module(db, invitation.tenant_id, 'training_management')
    registration = owned(db, TrainingRegistration, invitation.tenant_id, invitation.registration_id)
    session = owned(db, TrainingSession, invitation.tenant_id, registration.session_id)
    if registration.attendance == 'cancelled' or session.status == 'cancelled':
        raise HTTPException(410, 'Assessment is no longer available')
    revision = owned(db, TrainingRevision, invitation.tenant_id, session.revision_id)
    return invitation, registration, session, revision


def submit_response(db, token, payload):
    invitation, registration, session, revision = resolve_token(db, token)
    questions = revision.questions
    if set(payload.answers) != {q['key'] for q in questions}:
        raise HTTPException(422, 'Answer every assessment question')
    if any(not 0 <= payload.answers[q['key']] < len(q['options']) for q in questions):
        raise HTTPException(422, 'Invalid answer option')
    claimed = db.execute(update(TrainingInvitation).where(TrainingInvitation.id == invitation.id,
        TrainingInvitation.consumed_at.is_(None)).values(consumed_at=utcnow()))
    if claimed.rowcount != 1:
        raise HTTPException(409, 'Assessment already submitted')
    score = 100 * sum(payload.answers[q['key']] == q['correct'] for q in questions) / len(questions)
    response = TrainingResponse(tenant_id=invitation.tenant_id, session_id=session.id,
        user_id=None if payload.anonymous else registration.user_id, phase=invitation.phase,
        answers=payload.answers, score=score, confidence=payload.confidence, feedback=payload.feedback)
    db.add(response)
    db.flush()
    # Never expose or persist actor/timing metadata with an anonymous response.
    if payload.anonymous:
        db.execute(update(TrainingResponse).where(TrainingResponse.id == response.id).values(
            sys_created_at=None, sys_updated_at=None, sys_created_by=None, sys_updated_by=None,
            sys_created_position_id=None, sys_department_id=None))
    db.commit()
    return {'submitted': True, 'score': score}


def run_due_training(session_factory):
    from app.models.tenant import Tenant
    with session_factory() as db:
        ids = [t.id for t in db.query(Tenant).all() if 'training_management' in enabled(db, t.id)]
    for tenant_id in ids:
        with session_factory() as db:
            try:
                automate(db, tenant_id)
                from app.crm_core import training_notify
                training_notify.tick(db, tenant_id)
                db.commit()
            except Exception:
                db.rollback()
                import logging
                logging.getLogger(__name__).exception('Training automation failed for tenant %s', tenant_id)


def training_action(code):
    """One transaction for the functional action, shared Flow Studio hooks and audit."""
    from functools import wraps
    def decorate(fn):
        @wraps(fn)
        def wrapped(*args, **kwargs):
            import inspect
            import json
            from app.models.intent_log import IntentExecutionLog
            from app.orchestration import flow_engine
            bound = inspect.signature(fn).bind(*args, **kwargs)
            db, user = bound.arguments['db'], bound.arguments['user']
            require_module(db, user.tenant_id, 'training_management')
            entities = {}
            for key, value in bound.arguments.items():
                if key not in ('db', 'user'):
                    if hasattr(value, 'model_dump'):
                        entities.update(value.model_dump(mode='json'))
                    else:
                        entities[key] = value
            log = IntentExecutionLog(tenant_id=user.tenant_id, user_id=user.id, intent_type=code,
                raw_text=next(i['label'] for i in INTENTS if i['code']==code), entities_json=json.dumps(entities),
                context_json='{}', plan_json='[]', confidence=1, status='pending')
            try:
                db.add(log); db.flush()
                db.info["training_execution_id"] = log.id
                ctx = {'entities': entities, 'intent_type': code}
                trace = []
                for flow in flow_engine.flows_for_intent(db, user.tenant_id, code, 'before'):
                    trace.append(flow_engine.run_flow(db, user.tenant_id, log.id, flow, ctx))
                result = fn(*args, **kwargs)
                ctx['result'] = result
                for flow in flow_engine.flows_for_intent(db, user.tenant_id, code, 'after'):
                    trace.append(flow_engine.run_flow(db, user.tenant_id, log.id, flow, ctx))
                log.status='executed'; log.completed_at=utcnow(); log.plan_json=json.dumps(trace, default=str)
                db.commit()
                return result
            except ValueError as exc:
                db.rollback()
                raise HTTPException(422, str(exc))
            except Exception:
                db.rollback()
                raise
            finally:
                db.info.pop("training_execution_id", None)
        return wrapped
    return decorate

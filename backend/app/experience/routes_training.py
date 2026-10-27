"""Training APIs and functional intents share the same validated operations."""
from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import ValidationError
from sqlalchemy import func
from sqlalchemy.orm import Session
from app.auth import get_current_user
from app.db import get_db
from app.models.org import Department
from app.models.auth import User
from app.models.training import *
from app.schemas.training import *
from app.crm_core import training_service as svc
from app.licensing import license_guard
from app.timeutils import utcnow

router = APIRouter(prefix='/api/training', tags=['training'], dependencies=[Depends(license_guard('training_management'))])
public_router = APIRouter(prefix='/api/assessments', tags=['assessments'])


def serialize(row, exclude=()):
    return {c.name: getattr(row, c.name) for c in row.__table__.columns
            if not c.name.startswith('sys_') and c.name != 'brt_id' and c.name not in exclude}

@router.get('/workspace')
def workspace(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    current = svc.role(db, user)
    programs = db.query(TrainingProgram).filter_by(tenant_id=user.tenant_id).all()
    revisions = db.query(TrainingRevision).filter_by(tenant_id=user.tenant_id).all()
    sessions = db.query(TrainingSession).filter_by(tenant_id=user.tenant_id).all()
    registrations = db.query(TrainingRegistration).filter_by(tenant_id=user.tenant_id).all()
    if current in ('hr', 'executive'):
        from app.crm_core import training_policy
        limits = training_policy.scope(db, user)
        sessions = [s for s in sessions if training_policy.in_scope(limits, s.location_id, s.department_id)]
    if current == 'facilitator':
        sessions = [s for s in sessions if s.facilitator_id == user.id]
    elif current == 'participant':
        sessions = [s for s in sessions if s.id in {r.session_id for r in registrations if r.user_id == user.id}
                    or (s.status == 'scheduled' and s.starts_at > utcnow())]
    ids = {s.id for s in sessions}
    registrations = [r for r in registrations if r.session_id in ids and (current != 'participant' or r.user_id == user.id)]
    deliveries = db.query(TrainingDelivery).filter_by(tenant_id=user.tenant_id, recipient_id=user.id).order_by(TrainingDelivery.id.desc()).all()
    return {'role': current, 'user_id': user.id, 'intents': svc.INTENTS,
        'programs': [serialize(p) for p in programs],
        'revisions': [dict(serialize(r, ('questions',)), questions=[{k: v for k, v in q.items() if k != 'correct'} for q in r.questions]) for r in revisions],
        'sessions': [serialize(s) for s in sessions], 'registrations': [serialize(r) for r in registrations],
        'members': [serialize(m) for m in db.query(TrainingMember).filter_by(tenant_id=user.tenant_id).all()] if current in ('admin', 'hr') else [],
        'departments': [{'id': d.id, 'name': d.name} for d in db.query(Department).filter_by(tenant_id=user.tenant_id, is_active=True).all()],
        'users': [{'id': u.id, 'name': u.name} for u in db.query(User).filter_by(tenant_id=user.tenant_id, is_active=True).all()] if current in ('admin', 'hr') else [],
        'certificates': [serialize(c) for c in db.query(TrainingCertificate).filter_by(tenant_id=user.tenant_id).all()] if current in ('admin', 'hr') else [],
        'inbox': [serialize(d, ('invitation_id',)) for d in deliveries]}

@router.put('/members')
def member(payload: MemberIn, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    svc.permit(db, user, 'admin')
    svc.owned(db, User, user.tenant_id, payload.user_id)
    svc.department(db, user.tenant_id, payload.department_id)
    row = db.query(TrainingMember).filter_by(tenant_id=user.tenant_id, user_id=payload.user_id).first()
    if row is None:
        row = TrainingMember(tenant_id=user.tenant_id, **payload.model_dump()); db.add(row)
    else:
        for key, value in payload.model_dump().items(): setattr(row, key, value)
    db.commit()
    return serialize(row)

@router.post('/programs', status_code=201)
@svc.training_action('TRN-01')
def create_program(payload: ProgramIn, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    svc.permit(db, user, 'admin', 'hr')
    row = TrainingProgram(tenant_id=user.tenant_id, name=payload.name, description=payload.description)
    from app.crm_core import page_service
    problems = page_service.validate(db, user.tenant_id, 'training_program', None, payload.details,
                                    execution_id=db.info.get('training_execution_id'))
    if problems:
        raise HTTPException(422, problems)
    db.add(row); db.flush()
    page_service.save_values(db, user.tenant_id, 'training_program', row, payload.details)
    revision = TrainingRevision(tenant_id=user.tenant_id, program_id=row.id, version=1,
        curriculum=payload.curriculum, questions=[q.model_dump() for q in payload.questions])
    db.add(revision); db.flush()
    return {'id': row.id, 'revision_id': revision.id}

@router.post('/programs/{program_id}/revisions', status_code=201)
@svc.training_action('TRN-02')
def revision(program_id: int, payload: RevisionIn, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    svc.permit(db, user, 'admin', 'hr')
    program = db.query(TrainingProgram).filter_by(id=program_id, tenant_id=user.tenant_id).with_for_update().first()
    if program is None:
        raise HTTPException(404, 'Record not found')
    version = db.query(func.max(TrainingRevision.version)).filter_by(program_id=program_id).scalar() or 0
    row = TrainingRevision(tenant_id=user.tenant_id, program_id=program_id, version=version + 1,
        curriculum=payload.curriculum, questions=[q.model_dump() for q in payload.questions])
    db.add(row); db.flush()
    return serialize(row)

@router.post('/sessions', status_code=201)
@svc.training_action('TRN-03')
def schedule(payload: SessionIn, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    svc.permit(db, user, 'admin', 'hr')
    version = svc.owned(db, TrainingRevision, user.tenant_id, payload.revision_id)
    if (version.state or 'published') != 'published':
        raise HTTPException(422, 'Sessions can only be created from a published program version (SES-001)')
    facilitator = svc.owned(db, User, user.tenant_id, payload.facilitator_id)
    if not facilitator.is_active or svc.role(db, facilitator) not in ('admin', 'facilitator'):
        raise HTTPException(422, 'Assign an active facilitator')
    svc.department(db, user.tenant_id, payload.department_id)
    if payload.starts_at <= utcnow():
        raise HTTPException(422, 'Schedule a future session')
    conflict = db.query(TrainingSession).filter(TrainingSession.tenant_id == user.tenant_id,
        TrainingSession.facilitator_id == payload.facilitator_id, TrainingSession.status != 'cancelled',
        TrainingSession.starts_at < payload.ends_at, TrainingSession.ends_at > payload.starts_at).first()
    if conflict:
        raise HTTPException(409, 'Facilitator already has a session at this time')
    import secrets
    from app.crm_core import training_delivery, training_notify
    row = TrainingSession(tenant_id=user.tenant_id, checkin_code=secrets.token_urlsafe(12), **payload.model_dump())
    db.add(row); db.flush()
    training_delivery.ensure_instances(db, row)  # assessments, when the version links templates
    training_notify.plan_session(db, row)
    return serialize(row)

@router.post('/sessions/{session_id}/cancel')
def cancel(session_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    svc.permit(db, user, 'admin', 'hr')
    row = svc.session_access(db, user, session_id, True)
    row.status = 'cancelled'
    from app.crm_core import training_notify
    training_notify.on_cancel(db, row)  # BR-WFA-01
    db.commit()
    return serialize(row)

@router.post('/sessions/{session_id}/registrations', status_code=201)
@svc.training_action('TRN-04')
def register(session_id: int, payload: RegistrationIn, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    current = svc.role(db, user)
    if current not in ('admin', 'hr') and (current != 'participant' or payload.user_id != user.id):
        raise HTTPException(403, 'Register yourself or ask HR to register participants')
    session = db.query(TrainingSession).filter_by(id=session_id, tenant_id=user.tenant_id).with_for_update().first()
    if session is None:
        raise HTTPException(404, 'Record not found')
    participant = svc.owned(db, User, user.tenant_id, payload.user_id)
    if not participant.is_active:
        raise HTTPException(422, 'Participant is inactive')
    if session.status != 'scheduled' or session.starts_at <= utcnow():
        raise HTTPException(409, 'Registration is closed')
    if db.query(TrainingRegistration).filter_by(session_id=session_id, user_id=payload.user_id).first():
        raise HTTPException(409, 'Participant is already registered')
    count = db.query(TrainingRegistration).filter(TrainingRegistration.session_id == session_id,
        TrainingRegistration.attendance != 'cancelled').count()
    if count >= session.capacity:
        raise HTTPException(409, 'Session is full')
    row = TrainingRegistration(tenant_id=user.tenant_id, session_id=session_id, user_id=payload.user_id,
                               status='registered', source='self' if payload.user_id == user.id else 'nominated', registered_at=utcnow())
    db.add(row); db.flush(); svc.issue(db, row, 'pre'); db.flush()
    from app.crm_core import training_notify
    training_notify.on_registered(db, row)
    return serialize(row)

@router.put('/registrations/{registration_id}/attendance')
@svc.training_action('TRN-05')
def attendance(registration_id: int, payload: AttendanceIn, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    row = svc.owned(db, TrainingRegistration, user.tenant_id, registration_id)
    svc.session_access(db, user, row.session_id, True)
    row.attendance = payload.attendance
    # keep the SES-006 status and the SES-008 attendance log in step
    row.status = {'present': 'attended', 'late': 'attended', 'absent': 'no_show', 'cancelled': 'cancelled'}.get(payload.attendance, 'registered')
    if payload.attendance in ('present', 'late', 'absent'):
        from app.models.training import TrainingAttendance
        db.add(TrainingAttendance(tenant_id=user.tenant_id, registration_id=row.id, status=payload.attendance,
                                  method='roster', recorded_by=user.id, recorded_at=utcnow()))
        if payload.attendance == 'late':
            row.attendance = 'present'
    db.flush(); svc.automate(db, user.tenant_id); db.flush()
    session = db.get(TrainingSession, row.session_id)
    if session.status in ('completed', 'closed'):
        from app.crm_core import training_assess
        training_assess.issue_post(db, session)
    return serialize(row)

@router.post('/certificates', status_code=201)
def certificate(payload: CertificateIn, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    svc.permit(db, user, 'admin', 'hr')
    svc.owned(db, User, user.tenant_id, payload.user_id)
    row = TrainingCertificate(tenant_id=user.tenant_id, **payload.model_dump())
    db.add(row); db.commit()
    return serialize(row)

@router.post('/automation/run')
@svc.training_action('TRN-06')
def automate(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    svc.permit(db, user, 'admin', 'hr')
    svc.automate(db, user.tenant_id); db.flush()
    return {'status': 'processed'}

@router.get('/sessions/{session_id}/outcomes')
def outcomes(session_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    session = svc.session_access(db, user, session_id)
    current = svc.role(db, user)
    if current == 'participant':
        raise HTTPException(403, 'Outcome reporting requires a staff role')
    revision = svc.owned(db, TrainingRevision, user.tenant_id, session.revision_id)
    rows = db.query(TrainingResponse).filter_by(tenant_id=user.tenant_id, session_id=session_id).all()
    def average(items, attr):
        return round(sum(getattr(r, attr) for r in items) / len(items), 2) if items else None
    phases = {phase: [r for r in rows if r.phase == phase] for phase in ('pre', 'post')}
    summary = {p: {'responses': len(rs), 'knowledge': average(rs, 'score'), 'confidence': average(rs, 'confidence')} for p, rs in phases.items()}
    matched = []
    for uid in {r.user_id for r in rows if r.user_id is not None}:
        pre = next((r for r in phases['pre'] if r.user_id == uid), None)
        post = next((r for r in phases['post'] if r.user_id == uid), None)
        if pre and post:
            matched.append({'user_id': uid, 'knowledge_change': round(post.score-pre.score, 2), 'confidence_change': post.confidence-pre.confidence})
    by_outcome = {}
    for outcome in {q['outcome'] for q in revision.questions}:
        qs = [q for q in revision.questions if q['outcome'] == outcome]
        by_outcome[outcome] = {p: round(100*sum(r.answers.get(q['key']) == q['correct'] for r in rs for q in qs)/(len(rs)*len(qs)), 2) if rs else None for p, rs in phases.items()}
    return {'phases': summary, 'matched_participants': matched if current != 'executive' else [],
        'matched_count': len(matched),
        'matched_knowledge_change': round(sum(r['knowledge_change'] for r in matched)/len(matched), 2) if matched else None,
        'matched_confidence_change': round(sum(r['confidence_change'] for r in matched)/len(matched), 2) if matched else None,
        'outcomes': by_outcome,
        'feedback': [{'phase': r.phase, 'text': r.feedback, 'anonymous': r.user_id is None, 'user_id': r.user_id if current != 'executive' else None} for r in rows if r.feedback],
        'note': 'Phase averages include anonymous responses. Individual comparisons use identified pre/post pairs only.'}

@router.post('/intents')
def execute(payload: TrainingIntentIn, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    data = dict(payload.entities)
    try:
        if payload.code == 'TRN-01': return create_program(ProgramIn(**data), db, user)
        if payload.code == 'TRN-02': return revision(int(data.pop('program_id')), RevisionIn(**data), db, user)
        if payload.code == 'TRN-03': return schedule(SessionIn(**data), db, user)
        if payload.code == 'TRN-04': return register(int(data.pop('session_id')), RegistrationIn(**data), db, user)
        if payload.code == 'TRN-05': return attendance(int(data.pop('registration_id')), AttendanceIn(**data), db, user)
        if payload.code == 'TRN-06': return automate(db, user)
    except (ValidationError, ValueError, KeyError) as exc:
        raise HTTPException(422, str(exc))
    raise HTTPException(422, 'Unknown training intent')

@public_router.get('/{token}')
def assessment(token: str, response: Response, db: Session = Depends(get_db)):
    response.headers['Cache-Control'] = 'no-store'
    invitation, registration, session, revision = svc.resolve_token(db, token)
    return {'title': session.title, 'phase': invitation.phase,
        'questions': [{k: v for k, v in q.items() if k != 'correct'} for q in revision.questions]}

@public_router.post('/{token}')
def submit(token: str, payload: ResponseIn, response: Response, db: Session = Depends(get_db)):
    response.headers['Cache-Control'] = 'no-store'
    return svc.submit_response(db, token, payload)

@public_router.get('/{token}/qr')
def qr(token: str, db: Session = Depends(get_db)):
    # Encode the browser route on the configured public origin; never trust Host.
    import io
    import qrcode
    from app.config import get_settings
    svc.resolve_token(db, token)
    origin = get_settings().training_public_origin.rstrip('/')
    if not origin:
        raise HTTPException(503, 'Set TRAINING_PUBLIC_ORIGIN to enable shareable QR codes')
    stream = io.BytesIO()
    qrcode.make(f'{origin}/assessment/{token}').save(stream, format='PNG')
    return Response(stream.getvalue(), media_type='image/png', headers={'Cache-Control': 'no-store'})

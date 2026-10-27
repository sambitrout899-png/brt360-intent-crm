"""Training domain; every root record is explicitly organization scoped.

The BRD (Training Module v1.0) additions are additive: every column added to
an original table is nullable or defaulted, so rows written before it keep
working (a revision with no templates runs the original single-form
assessment; a session created the original way stays 'scheduled')."""
import datetime as dt
from sqlalchemy import Boolean, Float, ForeignKey, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from app.db import Base
from app.timeutils import utcnow

class TrainingMember(Base):
    __tablename__ = 'training_members'
    __table_args__ = (UniqueConstraint('tenant_id', 'user_id'),)
    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey('tenants.id'))
    user_id: Mapped[int] = mapped_column(ForeignKey('users.id'))
    role: Mapped[str] = mapped_column(String(30))
    department_id: Mapped[int | None] = mapped_column(ForeignKey('departments.id'))
    location: Mapped[str] = mapped_column(String(200), default='')
    # ORG-005: further roles (the effective permission is the union) and the
    # locations/departments an HR or Executive role is limited to (ORG-006).
    roles: Mapped[list | None] = mapped_column(JSON, nullable=True)
    scope_location_ids: Mapped[list | None] = mapped_column(JSON, nullable=True)
    scope_department_ids: Mapped[list | None] = mapped_column(JSON, nullable=True)
    # FAC-001 facilitator profile: bio, expertise, languages, is_external, vendor
    profile: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    phone: Mapped[str | None] = mapped_column(String(40), nullable=True)

class TrainingProgram(Base):
    __tablename__ = 'training_programs'
    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey('tenants.id'))
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(String(4000), default='')
    # PRG-001 / PRG-006
    code: Mapped[str | None] = mapped_column(String(50), nullable=True)
    category: Mapped[str | None] = mapped_column(String(100), nullable=True)
    audience: Mapped[str | None] = mapped_column(String(200), nullable=True)
    delivery_mode: Mapped[str | None] = mapped_column(String(20), nullable=True)
    duration_hours: Mapped[float | None] = mapped_column(Float, nullable=True)
    objectives: Mapped[list | None] = mapped_column(JSON, nullable=True)
    status: Mapped[str | None] = mapped_column(String(20), nullable=True)
    required_certification_id: Mapped[int | None] = mapped_column(ForeignKey('training_certifications.id'), nullable=True)

class TrainingRevision(Base):
    """A program version (PRG-003): draft -> published -> retired. Rows from
    before the BRD have no state and count as published."""
    __tablename__ = 'training_revisions'
    __table_args__ = (UniqueConstraint('program_id', 'version'),)
    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey('tenants.id'))
    program_id: Mapped[int] = mapped_column(ForeignKey('training_programs.id'))
    version: Mapped[int]
    curriculum: Mapped[list] = mapped_column(JSON)
    questions: Mapped[list] = mapped_column(JSON)
    created_at: Mapped[dt.datetime] = mapped_column(default=utcnow)
    version_label: Mapped[str | None] = mapped_column(String(20), nullable=True)
    state: Mapped[str | None] = mapped_column(String(20), nullable=True)
    # PRG-002 modules: [{title, objectives, duration_min, materials, notes}] in order
    modules: Mapped[list | None] = mapped_column(JSON, nullable=True)
    # learning objectives: [{code, text}] (the learning_objective entity)
    objectives: Mapped[list | None] = mapped_column(JSON, nullable=True)
    duration_hours: Mapped[float | None] = mapped_column(Float, nullable=True)
    pre_template_id: Mapped[int | None] = mapped_column(ForeignKey('training_templates.id'), nullable=True)
    post_template_id: Mapped[int | None] = mapped_column(ForeignKey('training_templates.id'), nullable=True)
    feedback_template_id: Mapped[int | None] = mapped_column(ForeignKey('training_templates.id'), nullable=True)
    assessments_waived: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    waive_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)
    required_certification_id: Mapped[int | None] = mapped_column(ForeignKey('training_certifications.id'), nullable=True)
    published_at: Mapped[dt.datetime | None] = mapped_column(nullable=True)
    retired_at: Mapped[dt.datetime | None] = mapped_column(nullable=True)

class TrainingSession(Base):
    __tablename__ = 'training_sessions'
    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey('tenants.id'))
    revision_id: Mapped[int] = mapped_column(ForeignKey('training_revisions.id'))
    facilitator_id: Mapped[int] = mapped_column(ForeignKey('users.id'))
    title: Mapped[str] = mapped_column(String(200))
    location: Mapped[str] = mapped_column(String(200), default='')
    department_id: Mapped[int | None] = mapped_column(ForeignKey('departments.id'))
    starts_at: Mapped[dt.datetime]
    ends_at: Mapped[dt.datetime]
    capacity: Mapped[int] = mapped_column(default=100)
    status: Mapped[str] = mapped_column(String(30), default='scheduled')
    # SES-001 .. SES-009
    location_id: Mapped[int | None] = mapped_column(ForeignKey('training_locations.id'), nullable=True)
    virtual_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    room: Mapped[str | None] = mapped_column(String(100), nullable=True)
    timezone: Mapped[str | None] = mapped_column(String(60), nullable=True)
    registration_deadline: Mapped[dt.datetime | None] = mapped_column(nullable=True)
    waitlist_enabled: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    feedback_mode: Mapped[str | None] = mapped_column(String(20), nullable=True)
    co_facilitator_ids: Mapped[list | None] = mapped_column(JSON, nullable=True)
    checkin_code: Mapped[str | None] = mapped_column(String(40), nullable=True, unique=True)
    status_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)
    override_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)
    # per-session overrides of the org's windows and offsets
    settings: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

class TrainingRegistration(Base):
    __tablename__ = 'training_registrations'
    __table_args__ = (UniqueConstraint('session_id', 'user_id'), UniqueConstraint('session_id', 'participant_id'))
    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey('tenants.id'))
    session_id: Mapped[int] = mapped_column(ForeignKey('training_sessions.id'))
    # a user with a login, or (SES-007) an external participant without one
    user_id: Mapped[int | None] = mapped_column(ForeignKey('users.id'), nullable=True)
    attendance: Mapped[str] = mapped_column(String(30), default='registered')
    participant_id: Mapped[int | None] = mapped_column(ForeignKey('training_participants.id'), nullable=True)
    # SES-006: nominated / registered / waitlisted / cancelled / attended / no_show
    status: Mapped[str | None] = mapped_column(String(20), nullable=True)
    source: Mapped[str | None] = mapped_column(String(20), nullable=True)
    waitlist_position: Mapped[int | None] = mapped_column(nullable=True)
    registered_at: Mapped[dt.datetime | None] = mapped_column(nullable=True)

class TrainingCertificate(Base):
    """A facilitator's certification record (FAC-002)."""
    __tablename__ = 'training_certificates'
    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey('tenants.id'))
    user_id: Mapped[int] = mapped_column(ForeignKey('users.id'))
    name: Mapped[str] = mapped_column(String(200))
    expires_at: Mapped[dt.datetime]
    quality_notes: Mapped[str] = mapped_column(String(4000), default='')
    certification_id: Mapped[int | None] = mapped_column(ForeignKey('training_certifications.id'), nullable=True)
    issuer: Mapped[str | None] = mapped_column(String(200), nullable=True)
    issued_on: Mapped[dt.datetime | None] = mapped_column(nullable=True)
    evidence_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    revoked: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    # FAC-005: thresholds already alerted, so each crossing alerts once
    alerted: Mapped[list | None] = mapped_column(JSON, nullable=True)

class TrainingInvitation(Base):
    __tablename__ = 'training_invitations'
    __table_args__ = (UniqueConstraint('registration_id', 'phase'),)
    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey('tenants.id'))
    registration_id: Mapped[int] = mapped_column(ForeignKey('training_registrations.id'))
    phase: Mapped[str] = mapped_column(String(10))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    expires_at: Mapped[dt.datetime]
    consumed_at: Mapped[dt.datetime | None]
    last_reminded_at: Mapped[dt.datetime | None]
    # Secure link retained only in the private delivery outbox, never in reports.
    instance_id: Mapped[int | None] = mapped_column(ForeignKey('training_instances.id'), nullable=True)
    # ASM-022: completion of an anonymous form is this flag only - never linked to the response
    has_submitted: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    sent_at: Mapped[dt.datetime | None] = mapped_column(nullable=True)
    # ASM-015 autosave (identifiable forms only; anonymous drafts stay on the device)
    draft: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    started_at: Mapped[dt.datetime | None] = mapped_column(nullable=True)
    # ASM-013: the participant's route in after identifying on the session QR
    alt_token_hash: Mapped[str | None] = mapped_column(String(64), nullable=True, unique=True)
    consented_at: Mapped[dt.datetime | None] = mapped_column(nullable=True)
    # ASM-011: the link token is HMAC(server key, nonce) - only its hash is
    # stored, and the server alone can re-derive it for reminders (WFA-008);
    # a new nonce regenerates the link.
    token_nonce: Mapped[str | None] = mapped_column(String(64), nullable=True)

class TrainingResponse(Base):
    __tablename__ = 'training_responses'
    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey('tenants.id'))
    session_id: Mapped[int] = mapped_column(ForeignKey('training_sessions.id'))
    user_id: Mapped[int | None] = mapped_column(ForeignKey('users.id'))
    phase: Mapped[str] = mapped_column(String(10))
    answers: Mapped[dict] = mapped_column(JSON)
    score: Mapped[float]
    confidence: Mapped[int]
    feedback: Mapped[str] = mapped_column(String(4000), default='')
    # Intentionally no invitation FK, timestamp, or user attribution on anonymous feedback.
    # Identifiable responses to a template-based assessment (ASM-016, ANL-001):
    instance_id: Mapped[int | None] = mapped_column(ForeignKey('training_instances.id'), nullable=True)
    invitation_id: Mapped[int | None] = mapped_column(ForeignKey('training_invitations.id'), nullable=True)
    participant_id: Mapped[int | None] = mapped_column(ForeignKey('training_participants.id'), nullable=True)
    submitted_at: Mapped[dt.datetime | None] = mapped_column(nullable=True)
    confidence_avg: Mapped[float | None] = mapped_column(Float, nullable=True)
    late: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    # per answer: {item_key: {is_correct, points_awarded}}
    scored: Mapped[dict | None] = mapped_column(JSON, nullable=True)

class TrainingDelivery(Base):
    __tablename__ = 'training_deliveries'
    __table_args__ = (UniqueConstraint('invitation_id', 'kind'),)
    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey('tenants.id'))
    invitation_id: Mapped[int] = mapped_column(ForeignKey('training_invitations.id'))
    recipient_id: Mapped[int] = mapped_column(ForeignKey('users.id'))
    kind: Mapped[str] = mapped_column(String(30))
    link: Mapped[str] = mapped_column(String(500))
    status: Mapped[str] = mapped_column(String(30), default='in_app')
    created_at: Mapped[dt.datetime] = mapped_column(default=utcnow)


# ---------------------------------------------------------------- BRD v1.0 additions


class TrainingLocation(Base):
    """ORG-002: sessions are only scheduled at active locations of the same org."""
    __tablename__ = 'training_locations'
    __table_args__ = (UniqueConstraint('tenant_id', 'code'),)
    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey('tenants.id'), index=True)
    name: Mapped[str] = mapped_column(String(200))
    code: Mapped[str] = mapped_column(String(50))
    address: Mapped[str] = mapped_column(String(1000), default='')
    timezone: Mapped[str] = mapped_column(String(60), default='UTC')
    status: Mapped[str] = mapped_column(String(20), default='active')


class TrainingParticipant(Base):
    """A person in the training directory without a login (SES-007, ORG-004
    import). Erasure (ORG-011) replaces the personal fields with a token."""
    __tablename__ = 'training_participants'
    __table_args__ = (UniqueConstraint('tenant_id', 'email'),)
    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey('tenants.id'), index=True)
    name: Mapped[str] = mapped_column(String(200))
    email: Mapped[str] = mapped_column(String(255))
    phone: Mapped[str] = mapped_column(String(40), default='')
    employee_id: Mapped[str] = mapped_column(String(60), default='')
    organization: Mapped[str] = mapped_column(String(200), default='')
    location_id: Mapped[int | None] = mapped_column(ForeignKey('training_locations.id'), nullable=True)
    department_id: Mapped[int | None] = mapped_column(ForeignKey('departments.id'), nullable=True)
    manager: Mapped[str] = mapped_column(String(200), default='')
    status: Mapped[str] = mapped_column(String(20), default='active')
    email_invalid: Mapped[bool] = mapped_column(Boolean, default=False)
    unsubscribed: Mapped[bool] = mapped_column(Boolean, default=False)
    erased_at: Mapped[dt.datetime | None] = mapped_column(nullable=True)


class TrainingCertification(Base):
    """A certification a program can require (PRG-006, BR-FAC-01)."""
    __tablename__ = 'training_certifications'
    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey('tenants.id'), index=True)
    name: Mapped[str] = mapped_column(String(200))
    issuer: Mapped[str] = mapped_column(String(200), default='')
    validity_months: Mapped[int | None] = mapped_column(nullable=True)
    program_ids: Mapped[list] = mapped_column(JSON, default=list)


class TrainingTemplate(Base):
    """An assessment template (ASM-001): pre / post / feedback, versioned like
    programs. Questions: [{item_key, section, type, text, options,
    correct_options, points, measure, objective, required}]."""
    __tablename__ = 'training_templates'
    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey('tenants.id'), index=True)
    type: Mapped[str] = mapped_column(String(20))
    name: Mapped[str] = mapped_column(String(200))
    version_label: Mapped[str] = mapped_column(String(20), default='1.0')
    family_id: Mapped[int | None] = mapped_column(nullable=True)
    state: Mapped[str] = mapped_column(String(20), default='draft')
    settings: Mapped[dict] = mapped_column(JSON, default=dict)
    sections: Mapped[list] = mapped_column(JSON, default=list)
    questions: Mapped[list] = mapped_column(JSON, default=list)


class TrainingInstance(Base):
    """One assessment of one session (scheduled -> open -> closed by its window)."""
    __tablename__ = 'training_instances'
    __table_args__ = (UniqueConstraint('session_id', 'type'),)
    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey('tenants.id'), index=True)
    session_id: Mapped[int] = mapped_column(ForeignKey('training_sessions.id'))
    template_id: Mapped[int] = mapped_column(ForeignKey('training_templates.id'))
    type: Mapped[str] = mapped_column(String(20))
    opens_at: Mapped[dt.datetime | None] = mapped_column(nullable=True)
    closes_at: Mapped[dt.datetime | None] = mapped_column(nullable=True)
    anonymous: Mapped[bool] = mapped_column(Boolean, default=False)
    closed_handled: Mapped[bool] = mapped_column(Boolean, default=False)


class TrainingAnonymousResponse(Base):
    """ASM-021: no participant, no token, no invitation, no IP; the time is
    rounded down to the hour. Rows are written with a random id and blank
    system fields (training_assess.save_anonymous), so neither their order nor
    their stamps can be lined up against invitations."""
    __tablename__ = 'training_anonymous_responses'
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=False)
    tenant_id: Mapped[int] = mapped_column(ForeignKey('tenants.id'), index=True)
    instance_id: Mapped[int] = mapped_column(ForeignKey('training_instances.id'))
    submitted_hour: Mapped[dt.datetime]
    answers: Mapped[dict] = mapped_column(JSON)


class TrainingAttendance(Base):
    """SES-008: each mark stores its method, when, and who recorded it."""
    __tablename__ = 'training_attendance'
    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey('tenants.id'), index=True)
    registration_id: Mapped[int] = mapped_column(ForeignKey('training_registrations.id'))
    status: Mapped[str] = mapped_column(String(20))
    method: Mapped[str] = mapped_column(String(20))
    recorded_by: Mapped[int | None] = mapped_column(nullable=True)
    recorded_at: Mapped[dt.datetime] = mapped_column(default=utcnow)


class TrainingQaReview(Base):
    """FAC-003: rubric scores 1-5 per criterion."""
    __tablename__ = 'training_qa_reviews'
    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey('tenants.id'), index=True)
    session_id: Mapped[int] = mapped_column(ForeignKey('training_sessions.id'))
    facilitator_id: Mapped[int] = mapped_column(ForeignKey('users.id'))
    reviewer_id: Mapped[int] = mapped_column(ForeignKey('users.id'))
    review_date: Mapped[dt.datetime] = mapped_column(default=utcnow)
    scores: Mapped[dict] = mapped_column(JSON, default=dict)
    overall_rating: Mapped[float] = mapped_column(Float)
    comments: Mapped[str] = mapped_column(String(4000), default='')
    actions: Mapped[str] = mapped_column(String(4000), default='')


class TrainingOutcome(Base):
    """outcome_result: derived and recomputable (ANL-001..003). participant_key
    is 'u<user id>' or 'p<participant id>'; blank for a session aggregate."""
    __tablename__ = 'training_outcomes'
    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey('tenants.id'), index=True)
    session_id: Mapped[int] = mapped_column(ForeignKey('training_sessions.id'), index=True)
    participant_key: Mapped[str] = mapped_column(String(20), default='')
    objective_code: Mapped[str] = mapped_column(String(50), default='')
    dimension: Mapped[str] = mapped_column(String(20))
    pre_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    post_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    delta: Mapped[float | None] = mapped_column(Float, nullable=True)
    normalized_gain: Mapped[float | None] = mapped_column(Float, nullable=True)
    computed_at: Mapped[dt.datetime] = mapped_column(default=utcnow)


class TrainingNotifyTemplate(Base):
    """WFA-003: per org and event (tenant_id NULL = platform default)."""
    __tablename__ = 'training_notify_templates'
    __table_args__ = (UniqueConstraint('tenant_id', 'event', 'channel'),)
    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int | None] = mapped_column(ForeignKey('tenants.id'), nullable=True)
    event: Mapped[str] = mapped_column(String(40))
    channel: Mapped[str] = mapped_column(String(20), default='email')
    subject: Mapped[str] = mapped_column(String(300), default='')
    body_html: Mapped[str] = mapped_column(Text, default='')
    body_text: Mapped[str] = mapped_column(Text, default='')


class TrainingNotification(Base):
    """One planned or sent message (WFA-001..010). The rendered message holds
    a personal link, so - like the delivery outbox - it is never reported
    beyond the session's automation log."""
    __tablename__ = 'training_notifications'
    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey('tenants.id'), index=True)
    session_id: Mapped[int | None] = mapped_column(ForeignKey('training_sessions.id'), nullable=True, index=True)
    registration_id: Mapped[int | None] = mapped_column(ForeignKey('training_registrations.id'), nullable=True)
    event: Mapped[str] = mapped_column(String(40))
    channel: Mapped[str] = mapped_column(String(20), default='email')
    recipient_user_id: Mapped[int | None] = mapped_column(ForeignKey('users.id'), nullable=True)
    recipient_participant_id: Mapped[int | None] = mapped_column(ForeignKey('training_participants.id'), nullable=True)
    recipient_email: Mapped[str] = mapped_column(String(255), default='')
    scheduled_at: Mapped[dt.datetime]
    sent_at: Mapped[dt.datetime | None] = mapped_column(nullable=True)
    # scheduled / queued / sent / delivered / bounced / failed / opened / clicked / skipped / cancelled
    status: Mapped[str] = mapped_column(String(20), default='scheduled')
    attempts: Mapped[int] = mapped_column(default=0)
    next_attempt_at: Mapped[dt.datetime | None] = mapped_column(nullable=True)
    idempotency_key: Mapped[str] = mapped_column(String(200), unique=True)
    provider_message_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    error: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    subject: Mapped[str | None] = mapped_column(String(300), nullable=True)
    body_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    body_html: Mapped[str | None] = mapped_column(Text, nullable=True)
    skip_reason: Mapped[str | None] = mapped_column(String(200), nullable=True)


class TrainingSettings(Base):
    """ORG-010 org-level training settings (versioned through the audit log)."""
    __tablename__ = 'training_settings'
    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey('tenants.id'), unique=True)
    data: Mapped[dict] = mapped_column(JSON, default=dict)
    version: Mapped[int] = mapped_column(default=1)


class TrainingAudit(Base):
    """ORG-012 / NFR-011: append-only (updates and deletes are refused)."""
    __tablename__ = 'training_audit'
    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey('tenants.id'), index=True)
    actor_id: Mapped[int | None] = mapped_column(nullable=True)
    action: Mapped[str] = mapped_column(String(60))
    entity: Mapped[str] = mapped_column(String(60))
    entity_id: Mapped[str] = mapped_column(String(60), default='')
    before: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    after: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    ip: Mapped[str] = mapped_column(String(64), default='')
    at: Mapped[dt.datetime] = mapped_column(default=utcnow)


class TrainingOtp(Base):
    """A one-time code for identifying on a session QR (ASM-013); stored hashed."""
    __tablename__ = 'training_otps'
    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey('tenants.id'), index=True)
    registration_id: Mapped[int] = mapped_column(ForeignKey('training_registrations.id'))
    code_hash: Mapped[str] = mapped_column(String(64))
    expires_at: Mapped[dt.datetime]
    used: Mapped[bool] = mapped_column(Boolean, default=False)
    attempts: Mapped[int] = mapped_column(default=0)


def _refuse_audit_change(mapper, connection, target):
    raise ValueError('The training audit log is append-only')


from sqlalchemy import event as _event  # noqa: E402

_event.listen(TrainingAudit, 'before_update', _refuse_audit_change)
_event.listen(TrainingAudit, 'before_delete', _refuse_audit_change)

"""Training assessments: personal links (ASM-011/012), the session QR route
(ASM-013/014), autosave and resume (ASM-015), final submission with scoring
(ASM-016, ANL-001) and anonymous feedback that cannot be linked to a person
(ASM-020..022, NFR-005).

Template-based assessments run only for sessions whose program version links
templates; sessions on an original single-form revision keep the original
flow in training_service."""
from __future__ import annotations

import hashlib
import hmac
import random
import secrets

from fastapi import HTTPException
from sqlalchemy import insert, update

from app import brt_id
from app.config import get_settings
from app.crm_core import training_policy as policy
from app.crm_core import training_scoring as scoring
from app.crm_core.training_delivery import facilitators_of, status_of
from app.models.training import (TrainingAnonymousResponse, TrainingDelivery, TrainingInstance, TrainingInvitation,
                                 TrainingRegistration, TrainingResponse, TrainingSession, TrainingTemplate)
from app.timeutils import utcnow

PHASES = ("pre", "post", "feedback")


# ---------------------------------------------------------------- tokens


def _key() -> bytes:
    return hmac.new(get_settings().brt_id_secret.encode(), b"training-assessment-link", hashlib.sha256).digest()


def token_for(invitation: TrainingInvitation) -> str:
    """>= 128-bit, URL-safe; re-derivable only by the server (ASM-011)."""
    return hmac.new(_key(), f"link:{invitation.token_nonce}".encode(), hashlib.sha256).hexdigest()[:43]


def digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def link_for(invitation: TrainingInvitation) -> str:
    return f"/p/a/{token_for(invitation)}"


def public_url(path: str) -> str:
    return get_settings().training_public_origin.rstrip("/") + path


def instance(db, session_id: int, kind: str) -> TrainingInstance | None:
    return db.query(TrainingInstance).filter_by(session_id=session_id, type=kind).first()


def _issue(db, registration: TrainingRegistration, inst: TrainingInstance) -> TrainingInvitation:
    prior = db.query(TrainingInvitation).filter_by(registration_id=registration.id, phase=inst.type).first()
    if prior:
        return prior
    row = TrainingInvitation(tenant_id=registration.tenant_id, registration_id=registration.id, phase=inst.type,
                             expires_at=inst.closes_at, instance_id=inst.id, has_submitted=False,
                             token_nonce=secrets.token_hex(16), sent_at=utcnow())
    row.token_hash = digest(token_for(row))  # set before insert: the row is never "updated" by its own creation
    db.add(row)
    db.flush()
    if registration.user_id:  # the in-app inbox of a user with a login
        db.add(TrainingDelivery(tenant_id=registration.tenant_id, invitation_id=row.id, recipient_id=registration.user_id,
                                kind="invitation", link=link_for(row)))
    db.flush()
    return row


def regenerate(db, invitation: TrainingInvitation) -> TrainingInvitation:
    """WFA-008 'regenerate': the old link stops working."""
    invitation.token_nonce = secrets.token_hex(16)
    invitation.token_hash = digest(token_for(invitation))
    invitation.alt_token_hash = None
    delivery = db.query(TrainingDelivery).filter_by(invitation_id=invitation.id, kind="invitation").first()
    if delivery:
        delivery.link = link_for(invitation)
    db.flush()
    return invitation


def issue_pre(db, registration: TrainingRegistration) -> TrainingInvitation | None:
    """Pre-assessment opens at invitation (ASM-012)."""
    inst = instance(db, registration.session_id, "pre")
    if inst is None or status_of(registration) != "registered" or (inst.closes_at and inst.closes_at <= utcnow()):
        return None
    return _issue(db, registration, inst)


def issue_post(db, session: TrainingSession) -> list[TrainingInvitation]:
    """BR-ASM-01: post (and feedback) only to Present or Late."""
    out = []
    kinds = [k for k in ("post", "feedback") if instance(db, session.id, k)]
    if not kinds or session.status == "cancelled":
        return out
    for registration in db.query(TrainingRegistration).filter_by(session_id=session.id).all():
        if status_of(registration) != "attended":
            continue
        for kind in kinds:
            inst = instance(db, session.id, kind)
            if inst.closes_at and inst.closes_at <= utcnow():
                continue
            fresh = db.query(TrainingInvitation).filter_by(registration_id=registration.id, phase=kind).first() is None
            invitation = _issue(db, registration, inst)
            if fresh:
                out.append(invitation)
    if out:
        from app.crm_core import training_notify as notify
        notify.plan_session(db, session)
    return out


def feedback_locked(db, session: TrainingSession) -> bool:
    inst = instance(db, session.id, "feedback")
    if inst is None:
        return False
    if db.query(TrainingAnonymousResponse).filter_by(instance_id=inst.id).first():
        return True
    if db.query(TrainingResponse).filter_by(instance_id=inst.id).first():
        return True
    return db.query(TrainingInvitation).filter(TrainingInvitation.instance_id == inst.id, TrainingInvitation.has_submitted.is_(True)).first() is not None


# ---------------------------------------------------------------- the player


def resolve(db, token: str):
    """(invitation, registration, session, instance, template), tenant checked."""
    hashed = digest(token or "")
    invitation = db.query(TrainingInvitation).filter((TrainingInvitation.token_hash == hashed) |
                                                     (TrainingInvitation.alt_token_hash == hashed)).first()
    if invitation is None or invitation.instance_id is None:
        raise HTTPException(404, "This link is not valid")
    from app.licensing import require_module
    require_module(db, invitation.tenant_id, "training_management")
    registration = db.get(TrainingRegistration, invitation.registration_id)
    session = db.get(TrainingSession, registration.session_id)
    inst = db.get(TrainingInstance, invitation.instance_id)
    template = db.get(TrainingTemplate, inst.template_id)
    if session.tenant_id != invitation.tenant_id or inst.tenant_id != invitation.tenant_id:
        raise HTTPException(404, "This link is not valid")
    return invitation, registration, session, inst, template


def state(invitation, registration, session, inst, now=None) -> str:
    now = now or utcnow()
    if invitation.has_submitted:
        return "submitted"
    if session.status == "cancelled" or status_of(registration) == "cancelled":
        return "closed"
    if inst.opens_at and now < inst.opens_at:
        return "not_yet_open"
    if inst.closes_at and now >= inst.closes_at:
        return "closed"
    return "open"


def present(template: TrainingTemplate, seed: int) -> list[dict]:
    """Questions without their answers; randomized per the template (ASM-007).
    Options carry their original index, which is what answers use."""
    rng = random.Random(seed)
    questions = [dict(q) for q in template.questions]
    if (template.settings or {}).get("randomize_questions"):
        rng.shuffle(questions)
    out = []
    for q in questions:
        options = [{"i": i, "label": label} for i, label in enumerate(q.get("options") or [])]
        if (template.settings or {}).get("randomize_options") and q["type"] in ("single", "multi"):
            rng.shuffle(options)
        out.append({"item_key": q["item_key"], "section": q.get("section") or "", "type": q["type"], "text": q["text"],
                    "options": options, "required": q.get("required", True), "help": q.get("help") or "", "measure": q["measure"]})
    return out


def open_form(db, token: str) -> dict:
    invitation, registration, session, inst, template = resolve(db, token)
    current = state(invitation, registration, session, inst)
    from app.crm_core import training_notify as notify
    notify.mark_clicked(db, registration.id, inst.type)
    base = {"status": current, "session": session.title, "type": inst.type, "anonymous": inst.anonymous,
            "opens_at": inst.opens_at.isoformat() + "Z" if inst.opens_at else None,
            "closes_at": inst.closes_at.isoformat() + "Z" if inst.closes_at else None}
    if current == "submitted":
        return {**base, **_confirmation(db, invitation, registration, session)}
    if current != "open":
        return base  # a friendly closed message - no data (ASM-012)
    if invitation.started_at is None and not inst.anonymous:  # an anonymous form leaves no trace of when it was opened
        invitation.started_at = utcnow()
    db.commit()
    return {**base, "name": template.name, "sections": template.sections or [], "settings": template.settings or {},
            "questions": present(template, invitation.id), "draft": None if inst.anonymous else (invitation.draft or {}),
            "consent_needed": invitation.consented_at is None}


def _validate(template: TrainingTemplate, answers: dict, partial: bool) -> dict:
    keys = {q["item_key"]: q for q in template.questions}
    clean, problems = {}, {}
    for key, value in (answers or {}).items():
        if key not in keys:
            continue
        if partial and value in (None, "", []):
            continue
        problem = scoring.answer_problem(keys[key], value)
        if problem and not (partial and problem == "Answer this question"):
            problems[key] = problem
        else:
            clean[key] = value
    if not partial:
        for key, q in keys.items():
            if key not in clean and key not in problems:
                problem = scoring.answer_problem(q, None)
                if problem:
                    problems[key] = problem
    if problems:
        raise HTTPException(422, {"message": "Some answers need attention", "fields": problems})
    return clean


def autosave(db, token: str, answers: dict) -> dict:
    """ASM-015: each answer is saved as given; resume on any device."""
    invitation, registration, session, inst, template = resolve(db, token)
    if state(invitation, registration, session, inst) != "open":
        raise HTTPException(409, "This assessment is not open")
    if inst.anonymous:
        return {"saved": False, "reason": "Anonymous forms keep your draft on this device only"}
    clean = _validate(template, answers, partial=True)
    invitation.draft = {**(invitation.draft or {}), **clean}
    invitation.started_at = invitation.started_at or utcnow()
    db.commit()
    return {"saved": True, "count": len(invitation.draft)}


def _score(template: TrainingTemplate, answers: dict) -> tuple[float | None, float | None, dict]:
    scored = {}
    for q in template.questions:
        if q["measure"] == "knowledge":
            ok = scoring.is_correct(q, answers.get(q["item_key"]))
            scored[q["item_key"]] = {"is_correct": ok, "points_awarded": float(q.get("points") or 1) if ok else 0.0}
    return scoring.knowledge_score(template.questions, answers), scoring.confidence_score(template.questions, answers), scored


def submit(db, token: str, answers: dict, consent: bool) -> dict:
    invitation, registration, session, inst, template = resolve(db, token)
    current = state(invitation, registration, session, inst)
    if current == "submitted":
        return {"submitted": True, "already": True, **_confirmation(db, invitation, registration, session)}
    if current != "open":
        raise HTTPException(409, "This assessment is closed")
    if invitation.consented_at is None and not consent:
        raise HTTPException(422, {"message": "Please confirm you agree to how your answers are used", "fields": {"consent": "required"}})
    clean = _validate(template, {**(invitation.draft or {}), **(answers or {})} if not inst.anonymous else answers, partial=False)
    if inst.anonymous:
        # ASM-022: the flag only - no time, no actor, no link to the response
        claimed = db.execute(update(TrainingInvitation).where(TrainingInvitation.id == invitation.id,
                                                              TrainingInvitation.has_submitted.isnot(True))
                             .values(has_submitted=True, draft=None))
        if claimed.rowcount != 1:
            return {"submitted": True, "already": True}
        save_anonymous(db, inst, clean)
        db.commit()
        return {"submitted": True, "anonymous": True}
    now = utcnow()
    claimed = db.execute(update(TrainingInvitation).where(TrainingInvitation.id == invitation.id,
                                                          TrainingInvitation.has_submitted.isnot(True))
                         .values(has_submitted=True, consumed_at=now, draft=None,
                                 consented_at=invitation.consented_at or now))
    if claimed.rowcount != 1:
        return {"submitted": True, "already": True}
    k, c, scored = _score(template, clean)
    late = inst.type == "pre" and now > session.starts_at
    db.add(TrainingResponse(tenant_id=session.tenant_id, session_id=session.id, user_id=registration.user_id,
                            participant_id=registration.participant_id, phase=inst.type, answers=clean,
                            score=k if k is not None else 0.0, confidence=int(round(c)) if c is not None else 0,
                            feedback="", instance_id=inst.id, invitation_id=invitation.id, submitted_at=now,
                            confidence_avg=c, late=late, scored=scored))
    db.flush()
    if inst.type == "post":
        from app.crm_core import training_analytics as analytics
        analytics.recompute(db, session)  # ANL-001: results right after the post submission
    db.commit()
    db.refresh(invitation)
    return {"submitted": True, **_confirmation(db, invitation, registration, session)}


def _confirmation(db, invitation, registration, session) -> dict:
    """ASM-016: the confirmation, with the participant's own scores if the org allows."""
    if not policy.settings(db, session.tenant_id, session)["show_own_scores"]:
        return {"scores": None}
    rows = {r.phase: r for r in db.query(TrainingResponse).filter(TrainingResponse.session_id == session.id,
                                                                  TrainingResponse.instance_id.isnot(None),
                                                                  TrainingResponse.user_id == registration.user_id,
                                                                  TrainingResponse.participant_id == registration.participant_id).all()}
    return {"scores": {p: {"knowledge": scoring.display(rows[p].score), "confidence": scoring.display(rows[p].confidence_avg)}
                       for p in ("pre", "post") if p in rows}}


def save_anonymous(db, inst: TrainingInstance, answers: dict) -> None:
    """ASM-021: a random id, a fresh BRT_ID, the hour only, blank system fields -
    written with a plain INSERT so no actor or timestamp is stamped on it."""
    now = utcnow()
    hour = now.replace(minute=0, second=0, microsecond=0)
    for _ in range(5):
        rid = secrets.randbelow(2 ** 31 - 2) + 1
        if db.get(TrainingAnonymousResponse, rid) is None:
            break
    code = brt_id.mint(db.connection(), TrainingAnonymousResponse.__tablename__)[0]
    db.execute(insert(TrainingAnonymousResponse.__table__).values(
        id=rid, tenant_id=inst.tenant_id, instance_id=inst.id, submitted_hour=hour, answers=answers, brt_id=code,
        sys_created_at=None, sys_created_by=None, sys_created_position_id=None, sys_department_id=None,
        sys_updated_at=None, sys_updated_by=None))


# ---------------------------------------------------------------- the session QR (ASM-013, ASM-014)


def qr_landing(db, code: str) -> dict:
    from app.crm_core.training_delivery import checkin_open, session_by_code
    session = session_by_code(db, code)
    now = utcnow()
    phases = []
    for kind in PHASES:
        inst = instance(db, session.id, kind)
        if inst and (inst.opens_at is None or inst.opens_at <= now) and (inst.closes_at is None or now < inst.closes_at):
            phases.append({"type": kind, "anonymous": inst.anonymous})
    anonymous = next((p for p in phases if p["anonymous"]), None)
    out = {"session": session.title, "starts_at": session.starts_at.isoformat() + "Z", "status": session.status,
           "checkin_open": checkin_open(db, session, now), "phases": phases}
    if anonymous and session.status != "cancelled":
        inst = instance(db, session.id, "feedback")
        template = db.get(TrainingTemplate, inst.template_id)
        out["anonymous_form"] = {"name": template.name, "questions": present(template, inst.id), "settings": template.settings or {}}
    return out


def qr_identify(db, code: str, email: str, otp: str | None, phone_last4: str | None, phase: str | None) -> dict:
    """Routes the participant to their own assessment with a fresh link; the
    QR on its own reveals nothing about anyone else."""
    from app.crm_core.training_delivery import identify, session_by_code
    session = session_by_code(db, code)
    registration = identify(db, session, email, otp, phone_last4)
    now = utcnow()
    for kind in ([phase] if phase else ["pre", "post", "feedback"]):
        invitation = db.query(TrainingInvitation).filter_by(registration_id=registration.id, phase=kind).first()
        if invitation is None or invitation.instance_id is None:
            continue
        inst = db.get(TrainingInstance, invitation.instance_id)
        if state(invitation, registration, session, inst, now) in ("open", "submitted"):
            token = secrets.token_urlsafe(32)
            invitation.alt_token_hash = digest(token)
            db.commit()
            return {"token": token, "type": kind}
    raise HTTPException(409, "You have no assessment open right now")


ANON_COOKIE = "brt_anon_"


def anon_cookie_value(instance_id: int) -> str:
    nonce = secrets.token_hex(8)
    sig = hmac.new(_key(), f"anon:{instance_id}:{nonce}".encode(), hashlib.sha256).hexdigest()[:32]
    return f"{nonce}.{sig}"


def anon_cookie_ok(instance_id: int, value: str | None) -> bool:
    if not value or "." not in value:
        return False
    nonce, sig = value.split(".", 1)
    expected = hmac.new(_key(), f"anon:{instance_id}:{nonce}".encode(), hashlib.sha256).hexdigest()[:32]
    return hmac.compare_digest(sig, expected)


def qr_anonymous_submit(db, code: str, answers: dict, cookie: str | None) -> tuple[dict, int | None]:
    """ASM-014: one submission per device (signed cookie + the rate limit); no
    identity is captured. Returns (result, instance id to set the cookie for)."""
    from app.crm_core.training_delivery import session_by_code
    session = session_by_code(db, code)
    inst = instance(db, session.id, "feedback")
    now = utcnow()
    if inst is None or not inst.anonymous or session.status == "cancelled" or \
            (inst.opens_at and now < inst.opens_at) or (inst.closes_at and now >= inst.closes_at):
        raise HTTPException(409, "This feedback form is not open")
    if anon_cookie_ok(inst.id, cookie):
        return {"submitted": True, "already": True}, None
    template = db.get(TrainingTemplate, inst.template_id)
    clean = _validate(template, answers, partial=False)
    save_anonymous(db, inst, clean)
    db.commit()
    return {"submitted": True, "anonymous": True}, inst.id


# ---------------------------------------------------------------- read helpers


def invitations_for(db, session: TrainingSession) -> list[dict]:
    """Per-registration progress - never the links (they stay private)."""
    out = []
    rows = db.query(TrainingInvitation).join(TrainingRegistration, TrainingRegistration.id == TrainingInvitation.registration_id) \
        .filter(TrainingRegistration.session_id == session.id, TrainingInvitation.instance_id.isnot(None)).all()
    for inv in rows:
        out.append({"registration_id": inv.registration_id, "phase": inv.phase, "has_submitted": bool(inv.has_submitted),
                    "started": inv.started_at is not None, "sent_at": inv.sent_at.isoformat() + "Z" if inv.sent_at else None})
    return out


def is_facilitator(session: TrainingSession, user_id: int) -> bool:
    return user_id in facilitators_of(session)


def window_text(inst: TrainingInstance) -> dict:
    return {"type": inst.type, "opens_at": inst.opens_at.isoformat() + "Z" if inst.opens_at else None,
            "closes_at": inst.closes_at.isoformat() + "Z" if inst.closes_at else None, "anonymous": inst.anonymous,
            "status": "scheduled" if inst.opens_at and utcnow() < inst.opens_at else
                      ("closed" if inst.closes_at and utcnow() >= inst.closes_at else "open")}

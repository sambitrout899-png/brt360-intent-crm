"""Training delivery: sessions and their lifecycle (SES-001, SES-004, section
10.2), facilitator assignment rules (SES-003, BR-FAC-01, BR-SES-02),
registration with capacity, deadline and waitlist (SES-005..007, BR-SES-03),
attendance and QR check-in (SES-008, SES-009) and the calendar (SES-010)."""
from __future__ import annotations

import csv
import datetime as dt
import hashlib
import hmac
import io
import secrets

from fastapi import HTTPException

from app.crm_core import training_catalog as catalog
from app.crm_core import training_policy as policy
from app.models.auth import User
from app.models.training import (TrainingAttendance, TrainingCertificate, TrainingInstance, TrainingLocation, TrainingMember,
                                 TrainingOtp, TrainingParticipant, TrainingProgram, TrainingRegistration, TrainingRevision,
                                 TrainingSession)
from app.timeutils import utcnow

owned = catalog.owned
ACTIVE = ("registered", "nominated", "attended", "no_show")
TRANSITIONS = {("draft", "scheduled"), ("scheduled", "in_progress"), ("in_progress", "completed"), ("completed", "closed"),
               ("draft", "cancelled"), ("scheduled", "cancelled"), ("in_progress", "cancelled")}


def status_of(registration: TrainingRegistration) -> str:
    """SES-006 status; rows from before the BRD derive it from attendance."""
    if registration.status:
        return registration.status
    return {"present": "attended", "absent": "no_show", "cancelled": "cancelled"}.get(registration.attendance, "registered")


def person(db, registration: TrainingRegistration) -> dict:
    if registration.user_id:
        u = db.get(User, registration.user_id)
        m = db.query(TrainingMember).filter_by(tenant_id=registration.tenant_id, user_id=registration.user_id).first()
        return {"kind": "user", "id": u.id, "name": u.name, "email": u.email, "phone": (m.phone if m else "") or ""}
    p = db.get(TrainingParticipant, registration.participant_id)
    return {"kind": "participant", "id": p.id, "name": p.name, "email": p.email, "phone": p.phone or "",
            "organization": p.organization, "erased": p.erased_at is not None, "email_invalid": p.email_invalid}


# ---------------------------------------------------------------- access


def session_for(db, user: User, session_id: int, op: str = "r") -> tuple[TrainingSession, set[str]]:
    """The session if this user may `op` it: admin always; HR/Executive inside
    their scope; a facilitator on their own sessions; a participant on
    sessions they registered for (or open ones, to read)."""
    held = policy.guard(db, user, "sessions", op)
    session = owned(db, TrainingSession, user.tenant_id, session_id)
    if "admin" in held:
        return session, held
    if {"hr", "executive"} & held and policy.in_scope(policy.scope(db, user, held), session.location_id, session.department_id):
        if op == "r" or "hr" in held:
            return session, held
    if "facilitator" in held and user.id in facilitators_of(session):
        return session, held
    if op == "r" and "participant" in held:
        mine = db.query(TrainingRegistration).filter_by(session_id=session.id, user_id=user.id).first()
        if mine or (session.status == "scheduled" and session.starts_at > utcnow()):
            return session, held
    raise HTTPException(403, "This session is outside your training access")


def facilitators_of(session: TrainingSession) -> list[int]:
    return [session.facilitator_id, *[int(x) for x in session.co_facilitator_ids or []]]


def visible_sessions(db, user: User) -> list[TrainingSession]:
    held = policy.guard(db, user, "sessions", "r")
    rows = db.query(TrainingSession).filter_by(tenant_id=user.tenant_id).all()
    if "admin" in held:
        return rows
    limits = policy.scope(db, user, held)
    mine = {r.session_id for r in db.query(TrainingRegistration).filter_by(tenant_id=user.tenant_id, user_id=user.id)}
    out = []
    for s in rows:
        if {"hr", "executive"} & held and policy.in_scope(limits, s.location_id, s.department_id):
            out.append(s)
        elif "facilitator" in held and user.id in facilitators_of(s):
            out.append(s)
        elif s.id in mine or (not ({"hr", "executive"} & held) and s.status == "scheduled" and s.starts_at > utcnow()):
            out.append(s)  # learners browse open sessions; a scoped HR / Executive does not see past their scope
    return out


# ---------------------------------------------------------------- facilitator rules


def check_facilitator(db, tenant_id: int, facilitator_id: int, starts_at, ends_at, location_id, virtual: bool,
                      required_cert: int | None, exclude_session: int | None, override_reason: str, held: set[str]) -> str | None:
    """BR-FAC-01 and BR-SES-02. Returns the override that was used (or None)."""
    facilitator = owned(db, User, tenant_id, facilitator_id)
    if not facilitator.is_active or not ({"facilitator", "admin"} & policy.roles(db, facilitator)):
        raise HTTPException(422, f"{facilitator.name} is not an active facilitator")
    used = None
    if required_cert:
        valid = db.query(TrainingCertificate).filter(TrainingCertificate.tenant_id == tenant_id,
                                                      TrainingCertificate.user_id == facilitator_id,
                                                      TrainingCertificate.certification_id == required_cert,
                                                      TrainingCertificate.expires_at > ends_at).all()
        if not [c for c in valid if not c.revoked]:
            if "admin" in held and (override_reason or "").strip():
                used = override_reason.strip()
            else:
                raise HTTPException(409, f"{facilitator.name} has no valid certification for this program through the session end "
                                         "(BR-FAC-01). An Org Admin can override with a reason.")
    settings = policy.settings(db, tenant_id)
    buffer = dt.timedelta(minutes=int(settings["travel_buffer_min"]))
    window_start, window_end = starts_at - buffer, ends_at + buffer
    others = db.query(TrainingSession).filter(TrainingSession.tenant_id == tenant_id, TrainingSession.status != "cancelled",
                                              TrainingSession.starts_at < window_end, TrainingSession.ends_at > window_start).all()
    for other in others:
        if other.id == exclude_session or facilitator_id not in facilitators_of(other):
            continue
        # the travel buffer applies between in-person sessions at different locations
        pad = buffer if (not virtual and not other.virtual_url and location_id and other.location_id
                         and location_id != other.location_id) else dt.timedelta(0)
        if other.starts_at < ends_at + pad and other.ends_at + pad > starts_at:
            raise HTTPException(409, f"{facilitator.name} is already booked for '{other.title}' at this time (BR-SES-02)")
    return used


# ---------------------------------------------------------------- sessions


def create_session(db, user: User, payload: dict) -> TrainingSession:
    """SES-001: schedule a session from a published program version."""
    held = policy.guard(db, user, "sessions", "c")
    version = owned(db, TrainingRevision, user.tenant_id, payload["revision_id"])
    if catalog.state_of(version) != "published":
        raise HTTPException(422, "Sessions can only be created from a published program version (SES-001)")
    starts, ends = policy.as_utc(payload["starts_at"]), policy.as_utc(payload["ends_at"])
    if ends <= starts:
        raise HTTPException(422, "Session end must follow start")
    if starts <= utcnow():
        raise HTTPException(422, "Schedule a future session")
    location = catalog.active_location(db, user.tenant_id, payload.get("location_id"))
    virtual = (payload.get("virtual_url") or "").strip()
    if virtual and not virtual.lower().startswith("https://"):
        raise HTTPException(422, "The virtual meeting link must be an https:// URL")
    deadline = policy.as_utc(payload["registration_deadline"]) if payload.get("registration_deadline") else None
    if deadline and deadline > starts:
        raise HTTPException(422, "The registration deadline must be before the session starts")
    status = payload.get("status") or "scheduled"
    if status not in ("draft", "scheduled"):
        raise HTTPException(422, "A new session is draft or scheduled")
    if status == "scheduled" and not (location or virtual or (payload.get("location") or "").strip()):
        raise HTTPException(422, "A scheduled session needs a location or a virtual link")
    if payload.get("department_id") is not None:
        from app.models.org import Department
        owned(db, Department, user.tenant_id, payload["department_id"])
    required = version.required_certification_id or db.get(TrainingProgram, version.program_id).required_certification_id
    co = [int(x) for x in payload.get("co_facilitator_ids") or [] if int(x) != int(payload["facilitator_id"])]
    overrides = []
    for fid in [int(payload["facilitator_id"]), *co]:
        used = check_facilitator(db, user.tenant_id, fid, starts, ends, location.id if location else None, bool(virtual),
                                 required, None, payload.get("override_reason") or "", held)
        if used:
            overrides.append({"facilitator_id": fid, "reason": used})
    org = policy.settings(db, user.tenant_id)
    mode = payload.get("feedback_mode") or org["anonymity_default"]
    if mode not in ("identifiable", "anonymous"):
        raise HTTPException(422, "feedback_mode must be identifiable or anonymous")
    row = TrainingSession(tenant_id=user.tenant_id, revision_id=version.id, facilitator_id=int(payload["facilitator_id"]),
                          title=payload["title"][:200], location=(payload.get("location") or (location.name if location else ""))[:200],
                          department_id=payload.get("department_id"), starts_at=starts, ends_at=ends,
                          capacity=int(payload.get("capacity") or 100), status=status, location_id=location.id if location else None,
                          virtual_url=virtual or None, room=(payload.get("room") or "")[:100] or None,
                          timezone=payload.get("timezone") or (location.timezone if location else org["timezone"]),
                          registration_deadline=deadline, waitlist_enabled=bool(payload.get("waitlist_enabled")),
                          feedback_mode=mode, co_facilitator_ids=co, checkin_code=secrets.token_urlsafe(12),
                          override_reason="; ".join(o["reason"] for o in overrides) or None,
                          settings={k: v for k, v in (payload.get("settings") or {}).items() if k in policy.DEFAULTS or k == "public_registration"})
    db.add(row)
    db.flush()
    ensure_instances(db, row)
    policy.audit(db, user.tenant_id, user.id, "session.create", "training_session", row.id, None, policy.snapshot(row))
    for o in overrides:
        policy.audit(db, user.tenant_id, user.id, "facilitator.override", "training_session", row.id, None, o)
    from app.crm_core import training_notify as notify
    notify.plan_session(db, row)
    db.flush()
    return row


def ensure_instances(db, session: TrainingSession) -> None:
    """One instance per linked template, its window from the org/session settings (ASM-012)."""
    version = db.get(TrainingRevision, session.revision_id)
    if version is None:
        return
    s = policy.settings(db, session.tenant_id, session)
    windows = {
        "pre": (None, session.starts_at + dt.timedelta(minutes=int(s["pre_grace_min"]))),
        "post": (session.ends_at, session.ends_at + dt.timedelta(days=int(s["post_window_days"]))),
        "feedback": (session.ends_at, session.ends_at + dt.timedelta(days=int(s["post_window_days"]))),
    }
    for kind, template_id in (("pre", version.pre_template_id), ("post", version.post_template_id), ("feedback", version.feedback_template_id)):
        if not template_id:
            continue
        opens, closes = windows[kind]
        row = db.query(TrainingInstance).filter_by(session_id=session.id, type=kind).first()
        if row is None:
            db.add(TrainingInstance(tenant_id=session.tenant_id, session_id=session.id, template_id=template_id, type=kind,
                                    opens_at=opens, closes_at=closes, anonymous=(kind == "feedback" and session.feedback_mode == "anonymous"),
                                    closed_handled=False))
        elif not row.closed_handled:
            row.opens_at, row.closes_at = opens, closes
    db.flush()


def update_session(db, user: User, session_id: int, payload: dict) -> TrainingSession:
    """Edit or reschedule; a reschedule recalculates every pending send (WFA-001)."""
    session, held = session_for(db, user, session_id, "u")
    if session.status in ("completed", "closed", "cancelled"):
        raise HTTPException(409, f"A {session.status} session cannot be edited")
    facilitator_only = not ({"admin", "hr"} & held)
    if facilitator_only and set(payload) - {"notes"}:
        raise HTTPException(403, "Facilitators can update notes and status only")
    before = policy.snapshot(session)
    starts = policy.as_utc(payload["starts_at"]) if payload.get("starts_at") else session.starts_at
    ends = policy.as_utc(payload["ends_at"]) if payload.get("ends_at") else session.ends_at
    if ends <= starts:
        raise HTTPException(422, "Session end must follow start")
    moved = (starts, ends) != (session.starts_at, session.ends_at)
    location_id = payload.get("location_id", session.location_id)
    if "location_id" in payload:
        catalog.active_location(db, user.tenant_id, location_id)
    lead = int(payload.get("facilitator_id") or session.facilitator_id)
    co = [int(x) for x in payload.get("co_facilitator_ids", session.co_facilitator_ids or []) if int(x) != lead]
    if moved or "facilitator_id" in payload or "co_facilitator_ids" in payload or "location_id" in payload:
        required = catalog.required_certification(db, session)
        virtual = payload.get("virtual_url", session.virtual_url)
        for fid in [lead, *co]:
            check_facilitator(db, user.tenant_id, fid, starts, ends, location_id, bool(virtual), required, session.id,
                              payload.get("override_reason") or "", held)
    if "feedback_mode" in payload and payload["feedback_mode"] != session.feedback_mode:
        from app.crm_core import training_assess as assess
        if assess.feedback_locked(db, session):
            raise HTTPException(409, "Feedback mode is locked once the first response arrives (ASM-020)")
        if payload["feedback_mode"] not in ("identifiable", "anonymous"):
            raise HTTPException(422, "feedback_mode must be identifiable or anonymous")
        session.feedback_mode = payload["feedback_mode"]
        inst = db.query(TrainingInstance).filter_by(session_id=session.id, type="feedback").first()
        if inst:
            inst.anonymous = session.feedback_mode == "anonymous"
    session.starts_at, session.ends_at, session.facilitator_id, session.co_facilitator_ids = starts, ends, lead, co
    session.location_id = location_id
    for key in ("title", "room", "virtual_url", "capacity", "waitlist_enabled", "timezone", "notes", "department_id"):
        if key in payload:
            setattr(session, key, payload[key])
    if "registration_deadline" in payload:
        session.registration_deadline = policy.as_utc(payload["registration_deadline"]) if payload["registration_deadline"] else None
    if "settings" in payload:
        session.settings = {**(session.settings or {}), **{k: v for k, v in (payload["settings"] or {}).items()
                                                            if k in policy.DEFAULTS or k == "public_registration"}}
    ensure_instances(db, session)
    policy.audit(db, user.tenant_id, user.id, "session.update", "training_session", session.id, before, policy.snapshot(session))
    from app.crm_core import training_notify as notify
    notify.plan_session(db, session, rescheduled=moved)
    db.flush()
    return session


def transition(db, user: User | None, session: TrainingSession, to: str, reason: str = "", now=None) -> TrainingSession:
    """Section 10.2 session state machine. `user` None = the scheduler."""
    now = now or utcnow()
    current = session.status or "scheduled"
    if (current, to) not in TRANSITIONS:
        raise HTTPException(409, f"A session cannot move from {current} to {to}")
    if to == "scheduled":
        if not session.facilitator_id or not (session.location_id or session.virtual_url or session.location):
            raise HTTPException(422, "Publishing needs a lead facilitator, dates and a location or virtual link")
    if to == "completed" and now < session.ends_at:
        raise HTTPException(409, "A session can be completed only after its end time (BR-SES-04)")
    if to == "closed":
        post = db.query(TrainingInstance).filter_by(session_id=session.id, type="post").first()
        closes = post.closes_at if post else session.ends_at + dt.timedelta(days=int(policy.settings(db, session.tenant_id, session)["post_window_days"]))
        if now < closes:
            raise HTTPException(409, "A session closes only after its post-assessment window closes (BR-SES-04)")
    if to == "cancelled" and not (reason or "").strip():
        raise HTTPException(422, "Give the reason for cancelling")
    session.status, session.status_reason = to, (reason or None)
    policy.audit(db, session.tenant_id, user.id if user else None, f"session.{to}", "training_session", session.id,
                 {"status": current}, {"status": to, "reason": reason or None})
    from app.crm_core import training_assess as assess
    from app.crm_core import training_notify as notify
    if to == "completed":
        assess.issue_post(db, session)
    if to == "closed":
        from app.crm_core import training_analytics as analytics
        analytics.recompute(db, session)
    if to == "cancelled":
        notify.on_cancel(db, session)
    else:
        notify.plan_session(db, session)
    db.flush()
    return session


def request_transition(db, user: User, session_id: int, to: str, reason: str) -> TrainingSession:
    session, held = session_for(db, user, session_id, "u")
    if to in ("scheduled", "cancelled", "closed") and not ({"admin", "hr"} & held):
        raise HTTPException(403, "Only an Org Admin or HR can publish, cancel or close a session")
    return transition(db, user, session, to, reason)


def auto_transitions(db, tenant_id: int, now=None) -> int:
    """Scheduler: start, end and close sessions on their times."""
    now = now or utcnow()
    moved = 0
    for session in db.query(TrainingSession).filter(TrainingSession.tenant_id == tenant_id,
                                                    TrainingSession.status.in_(("scheduled", "in_progress", "completed"))).all():
        try:
            if session.status == "scheduled" and now >= session.starts_at:
                transition(db, None, session, "in_progress", now=now)
                moved += 1
            if session.status == "in_progress" and now >= session.ends_at:
                transition(db, None, session, "completed", now=now)
                moved += 1
            if session.status == "completed":
                transition(db, None, session, "closed", now=now)
                moved += 1
        except HTTPException:
            continue
    return moved


# ---------------------------------------------------------------- registration (SES-005..007, BR-SES-03)


def _active_count(db, session_id: int) -> int:
    return sum(1 for r in db.query(TrainingRegistration).filter_by(session_id=session_id).all() if status_of(r) in ACTIVE)


def find_or_create_participant(db, tenant_id: int, data: dict) -> tuple[int | None, int | None]:
    """(user_id, participant_id) for an email: a user with a login first, then
    the directory; a new external participant when a name is given (SES-007)."""
    email = (data.get("email") or "").strip().lower()
    if not catalog.EMAIL.match(email):
        raise HTTPException(422, "Give a valid email")
    found = db.query(User).filter(User.tenant_id == tenant_id, User.email == email, User.is_active.is_(True)).first()
    if found:
        return found.id, None
    existing = db.query(TrainingParticipant).filter_by(tenant_id=tenant_id, email=email).first()
    if existing:
        if existing.erased_at:
            raise HTTPException(422, "This participant's data was erased")
        return None, existing.id
    if not (data.get("name") or "").strip():
        raise HTTPException(422, f"{email} is not in the directory - give their name to add them")
    row = TrainingParticipant(tenant_id=tenant_id, name=data["name"].strip()[:200], email=email,
                              phone=(data.get("phone") or "")[:40], organization=(data.get("organization") or "")[:200])
    db.add(row)
    db.flush()
    return None, row.id


def enrol(db, session: TrainingSession, user_id: int | None, participant_id: int | None, source: str,
          actor_id: int | None, confirm: bool = True, now=None) -> TrainingRegistration:
    now = now or utcnow()
    if session.status not in ("scheduled", "draft") or session.starts_at <= now:
        raise HTTPException(409, "Registration is closed")
    if session.registration_deadline and now > session.registration_deadline:
        raise HTTPException(409, "Registration closed at the registration deadline (BR-SES-03)")
    q = db.query(TrainingRegistration).filter_by(session_id=session.id)
    prior = q.filter_by(user_id=user_id).first() if user_id else q.filter_by(participant_id=participant_id).first()
    if prior and status_of(prior) != "cancelled":
        raise HTTPException(409, "Participant is already registered")
    if user_id:
        u = db.get(User, user_id)
        if u is None or u.tenant_id != session.tenant_id or not u.is_active:
            raise HTTPException(422, "Participant is inactive")
    full = _active_count(db, session.id) >= session.capacity
    if full and not session.waitlist_enabled:
        raise HTTPException(409, "Session is full")
    status = "waitlisted" if full else ("registered" if confirm else "nominated")
    position = None
    if full:
        position = 1 + max([r.waitlist_position or 0 for r in q.all() if status_of(r) == "waitlisted"] or [0])
    row = prior or TrainingRegistration(tenant_id=session.tenant_id, session_id=session.id, user_id=user_id, participant_id=participant_id)
    row.status, row.source, row.waitlist_position, row.registered_at = status, source, position, now
    row.attendance = "registered"
    if prior is None:
        db.add(row)
    db.flush()
    policy.audit(db, session.tenant_id, actor_id, f"registration.{status}", "training_registration", row.id, None,
                 {"session_id": session.id, "source": source})
    if status == "registered":
        _registered(db, row)
    return row


def _registered(db, registration: TrainingRegistration) -> None:
    from app.crm_core import training_assess as assess
    from app.crm_core import training_notify as notify
    assess.issue_pre(db, registration)
    notify.on_registered(db, registration)


def register(db, user: User, session_id: int, payload: dict) -> TrainingRegistration:
    held = policy.guard(db, user, "registrations", "c")
    session = owned(db, TrainingSession, user.tenant_id, session_id)
    staff = {"admin", "hr"} & held
    if staff:
        session_for(db, user, session_id, "r")  # an HR user stays inside their scope
        if payload.get("user_id"):
            user_id, participant_id = int(payload["user_id"]), None
        elif payload.get("participant_id"):
            user_id, participant_id = None, owned(db, TrainingParticipant, user.tenant_id, payload["participant_id"]).id
        else:
            user_id, participant_id = find_or_create_participant(db, user.tenant_id, payload)
        source = payload.get("source") or "nominated"
        return enrol(db, session, user_id, participant_id, source, user.id, confirm=payload.get("confirm", True))
    if payload.get("user_id") not in (None, user.id) or session.status != "scheduled":
        raise HTTPException(403, "Register yourself or ask HR to register participants")
    return enrol(db, session, user.id, None, "self", user.id)


def confirm(db, user: User, registration_id: int) -> TrainingRegistration:
    row = owned(db, TrainingRegistration, user.tenant_id, registration_id)
    held = policy.guard(db, user, "registrations", "u")
    if not ({"admin", "hr"} & held) and row.user_id != user.id:
        raise HTTPException(403, "You can confirm only your own nomination")
    if status_of(row) != "nominated":
        raise HTTPException(409, "Only a nomination can be confirmed")
    session = db.get(TrainingSession, row.session_id)
    if _active_count(db, session.id) > session.capacity:
        raise HTTPException(409, "Session is full")
    row.status = "registered"
    db.flush()
    _registered(db, row)
    return row


def cancel_registration(db, user: User | None, registration_id: int, tenant_id: int | None = None) -> TrainingRegistration:
    tenant = user.tenant_id if user else tenant_id
    row = owned(db, TrainingRegistration, tenant, registration_id)
    if user is not None:
        held = policy.guard(db, user, "registrations", "u")
        if not ({"admin", "hr"} & held) and row.user_id != user.id:
            raise HTTPException(403, "You can cancel only your own registration")
    session = db.get(TrainingSession, row.session_id)
    if utcnow() >= session.ends_at:
        raise HTTPException(409, "A registration cannot be cancelled after the session ends")
    was = status_of(row)
    row.status, row.attendance, row.waitlist_position = "cancelled", "cancelled", None
    policy.audit(db, tenant, user.id if user else None, "registration.cancelled", "training_registration", row.id, {"status": was}, {"status": "cancelled"})
    db.flush()
    from app.crm_core import training_notify as notify
    notify.cancel_pending(db, registration_id=row.id)
    if was in ACTIVE:
        promote(db, session)
    return row


def promote(db, session: TrainingSession) -> TrainingRegistration | None:
    """SES-006: a freed seat goes to the first on the waitlist, who is notified."""
    if not session.waitlist_enabled or _active_count(db, session.id) >= session.capacity:
        return None
    waiting = sorted([r for r in db.query(TrainingRegistration).filter_by(session_id=session.id).all() if status_of(r) == "waitlisted"],
                     key=lambda r: r.waitlist_position or 0)
    if not waiting:
        return None
    row = waiting[0]
    row.status, row.waitlist_position = "registered", None
    policy.audit(db, session.tenant_id, None, "registration.promoted", "training_registration", row.id, {"status": "waitlisted"}, {"status": "registered"})
    db.flush()
    from app.crm_core import training_notify as notify
    notify.queue(db, session, row, "waitlist_promoted", utcnow())
    _registered(db, row)
    return row


def bulk_register(db, user: User, session_id: int, text: str) -> dict:
    """SES-005 (b): CSV of email[,name,phone,organization]; one report row per line."""
    policy.guard(db, user, "registrations", "c")
    session, _ = session_for(db, user, session_id, "r")
    reader = csv.DictReader(io.StringIO(text.lstrip("﻿")))
    if not reader.fieldnames or "email" not in [f.strip().lower() for f in reader.fieldnames]:
        raise HTTPException(422, "The CSV needs an email column")
    report = []
    for number, raw in enumerate(reader, start=2):
        row = {(k or "").strip().lower(): (v or "").strip() for k, v in raw.items()}
        try:
            with db.begin_nested():
                user_id, participant_id = find_or_create_participant(db, user.tenant_id, row)
                reg = enrol(db, session, user_id, participant_id, "bulk", user.id)
            report.append({"row": number, "email": row.get("email"), "ok": True, "status": reg.status})
        except HTTPException as exc:
            report.append({"row": number, "email": row.get("email"), "ok": False, "errors": [str(exc.detail)]})
    return {"rows": report, "registered": sum(r["ok"] for r in report)}


# ---------------------------------------------------------------- attendance (SES-008, SES-009)


def _mark(db, registration: TrainingRegistration, status: str, method: str, recorded_by: int | None) -> None:
    if status not in ("present", "late", "absent"):
        raise HTTPException(422, "Attendance is present, late or absent")
    if status_of(registration) in ("cancelled", "waitlisted"):
        raise HTTPException(409, "Only registered participants can be marked")
    db.add(TrainingAttendance(tenant_id=registration.tenant_id, registration_id=registration.id, status=status,
                              method=method, recorded_by=recorded_by, recorded_at=utcnow()))
    registration.status = "no_show" if status == "absent" else "attended"
    registration.attendance = "absent" if status == "absent" else "present"


def mark_attendance(db, user: User, session_id: int, items: list[dict], method: str = "roster") -> dict:
    session, held = session_for(db, user, session_id, "r")
    if not ({"admin", "hr"} & held or ("facilitator" in held and user.id in facilitators_of(session))):
        raise HTTPException(403, "Only the session's facilitators, HR or an Org Admin can take attendance")
    if method not in ("roster", "upload"):
        raise HTTPException(422, "method must be roster or upload")
    if session.status in ("draft", "cancelled"):
        raise HTTPException(409, "Attendance is taken for scheduled sessions")
    for item in items:
        registration = owned(db, TrainingRegistration, user.tenant_id, item["registration_id"])
        if registration.session_id != session.id:
            raise HTTPException(422, "That registration is for another session")
        _mark(db, registration, item["status"], method, user.id)
    db.flush()
    policy.audit(db, user.tenant_id, user.id, "attendance.mark", "training_session", session.id, None, {"count": len(items), "method": method})
    if session.status in ("completed", "closed"):
        from app.crm_core import training_assess as assess
        assess.issue_post(db, session)
    return {"marked": len(items)}


def upload_attendance(db, user: User, session_id: int, text: str) -> dict:
    session, _ = session_for(db, user, session_id, "r")
    reader = csv.DictReader(io.StringIO(text.lstrip("﻿")))
    items, report = [], []
    for number, raw in enumerate(reader, start=2):
        row = {(k or "").strip().lower(): (v or "").strip() for k, v in raw.items()}
        match = registration_by_email(db, session, row.get("email", ""))
        if match is None:
            report.append({"row": number, "ok": False, "errors": ["not registered for this session"]})
            continue
        items.append({"registration_id": match.id, "status": (row.get("status") or "present").lower()})
        report.append({"row": number, "ok": True})
    marked = mark_attendance(db, user, session_id, items, "upload")["marked"] if items else 0
    return {"rows": report, "marked": marked}


def registration_by_email(db, session: TrainingSession, email: str) -> TrainingRegistration | None:
    email = (email or "").strip().lower()
    if not email:
        return None
    for r in db.query(TrainingRegistration).filter_by(session_id=session.id).all():
        if status_of(r) in ("cancelled", "waitlisted"):
            continue
        if r.user_id:
            u = db.get(User, r.user_id)
            if u and u.email.lower() == email:
                return r
        elif r.participant_id:
            p = db.get(TrainingParticipant, r.participant_id)
            if p and p.email.lower() == email and not p.erased_at:
                return r
    return None


def session_by_code(db, code: str) -> TrainingSession:
    row = db.query(TrainingSession).filter_by(checkin_code=code).first() if code else None
    if row is None:
        raise HTTPException(404, "This QR code is not valid - ask the facilitator for the current one")
    from app.licensing import require_module
    require_module(db, row.tenant_id, "training_management")
    return row


def checkin_open(db, session: TrainingSession, now=None) -> bool:
    now = now or utcnow()
    s = policy.settings(db, session.tenant_id, session)
    return (session.status not in ("cancelled", "draft") and
            session.starts_at - dt.timedelta(minutes=int(s["checkin_before_min"])) <= now
            <= session.starts_at + dt.timedelta(minutes=int(s["checkin_after_min"])))


def send_otp(db, session: TrainingSession, email: str) -> None:
    """Always answers the same way, whether or not the email is registered."""
    registration = registration_by_email(db, session, email)
    if registration is None:
        return
    code = f"{secrets.randbelow(10 ** 6):06d}"
    db.add(TrainingOtp(tenant_id=session.tenant_id, registration_id=registration.id,
                       code_hash=hashlib.sha256(f"{registration.id}:{code}".encode()).hexdigest(),
                       expires_at=utcnow() + dt.timedelta(minutes=10), used=False, attempts=0))
    from app.crm_core import training_notify as notify
    notify.queue(db, session, registration, "otp", utcnow(), extra={"otp.code": code}, send_now=True)
    db.flush()


def identify(db, session: TrainingSession, email: str, otp: str | None, phone_last4: str | None) -> TrainingRegistration:
    """ASM-013 / SES-009: email + one-time code, or email + last 4 digits of the
    phone on file. Never reveals who else is on the roster."""
    registration = registration_by_email(db, session, email)
    fail = HTTPException(403, "We could not confirm it's you - check your details and try again")
    if registration is None:
        raise fail
    if otp:
        rows = db.query(TrainingOtp).filter(TrainingOtp.registration_id == registration.id, TrainingOtp.used.is_(False),
                                            TrainingOtp.expires_at > utcnow()).order_by(TrainingOtp.id.desc()).all()
        digest = hashlib.sha256(f"{registration.id}:{otp.strip()}".encode()).hexdigest()
        for row in rows:
            row.attempts += 1
            if row.attempts <= 5 and hmac.compare_digest(row.code_hash, digest):
                row.used = True
                db.flush()
                return registration
        db.flush()
        raise fail
    if phone_last4:
        phone = "".join(ch for ch in person(db, registration).get("phone", "") if ch.isdigit())
        if len(phone) >= 4 and hmac.compare_digest(phone[-4:], "".join(ch for ch in phone_last4 if ch.isdigit())[-4:]):
            return registration
    raise fail


def qr_checkin(db, code: str, email: str, otp: str | None, phone_last4: str | None) -> dict:
    session = session_by_code(db, code)
    if not checkin_open(db, session):
        raise HTTPException(409, "Check-in closed - please ask your facilitator to mark you present")
    registration = identify(db, session, email, otp, phone_last4)
    late = utcnow() > session.starts_at + dt.timedelta(minutes=10)
    if status_of(registration) != "attended":
        _mark(db, registration, "late" if late else "present", "qr", None)
        policy.audit(db, session.tenant_id, None, "attendance.qr", "training_registration", registration.id, None, {"late": late})
    db.flush()
    return {"checked_in": True, "session": session.title, "late": late}


def regenerate_checkin(db, user: User, session_id: int) -> TrainingSession:
    session, held = session_for(db, user, session_id, "u")
    session.checkin_code = secrets.token_urlsafe(12)
    policy.audit(db, user.tenant_id, user.id, "session.qr_regenerate", "training_session", session.id)
    db.flush()
    return session


# ---------------------------------------------------------------- calendar (SES-010)


def calendar(db, user: User, start=None, end=None, program_id=None, location_id=None, facilitator_id=None, status=None) -> list[dict]:
    out = []
    versions = {v.id: v for v in db.query(TrainingRevision).filter_by(tenant_id=user.tenant_id).all()}
    programs = {p.id: p for p in db.query(TrainingProgram).filter_by(tenant_id=user.tenant_id).all()}
    locations = {loc.id: loc for loc in db.query(TrainingLocation).filter_by(tenant_id=user.tenant_id).all()}
    for s in visible_sessions(db, user):
        version = versions.get(s.revision_id)
        if start and s.ends_at < start or end and s.starts_at > end:
            continue
        if program_id and (version is None or version.program_id != program_id):
            continue
        if location_id and s.location_id != location_id or status and (s.status or "scheduled") != status:
            continue
        if facilitator_id and facilitator_id not in facilitators_of(s):
            continue
        loc = locations.get(s.location_id)
        out.append({"id": s.id, "title": s.title, "status": s.status, "starts_at": s.starts_at.isoformat() + "Z",
                    "ends_at": s.ends_at.isoformat() + "Z", "timezone": s.timezone or (loc.timezone if loc else "UTC"),
                    "start_local": policy.to_local(s.starts_at, s.timezone or (loc.timezone if loc else None)),
                    "location": loc.name if loc else s.location, "virtual": bool(s.virtual_url),
                    "program": programs[version.program_id].name if version and version.program_id in programs else "",
                    "program_id": version.program_id if version else None, "version": catalog.label_of(version) if version else "",
                    "facilitator_ids": facilitators_of(s), "capacity": s.capacity,
                    "registered": _active_count(db, s.id)})
    return sorted(out, key=lambda x: x["starts_at"])

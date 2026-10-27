"""Training module BRD v1.0 API. Staff endpoints live beside the original
ones under /api/training (the org always comes from the auth token, never
the URL); the participant's public token endpoints are under /api/p and are
rate limited (NFR-003). See docs/training-brd.md for the map of requirement
IDs to endpoints."""
from __future__ import annotations

import datetime as dt
import io
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.auth import get_current_user
from app.db import get_db
from app.crm_core import training_analytics as analytics
from app.crm_core import training_assess as assess
from app.crm_core import training_catalog as catalog
from app.crm_core import training_delivery as delivery
from app.crm_core import training_export as exporter
from app.crm_core import training_notify as notify
from app.crm_core import training_policy as policy
from app.licensing import license_guard
from app.models.auth import User, UserRole
from app.models.training import (TrainingAudit, TrainingCertificate, TrainingCertification, TrainingInstance, TrainingLocation,
                                 TrainingMember, TrainingParticipant, TrainingProgram, TrainingQaReview, TrainingRegistration,
                                 TrainingRevision, TrainingSession, TrainingTemplate)
from app.timeutils import utcnow

import re

from app.access import get_access
from app.crm_core.access_service import TRAINING_PAGES, Access

ANY_PAGE = ("training", *TRAINING_PAGES)
S, M, P, A, PE, F, O, MS, ST, AU = ("training_sessions", "training_mine", "training_programs", "training_assessments", "training_people",
                                    "training_facilitators", "training_outcomes", "training_messages", "training_settings", "training_audit")
# (methods or None for all, path under /api/training, pages that may use it) - first match wins;
# anything unlisted (shared lookups such as /me, /catalog, /locations) needs any Learning page.
PAGE_RULES: list[tuple[set[str] | None, re.Pattern, tuple[str, ...]]] = [(m, re.compile(p), pages) for m, p, pages in [
    ({"PUT"}, r"^/settings$", (ST,)),
    ({"POST", "PATCH"}, r"^/locations", (PE,)),
    (None, r"^/people", (PE,)),
    (None, r"^/members/\d+/access$", (PE,)),
    (None, r"^/catalog/programs", (P,)),
    (None, r"^/versions/", (P,)),
    (None, r"^/templates", (A,)),
    ({"POST"}, r"^/certifications$", (F,)),
    (None, r"^/facilitator-certificates", (F,)),
    (None, r"^/facilitators/\d+/scorecard$", (F,)),
    (None, r"^/qa-reviews$", (F, S)),
    (None, r"^/registrations/\d+/(cancel|confirm)$", (S, M)),
    ({"GET"}, r"^/sessions/\d+/detail$", (S, M)),
    ({"POST"}, r"^/sessions/\d+/enrol$", (S, M)),
    (None, r"^/sessions/\d+/notifications$", (S, MS)),
    (None, r"^/(schedule|calendar|sessions/)", (S,)),
    (None, r"^/analytics/(sessions|export)", (S, O)),
    (None, r"^/analytics/", (O,)),
    (None, r"^/notifications/resend$", (S, MS)),
    (None, r"^/(notification-templates|notifications/|automation/tick)", (MS,)),
    (None, r"^/audit-log$", (AU,)),
]]


def require_training_page(request: Request, access: Access = Depends(get_access)) -> None:
    """Each Learning page is granted to roles under Admin -> Roles; its APIs
    follow it, like require_page does for the rest of BRT 360."""
    path, method = request.url.path[len("/api/training"):], request.method
    needed = next((pages for methods, pattern, pages in PAGE_RULES if (methods is None or method in methods) and pattern.search(path)), ANY_PAGE)
    if not any(p in access.pages for p in needed):
        from app.crm_core.access_service import PAGES
        names = " or ".join(dict(PAGES).get(p, p).replace("Learning · ", "") for p in needed[:3])
        raise HTTPException(403, f"This needs the Learning {names} page - ask your administrator for access.")


router = APIRouter(prefix="/api/training", tags=["training"],
                   dependencies=[Depends(license_guard("training_management")), Depends(policy.remember_ip), Depends(require_training_page)])
public = APIRouter(prefix="/api/p", tags=["training-public"], dependencies=[Depends(policy.rate_limit)])


def iso(value):
    return value.isoformat() + "Z" if isinstance(value, dt.datetime) else value


def plain(row, exclude=()) -> dict:
    return {c.name: iso(getattr(row, c.name)) for c in row.__table__.columns
            if not c.name.startswith("sys_") and c.name != "brt_id" and c.name not in exclude}


class Body(BaseModel):
    model_config = ConfigDict(extra="allow")


def data(body: Body) -> dict:
    return body.model_dump(exclude_unset=True)


# ================================================================ settings, locations, people


@router.get("/settings")
def get_settings_(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    policy.guard(db, user, "settings", "r")
    from app.models.training import TrainingSettings
    row = db.query(TrainingSettings).filter_by(tenant_id=user.tenant_id).first()
    return {"version": row.version if row else 0, "settings": policy.settings(db, user.tenant_id),
            "time_zones_hint": ["UTC", "Asia/Kolkata", "Asia/Dubai", "Asia/Riyadh", "Europe/London"]}


@router.put("/settings")
def put_settings(body: Body, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    out = catalog.save_settings(db, user, data(body).get("settings", data(body)))
    db.commit()
    return out


@router.get("/locations")
def list_locations(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    policy.guard(db, user, "locations", "r")
    return [plain(r) for r in db.query(TrainingLocation).filter_by(tenant_id=user.tenant_id).order_by(TrainingLocation.name)]


class LocationIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    code: str = Field(min_length=1, max_length=50)
    address: str = Field(default="", max_length=1000)
    timezone: str = Field(default="UTC", max_length=60)
    status: Literal["active", "inactive"] = "active"


@router.post("/locations", status_code=201)
def add_location(payload: LocationIn, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    row = catalog.save_location(db, user, payload.model_dump())
    db.commit()
    return plain(row)


@router.patch("/locations/{location_id}")
def edit_location(location_id: int, body: Body, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    row = catalog.save_location(db, user, data(body), location_id)
    db.commit()
    return plain(row)


@router.get("/people")
def people(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """The directory: users with a login (and their training roles) and
    participants without one - for Org Admin and HR (scoped)."""
    held = policy.roles(db, user)
    if not ({"admin", "hr"} & held):
        raise HTTPException(403, "Your training role does not allow this action")
    limits = policy.scope(db, user, held)
    members = {m.user_id: m for m in db.query(TrainingMember).filter_by(tenant_id=user.tenant_id)}
    users = []
    for u in db.query(User).filter_by(tenant_id=user.tenant_id).order_by(User.name):
        m = members.get(u.id)
        if limits is not None and not (m and policy.in_scope(limits, None, m.department_id)):
            continue
        users.append({"id": u.id, "name": u.name, "email": u.email, "active": u.is_active,
                      "roles": sorted(policy.roles(db, u)), "department_id": m.department_id if m else None,
                      "location": m.location if m else "", "phone": (m.phone if m else "") or "",
                      "scope_location_ids": (m.scope_location_ids if m else None) or [],
                      "scope_department_ids": (m.scope_department_ids if m else None) or [],
                      "profile": (m.profile if m else None) or {}})
    parts = [plain(p) for p in db.query(TrainingParticipant).filter_by(tenant_id=user.tenant_id).order_by(TrainingParticipant.name)
             if limits is None or policy.in_scope(limits, p.location_id, p.department_id)]
    return {"users": users, "participants": parts}


class ParticipantIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    email: str = Field(min_length=3, max_length=255)
    phone: str = Field(default="", max_length=40)
    employee_id: str = Field(default="", max_length=60)
    organization: str = Field(default="", max_length=200)
    location: str = Field(default="", max_length=50)
    department: str = Field(default="", max_length=150)
    manager: str = Field(default="", max_length=200)


@router.post("/people", status_code=201)
def add_person(payload: ParticipantIn, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    row = catalog.create_participant(db, user, payload.model_dump())
    db.commit()
    return plain(row)


class ImportIn(BaseModel):
    csv: str = Field(max_length=2_000_000)
    commit: bool = False


@router.post("/people/import")
def import_people(payload: ImportIn, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    out = catalog.import_participants(db, user, payload.csv, payload.commit)
    db.commit() if payload.commit else db.rollback()
    return out


@router.post("/people/{participant_id}/erase")
def erase_person(participant_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    row = catalog.erase_participant(db, user, participant_id)
    db.commit()
    return plain(row)


class AccessIn(BaseModel):
    roles: list[Literal["hr", "executive", "facilitator", "participant"]] = Field(default_factory=list)
    scope_location_ids: list[int] = Field(default_factory=list)
    scope_department_ids: list[int] = Field(default_factory=list)
    department_id: int | None = None
    location: str = Field(default="", max_length=200)
    phone: str = Field(default="", max_length=40)
    profile: dict[str, Any] | None = None


@router.put("/members/{user_id}/access")
def member_access(user_id: int, payload: AccessIn, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """ORG-005: several roles per user, each optionally scoped (ORG-006)."""
    held = policy.roles(db, user)
    if "admin" not in held:
        raise HTTPException(403, "Only an Org Admin assigns training roles")
    target = catalog.owned(db, User, user.tenant_id, user_id)
    for lid in payload.scope_location_ids:
        catalog.owned(db, TrainingLocation, user.tenant_id, lid)
    from app.models.org import Department
    for did in payload.scope_department_ids + ([payload.department_id] if payload.department_id else []):
        catalog.owned(db, Department, user.tenant_id, did)
    row = db.query(TrainingMember).filter_by(tenant_id=user.tenant_id, user_id=target.id).first()
    before = policy.snapshot(row) if row else None
    chosen = set(payload.roles) or {"participant"}
    if row is None:
        row = TrainingMember(tenant_id=user.tenant_id, user_id=target.id, role="participant", location="")
        db.add(row)
    row.role = policy.primary(chosen)
    row.roles = sorted(chosen)
    row.scope_location_ids, row.scope_department_ids = payload.scope_location_ids, payload.scope_department_ids
    row.department_id, row.location, row.phone = payload.department_id, payload.location, payload.phone
    if payload.profile is not None:
        allowed = {"bio", "expertise", "languages", "is_external", "vendor"}
        row.profile = {**(row.profile or {}), **{k: v for k, v in payload.profile.items() if k in allowed}}
    db.flush()
    policy.audit(db, user.tenant_id, user.id, "member.access", "training_member", row.id, before, policy.snapshot(row))
    db.commit()
    return plain(row)


# ================================================================ catalog: programs, versions, templates


def _version(db, v: TrainingRevision) -> dict:
    out = plain(v, ("questions",))
    out["state"] = catalog.state_of(v)
    out["version_label"] = catalog.label_of(v)
    out["sessions"] = db.query(TrainingSession).filter_by(revision_id=v.id).count()
    return out


@router.get("/catalog")
def catalog_overview(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    held = policy.guard(db, user, "programs", "r")
    programs = db.query(TrainingProgram).filter_by(tenant_id=user.tenant_id).order_by(TrainingProgram.name).all()
    versions = db.query(TrainingRevision).filter_by(tenant_id=user.tenant_id).order_by(TrainingRevision.version).all()
    out = {"programs": [{**plain(p), "versions": [_version(db, v) for v in versions if v.program_id == p.id]} for p in programs]}
    if policy.can(held, "templates", "r"):
        out["templates"] = [{**plain(t), "in_use": catalog.template_in_use(db, t)}
                            for t in db.query(TrainingTemplate).filter_by(tenant_id=user.tenant_id).order_by(TrainingTemplate.name)]
    if policy.can(held, "certifications", "r"):
        out["certifications"] = [plain(c) for c in db.query(TrainingCertification).filter_by(tenant_id=user.tenant_id)]
    return out


@router.post("/catalog/programs", status_code=201)
def add_program(body: Body, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    program, version = catalog.create_program(db, user, data(body))
    db.commit()
    return {"program": plain(program), "version": _version(db, version)}


@router.patch("/catalog/programs/{program_id}")
def edit_program(program_id: int, body: Body, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    row = catalog.update_program(db, user, program_id, data(body))
    db.commit()
    return plain(row)


class NewVersionIn(BaseModel):
    base_id: int | None = None
    bump: Literal["minor", "major"] = "minor"


@router.post("/catalog/programs/{program_id}/versions", status_code=201)
def add_version(program_id: int, payload: NewVersionIn, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    row = catalog.new_version(db, user, program_id, payload.base_id, payload.bump)
    db.commit()
    return _version(db, row)


@router.patch("/versions/{version_id}")
def edit_version(version_id: int, body: Body, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    row = catalog.update_version(db, user, version_id, data(body))
    db.commit()
    return _version(db, row)


@router.get("/versions/{version_id}/check")
def check_version(version_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    policy.guard(db, user, "versions", "r")
    row = catalog.owned(db, TrainingRevision, user.tenant_id, version_id)
    return {"problems": catalog.publish_problems(db, row) if catalog.state_of(row) == "draft" else []}


@router.post("/versions/{version_id}/publish")
def publish_version(version_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    row = catalog.publish_version(db, user, version_id)
    db.commit()
    return _version(db, row)


@router.post("/versions/{version_id}/retire")
def retire_version(version_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    row = catalog.retire_version(db, user, version_id)
    db.commit()
    return _version(db, row)


@router.get("/versions/{a}/diff/{b}")
def diff(a: int, b: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return catalog.diff_versions(db, user, a, b)


@router.get("/templates/{template_id}")
def get_template(template_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    policy.guard(db, user, "templates", "r")
    row = catalog.owned(db, TrainingTemplate, user.tenant_id, template_id)
    return {**plain(row), "in_use": catalog.template_in_use(db, row)}


@router.post("/templates", status_code=201)
def add_template(body: Body, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    row = catalog.save_template(db, user, data(body))
    db.commit()
    return plain(row)


@router.patch("/templates/{template_id}")
def edit_template(template_id: int, body: Body, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    row = catalog.save_template(db, user, data(body), template_id)
    db.commit()
    return plain(row)


@router.post("/templates/{template_id}/clone", status_code=201)
def clone_template(template_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    row = catalog.clone_template(db, user, template_id)
    db.commit()
    return plain(row)


@router.post("/templates/{template_id}/publish")
def publish_template(template_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    row = catalog.publish_template(db, user, template_id)
    db.commit()
    return plain(row)


# ================================================================ certifications, facilitators, QA


@router.post("/certifications", status_code=201)
def add_certification(body: Body, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    row = catalog.save_certification(db, user, data(body))
    db.commit()
    return plain(row)


class FacilitatorCertIn(BaseModel):
    user_id: int
    certification_id: int | None = None
    name: str = Field(default="", max_length=200)
    issuer: str = Field(default="", max_length=200)
    issued_on: dt.datetime | None = None
    expires_at: dt.datetime | None = None
    evidence_url: str = Field(default="", max_length=500)
    quality_notes: str = Field(default="", max_length=4000)


@router.post("/facilitator-certificates", status_code=201)
def add_facilitator_cert(payload: FacilitatorCertIn, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    values = payload.model_dump()
    for key in ("issued_on", "expires_at"):
        if values[key] is not None:
            values[key] = policy.as_utc(values[key])
    row = catalog.record_certificate(db, user, values)
    db.commit()
    return plain(row)


class ReasonIn(BaseModel):
    reason: str = Field(default="", max_length=500)


@router.post("/facilitator-certificates/{certificate_id}/revoke")
def revoke(certificate_id: int, payload: ReasonIn, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    row = catalog.revoke_certificate(db, user, certificate_id, payload.reason)
    db.commit()
    return plain(row)


@router.get("/facilitators")
def facilitators(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    held = policy.guard(db, user, "certifications", "r")
    settings = policy.settings(db, user.tenant_id)
    members = db.query(TrainingMember).filter_by(tenant_id=user.tenant_id).all()
    ids = [m.user_id for m in members if "facilitator" in {m.role, *(m.roles or [])}]
    ids += [u.id for u in db.query(User).filter_by(tenant_id=user.tenant_id, role=UserRole.admin, is_active=True) if u.id not in ids]
    if not ({"admin", "hr", "executive"} & held):
        ids = [i for i in ids if i == user.id]
    out = []
    for uid in ids:
        u = db.get(User, uid)
        m = next((m for m in members if m.user_id == uid), None)
        certs = db.query(TrainingCertificate).filter_by(tenant_id=user.tenant_id, user_id=uid).all()
        out.append({"id": u.id, "name": u.name, "email": u.email, "profile": (m.profile if m else None) or {},
                    "certificates": [{**plain(c), "status": catalog.certificate_status(c, int(settings["expiring_days"]))} for c in certs]})
    return out


@router.get("/facilitators/{facilitator_id}/scorecard")
def scorecard(facilitator_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return analytics.scorecard(db, user, facilitator_id)


@router.get("/qa-reviews")
def qa_reviews(facilitator_id: int | None = None, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    held = policy.guard(db, user, "qa_reviews", "r")
    q = db.query(TrainingQaReview).filter_by(tenant_id=user.tenant_id)
    if not ({"admin", "hr", "executive"} & held):
        q = q.filter_by(facilitator_id=user.id)
    elif facilitator_id:
        q = q.filter_by(facilitator_id=facilitator_id)
    return {"rubric": policy.settings(db, user.tenant_id)["qa_rubric"],
            "reviews": [plain(r) for r in q.order_by(TrainingQaReview.review_date.desc()).all()]}


@router.post("/qa-reviews", status_code=201)
def add_qa_review(body: Body, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    row = analytics.save_qa_review(db, user, data(body))
    db.commit()
    return plain(row)


# ================================================================ sessions


class ScheduleIn(BaseModel):
    revision_id: int
    facilitator_id: int
    co_facilitator_ids: list[int] = Field(default_factory=list)
    title: str = Field(min_length=1, max_length=200)
    location_id: int | None = None
    location: str = Field(default="", max_length=200)
    virtual_url: str = Field(default="", max_length=500)
    room: str = Field(default="", max_length=100)
    timezone: str | None = Field(default=None, max_length=60)
    starts_at: dt.datetime
    ends_at: dt.datetime
    capacity: int = Field(default=30, ge=1, le=100000)
    registration_deadline: dt.datetime | None = None
    waitlist_enabled: bool = False
    feedback_mode: Literal["identifiable", "anonymous"] | None = None
    department_id: int | None = None
    status: Literal["draft", "scheduled"] = "scheduled"
    override_reason: str = Field(default="", max_length=500)
    settings: dict[str, Any] = Field(default_factory=dict)


@router.post("/schedule", status_code=201)
def schedule(payload: ScheduleIn, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    for key in ("starts_at", "ends_at", "registration_deadline"):
        value = getattr(payload, key)
        if value is not None and value.tzinfo is None:
            raise HTTPException(422, "Include a timezone in session dates")
    row = delivery.create_session(db, user, payload.model_dump())
    db.commit()
    return plain(row)


@router.get("/calendar")
def calendar(start: dt.datetime | None = None, end: dt.datetime | None = None, program_id: int | None = None,
             location_id: int | None = None, facilitator_id: int | None = None, status: str | None = None,
             db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return delivery.calendar(db, user, policy.as_utc(start) if start else None, policy.as_utc(end) if end else None,
                             program_id, location_id, facilitator_id, status)


@router.get("/sessions/{session_id}/detail")
def session_detail(session_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    session, held = delivery.session_for(db, user, session_id, "r")
    staff = bool({"admin", "hr"} & held) or ("facilitator" in held and user.id in delivery.facilitators_of(session))
    version = db.get(TrainingRevision, session.revision_id)
    program = db.get(TrainingProgram, version.program_id)
    out = {**plain(session, ("checkin_code",)), "program": program.name, "version": catalog.label_of(version),
           "modules": version.modules or [{"title": t} for t in version.curriculum or []],
           "facilitators": [{"id": fid, "name": db.get(User, fid).name, "lead": fid == session.facilitator_id}
                            for fid in delivery.facilitators_of(session)],
           "instances": [assess.window_text(i) for i in db.query(TrainingInstance).filter_by(session_id=session.id)],
           "start_local": policy.to_local(session.starts_at, session.timezone), "end_local": policy.to_local(session.ends_at, session.timezone),
           "staff": staff, "checkin_open": delivery.checkin_open(db, session),
           "feedback_locked": assess.feedback_locked(db, session)}
    regs = db.query(TrainingRegistration).filter_by(session_id=session.id).all()
    if staff or ({"executive"} & held):
        progress = assess.invitations_for(db, session)
        out["registrations"] = [] if not staff else [
            {"id": r.id, "status": delivery.status_of(r), "source": r.source, "waitlist_position": r.waitlist_position,
             "person": delivery.person(db, r),
             "assessments": {p["phase"]: {"submitted": p["has_submitted"], "started": p["started"]}
                             for p in progress if p["registration_id"] == r.id}} for r in regs]
        out["counts"] = {s: sum(1 for r in regs if delivery.status_of(r) == s)
                         for s in ("nominated", "registered", "waitlisted", "attended", "no_show", "cancelled")}
    if staff:
        out["checkin_code"] = session.checkin_code
        out["checkin_url"] = assess.public_url(f"/p/q/{session.checkin_code}")
    mine = next((r for r in regs if r.user_id == user.id), None)
    out["mine"] = {"id": mine.id, "status": delivery.status_of(mine)} if mine else None
    return out


@router.patch("/sessions/{session_id}")
def edit_session(session_id: int, body: Body, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    values = data(body)
    for key in ("starts_at", "ends_at", "registration_deadline"):
        if values.get(key):
            parsed = dt.datetime.fromisoformat(str(values[key]).replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                raise HTTPException(422, "Include a timezone in session dates")
            values[key] = parsed
    row = delivery.update_session(db, user, session_id, values)
    db.commit()
    return plain(row)


class TransitionIn(BaseModel):
    to: Literal["scheduled", "in_progress", "completed", "closed", "cancelled"]
    reason: str = Field(default="", max_length=500)


@router.post("/sessions/{session_id}/transition")
def transition(session_id: int, payload: TransitionIn, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    row = delivery.request_transition(db, user, session_id, payload.to, payload.reason)
    db.commit()
    return plain(row)


@router.post("/sessions/{session_id}/enrol", status_code=201)
def enrol(session_id: int, body: Body, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    row = delivery.register(db, user, session_id, data(body))
    db.commit()
    return {**plain(row), "status": delivery.status_of(row)}


@router.post("/sessions/{session_id}/enrol/bulk")
def enrol_bulk(session_id: int, payload: ImportIn, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    out = delivery.bulk_register(db, user, session_id, payload.csv)
    db.commit()
    return out


@router.post("/registrations/{registration_id}/confirm")
def confirm(registration_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    row = delivery.confirm(db, user, registration_id)
    db.commit()
    return {**plain(row), "status": delivery.status_of(row)}


@router.post("/registrations/{registration_id}/cancel")
def cancel_registration(registration_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    row = delivery.cancel_registration(db, user, registration_id)
    db.commit()
    return {**plain(row), "status": delivery.status_of(row)}


class MarkIn(BaseModel):
    items: list[dict[str, Any]]


@router.post("/sessions/{session_id}/attendance")
def mark(session_id: int, payload: MarkIn, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    out = delivery.mark_attendance(db, user, session_id, payload.items, "roster")
    db.commit()
    return out


@router.post("/sessions/{session_id}/attendance/upload")
def mark_upload(session_id: int, payload: ImportIn, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    out = delivery.upload_attendance(db, user, session_id, payload.csv)
    db.commit()
    return out


def _qr_png(text: str) -> Response:
    import qrcode
    stream = io.BytesIO()
    qrcode.make(text).save(stream, format="PNG")
    return Response(stream.getvalue(), media_type="image/png", headers={"Cache-Control": "no-store"})


@router.get("/sessions/{session_id}/qr.png")
def session_qr(session_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    session, held = delivery.session_for(db, user, session_id, "r")
    if not ({"admin", "hr"} & held or user.id in delivery.facilitators_of(session)):
        raise HTTPException(403, "Only staff can show the session QR")
    return _qr_png(assess.public_url(f"/p/q/{session.checkin_code}"))


@router.post("/sessions/{session_id}/qr/regenerate")
def regenerate_qr(session_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    row = delivery.regenerate_checkin(db, user, session_id)
    db.commit()
    return {"checkin_code": row.checkin_code}


# ================================================================ analytics


@router.get("/analytics/sessions/{session_id}")
def session_analytics(session_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return analytics.session_dashboard(db, user, session_id)


@router.post("/analytics/sessions/{session_id}/recompute")
def recompute(session_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    session, held = delivery.session_for(db, user, session_id, "r")
    if not ({"admin", "hr"} & held):
        raise HTTPException(403, "Only an Org Admin or HR can recompute")
    count = analytics.recompute(db, session)
    db.commit()
    return {"participants": count}


@router.get("/analytics/compare")
def compare(group_by: str = "session", start: dt.datetime | None = None, end: dt.datetime | None = None,
            db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return analytics.compare(db, user, group_by, policy.as_utc(start) if start else None, policy.as_utc(end) if end else None)


@router.get("/analytics/export")
def export(format: Literal["csv", "xlsx", "pdf"] = "csv", session_id: int | None = None, group_by: str = "session",
           start: dt.datetime | None = None, end: dt.datetime | None = None,
           db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """ANL-008: the same scoped, anonymity-respecting numbers as the screens."""
    if session_id:
        d = analytics.session_dashboard(db, user, session_id)
        title = f"Session outcomes - {d['session']['title']}"
        summary = [f"Status: {d['session']['status']}", f"Invited {d['funnel']['invited']} / pre done {d['funnel']['pre_done']} / "
                   f"attended {d['funnel']['attended']} / post done {d['funnel']['post_done']}",
                   f"Paired participants: {d['paired_n']} (post-only {d['post_only']}, late pre excluded {d['late_pre_excluded']})"]
        if d.get("knowledge"):
            summary += [f"Knowledge: {d['knowledge']['pre']['mean']}% -> {d['knowledge']['post']['mean']}% (change {d['knowledge']['change']} pts)",
                        f"Confidence: {d['confidence']['pre']['mean']} -> {d['confidence']['post']['mean']} (change {d['confidence']['change']})",
                        f"Normalized gain: {d['gain']['mean']}", f"Outcome rate: {d['outcome_rate']}%"]
            summary += [f"Flag - {f['flag']}: {f['detail']}" for f in d["flags"]]
        if d.get("participants") is not None:
            headers = ["Participant", "Knowledge pre %", "Knowledge post %", "Change", "Confidence pre", "Confidence post", "Change", "Gain", "Band"]
            rows = [[p["name"], p["k_pre"], p["k_post"], p["dk"], p["c_pre"], p["c_post"], p["dc"], p["g"], p["band"]] for p in d["participants"]]
        else:
            headers = ["Objective", "Pre %", "Post %", "Change", "Achieved", "Of"]
            rows = [[o["code"] + " " + o["text"], o["pre"], o["post"], o["change"], o["achieved"], o["achieved_of"]] for o in d.get("objectives") or []]
        name = f"session-{session_id}"
    else:
        d = analytics.compare(db, user, group_by, policy.as_utc(start) if start else None, policy.as_utc(end) if end else None)
        title = f"Outcome comparison by {group_by}"
        headers = ["Group", "n", "Sessions", "Knowledge pre %", "Knowledge post %", "Change", "Gain", "Confidence pre", "Confidence post", "Change"]
        rows = [[g["label"], g["n"], g["sessions"], g["knowledge_pre"], g["knowledge_post"], g["knowledge_change"], g["gain"],
                 g["confidence_pre"], g["confidence_post"], g["confidence_change"]] for g in d["groups"]]
        summary = [f"Groups shown: {len(rows)}; hidden below the anonymity threshold: {d['hidden_small_groups']}"]
        name = f"compare-{group_by}"
    content, media = exporter.export(format, title, headers, rows, summary)
    policy.audit(db, user.tenant_id, user.id, "analytics.export", "training_report", name, None, {"format": format})
    db.commit()
    return Response(content, media_type=media, headers={"Content-Disposition": f'attachment; filename="{name}.{format}"'})


# ================================================================ notifications, audit, me


@router.get("/notification-templates")
def templates(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    policy.guard(db, user, "notify_templates", "r")
    return {"fields": sorted(notify.MERGE_FIELDS),
            "events": [{"event": e, **notify.template_for(db, user.tenant_id, e)} for e in notify.EVENTS]}


@router.put("/notification-templates/{event}")
def save_template(event: str, body: Body, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    out = notify.save_template(db, user, event, data(body))
    db.commit()
    return out


@router.post("/notification-templates/{event}/preview")
def preview(event: str, body: Body, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return notify.preview(db, user, event, data(body))


@router.get("/sessions/{session_id}/notifications")
def notification_log(session_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return notify.session_log(db, user, session_id)


class ResendIn(BaseModel):
    registration_ids: list[int] = Field(min_length=1, max_length=1000)
    phase: Literal["pre", "post", "feedback"] = "pre"
    regenerate: bool = False


@router.post("/notifications/resend")
def resend(payload: ResendIn, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    out = notify.resend(db, user, payload.registration_ids, payload.phase, payload.regenerate)
    db.commit()
    return out


class StatusIn(BaseModel):
    status: Literal["delivered", "bounced", "opened", "clicked", "failed"]
    hard: bool = False


@router.post("/notifications/{notification_id}/status")
def delivery_status(notification_id: int, payload: StatusIn, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    row = notify.record_status(db, user, notification_id, payload.status, payload.hard)
    db.commit()
    return {"id": row.id, "status": row.status}


@router.post("/automation/tick")
def run_tick(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    held = policy.roles(db, user)
    if not ({"admin", "hr"} & held):
        raise HTTPException(403, "Your training role does not allow this action")
    out = notify.tick(db, user.tenant_id)
    db.commit()
    return out


@router.get("/audit-log")
def audit_log(format: Literal["json", "csv"] = "json", limit: int = Query(500, le=5000),
              db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    policy.guard(db, user, "audit", "r")
    rows = db.query(TrainingAudit).filter_by(tenant_id=user.tenant_id).order_by(TrainingAudit.id.desc()).limit(limit).all()
    names = {u.id: u.name for u in db.query(User).filter_by(tenant_id=user.tenant_id)}
    items = [{"at": iso(r.at), "actor": names.get(r.actor_id, "system" if r.actor_id is None else str(r.actor_id)), "action": r.action,
              "entity": r.entity, "entity_id": r.entity_id, "before": r.before, "after": r.after, "ip": r.ip} for r in rows]
    if format == "csv":
        import json
        content = exporter.to_csv(["At", "Actor", "Action", "Entity", "Entity id", "Before", "After", "IP"],
                                  [[i["at"], i["actor"], i["action"], i["entity"], i["entity_id"], json.dumps(i["before"]),
                                    json.dumps(i["after"]), i["ip"]] for i in items])
        return Response(content, media_type=exporter.MEDIA["csv"], headers={"Content-Disposition": 'attachment; filename="training-audit.csv"'})
    return items


@router.get("/overview")
def overview(db: Session = Depends(get_db), user: User = Depends(get_current_user), access: Access = Depends(get_access)):
    """Header numbers for every Learning page and the Learning home's alerts."""
    from app.crm_core import training_overview
    return training_overview.overview(db, user, access.pages)


@router.get("/insights")
def insights(db: Session = Depends(get_db), user: User = Depends(get_current_user), access: Access = Depends(get_access)):
    """The Learning home dashboard's charts."""
    from app.crm_core import training_overview
    return training_overview.insights(db, user, access.pages)


@router.get("/me")
def me(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    held = policy.roles(db, user)
    regs = db.query(TrainingRegistration).filter_by(tenant_id=user.tenant_id, user_id=user.id).all()
    mine = []
    for r in regs:
        s = db.get(TrainingSession, r.session_id)
        mine.append({"registration_id": r.id, "session_id": s.id, "title": s.title, "starts_at": iso(s.starts_at),
                     "status": delivery.status_of(r), "session_status": s.status})
    return {"roles": sorted(held), "primary": policy.primary(held),
            "can": {res: "".join(op for op in "crudp" if policy.can(held, res, op)) for res in policy.POLICY},
            "registrations": mine, "scores": analytics.own_scores(db, user), "messages": notify.my_messages(db, user)}


# ================================================================ public: participant links (no login)


class AnswersIn(BaseModel):
    answers: dict[str, Any] = Field(default_factory=dict)
    consent: bool = False


@public.get("/a/{token}")
def open_assessment(token: str, response: Response, db: Session = Depends(get_db)):
    response.headers["Cache-Control"] = "no-store"
    return assess.open_form(db, token)


@public.put("/a/{token}/answers")
def save_answers(token: str, payload: AnswersIn, response: Response, db: Session = Depends(get_db)):
    response.headers["Cache-Control"] = "no-store"
    return assess.autosave(db, token, payload.answers)


@public.post("/a/{token}/submit")
def submit_assessment(token: str, payload: AnswersIn, response: Response, db: Session = Depends(get_db)):
    response.headers["Cache-Control"] = "no-store"
    return assess.submit(db, token, payload.answers, payload.consent)


@public.get("/q/{code}")
def qr_landing(code: str, response: Response, db: Session = Depends(get_db)):
    response.headers["Cache-Control"] = "no-store"
    return assess.qr_landing(db, code)


@public.get("/q/{code}/qr.png")
def qr_image(code: str, db: Session = Depends(get_db)):
    delivery.session_by_code(db, code)
    return _qr_png(assess.public_url(f"/p/q/{code}"))


class EmailIn(BaseModel):
    email: str = Field(min_length=3, max_length=255)


@public.post("/q/{code}/otp")
def qr_otp(code: str, payload: EmailIn, db: Session = Depends(get_db)):
    session = delivery.session_by_code(db, code)
    delivery.send_otp(db, session, payload.email)
    db.commit()
    return {"sent": True, "message": "If you are registered, a code is on its way to your email."}


class IdentifyIn(BaseModel):
    email: str = Field(min_length=3, max_length=255)
    otp: str | None = Field(default=None, max_length=10)
    phone_last4: str | None = Field(default=None, max_length=10)
    phase: Literal["pre", "post", "feedback"] | None = None


@public.post("/q/{code}/identify")
def qr_identify(code: str, payload: IdentifyIn, db: Session = Depends(get_db)):
    return assess.qr_identify(db, code, payload.email, payload.otp, payload.phone_last4, payload.phase)


@public.post("/q/{code}/anonymous")
def qr_anonymous(code: str, payload: AnswersIn, response: Response, request: Request, db: Session = Depends(get_db)):
    session = delivery.session_by_code(db, code)
    inst = assess.instance(db, session.id, "feedback")
    cookie = request.cookies.get(f"{assess.ANON_COOKIE}{inst.id}") if inst else None
    out, instance_id = assess.qr_anonymous_submit(db, code, payload.answers, cookie)
    if instance_id:
        response.set_cookie(f"{assess.ANON_COOKIE}{instance_id}", assess.anon_cookie_value(instance_id), max_age=60 * 60 * 24 * 60,
                            httponly=True, samesite="lax", secure=request.url.scheme == "https")
    return out


@public.post("/checkin/{code}")
def checkin(code: str, payload: IdentifyIn, db: Session = Depends(get_db)):
    out = delivery.qr_checkin(db, code, payload.email, payload.otp, payload.phone_last4)
    db.commit()
    return out


@public.get("/register/{code}")
def registration_page(code: str, db: Session = Depends(get_db)):
    session = delivery.session_by_code(db, code)
    s = policy.settings(db, session.tenant_id, session)
    if not (session.settings or {}).get("public_registration"):
        raise HTTPException(404, "This session does not take public registrations")
    return {"session": session.title, "starts_at": iso(session.starts_at), "start_local": policy.to_local(session.starts_at, session.timezone),
            "open": session.status == "scheduled" and session.starts_at > utcnow()
                    and (session.registration_deadline is None or utcnow() <= session.registration_deadline),
            "brand": s["brand"]}


class PublicRegisterIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    email: str = Field(min_length=3, max_length=255)
    phone: str = Field(default="", max_length=40)
    organization: str = Field(default="", max_length=200)
    consent: bool = False


@public.post("/register/{code}", status_code=201)
def public_register(code: str, payload: PublicRegisterIn, db: Session = Depends(get_db)):
    """SES-005 (c) / SES-007: external self-registration - no password is created."""
    session = delivery.session_by_code(db, code)
    if not (session.settings or {}).get("public_registration"):
        raise HTTPException(404, "This session does not take public registrations")
    if not payload.consent:
        raise HTTPException(422, "Please agree to how your details are used")
    user_id, participant_id = delivery.find_or_create_participant(db, session.tenant_id, payload.model_dump())
    row = delivery.enrol(db, session, user_id, participant_id, "self", None)
    db.commit()
    return {"status": delivery.status_of(row), "waitlist_position": row.waitlist_position}


@public.post("/u/{token}")
def unsubscribe(token: str, db: Session = Depends(get_db)):
    return notify.unsubscribe(db, token)


_GIF = bytes.fromhex("47494638396101000100800000000000ffffff21f90401000000002c00000000010001000002024401003b")


@public.get("/o/{notification_id}/{token}.gif")
def opened(notification_id: int, token: str, db: Session = Depends(get_db)):
    notify.mark_opened(db, notification_id, token)
    return Response(_GIF, media_type="image/gif", headers={"Cache-Control": "no-store"})

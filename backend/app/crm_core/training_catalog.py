"""Training catalog: locations (ORG-002), the participant directory and its
CSV import and erasure (ORG-004, ORG-011, SES-007), programs and versions
(PRG-001..006), assessment templates (ASM-001..007) and certifications
(FAC-002, PRG-006)."""
from __future__ import annotations

import csv
import io
import re
import secrets

from fastapi import HTTPException

from app.crm_core import training_policy as policy
from app.crm_core import training_scoring as scoring
from app.models.auth import User
from app.models.org import Department
from app.models.training import (TrainingCertificate, TrainingCertification, TrainingInstance, TrainingLocation,
                                 TrainingParticipant, TrainingProgram, TrainingRevision, TrainingSession,
                                 TrainingSettings, TrainingTemplate)
from app.timeutils import utcnow

EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
ITEM_KEY = re.compile(r"^[a-z][a-z0-9_]{0,49}$")


def owned(db, model, tenant_id, record_id):
    row = db.query(model).filter(model.id == record_id, model.tenant_id == tenant_id).first() if record_id is not None else None
    if row is None:
        raise HTTPException(404, "Record not found")
    return row


# ---------------------------------------------------------------- settings (ORG-010)


def save_settings(db, user: User, data: dict) -> dict:
    policy.guard(db, user, "settings", "u")
    clean = policy.validate_settings(data)
    row = db.query(TrainingSettings).filter_by(tenant_id=user.tenant_id).first()
    before = policy.settings(db, user.tenant_id)
    if row is None:
        row = TrainingSettings(tenant_id=user.tenant_id, data=clean, version=1)
        db.add(row)
    else:
        row.data = clean
        row.version = (row.version or 1) + 1
    policy.audit(db, user.tenant_id, user.id, "settings.update", "training_settings", row.version, before, clean)
    db.flush()
    return {"version": row.version, "settings": clean}


# ---------------------------------------------------------------- locations (ORG-002)


def save_location(db, user: User, payload: dict, location_id: int | None = None) -> TrainingLocation:
    policy.guard(db, user, "locations", "u" if location_id else "c")
    if payload.get("timezone"):
        try:
            from zoneinfo import ZoneInfo
            ZoneInfo(payload["timezone"])
        except Exception:
            raise HTTPException(422, "Unknown time zone")
    clash = db.query(TrainingLocation).filter(TrainingLocation.tenant_id == user.tenant_id,
                                              TrainingLocation.code == payload.get("code"),
                                              TrainingLocation.id != (location_id or 0)).first()
    if clash:
        raise HTTPException(409, "A location with this code already exists")
    if location_id:
        row = owned(db, TrainingLocation, user.tenant_id, location_id)
        before = policy.snapshot(row)
        for key in ("name", "code", "address", "timezone", "status"):
            if key in payload and payload[key] is not None:
                setattr(row, key, payload[key])
        policy.audit(db, user.tenant_id, user.id, "location.update", "training_location", row.id, before, policy.snapshot(row))
    else:
        row = TrainingLocation(tenant_id=user.tenant_id, name=payload["name"], code=payload["code"],
                               address=payload.get("address") or "", timezone=payload.get("timezone") or "UTC",
                               status=payload.get("status") or "active")
        db.add(row)
        db.flush()
        policy.audit(db, user.tenant_id, user.id, "location.create", "training_location", row.id, None, policy.snapshot(row))
    db.flush()
    return row


def active_location(db, tenant_id: int, location_id: int | None) -> TrainingLocation | None:
    if location_id is None:
        return None
    row = owned(db, TrainingLocation, tenant_id, location_id)
    if row.status != "active":
        raise HTTPException(422, "Sessions can only be scheduled at an active location")
    return row


# ---------------------------------------------------------------- participant directory (ORG-004, ORG-011)


def _email_taken(db, tenant_id: int, email: str, exclude_id: int | None = None) -> bool:
    email = email.strip().lower()
    q = db.query(TrainingParticipant).filter(TrainingParticipant.tenant_id == tenant_id,
                                             TrainingParticipant.email == email)
    if exclude_id:
        q = q.filter(TrainingParticipant.id != exclude_id)
    if q.first():
        return True
    return db.query(User).filter(User.tenant_id == tenant_id, User.email == email).first() is not None


def _participant_problems(db, tenant_id: int, row: dict) -> tuple[list[str], dict]:
    problems, clean = [], {}
    name = (row.get("name") or "").strip()
    email = (row.get("email") or "").strip().lower()
    if not name:
        problems.append("name is required")
    if not EMAIL.match(email):
        problems.append("email is not valid")
    clean.update(name=name[:200], email=email, phone=(row.get("phone") or "").strip()[:40],
                 employee_id=(row.get("employee_id") or "").strip()[:60],
                 organization=(row.get("organization") or "").strip()[:200], manager=(row.get("manager") or "").strip()[:200])
    location = (row.get("location") or "").strip()
    if location:
        found = db.query(TrainingLocation).filter_by(tenant_id=tenant_id, code=location, status="active").first()
        if found is None:
            problems.append(f"location '{location}' is not an active location code")
        else:
            clean["location_id"] = found.id
    department = (row.get("department") or "").strip()
    if department:
        found = db.query(Department).filter_by(tenant_id=tenant_id, name=department, is_active=True).first()
        if found is None:
            problems.append(f"department '{department}' does not exist")
        else:
            clean["department_id"] = found.id
    for role in [r.strip() for r in (row.get("roles") or "").replace(";", ",").split(",") if r.strip()]:
        if role != "participant":
            problems.append(f"role '{role}' needs a login - add the person as a user, then assign the role under People")
    return problems, clean


def create_participant(db, user: User, payload: dict) -> TrainingParticipant:
    policy.guard(db, user, "people", "c")
    problems, clean = _participant_problems(db, user.tenant_id, payload)
    if not problems and _email_taken(db, user.tenant_id, clean["email"]):
        problems.append("a person with this email already exists in this organization")
    if problems:
        raise HTTPException(422, "; ".join(problems))
    row = TrainingParticipant(tenant_id=user.tenant_id, **clean)
    db.add(row)
    db.flush()
    policy.audit(db, user.tenant_id, user.id, "participant.create", "training_participant", row.id, None, {"id": row.id})
    return row


def import_participants(db, user: User, text: str, commit: bool) -> dict:
    """ORG-004: every row is validated and reported; rows with errors create
    nothing; with commit=False nothing is created at all (a dry run)."""
    policy.guard(db, user, "people", "c")
    reader = csv.DictReader(io.StringIO(text.lstrip("﻿")))
    if not reader.fieldnames or not {"name", "email"} <= {f.strip().lower() for f in reader.fieldnames}:
        raise HTTPException(422, "The CSV needs a header row with at least name and email")
    seen: set[str] = set()
    report, created = [], 0
    for number, raw in enumerate(reader, start=2):
        row = {(k or "").strip().lower(): (v or "") for k, v in raw.items()}
        problems, clean = _participant_problems(db, user.tenant_id, row)
        email = clean.get("email", "")
        if email in seen:
            problems.append("duplicate email in this file")
        elif email and _email_taken(db, user.tenant_id, email):
            problems.append("a person with this email already exists in this organization")
        seen.add(email)
        if not problems and commit:
            db.add(TrainingParticipant(tenant_id=user.tenant_id, **clean))
            created += 1
        report.append({"row": number, "email": email, "ok": not problems, "errors": problems})
    if commit and created:
        db.flush()
        policy.audit(db, user.tenant_id, user.id, "participant.import", "training_participant", "", None, {"created": created})
    return {"rows": report, "valid": sum(r["ok"] for r in report), "invalid": sum(not r["ok"] for r in report),
            "created": created, "committed": commit}


def erase_participant(db, user: User, participant_id: int) -> TrainingParticipant:
    """ORG-011: personal fields become a token; results stay in aggregates and
    exports can no longer identify the person."""
    policy.guard(db, user, "people", "d")
    row = owned(db, TrainingParticipant, user.tenant_id, participant_id)
    token = "ERASED-" + secrets.token_hex(6).upper()
    row.name, row.email, row.phone = token, f"{token.lower()}@erased.invalid", ""
    row.employee_id = row.manager = row.organization = ""
    row.status, row.erased_at = "erased", utcnow()
    policy.audit(db, user.tenant_id, user.id, "participant.erase", "training_participant", row.id, None, {"token": token})
    db.flush()
    return row


# ---------------------------------------------------------------- assessment templates (ASM-001..007)


def _clean_question(q: dict, index: int) -> dict:
    key = (q.get("item_key") or "").strip()
    kind = q.get("type") or "single"
    measure = q.get("measure") or "feedback"
    text = (q.get("text") or "").strip()
    problems = []
    if not ITEM_KEY.match(key):
        problems.append("item_key must be lower-case letters, digits or _ and start with a letter")
    if kind not in scoring.QUESTION_TYPES:
        problems.append(f"unknown type '{kind}'")
    if measure not in scoring.MEASURES:
        problems.append(f"unknown measure '{measure}'")
    if not text or len(text) > 1000:
        problems.append("text must be 1-1000 characters")
    options = [str(o) for o in (q.get("options") or [])]
    if kind == "true_false":
        options = options[:2] if len(options) == 2 else ["True", "False"]
    if kind in ("single", "multi") and not 2 <= len(options) <= 12:
        problems.append("choice questions need 2-12 options")
    if kind not in scoring.CHOICE_TYPES:
        options = []
    correct = sorted({int(c) for c in (q.get("correct_options") or [])})
    points = float(q.get("points") or (1 if measure == "knowledge" else 0))
    if measure == "knowledge":
        if kind not in scoring.CHOICE_TYPES:
            problems.append("knowledge questions must be single, multiple choice or true/false")
        elif not correct or any(not 0 <= c < len(options) for c in correct):
            problems.append("knowledge questions need correct option(s)")
        elif kind != "multi" and len(correct) != 1 and kind == "true_false":
            problems.append("a true/false question has one correct answer")
        if points <= 0:
            problems.append("knowledge questions need points above 0")
    else:
        correct, points = [], 0
    if measure == "confidence" and kind != "likert5":
        problems.append("confidence questions use the Likert 1-5 type")
    if problems:
        raise HTTPException(422, f"Question {index + 1}: " + "; ".join(problems))
    return {"item_key": key, "section": q.get("section") or "", "type": kind, "text": text, "options": options,
            "correct_options": correct, "points": points, "measure": measure,
            "objective": (q.get("objective") or "").strip() or None, "required": bool(q.get("required", True)),
            "help": (q.get("help") or "")[:500]}


def _clean_template(payload: dict) -> dict:
    kind = payload.get("type")
    if kind not in ("pre", "post", "feedback"):
        raise HTTPException(422, "type must be pre, post or feedback")
    questions = [_clean_question(q, i) for i, q in enumerate(payload.get("questions") or [])]
    if not questions:
        raise HTTPException(422, "Add at least one question")
    keys = [q["item_key"] for q in questions]
    if len(set(keys)) != len(keys):
        raise HTTPException(422, "item_key must be unique within a template")
    s = payload.get("settings") or {}
    settings = {"randomize_questions": bool(s.get("randomize_questions")), "randomize_options": bool(s.get("randomize_options")),
                "time_limit_min": int(s["time_limit_min"]) if s.get("time_limit_min") else None,
                "one_attempt": bool(s.get("one_attempt", True))}
    sections = [{"key": str(x.get("key") or f"s{i + 1}"), "title": str(x.get("title") or "")[:200]}
                for i, x in enumerate(payload.get("sections") or [])]
    return {"type": kind, "name": (payload.get("name") or "").strip()[:200] or "Untitled", "settings": settings,
            "sections": sections, "questions": questions}


def template_in_use(db, template: TrainingTemplate) -> bool:
    if template.state != "draft":
        return True
    if db.query(TrainingInstance).filter_by(template_id=template.id).first():
        return True
    refs = (TrainingRevision.pre_template_id == template.id) | (TrainingRevision.post_template_id == template.id) | \
           (TrainingRevision.feedback_template_id == template.id)
    return db.query(TrainingRevision).filter(refs, TrainingRevision.state.in_(("published", "retired"))).first() is not None


def save_template(db, user: User, payload: dict, template_id: int | None = None) -> TrainingTemplate:
    policy.guard(db, user, "templates", "u" if template_id else "c")
    clean = _clean_template(payload)
    if template_id:
        row = owned(db, TrainingTemplate, user.tenant_id, template_id)
        if template_in_use(db, row):
            raise HTTPException(409, "This template is published or in use - clone it to make changes (ASM-001)")
        if row.type != clean["type"]:
            raise HTTPException(422, "A template's type cannot change")
        for key, value in clean.items():
            setattr(row, key, value)
        if payload.get("version_label"):
            row.version_label = str(payload["version_label"])[:20]
        policy.audit(db, user.tenant_id, user.id, "template.update", "training_template", row.id)
    else:
        row = TrainingTemplate(tenant_id=user.tenant_id, state="draft", version_label=str(payload.get("version_label") or "1.0")[:20], **clean)
        db.add(row)
        db.flush()
        row.family_id = row.id
        policy.audit(db, user.tenant_id, user.id, "template.create", "training_template", row.id)
    db.flush()
    return row


def clone_template(db, user: User, template_id: int) -> TrainingTemplate:
    policy.guard(db, user, "templates", "c")
    source = owned(db, TrainingTemplate, user.tenant_id, template_id)
    major, _, minor = (source.version_label or "1.0").partition(".")
    label = f"{major}.{int(minor or 0) + 1}" if (minor or "0").isdigit() else f"{source.version_label}-copy"
    row = TrainingTemplate(tenant_id=user.tenant_id, type=source.type, name=source.name, version_label=label,
                           family_id=source.family_id or source.id, state="draft", settings=dict(source.settings or {}),
                           sections=list(source.sections or []), questions=[dict(q) for q in source.questions or []])
    db.add(row)
    db.flush()
    policy.audit(db, user.tenant_id, user.id, "template.clone", "training_template", row.id, None, {"from": source.id})
    return row


def publish_template(db, user: User, template_id: int) -> TrainingTemplate:
    policy.guard(db, user, "templates", "u")
    row = owned(db, TrainingTemplate, user.tenant_id, template_id)
    if row.state == "draft":
        row.state = "published"
        policy.audit(db, user.tenant_id, user.id, "template.publish", "training_template", row.id)
    db.flush()
    return row


# ---------------------------------------------------------------- programs and versions (PRG-001..006)

DELIVERY_MODES = ("in_person", "virtual", "hybrid")


def _objectives(items) -> list[dict]:
    out, seen = [], set()
    for i, item in enumerate(items or []):
        if isinstance(item, str):
            item = {"text": item}
        text = (item.get("text") or "").strip()
        if not text:
            continue
        code = (item.get("code") or f"LO{i + 1}").strip()[:50]
        if code in seen:
            raise HTTPException(422, f"Objective code {code} is used twice")
        seen.add(code)
        out.append({"code": code, "text": text[:1000]})
    return out


def _modules(items) -> list[dict]:
    out = []
    for i, m in enumerate(items or []):
        title = (m.get("title") or "").strip()
        if not title:
            raise HTTPException(422, f"Module {i + 1} needs a title")
        materials = []
        for mat in m.get("materials") or []:
            url = (mat.get("url") if isinstance(mat, dict) else str(mat)).strip()
            if url and not url.lower().startswith(("http://", "https://", "/")):
                raise HTTPException(422, f"Module {i + 1}: materials must be http(s) links")
            if url:
                materials.append({"label": (mat.get("label") if isinstance(mat, dict) else "") or url, "url": url})
        out.append({"title": title[:200], "objectives": [str(o)[:50] for o in m.get("objectives") or []],
                    "duration_min": max(0, int(m.get("duration_min") or 0)), "materials": materials,
                    "notes": (m.get("notes") or "")[:4000]})
    return out


def create_program(db, user: User, payload: dict) -> tuple[TrainingProgram, TrainingRevision]:
    policy.guard(db, user, "programs", "c")
    code = (payload.get("code") or "").strip()
    title = (payload.get("title") or payload.get("name") or "").strip()
    if not code or not title:
        raise HTTPException(422, "A program needs a code and a title")
    if db.query(TrainingProgram).filter_by(tenant_id=user.tenant_id, code=code).first():
        raise HTTPException(409, "Program code must be unique in your organization (PRG-001)")
    mode = payload.get("delivery_mode") or "in_person"
    if mode not in DELIVERY_MODES:
        raise HTTPException(422, "delivery_mode must be in_person, virtual or hybrid")
    objectives = _objectives(payload.get("objectives"))
    certification = payload.get("required_certification_id")
    if certification:
        owned(db, TrainingCertification, user.tenant_id, certification)
    program = TrainingProgram(tenant_id=user.tenant_id, name=title[:200], description=(payload.get("description") or "")[:4000],
                              code=code[:50], category=(payload.get("category") or "")[:100], audience=(payload.get("audience") or "")[:200],
                              delivery_mode=mode, duration_hours=payload.get("duration_hours"), objectives=objectives,
                              status="active", required_certification_id=certification)
    db.add(program)
    db.flush()
    version = TrainingRevision(tenant_id=user.tenant_id, program_id=program.id, version=1, version_label="1.0", state="draft",
                               curriculum=[], questions=[], modules=_modules(payload.get("modules")), objectives=objectives,
                               duration_hours=payload.get("duration_hours"), required_certification_id=certification)
    db.add(version)
    db.flush()
    policy.audit(db, user.tenant_id, user.id, "program.create", "training_program", program.id, None, policy.snapshot(program))
    return program, version


def update_program(db, user: User, program_id: int, payload: dict) -> TrainingProgram:
    policy.guard(db, user, "programs", "u")
    row = owned(db, TrainingProgram, user.tenant_id, program_id)
    before = policy.snapshot(row)
    if "code" in payload and payload["code"] != row.code:
        if db.query(TrainingProgram).filter(TrainingProgram.tenant_id == user.tenant_id, TrainingProgram.code == payload["code"],
                                            TrainingProgram.id != row.id).first():
            raise HTTPException(409, "Program code must be unique in your organization (PRG-001)")
        row.code = payload["code"]
    if payload.get("title"):
        row.name = payload["title"][:200]
    for key in ("description", "category", "audience", "duration_hours", "status"):
        if key in payload:
            setattr(row, key, payload[key])
    if "delivery_mode" in payload:
        if payload["delivery_mode"] not in DELIVERY_MODES:
            raise HTTPException(422, "delivery_mode must be in_person, virtual or hybrid")
        row.delivery_mode = payload["delivery_mode"]
    if "objectives" in payload:
        row.objectives = _objectives(payload["objectives"])
    if "required_certification_id" in payload:
        if payload["required_certification_id"]:
            owned(db, TrainingCertification, user.tenant_id, payload["required_certification_id"])
        row.required_certification_id = payload["required_certification_id"] or None
    policy.audit(db, user.tenant_id, user.id, "program.update", "training_program", row.id, before, policy.snapshot(row))
    db.flush()
    return row


def state_of(version: TrainingRevision) -> str:
    return version.state or "published"  # rows from before versioning were published on creation


def label_of(version: TrainingRevision) -> str:
    return version.version_label or f"{version.version}.0"


def new_version(db, user: User, program_id: int, base_id: int | None, bump: str = "minor") -> TrainingRevision:
    """PRG-003: editing a published version starts a new draft (minor x.y+1 or major x+1.0)."""
    policy.guard(db, user, "versions", "c")
    program = owned(db, TrainingProgram, user.tenant_id, program_id)
    versions = db.query(TrainingRevision).filter_by(tenant_id=user.tenant_id, program_id=program.id).all()
    if any(state_of(v) == "draft" for v in versions):
        raise HTTPException(409, "This program already has a draft version - edit that one")
    base = owned(db, TrainingRevision, user.tenant_id, base_id) if base_id else max(versions, key=lambda v: v.version)
    if base.program_id != program.id:
        raise HTTPException(422, "That version belongs to another program")
    major, _, minor = label_of(base).partition(".")
    try:
        label = f"{int(major) + 1}.0" if bump == "major" else f"{int(major)}.{int(minor or 0) + 1}"
    except ValueError:
        label = f"{base.version + 1}.0"
    taken = {label_of(v) for v in versions}
    while label in taken:
        major, _, minor = label.partition(".")
        label = f"{major}.{int(minor) + 1}"
    modules = base.modules if base.modules is not None else [{"title": t, "objectives": [], "duration_min": 0, "materials": [], "notes": ""} for t in base.curriculum or []]
    row = TrainingRevision(tenant_id=user.tenant_id, program_id=program.id, version=max(v.version for v in versions) + 1,
                           version_label=label, state="draft", curriculum=[], questions=[],
                           modules=[dict(m) for m in modules or []], objectives=list(base.objectives or program.objectives or []),
                           duration_hours=base.duration_hours, pre_template_id=base.pre_template_id,
                           post_template_id=base.post_template_id, feedback_template_id=base.feedback_template_id,
                           assessments_waived=base.assessments_waived, waive_reason=base.waive_reason,
                           required_certification_id=base.required_certification_id or program.required_certification_id)
    db.add(row)
    db.flush()
    policy.audit(db, user.tenant_id, user.id, "version.create", "training_revision", row.id, None, {"from": base.id, "label": label})
    return row


def update_version(db, user: User, version_id: int, payload: dict) -> TrainingRevision:
    policy.guard(db, user, "versions", "u")
    row = owned(db, TrainingRevision, user.tenant_id, version_id)
    if state_of(row) != "draft":
        raise HTTPException(409, "Published versions are immutable - start a new version (PRG-003)")
    if "modules" in payload:
        row.modules = _modules(payload["modules"])
    if "objectives" in payload:
        row.objectives = _objectives(payload["objectives"])
    for key in ("duration_hours", "waive_reason"):
        if key in payload:
            setattr(row, key, payload[key])
    if "assessments_waived" in payload:
        row.assessments_waived = bool(payload["assessments_waived"])
    if "version_label" in payload and payload["version_label"]:
        row.version_label = str(payload["version_label"])[:20]
    for field, kind in (("pre_template_id", "pre"), ("post_template_id", "post"), ("feedback_template_id", "feedback")):
        if field in payload:
            if payload[field]:
                template = owned(db, TrainingTemplate, user.tenant_id, payload[field])
                if template.type != kind:
                    raise HTTPException(422, f"{field} must be a {kind} template")
            setattr(row, field, payload[field] or None)
    if "required_certification_id" in payload:
        if payload["required_certification_id"]:
            owned(db, TrainingCertification, user.tenant_id, payload["required_certification_id"])
        row.required_certification_id = payload["required_certification_id"] or None
    policy.audit(db, user.tenant_id, user.id, "version.update", "training_revision", row.id)
    db.flush()
    return row


def publish_problems(db, row: TrainingRevision) -> list[str]:
    problems = []
    if not row.modules:
        problems.append("Add at least one curriculum module")
    pre = db.get(TrainingTemplate, row.pre_template_id) if row.pre_template_id else None
    post = db.get(TrainingTemplate, row.post_template_id) if row.post_template_id else None
    if not (pre and post):
        if not row.assessments_waived:
            problems.append("Link a pre and a post assessment template, or tick 'assessments not required' with a reason (PRG-004)")
        elif not (row.waive_reason or "").strip():
            problems.append("Give the reason assessments are not required (PRG-004)")
        return problems
    if not scoring.paired_keys(pre.questions, post.questions, "knowledge"):
        problems.append("Pre and post templates must share at least one paired Knowledge item (same item_key) (ASM-004)")
    if not scoring.paired_keys(pre.questions, post.questions, "confidence"):
        problems.append("Pre and post templates must share at least one paired Confidence item (same item_key) (ASM-004)")
    codes = {o["code"] for o in row.objectives or []}
    for template in (pre, post):
        for q in template.questions:
            if q["measure"] in ("knowledge", "confidence") and q.get("objective") not in codes:
                problems.append(f"{template.name} · {q['item_key']}: map it to one of this version's learning objectives (ASM-005)")
    return problems


def publish_version(db, user: User, version_id: int) -> TrainingRevision:
    policy.guard(db, user, "versions", "p")
    row = owned(db, TrainingRevision, user.tenant_id, version_id)
    if state_of(row) != "draft":
        raise HTTPException(409, "Only a draft version can be published")
    problems = publish_problems(db, row)
    if problems:
        raise HTTPException(422, problems)
    for template_id in (row.pre_template_id, row.post_template_id, row.feedback_template_id):
        if template_id:
            template = db.get(TrainingTemplate, template_id)
            template.state = "published"  # in use from now on: clone to change (ASM-001)
    row.state, row.published_at = "published", utcnow()
    row.curriculum = [m["title"] for m in row.modules or []]
    policy.audit(db, user.tenant_id, user.id, "version.publish", "training_revision", row.id, {"state": "draft"}, {"state": "published"})
    db.flush()
    return row


def retire_version(db, user: User, version_id: int) -> TrainingRevision:
    policy.guard(db, user, "versions", "p")
    row = owned(db, TrainingRevision, user.tenant_id, version_id)
    if state_of(row) != "published":
        raise HTTPException(409, "Only a published version can be retired")
    row.state, row.retired_at = "retired", utcnow()
    policy.audit(db, user.tenant_id, user.id, "version.retire", "training_revision", row.id, {"state": "published"}, {"state": "retired"})
    db.flush()
    return row


def diff_versions(db, user: User, a_id: int, b_id: int) -> dict:
    """PRG-005: module-level changes with field names (modules matched by title)."""
    policy.guard(db, user, "versions", "r")
    a, b = owned(db, TrainingRevision, user.tenant_id, a_id), owned(db, TrainingRevision, user.tenant_id, b_id)
    if a.program_id != b.program_id:
        raise HTTPException(422, "Compare two versions of the same program")

    def modules(v):
        items = v.modules if v.modules is not None else [{"title": t} for t in v.curriculum or []]
        return {m["title"]: m for m in items}
    old, new = modules(a), modules(b)
    changed = []
    for title in old.keys() & new.keys():
        fields = [f for f in ("objectives", "duration_min", "materials", "notes") if old[title].get(f) != new[title].get(f)]
        if list(old).index(title) != list(new).index(title):
            fields.append("order")
        if fields:
            changed.append({"title": title, "fields": fields})
    oa = {o["code"]: o["text"] for o in a.objectives or []}
    ob = {o["code"]: o["text"] for o in b.objectives or []}
    return {"from": label_of(a), "to": label_of(b),
            "added": [t for t in new if t not in old], "removed": [t for t in old if t not in new], "changed": changed,
            "objectives": {"added": [c for c in ob if c not in oa], "removed": [c for c in oa if c not in ob],
                           "changed": [c for c in oa.keys() & ob.keys() if oa[c] != ob[c]]}}


# ---------------------------------------------------------------- certifications (FAC-002)


def certificate_status(cert: TrainingCertificate, expiring_days: int, now=None) -> str:
    import datetime as dt
    now = now or utcnow()
    if cert.revoked:
        return "Revoked"
    if cert.expires_at <= now:
        return "Expired"
    if cert.expires_at <= now + dt.timedelta(days=expiring_days):
        return "Expiring"
    return "Valid"


def save_certification(db, user: User, payload: dict) -> TrainingCertification:
    policy.guard(db, user, "certifications", "c")
    name = (payload.get("name") or "").strip()
    if not name:
        raise HTTPException(422, "A certification needs a name")
    program_ids = [int(p) for p in payload.get("program_ids") or []]
    for pid in program_ids:
        owned(db, TrainingProgram, user.tenant_id, pid)
    row = TrainingCertification(tenant_id=user.tenant_id, name=name[:200], issuer=(payload.get("issuer") or "")[:200],
                                validity_months=payload.get("validity_months"), program_ids=program_ids)
    db.add(row)
    db.flush()
    for pid in program_ids:  # PRG-006: the programs now require it
        program = db.get(TrainingProgram, pid)
        if program.required_certification_id is None:
            program.required_certification_id = row.id
    policy.audit(db, user.tenant_id, user.id, "certification.create", "training_certification", row.id, None, policy.snapshot(row))
    db.flush()
    return row


def record_certificate(db, user: User, payload: dict) -> TrainingCertificate:
    """A facilitator's certificate with issuer, dates and evidence (FAC-002)."""
    import datetime as dt
    policy.guard(db, user, "certifications", "c")
    facilitator = owned(db, User, user.tenant_id, payload["user_id"])
    certification = owned(db, TrainingCertification, user.tenant_id, payload["certification_id"]) if payload.get("certification_id") else None
    issued = payload.get("issued_on")
    expires = payload.get("expires_at")
    if expires is None and certification and certification.validity_months and issued:
        expires = issued + dt.timedelta(days=30 * certification.validity_months)
    if expires is None:
        raise HTTPException(422, "Give the expiry date")
    if issued and expires <= issued:
        raise HTTPException(422, "Expiry must follow the issue date")
    evidence = (payload.get("evidence_url") or "").strip()
    if evidence and not evidence.lower().startswith(("http://", "https://")):
        raise HTTPException(422, "Evidence must be an http(s) link")
    row = TrainingCertificate(tenant_id=user.tenant_id, user_id=facilitator.id,
                              name=(payload.get("name") or (certification.name if certification else "")).strip()[:200] or "Certification",
                              expires_at=expires, quality_notes=(payload.get("quality_notes") or "")[:4000],
                              certification_id=certification.id if certification else None,
                              issuer=(payload.get("issuer") or (certification.issuer if certification else ""))[:200],
                              issued_on=issued, evidence_url=evidence, revoked=False)
    db.add(row)
    db.flush()
    policy.audit(db, user.tenant_id, user.id, "certificate.create", "training_certificate", row.id, None, policy.snapshot(row))
    return row


def revoke_certificate(db, user: User, certificate_id: int, reason: str) -> TrainingCertificate:
    policy.guard(db, user, "certifications", "u")
    row = owned(db, TrainingCertificate, user.tenant_id, certificate_id)
    row.revoked = True
    policy.audit(db, user.tenant_id, user.id, "certificate.revoke", "training_certificate", row.id, None, {"reason": reason})
    db.flush()
    return row


def required_certification(db, session: TrainingSession) -> int | None:
    version = db.get(TrainingRevision, session.revision_id)
    if version and version.required_certification_id:
        return version.required_certification_id
    program = db.get(TrainingProgram, version.program_id) if version else None
    return program.required_certification_id if program else None

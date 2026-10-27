"""Training module BRD v1.0 - requirement IDs are in every test name."""
import datetime as dt

import pytest

from app.crm_core import training_assess as assess
from app.crm_core import training_notify as notify
from app.crm_core import training_policy as policy
from app.models.auth import User, UserRole
from app.models.tenant import Tenant
from app.models.training import (TrainingAnonymousResponse, TrainingAttendance, TrainingAudit, TrainingInstance, TrainingInvitation,
                                 TrainingMember, TrainingNotification, TrainingParticipant, TrainingRegistration, TrainingSession)
from app.timeutils import utcnow


@pytest.fixture(autouse=True)
def _open_rate_limit(monkeypatch):
    policy._hits.clear()
    monkeypatch.setattr(policy, "RATE_LIMIT", 10_000)
    monkeypatch.delenv("TRAINING_EMAIL_ADAPTER", raising=False)


def licensed(client):
    with client.TestSessionLocal() as db:
        tenant = db.query(Tenant).first()
        tenant.licensed_modules = ["training_management"]
        admin = db.query(User).filter_by(role=UserRole.admin, tenant_id=tenant.id).first()
        db.commit()
        return tenant.id, admin.id


def make_user(client, name, roles=(), tenant_id=None, phone=None, department_id=None, scope_departments=None):
    with client.TestSessionLocal() as db:
        tid = tenant_id or db.query(Tenant).first().id
        u = User(tenant_id=tid, name=name, email=f"{name.lower().replace(' ', '.')}@example.test", password_hash="x", role=UserRole.viewer)
        db.add(u)
        db.flush()
        if roles or phone:
            held = set(roles) or {"participant"}
            db.add(TrainingMember(tenant_id=tid, user_id=u.id, role=policy.primary(held), roles=sorted(held), location="",
                                  phone=phone, department_id=department_id, scope_department_ids=scope_departments))
        db.commit()
        return u.id


def as_(uid):
    return {"X-Test-User-Id": str(uid)}


def iso(delta_hours):
    return (utcnow() + dt.timedelta(hours=delta_hours)).isoformat() + "Z"


PRE = {"type": "pre", "name": "Safety pre", "questions": [
    {"item_key": "k1", "type": "single", "text": "Which is safe?", "options": ["A", "B"], "correct_options": [1], "points": 1, "measure": "knowledge", "objective": "LO1"},
    {"item_key": "k2", "type": "true_false", "text": "Helmets matter", "correct_options": [0], "points": 1, "measure": "knowledge", "objective": "LO2"},
    {"item_key": "c1", "type": "likert5", "text": "How confident are you?", "measure": "confidence", "objective": "LO1"},
]}
POST = {**PRE, "type": "post", "name": "Safety post"}
FEEDBACK = {"type": "feedback", "name": "Safety feedback", "questions": [
    {"item_key": "f1", "type": "likert5", "text": "Overall", "measure": "feedback"},
    {"item_key": "f2", "type": "long_text", "text": "Comments", "measure": "feedback", "required": False},
]}


def published_version(client, code="SAFE", feedback=True, certification_id=None):
    ids = {}
    for name, body in (("pre", PRE), ("post", POST), ("feedback", FEEDBACK)):
        if name == "feedback" and not feedback:
            continue
        r = client.post("/api/training/templates", json=body)
        assert r.status_code == 201, r.text
        ids[name] = r.json()["id"]
    r = client.post("/api/training/catalog/programs", json={"code": code, "title": "Safe working", "delivery_mode": "in_person",
                                                             "objectives": ["Spot hazards", "Use protective equipment"],
                                                             "modules": [{"title": "Hazards", "duration_min": 30}],
                                                             "required_certification_id": certification_id})
    assert r.status_code == 201, r.text
    version_id = r.json()["version"]["id"]
    body = {"pre_template_id": ids["pre"], "post_template_id": ids["post"]}
    if feedback:
        body["feedback_template_id"] = ids["feedback"]
    assert client.patch(f"/api/training/versions/{version_id}", json=body).status_code == 200
    r = client.post(f"/api/training/versions/{version_id}/publish")
    assert r.status_code == 200, r.text
    return r.json()["program_id"], version_id, ids


def location(client, code="BLR"):
    r = client.post("/api/training/locations", json={"name": f"Office {code}", "code": code, "timezone": "Asia/Kolkata"})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def schedule(client, version_id, facilitator_id, start=48, hours=3, **extra):
    body = {"revision_id": version_id, "facilitator_id": facilitator_id, "title": "Safety day", "starts_at": iso(start),
            "ends_at": iso(start + hours), "capacity": 30, **extra}
    if "location_id" not in body and "virtual_url" not in body:
        body["location"] = "Main hall"
    return client.post("/api/training/schedule", json=body, headers=extra.pop("headers", None) if "headers" in extra else None)


def enrol_external(client, sid, n):
    out = []
    for i in range(n):
        r = client.post(f"/api/training/sessions/{sid}/enrol", json={"email": f"p{i}@ext.test", "name": f"Person {i}", "phone": f"+91 98450 1{i:04d}"})
        assert r.status_code == 201, r.text
        out.append(r.json()["id"])
    return out


def token(client, registration_id, phase):
    with client.TestSessionLocal() as db:
        inv = db.query(TrainingInvitation).filter_by(registration_id=registration_id, phase=phase).first()
        return assess.token_for(inv) if inv else None


def shift(client, sid, hours):
    """Moves a session (and its assessment windows) `hours` later - negative = into the past."""
    delta = dt.timedelta(hours=hours)
    with client.TestSessionLocal() as db:
        s = db.get(TrainingSession, sid)
        s.starts_at += delta
        s.ends_at += delta
        for inst in db.query(TrainingInstance).filter_by(session_id=sid):
            inst.opens_at = inst.opens_at + delta if inst.opens_at else None
            inst.closes_at = inst.closes_at + delta if inst.closes_at else None
        for inv in db.query(TrainingInvitation).join(TrainingRegistration, TrainingRegistration.id == TrainingInvitation.registration_id) \
                .filter(TrainingRegistration.session_id == sid):
            inv.expires_at += delta
        db.commit()


def tick(client, tenant_id, now=None):
    with client.TestSessionLocal() as db:
        out = notify.tick(db, tenant_id, now)
        db.commit()
        return out


# ---------------------------------------------------------------- programs, versions, templates


def test_prg_001_003_004_005_asm_004_versioning_and_publish_rules(client):
    licensed(client)
    pre = client.post("/api/training/templates", json=PRE).json()["id"]
    lonely = client.post("/api/training/templates", json={**POST, "questions": [
        {"item_key": "zz", "type": "single", "text": "Other?", "options": ["A", "B"], "correct_options": [0], "measure": "knowledge", "objective": "LO1"}]}).json()["id"]
    r = client.post("/api/training/catalog/programs", json={"code": "P1", "title": "One", "objectives": ["A"], "modules": [{"title": "M1"}]})
    vid = r.json()["version"]["id"]
    pid = r.json()["program"]["id"]
    assert client.post("/api/training/catalog/programs", json={"code": "P1", "title": "Dup"}).status_code == 409  # PRG-001
    blocked = client.post(f"/api/training/versions/{vid}/publish")
    assert blocked.status_code == 422 and "PRG-004" in blocked.text
    client.patch(f"/api/training/versions/{vid}", json={"assessments_waived": True})
    assert "reason" in client.post(f"/api/training/versions/{vid}/publish").text
    client.patch(f"/api/training/versions/{vid}", json={"assessments_waived": False, "pre_template_id": pre, "post_template_id": lonely})
    unpaired = client.post(f"/api/training/versions/{vid}/publish")
    assert unpaired.status_code == 422 and "ASM-004" in unpaired.text
    assert client.patch(f"/api/training/versions/{vid}", json={"pre_template_id": lonely}).status_code == 422  # a post template is not a pre template
    post = client.post("/api/training/templates", json=POST).json()["id"]
    client.patch(f"/api/training/versions/{vid}", json={"post_template_id": post, "objectives": ["Spot hazards", "Use PPE"]})
    published = client.post(f"/api/training/versions/{vid}/publish")
    assert published.status_code == 200 and published.json()["state"] == "published"
    assert client.patch(f"/api/training/versions/{vid}", json={"modules": [{"title": "X"}]}).status_code == 409  # immutable
    draft = client.post(f"/api/training/catalog/programs/{pid}/versions", json={"bump": "minor"}).json()
    assert draft["version_label"] == "1.1" and draft["state"] == "draft"
    client.patch(f"/api/training/versions/{draft['id']}", json={"modules": [{"title": "M1", "duration_min": 45}, {"title": "M2"}]})
    d = client.get(f"/api/training/versions/{vid}/diff/{draft['id']}").json()  # PRG-005
    assert d["added"] == ["M2"] and d["changed"][0]["fields"] == ["duration_min"]
    _, admin = licensed(client)
    assert schedule(client, draft["id"], admin).status_code == 422          # SES-001: not from a draft
    assert client.post(f"/api/training/versions/{vid}/retire").status_code == 200
    assert schedule(client, vid, admin).status_code == 422                  # nor from a retired version


def test_asm_001_005_template_rules_in_use_and_clone(client):
    licensed(client)
    _, _, ids = published_version(client)
    assert client.patch(f"/api/training/templates/{ids['pre']}", json=PRE).status_code == 409
    clone = client.post(f"/api/training/templates/{ids['pre']}/clone").json()
    assert clone["state"] == "draft" and clone["version_label"] == "1.1"
    assert client.patch(f"/api/training/templates/{clone['id']}", json={**PRE, "name": "Edited"}).json()["name"] == "Edited"
    bad = client.post("/api/training/templates", json={"type": "pre", "questions": [
        {"item_key": "k", "type": "short_text", "text": "?", "measure": "knowledge"}]})
    assert bad.status_code == 422
    conf = client.post("/api/training/templates", json={"type": "pre", "questions": [
        {"item_key": "c", "type": "rating10", "text": "?", "measure": "confidence"}]})
    assert conf.status_code == 422 and "Likert" in conf.text


# ---------------------------------------------------------------- facilitators


def test_br_fac_01_certification_required_with_admin_override(client):
    tid, admin = licensed(client)
    trainer = make_user(client, "Tara Trainer", ["facilitator"])
    cert = client.post("/api/training/certifications", json={"name": "Safety trainer", "issuer": "BRT"}).json()["id"]
    _, vid, _ = published_version(client, certification_id=cert)
    refused = schedule(client, vid, trainer)
    assert refused.status_code == 409 and "BR-FAC-01" in refused.text
    overridden = schedule(client, vid, trainer, override_reason="Shadowed by lead trainer")
    assert overridden.status_code == 201, overridden.text
    with client.TestSessionLocal() as db:
        assert db.query(TrainingAudit).filter_by(action="facilitator.override").count() == 1
    expiring_soon = client.post("/api/training/facilitator-certificates", json={"user_id": trainer, "certification_id": cert,
                                                                               "expires_at": iso(60)})  # before a session 3 days out ends
    assert expiring_soon.status_code == 201
    assert schedule(client, vid, trainer, start=72).status_code == 409
    client.post("/api/training/facilitator-certificates", json={"user_id": trainer, "certification_id": cert, "expires_at": iso(24 * 400)})
    assert schedule(client, vid, trainer, start=96).status_code == 201


def test_br_ses_02_double_booking_with_travel_buffer(client):
    _, admin = licensed(client)
    trainer = make_user(client, "Ravi Trainer", ["facilitator"])
    _, vid, _ = published_version(client)
    a, b = location(client, "A"), location(client, "B")
    assert schedule(client, vid, trainer, start=48, hours=2, location_id=a).status_code == 201
    assert schedule(client, vid, trainer, start=49, hours=2, location_id=a).status_code == 409
    gap = 50 + 0.25  # 15 minutes after the first ends, at another location: inside the 30-minute buffer
    assert schedule(client, vid, trainer, start=gap, hours=1, location_id=b).status_code == 409
    assert schedule(client, vid, trainer, start=gap, hours=1, location_id=a).status_code == 201
    co = schedule(client, vid, admin, start=48.5, hours=1, location_id=a, co_facilitator_ids=[trainer])
    assert co.status_code == 409  # co-facilitators are checked too (SES-003)


def test_ses_004_br_ses_04_state_machine(client):
    tid, admin = licensed(client)
    _, vid, _ = published_version(client)
    draft = schedule(client, vid, admin, status="draft").json()
    sid = draft["id"]
    assert draft["status"] == "draft"
    assert client.post(f"/api/training/sessions/{sid}/transition", json={"to": "completed"}).status_code == 409
    assert client.post(f"/api/training/sessions/{sid}/transition", json={"to": "scheduled"}).json()["status"] == "scheduled"
    assert client.post(f"/api/training/sessions/{sid}/transition", json={"to": "in_progress"}).status_code == 200
    early = client.post(f"/api/training/sessions/{sid}/transition", json={"to": "completed"})
    assert early.status_code == 409 and "BR-SES-04" in early.text
    shift(client, sid, -60)
    assert client.post(f"/api/training/sessions/{sid}/transition", json={"to": "completed"}).status_code == 200
    assert client.post(f"/api/training/sessions/{sid}/transition", json={"to": "closed"}).status_code == 409  # window still open
    assert client.post(f"/api/training/sessions/{sid}/transition", json={"to": "cancelled"}).status_code == 409
    other = schedule(client, vid, admin, start=200).json()["id"]
    assert client.post(f"/api/training/sessions/{other}/transition", json={"to": "cancelled"}).status_code == 422  # reason needed
    assert client.post(f"/api/training/sessions/{other}/transition", json={"to": "cancelled", "reason": "Venue flooded"}).status_code == 200
    with client.TestSessionLocal() as db:
        actions = {a.action for a in db.query(TrainingAudit).filter_by(entity_id=str(sid))}
    assert {"session.scheduled", "session.in_progress", "session.completed"} <= actions


# ---------------------------------------------------------------- registration


def test_ses_005_006_br_ses_03_capacity_waitlist_promotion_and_deadline(client):
    _, admin = licensed(client)
    _, vid, _ = published_version(client)
    sid = schedule(client, vid, admin, capacity=2, waitlist_enabled=True, registration_deadline=iso(24)).json()["id"]
    first, second = enrol_external(client, sid, 2)
    third = client.post(f"/api/training/sessions/{sid}/enrol", json={"email": "late@ext.test", "name": "Late"}).json()
    assert third["status"] == "waitlisted" and third["waitlist_position"] == 1
    client.post(f"/api/training/registrations/{first}/cancel")
    with client.TestSessionLocal() as db:
        promoted = db.get(TrainingRegistration, third["id"])
        assert promoted.status == "registered"
        assert db.query(TrainingNotification).filter_by(registration_id=promoted.id, event="waitlist_promoted").count() == 1
        assert db.query(TrainingInvitation).filter_by(registration_id=promoted.id, phase="pre").count() == 1
    full = schedule(client, vid, admin, start=300, capacity=1).json()["id"]
    enrol_external(client, full, 1)
    assert client.post(f"/api/training/sessions/{full}/enrol", json={"email": "x@ext.test", "name": "X"}).status_code == 409
    with client.TestSessionLocal() as db:
        s = db.get(TrainingSession, sid)
        s.registration_deadline = utcnow() - dt.timedelta(minutes=1)
        db.commit()
    closed = client.post(f"/api/training/sessions/{sid}/enrol", json={"email": "y@ext.test", "name": "Y"})
    assert closed.status_code == 409 and "BR-SES-03" in closed.text


def test_ses_007_external_participants_and_public_self_registration(client):
    tid, admin = licensed(client)
    _, vid, _ = published_version(client)
    sid = schedule(client, vid, admin, settings={"public_registration": True}).json()["id"]
    with client.TestSessionLocal() as db:
        users_before = db.query(User).count()
        code = db.get(TrainingSession, sid).checkin_code
    assert client.get(f"/api/p/register/{code}").json()["open"] is True
    assert client.post(f"/api/p/register/{code}", json={"name": "Guest", "email": "guest@partner.test"}).status_code == 422  # consent
    r = client.post(f"/api/p/register/{code}", json={"name": "Guest", "email": "guest@partner.test", "organization": "Partner", "consent": True})
    assert r.status_code == 201 and r.json()["status"] == "registered"
    with client.TestSessionLocal() as db:
        assert db.query(User).count() == users_before  # no account, no password
        assert db.query(TrainingParticipant).filter_by(email="guest@partner.test").one().organization == "Partner"


def test_ses_005_bulk_registration_csv(client):
    _, admin = licensed(client)
    _, vid, _ = published_version(client)
    sid = schedule(client, vid, admin).json()["id"]
    csv_text = "email,name\na@x.test,Anna\nnot-an-email,Bad\nb@x.test,\n"
    out = client.post(f"/api/training/sessions/{sid}/enrol/bulk", json={"csv": csv_text}).json()
    assert out["registered"] == 1
    assert [r["ok"] for r in out["rows"]] == [True, False, False]


# ---------------------------------------------------------------- attendance and QR


def test_ses_008_009_attendance_methods_and_checkin_window(client):
    _, admin = licensed(client)
    _, vid, _ = published_version(client)
    sid = schedule(client, vid, admin).json()["id"]
    regs = enrol_external(client, sid, 3)
    with client.TestSessionLocal() as db:
        code = db.get(TrainingSession, sid).checkin_code
    closed = client.post(f"/api/p/checkin/{code}", json={"email": "p0@ext.test", "phone_last4": "0000"})
    assert closed.status_code == 409 and "Check-in closed" in closed.text
    shift(client, sid, -48 + 0.25)  # starts in 15 minutes
    wrong = client.post(f"/api/p/checkin/{code}", json={"email": "p0@ext.test", "phone_last4": "9999"})
    assert wrong.status_code == 403
    ok = client.post(f"/api/p/checkin/{code}", json={"email": "p0@ext.test", "phone_last4": "0000"})
    assert ok.status_code == 200 and ok.json()["checked_in"]
    client.post(f"/api/p/q/{code}/otp", json={"email": "p1@ext.test"})
    with client.TestSessionLocal() as db:
        msg = db.query(TrainingNotification).filter_by(event="otp").one()
        otp = msg.body_text.split("code is ")[1][:6]
    assert client.post(f"/api/p/checkin/{code}", json={"email": "p1@ext.test", "otp": "000000" if otp != "000000" else "111111"}).status_code == 403
    assert client.post(f"/api/p/checkin/{code}", json={"email": "p1@ext.test", "otp": otp}).status_code == 200
    assert client.post(f"/api/p/q/{code}/otp", json={"email": "nobody@ext.test"}).json()["sent"] is True  # no enumeration
    roster = client.post(f"/api/training/sessions/{sid}/attendance", json={"items": [{"registration_id": regs[2], "status": "absent"}]})
    assert roster.status_code == 200
    with client.TestSessionLocal() as db:
        methods = sorted((a.method, a.status, a.recorded_by is not None) for a in db.query(TrainingAttendance))
        assert methods == [("qr", "present", False), ("qr", "present", False), ("roster", "absent", True)]
        assert db.get(TrainingRegistration, regs[2]).status == "no_show"
    regenerated = client.post(f"/api/training/sessions/{sid}/qr/regenerate").json()["checkin_code"]
    assert regenerated != code and client.get(f"/api/p/q/{code}").status_code == 404
    assert client.get(f"/api/training/sessions/{sid}/qr.png").content.startswith(b"\x89PNG")


# ---------------------------------------------------------------- the full pre/post run


def answer(correct: bool, confidence: int):
    return {"k1": 1 if correct else 0, "k2": 0 if correct else 1, "c1": confidence}


def test_asm_011_012_015_016_anl_001_004_br_asm_01_02_full_run(client):
    tid, admin = licensed(client)
    with client.TestSessionLocal() as db:
        from app.models.training import TrainingSettings
        db.add(TrainingSettings(tenant_id=tid, data={"show_own_scores": True, "quiet_hours": {"enabled": False}}))
        db.commit()
    _, vid, _ = published_version(client, feedback=False)
    sid = schedule(client, vid, admin).json()["id"]
    regs = enrol_external(client, sid, 7)
    tokens = [token(client, r, "pre") for r in regs]
    assert all(t and len(t) >= 22 for t in tokens) and len(set(tokens)) == 7
    with client.TestSessionLocal() as db:
        assert all(i.token_hash != tokens[0] for i in db.query(TrainingInvitation))  # ASM-011: stored hashed
    form = client.get(f"/api/p/a/{tokens[0]}").json()
    assert form["status"] == "open" and form["consent_needed"] and all("correct_options" not in q for q in form["questions"])
    assert client.put(f"/api/p/a/{tokens[0]}/answers", json={"answers": {"k1": 1}}).json()["saved"]  # ASM-015
    assert client.get(f"/api/p/a/{tokens[0]}").json()["draft"] == {"k1": 1}
    missing = client.post(f"/api/p/a/{tokens[0]}/submit", json={"answers": {"c1": 2}, "consent": True})
    assert missing.status_code == 422 and "k2" in missing.text
    for i, t in enumerate(tokens[:6]):  # participant 6 never does the pre (post-only, BR-ASM-02)
        r = client.post(f"/api/p/a/{t}/submit", json={"answers": answer(False, 2), "consent": True})
        assert r.status_code == 200, r.text
    again = client.post(f"/api/p/a/{tokens[0]}/submit", json={"answers": answer(True, 5), "consent": True})
    assert again.json()["already"] is True                                   # ASM-016: confirmation again
    assert client.get(f"/api/p/a/{tokens[0]}").json()["status"] == "submitted"
    shift(client, sid, -52)  # the session is over
    tick(client, tid)
    with client.TestSessionLocal() as db:
        assert db.get(TrainingSession, sid).status == "completed"
    assert client.get(f"/api/p/a/{tokens[6]}").json()["status"] == "closed"  # ASM-012 pre window closed
    items = [{"registration_id": r, "status": "present"} for r in regs[:6]] + [{"registration_id": regs[6], "status": "late"}]
    items[5]["status"] = "absent"
    client.post(f"/api/training/sessions/{sid}/attendance", json={"items": items})
    assert token(client, regs[5], "post") is None                            # BR-ASM-01: absent -> no post
    for i, r in enumerate([regs[0], regs[1], regs[2], regs[3], regs[4], regs[6]]):
        t = token(client, r, "post")
        out = client.post(f"/api/p/a/{t}/submit", json={"answers": answer(i != 4, 4), "consent": True}).json()
        assert out["submitted"]
    assert out["scores"]["post"]["knowledge"] == 100.0                       # own score shown (org setting)
    d = client.get(f"/api/training/analytics/sessions/{sid}").json()
    assert d["funnel"] == {"invited": 7, "pre_done": 6, "attended": 6, "post_done": 6}
    assert d["paired_n"] == 5 and d["post_only"] == 1
    assert d["knowledge"]["pre"]["mean"] == 0.0 and d["knowledge"]["post"]["mean"] == 80.0 and d["knowledge"]["change"] == 80.0
    assert d["confidence"]["change"] == 2.0 and d["gain"]["bands"] == {"High": 4, "Low": 1}
    assert d["significance"]["knowledge"]["test"] == "Wilcoxon signed-rank"
    assert {o["code"]: o["post"] for o in d["objectives"]} == {"LO1": 80.0, "LO2": 80.0}
    assert len(d["participants"]) == 5
    cmp = client.get("/api/training/analytics/compare?group_by=program_version").json()
    assert cmp["groups"][0]["n"] == 5 and cmp["groups"][0]["knowledge_change"] == 80.0
    for fmt, magic in (("csv", b"\xef\xbb\xbf"), ("xlsx", b"PK"), ("pdf", b"%PDF")):           # ANL-008
        r = client.get(f"/api/training/analytics/export?format={fmt}&session_id={sid}")
        assert r.status_code == 200 and r.content.startswith(magic)
    card = client.get(f"/api/training/facilitators/{admin}/scorecard").json()                     # FAC-004
    assert card["average_knowledge_gain"] == 80.0 and card["sessions_delivered"] == 1


def test_br_asm_03_late_pre_is_excluded(client):
    tid, admin = licensed(client)
    _, vid, _ = published_version(client, feedback=False)
    sid = schedule(client, vid, admin).json()["id"]
    reg = enrol_external(client, sid, 1)[0]
    shift(client, sid, -48 + -0.1)  # started 6 minutes ago; pre grace (15 min) still open
    client.post(f"/api/p/a/{token(client, reg, 'pre')}/submit", json={"answers": answer(False, 1), "consent": True})
    shift(client, sid, -10)
    tick(client, tid)
    client.post(f"/api/training/sessions/{sid}/attendance", json={"items": [{"registration_id": reg, "status": "present"}]})
    client.post(f"/api/p/a/{token(client, reg, 'post')}/submit", json={"answers": answer(True, 5), "consent": True})
    d = client.get(f"/api/training/analytics/sessions/{sid}").json()
    assert d["paired_n"] == 0 and d["late_pre_excluded"] == 1


# ---------------------------------------------------------------- anonymity


def test_asm_020_021_022_023_br_asm_04_nfr_005_anonymous_feedback(client):
    tid, admin = licensed(client)
    _, vid, _ = published_version(client)
    sid = schedule(client, vid, admin, feedback_mode="anonymous").json()["id"]
    regs = enrol_external(client, sid, 3)
    shift(client, sid, -52)
    tick(client, tid)
    client.post(f"/api/training/sessions/{sid}/attendance", json={"items": [{"registration_id": r, "status": "present"} for r in regs]})
    t = token(client, regs[0], "feedback")
    form = client.get(f"/api/p/a/{t}").json()
    assert form["anonymous"] and form["draft"] is None
    assert client.put(f"/api/p/a/{t}/answers", json={"answers": {"f1": 4}}).json()["saved"] is False
    assert client.post(f"/api/p/a/{t}/submit", json={"answers": {"f1": 4, "f2": "Great"}, "consent": True}).json()["anonymous"]
    with client.TestSessionLocal() as db:
        row = db.query(TrainingAnonymousResponse).one()
        assert row.submitted_hour.minute == 0 and row.submitted_hour.second == 0
        assert row.sys_created_at is None and row.sys_created_by is None and row.sys_updated_at is None
        inv = db.query(TrainingInvitation).filter_by(registration_id=regs[0], phase="feedback").one()
        assert inv.has_submitted is True and inv.consumed_at is None and inv.sys_updated_at is None  # ASM-022: the flag only
    assert client.patch(f"/api/training/sessions/{sid}", json={"feedback_mode": "identifiable"}).status_code in (409,)  # ASM-020 locked
    d = client.get(f"/api/training/analytics/sessions/{sid}").json()
    assert d["feedback"]["suppressed"] and "anonymity" in d["feedback"]["message"]  # ASM-023: 1 < 5
    with client.TestSessionLocal() as db:
        code = db.get(TrainingSession, sid).checkin_code
    landing = client.get(f"/api/p/q/{code}").json()
    assert landing["anonymous_form"]["questions"]  # ASM-014: QR opens the form directly
    first = client.post(f"/api/p/q/{code}/anonymous", json={"answers": {"f1": 5}})
    assert first.json()["anonymous"] and any(c.startswith(assess.ANON_COOKIE) for c in first.cookies.keys())
    assert client.post(f"/api/p/q/{code}/anonymous", json={"answers": {"f1": 5}}).json()["already"]  # one per device


def test_nfr_005_asm_021_anonymous_table_has_no_path_to_a_person():
    table = TrainingAnonymousResponse.__table__
    assert {c.name for c in table.columns} == {"id", "tenant_id", "instance_id", "submitted_hour", "answers", "brt_id",
                                               "sys_created_at", "sys_created_by", "sys_created_position_id", "sys_department_id",
                                               "sys_updated_at", "sys_updated_by"}
    assert {fk.column.table.name for fk in table.foreign_keys} == {"tenants", "training_instances"}
    assert not {fk.column.table.name for fk in TrainingInstance.__table__.foreign_keys} & {"users", "training_registrations", "training_invitations"}


def test_asm_013_session_qr_routes_participant_to_own_assessment(client):
    _, admin = licensed(client)
    _, vid, _ = published_version(client)
    sid = schedule(client, vid, admin).json()["id"]
    regs = enrol_external(client, sid, 2)
    with client.TestSessionLocal() as db:
        code = db.get(TrainingSession, sid).checkin_code
    landing = client.get(f"/api/p/q/{code}").json()
    assert landing["phases"] == [{"type": "pre", "anonymous": False}] and "p0@ext.test" not in str(landing)
    assert client.post(f"/api/p/q/{code}/identify", json={"email": "p1@ext.test", "phone_last4": "0000"}).status_code == 403
    routed = client.post(f"/api/p/q/{code}/identify", json={"email": "p1@ext.test", "phone_last4": "0001"}).json()
    assert routed["type"] == "pre"
    assert client.post(f"/api/p/a/{routed['token']}/submit", json={"answers": answer(True, 3), "consent": True}).status_code == 200
    with client.TestSessionLocal() as db:
        assert db.query(TrainingInvitation).filter_by(registration_id=regs[1], phase="pre").one().has_submitted


# ---------------------------------------------------------------- automation


def test_wfa_001_002_br_wfa_01_reminders_skip_submitters_reschedule_and_cancel(client):
    tid, admin = licensed(client)
    client.put("/api/training/settings", json={"settings": {"quiet_hours": {"enabled": False}}})
    _, vid, _ = published_version(client)
    sid = schedule(client, vid, admin, start=72).json()["id"]
    done, pending = enrol_external(client, sid, 2)
    with client.TestSessionLocal() as db:
        events = sorted({n.event for n in db.query(TrainingNotification).filter_by(session_id=sid)})
    assert {"registration_confirmed", "pre_reminder_1", "pre_reminder_2", "session_day", "post_invite", "post_reminder_1", "post_reminder_2"} <= set(events)
    client.post(f"/api/p/a/{token(client, done, 'pre')}/submit", json={"answers": answer(True, 3), "consent": True})
    with client.TestSessionLocal() as db:
        s = db.get(TrainingSession, sid)
        due = s.starts_at - dt.timedelta(hours=47)
    tick(client, tid, now=due)
    with client.TestSessionLocal() as db:
        r1 = {n.registration_id: n for n in db.query(TrainingNotification).filter_by(session_id=sid, event="pre_reminder_1")}
        assert r1[done].status == "skipped" and r1[done].skip_reason == "pre-assessment already completed"
        assert r1[pending].status == "sent"
    moved = client.patch(f"/api/training/sessions/{sid}", json={"starts_at": iso(120), "ends_at": iso(123)})
    assert moved.status_code == 200, moved.text
    with client.TestSessionLocal() as db:
        rows = db.query(TrainingNotification).filter_by(session_id=sid, event="pre_reminder_2").all()
        assert any(n.status == "cancelled" and n.skip_reason == "replanned" for n in rows)
        assert any(n.status == "scheduled" for n in rows)
        assert db.query(TrainingNotification).filter_by(session_id=sid, event="session_updated").count() == 2
    client.post(f"/api/training/sessions/{sid}/transition", json={"to": "cancelled", "reason": "Trainer unwell"})
    with client.TestSessionLocal() as db:
        left = [n for n in db.query(TrainingNotification).filter_by(session_id=sid) if n.status in ("scheduled", "queued")]
        assert left and all(n.event == "session_cancelled" for n in left)


def test_wfa_003_templates_reject_unknown_merge_fields(client):
    licensed(client)
    bad = client.put("/api/training/notification-templates/pre_reminder_1", json={"subject": "Hi {{participant.nickname}}", "body_text": "x"})
    assert bad.status_code == 422 and "participant.nickname" in bad.text
    ok = client.put("/api/training/notification-templates/pre_reminder_1",
                    json={"subject": "Reminder for {{session.title}}", "body_text": "Link: {{assessment.link}}", "body_html": "<p>{{assessment.link}}</p>"})
    assert ok.status_code == 200 and ok.json()["custom"]
    preview = client.post("/api/training/notification-templates/pre_reminder_1/preview", json={}).json()
    assert preview["subject"] == "Reminder for Safe Working Practices"


def test_wfa_005_quiet_hours_defer_to_08_local(client):
    tid, admin = licensed(client)
    _, vid, _ = published_version(client)
    sid = schedule(client, vid, admin, timezone="Asia/Kolkata").json()["id"]
    with client.TestSessionLocal() as db:
        n = db.query(TrainingNotification).filter_by(session_id=sid).first()
        night = dt.datetime(2026, 10, 20, 17, 0)  # 22:30 in India
        resume = notify._quiet(db, n, policy.settings(db, tid), night)
        assert resume == dt.datetime(2026, 10, 21, 2, 30)  # 08:00 IST
        assert notify._quiet(db, n, policy.settings(db, tid), dt.datetime(2026, 10, 20, 6, 0)) is None


def test_wfa_006_007_retries_backoff_then_failed_and_hard_bounce(client, monkeypatch):
    tid, admin = licensed(client)
    client.put("/api/training/settings", json={"settings": {"quiet_hours": {"enabled": False}}})
    hr = make_user(client, "Hema HR", ["hr"])
    _, vid, _ = published_version(client)
    sid = schedule(client, vid, admin).json()["id"]

    class Broken(notify.Adapter):
        def send(self, *a):
            raise RuntimeError("smtp down")
    monkeypatch.setitem(notify.ADAPTERS, "outbox", Broken())
    enrol_external(client, sid, 1)
    with client.TestSessionLocal() as db:
        n = db.query(TrainingNotification).filter_by(session_id=sid, event="registration_confirmed").one()
        now = utcnow()
        for minutes in (0, 1, 5, 30):
            now = now + dt.timedelta(minutes=minutes, seconds=1)
            notify.dispatch_due(db, tid, now)
        db.commit()
        db.refresh(n)
        assert n.status == "failed" and n.attempts == 4
        nid = n.id
        key = n.idempotency_key
    monkeypatch.setitem(notify.ADAPTERS, "outbox", notify.OutboxAdapter())
    with client.TestSessionLocal() as db:  # WFA-007: the same key never makes a second message
        s = db.get(TrainingSession, sid)
        assert notify.queue(db, s, None, "registration_confirmed", utcnow(), user_id=admin, key=key).id == nid
    assert client.post(f"/api/training/notifications/{nid}/status", json={"status": "bounced", "hard": True}).status_code == 200
    with client.TestSessionLocal() as db:
        assert db.query(TrainingParticipant).one().email_invalid
        assert db.query(TrainingNotification).filter_by(event="bounce_alert", recipient_user_id=hr).count() == 1


def test_wfa_008_resend_reuses_link_unless_regenerated(client):
    _, admin = licensed(client)
    _, vid, _ = published_version(client)
    sid = schedule(client, vid, admin).json()["id"]
    reg = enrol_external(client, sid, 1)[0]
    before = token(client, reg, "pre")
    assert client.post("/api/training/notifications/resend", json={"registration_ids": [reg], "phase": "pre"}).status_code == 200
    assert token(client, reg, "pre") == before
    client.post("/api/training/notifications/resend", json={"registration_ids": [reg], "phase": "pre", "regenerate": True})
    after = token(client, reg, "pre")
    assert after != before and client.get(f"/api/p/a/{before}").status_code == 404 and client.get(f"/api/p/a/{after}").status_code == 200
    log = client.get(f"/api/training/sessions/{sid}/notifications").json()   # WFA-010
    # opening the link marked the sent messages clicked (WFA-006 tracking)
    assert any(e["event"] == "registration_confirmed" and e["status"] == "clicked" for e in log) and "body" not in log[0]


def test_wfa_009_unsubscribe_stops_reminders_not_invitations(client):
    tid, admin = licensed(client)
    client.put("/api/training/settings", json={"settings": {"quiet_hours": {"enabled": False}}})
    _, vid, _ = published_version(client)
    sid = schedule(client, vid, admin, start=72).json()["id"]
    reg = enrol_external(client, sid, 1)[0]
    with client.TestSessionLocal() as db:
        email = db.query(TrainingParticipant).one().email
    assert client.post(f"/api/p/u/{notify._unsub_token(tid, email)}").json()["unsubscribed"]
    assert client.post("/api/p/u/forged.token").status_code == 404
    with client.TestSessionLocal() as db:
        s = db.get(TrainingSession, sid)
        due = s.starts_at - dt.timedelta(hours=47)
    tick(client, tid, now=due)
    with client.TestSessionLocal() as db:
        by_event = {n.event: n for n in db.query(TrainingNotification).filter_by(registration_id=reg)}
        assert by_event["pre_reminder_1"].skip_reason == "unsubscribed from reminders"
        assert by_event["registration_confirmed"].status == "sent"


# ---------------------------------------------------------------- org, people, permissions


def test_org_004_import_validates_every_row(client):
    licensed(client)
    location(client, "HYD")
    csv_text = "name,email,phone,location,roles\nA One,a1@x.test,1,HYD,participant\n,bad,2,,\nB Two,b2@x.test,3,NOPE,\nC,a1@x.test,4,,\nD,d@x.test,,,hr\n"
    dry = client.post("/api/training/people/import", json={"csv": csv_text}).json()
    assert dry["valid"] == 1 and dry["created"] == 0
    assert [r["ok"] for r in dry["rows"]] == [True, False, False, False, False]
    real = client.post("/api/training/people/import", json={"csv": csv_text, "commit": True}).json()
    assert real["created"] == 1
    again = client.post("/api/training/people/import", json={"csv": csv_text, "commit": True}).json()
    assert again["created"] == 0 and "already exists" in again["rows"][0]["errors"][0]


def test_org_011_erasure_keeps_aggregates_but_not_identity(client):
    _, admin = licensed(client)
    _, vid, _ = published_version(client)
    sid = schedule(client, vid, admin).json()["id"]
    enrol_external(client, sid, 1)
    with client.TestSessionLocal() as db:
        pid = db.query(TrainingParticipant).one().id
    erased = client.post(f"/api/training/people/{pid}/erase").json()
    assert erased["name"].startswith("ERASED-") and erased["phone"] == "" and "p0@ext.test" not in str(erased)
    with client.TestSessionLocal() as db:
        assert db.query(TrainingRegistration).filter_by(participant_id=pid).count() == 1


def test_org_005_permission_matrix_per_role(client):
    _, admin = licensed(client)
    hr, execu, fac, learner = (make_user(client, n, r) for n, r in
                               (("H R", ["hr"]), ("E X", ["executive"]), ("F A", ["facilitator"]), ("P A", [])))
    _, vid, _ = published_version(client)
    sid = schedule(client, vid, admin).json()["id"]
    cases = [
        ("post", "/api/training/catalog/programs", {"code": "Z", "title": "Z"}, {hr: 403, execu: 403, fac: 403, learner: 403}),
        ("post", "/api/training/templates", PRE, {hr: 403, execu: 403, fac: 403, learner: 403}),
        ("get", "/api/training/audit-log", None, {hr: 403, execu: 403, fac: 403, learner: 403, admin: 200}),
        ("get", "/api/training/people", None, {hr: 200, execu: 403, fac: 403, learner: 403}),
        ("post", "/api/training/locations", {"name": "L", "code": "L"}, {hr: 403, execu: 403, fac: 403, learner: 403}),
        ("put", "/api/training/settings", {"settings": {}}, {hr: 403, execu: 403, fac: 403, learner: 403}),
        ("get", "/api/training/settings", None, {hr: 200, execu: 200, fac: 403, learner: 403}),
        ("get", f"/api/training/analytics/sessions/{sid}", None, {hr: 200, execu: 200, fac: 403, learner: 403}),
        ("post", f"/api/training/sessions/{sid}/transition", {"to": "cancelled", "reason": "x"}, {execu: 403, fac: 403, learner: 403}),
        ("get", "/api/training/notification-templates", None, {hr: 200, execu: 403, fac: 403, learner: 403}),
    ]
    for method, path, body, expected in cases:
        for uid, status in expected.items():
            r = getattr(client, method)(path, headers=as_(uid), **({"json": body} if body is not None else {}))
            assert r.status_code == status, (method, path, uid, r.status_code, r.text[:200])
    assert client.post(f"/api/training/sessions/{sid}/enrol", json={"user_id": learner}, headers=as_(learner)).status_code == 201
    assert client.post(f"/api/training/sessions/{sid}/enrol", json={"user_id": hr}, headers=as_(learner)).status_code == 403
    # effective permission is the union of roles (section 4)
    both = make_user(client, "F P", ["facilitator", "executive"])
    assert client.get("/api/training/settings", headers=as_(both)).status_code == 200


def test_org_005_policy_table_is_the_brd_matrix():
    can = policy.can
    assert can({"admin"}, "versions", "p") and not can({"hr"}, "versions", "p")
    assert can({"hr"}, "registrations", "d") and not can({"hr"}, "sessions", "d")
    assert can({"facilitator"}, "attendance", "c") and not can({"executive"}, "attendance", "c")
    assert can({"participant"}, "attendance", "c") and not can({"participant"}, "people", "r")
    assert not any(can({role}, "audit", "r") for role in ("hr", "facilitator", "executive", "participant"))
    assert can({"participant", "hr"}, "people", "c")


def test_org_006_scoped_hr_sees_only_their_departments(client):
    tid, admin = licensed(client)
    from app.models.org import Department
    with client.TestSessionLocal() as db:
        parent, other = Department(tenant_id=tid, name="Ops"), Department(tenant_id=tid, name="Sales")
        db.add_all([parent, other])
        db.flush()
        child = Department(tenant_id=tid, name="Ops North", parent_department_id=parent.id)
        db.add(child)
        db.commit()
        ids = parent.id, child.id, other.id
    hr = make_user(client, "Scoped HR", ["hr"], scope_departments=[ids[0]])
    _, vid, _ = published_version(client)
    mine = schedule(client, vid, admin, start=48, department_id=ids[1]).json()["id"]
    theirs = schedule(client, vid, admin, start=100, department_id=ids[2]).json()["id"]
    seen = {s["id"] for s in client.get("/api/training/calendar", headers=as_(hr)).json()}
    assert mine in seen and theirs not in seen                                 # child departments included
    assert client.get(f"/api/training/sessions/{theirs}/detail", headers=as_(hr)).status_code == 403
    assert client.get(f"/api/training/sessions/{theirs}/outcomes", headers=as_(hr)).status_code == 404
    assert theirs not in {s["id"] for s in client.get("/api/training/workspace", headers=as_(hr)).json()["sessions"]}


def test_nfr_001_br_org_01_cross_tenant_isolation(client):
    tid, admin = licensed(client)
    _, vid, ids = published_version(client)
    sid = schedule(client, vid, admin).json()["id"]
    with client.TestSessionLocal() as db:
        other = Tenant(name="Other", slug="other-t", licensed_modules=["training_management"])
        db.add(other)
        db.flush()
        outsider = User(tenant_id=other.id, name="Out", email="out@other.test", password_hash="x", role=UserRole.admin)
        db.add(outsider)
        db.commit()
        oid = outsider.id
    h = as_(oid)
    for path in (f"/api/training/sessions/{sid}/detail", f"/api/training/analytics/sessions/{sid}", f"/api/training/templates/{ids['pre']}",
                 f"/api/training/sessions/{sid}/notifications", f"/api/training/versions/{vid}/check"):
        assert client.get(path, headers=h).status_code == 404, path
    assert client.post(f"/api/training/sessions/{sid}/enrol", json={"email": "z@z.test", "name": "Z"}, headers=h).status_code == 404
    assert client.patch(f"/api/training/versions/{vid}", json={}, headers=h).status_code == 404
    assert client.get("/api/training/calendar", headers=h).json() == []
    assert client.get("/api/training/catalog", headers=h).json()["programs"] == []


def test_org_010_settings_validated_and_versioned(client):
    licensed(client)
    assert client.put("/api/training/settings", json={"settings": {"min_group_size": 2}}).status_code == 422
    assert client.put("/api/training/settings", json={"settings": {"timezone": "Mars/Base"}}).status_code == 422
    first = client.put("/api/training/settings", json={"settings": {"pass_mark": 75}}).json()
    second = client.put("/api/training/settings", json={"settings": {"pass_mark": 80}}).json()
    assert second["version"] == first["version"] + 1 and second["settings"]["pass_mark"] == 80
    log = client.get("/api/training/audit-log").json()
    assert log[0]["action"] == "settings.update" and log[0]["after"]["pass_mark"] == 80


def test_org_012_nfr_011_audit_is_append_only(client):
    tid, admin = licensed(client)
    location(client)
    with client.TestSessionLocal() as db:
        row = db.query(TrainingAudit).first()
        row.action = "tampered"
        with pytest.raises(ValueError):
            db.flush()
        db.rollback()
        with pytest.raises(ValueError):
            db.delete(db.query(TrainingAudit).first())
            db.flush()
    csv_out = client.get("/api/training/audit-log?format=csv")
    assert csv_out.status_code == 200 and b"location.create" in csv_out.content


def test_fac_002_003_certification_status_and_qa_rubric(client):
    tid, admin = licensed(client)
    trainer = make_user(client, "Q Trainer", ["facilitator"])
    for days in (400, 30, -1):
        client.post("/api/training/facilitator-certificates", json={"user_id": trainer, "name": f"C{days}", "expires_at": iso(days * 24)})
    revoked = client.post("/api/training/facilitator-certificates", json={"user_id": trainer, "name": "R", "expires_at": iso(9000)}).json()["id"]
    client.post(f"/api/training/facilitator-certificates/{revoked}/revoke", json={"reason": "Fraud"})
    statuses = {c["name"]: c["status"] for f in client.get("/api/training/facilitators").json() if f["id"] == trainer for c in f["certificates"]}
    assert statuses == {"C400": "Valid", "C30": "Expiring", "C-1": "Expired", "R": "Revoked"}
    _, vid, _ = published_version(client)
    sid = schedule(client, vid, trainer).json()["id"]
    assert client.post("/api/training/qa-reviews", json={"session_id": sid, "facilitator_id": trainer, "scores": {"Nonsense": 3}}).status_code == 422
    ok = client.post("/api/training/qa-reviews", json={"session_id": sid, "facilitator_id": trainer, "scores": {"Delivery": 4, "Engagement": 2}})
    assert ok.status_code == 201 and ok.json()["overall_rating"] == 3.0
    assert client.get(f"/api/training/facilitators/{trainer}/scorecard").json()["latest_qa_rating"] == 3.0
    assert client.get(f"/api/training/facilitators/{admin}/scorecard", headers=as_(trainer)).status_code == 403  # own only


def test_nfr_003_public_links_are_rate_limited(client, monkeypatch):
    monkeypatch.setattr(policy, "RATE_LIMIT", 30)
    policy._hits.clear()
    codes = [client.get("/api/p/q/no-such-code").status_code for _ in range(31)]
    assert codes[:30] == [404] * 30 and codes[30] == 429


class _FakeRedis:
    """Enough of redis for the limiter: one counter store shared by every 'worker'."""

    def __init__(self, store, down=False):
        self.store, self.down, self.ops = store, down, []

    def pipeline(self):
        self.ops = []
        return self

    def incr(self, key):
        self.ops.append(key)

    def expire(self, key, seconds):
        pass

    def execute(self):
        if self.down:
            raise ConnectionError("redis is down")
        self.store[self.ops[0]] = self.store.get(self.ops[0], 0) + 1
        return [self.store[self.ops[0]], True]


def test_nfr_003_with_redis_the_limit_is_shared_by_every_worker(client, monkeypatch):
    monkeypatch.setattr(policy, "RATE_LIMIT", 30)
    monkeypatch.setattr(policy, "get_settings", lambda: type("S", (), {"redis_url": "redis://cache:6379/0"})())
    store = {}
    monkeypatch.setattr(policy, "_redis_client", lambda url: _FakeRedis(store))
    codes = [client.get("/api/p/q/no-such-code").status_code for _ in range(31)]
    assert codes[:30] == [404] * 30 and codes[30] == 429
    policy._hits.clear()  # another worker: its own count is empty, the shared one is not
    assert client.get("/api/p/q/no-such-code").status_code == 429


def test_nfr_003_a_redis_outage_falls_back_to_the_worker_count(client, monkeypatch):
    monkeypatch.setattr(policy, "RATE_LIMIT", 30)
    monkeypatch.setattr(policy, "get_settings", lambda: type("S", (), {"redis_url": "redis://cache:6379/0"})())
    monkeypatch.setattr(policy, "_redis_client", lambda url: _FakeRedis({}, down=True))
    policy._hits.clear()
    codes = [client.get("/api/p/q/no-such-code").status_code for _ in range(31)]
    assert codes[:30] == [404] * 30 and codes[30] == 429  # still limited, and the links still work


def grant_pages(client, uid, pages):
    from app.models.org import AppRole, UserAppRole
    with client.TestSessionLocal() as db:
        tid = db.get(User, uid).tenant_id
        role = AppRole(tenant_id=tid, name=f"Role {uid} {pages}", pages=pages)
        db.add(role)
        db.flush()
        db.add(UserAppRole(user_id=uid, role_id=role.id))
        db.commit()


def test_learning_pages_are_granted_to_roles_selectively(client):
    licensed(client)
    hr = make_user(client, "Page HR", ["hr"])
    grant_pages(client, hr, "training_sessions,training_outcomes")
    pages = client.get("/api/access/me", headers=as_(hr)).json()["pages"]
    assert "training_sessions" in pages and "training_people" not in pages and "training" not in pages
    assert client.get("/api/training/calendar", headers=as_(hr)).status_code == 200
    assert client.get("/api/training/analytics/compare", headers=as_(hr)).status_code == 200
    blocked = client.get("/api/training/people", headers=as_(hr))
    assert blocked.status_code == 403 and "People" in blocked.text
    assert client.get("/api/training/notification-templates", headers=as_(hr)).status_code == 403
    assert client.get("/api/training/me", headers=as_(hr)).status_code == 200  # shared lookups need any Learning page
    nothing = make_user(client, "No Learning", ["hr"])
    grant_pages(client, nothing, "dashboard")
    assert client.get("/api/training/me", headers=as_(nothing)).status_code == 403


def test_roles_saved_before_the_split_keep_every_learning_page(client):
    from app.crm_core.access_service import TRAINING_PAGES, role_pages
    assert set(TRAINING_PAGES) <= set(role_pages("training,dashboard"))
    assert role_pages("training,training_mine") == ["training", "training_mine"]
    licensed(client)
    old = make_user(client, "Old Role HR", ["hr"])
    grant_pages(client, old, "training")
    assert client.get("/api/training/people", headers=as_(old)).status_code == 200


def test_overview_numbers_follow_role_and_pages(client):
    tid, admin = licensed(client)
    _, vid, _ = published_version(client)
    sid = schedule(client, vid, admin, start=30).json()["id"]
    enrol_external(client, sid, 2)
    o = client.get("/api/training/overview").json()
    assert o["sessions"]["next_7_days"] == 1 and o["sessions"]["upcoming"][0]["registered"] == 2
    assert o["programs"]["published"] == 1 and o["assessments"]["templates"] == 3 and o["people"]["participants"] == 2
    learner = make_user(client, "Over Learner")
    lo = client.get("/api/training/overview", headers=as_(learner)).json()
    assert "people" not in lo and "programs" in lo and "messages" not in lo


def test_learning_home_insights_on_demo_data(client):
    from app.crm_core.training_demo import seed_demo
    tid, admin = licensed(client)
    with client.TestSessionLocal() as db:
        seed_demo(db, tid)
    i = client.get("/api/training/insights").json()
    assert len(i["activity"]) == 12 and sum(w["sessions"] for w in i["activity"]) == 3
    assert [f["step"] for f in i["funnel"]] == ["Invited", "Pre done", "Attended", "Post done"]
    assert i["funnel"][2]["value"] == 7 and i["completion"]["post"]["rate"] == 100.0
    assert i["impact"]["n"] == 6 and i["impact"]["knowledge"]["post"] > i["impact"]["knowledge"]["pre"]
    assert sum(i["impact"]["bands"].values()) == 6 and i["programs"][0]["program"] == "Safe Working Practices"
    assert i["feedback"]["n"] == 7 and i["facilitators"][0]["sessions"] == 1
    assert i["certificates"]["Valid"] == 1 and len(i["messages"]) == 14
    learner = make_user(client, "Insight Learner")
    li = client.get("/api/training/insights", headers=as_(learner)).json()
    assert "impact" not in li and "messages" not in li and not li["analyst"]


def test_demo_seed_builds_a_complete_story_and_is_idempotent(client):
    from app.crm_core.training_demo import seed_demo
    tid, admin = licensed(client)
    with client.TestSessionLocal() as db:
        out = seed_demo(db, tid)
    assert out["seeded"] and len(out["sessions"]) == 3
    finished = out["sessions"][0]
    d = client.get(f"/api/training/analytics/sessions/{finished}").json()
    assert d["session"]["status"] == "completed" and d["funnel"]["attended"] == 7
    assert d["paired_n"] == 6 and d["post_only"] == 1 and d["knowledge"]["change"] > 0
    assert d["feedback"]["n"] == 7 and d["feedback"]["rating"] is not None
    with client.TestSessionLocal() as db:
        assert seed_demo(db, tid)["seeded"] is False


def test_legacy_training_flow_still_runs_on_original_revisions(client):
    """Additive: a revision without templates keeps the original single-form flow."""
    _, admin = licensed(client)
    program = client.post("/api/training/programs", json={"name": "Legacy", "curriculum": ["One"],
                                                          "questions": [{"key": "q1", "prompt": "?", "options": ["A", "B"], "correct": 0}]}).json()
    session = client.post("/api/training/sessions", json={"title": "Old", "revision_id": program["revision_id"], "facilitator_id": admin,
                                                          "starts_at": iso(5), "ends_at": iso(6)}).json()
    assert client.post(f"/api/training/sessions/{session['id']}/registrations", json={"user_id": admin}).status_code == 201
    link = client.get("/api/training/workspace").json()["inbox"][0]["link"]
    assert link.startswith("/assessment/")
    assert client.get(f"/api/assessments/{link.split('/')[-1]}").status_code == 200

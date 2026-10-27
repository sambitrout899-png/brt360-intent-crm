"""Training demo data (BRD 12.5): a location, people in every training role,
two programs with paired pre/post templates and a feedback form, and three
sessions - one finished with responses and results, one upcoming, one
upcoming with anonymous feedback. Everything goes through the same services
the API uses, so it obeys the same rules. Idempotent per tenant: it does
nothing when the demo program codes already exist.

Run: python -m scripts.seed_training_demo --tenant-slug default
"""
from __future__ import annotations

import datetime as dt

from app.crm_core import training_assess as assess
from app.crm_core import training_catalog as catalog
from app.crm_core import training_delivery as delivery
from app.crm_core import training_notify as notify
from app.licensing import enabled
from app.models.auth import User, UserRole
from app.models.tenant import Tenant
from app.models.training import TrainingInstance, TrainingInvitation, TrainingMember, TrainingProgram
from app.timeutils import utcnow

PRE = [
    {"item_key": "hazard_spot", "type": "single", "text": "Which of these is a trip hazard?", "options": ["A coiled cable on a desk", "A cable across a walkway", "A cable in a duct"],
     "correct_options": [1], "points": 1, "measure": "knowledge", "objective": "LO1"},
    {"item_key": "ppe_when", "type": "true_false", "text": "Safety glasses are needed only when cutting metal.", "correct_options": [1], "points": 1, "measure": "knowledge", "objective": "LO2"},
    {"item_key": "report_steps", "type": "multi", "text": "Which steps belong in an incident report?", "options": ["What happened", "Who was involved", "Your opinion of the manager", "When and where"],
     "correct_options": [0, 1, 3], "points": 2, "measure": "knowledge", "objective": "LO1"},
    {"item_key": "conf_hazard", "type": "likert5", "text": "How confident are you at spotting workplace hazards?", "measure": "confidence", "objective": "LO1"},
    {"item_key": "conf_ppe", "type": "likert5", "text": "How confident are you choosing the right protective equipment?", "measure": "confidence", "objective": "LO2"},
]
FEEDBACK = [
    {"item_key": "overall", "type": "likert5", "text": "Overall, the session was worth my time.", "measure": "feedback"},
    {"item_key": "trainer", "type": "rating10", "text": "Rate the facilitator.", "measure": "feedback"},
    {"item_key": "recommend", "type": "nps", "text": "How likely are you to recommend this session to a colleague?", "measure": "feedback"},
    {"item_key": "comments", "type": "long_text", "text": "Anything we should change?", "measure": "feedback", "required": False},
]
PROGRAMS = [
    ("DEMO-SAFE", "Safe Working Practices", ["Spot and report hazards", "Choose the right protective equipment"]),
    ("DEMO-CARE", "Customer Care Essentials", ["Spot and report hazards", "Choose the right protective equipment"]),
]


def seed_demo(db, tenant_id: int, participants: int = 8) -> dict:
    if "training_management" not in enabled(db, tenant_id):
        raise ValueError("Training & Learning is not licensed for this organization")
    if db.query(TrainingProgram).filter(TrainingProgram.tenant_id == tenant_id, TrainingProgram.code.in_([p[0] for p in PROGRAMS])).first():
        return {"seeded": False, "reason": "demo data already present"}
    admin = db.query(User).filter_by(tenant_id=tenant_id, role=UserRole.admin, is_active=True).first()
    if admin is None:
        raise ValueError("The organization needs an active admin")
    location = catalog.save_location(db, admin, {"name": "Demo Training Centre", "code": "DEMO-TC", "address": "1 Learning Way", "timezone": "Asia/Kolkata"})
    cert = catalog.save_certification(db, admin, {"name": "Certified Safety Trainer", "issuer": "BRT Academy", "validity_months": 24})
    if not db.query(TrainingMember).filter_by(tenant_id=tenant_id, user_id=admin.id).first():
        db.add(TrainingMember(tenant_id=tenant_id, user_id=admin.id, role="hr", roles=["facilitator", "hr"], location="", phone="+91 98450 00000",
                              profile={"bio": "Demo lead facilitator", "expertise": "Workplace safety", "languages": "English, Hindi"}))
    catalog.record_certificate(db, admin, {"user_id": admin.id, "certification_id": cert.id, "issued_on": utcnow() - dt.timedelta(days=30),
                                           "expires_at": utcnow() + dt.timedelta(days=700), "evidence_url": "https://example.com/certificate.pdf"})
    versions = []
    for code, title, objectives in PROGRAMS:
        pre = catalog.save_template(db, admin, {"type": "pre", "name": f"{title} - before", "questions": PRE})
        post = catalog.save_template(db, admin, {"type": "post", "name": f"{title} - after", "questions": PRE})
        feedback = catalog.save_template(db, admin, {"type": "feedback", "name": f"{title} - feedback", "questions": FEEDBACK})
        _, version = catalog.create_program(db, admin, {"code": code, "title": title, "delivery_mode": "in_person", "duration_hours": 3,
                                                        "category": "Demo", "audience": "All staff", "objectives": objectives,
                                                        "required_certification_id": cert.id if code == "DEMO-SAFE" else None,
                                                        "modules": [{"title": "Why it matters", "duration_min": 30}, {"title": "Hands-on practice", "duration_min": 90},
                                                                    {"title": "Wrap-up and quiz", "duration_min": 30}]})
        catalog.update_version(db, admin, version.id, {"pre_template_id": pre.id, "post_template_id": post.id, "feedback_template_id": feedback.id})
        versions.append(catalog.publish_version(db, admin, version.id))
    people = []
    for i in range(participants):
        user_id, participant_id = delivery.find_or_create_participant(db, tenant_id, {"email": f"demo.learner{i + 1}@example.com",
                                                                                      "name": f"Demo Learner {i + 1}", "phone": f"+91 90000 0{i:04d}",
                                                                                      "organization": "Demo Co"})
        people.append(participant_id)

    def session(version, start_h, title, **extra):
        return delivery.create_session(db, admin, {"revision_id": version.id, "facilitator_id": admin.id, "title": title, "location_id": location.id,
                                                   "starts_at": utcnow() + dt.timedelta(hours=start_h), "ends_at": utcnow() + dt.timedelta(hours=start_h + 3),
                                                   "capacity": 20, "waitlist_enabled": True, **extra})

    finished = session(versions[0], 24, "Safe Working Practices - demo (finished)")
    upcoming = session(versions[0], 24 * 7, "Safe Working Practices - next cohort")
    anonymous = session(versions[1], 24 * 10, "Customer Care Essentials - pilot", feedback_mode="anonymous")
    regs = [delivery.enrol(db, finished, None, pid, "nominated", admin.id) for pid in people]
    for pid in people[:3]:
        delivery.enrol(db, upcoming, None, pid, "nominated", admin.id)
    for pid in people[3:6]:
        delivery.enrol(db, anonymous, None, pid, "nominated", admin.id)
    db.flush()
    # every learner answers the pre before the session...
    for i, reg in enumerate(regs[:-1]):
        inv = db.query(TrainingInvitation).filter_by(registration_id=reg.id, phase="pre").one()
        assess.submit(db, assess.token_for(inv), {"hazard_spot": i % 2, "ppe_when": 0, "report_steps": [0], "conf_hazard": 2, "conf_ppe": 2 + i % 2}, consent=True)
    # ...then the session is moved into the past, run, and the post completed
    shift = dt.timedelta(hours=28)  # it ended an hour ago
    finished.starts_at -= shift
    finished.ends_at -= shift
    for inst in db.query(TrainingInstance).filter_by(session_id=finished.id):
        inst.opens_at = inst.opens_at - shift if inst.opens_at else None
        inst.closes_at = inst.closes_at - shift if inst.closes_at else None
    db.flush()
    delivery.transition(db, admin, finished, "in_progress")
    delivery.transition(db, admin, finished, "completed")
    delivery.mark_attendance(db, admin, finished.id, [{"registration_id": r.id, "status": "absent" if i == 1 else "present"} for i, r in enumerate(regs)])
    for i, reg in enumerate(regs):
        if i == 1:
            continue
        for phase, answers in (("post", {"hazard_spot": 1, "ppe_when": 1, "report_steps": [0, 1, 3] if i % 3 else [0, 1], "conf_hazard": 4, "conf_ppe": 4 + (i % 2)}),
                               ("feedback", {"overall": 4 + (i % 2), "trainer": 8 + (i % 3), "recommend": 7 + (i % 4), "comments": "More practice time please." if i == 2 else ""})):
            inv = db.query(TrainingInvitation).filter_by(registration_id=reg.id, phase=phase).one()
            assess.submit(db, assess.token_for(inv), answers, consent=True)
    notify.plan_session(db, upcoming)
    db.commit()
    return {"seeded": True, "location": location.id, "programs": [v.program_id for v in versions],
            "sessions": [finished.id, upcoming.id, anonymous.id], "participants": len(people)}


def main(argv=None) -> None:
    import argparse
    from app.db import SessionLocal
    parser = argparse.ArgumentParser(description="Seed Training & Learning demo data into one organization")
    parser.add_argument("--tenant-slug", default="default")
    args = parser.parse_args(argv)
    with SessionLocal() as db:
        tenant = db.query(Tenant).filter_by(slug=args.tenant_slug).first()
        if tenant is None:
            raise SystemExit(f"No organization with slug {args.tenant_slug!r}")
        print(seed_demo(db, tenant.id))

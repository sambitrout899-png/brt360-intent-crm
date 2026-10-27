"""The numbers behind each Learning page's header and the Learning home:
one call, filtered to what the caller's training roles may see (sessions in
scope, a facilitator's own sessions, a learner's own registrations) and to
the Learning pages their app roles grant."""
from __future__ import annotations

import datetime as dt

from app.crm_core import training_catalog as catalog
from app.crm_core import training_policy as policy
from app.crm_core import training_scoring as scoring
from app.crm_core.training_delivery import facilitators_of, status_of, visible_sessions
from app.models.auth import User, UserRole
from app.models.training import (TrainingCertificate, TrainingDelivery, TrainingInstance, TrainingInvitation, TrainingMember,
                                 TrainingNotification, TrainingOutcome, TrainingParticipant, TrainingProgram, TrainingRegistration,
                                 TrainingRevision, TrainingSession, TrainingTemplate)
from app.timeutils import utcnow


def _iso(value):
    return value.isoformat() + "Z" if value else None


def _week(d: dt.datetime) -> dt.date:
    return (d - dt.timedelta(days=d.weekday())).date()


def insights(db, user: User, pages: list[str]) -> dict:
    """The Learning home dashboard: trends, funnel, completion, learning
    impact, programs, facilitators, feedback and messages - over the sessions
    this person may see (a facilitator's own, an HR/Executive's scope)."""
    from app.crm_core import training_analytics as analytics
    held = policy.roles(db, user)
    now = utcnow()
    settings = policy.settings(db, user.tenant_id)
    sessions = visible_sessions(db, user)
    analyst = policy.can(held, "analytics", "r") and held != {"participant"}
    if analyst and not ({"admin", "hr", "executive"} & held):
        sessions = [s for s in sessions if user.id in facilitators_of(s)]
    ids = [s.id for s in sessions]
    by_id = {s.id: s for s in sessions}
    regs = db.query(TrainingRegistration).filter(TrainingRegistration.session_id.in_(ids or [-1])).all()
    statuses = [status_of(r) for r in regs]

    # ---- 12-week activity: 8 weeks back, this week, 3 ahead
    this_week = _week(now)
    weeks = [this_week + dt.timedelta(weeks=i) for i in range(-8, 4)]
    activity = {w: {"week": w.isoformat(), "sessions": 0, "registered": 0, "attended": 0, "future": w > this_week} for w in weeks}
    for s in sessions:
        w = _week(s.starts_at)
        if w in activity and s.status != "cancelled":
            activity[w]["sessions"] += 1
    for r, st in zip(regs, statuses):
        w = _week(by_id[r.session_id].starts_at)
        if w in activity:
            activity[w]["registered"] += st in ("registered", "attended", "no_show")
            activity[w]["attended"] += st == "attended"

    # ---- funnel and completion (template-based assessments)
    invs = db.query(TrainingInvitation).join(TrainingRegistration, TrainingRegistration.id == TrainingInvitation.registration_id) \
        .filter(TrainingRegistration.session_id.in_(ids or [-1]), TrainingInvitation.instance_id.isnot(None)).all()
    phase = {p: [i for i in invs if i.phase == p] for p in ("pre", "post", "feedback")}
    completion = {p: {"invited": len(v), "done": sum(1 for i in v if i.has_submitted),
                      "rate": scoring.display(100 * sum(1 for i in v if i.has_submitted) / len(v)) if v else None} for p, v in phase.items()}
    attended, no_show = statuses.count("attended"), statuses.count("no_show")
    out: dict = {
        "activity": list(activity.values()),
        "session_status": {k: sum(1 for s in sessions if (s.status or "scheduled") == k)
                           for k in ("draft", "scheduled", "in_progress", "completed", "closed", "cancelled")},
        "registration_status": {k: statuses.count(k) for k in ("nominated", "registered", "waitlisted", "attended", "no_show", "cancelled")},
        "funnel": [{"step": "Invited", "value": sum(1 for st in statuses if st in ("registered", "attended", "no_show"))},
                   {"step": "Pre done", "value": completion["pre"]["done"]},
                   {"step": "Attended", "value": attended},
                   {"step": "Post done", "value": completion["post"]["done"]}],
        "completion": completion,
        "attendance_rate": scoring.display(100 * attended / (attended + no_show)) if attended + no_show else None,
        "targets": {"pre": 80, "post": 70},  # BRD OBJ-4
        "sources": {k: sum(1 for r in regs if (r.source or "nominated") == k) for k in ("nominated", "bulk", "self")},
        "analyst": analyst,
    }

    # ---- learning impact (paired outcome rows)
    if analyst:
        rows = db.query(TrainingOutcome).filter(TrainingOutcome.tenant_id == user.tenant_id, TrainingOutcome.objective_code == "",
                                                TrainingOutcome.session_id.in_(ids or [-1])).all()
        k = [r for r in rows if r.dimension == "knowledge"]
        c = [r for r in rows if r.dimension == "confidence"]
        bands = {"Decline": 0, "Low": 0, "Medium": 0, "High": 0}
        for r in k:
            band = scoring.gain_band(r.normalized_gain)
            if band:
                bands[band] += 1
        out["impact"] = {"n": len(k),
                         "knowledge": {"pre": scoring.display(scoring.mean([r.pre_value for r in k])), "post": scoring.display(scoring.mean([r.post_value for r in k]))},
                         "confidence": {"pre": scoring.display(scoring.mean([r.pre_value for r in c])), "post": scoring.display(scoring.mean([r.post_value for r in c]))},
                         "gain": scoring.display(scoring.mean([r.normalized_gain for r in k]), 2), "bands": bands,
                         "pass_mark": settings["pass_mark"]}
        # programs ranked by knowledge change
        versions = {v.id: v for v in db.query(TrainingRevision).filter_by(tenant_id=user.tenant_id)}
        programs = {p.id: p for p in db.query(TrainingProgram).filter_by(tenant_id=user.tenant_id)}
        per_program: dict[int, list] = {}
        for r in k:
            v = versions.get(by_id[r.session_id].revision_id)
            if v:
                per_program.setdefault(v.program_id, []).append(r)
        threshold = int(settings["min_group_size"])
        executive_only = "executive" in held and not ({"admin", "hr", "facilitator"} & held)
        out["programs"] = sorted([{"program": programs[pid].name, "n": len(rs), "knowledge_change": scoring.display(scoring.mean([r.delta for r in rs])),
                                   "gain": scoring.display(scoring.mean([r.normalized_gain for r in rs]), 2)}
                                  for pid, rs in per_program.items() if pid in programs and not (executive_only and len(rs) < threshold)],
                                 key=lambda x: -(x["knowledge_change"] or -999))[:6]
        # facilitators: sessions run, knowledge change, participant feedback
        ratings: dict[int, list[float]] = {}
        for s in sessions:
            inst = db.query(TrainingInstance).filter_by(session_id=s.id, type="feedback").first()
            if inst is None:
                continue
            template = db.get(TrainingTemplate, inst.template_id)
            if inst.anonymous:
                from app.models.training import TrainingAnonymousResponse
                answers = [a.answers for a in db.query(TrainingAnonymousResponse).filter_by(instance_id=inst.id)]
                if len(answers) < threshold:
                    continue
            else:
                from app.models.training import TrainingResponse
                answers = [a.answers for a in db.query(TrainingResponse).filter_by(instance_id=inst.id)]
            for a in answers:
                value = analytics.feedback_rating(template, [a])
                if value is not None:
                    ratings.setdefault(s.facilitator_id, []).append(value)
        all_ratings = [v for vs in ratings.values() for v in vs]
        out["feedback"] = {"rating": scoring.display(scoring.mean(all_ratings), 2), "n": len(all_ratings),
                           "distribution": [sum(1 for v in all_ratings if lo <= v < hi) for lo, hi in ((1, 2), (2, 3), (3, 4), (4, 4.5), (4.5, 5.01))]}
        if {"admin", "hr", "executive"} & held:
            fac: dict[int, dict] = {}
            for s in sessions:
                if s.status in ("completed", "closed"):
                    fac.setdefault(s.facilitator_id, {"sessions": 0, "deltas": []})["sessions"] += 1
            for r in k:
                fid = by_id[r.session_id].facilitator_id
                fac.setdefault(fid, {"sessions": 0, "deltas": []})["deltas"].append(r.delta)
            out["facilitators"] = sorted([{"name": db.get(User, fid).name, "sessions": f["sessions"],
                                           "knowledge_change": scoring.display(scoring.mean(f["deltas"])),
                                           "rating": scoring.display(scoring.mean(ratings.get(fid, [])), 2)}
                                          for fid, f in fac.items()], key=lambda x: -(x["knowledge_change"] or -999))[:6]

    # ---- certificates and messages (who may see them)
    if policy.can(held, "certifications", "r") and ({"admin", "hr", "executive"} & held):
        status = [catalog.certificate_status(c, int(settings["expiring_days"]), now) for c in db.query(TrainingCertificate).filter_by(tenant_id=user.tenant_id)]
        out["certificates"] = {k: status.count(k) for k in ("Valid", "Expiring", "Expired", "Revoked")}
    if policy.can(held, "notifications", "r") and "training_messages" in pages:
        start = (now - dt.timedelta(days=13)).date()
        days = [start + dt.timedelta(days=i) for i in range(14)]
        notes = db.query(TrainingNotification).filter(TrainingNotification.tenant_id == user.tenant_id,
                                                      TrainingNotification.scheduled_at >= dt.datetime.combine(start, dt.time())).all()
        out["messages"] = [{"day": d.isoformat(),
                            "sent": sum(1 for n in notes if n.sent_at and n.sent_at.date() == d),
                            "skipped": sum(1 for n in notes if n.status == "skipped" and n.scheduled_at.date() == d),
                            "failed": sum(1 for n in notes if n.status == "failed" and n.scheduled_at.date() == d)} for d in days]
    return out


def overview(db, user: User, pages: list[str]) -> dict:
    held = policy.roles(db, user)
    now = utcnow()
    settings = policy.settings(db, user.tenant_id)
    out: dict = {"roles": sorted(held), "pages": [p for p in pages if p.startswith("training")], "alerts": []}
    alerts = out["alerts"]
    has = lambda page: page in pages  # noqa: E731
    staff = bool({"admin", "hr"} & held)

    # ---- sessions (everyone sees the sessions visible to them)
    sessions = visible_sessions(db, user)
    regs_by_session: dict[int, list[TrainingRegistration]] = {}
    ids = [s.id for s in sessions]
    for r in db.query(TrainingRegistration).filter(TrainingRegistration.session_id.in_(ids or [-1])).all():
        regs_by_session.setdefault(r.session_id, []).append(r)
    mine_to_run = [s for s in sessions if user.id in facilitators_of(s)]
    upcoming = sorted([s for s in sessions if s.status in ("scheduled", "draft") and s.starts_at >= now], key=lambda s: s.starts_at)
    no_attendance = [s for s in sessions if s.status in ("in_progress", "completed") and s.starts_at <= now
                     and any(status_of(r) == "registered" for r in regs_by_session.get(s.id, []))
                     and (staff or user.id in facilitators_of(s))]
    out["sessions"] = {
        "next_7_days": sum(1 for s in upcoming if s.starts_at <= now + dt.timedelta(days=7)),
        "in_progress": sum(1 for s in sessions if s.status == "in_progress"),
        "drafts": sum(1 for s in sessions if s.status == "draft"),
        "awaiting_attendance": len(no_attendance),
        "waitlisted": sum(1 for rs in regs_by_session.values() for r in rs if status_of(r) == "waitlisted"),
        "seats_filled": scoring.display(scoring.mean([100 * sum(1 for r in regs_by_session.get(s.id, []) if status_of(r) in ("registered", "attended"))
                                                      / s.capacity for s in upcoming if s.capacity]) ),
        "i_facilitate": len([s for s in mine_to_run if s.status not in ("closed", "cancelled")]),
        "upcoming": [{"id": s.id, "title": s.title, "starts_at": _iso(s.starts_at), "status": s.status, "capacity": s.capacity,
                      "registered": sum(1 for r in regs_by_session.get(s.id, []) if status_of(r) in ("registered", "attended")),
                      "facilitating": user.id in facilitators_of(s)} for s in upcoming[:6]],
    }
    for s in no_attendance[:3]:
        alerts.append({"page": "training_sessions", "tone": "warn", "session_id": s.id, "text": f"Take attendance for '{s.title}' - post assessments go out only to those marked present."})
    for s in [s for s in sessions if s.status == "draft"][:2]:
        alerts.append({"page": "training_sessions", "tone": "info", "session_id": s.id, "text": f"'{s.title}' is still a draft - publish it to open registration."})

    # ---- my trainings
    my_regs = db.query(TrainingRegistration).filter_by(tenant_id=user.tenant_id, user_id=user.id).all()
    open_links = []
    for inv in db.query(TrainingInvitation).filter(TrainingInvitation.registration_id.in_([r.id for r in my_regs] or [-1]),
                                                   TrainingInvitation.consumed_at.is_(None), TrainingInvitation.has_submitted.isnot(True),
                                                   TrainingInvitation.expires_at > now).all():
        delivery = db.query(TrainingDelivery).filter_by(invitation_id=inv.id, kind="invitation").first()
        if inv.instance_id:
            inst = db.get(TrainingInstance, inv.instance_id)
            if inst.opens_at and inst.opens_at > now:
                continue
        reg = next(r for r in my_regs if r.id == inv.registration_id)
        session = db.get(TrainingSession, reg.session_id)
        if session.status == "cancelled" or status_of(reg) == "cancelled":
            continue
        open_links.append({"session": session.title, "phase": inv.phase, "closes_at": _iso(inv.expires_at), "link": delivery.link if delivery else None})
    out["mine"] = {
        "upcoming": sum(1 for r in my_regs if status_of(r) in ("registered", "waitlisted", "nominated")
                        and db.get(TrainingSession, r.session_id).starts_at >= now),
        "completed": sum(1 for r in my_regs if status_of(r) == "attended"),
        "open_assessments": sorted(open_links, key=lambda x: x["closes_at"] or "")[:6],
    }
    for link in out["mine"]["open_assessments"][:2]:
        alerts.append({"page": "training_mine", "tone": "info", "text": f"Your {link['phase']}-assessment for '{link['session']}' is open until {link['closes_at'][:16].replace('T', ' ')} UTC.", "link": link["link"]})

    # ---- programs and assessments
    if policy.can(held, "programs", "r"):
        versions = db.query(TrainingRevision).filter_by(tenant_id=user.tenant_id).all()
        out["programs"] = {"programs": db.query(TrainingProgram).filter_by(tenant_id=user.tenant_id).count(),
                           "published": sum(1 for v in versions if catalog.state_of(v) == "published"),
                           "drafts": sum(1 for v in versions if catalog.state_of(v) == "draft"),
                           "retired": sum(1 for v in versions if catalog.state_of(v) == "retired")}
        if policy.can(held, "versions", "p") and out["programs"]["drafts"]:
            alerts.append({"page": "training_programs", "tone": "info", "text": f"{out['programs']['drafts']} program version(s) are drafts waiting to be published."})
    if policy.can(held, "templates", "r"):
        templates = db.query(TrainingTemplate).filter_by(tenant_id=user.tenant_id).all()
        out["assessments"] = {"templates": len(templates), "drafts": sum(1 for t in templates if t.state == "draft"),
                              "by_type": {k: sum(1 for t in templates if t.type == k) for k in ("pre", "post", "feedback")},
                              "questions": sum(len(t.questions or []) for t in templates)}

    # ---- people
    if staff:
        members = db.query(TrainingMember).filter_by(tenant_id=user.tenant_id).all()
        roles_count = {r: sum(1 for m in members if r in {m.role, *(m.roles or [])}) for r in ("hr", "facilitator", "executive")}
        parts = db.query(TrainingParticipant).filter_by(tenant_id=user.tenant_id).all()
        out["people"] = {"users": db.query(User).filter_by(tenant_id=user.tenant_id, is_active=True).count(), "roles": roles_count,
                         "participants": sum(1 for p in parts if not p.erased_at), "erased": sum(1 for p in parts if p.erased_at),
                         "bounced": sum(1 for p in parts if p.email_invalid), "unsubscribed": sum(1 for p in parts if p.unsubscribed)}
        if out["people"]["bounced"]:
            alerts.append({"page": "training_people", "tone": "warn", "text": f"{out['people']['bounced']} participant email(s) bounced - correct them in People & places."})

    # ---- facilitators
    if policy.can(held, "certifications", "r"):
        certs = db.query(TrainingCertificate).filter_by(tenant_id=user.tenant_id).all()
        if not ({"admin", "hr", "executive"} & held):
            certs = [c for c in certs if c.user_id == user.id]
        status = [catalog.certificate_status(c, int(settings["expiring_days"]), now) for c in certs]
        out["facilitators"] = {"facilitators": len({c.user_id for c in certs} | {m.user_id for m in db.query(TrainingMember).filter_by(tenant_id=user.tenant_id)
                                                                                   if "facilitator" in {m.role, *(m.roles or [])}}) if staff else 1,
                               "valid": status.count("Valid"), "expiring": status.count("Expiring"), "expired": status.count("Expired"),
                               "revoked": status.count("Revoked")}
        for c, st in zip(certs, status):
            if st == "Expiring":
                holder = db.get(User, c.user_id)
                alerts.append({"page": "training_facilitators", "tone": "warn", "text": f"{holder.name}'s '{c.name}' expires {c.expires_at:%d %b %Y}."})

    # ---- outcomes
    if policy.can(held, "analytics", "r") and held != {"participant"}:
        visible = {s.id for s in sessions}
        if not ({"admin", "hr", "executive"} & held):
            visible = {s.id for s in mine_to_run}
        rows = db.query(TrainingOutcome).filter(TrainingOutcome.tenant_id == user.tenant_id, TrainingOutcome.objective_code == "",
                                                TrainingOutcome.session_id.in_(list(visible) or [-1])).all()
        k = [r for r in rows if r.dimension == "knowledge"]
        c = [r for r in rows if r.dimension == "confidence"]
        out["outcomes"] = {"paired_participants": len(k), "sessions_with_results": len({r.session_id for r in k}),
                           "knowledge_change": scoring.display(scoring.mean([r.delta for r in k])),
                           "confidence_change": scoring.display(scoring.mean([r.delta for r in c])),
                           "gain": scoring.display(scoring.mean([r.normalized_gain for r in k]), 2)}

    # ---- messages
    if policy.can(held, "notifications", "r"):
        since = now - dt.timedelta(days=7)
        notes = db.query(TrainingNotification).filter(TrainingNotification.tenant_id == user.tenant_id,
                                                      TrainingNotification.scheduled_at >= since - dt.timedelta(days=60)).all()
        recent = [n for n in notes if n.sent_at and n.sent_at >= since]
        failed = [n for n in notes if n.status == "failed"]
        out["messages"] = {"sent_7d": len(recent), "opened_7d": sum(1 for n in recent if n.status in ("opened", "clicked")),
                           "planned": sum(1 for n in notes if n.status in ("scheduled", "queued")), "failed": len(failed),
                           "skipped_7d": sum(1 for n in notes if n.status == "skipped" and n.scheduled_at >= since),
                           "recent_failures": [{"event": n.event, "recipient": n.recipient_email, "error": n.error, "session_id": n.session_id}
                                               for n in sorted(failed, key=lambda n: n.id, reverse=True)[:5]]}
        if failed:
            alerts.append({"page": "training_messages", "tone": "warn", "text": f"{len(failed)} message(s) failed after retries - see Messages."})

    # ---- closing windows with low completion (staff)
    if staff:
        for inst in db.query(TrainingInstance).filter(TrainingInstance.tenant_id == user.tenant_id, TrainingInstance.closes_at > now,
                                                      TrainingInstance.closes_at <= now + dt.timedelta(hours=48)).all():
            invs = db.query(TrainingInvitation).filter_by(instance_id=inst.id).all()
            done = sum(1 for i in invs if i.has_submitted)
            if invs and done / len(invs) < 0.7:
                s = db.get(TrainingSession, inst.session_id)
                if s.id in ids:
                    alerts.append({"page": "training_sessions", "tone": "warn", "session_id": s.id,
                                   "text": f"'{s.title}' {inst.type}-assessment closes within 48 h with {done}/{len(invs)} done - consider resending links."})
    if user.role == UserRole.admin and not db.query(TrainingMember).filter_by(tenant_id=user.tenant_id).first():
        alerts.append({"page": "training_people", "tone": "info", "text": "Nobody has a training role yet - assign HR, facilitators and executives in People & places."})
    out["alerts"] = [a for a in alerts if a["page"] in pages or a["page"] == "training"][:12]
    return out

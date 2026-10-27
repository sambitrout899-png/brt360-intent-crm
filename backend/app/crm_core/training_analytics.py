"""Training analytics: per-participant pre/post outcomes (ANL-001..003),
the session dashboard (ANL-004), comparison across sessions, facilitators,
locations, departments and versions (ANL-005), significance and flags
(ANL-006/007), facilitator scorecards (FAC-004) - all on the pure formulas
in training_scoring."""
from __future__ import annotations

from collections import defaultdict

from fastapi import HTTPException

from app.crm_core import training_policy as policy
from app.crm_core import training_scoring as scoring
from app.crm_core.training_delivery import facilitators_of, owned, person, session_for, status_of
from app.models.auth import User
from app.models.training import (TrainingAnonymousResponse, TrainingCertificate, TrainingInstance, TrainingLocation,
                                 TrainingOutcome, TrainingProgram, TrainingQaReview, TrainingRegistration, TrainingResponse,
                                 TrainingRevision, TrainingSession, TrainingTemplate)
from app.timeutils import utcnow


def _key(response) -> str:
    return f"u{response.user_id}" if response.user_id else f"p{response.participant_id}"


def _instances(db, session_id: int) -> dict[str, tuple[TrainingInstance, TrainingTemplate]]:
    out = {}
    for inst in db.query(TrainingInstance).filter_by(session_id=session_id).all():
        out[inst.type] = (inst, db.get(TrainingTemplate, inst.template_id))
    return out


def participant_results(db, session: TrainingSession) -> dict:
    """Everything participant-level for one session, computed from responses."""
    inst = _instances(db, session.id)
    settings = policy.settings(db, session.tenant_id, session)
    version = db.get(TrainingRevision, session.revision_id)
    objectives = version.objectives or []
    if "pre" not in inst or "post" not in inst:
        return {"template_based": False, "settings": settings, "objectives": objectives, "rows": [], "post_only": 0,
                "late_excluded": 0, "inst": inst}
    (pre_i, pre_t), (post_i, post_t) = inst["pre"], inst["post"]
    k_keys = scoring.paired_keys(pre_t.questions, post_t.questions, "knowledge")
    c_keys = scoring.paired_keys(pre_t.questions, post_t.questions, "confidence")
    responses = db.query(TrainingResponse).filter(TrainingResponse.session_id == session.id,
                                                  TrainingResponse.instance_id.in_((pre_i.id, post_i.id))).all()
    pre = {_key(r): r for r in responses if r.instance_id == pre_i.id}
    post = {_key(r): r for r in responses if r.instance_id == post_i.id}
    rows, post_only, late_excluded = [], 0, 0
    for key, after in post.items():
        before = pre.get(key)
        if before is None:
            post_only += 1  # BR-ASM-02
            continue
        if before.late and settings["exclude_late_pre"]:
            late_excluded += 1  # BR-ASM-03
            continue
        k_pre = scoring.knowledge_score(pre_t.questions, before.answers, k_keys)
        k_post = scoring.knowledge_score(post_t.questions, after.answers, k_keys)
        c_pre = scoring.confidence_score(pre_t.questions, before.answers, c_keys)
        c_post = scoring.confidence_score(post_t.questions, after.answers, c_keys)
        g = scoring.normalized_gain(k_pre, k_post)
        by_objective = {}
        for o in objectives:
            code = o["code"]
            by_objective[code] = {
                "pre": scoring.objective_percent([q for q in pre_t.questions if q["item_key"] in k_keys], before.answers, code),
                "post": scoring.objective_percent([q for q in post_t.questions if q["item_key"] in k_keys], after.answers, code),
                "c_pre": scoring.objective_confidence([q for q in pre_t.questions if q["item_key"] in c_keys], before.answers, code),
                "c_post": scoring.objective_confidence([q for q in post_t.questions if q["item_key"] in c_keys], after.answers, code),
            }
        rows.append({"key": key, "user_id": after.user_id, "participant_id": after.participant_id,
                     "k_pre": k_pre, "k_post": k_post, "dk": scoring.delta(k_pre, k_post),
                     "c_pre": c_pre, "c_post": c_post, "dc": scoring.delta(c_pre, c_post), "g": g, "band": scoring.gain_band(g),
                     "objectives": by_objective})
    # outcome achievement uses every post submission (10.3), paired or not
    achievement = {}
    for o in objectives:
        code = o["code"]
        measured = [q for q in post_t.questions if q.get("objective") == code and q["measure"] == "knowledge"]
        if not measured:
            achievement[code] = None
            continue
        flags_ = [scoring.achieved(scoring.objective_percent(post_t.questions, r.answers, code), float(settings["pass_mark"])) for r in post.values()]
        achievement[code] = {"achieved": sum(1 for f in flags_ if f), "n": len(flags_)}
    measured_codes = [c for c, a in achievement.items() if a is not None]
    all_achieved = sum(1 for r in post.values()
                       if measured_codes and all(scoring.achieved(scoring.objective_percent(post_t.questions, r.answers, c), float(settings["pass_mark"]))
                                                 for c in measured_codes))
    return {"template_based": True, "settings": settings, "objectives": objectives, "rows": rows, "post_only": post_only,
            "late_excluded": late_excluded, "inst": inst, "pre_count": len(pre), "post_count": len(post),
            "achievement": achievement, "outcome_rate": (all_achieved / len(post)) if post and measured_codes else None}


def recompute(db, session: TrainingSession) -> int:
    """outcome_result rows for one session (derived; safe to run any time)."""
    data = participant_results(db, session)
    db.query(TrainingOutcome).filter_by(session_id=session.id).delete()
    now = utcnow()
    count = 0
    for r in data["rows"]:
        db.add(TrainingOutcome(tenant_id=session.tenant_id, session_id=session.id, participant_key=r["key"], objective_code="",
                               dimension="knowledge", pre_value=r["k_pre"], post_value=r["k_post"], delta=r["dk"],
                               normalized_gain=r["g"], computed_at=now))
        db.add(TrainingOutcome(tenant_id=session.tenant_id, session_id=session.id, participant_key=r["key"], objective_code="",
                               dimension="confidence", pre_value=r["c_pre"], post_value=r["c_post"], delta=r["dc"], computed_at=now))
        for code, v in r["objectives"].items():
            db.add(TrainingOutcome(tenant_id=session.tenant_id, session_id=session.id, participant_key=r["key"], objective_code=code,
                                   dimension="knowledge", pre_value=v["pre"], post_value=v["post"], delta=scoring.delta(v["pre"], v["post"]),
                                   computed_at=now))
        count += 1
    db.flush()
    return count


def _summary(values: list[float | None]) -> dict:
    clean = [v for v in values if v is not None]
    return {"mean": scoring.display(scoring.mean(clean)), "n": len(clean)}


def feedback_summary(db, session: TrainingSession, settings: dict, inst) -> dict | None:
    if "feedback" not in inst:
        return None
    feedback_i, template = inst["feedback"]
    if feedback_i.anonymous:
        answers = [r.answers for r in db.query(TrainingAnonymousResponse).filter_by(instance_id=feedback_i.id).all()]
    else:
        answers = [r.answers for r in db.query(TrainingResponse).filter_by(instance_id=feedback_i.id).all()]
    n, threshold = len(answers), int(settings["min_group_size"])
    if feedback_i.anonymous and n < threshold:
        return {"anonymous": True, "n": n, "suppressed": True,
                "message": "Not enough responses to protect anonymity"}  # ASM-023, BR-ASM-04
    questions = []
    for q in template.questions:
        values = [a.get(q["item_key"]) for a in answers if a.get(q["item_key"]) not in (None, "", [])]
        item = {"item_key": q["item_key"], "text": q["text"], "type": q["type"], "n": len(values)}
        if q["type"] in scoring.NUMBER_RANGES:
            item["mean"] = scoring.display(scoring.mean(values))
            if q["type"] == "nps" and values:
                promoters = sum(1 for v in values if v >= 9)
                detractors = sum(1 for v in values if v <= 6)
                item["nps"] = round(100 * (promoters - detractors) / len(values))
        elif q["type"] in scoring.CHOICE_TYPES:
            counts = [0] * len(q.get("options") or [])
            for v in values:
                for i in (v if isinstance(v, list) else [v]):
                    if isinstance(i, int) and 0 <= i < len(counts):
                        counts[i] += 1
            item["distribution"] = [{"label": label, "count": c} for label, c in zip(q.get("options") or [], counts)]
        else:
            item["comments"] = [str(v) for v in values][:200]
        questions.append(item)
    return {"anonymous": feedback_i.anonymous, "n": n, "suppressed": False, "questions": questions, "rating": feedback_rating(template, answers)}


def feedback_rating(template: TrainingTemplate, answers: list[dict]) -> float | None:
    """One 1-5 rating per response (Likert as is, 1-10 halved), then their mean."""
    per_response = []
    for a in answers:
        values = []
        for q in template.questions:
            v = a.get(q["item_key"])
            if q["measure"] != "feedback" or not isinstance(v, (int, float)) or isinstance(v, bool):
                continue
            if q["type"] == "likert5":
                values.append(float(v))
            elif q["type"] == "rating10":
                values.append(float(v) / 2)
        if values:
            per_response.append(sum(values) / len(values))
    return scoring.mean(per_response)


def session_dashboard(db, user: User, session_id: int) -> dict:
    held = policy.guard(db, user, "analytics", "r")
    session, held = session_for(db, user, session_id, "r")
    if held == {"participant"}:
        raise HTTPException(403, "Outcome reporting requires a staff role")
    aggregate_only = "executive" in held and not ({"admin", "hr", "facilitator"} & held)
    data = participant_results(db, session)
    settings = data["settings"]
    regs = db.query(TrainingRegistration).filter_by(session_id=session.id).all()
    invited = [r for r in regs if status_of(r) in ("registered", "attended", "no_show")]
    funnel = {"invited": len(invited), "pre_done": data.get("pre_count", 0),
              "attended": sum(1 for r in regs if status_of(r) == "attended"), "post_done": data.get("post_count", 0)}
    rows = data["rows"]
    out = {"session": {"id": session.id, "title": session.title, "status": session.status},
           "template_based": data["template_based"], "funnel": funnel, "paired_n": len(rows),
           "post_only": data["post_only"], "late_pre_excluded": data["late_excluded"], "aggregate_only": aggregate_only,
           "pass_mark": settings["pass_mark"]}
    if not data["template_based"]:
        out["note"] = "This session runs the original single-form assessment - see Learning outcomes."
        return out
    k_pre, k_post = [r["k_pre"] for r in rows], [r["k_post"] for r in rows]
    c_pre, c_post = [r["c_pre"] for r in rows], [r["c_post"] for r in rows]
    bands = defaultdict(int)
    for r in rows:
        bands[r["band"] or "Excluded (pre 100%)"] += 1
    dk, dc = scoring.mean([r["dk"] for r in rows]), scoring.mean([r["dc"] for r in rows])
    objectives = []
    objective_post = {}
    for o in data["objectives"]:
        code = o["code"]
        pre_v = [r["objectives"][code]["pre"] for r in rows]
        post_v = [r["objectives"][code]["post"] for r in rows]
        measured = any(v is not None for v in pre_v + post_v) or data["achievement"].get(code) is not None
        post_mean = scoring.mean(post_v)
        objective_post[code] = post_mean
        ach = data["achievement"].get(code)
        objectives.append({"code": code, "text": o["text"], "measured": measured,
                           "status": None if measured else "not measured",
                           "pre": scoring.display(scoring.mean(pre_v)), "post": scoring.display(post_mean),
                           "change": scoring.display(scoring.delta(scoring.mean(pre_v), post_mean)),
                           "confidence_pre": scoring.display(scoring.mean([r["objectives"][code]["c_pre"] for r in rows])),
                           "confidence_post": scoring.display(scoring.mean([r["objectives"][code]["c_post"] for r in rows])),
                           "achieved": ach["achieved"] if ach else None, "achieved_of": ach["n"] if ach else None})
    stats = {"knowledge": scoring.significance(k_pre, k_post), "confidence": scoring.significance(c_pre, c_post)}
    for value in stats.values():
        if value:
            value["p_value"] = round(value["p_value"], 4)
            value["cohens_d"] = None if value["cohens_d"] is None else round(value["cohens_d"], 2)
            value["mean_difference"] = round(value["mean_difference"], 2)
    out.update({
        "knowledge": {"pre": _summary(k_pre), "post": _summary(k_post), "change": scoring.display(dk)},
        "confidence": {"pre": _summary(c_pre), "post": _summary(c_post), "change": scoring.display(dc)},
        "gain": {"mean": scoring.display(scoring.mean([r["g"] for r in rows]), 2), "bands": dict(bands)},
        "objectives": objectives, "outcome_rate": scoring.display(None if data["outcome_rate"] is None else data["outcome_rate"] * 100),
        "significance": stats, "flags": scoring.flags(objective_post, float(settings["pass_mark"]), dc, dk),
        "feedback": feedback_summary(db, session, settings, data["inst"]),
    })
    if not aggregate_only:
        people = []
        for r in rows:
            reg = db.query(TrainingRegistration).filter_by(session_id=session.id, user_id=r["user_id"], participant_id=r["participant_id"]).first()
            who = person(db, reg) if reg else {"name": r["key"]}
            people.append({"name": who["name"], "k_pre": scoring.display(r["k_pre"]), "k_post": scoring.display(r["k_post"]),
                           "dk": scoring.display(r["dk"]), "c_pre": scoring.display(r["c_pre"]), "c_post": scoring.display(r["c_post"]),
                           "dc": scoring.display(r["dc"]), "g": scoring.display(r["g"], 2), "band": r["band"]})
        out["participants"] = people
    return out


GROUPS = ("session", "facilitator", "location", "department", "program_version", "program")


def compare(db, user: User, group_by: str, start=None, end=None) -> dict:
    """ANL-005 from the stored outcome rows (participant-level means, with n)."""
    held = policy.guard(db, user, "analytics", "r")
    if held == {"participant"}:
        raise HTTPException(403, "Comparisons require a staff role")
    if group_by not in GROUPS:
        raise HTTPException(422, f"group_by must be one of {', '.join(GROUPS)}")
    from app.crm_core.training_delivery import visible_sessions
    sessions = {s.id: s for s in visible_sessions(db, user)
                if (not start or s.starts_at >= start) and (not end or s.starts_at <= end)}
    if "facilitator" in held and not ({"admin", "hr", "executive"} & held):
        sessions = {k: s for k, s in sessions.items() if user.id in facilitators_of(s)}
    settings = policy.settings(db, user.tenant_id)
    versions = {v.id: v for v in db.query(TrainingRevision).filter_by(tenant_id=user.tenant_id)}
    programs = {p.id: p for p in db.query(TrainingProgram).filter_by(tenant_id=user.tenant_id)}
    locations = {loc.id: loc for loc in db.query(TrainingLocation).filter_by(tenant_id=user.tenant_id)}
    from app.models.org import Department
    departments = {d.id: d for d in db.query(Department).filter_by(tenant_id=user.tenant_id)}
    users = {u.id: u for u in db.query(User).filter_by(tenant_id=user.tenant_id)}
    groups: dict[str, dict] = {}
    rows = db.query(TrainingOutcome).filter(TrainingOutcome.tenant_id == user.tenant_id, TrainingOutcome.objective_code == "",
                                            TrainingOutcome.session_id.in_(list(sessions) or [-1])).all()
    for row in rows:
        s = sessions[row.session_id]
        version = versions.get(s.revision_id)
        if group_by == "session":
            keys = [(f"s{s.id}", f"{s.title} ({s.starts_at:%Y-%m-%d})")]
        elif group_by == "facilitator":
            keys = [(f"f{s.facilitator_id}", users[s.facilitator_id].name if s.facilitator_id in users else "?")]
        elif group_by == "location":
            keys = [(f"l{s.location_id}", locations[s.location_id].name if s.location_id in locations else (s.location or "No location"))]
        elif group_by == "department":
            keys = [(f"d{s.department_id}", departments[s.department_id].name if s.department_id in departments else "Whole organization")]
        elif group_by == "program_version":
            program = programs.get(version.program_id) if version else None
            keys = [(f"v{s.revision_id}", f"{program.name if program else '?'} v{version.version_label or version.version}")]
        else:
            program = programs.get(version.program_id) if version else None
            keys = [(f"p{program.id if program else 0}", program.name if program else "?")]
        for key, label in keys:
            g = groups.setdefault(key, {"key": key, "label": label, "participants": set(), "sessions": set(),
                                        "k_pre": [], "k_post": [], "dk": [], "g": [], "c_pre": [], "c_post": [], "dc": []})
            g["participants"].add((row.session_id, row.participant_key))
            g["sessions"].add(row.session_id)
            if row.dimension == "knowledge":
                g["k_pre"].append(row.pre_value)
                g["k_post"].append(row.post_value)
                g["dk"].append(row.delta)
                g["g"].append(row.normalized_gain)
            else:
                g["c_pre"].append(row.pre_value)
                g["c_post"].append(row.post_value)
                g["dc"].append(row.delta)
    threshold = int(settings["min_group_size"])
    executive = "executive" in held and not ({"admin", "hr", "facilitator"} & held)
    out, hidden = [], 0
    for g in groups.values():
        n = len(g["participants"])
        if executive and n < threshold:
            hidden += 1
            continue
        out.append({"key": g["key"], "label": g["label"], "n": n, "sessions": len(g["sessions"]),
                    "knowledge_pre": scoring.display(scoring.mean(g["k_pre"])), "knowledge_post": scoring.display(scoring.mean(g["k_post"])),
                    "knowledge_change": scoring.display(scoring.mean(g["dk"])), "gain": scoring.display(scoring.mean(g["g"]), 2),
                    "confidence_pre": scoring.display(scoring.mean(g["c_pre"])), "confidence_post": scoring.display(scoring.mean(g["c_post"])),
                    "confidence_change": scoring.display(scoring.mean(g["dc"])), "small_group": n < threshold})
    return {"group_by": group_by, "groups": sorted(out, key=lambda x: x["label"]), "hidden_small_groups": hidden,
            "threshold": threshold}


def own_scores(db, user: User) -> list[dict]:
    """A participant's own pre/post scores, when the org enables it."""
    policy.guard(db, user, "analytics", "r")
    if not policy.settings(db, user.tenant_id)["show_own_scores"]:
        return []
    out = []
    for r in db.query(TrainingResponse).filter(TrainingResponse.tenant_id == user.tenant_id, TrainingResponse.user_id == user.id,
                                               TrainingResponse.instance_id.isnot(None)).all():
        s = db.get(TrainingSession, r.session_id)
        out.append({"session": s.title, "phase": r.phase, "knowledge": scoring.display(r.score),
                    "confidence": scoring.display(r.confidence_avg)})
    return out


# ---------------------------------------------------------------- scorecards (FAC-004)


def scorecard(db, user: User, facilitator_id: int) -> dict:
    held = policy.guard(db, user, "certifications", "r")
    if held <= {"facilitator", "participant"} and facilitator_id != user.id:
        raise HTTPException(403, "Facilitators see their own scorecard")
    facilitator = owned(db, User, user.tenant_id, facilitator_id)
    settings = policy.settings(db, user.tenant_id)
    sessions = [s for s in db.query(TrainingSession).filter_by(tenant_id=user.tenant_id).all() if facilitator_id in facilitators_of(s)]
    delivered = [s for s in sessions if s.status in ("completed", "closed")]
    ratings, gains = [], []
    for s in sessions:
        inst = _instances(db, s.id)
        if "feedback" in inst:
            feedback_i, template = inst["feedback"]
            if feedback_i.anonymous:
                answers = [r.answers for r in db.query(TrainingAnonymousResponse).filter_by(instance_id=feedback_i.id)]
                if len(answers) < int(settings["min_group_size"]):
                    answers = []  # BR-ASM-04 holds here too
            else:
                answers = [r.answers for r in db.query(TrainingResponse).filter_by(instance_id=feedback_i.id)]
            for a in answers:
                value = feedback_rating(template, [a])
                if value is not None:
                    ratings.append(value)
        gains += [row.delta for row in db.query(TrainingOutcome).filter_by(session_id=s.id, dimension="knowledge", objective_code="")]
    latest = db.query(TrainingQaReview).filter_by(tenant_id=user.tenant_id, facilitator_id=facilitator_id) \
        .order_by(TrainingQaReview.review_date.desc(), TrainingQaReview.id.desc()).first()
    from app.crm_core.training_catalog import certificate_status
    certs = db.query(TrainingCertificate).filter_by(tenant_id=user.tenant_id, user_id=facilitator_id).all()
    rating = scoring.mean(ratings)
    return {"facilitator": {"id": facilitator.id, "name": facilitator.name},
            "sessions_delivered": len(delivered), "sessions_assigned": len(sessions),
            "average_feedback": None if rating is None else round(rating, 2), "feedback_n": len(ratings),
            "average_knowledge_gain": None if not [g for g in gains if g is not None] else round(scoring.mean(gains), 2),
            "latest_qa_rating": latest.overall_rating if latest else None,
            "certifications": [{"id": c.id, "name": c.name, "expires_at": c.expires_at.isoformat() + "Z",
                                "status": certificate_status(c, int(settings["expiring_days"]))} for c in certs],
            "below_threshold": rating is not None and rating < float(settings["rating_threshold"])}


def save_qa_review(db, user: User, payload: dict) -> TrainingQaReview:
    policy.guard(db, user, "qa_reviews", "c")
    session = owned(db, TrainingSession, user.tenant_id, payload["session_id"])
    facilitator_id = int(payload["facilitator_id"])
    if facilitator_id not in facilitators_of(session):
        raise HTTPException(422, "That facilitator did not run this session")
    rubric = policy.settings(db, user.tenant_id)["qa_rubric"]
    scores = {k: int(v) for k, v in (payload.get("scores") or {}).items()}
    if set(scores) - set(rubric):
        raise HTTPException(422, f"Score only the rubric criteria: {', '.join(rubric)}")
    if any(not 1 <= v <= 5 for v in scores.values()):
        raise HTTPException(422, "Rubric scores are 1-5")
    overall = payload.get("overall_rating")
    if overall is None:
        overall = scoring.mean(scores.values())
    if overall is None or not 1 <= float(overall) <= 5:
        raise HTTPException(422, "Give an overall rating from 1 to 5")
    row = TrainingQaReview(tenant_id=user.tenant_id, session_id=session.id, facilitator_id=facilitator_id, reviewer_id=user.id,
                           review_date=utcnow(), scores=scores, overall_rating=round(float(overall), 2),
                           comments=(payload.get("comments") or "")[:4000], actions=(payload.get("actions") or "")[:4000])
    db.add(row)
    db.flush()
    policy.audit(db, user.tenant_id, user.id, "qa_review.create", "training_qa_review", row.id, None, policy.snapshot(row))
    return row

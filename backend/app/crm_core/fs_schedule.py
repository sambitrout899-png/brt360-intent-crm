"""Scheduling and dispatch - who does the job, and when.

- rank(): the Assign command - every engineer scored the way Assignment
  Manager does it: required skills (with expertise weighting), service
  region, workload  [weight x (1 - load / max load)], how soon they are
  free, and whether their trunk already holds the parts.
- slots(): the Appointment Booking System - free time windows inside each
  qualified engineer's schedule hours, outside breaks/absences and booked
  jobs (with travel time between jobs), offered earliest-first or by
  resource leveling / loading.
- book() / unassign(): what a drag and drop on the dispatch board does.
- auto_schedule(): contract scheduling - unscheduled work booked in
  priority and committed-date order into the best free slot.
- board(): the dispatch board's Gantt rows and the unscheduled list.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from sqlalchemy.orm import Session

from app.crm_core import fs_inventory, fs_service as fs
from app.crm_core.fs_calendar import Calendar
from app.models.assurance import Technician, TechnicianAssignment, WorkOrder
from app.models.field_service import FsAbsence, FsActivity, FsEngineer, FsServiceRegion
from app.timeutils import utcnow

HEURISTICS = ("earliest", "leveling", "loading")
STEP = dt.timedelta(minutes=15)


def _round_up(moment: dt.datetime) -> dt.datetime:
    base = moment.replace(second=0, microsecond=0)
    if base < moment:
        base += dt.timedelta(minutes=1)
    return base + dt.timedelta(minutes=(-base.minute) % 15)


def default_region(db: Session, tenant_id: int) -> FsServiceRegion | None:
    return db.query(FsServiceRegion).filter(FsServiceRegion.tenant_id == tenant_id).order_by(FsServiceRegion.id).first()


def region_of(db: Session, tenant_id: int, profile: FsEngineer | None) -> FsServiceRegion | None:
    if profile is not None and profile.region_id:
        region = db.get(FsServiceRegion, profile.region_id)
        if region is not None:
            return region
    return default_region(db, tenant_id)


def calendar_of(db: Session, tenant_id: int, profile: FsEngineer | None) -> Calendar:
    return Calendar.of(region_of(db, tenant_id, profile), profile)


def travel_of(db: Session, tenant_id: int, profile: FsEngineer | None) -> dt.timedelta:
    region = region_of(db, tenant_id, profile)
    return dt.timedelta(minutes=region.travel_minutes if region else 30)


def busy(db: Session, tenant_id: int, technician_id: int, start: dt.datetime, end: dt.datetime,
         exclude_activity: int | None = None) -> list[tuple[dt.datetime, dt.datetime, str]]:
    """What already fills an engineer's time between start and end (UTC)."""
    wo_ids = [a.work_order_id for a in db.query(TechnicianAssignment).filter(TechnicianAssignment.technician_id == technician_id).all()]
    out: list[tuple[dt.datetime, dt.datetime, str]] = []
    if wo_ids:
        for act in db.query(FsActivity).filter(FsActivity.work_order_id.in_(wo_ids), FsActivity.planned_start.isnot(None),
                                               FsActivity.planned_start < end, FsActivity.planned_end > start).all():
            if act.id == exclude_activity:
                continue
            wo = db.get(WorkOrder, act.work_order_id)
            if wo is None or wo.status == "cancelled":
                continue
            out.append((act.planned_start, act.planned_end, f"activity {act.id}"))
    for ab in db.query(FsAbsence).filter(FsAbsence.tenant_id == tenant_id, FsAbsence.technician_id == technician_id,
                                         FsAbsence.starts_at < end, FsAbsence.ends_at > start).all():
        out.append((ab.starts_at, ab.ends_at, f"away: {ab.reason or 'unavailable'}"))
    return sorted(out)


def jobs_on(db: Session, tenant_id: int, technician_id: int, cal: Calendar, day: dt.date) -> tuple[int, float]:
    win = cal.window(day) or (cal.utc(dt.datetime.combine(day, dt.time())), cal.utc(dt.datetime.combine(day + dt.timedelta(days=1), dt.time())))
    jobs = [b for b in busy(db, tenant_id, technician_id, win[0], win[1]) if b[2].startswith("activity")]
    return len(jobs), sum((b[1] - b[0]).total_seconds() / 60 for b in jobs)


def free_starts(window: tuple[dt.datetime, dt.datetime], taken: list[tuple[dt.datetime, dt.datetime, str]], duration: dt.timedelta,
                travel: dt.timedelta, not_before: dt.datetime, per_gap: int = 3) -> list[dt.datetime]:
    """Start times that fit `duration` into the window around what is taken (travel time kept free both sides)."""
    out: list[dt.datetime] = []
    cursor = _round_up(max(window[0], not_before))
    edges = [(s, e) for s, e, _ in taken if e > window[0] and s < window[1]]
    edges.append((window[1] + travel, window[1] + travel))  # the end of the day closes the last gap
    for s, e in edges:
        gap_end = min(s - travel, window[1])
        placed = 0
        t = cursor
        while t + duration <= gap_end and placed < per_gap:
            out.append(t)
            placed += 1
            t = t + max(duration, dt.timedelta(hours=2))
        cursor = _round_up(max(cursor, e + travel))
        if cursor >= window[1]:
            break
    return out


def _needs(act: FsActivity) -> list[str]:
    return [s for s in fs._j(act.skills_json, []) if s and s != "general"]


def _parts_score(db: Session, tenant_id: int, act: FsActivity, technician_id: int) -> tuple[float, str]:
    items = [i for i in fs._j(act.items_json, []) if isinstance(i, dict) and i.get("product") and not i.get("tool")]
    if not items:
        return 5.0, "no parts needed"
    trunk = fs_inventory.trunk_of(db, tenant_id, technician_id)
    if trunk is None:
        return 0.0, "no trunk stock"
    have = 0
    for i in items:
        lv = fs_inventory.level(db, tenant_id, trunk.id, i["product"], "good", create=False)
        if lv is not None and lv.on_hand - lv.reserved >= float(i.get("quantity") or 1):
            have += 1
    if have == len(items):
        return 10.0, "parts in trunk"
    return (5.0, f"{have}/{len(items)} parts in trunk") if have else (0.0, "parts not in trunk")


def _slot_for(db: Session, tenant_id: int, act: FsActivity, tech: Technician, profile: FsEngineer, start_day: dt.date, days: int,
              now: dt.datetime, per_gap: int = 3) -> list[tuple[dt.datetime, dt.datetime]]:
    cal = calendar_of(db, tenant_id, profile)
    travel = travel_of(db, tenant_id, profile)
    duration = dt.timedelta(minutes=act.duration_minutes)
    not_before = max(now, act.earliest_start or now)
    out = []
    for i in range(days):
        day = start_day + dt.timedelta(days=i)
        win = cal.window(day)
        if win is None or win[1] <= not_before:
            continue
        jobs, _ = jobs_on(db, tenant_id, tech.id, cal, day)
        if jobs >= max(1, profile.max_jobs_per_day):
            continue
        taken = busy(db, tenant_id, tech.id, win[0] - dt.timedelta(hours=12), win[1] + dt.timedelta(hours=12), exclude_activity=act.id)
        for s in free_starts(win, taken, duration, travel, not_before, per_gap):
            out.append((s, s + duration))
    return out


def rank(db: Session, tenant_id: int, activity_id: int, days: int = 7, now: Any = None) -> list[dict[str, Any]]:
    act, _ = fs.activity(db, tenant_id, activity_id)
    now = fs._when(now) or utcnow()
    needs = _needs(act)
    horizon_hours = max(24.0, days * 24.0)
    out = []
    for tech, profile in fs.engineers(db, tenant_id):
        skills = fs.skills_of(profile, tech)
        missing = [s for s in needs if s not in skills]
        cal = calendar_of(db, tenant_id, profile)
        start_day = cal.local_date(max(now, act.earliest_start or now))
        first = _slot_for(db, tenant_id, act, tech, profile, start_day, days, now, per_gap=1)
        nxt = first[0] if first else None
        skill = round(sum(15.0 * skills.get(s, 0) / 3 for s in needs) / len(needs), 1) if needs else 10.0
        if act.region_id:
            region = 20.0 if profile.region_id == act.region_id else (8.0 if profile.region_id is None else 0.0)
        else:
            region = 10.0
        jobs, _ = jobs_on(db, tenant_id, tech.id, cal, cal.local_date(nxt[0]) if nxt else start_day)
        workload = round(20.0 * max(0.0, 1 - jobs / max(1, profile.max_jobs_per_day)), 1)
        availability = round(30.0 * (1 - min(1.0, ((nxt[0] - now).total_seconds() / 3600) / horizon_hours)), 1) if nxt else 0.0
        parts, parts_note = _parts_score(db, tenant_id, act, tech.id)
        late = bool(nxt and act.due_at and nxt[0] > act.due_at)
        total = skill + region + workload + availability + parts - (15.0 if late else 0.0)
        qualified = not missing
        out.append({
            "technician_id": tech.id, "name": tech.name, "region_id": profile.region_id, "qualified": qualified, "missing_skills": missing,
            "score": round(total, 1) if qualified else 0.0, "available": nxt is not None,
            "next_start": nxt[0] if nxt else None, "next_end": nxt[1] if nxt else None, "late": late,
            "breakdown": {"skills": skill, "region": region, "workload": workload, "availability": availability, "parts": parts},
            "parts_note": parts_note, "jobs_that_day": jobs,
        })
    return sorted(out, key=lambda r: (not r["qualified"], not r["available"], -r["score"], r["name"]))


def slots(db: Session, tenant_id: int, activity_id: int, start_day: Any = None, days: int = 7, heuristic: str = "earliest",
          limit: int = 8, technician_id: int | None = None, now: Any = None) -> list[dict[str, Any]]:
    act, wo = fs.activity(db, tenant_id, activity_id)
    now = fs._when(now) or utcnow()
    needs = _needs(act)
    current = fs.assigned_technician(db, wo)
    if act.locked and current is not None:
        technician_id = current.id
    rows = []
    for tech, profile in fs.engineers(db, tenant_id):
        if technician_id and tech.id != technician_id:
            continue
        skills = fs.skills_of(profile, tech)
        if any(s not in skills for s in needs):
            continue
        cal = calendar_of(db, tenant_id, profile)
        first_day = fs._date(start_day) or cal.local_date(max(now, act.earliest_start or now))
        load = 0.0
        for i in range(days):
            load += jobs_on(db, tenant_id, tech.id, cal, first_day + dt.timedelta(days=i))[1]
        for s, e in _slot_for(db, tenant_id, act, tech, profile, first_day, days, now):
            rows.append({"technician_id": tech.id, "name": tech.name, "start": s, "end": e, "booked_minutes": load,
                         "within_commitment": not (act.due_at and s > act.due_at), "local_start": cal.local(s), "local_end": cal.local(e)})
    if heuristic == "leveling":
        rows.sort(key=lambda r: (r["booked_minutes"], r["start"]))
    elif heuristic == "loading":
        rows.sort(key=lambda r: (-r["booked_minutes"], r["start"]))
    else:
        rows.sort(key=lambda r: (r["start"], r["booked_minutes"]))
    if heuristic != "earliest":
        seen: dict[int, int] = {}
        spread = []
        for r in rows:  # resource loading/leveling still offers a choice of times
            seen[r["technician_id"]] = seen.get(r["technician_id"], 0) + 1
            if seen[r["technician_id"]] <= max(2, limit // 2):
                spread.append(r)
        rows = spread
    return rows[:limit]


def _label(cal: Calendar, start: dt.datetime, end: dt.datetime) -> str:
    s, e = cal.local(start), cal.local(end)
    return f"{s:%a %d %b %H:%M}-{e:%H:%M}"


def book(db: Session, tenant_id: int, activity_id: int, technician_id: int, start: Any, force: bool = False) -> FsActivity:
    """Assign and schedule: the engineer does the activity at `start` (UTC)."""
    act, wo = fs.activity(db, tenant_id, activity_id)
    if wo.status in ("complete", "cancelled", "en_route", "on_site"):
        raise ValueError(f"An activity that is '{wo.status.replace('_', ' ')}' can't be rescheduled")
    tech = fs._own(db, Technician, technician_id, tenant_id, "Engineer")
    current = fs.assigned_technician(db, wo)
    if act.locked and current is not None and current.id != tech.id:
        raise ValueError(f"The assignment is locked to {current.name} - unlock it to change the engineer")
    begin = fs._when(start)
    if begin is None:
        raise ValueError("When should the job start?")
    begin = begin.replace(second=0, microsecond=0)
    end = begin + dt.timedelta(minutes=act.duration_minutes)
    profile = fs.engineer_profile(db, tenant_id, tech)
    cal = calendar_of(db, tenant_id, profile)
    if not force:
        missing = [s for s in _needs(act) if s not in fs.skills_of(profile, tech)]
        if missing:
            raise ValueError(f"{tech.name} lacks {', '.join(missing)} - book anyway to override")
        clash = [b for b in busy(db, tenant_id, tech.id, begin, end, exclude_activity=act.id)]
        if clash:
            raise ValueError(f"{tech.name} is busy then ({clash[0][2]}) - book anyway to double-book")
        win = cal.window(cal.local_date(begin))
        if win is None or begin < win[0] or end > win[1]:
            raise ValueError(f"That is outside {tech.name}'s working hours - book anyway for overtime")
    assignment = db.query(TechnicianAssignment).filter(TechnicianAssignment.work_order_id == wo.id).first()
    if assignment is None:
        db.add(TechnicianAssignment(work_order_id=wo.id, technician_id=tech.id))
    elif assignment.technician_id != tech.id:
        assignment.technician_id = tech.id
        assignment.assigned_at = utcnow()
    act.planned_start, act.planned_end = begin, end
    if not act.region_id and profile.region_id:
        act.region_id = profile.region_id
    wo.status = "scheduled"
    wo.scheduled_window = _label(cal, begin, end)
    db.flush()
    return act


def unassign(db: Session, tenant_id: int, activity_id: int) -> FsActivity:
    act, wo = fs.activity(db, tenant_id, activity_id)
    if wo.status not in ("scheduled", "unscheduled"):
        raise ValueError(f"An activity that is '{wo.status.replace('_', ' ')}' can't be unassigned")
    if act.locked:
        raise ValueError("The assignment is locked - unlock it first")
    for a in db.query(TechnicianAssignment).filter(TechnicianAssignment.work_order_id == wo.id).all():
        db.delete(a)
    act.planned_start = act.planned_end = None
    wo.status = "unscheduled"
    wo.scheduled_window = "not scheduled yet"
    db.flush()
    return act


def add_absence(db: Session, tenant_id: int, technician_id: int, starts_at: Any, ends_at: Any, reason: str = "") -> FsAbsence:
    fs._own(db, Technician, technician_id, tenant_id, "Engineer")
    s, e = fs._when(starts_at), fs._when(ends_at)
    if not s or not e or e <= s:
        raise ValueError("An absence needs a start before its end")
    row = FsAbsence(tenant_id=tenant_id, technician_id=technician_id, starts_at=s, ends_at=e, reason=reason)
    db.add(row)
    db.flush()
    return row


def _schedulable(db: Session, tenant_id: int, region_id: int | None = None) -> list[tuple[FsActivity, WorkOrder]]:
    out = []
    for wo in db.query(WorkOrder).filter(WorkOrder.tenant_id == tenant_id, WorkOrder.status.in_(("unscheduled", "scheduled"))).all():
        act = fs.ensure(db, wo)
        if act.planned_start is not None:
            continue
        if region_id and act.region_id not in (None, region_id):
            continue
        out.append((act, wo))
    far = dt.datetime.max
    return sorted(out, key=lambda p: (p[0].priority, p[0].due_at or far, p[0].id))


def auto_schedule(db: Session, tenant_id: int, days: int = 5, region_id: int | None = None, now: Any = None) -> dict[str, Any]:
    """Contract scheduling: book every unscheduled activity, most urgent first, into the best free slot."""
    now = fs._when(now) or utcnow()
    booked, skipped = [], []
    for act, wo in _schedulable(db, tenant_id, region_id):
        current = fs.assigned_technician(db, wo)
        options = slots(db, tenant_id, act.id, days=days, limit=40, technician_id=current.id if current else None, now=now)
        if not options:
            skipped.append({"activity_id": act.id, "subject": act.subject, "reason": "no qualified engineer is free in the next "
                            f"{days} days" + (f" (assigned to {current.name})" if current else "")})
            continue
        ranked = {r["technician_id"]: r["score"] for r in rank(db, tenant_id, act.id, days=days, now=now)}
        best = sorted(options, key=lambda o: (not o["within_commitment"], o["start"].date(), -ranked.get(o["technician_id"], 0), o["start"]))[0]
        book(db, tenant_id, act.id, best["technician_id"], best["start"], force=True)
        booked.append({"activity_id": act.id, "subject": act.subject, "technician_name": best["name"], "start": best["start"],
                       "within_commitment": best["within_commitment"]})
    return {"booked": booked, "skipped": skipped}


def board(db: Session, tenant_id: int, day: Any = None, region_id: int | None = None) -> dict[str, Any]:
    """The dispatch board for one day: a row per engineer, the unscheduled list below."""
    region = db.get(FsServiceRegion, region_id) if region_id else default_region(db, tenant_id)
    cal = Calendar.of(region)
    the_day = fs._date(day) or cal.local_date(utcnow())
    start_local = dt.datetime.combine(the_day, dt.time()) + dt.timedelta(minutes=max(0, cal.start - 60))
    end_local = dt.datetime.combine(the_day, dt.time()) + dt.timedelta(minutes=min(24 * 60, cal.end + 120))
    start, end = cal.utc(start_local), cal.utc(end_local)
    rows = []
    for tech, profile in fs.engineers(db, tenant_id, region_id):
        ecal = calendar_of(db, tenant_id, profile)
        win = ecal.window(the_day)
        jobs = []
        for b in busy(db, tenant_id, tech.id, start, end):
            if b[2].startswith("activity"):
                act = db.get(FsActivity, int(b[2].split()[1]))
                info = fs.activity_out(db, act)
                jobs.append({k: info[k] for k in ("id", "work_order_id", "subject", "status", "priority", "priority_label", "party_name",
                                                  "planned_start", "planned_end", "category", "late", "locked", "ticket_id", "kind")})
        away = [{"start": b[0], "end": b[1], "reason": b[2][6:]} for b in busy(db, tenant_id, tech.id, start, end) if b[2].startswith("away")]
        minutes = sum((j["planned_end"] - j["planned_start"]).total_seconds() / 60 for j in jobs)
        capacity = ((win[1] - win[0]).total_seconds() / 60) if win else 0
        rows.append({
            "technician_id": tech.id, "name": tech.name, "status": tech.status,
            "skills": [s["skill"] for s in fs.engineer_out(db, tech, profile)["skills"]],
            "region_id": profile.region_id, "working": win is not None,
            "window_start": win[0] if win else None, "window_end": win[1] if win else None,
            "jobs": sorted(jobs, key=lambda j: j["planned_start"]), "away": away,
            "booked_minutes": round(minutes), "utilization": round(100 * minutes / capacity) if capacity else 0,
        })
    hours = []
    t = start_local.replace(minute=0)
    while t <= end_local:
        if t >= start_local:
            hours.append({"at": cal.utc(t), "label": f"{t:%H:%M}"})
        t += dt.timedelta(hours=1)
    unscheduled = [fs.activity_out(db, a) for a, _ in _schedulable(db, tenant_id, region_id)]
    return {"day": the_day, "region_id": region.id if region else None, "region_name": region.name if region else None,
            "timezone": region.timezone if region else "UTC", "start": start, "end": end, "hours": hours, "rows": rows,
            "unscheduled": unscheduled, "now": utcnow()}

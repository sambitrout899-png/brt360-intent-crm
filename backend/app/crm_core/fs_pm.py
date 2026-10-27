"""Preventive Maintenance engine (Siebel Field Service, chapter 21).

A PM plan covers assets (all assets of its products, plus named assets) and
has triggers:

  interval   {"type": "interval", "every_days": 90, "valid_start": 0, "valid_end": null}
             days in the asset's life; several rows give e.g. "every 3 days
             in the first week, then every 5 days"
  usage      {"type": "usage", "measurement": "Copies", "every": 3000, "valid_start": 0, "valid_end": 15000}
             the reading moved `every` since the last PM action; a counter
             that passed its maximum wraps:  (max - last) + (new - min) + 1
  threshold  {"type": "threshold", "measurement": "Temperature", "lower": 5, "upper": 45}
             any reading since the last PM action outside the limits
  event      {"type": "event", "requests": 3, "within_days": 30}
             that many service requests on the asset within the period

Rules: triggers never fire in the past (an overdue date becomes today), no
trigger fires twice on one day, and only interval triggers schedule ahead -
the others need no outstanding (future) action for that plan and asset.
Plans run in priority order; an optional plan skips a day another plan of
this run already serves. Each action becomes a PM service request plus an
activity from the plan's template, due on the action date.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from sqlalchemy.orm import Session

from app.crm_core import fs_service as fs
from app.models.assurance import TroubleTicket
from app.models.field_service import FsActivityTemplate, FsAssetDetail, FsMeasurement, FsPmAction, FsPmPlan, FsReading, FsRequestDetail
from app.models.fulfillment import Asset, AssetStatus
from app.timeutils import utcnow

TRIGGERS = ("interval", "usage", "threshold", "event")


def plan_out(db: Session, p: FsPmPlan) -> dict[str, Any]:
    template = db.get(FsActivityTemplate, p.template_id) if p.template_id else None
    actions = db.query(FsPmAction).filter(FsPmAction.plan_id == p.id)
    return {"id": p.id, "name": p.name, "priority": p.priority, "optional": p.optional,
            "products": [x.strip() for x in (p.products or "").split(",") if x.strip()], "asset_ids": fs._j(p.asset_ids_json, []),
            "triggers": fs._j(p.triggers_json, []), "template_id": p.template_id, "template_name": template.name if template else None,
            "active": p.active, "asset_count": len(assets_of_plan(db, p)),
            "scheduled": actions.filter(FsPmAction.status == "scheduled").count(), "done": actions.filter(FsPmAction.status == "done").count()}


def _clean_trigger(t: dict[str, Any]) -> dict[str, Any] | None:
    kind = t.get("type")
    num = lambda v: float(v) if v not in (None, "") else None  # noqa: E731
    if kind == "interval" and num(t.get("every_days")):
        return {"type": "interval", "every_days": max(1, int(float(t["every_days"]))), "valid_start": int(num(t.get("valid_start")) or 0),
                "valid_end": int(num(t["valid_end"])) if num(t.get("valid_end")) is not None else None}
    if kind == "usage" and t.get("measurement") and num(t.get("every")):
        return {"type": "usage", "measurement": str(t["measurement"]), "every": num(t["every"]), "valid_start": num(t.get("valid_start")),
                "valid_end": num(t.get("valid_end"))}
    if kind == "threshold" and t.get("measurement") and (num(t.get("lower")) is not None or num(t.get("upper")) is not None):
        return {"type": "threshold", "measurement": str(t["measurement"]), "lower": num(t.get("lower")), "upper": num(t.get("upper"))}
    if kind == "event" and num(t.get("requests")):
        return {"type": "event", "requests": max(1, int(float(t["requests"]))), "within_days": max(1, int(num(t.get("within_days")) or 30))}
    return None


def save_plan(db: Session, tenant_id: int, data: dict[str, Any], plan_id: int | None = None) -> FsPmPlan:
    p = fs._own(db, FsPmPlan, plan_id, tenant_id, "PM plan") if plan_id else FsPmPlan(tenant_id=tenant_id)
    if not plan_id:
        db.add(p)
    p.name = str(data.get("name") or p.name or "PM plan").strip()
    if data.get("priority"):
        p.priority = max(1, min(4, int(data["priority"])))
    for f in ("optional", "active"):
        if data.get(f) is not None:
            setattr(p, f, bool(data[f]))
    if data.get("products") is not None:
        p.products = ",".join(str(x).strip() for x in data["products"] if str(x).strip())
    if data.get("asset_ids") is not None:
        p.asset_ids_json = fs._dump(sorted({int(a) for a in data["asset_ids"] if a}))
    if data.get("triggers") is not None:
        triggers = [c for c in (_clean_trigger(t) for t in data["triggers"]) if c]
        if not triggers:
            raise ValueError("Give the plan at least one complete trigger")
        p.triggers_json = fs._dump(triggers)
    if "template_id" in data:
        p.template_id = fs._own(db, FsActivityTemplate, data["template_id"], tenant_id, "Template").id if data["template_id"] else None
    db.flush()
    return p


def assets_of_plan(db: Session, plan: FsPmPlan) -> list[Asset]:
    """Products first (all their assets), then the plan's named assets."""
    products = {fs._key(x) for x in (plan.products or "").split(",") if x.strip()}
    ids = set(fs._j(plan.asset_ids_json, []))
    out = []
    for a in db.query(Asset).filter(Asset.tenant_id == plan.tenant_id, Asset.status == AssetStatus.active).all():
        if a.id in ids or (products and fs._key(fs.product_of(db, a)) in products):
            out.append(a)
    return out


def _install_date(db: Session, asset: Asset) -> dt.date:
    d = db.query(FsAssetDetail).filter(FsAssetDetail.asset_id == asset.id).first()
    return (d.installed_on if d and d.installed_on else None) or (asset.created_at.date() if asset.created_at else utcnow().date())


def interval_dates(install: dt.date, triggers: list[dict[str, Any]], last_done: dt.date | None, last_due: dt.date | None,
                   until: dt.date) -> list[dt.date]:
    """The PM dates of the interval triggers up to `until`.

    From the install date the dates are whole multiples of each row's
    interval that fall inside its valid days; after a completed (or, failing
    that, a scheduled) action the engine steps on from that date with the
    interval valid at that age."""
    rows = [t for t in triggers if t.get("type") == "interval" and t.get("every_days")]
    if not rows:
        return []

    def row_at(age: int) -> dict[str, Any] | None:
        for t in rows:
            if (t.get("valid_start") or 0) <= age and (t.get("valid_end") is None or age < t["valid_end"]):
                return t
        return None

    base = last_done or last_due
    out: list[dt.date] = []
    if base is None:
        for t in rows:
            k = 1
            while True:
                offset = k * t["every_days"]
                when = install + dt.timedelta(days=offset)
                if when > until or (t.get("valid_end") is not None and offset >= t["valid_end"]):
                    break
                if offset >= (t.get("valid_start") or 0):
                    out.append(when)
                k += 1
        return sorted(set(out))
    cursor = base
    for _ in range(2000):
        t = row_at((cursor - install).days)
        if t is None:
            break
        nxt = cursor + dt.timedelta(days=t["every_days"])
        if t.get("valid_end") is not None and (nxt - install).days >= t["valid_end"]:
            # the interval falls past this row's valid end - the next row may take over from there
            t2 = row_at((nxt - install).days)
            if t2 is None:
                break
        if nxt > until:
            break
        out.append(nxt)
        cursor = nxt
    return out


def _readings_since(db: Session, measurement: FsMeasurement, since: dt.datetime | None) -> list[FsReading]:
    q = db.query(FsReading).filter(FsReading.measurement_id == measurement.id)
    if since is not None:
        q = q.filter(FsReading.read_at > since)
    return q.order_by(FsReading.read_at, FsReading.id).all()


def usage_change(last: float, new: float, minimum: float, maximum: float | None) -> float:
    if new >= last or maximum is None:
        return new - last
    return (maximum - last) + (new - minimum) + 1


def _make_action(db: Session, tenant_id: int, plan: FsPmPlan, asset: Asset, kind: str, due: dt.date,
                 reading: float | None = None) -> FsPmAction:
    action = FsPmAction(tenant_id=tenant_id, plan_id=plan.id, asset_id=asset.id, trigger_kind=kind, due_on=due, reading_value=reading)
    db.add(action)
    db.flush()
    label = fs.asset_label(db, asset)
    ticket = fs.create_request(db, tenant_id, description=f"Preventive maintenance: {plan.name} - {label} (due {due:%d %b %Y}, {kind} trigger)",
                               asset_id=asset.id, severity="low", source="pm")
    ticket.priority = {1: "P1", 2: "P2", 3: "P3", 4: "P4"}.get(plan.priority, "P3")
    detail = fs.request_detail(db, tenant_id, ticket)
    cal = fs.calendar_for(db, tenant_id, asset.id)
    win = cal.window(due) or (cal.utc(dt.datetime.combine(due, dt.time(8))), cal.utc(dt.datetime.combine(due, dt.time(17))))
    detail.committed_at = win[1]
    detail.verified_at = utcnow()
    detail.verify_note = f"Preventive maintenance plan '{plan.name}'"
    act = fs.create_activity(db, tenant_id, ticket_id=ticket.id, asset_id=asset.id, template_id=plan.template_id,
                             subject=f"PM: {plan.name}", category="preventive_maintenance", priority=plan.priority,
                             earliest_start=max(win[0], utcnow()), due_at=win[1], pm_action_id=action.id)
    action.ticket_id, action.activity_id = ticket.id, act.id
    db.flush()
    return action


def run(db: Session, tenant_id: int, until: Any = None, plan_id: int | None = None, asset_id: int | None = None,
        today: Any = None) -> dict[str, Any]:
    """Run the PM engine up to `until` (default: 30 days ahead)."""
    today = fs._date(today) or utcnow().date()
    until = fs._date(until) or today + dt.timedelta(days=30)
    if until < today:
        raise ValueError("Run the engine up to a date that is not in the past")
    q = db.query(FsPmPlan).filter(FsPmPlan.tenant_id == tenant_id, FsPmPlan.active.is_(True))
    if plan_id:
        q = q.filter(FsPmPlan.id == plan_id)
    plans = sorted(q.all(), key=lambda p: (p.optional, p.priority or 9, p.id))  # mandatory first, then by priority
    generated: list[dict[str, Any]] = []
    served: dict[tuple[int, dt.date], int] = {}  # (asset, day) -> priority already generated in this run
    for plan in plans:
        triggers = fs._j(plan.triggers_json, [])
        for asset in assets_of_plan(db, plan):
            if asset_id and asset.id != asset_id:
                continue
            existing = db.query(FsPmAction).filter(FsPmAction.plan_id == plan.id, FsPmAction.asset_id == asset.id).all()
            taken_days = {a.due_on for a in existing}
            outstanding = any(a.status == "scheduled" and a.due_on >= today for a in existing)
            candidates: list[tuple[str, dt.date, float | None]] = []

            # interval triggers - may schedule ahead
            # step on from the latest action: its completion date if done, else its scheduled date
            live = [a for a in existing if a.status != "cancelled"]
            latest = max(live, key=lambda a: (a.due_on, a.id)) if live else None
            base_done = latest.completed_on if latest is not None and latest.status == "done" else None
            base_due = latest.due_on if latest is not None and latest.status != "done" else None
            for d in interval_dates(_install_date(db, asset), triggers, base_done, base_due, until):
                candidates.append(("interval", max(d, today), None))

            last_action = max(existing, key=lambda a: a.created_at) if existing else None
            if not outstanding:
                for t in triggers:
                    kind = t.get("type")
                    if kind in ("usage", "threshold"):
                        m = db.query(FsMeasurement).filter(FsMeasurement.asset_id == asset.id, FsMeasurement.name.ilike(t["measurement"])).first()
                        if m is None:
                            continue
                        if kind == "usage":
                            readings = _readings_since(db, m, None)
                            if not readings:
                                continue
                            new = readings[-1].value
                            base_actions = [a for a in existing if a.trigger_kind == "usage" and a.reading_value is not None]
                            last = max(base_actions, key=lambda a: a.created_at).reading_value if base_actions else (readings[0].value if len(readings) > 1 else m.min_value)
                            vs, ve = t.get("valid_start"), t.get("valid_end")
                            if (vs is not None and new < vs) or (ve is not None and new >= ve):
                                continue
                            if usage_change(last, new, m.min_value, m.max_value) >= float(t["every"]):
                                candidates.append(("usage", today, new))
                        else:
                            since = last_action.created_at if last_action else None
                            for r in _readings_since(db, m, since):
                                if (t.get("lower") is not None and r.value < t["lower"]) or (t.get("upper") is not None and r.value > t["upper"]):
                                    candidates.append(("threshold", today, r.value))
                                    break
                    elif kind == "event":
                        since = utcnow() - dt.timedelta(days=int(t.get("within_days") or 30))
                        if last_action is not None:
                            since = max(since, last_action.created_at)
                        count = (
                            db.query(FsRequestDetail)
                            .join(TroubleTicket, TroubleTicket.id == FsRequestDetail.ticket_id)
                            .filter(FsRequestDetail.asset_id == asset.id, FsRequestDetail.source != "pm", FsRequestDetail.opened_at >= since)
                            .count()
                        )
                        if count >= int(t["requests"]):
                            candidates.append(("event", today, float(count)))

            for kind, day, reading in sorted(candidates, key=lambda c: c[1]):
                if day in taken_days or day > until:
                    continue  # no trigger twice a day
                if plan.optional and (asset.id, day) in served and served[(asset.id, day)] <= (plan.priority or 9):
                    continue  # a higher-priority plan already serves this asset that day
                action = _make_action(db, tenant_id, plan, asset, kind, day, reading)
                taken_days.add(day)
                served[(asset.id, day)] = min(served.get((asset.id, day), 9), plan.priority or 9)
                generated.append({"action_id": action.id, "plan": plan.name, "asset_id": asset.id, "asset": fs.asset_label(db, asset),
                                  "trigger": kind, "due_on": day, "ticket_id": action.ticket_id, "activity_id": action.activity_id})
    return {"until": until, "generated": generated}


def actions(db: Session, tenant_id: int, status: str | None = None, limit: int = 200) -> list[dict[str, Any]]:
    q = db.query(FsPmAction).filter(FsPmAction.tenant_id == tenant_id)
    if status:
        q = q.filter(FsPmAction.status == status)
    out = []
    for a in q.order_by(FsPmAction.due_on.desc(), FsPmAction.id.desc()).limit(limit).all():
        plan = db.get(FsPmPlan, a.plan_id)
        asset = db.get(Asset, a.asset_id)
        out.append({"id": a.id, "plan_id": a.plan_id, "plan_name": plan.name if plan else "?", "asset_id": a.asset_id,
                    "asset_label": fs.asset_label(db, asset), "trigger_kind": a.trigger_kind, "due_on": a.due_on, "status": a.status,
                    "completed_on": a.completed_on, "ticket_id": a.ticket_id, "activity_id": a.activity_id, "reading_value": a.reading_value})
    return out

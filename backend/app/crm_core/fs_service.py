"""Field Service - service requests, activities, field execution and charges.

The process (Siebel Field Service, chapter 3/4):

  1 receive the call -> service request (a trouble ticket, INT-10 or here)
  2 verify: account, asset, entitlement (-> committed date), warranty
  3 try known fixes (INT-11 diagnosis)
  4 create activities from activity templates (steps, items, skills)
  5 order / locate parts (fs_inventory)
  6 assign and schedule an engineer (fs_schedule, dispatch board)
  7 the engineer works the job: en route -> on site -> complete, recording
    time, parts (from the trunk), expenses, readings and asset swaps
  8 charges are priced from that record, less warranty / entitlement cover,
    and invoiced to the customer's billing account
  9 close the service request

Existing rows keep their meaning: a work order made by INT-12 or by order
fulfillment shows up here with default field details (ensure()), and a
fulfillment work order still closes through its own intent.
"""

from __future__ import annotations

import datetime as dt
import json
from typing import Any

from sqlalchemy.orm import Session

from app.crm_core import fs_inventory
from app.crm_core.fs_calendar import Calendar
from app.models.accounts import ServiceAccount
from app.models.assurance import CompletionEvidence, Technician, TechnicianAssignment, TroubleTicket, WorkOrder
from app.models.crm import Party
from app.models.field_service import (
    FsActivity,
    FsActivityTemplate,
    FsAssetDetail,
    FsAssetWarranty,
    FsCharge,
    FsEngineer,
    FsEntitlement,
    FsExpense,
    FsInventoryLocation,
    FsMeasurement,
    FsPartMovement,
    FsPmAction,
    FsReading,
    FsRepair,
    FsRequestDetail,
    FsServiceRegion,
    FsTimeEntry,
    FsWarranty,
)
from app.models.fulfillment import Asset, AssetStatus
from app.models.orders import BillingAccount
from app.timeutils import utcnow

PRIORITIES = {1: "ASAP", 2: "High", 3: "Medium", 4: "Low"}
TICKET_PRIORITY = {"P1": 1, "P2": 2, "P3": 3, "P4": 4}
DEFAULT_RESPONSE_HOURS = {1: 4.0, 2: 8.0, 3: 24.0, 4: 72.0}  # no entitlement: best effort, billable
CATEGORIES = ("installation", "repair", "preventive_maintenance", "inspection", "other")
OPEN = ("unscheduled", "scheduled", "en_route", "on_site")
DONE = ("complete", "cancelled")
FULFILLMENT_KINDS = {
    "installation": "Close Work Order 1 (Installation). work_order_id: {id}, technical_parameters: ",
    "connectivity_test": "Close Work Order 2 (Connectivity Test & Acceptance). work_order_id: {id}, signature: ",
    "acceptance_letter": "Close Work Order 3 (Acceptance Letter). work_order_id: {id}",
}


def _j(text: str | None, default: Any) -> Any:
    try:
        value = json.loads(text or "")
        return value if isinstance(value, type(default)) else default
    except (ValueError, TypeError):
        return default


def _dump(value: Any) -> str:
    return json.dumps(value, default=str)


def _own(db: Session, model, row_id: int | None, tenant_id: int, label: str):
    row = db.get(model, row_id) if row_id else None
    if row is None or getattr(row, "tenant_id", tenant_id) != tenant_id:
        raise ValueError(f"{label} {row_id} not found")
    return row


def _key(text: str | None) -> str:
    return (text or "").strip().lower()


def _date(value: Any) -> dt.date | None:
    if value in (None, ""):
        return None
    if isinstance(value, dt.datetime):
        return value.date()
    if isinstance(value, dt.date):
        return value
    return dt.date.fromisoformat(str(value)[:10])


def _when(value: Any) -> dt.datetime | None:
    if value in (None, ""):
        return None
    if isinstance(value, dt.datetime):
        return value.replace(tzinfo=None)
    return dt.datetime.fromisoformat(str(value).replace("Z", "")).replace(tzinfo=None)


# ------------------------------------------------------------------ assets

def asset_detail(db: Session, tenant_id: int, asset: Asset, create: bool = False) -> FsAssetDetail | None:
    row = db.query(FsAssetDetail).filter(FsAssetDetail.asset_id == asset.id).first()
    if row is None and create:
        row = FsAssetDetail(tenant_id=tenant_id, asset_id=asset.id, party_id=party_of_asset(db, asset),
                            product_name=asset.asset_type, installed_on=asset.created_at.date() if asset.created_at else None)
        db.add(row)
        db.flush()
    return row


def party_of_asset(db: Session, asset: Asset) -> int | None:
    sa = db.get(ServiceAccount, asset.service_account_id)
    if sa and sa.party_id:
        return sa.party_id
    detail = db.query(FsAssetDetail).filter(FsAssetDetail.asset_id == asset.id).first()
    if detail and detail.party_id:
        return detail.party_id
    ba = db.get(BillingAccount, sa.billing_account_id) if sa else None
    return ba.party_id if ba else None


def product_of(db: Session, asset: Asset | None) -> str:
    if asset is None:
        return ""
    detail = db.query(FsAssetDetail).filter(FsAssetDetail.asset_id == asset.id).first()
    return (detail.product_name if detail and detail.product_name else asset.asset_type) or ""


def asset_label(db: Session, asset: Asset | None) -> str:
    if asset is None:
        return ""
    detail = db.query(FsAssetDetail).filter(FsAssetDetail.asset_id == asset.id).first()
    serial = detail.serial_number if detail and detail.serial_number else asset.identifier
    return f"{product_of(db, asset)} · {serial}" if serial else product_of(db, asset)


def assets_of_party(db: Session, tenant_id: int, party_id: int) -> list[Asset]:
    from app.crm_core import abo_service
    return sorted(abo_service.customer_assets(db, tenant_id, party_id), key=lambda a: a.id)


def register_asset(db: Session, tenant_id: int, *, party_id: int, product_name: str, serial_number: str = "",
                   installed_on: Any = None, service_account_id: int | None = None, parent_asset_id: int | None = None,
                   address: str = "", region_id: int | None = None, auto_warranty: bool = True) -> Asset:
    _own(db, Party, party_id, tenant_id, "Customer")
    if service_account_id:
        sa = _own(db, ServiceAccount, service_account_id, tenant_id, "Service account")
    else:
        ba = db.query(BillingAccount).filter(BillingAccount.party_id == party_id).first()
        sa = db.query(ServiceAccount).filter(ServiceAccount.billing_account_id == ba.id).order_by(ServiceAccount.id).first() if ba else None
        if sa is None:
            raise ValueError("This customer has no service account yet - assets are registered under one (it comes with the first order).")
    asset = Asset(tenant_id=tenant_id, service_account_id=sa.id, asset_type=product_name.strip() or "equipment",
                  identifier=serial_number or product_name, status=AssetStatus.active)
    db.add(asset)
    db.flush()
    detail = FsAssetDetail(tenant_id=tenant_id, asset_id=asset.id, party_id=party_id, product_name=product_name.strip(),
                           serial_number=serial_number, installed_on=_date(installed_on) or utcnow().date(),
                           parent_asset_id=parent_asset_id, address=address, region_id=region_id)
    db.add(detail)
    db.flush()
    if auto_warranty:
        apply_warranties(db, tenant_id, asset.id)
    return asset


def update_asset(db: Session, tenant_id: int, asset_id: int, data: dict[str, Any]) -> Asset:
    asset = _own(db, Asset, asset_id, tenant_id, "Asset")
    detail = asset_detail(db, tenant_id, asset, create=True)
    for field in ("product_name", "serial_number", "address", "notes"):
        if field in data and data[field] is not None:
            setattr(detail, field, str(data[field]))
    if "installed_on" in data:
        detail.installed_on = _date(data["installed_on"])
    for field in ("parent_asset_id", "region_id"):
        if field in data:
            setattr(detail, field, data[field] or None)
    db.flush()
    return asset


def apply_warranties(db: Session, tenant_id: int, asset_id: int) -> list[FsAssetWarranty]:
    """Get Warranties: attach every warranty that covers the asset's product, from its install date."""
    asset = _own(db, Asset, asset_id, tenant_id, "Asset")
    detail = asset_detail(db, tenant_id, asset, create=True)
    product = _key(detail.product_name or asset.asset_type)
    start = detail.installed_on or utcnow().date()
    have = {w.warranty_id for w in db.query(FsAssetWarranty).filter(FsAssetWarranty.asset_id == asset.id).all()}
    out = []
    for w in db.query(FsWarranty).filter(FsWarranty.tenant_id == tenant_id).all():
        products = [_key(p) for p in (w.products or "").split(",") if p.strip()]
        if w.id in have or (products and product not in products):
            continue
        row = FsAssetWarranty(tenant_id=tenant_id, asset_id=asset.id, warranty_id=w.id, starts_on=start,
                              ends_on=start + dt.timedelta(days=w.duration_days))
        db.add(row)
        out.append(row)
    db.flush()
    return out


def latest_reading(db: Session, measurement_id: int) -> FsReading | None:
    return db.query(FsReading).filter(FsReading.measurement_id == measurement_id).order_by(FsReading.read_at.desc(), FsReading.id.desc()).first()


def warranty_check(db: Session, tenant_id: int, asset_id: int | None, on: dt.date | None = None) -> tuple[FsAssetWarranty, FsWarranty] | None:
    """Check Warranty: the asset's own warranty, else a component warranty of its parent/root asset."""
    on = on or utcnow().date()
    seen: set[int] = set()
    current = asset_id
    while current and current not in seen:
        seen.add(current)
        for aw in db.query(FsAssetWarranty).filter(FsAssetWarranty.tenant_id == tenant_id, FsAssetWarranty.asset_id == current).all():
            w = db.get(FsWarranty, aw.warranty_id)
            if w is None or not (aw.starts_on <= on <= aw.ends_on):
                continue
            if w.measurement and w.usage_limit is not None:
                m = db.query(FsMeasurement).filter(FsMeasurement.asset_id == current, FsMeasurement.name.ilike(w.measurement)).first()
                r = latest_reading(db, m.id) if m else None
                if r is not None and r.value > w.usage_limit:
                    continue  # concurrent warranty: whichever runs out first
            return aw, w
        detail = db.query(FsAssetDetail).filter(FsAssetDetail.asset_id == current).first()
        current = detail.parent_asset_id if detail else None
    return None


def asset_out(db: Session, tenant_id: int, asset: Asset, full: bool = False) -> dict[str, Any]:
    detail = db.query(FsAssetDetail).filter(FsAssetDetail.asset_id == asset.id).first()
    party_id = party_of_asset(db, asset)
    party = db.get(Party, party_id) if party_id else None
    cover = warranty_check(db, tenant_id, asset.id)
    out = {
        "id": asset.id, "brt_id": getattr(asset, "brt_id", None), "status": asset.status.value if hasattr(asset.status, "value") else asset.status,
        "asset_type": asset.asset_type, "identifier": asset.identifier, "party_id": party_id, "party_name": party.name if party else None,
        "product_name": product_of(db, asset), "serial_number": detail.serial_number if detail else "",
        "installed_on": detail.installed_on if detail else None, "parent_asset_id": detail.parent_asset_id if detail else None,
        "address": detail.address if detail else "", "region_id": detail.region_id if detail else None,
        "label": asset_label(db, asset),
        "warranty": {"name": cover[1].name, "ends_on": cover[0].ends_on, "days_left": (cover[0].ends_on - utcnow().date()).days,
                     "covers_parts": cover[1].covers_parts, "covers_labor": cover[1].covers_labor} if cover else None,
    }
    if full:
        out["warranties"] = [
            {"id": aw.id, "warranty_id": aw.warranty_id, "name": (db.get(FsWarranty, aw.warranty_id) or FsWarranty(name="?")).name,
             "starts_on": aw.starts_on, "ends_on": aw.ends_on, "active": aw.starts_on <= utcnow().date() <= aw.ends_on}
            for aw in db.query(FsAssetWarranty).filter(FsAssetWarranty.asset_id == asset.id).order_by(FsAssetWarranty.ends_on.desc()).all()
        ]
        out["measurements"] = [measurement_out(db, m) for m in db.query(FsMeasurement).filter(FsMeasurement.asset_id == asset.id).all()]
        out["components"] = [
            {"id": d.asset_id, "label": asset_label(db, db.get(Asset, d.asset_id))}
            for d in db.query(FsAssetDetail).filter(FsAssetDetail.parent_asset_id == asset.id).all()
        ]
        tickets = [d.ticket_id for d in db.query(FsRequestDetail).filter(FsRequestDetail.asset_id == asset.id).all()]
        out["service_requests"] = [
            {"id": t.id, "description": t.description, "status": t.status, "created_at": t.created_at}
            for t in db.query(TroubleTicket).filter(TroubleTicket.id.in_(tickets or [-1])).order_by(TroubleTicket.id.desc()).all()
        ]
        out["pm_actions"] = [
            {"id": a.id, "plan_id": a.plan_id, "due_on": a.due_on, "status": a.status, "trigger_kind": a.trigger_kind}
            for a in db.query(FsPmAction).filter(FsPmAction.asset_id == asset.id).order_by(FsPmAction.due_on.desc()).limit(10).all()
        ]
        out["repairs"] = [
            {"id": r.id, "repair_number": r.repair_number, "status": r.status}
            for r in db.query(FsRepair).filter(FsRepair.asset_id == asset.id).all()
        ]
    return out


def assets(db: Session, tenant_id: int, party_id: int | None = None, query: str = "") -> list[dict[str, Any]]:
    rows = assets_of_party(db, tenant_id, party_id) if party_id else db.query(Asset).filter(Asset.tenant_id == tenant_id).order_by(Asset.id.desc()).limit(500).all()
    out = [asset_out(db, tenant_id, a) for a in rows]
    if query.strip():
        q = _key(query)
        out = [a for a in out if q in _key(a["label"]) or q in _key(a["party_name"]) or q in _key(a["identifier"])]
    return out


def measurement_out(db: Session, m: FsMeasurement) -> dict[str, Any]:
    readings = db.query(FsReading).filter(FsReading.measurement_id == m.id).order_by(FsReading.read_at.desc(), FsReading.id.desc()).limit(12).all()
    last = readings[0] if readings else None
    alarm = bool(last and ((m.lower_limit is not None and last.value < m.lower_limit) or (m.upper_limit is not None and last.value > m.upper_limit)))
    return {"id": m.id, "asset_id": m.asset_id, "name": m.name, "unit": m.unit, "kind": m.kind, "min_value": m.min_value,
            "max_value": m.max_value, "lower_limit": m.lower_limit, "upper_limit": m.upper_limit,
            "last_value": last.value if last else None, "last_read_at": last.read_at if last else None, "alarm": alarm,
            "readings": [{"id": r.id, "value": r.value, "read_at": r.read_at, "source": r.source} for r in readings]}


def add_measurement(db: Session, tenant_id: int, asset_id: int, data: dict[str, Any]) -> FsMeasurement:
    _own(db, Asset, asset_id, tenant_id, "Asset")
    m = FsMeasurement(tenant_id=tenant_id, asset_id=asset_id, name=str(data.get("name") or "Counter").strip(), unit=data.get("unit") or "",
                      kind=data.get("kind") if data.get("kind") in ("counter", "gauge") else "counter",
                      min_value=float(data.get("min_value") or 0), max_value=data.get("max_value"),
                      lower_limit=data.get("lower_limit"), upper_limit=data.get("upper_limit"))
    db.add(m)
    db.flush()
    return m


def record_reading(db: Session, tenant_id: int, measurement_id: int, value: float, activity_id: int | None = None,
                   read_at: Any = None, source: str = "manual") -> FsReading:
    _own(db, FsMeasurement, measurement_id, tenant_id, "Measurement")
    r = FsReading(tenant_id=tenant_id, measurement_id=measurement_id, value=float(value), activity_id=activity_id,
                  read_at=_when(read_at) or utcnow(), source=source)
    db.add(r)
    db.flush()
    return r


# ------------------------------------------------------- regions/engineers

def regions(db: Session, tenant_id: int) -> list[FsServiceRegion]:
    return db.query(FsServiceRegion).filter(FsServiceRegion.tenant_id == tenant_id).order_by(FsServiceRegion.id).all()


def region_out(r: FsServiceRegion) -> dict[str, Any]:
    return {"id": r.id, "name": r.name, "timezone": r.timezone, "day_start": r.day_start, "day_end": r.day_end,
            "work_days": [int(d) for d in (r.work_days or "").split(",") if d.strip().isdigit()],
            "holidays": _j(r.holidays_json, []), "labor_rate": r.labor_rate, "travel_minutes": r.travel_minutes}


def save_region(db: Session, tenant_id: int, data: dict[str, Any], region_id: int | None = None) -> FsServiceRegion:
    r = _own(db, FsServiceRegion, region_id, tenant_id, "Region") if region_id else FsServiceRegion(tenant_id=tenant_id)
    if not region_id:
        db.add(r)
    r.name = str(data.get("name") or r.name or "Region").strip()
    for f in ("timezone", "day_start", "day_end"):
        if data.get(f):
            setattr(r, f, str(data[f]))
    if data.get("work_days") is not None:
        r.work_days = ",".join(str(int(d)) for d in data["work_days"] if 0 <= int(d) <= 6) or "0,1,2,3,4"
    if data.get("holidays") is not None:
        r.holidays_json = _dump(sorted({str(_date(h)) for h in data["holidays"] if h}))
    if data.get("labor_rate") is not None:
        r.labor_rate = float(data["labor_rate"])
    if data.get("travel_minutes") is not None:
        r.travel_minutes = max(0, int(data["travel_minutes"]))
    db.flush()
    return r


def region_by_name(db: Session, tenant_id: int, name: str | None) -> FsServiceRegion | None:
    if not name:
        return None
    return db.query(FsServiceRegion).filter(FsServiceRegion.tenant_id == tenant_id, FsServiceRegion.name.ilike(name.strip())).first()


def engineer_profile(db: Session, tenant_id: int, tech: Technician, create: bool = True) -> FsEngineer | None:
    row = db.query(FsEngineer).filter(FsEngineer.technician_id == tech.id).first()
    if row is None and create:
        region = region_by_name(db, tenant_id, tech.region)
        skills = [{"skill": s.strip(), "level": 2} for s in (tech.skills or "").split(",") if s.strip()]
        trunk = fs_inventory.trunk_of(db, tenant_id, tech.id)
        row = FsEngineer(tenant_id=tenant_id, technician_id=tech.id, region_id=region.id if region else None,
                         skills_json=_dump(skills), trunk_location_id=trunk.id if trunk else None)
        db.add(row)
        db.flush()
    return row


def skills_of(profile: FsEngineer | None, tech: Technician) -> dict[str, int]:
    if profile is not None:
        out = {}
        for s in _j(profile.skills_json, []):
            if isinstance(s, dict) and s.get("skill"):
                out[_key(s["skill"])] = int(s.get("level") or 2)
        if out:
            return out
    return {_key(s): 2 for s in (tech.skills or "").split(",") if s.strip()}


def engineers(db: Session, tenant_id: int, region_id: int | None = None) -> list[tuple[Technician, FsEngineer]]:
    out = []
    for tech in db.query(Technician).filter(Technician.tenant_id == tenant_id).order_by(Technician.name).all():
        profile = engineer_profile(db, tenant_id, tech)
        if region_id and profile.region_id not in (region_id, None) and _key(tech.region) != "all":
            continue
        out.append((tech, profile))
    return out


def engineer_out(db: Session, tech: Technician, profile: FsEngineer) -> dict[str, Any]:
    region = db.get(FsServiceRegion, profile.region_id) if profile.region_id else None
    trunk = db.get(FsInventoryLocation, profile.trunk_location_id) if profile.trunk_location_id else fs_inventory.trunk_of(db, tech.tenant_id, tech.id)
    return {"technician_id": tech.id, "name": tech.name, "status": tech.status, "legacy_region": tech.region,
            "region_id": profile.region_id, "region_name": region.name if region else None,
            "skills": [{"skill": k, "level": v} for k, v in skills_of(profile, tech).items()],
            "day_start": profile.day_start or (region.day_start if region else "08:00"),
            "day_end": profile.day_end or (region.day_end if region else "17:00"),
            "work_days": [int(d) for d in (profile.work_days or (region.work_days if region else "0,1,2,3,4")).split(",") if d.strip().isdigit()],
            "own_hours": bool(profile.day_start or profile.day_end or profile.work_days),
            "cost_per_hour": profile.cost_per_hour, "max_jobs_per_day": profile.max_jobs_per_day, "phone": profile.phone,
            "trunk_location_id": trunk.id if trunk else None, "trunk_name": trunk.name if trunk else None}


def save_engineer(db: Session, tenant_id: int, technician_id: int | None, data: dict[str, Any]) -> Technician:
    if technician_id:
        tech = _own(db, Technician, technician_id, tenant_id, "Engineer")
    else:
        name = str(data.get("name") or "").strip()
        if not name:
            raise ValueError("The engineer needs a name")
        tech = Technician(tenant_id=tenant_id, name=name, skills="", region="ALL", status="available")
        db.add(tech)
        db.flush()
    profile = engineer_profile(db, tenant_id, tech)
    if data.get("name"):
        tech.name = str(data["name"]).strip()
    if data.get("status") in ("available", "busy"):
        tech.status = data["status"]
    if data.get("skills") is not None:
        skills = [{"skill": _key(s.get("skill")), "level": max(1, min(3, int(s.get("level") or 2)))} for s in data["skills"] if s.get("skill")]
        profile.skills_json = _dump(skills)
        tech.skills = ",".join(s["skill"] for s in skills)[:300]  # INT-12 matching keeps working
    if "region_id" in data:
        profile.region_id = data["region_id"] or None
        region = db.get(FsServiceRegion, profile.region_id) if profile.region_id else None
        tech.region = region.name if region else "ALL"
    for f in ("day_start", "day_end"):
        if f in data:
            setattr(profile, f, data[f] or None)
    if "work_days" in data:
        profile.work_days = ",".join(str(int(d)) for d in data["work_days"]) if data["work_days"] else None
    for f, cast in (("cost_per_hour", float), ("max_jobs_per_day", int)):
        if data.get(f) is not None:
            setattr(profile, f, cast(data[f]))
    if data.get("phone") is not None:
        profile.phone = str(data["phone"])
    if data.get("create_trunk") and not fs_inventory.trunk_of(db, tenant_id, tech.id):
        parent = db.query(FsInventoryLocation).filter(FsInventoryLocation.tenant_id == tenant_id, FsInventoryLocation.kind == "warehouse").first()
        trunk = FsInventoryLocation(tenant_id=tenant_id, name=f"{tech.name} - trunk", kind="trunk", technician_id=tech.id,
                                    parent_id=parent.id if parent else None, region_id=profile.region_id)
        db.add(trunk)
        db.flush()
        profile.trunk_location_id = trunk.id
    db.flush()
    return tech


# ------------------------------------------------------------- templates

def template_out(t: FsActivityTemplate) -> dict[str, Any]:
    return {"id": t.id, "name": t.name, "category": t.category, "description": t.description, "duration_minutes": t.duration_minutes,
            "priority": t.priority, "skills": _j(t.skills_json, []), "steps": _j(t.steps_json, []), "items": _j(t.items_json, []),
            "instructions": t.instructions, "billable": t.billable, "active": t.active}


def save_template(db: Session, tenant_id: int, data: dict[str, Any], template_id: int | None = None) -> FsActivityTemplate:
    t = _own(db, FsActivityTemplate, template_id, tenant_id, "Template") if template_id else FsActivityTemplate(tenant_id=tenant_id)
    if not template_id:
        db.add(t)
    t.name = str(data.get("name") or t.name or "Activity").strip()
    if data.get("category") in CATEGORIES:
        t.category = data["category"]
    for f in ("description", "instructions"):
        if data.get(f) is not None:
            setattr(t, f, str(data[f]))
    if data.get("duration_minutes"):
        t.duration_minutes = max(15, int(data["duration_minutes"]))
    if data.get("priority"):
        t.priority = max(1, min(4, int(data["priority"])))
    if data.get("skills") is not None:
        t.skills_json = _dump([_key(s) for s in data["skills"] if str(s).strip()])
    if data.get("steps") is not None:
        t.steps_json = _dump([str(s).strip() for s in data["steps"] if str(s).strip()])
    if data.get("items") is not None:
        t.items_json = _dump([{"product": str(i.get("product")).strip(), "quantity": float(i.get("quantity") or 1), "tool": bool(i.get("tool"))}
                              for i in data["items"] if i.get("product")])
    for f in ("billable", "active"):
        if data.get(f) is not None:
            setattr(t, f, bool(data[f]))
    db.flush()
    return t


# ----------------------------------------------------- entitlements/warranty

def entitlement_out(db: Session, e: FsEntitlement) -> dict[str, Any]:
    party = db.get(Party, e.party_id)
    asset = db.get(Asset, e.asset_id) if e.asset_id else None
    today = utcnow().date()
    return {"id": e.id, "party_id": e.party_id, "party_name": party.name if party else None, "name": e.name, "asset_id": e.asset_id,
            "asset_label": asset_label(db, asset) if asset else None, "product": e.product, "coverage": e.coverage,
            "response_hours": e.response_hours, "resolution_hours": e.resolution_hours, "labor_discount_pct": e.labor_discount_pct,
            "parts_discount_pct": e.parts_discount_pct, "expense_discount_pct": e.expense_discount_pct, "visits_allowed": e.visits_allowed,
            "visits_used": e.visits_used, "starts_on": e.starts_on, "ends_on": e.ends_on, "active": e.active,
            "current": e.active and e.starts_on <= today and (e.ends_on is None or today <= e.ends_on)}


def save_entitlement(db: Session, tenant_id: int, data: dict[str, Any], entitlement_id: int | None = None) -> FsEntitlement:
    e = _own(db, FsEntitlement, entitlement_id, tenant_id, "Entitlement") if entitlement_id else None
    if e is None:
        party = _own(db, Party, data.get("party_id"), tenant_id, "Customer")
        e = FsEntitlement(tenant_id=tenant_id, party_id=party.id, name="", starts_on=utcnow().date())
        db.add(e)
    e.name = str(data.get("name") or e.name or "Service entitlement").strip()
    if "asset_id" in data:
        e.asset_id = data["asset_id"] or None
    for f in ("product", "coverage"):
        if data.get(f) is not None:
            setattr(e, f, str(data[f]))
    for f in ("response_hours", "labor_discount_pct", "parts_discount_pct", "expense_discount_pct"):
        if data.get(f) is not None:
            setattr(e, f, float(data[f]))
    if "resolution_hours" in data:
        e.resolution_hours = float(data["resolution_hours"]) if data["resolution_hours"] not in (None, "") else None
    if "visits_allowed" in data:
        e.visits_allowed = int(data["visits_allowed"]) if data["visits_allowed"] not in (None, "") else None
    if data.get("starts_on"):
        e.starts_on = _date(data["starts_on"])
    if "ends_on" in data:
        e.ends_on = _date(data["ends_on"])
    if data.get("active") is not None:
        e.active = bool(data["active"])
    db.flush()
    return e


def warranty_out(w: FsWarranty) -> dict[str, Any]:
    return {"id": w.id, "name": w.name, "products": [p.strip() for p in (w.products or "").split(",") if p.strip()],
            "duration_days": w.duration_days, "covers_parts": w.covers_parts, "covers_labor": w.covers_labor,
            "covers_expenses": w.covers_expenses, "measurement": w.measurement, "usage_limit": w.usage_limit,
            "transferable": w.transferable, "provider": w.provider}


def save_warranty(db: Session, tenant_id: int, data: dict[str, Any], warranty_id: int | None = None) -> FsWarranty:
    w = _own(db, FsWarranty, warranty_id, tenant_id, "Warranty") if warranty_id else FsWarranty(tenant_id=tenant_id)
    if not warranty_id:
        db.add(w)
    w.name = str(data.get("name") or w.name or "Warranty").strip()
    if data.get("products") is not None:
        w.products = ",".join(str(p).strip() for p in data["products"] if str(p).strip())
    if data.get("duration_days"):
        w.duration_days = max(1, int(data["duration_days"]))
    for f in ("covers_parts", "covers_labor", "covers_expenses", "transferable"):
        if data.get(f) is not None:
            setattr(w, f, bool(data[f]))
    for f in ("measurement", "provider"):
        if data.get(f) is not None:
            setattr(w, f, str(data[f]))
    if "usage_limit" in data:
        w.usage_limit = float(data["usage_limit"]) if data["usage_limit"] not in (None, "") else None
    db.flush()
    return w


# ------------------------------------------------------- service requests

def request_detail(db: Session, tenant_id: int, ticket: TroubleTicket, create: bool = True) -> FsRequestDetail | None:
    row = db.query(FsRequestDetail).filter(FsRequestDetail.ticket_id == ticket.id).first()
    if row is None and create:
        row = FsRequestDetail(tenant_id=tenant_id, ticket_id=ticket.id, opened_at=ticket.created_at or utcnow())
        db.add(row)
        db.flush()
    return row


def calendar_for(db: Session, tenant_id: int, asset_id: int | None, always: bool = False) -> Calendar:
    region = None
    if asset_id:
        detail = db.query(FsAssetDetail).filter(FsAssetDetail.asset_id == asset_id).first()
        region = db.get(FsServiceRegion, detail.region_id) if detail and detail.region_id else None
    if region is None:
        region = db.query(FsServiceRegion).filter(FsServiceRegion.tenant_id == tenant_id).order_by(FsServiceRegion.id).first()
    return Calendar.of(region, always=always)


def entitlement_candidates(db: Session, tenant_id: int, party_id: int | None, asset_id: int | None, product: str = "",
                           on: dt.date | None = None) -> list[FsEntitlement]:
    """Pick Entitlement: the customer's current entitlements matching the asset/product, best response first."""
    if not party_id:
        return []
    on = on or utcnow().date()
    asset = db.get(Asset, asset_id) if asset_id else None
    asset_product = _key(product_of(db, asset)) if asset else ""
    out = []
    for e in db.query(FsEntitlement).filter(FsEntitlement.tenant_id == tenant_id, FsEntitlement.party_id == party_id, FsEntitlement.active.is_(True)).all():
        if e.starts_on > on or (e.ends_on is not None and on > e.ends_on):
            continue
        if e.visits_allowed is not None and e.visits_used >= e.visits_allowed:
            continue
        if e.asset_id and e.asset_id != asset_id:
            continue
        if e.product and _key(e.product) not in (asset_product, _key(product)) and not (asset_product and _key(e.product) in asset_product):
            continue
        out.append(e)
    return sorted(out, key=lambda e: (e.response_hours, -(e.labor_discount_pct + e.parts_discount_pct), e.id))


def verify(db: Session, tenant_id: int, ticket_id: int, asset_id: int | None = None, entitlement_id: int | None = None) -> dict[str, Any]:
    """Verify a service request: entitlement -> committed date, and warranty cover."""
    ticket = _own(db, TroubleTicket, ticket_id, tenant_id, "Service request")
    detail = request_detail(db, tenant_id, ticket)
    if asset_id is not None:
        if asset_id:
            asset = _own(db, Asset, asset_id, tenant_id, "Asset")
            owner = party_of_asset(db, asset)
            if ticket.party_id and owner and owner != ticket.party_id:
                raise ValueError("That asset belongs to another customer")
            if not ticket.party_id and owner:
                ticket.party_id = owner
        detail.asset_id = asset_id or None
    candidates = entitlement_candidates(db, tenant_id, ticket.party_id, detail.asset_id, ticket.product_line)
    chosen = None
    if entitlement_id:
        chosen = next((e for e in candidates if e.id == entitlement_id), None)
        if chosen is None:
            raise ValueError("That entitlement does not cover this service request")
    elif candidates:
        chosen = candidates[0]
    cover = warranty_check(db, tenant_id, detail.asset_id)
    priority = TICKET_PRIORITY.get(ticket.priority, 3)
    notes = []
    if chosen is not None:
        cal = calendar_for(db, tenant_id, detail.asset_id, always=chosen.coverage == "24x7")
        detail.committed_at = cal.add_hours(detail.opened_at, chosen.response_hours)
        detail.entitlement_id = chosen.id
        notes.append(f"Entitled: {chosen.name} ({chosen.response_hours:g}h response, {'24x7' if chosen.coverage == '24x7' else 'business hours'})")
    else:
        cal = calendar_for(db, tenant_id, detail.asset_id)
        detail.committed_at = cal.add_hours(detail.opened_at, DEFAULT_RESPONSE_HOURS.get(priority, 24.0))
        detail.entitlement_id = None
        notes.append("Not entitled - best-effort response, the work is billable")
    if cover is not None:
        detail.asset_warranty_id = cover[0].id
        covered = [k for k, v in (("parts", cover[1].covers_parts), ("labor", cover[1].covers_labor), ("expenses", cover[1].covers_expenses)) if v]
        notes.append(f"Under warranty: {cover[1].name} until {cover[0].ends_on} (covers {', '.join(covered) or 'nothing billable'})")
    else:
        detail.asset_warranty_id = None
        if detail.asset_id:
            notes.append("No warranty cover on this asset")
    detail.verified_at = utcnow()
    detail.verify_note = " · ".join(notes)
    for act in db.query(FsActivity).filter(FsActivity.ticket_id == ticket.id).all():
        wo = db.get(WorkOrder, act.work_order_id)
        if wo and wo.status not in DONE:
            act.due_at = detail.committed_at  # activities inherit Date Committed
            if detail.asset_id and not act.asset_id:
                act.asset_id = detail.asset_id
    db.flush()
    return {"ticket_id": ticket.id, "committed_at": detail.committed_at, "entitlement": entitlement_out(db, chosen) if chosen else None,
            "candidates": [entitlement_out(db, e) for e in candidates],
            "warranty": {"name": cover[1].name, "ends_on": cover[0].ends_on} if cover else None, "note": detail.verify_note}


def create_request(db: Session, tenant_id: int, *, description: str, party_id: int | None = None, asset_id: int | None = None,
                   severity: str | None = None, source: str = "call", sla_class: str = "standard") -> TroubleTicket:
    """A service request logged straight from the field service screen (calls use INT-10 the same way)."""
    from app.crm_core import ticket_service

    if party_id:
        _own(db, Party, party_id, tenant_id, "Customer")
    if asset_id:
        asset = _own(db, Asset, asset_id, tenant_id, "Asset")
        party_id = party_id or party_of_asset(db, asset)
    c = ticket_service.classify(description)
    sev = severity if severity in ("low", "medium", "high", "critical") else c["severity"]
    ticket = TroubleTicket(tenant_id=tenant_id, party_id=party_id, category=c["category"] if source != "pm" else "preventive_maintenance",
                           severity=sev, product_line=c["product_line"], sla_class=sla_class,
                           priority=ticket_service.assign_priority(sla_class, sev), status="open", description=description)
    db.add(ticket)
    db.flush()
    detail = request_detail(db, tenant_id, ticket)
    detail.source = source
    detail.asset_id = asset_id
    db.flush()
    return ticket


def sla_state(committed: dt.datetime | None, closed: bool, now: dt.datetime | None = None) -> str:
    if committed is None:
        return "none"
    if closed:
        return "met"
    now = now or utcnow()
    if now > committed:
        return "breached"
    if committed - now <= dt.timedelta(hours=2):
        return "at_risk"
    return "on_track"


def request_out(db: Session, tenant_id: int, ticket: TroubleTicket, full: bool = False) -> dict[str, Any]:
    detail = db.query(FsRequestDetail).filter(FsRequestDetail.ticket_id == ticket.id).first()
    party = db.get(Party, ticket.party_id) if ticket.party_id else None
    asset = db.get(Asset, detail.asset_id) if detail and detail.asset_id else None
    ent = db.get(FsEntitlement, detail.entitlement_id) if detail and detail.entitlement_id else None
    aw = db.get(FsAssetWarranty, detail.asset_warranty_id) if detail and detail.asset_warranty_id else None
    warranty = db.get(FsWarranty, aw.warranty_id) if aw else None
    wos = db.query(WorkOrder).filter(WorkOrder.ticket_id == ticket.id).order_by(WorkOrder.id).all()
    acts = [ensure(db, w) for w in wos]
    closed = ticket.status in ("resolved", "closed")
    out = {
        "id": ticket.id, "brt_id": getattr(ticket, "brt_id", None), "description": ticket.description, "category": ticket.category,
        "severity": ticket.severity, "priority": ticket.priority, "product_line": ticket.product_line, "sla_class": ticket.sla_class,
        "status": ticket.status, "party_id": ticket.party_id, "party_name": party.name if party else None,
        "asset_id": asset.id if asset else None, "asset_label": asset_label(db, asset) if asset else None,
        "opened_at": detail.opened_at if detail else ticket.created_at, "committed_at": detail.committed_at if detail else None,
        "verified": bool(detail and detail.verified_at), "verify_note": detail.verify_note if detail else "",
        "entitlement_name": ent.name if ent else None, "warranty_name": warranty.name if warranty else None,
        "source": detail.source if detail else "call", "closed_at": detail.closed_at if detail else None,
        "resolution_code": detail.resolution_code if detail else "", "resolution_notes": detail.resolution_notes if detail else "",
        "sla": sla_state(detail.committed_at if detail else None, closed),
        "activity_count": len(acts), "open_activities": sum(1 for w in wos if w.status not in DONE),
        "root_cause": ticket.root_cause_analysis.probable_cause if ticket.root_cause_analysis else None,
        "field_dispatch_required": bool(ticket.root_cause_analysis and ticket.root_cause_analysis.field_dispatch_required),
    }
    if full:
        out["activities"] = [activity_out(db, a) for a in acts]
        out["charges"] = charges_out(db, tenant_id, ticket_id=ticket.id)
        out["candidates"] = [entitlement_out(db, e) for e in entitlement_candidates(db, tenant_id, ticket.party_id, out["asset_id"], ticket.product_line)]
        out["customer_assets"] = [{"id": a.id, "label": asset_label(db, a)} for a in assets_of_party(db, tenant_id, ticket.party_id)] if ticket.party_id else []
    return out


def requests(db: Session, tenant_id: int, status: str = "open", party_id: int | None = None) -> list[dict[str, Any]]:
    q = db.query(TroubleTicket).filter(TroubleTicket.tenant_id == tenant_id)
    if party_id:
        q = q.filter(TroubleTicket.party_id == party_id)
    if status == "open":
        q = q.filter(TroubleTicket.status.notin_(("resolved", "closed")))
    elif status == "closed":
        q = q.filter(TroubleTicket.status.in_(("resolved", "closed")))
    rows = [request_out(db, tenant_id, t) for t in q.order_by(TroubleTicket.id.desc()).limit(300).all()]
    order = {"breached": 0, "at_risk": 1, "on_track": 2, "none": 3, "met": 4}
    return sorted(rows, key=lambda r: (order.get(r["sla"], 5), r["committed_at"] or dt.datetime.max, -r["id"]))


def close_request(db: Session, tenant_id: int, ticket_id: int, resolution_code: str = "", notes: str = "") -> TroubleTicket:
    ticket = _own(db, TroubleTicket, ticket_id, tenant_id, "Service request")
    open_wos = db.query(WorkOrder).filter(WorkOrder.ticket_id == ticket.id, WorkOrder.status.notin_(DONE)).count()
    if open_wos:
        raise ValueError(f"{open_wos} activit{'y is' if open_wos == 1 else 'ies are'} still open - complete or cancel them first")
    detail = request_detail(db, tenant_id, ticket)
    detail.closed_at = utcnow()
    detail.resolution_code = resolution_code or detail.resolution_code
    detail.resolution_notes = notes or detail.resolution_notes
    ticket.status = "closed"
    db.flush()
    return ticket


# -------------------------------------------------------------- activities

def assigned_technician(db: Session, wo: WorkOrder) -> Technician | None:
    a = db.query(TechnicianAssignment).filter(TechnicianAssignment.work_order_id == wo.id).first()
    return db.get(Technician, a.technician_id) if a else None


def ensure(db: Session, wo: WorkOrder) -> FsActivity:
    """The field details of a work order - created with defaults for work
    orders that INT-12 or order fulfillment made before this module."""
    act = db.query(FsActivity).filter(FsActivity.work_order_id == wo.id).first()
    if act is not None:
        return act
    ticket = db.get(TroubleTicket, wo.ticket_id) if wo.ticket_id else None
    detail = db.query(FsRequestDetail).filter(FsRequestDetail.ticket_id == ticket.id).first() if ticket else None
    party_id = ticket.party_id if ticket else None
    if party_id is None and wo.order_id:
        from app.models.orders import Order

        order = db.get(Order, wo.order_id)
        party_id = order.party_id if order else None
    labels = {"installation": "Installation", "connectivity_test": "Connectivity test & acceptance", "acceptance_letter": "Acceptance letter"}
    subject = labels.get(wo.kind) or (ticket.description[:120] if ticket else f"Work order {wo.id}")
    act = FsActivity(tenant_id=wo.tenant_id, work_order_id=wo.id, ticket_id=wo.ticket_id, party_id=party_id,
                     asset_id=detail.asset_id if detail else None, subject=subject,
                     category="installation" if wo.kind in FULFILLMENT_KINDS else "repair",
                     priority=TICKET_PRIORITY.get(ticket.priority, 3) if ticket else 3, duration_minutes=120,
                     due_at=detail.committed_at if detail else None, billable=wo.kind not in FULFILLMENT_KINDS)
    db.add(act)
    db.flush()
    return act


def create_activity(db: Session, tenant_id: int, *, ticket_id: int | None = None, party_id: int | None = None,
                    asset_id: int | None = None, template_id: int | None = None, subject: str = "", category: str | None = None,
                    priority: int | None = None, duration_minutes: int | None = None, skills: list[str] | None = None,
                    earliest_start: Any = None, due_at: Any = None, region_id: int | None = None, address: str = "",
                    billable: bool | None = None, instructions: str = "", pm_action_id: int | None = None) -> FsActivity:
    ticket = _own(db, TroubleTicket, ticket_id, tenant_id, "Service request") if ticket_id else None
    detail = request_detail(db, tenant_id, ticket) if ticket else None
    template = _own(db, FsActivityTemplate, template_id, tenant_id, "Template") if template_id else None
    asset_id = asset_id or (detail.asset_id if detail else None)
    asset = _own(db, Asset, asset_id, tenant_id, "Asset") if asset_id else None
    party_id = party_id or (ticket.party_id if ticket else None) or (party_of_asset(db, asset) if asset else None)
    adetail = db.query(FsAssetDetail).filter(FsAssetDetail.asset_id == asset.id).first() if asset else None
    wo = WorkOrder(tenant_id=tenant_id, ticket_id=ticket.id if ticket else None, kind="field_dispatch", status="unscheduled",
                   scheduled_window="not scheduled yet")
    db.add(wo)
    db.flush()
    t_priority = TICKET_PRIORITY.get(ticket.priority, 3) if ticket else 3
    act = FsActivity(
        tenant_id=tenant_id, work_order_id=wo.id, ticket_id=wo.ticket_id, party_id=party_id, asset_id=asset_id,
        template_id=template.id if template else None, pm_action_id=pm_action_id,
        subject=(subject or (template.name if template else "") or (ticket.description[:120] if ticket else "Field activity")).strip(),
        category=category if category in CATEGORIES else (template.category if template else "repair"),
        priority=max(1, min(4, int(priority or (template.priority if template else t_priority)))),
        duration_minutes=max(15, int(duration_minutes or (template.duration_minutes if template else 60))),
        earliest_start=_when(earliest_start) or utcnow(),
        due_at=_when(due_at) or (detail.committed_at if detail else None),
        region_id=region_id or (adetail.region_id if adetail else None),
        address=address or (adetail.address if adetail else ""),
        skills_json=_dump([_key(s) for s in (skills if skills is not None else (_j(template.skills_json, []) if template else [])) if str(s).strip()]),
        steps_json=_dump([{"text": s, "done": False} for s in (_j(template.steps_json, []) if template else [])]),
        items_json=template.items_json if template else "[]",
        instructions=instructions or (template.instructions if template else ""),
        billable=billable if billable is not None else (template.billable if template else True),
    )
    db.add(act)
    db.flush()
    return act


def activity(db: Session, tenant_id: int, activity_id: int) -> tuple[FsActivity, WorkOrder]:
    act = _own(db, FsActivity, activity_id, tenant_id, "Activity")
    return act, db.get(WorkOrder, act.work_order_id)


def activity_for_work_order(db: Session, tenant_id: int, work_order_id: int) -> FsActivity:
    wo = _own(db, WorkOrder, work_order_id, tenant_id, "Work order")
    return ensure(db, wo)


def activity_out(db: Session, act: FsActivity, full: bool = False) -> dict[str, Any]:
    wo = db.get(WorkOrder, act.work_order_id)
    tech = assigned_technician(db, wo) if wo else None
    party = db.get(Party, act.party_id) if act.party_id else None
    asset = db.get(Asset, act.asset_id) if act.asset_id else None
    steps = _j(act.steps_json, [])
    status = wo.status if wo else "unscheduled"
    if status == "scheduled" and not act.planned_start and not tech:
        status = "unscheduled"
    out = {
        "id": act.id, "work_order_id": act.work_order_id, "brt_id": getattr(wo, "brt_id", None), "kind": wo.kind if wo else "field_dispatch",
        "ticket_id": act.ticket_id, "party_id": act.party_id, "party_name": party.name if party else None,
        "asset_id": act.asset_id, "asset_label": asset_label(db, asset) if asset else None, "subject": act.subject,
        "category": act.category, "priority": act.priority, "priority_label": PRIORITIES.get(act.priority, "Medium"),
        "duration_minutes": act.duration_minutes, "earliest_start": act.earliest_start, "due_at": act.due_at,
        "planned_start": act.planned_start, "planned_end": act.planned_end, "window": wo.scheduled_window if wo else "",
        "status": status, "technician_id": tech.id if tech else None, "technician_name": tech.name if tech else None,
        "region_id": act.region_id, "address": act.address, "skills": _j(act.skills_json, []), "locked": act.locked,
        "billable": act.billable, "steps_done": sum(1 for s in steps if s.get("done")), "steps_total": len(steps),
        "late": bool(act.due_at and status in OPEN and ((act.planned_start or utcnow()) > act.due_at)),
        "close_intent": FULFILLMENT_KINDS[wo.kind].format(id=wo.id) if wo and wo.kind in FULFILLMENT_KINDS else None,
        "actual_start": act.actual_start, "actual_end": act.actual_end, "resolution_code": act.resolution_code,
    }
    if full:
        out["steps"] = steps
        out["items"] = _j(act.items_json, [])
        out["instructions"] = act.instructions
        out["notes"] = act.notes
        out["time"] = [{"id": t.id, "kind": t.kind, "started_at": t.started_at, "ended_at": t.ended_at, "minutes": round(t.minutes, 1),
                        "billable": t.billable, "note": t.note}
                       for t in db.query(FsTimeEntry).filter(FsTimeEntry.activity_id == act.id).order_by(FsTimeEntry.id).all()]
        out["expenses"] = [{"id": e.id, "kind": e.kind, "amount": e.amount, "billable": e.billable, "note": e.note}
                           for e in db.query(FsExpense).filter(FsExpense.activity_id == act.id).order_by(FsExpense.id).all()]
        out["parts"] = [{"id": p.id, "product_name": p.product_name, "quantity": p.quantity, "direction": p.direction,
                         "serial_number": p.serial_number, "asset_id": p.asset_id, "billable": p.billable, "unit_price": p.unit_price,
                         "swap_of_id": p.swap_of_id}
                        for p in db.query(FsPartMovement).filter(FsPartMovement.activity_id == act.id).order_by(FsPartMovement.id).all()]
        out["charges"] = charges_out(db, act.tenant_id, activity_id=act.id)
        out["measurements"] = [measurement_out(db, m) for m in db.query(FsMeasurement).filter(FsMeasurement.asset_id == act.asset_id).all()] if act.asset_id else []
        trunk = fs_inventory.trunk_of(db, act.tenant_id, tech.id) if tech else None
        out["trunk"] = fs_inventory.location_out(db, trunk) if trunk else None
        ev = db.query(CompletionEvidence).filter(CompletionEvidence.work_order_id == act.work_order_id).first()
        out["signature"] = ev.signature if ev else ""
        out["description"] = (db.get(TroubleTicket, act.ticket_id).description if act.ticket_id else "")
    return out


def activities(db: Session, tenant_id: int, status: str = "open", technician_id: int | None = None, day: Any = None,
               ticket_id: int | None = None) -> list[dict[str, Any]]:
    q = db.query(WorkOrder).filter(WorkOrder.tenant_id == tenant_id)
    if status == "open":
        q = q.filter(WorkOrder.status.notin_(DONE))
    elif status in DONE or status in OPEN:
        q = q.filter(WorkOrder.status == status)
    if ticket_id:
        q = q.filter(WorkOrder.ticket_id == ticket_id)
    rows = []
    the_day = _date(day)
    for wo in q.order_by(WorkOrder.id.desc()).limit(500).all():
        act = ensure(db, wo)
        if technician_id:
            tech = assigned_technician(db, wo)
            if tech is None or tech.id != technician_id:
                continue
        if the_day and not (act.planned_start and act.planned_start.date() == the_day):
            continue
        rows.append(activity_out(db, act))
    return sorted(rows, key=lambda a: (a["planned_start"] or dt.datetime.max, a["priority"], a["id"]))


def update_activity(db: Session, tenant_id: int, activity_id: int, data: dict[str, Any]) -> FsActivity:
    act, wo = activity(db, tenant_id, activity_id)
    for f in ("subject", "address", "instructions", "notes"):
        if data.get(f) is not None:
            setattr(act, f, str(data[f]))
    if data.get("category") in CATEGORIES:
        act.category = data["category"]
    if data.get("priority"):
        act.priority = max(1, min(4, int(data["priority"])))
    if data.get("duration_minutes"):
        act.duration_minutes = max(15, int(data["duration_minutes"]))
        if act.planned_start:
            act.planned_end = act.planned_start + dt.timedelta(minutes=act.duration_minutes)
    for f in ("earliest_start", "due_at"):
        if f in data:
            setattr(act, f, _when(data[f]))
    if "region_id" in data:
        act.region_id = data["region_id"] or None
    if data.get("skills") is not None:
        act.skills_json = _dump([_key(s) for s in data["skills"] if str(s).strip()])
    for f in ("locked", "billable"):
        if data.get(f) is not None:
            setattr(act, f, bool(data[f]))
    if data.get("asset_id") is not None:
        act.asset_id = _own(db, Asset, data["asset_id"], tenant_id, "Asset").id if data["asset_id"] else None
    db.flush()
    return act


def set_steps(db: Session, tenant_id: int, activity_id: int, steps: list[dict[str, Any]]) -> FsActivity:
    act, _ = activity(db, tenant_id, activity_id)
    act.steps_json = _dump([{"text": str(s.get("text") or "").strip(), "done": bool(s.get("done"))} for s in steps if str(s.get("text") or "").strip()])
    db.flush()
    return act


def _open_entry(db: Session, act: FsActivity, kind: str) -> FsTimeEntry | None:
    return (
        db.query(FsTimeEntry)
        .filter(FsTimeEntry.activity_id == act.id, FsTimeEntry.kind == kind, FsTimeEntry.ended_at.is_(None))
        .order_by(FsTimeEntry.id.desc())
        .first()
    )


def _stop(entry: FsTimeEntry | None, at: dt.datetime) -> None:
    if entry is not None:
        entry.ended_at = at
        entry.minutes = max(0.0, (at - entry.started_at).total_seconds() / 60)


def set_status(db: Session, tenant_id: int, activity_id: int, status: str, at: Any = None) -> FsActivity:
    """The engineer's progress: en route -> on site. The time tracker follows."""
    act, wo = activity(db, tenant_id, activity_id)
    now = _when(at) or utcnow()
    tech = assigned_technician(db, wo)
    if status == "en_route":
        if wo.status not in ("scheduled",):
            raise ValueError(f"An activity that is '{wo.status}' can't go en route")
        if tech is None:
            raise ValueError("Assign an engineer first")
        db.add(FsTimeEntry(tenant_id=tenant_id, activity_id=act.id, technician_id=tech.id, kind="travel", started_at=now, billable=act.billable))
    elif status == "on_site":
        if wo.status not in ("scheduled", "en_route"):
            raise ValueError(f"An activity that is '{wo.status}' can't start on site")
        if tech is None:
            raise ValueError("Assign an engineer first")
        _stop(_open_entry(db, act, "travel"), now)
        act.actual_start = now
        db.add(FsTimeEntry(tenant_id=tenant_id, activity_id=act.id, technician_id=tech.id, kind="work", started_at=now, billable=act.billable))
    elif status == "cancelled":
        if wo.status in ("complete", "cancelled"):
            raise ValueError(f"The activity is already {wo.status}")
        if wo.kind in FULFILLMENT_KINDS:
            raise ValueError("Fulfillment work orders are closed from the order, not cancelled here")
        for kind in ("travel", "work"):
            _stop(_open_entry(db, act, kind), now)
    else:
        raise ValueError(f"Unknown status '{status}' - use en_route, on_site or cancelled (complete has its own step)")
    wo.status = status
    db.flush()
    if status == "cancelled" and wo.kind == "tom":  # its order falls out - someone decides what happens instead
        from app.orchestration import fulfillment_engine

        fulfillment_engine.on_work_order_cancelled(db, wo)
    return act


def log_time(db: Session, tenant_id: int, activity_id: int, kind: str, minutes: float, billable: bool | None = None, note: str = "") -> FsTimeEntry:
    act, wo = activity(db, tenant_id, activity_id)
    tech = assigned_technician(db, wo)
    if minutes <= 0:
        raise ValueError("Minutes must be more than zero")
    now = utcnow()
    entry = FsTimeEntry(tenant_id=tenant_id, activity_id=act.id, technician_id=tech.id if tech else None,
                        kind=kind if kind in ("travel", "work") else "work", started_at=now - dt.timedelta(minutes=minutes),
                        ended_at=now, minutes=float(minutes), billable=act.billable if billable is None else billable, note=note)
    db.add(entry)
    db.flush()
    return entry


def add_expense(db: Session, tenant_id: int, activity_id: int, kind: str, amount: float, billable: bool | None = None, note: str = "") -> FsExpense:
    act, _ = activity(db, tenant_id, activity_id)
    if amount <= 0:
        raise ValueError("Amount must be more than zero")
    e = FsExpense(tenant_id=tenant_id, activity_id=act.id, kind=kind or "other", amount=float(amount),
                  billable=act.billable if billable is None else billable, note=note)
    db.add(e)
    db.flush()
    return e


def use_part(db: Session, tenant_id: int, activity_id: int, product_name: str, quantity: float = 1.0, location_id: int | None = None,
             billable: bool | None = None, serial_number: str = "", swap_of_id: int | None = None) -> FsPartMovement:
    """Part tracker - install: the part leaves the engineer's trunk (or the chosen stock) for the customer."""
    act, wo = activity(db, tenant_id, activity_id)
    tech = assigned_technician(db, wo)
    if location_id is None:
        trunk = fs_inventory.trunk_of(db, tenant_id, tech.id) if tech else None
        if trunk is None:
            raise ValueError("No trunk stock for this engineer - pick the location the part comes from")
        location_id = trunk.id
    txn = fs_inventory.move(db, tenant_id, "consume", product_name, quantity, source_id=location_id,
                            reference_type="activity", reference_id=act.id, note=f"Installed on activity {act.id}")
    mv = FsPartMovement(tenant_id=tenant_id, activity_id=act.id, product_name=txn.product_name, quantity=quantity, direction="install",
                        location_id=location_id, asset_id=act.asset_id, serial_number=serial_number,
                        billable=act.billable if billable is None else billable, unit_price=fs_inventory.unit_price(db, tenant_id, product_name),
                        transaction_id=txn.id, swap_of_id=swap_of_id)
    db.add(mv)
    db.flush()
    return mv


def remove_part(db: Session, tenant_id: int, activity_id: int, product_name: str, quantity: float = 1.0, location_id: int | None = None,
                defective: bool = True, serial_number: str = "", asset_id: int | None = None) -> FsPartMovement:
    """Part tracker - remove: a part taken from the customer site back into the trunk (defective by default)."""
    act, wo = activity(db, tenant_id, activity_id)
    tech = assigned_technician(db, wo)
    if location_id is None:
        trunk = fs_inventory.trunk_of(db, tenant_id, tech.id) if tech else None
        if trunk is None:
            raise ValueError("No trunk stock for this engineer - pick the location the part goes to")
        location_id = trunk.id
    txn = fs_inventory.move(db, tenant_id, "return_defective" if defective else "transfer", product_name, quantity, target_id=location_id,
                            target_status="defective" if defective else "good", reference_type="activity", reference_id=act.id,
                            note=f"Removed on activity {act.id}")
    mv = FsPartMovement(tenant_id=tenant_id, activity_id=act.id, product_name=txn.product_name, quantity=quantity, direction="remove",
                        location_id=location_id, asset_id=asset_id or act.asset_id, serial_number=serial_number, billable=False,
                        transaction_id=txn.id)
    db.add(mv)
    db.flush()
    return mv


def swap_asset(db: Session, tenant_id: int, activity_id: int, new_serial: str, product_name: str | None = None,
               location_id: int | None = None) -> Asset:
    """Asset swap: the faulty asset comes out, a new one goes in. Transferable
    warranties and the asset's entitlements move to the new asset."""
    act, wo = activity(db, tenant_id, activity_id)
    if not act.asset_id:
        raise ValueError("The activity has no asset to swap")
    old = _own(db, Asset, act.asset_id, tenant_id, "Asset")
    old_detail = asset_detail(db, tenant_id, old, create=True)
    product = (product_name or old_detail.product_name or old.asset_type).strip()
    removed = remove_part(db, tenant_id, act.id, product, 1, location_id=location_id, defective=True,
                          serial_number=old_detail.serial_number or old.identifier, asset_id=old.id)
    stock = location_id
    if stock is None:
        tech = assigned_technician(db, wo)
        trunk = fs_inventory.trunk_of(db, tenant_id, tech.id) if tech else None
        stock = trunk.id if trunk else None
    if stock is not None:
        lv = fs_inventory.level(db, tenant_id, stock, product, "good", create=False)
        if lv is not None and lv.on_hand - lv.reserved >= 1:
            use_part(db, tenant_id, act.id, product, 1, location_id=stock, serial_number=new_serial, swap_of_id=removed.id)
    new = Asset(tenant_id=tenant_id, service_account_id=old.service_account_id, order_item_id=old.order_item_id, asset_type=old.asset_type,
                identifier=new_serial or old.identifier, status=AssetStatus.active, attributes=old.attributes or "")
    db.add(new)
    db.flush()
    db.add(FsAssetDetail(tenant_id=tenant_id, asset_id=new.id, party_id=old_detail.party_id or party_of_asset(db, old), product_name=product,
                         serial_number=new_serial, installed_on=utcnow().date(), parent_asset_id=old_detail.parent_asset_id,
                         address=old_detail.address, region_id=old_detail.region_id, notes=f"Replaced asset {old.id} on activity {act.id}"))
    old.status = AssetStatus.decommissioned
    for aw in db.query(FsAssetWarranty).filter(FsAssetWarranty.asset_id == old.id).all():
        w = db.get(FsWarranty, aw.warranty_id)
        if w is not None and w.transferable:
            aw.asset_id = new.id
    for e in db.query(FsEntitlement).filter(FsEntitlement.asset_id == old.id).all():
        e.asset_id = new.id
    for m in db.query(FsMeasurement).filter(FsMeasurement.asset_id == old.id).all():
        db.add(FsMeasurement(tenant_id=tenant_id, asset_id=new.id, name=m.name, unit=m.unit, kind=m.kind, min_value=m.min_value,
                             max_value=m.max_value, lower_limit=m.lower_limit, upper_limit=m.upper_limit))
    for d in db.query(FsAssetDetail).filter(FsAssetDetail.parent_asset_id == old.id).all():
        d.parent_asset_id = new.id
    act.asset_id = new.id
    if act.ticket_id:
        detail = db.query(FsRequestDetail).filter(FsRequestDetail.ticket_id == act.ticket_id).first()
        if detail is not None and detail.asset_id == old.id:
            detail.asset_id = new.id
    db.flush()
    return new


def complete(db: Session, tenant_id: int, activity_id: int, resolution_code: str = "", notes: str = "", signature: str = "",
             at: Any = None) -> FsActivity:
    """Complete the activity: stop the clocks, record the evidence, price the work."""
    act, wo = activity(db, tenant_id, activity_id)
    if wo.kind in FULFILLMENT_KINDS:
        raise ValueError(f"Work order {wo.id} belongs to order fulfillment - close it from the order (it moves the order on)")
    if wo.status in DONE:
        raise ValueError(f"The activity is already {wo.status}")
    tech = assigned_technician(db, wo)
    if tech is None:
        raise ValueError("Assign an engineer first")
    now = _when(at) or utcnow()
    _stop(_open_entry(db, act, "travel"), now)
    _stop(_open_entry(db, act, "work"), now)
    if not db.query(FsTimeEntry).filter(FsTimeEntry.activity_id == act.id, FsTimeEntry.kind == "work").count():
        start = act.actual_start or now - dt.timedelta(minutes=act.duration_minutes)
        db.add(FsTimeEntry(tenant_id=tenant_id, activity_id=act.id, technician_id=tech.id, kind="work", started_at=start, ended_at=now,
                           minutes=max(1.0, (now - start).total_seconds() / 60), billable=act.billable))
    act.actual_start = act.actual_start or now
    act.actual_end = now
    act.resolution_code = resolution_code or act.resolution_code
    if notes:
        act.notes = notes
    steps = _j(act.steps_json, [])
    checklist = "; ".join(f"[{'x' if s.get('done') else ' '}] {s.get('text')}" for s in steps) or "Work done"
    ev = db.query(CompletionEvidence).filter(CompletionEvidence.work_order_id == wo.id).first()
    if ev is None:
        db.add(CompletionEvidence(work_order_id=wo.id, checklist=checklist[:4000], signature=(signature or "customer")[:200], completed_at=now))
    wo.status = "complete"
    db.flush()
    if act.ticket_id:
        detail = db.query(FsRequestDetail).filter(FsRequestDetail.ticket_id == act.ticket_id).first()
        if detail and detail.entitlement_id:
            ent = db.get(FsEntitlement, detail.entitlement_id)
            if ent is not None:
                ent.visits_used += 1
        if not db.query(WorkOrder).filter(WorkOrder.ticket_id == act.ticket_id, WorkOrder.status.notin_(DONE)).count():
            ticket = db.get(TroubleTicket, act.ticket_id)
            if ticket.status not in ("resolved", "closed"):
                ticket.status = "resolved"
    if act.pm_action_id:
        pm = db.get(FsPmAction, act.pm_action_id)
        if pm is not None:
            pm.status, pm.completed_on = "done", now.date()
    charge_activity(db, tenant_id, act.id)
    db.flush()
    if wo.kind == "tom":  # a technical-order work order: its orchestration task is done, the order moves on
        from app.orchestration import fulfillment_engine

        fulfillment_engine.on_work_order_closed(db, wo)
    return act


# ----------------------------------------------------------------- charges

def labor_rate(db: Session, tenant_id: int, act: FsActivity) -> float:
    region = db.get(FsServiceRegion, act.region_id) if act.region_id else None
    if region is None or not region.labor_rate:
        wo = db.get(WorkOrder, act.work_order_id)
        tech = assigned_technician(db, wo) if wo else None
        profile = db.query(FsEngineer).filter(FsEngineer.technician_id == tech.id).first() if tech else None
        region = db.get(FsServiceRegion, profile.region_id) if profile and profile.region_id else region
    if region is None or not region.labor_rate:
        region = next((r for r in regions(db, tenant_id) if r.labor_rate), None)
    return region.labor_rate if region else 0.0


def charge_activity(db: Session, tenant_id: int, activity_id: int) -> list[FsCharge]:
    """Price the billable time, parts and expenses of an activity, less cover.
    Warranty cover wins (100% of what it covers); otherwise the entitlement's discounts apply."""
    act, _ = activity(db, tenant_id, activity_id)
    for old in db.query(FsCharge).filter(FsCharge.activity_id == act.id, FsCharge.status == "pending").all():
        db.delete(old)
    db.flush()
    if not act.billable:
        return []
    detail = db.query(FsRequestDetail).filter(FsRequestDetail.ticket_id == act.ticket_id).first() if act.ticket_id else None
    ent = db.get(FsEntitlement, detail.entitlement_id) if detail and detail.entitlement_id else None
    cover = warranty_check(db, tenant_id, act.asset_id or (detail.asset_id if detail else None), (act.actual_end or utcnow()).date())
    warranty = cover[1] if cover else None

    def discount(kind: str) -> tuple[float, str]:
        if warranty is not None:
            covered = {"labor": warranty.covers_labor, "travel": warranty.covers_labor, "part": warranty.covers_parts,
                       "expense": warranty.covers_expenses}[kind]
            if covered:
                return 100.0, f"Warranty: {warranty.name}"
        if ent is not None:
            pct = {"labor": ent.labor_discount_pct, "travel": ent.labor_discount_pct, "part": ent.parts_discount_pct,
                   "expense": ent.expense_discount_pct}[kind]
            if pct:
                return min(100.0, pct), f"Entitlement: {ent.name}"
        return 0.0, ""

    rate = labor_rate(db, tenant_id, act)
    lines: list[tuple[str, str, float, float]] = []
    for kind, label in (("work", "labor"), ("travel", "travel")):
        minutes = sum(t.minutes for t in db.query(FsTimeEntry).filter(FsTimeEntry.activity_id == act.id, FsTimeEntry.kind == kind,
                                                                      FsTimeEntry.billable.is_(True)).all())
        if minutes > 0:
            hours = round(minutes / 60, 2)
            lines.append((label, f"{'Labor' if label == 'labor' else 'Travel'} {hours:g} h @ {rate:g}/h", hours, rate))
    for p in db.query(FsPartMovement).filter(FsPartMovement.activity_id == act.id, FsPartMovement.direction == "install",
                                             FsPartMovement.billable.is_(True)).all():
        lines.append(("part", p.product_name + (f" · {p.serial_number}" if p.serial_number else ""), p.quantity, p.unit_price))
    for e in db.query(FsExpense).filter(FsExpense.activity_id == act.id, FsExpense.billable.is_(True)).all():
        lines.append(("expense", f"Expense: {e.kind}" + (f" - {e.note}" if e.note else ""), 1.0, e.amount))
    out = []
    for kind, description, qty, price in lines:
        gross = round(qty * price, 2)
        pct, reason = discount(kind)
        net = round(gross * (1 - pct / 100), 2)
        row = FsCharge(tenant_id=tenant_id, ticket_id=act.ticket_id, activity_id=act.id, party_id=act.party_id, kind=kind,
                       description=description[:300], quantity=qty, unit_price=price, gross=gross, discount_pct=pct,
                       discount_reason=reason, net=net, status="pending")
        db.add(row)
        out.append(row)
    db.flush()
    return out


def charges_out(db: Session, tenant_id: int, ticket_id: int | None = None, activity_id: int | None = None,
                status: str | None = None) -> dict[str, Any]:
    q = db.query(FsCharge).filter(FsCharge.tenant_id == tenant_id)
    if ticket_id:
        q = q.filter(FsCharge.ticket_id == ticket_id)
    if activity_id:
        q = q.filter(FsCharge.activity_id == activity_id)
    if status:
        q = q.filter(FsCharge.status == status)
    rows = q.order_by(FsCharge.id).all()
    return {
        "lines": [{"id": c.id, "ticket_id": c.ticket_id, "activity_id": c.activity_id, "party_id": c.party_id, "kind": c.kind,
                   "description": c.description, "quantity": c.quantity, "unit_price": c.unit_price, "gross": c.gross,
                   "discount_pct": c.discount_pct, "discount_reason": c.discount_reason, "net": c.net, "status": c.status}
                  for c in rows],
        "gross": round(sum(c.gross for c in rows), 2),
        "covered": round(sum(c.gross - c.net for c in rows), 2),
        "net": round(sum(c.net for c in rows), 2),
        "pending": round(sum(c.net for c in rows if c.status == "pending"), 2),
    }


def invoice(db: Session, tenant_id: int, ticket_id: int | None = None, activity_id: int | None = None) -> dict[str, Any]:
    """Consolidate the pending charges onto the customer's billing account."""
    from app.integration import order_adapter

    q = db.query(FsCharge).filter(FsCharge.tenant_id == tenant_id, FsCharge.status == "pending")
    if ticket_id:
        q = q.filter(FsCharge.ticket_id == ticket_id)
    elif activity_id:
        q = q.filter(FsCharge.activity_id == activity_id)
    else:
        raise ValueError("Invoice a service request or an activity")
    rows = q.all()
    if not rows:
        raise ValueError("Nothing pending to invoice")
    party_id = next((c.party_id for c in rows if c.party_id), None)
    account = db.query(BillingAccount).filter(BillingAccount.party_id == party_id).first() if party_id else None
    total = round(sum(c.net for c in rows), 2)
    adjustment = None
    if total > 0:
        if account is None:
            raise ValueError("The customer has no billing account to invoice - it is opened with their first order")
        ref = f"service request {ticket_id}" if ticket_id else f"activity {activity_id}"
        adjustment = order_adapter.apply_billing_adjustment(db, account.id, None, total, f"Field service charges - {ref} ({len(rows)} lines)")
    for c in rows:
        c.status = "invoiced" if c.net > 0 else "waived"
        c.billing_adjustment_id = adjustment.id if adjustment else None
    db.flush()
    return {"invoiced": total, "lines": len(rows), "billing_adjustment_id": adjustment.id if adjustment else None,
            "waived": sum(1 for c in rows if c.status == "waived")}


# ---------------------------------------------------------------- overview

def overview(db: Session, tenant_id: int) -> dict[str, Any]:
    from app.models.field_service import FsInventoryLevel

    now = utcnow()
    today = now.date()
    reqs = requests(db, tenant_id, "open")
    wos = db.query(WorkOrder).filter(WorkOrder.tenant_id == tenant_id).all()
    acts = {wo.id: ensure(db, wo) for wo in wos}
    open_wos = [w for w in wos if w.status not in DONE]
    unscheduled = [w for w in open_wos if not acts[w.id].planned_start]
    todays = [w for w in wos if acts[w.id].planned_start and acts[w.id].planned_start.date() == today and w.status != "cancelled"]
    done_today = [w for w in wos if w.status == "complete" and acts[w.id].actual_end and acts[w.id].actual_end.date() == today]
    resolved = db.query(TroubleTicket).filter(TroubleTicket.tenant_id == tenant_id, TroubleTicket.status.in_(("resolved", "closed"))).all()
    visits = {t.id: sum(1 for w in wos if w.ticket_id == t.id and w.status == "complete") for t in resolved}
    with_visits = [t for t in resolved if visits[t.id] > 0]
    ftf = round(100 * sum(1 for t in with_visits if visits[t.id] == 1) / len(with_visits)) if with_visits else None
    pm_due = db.query(FsPmAction).filter(FsPmAction.tenant_id == tenant_id, FsPmAction.status == "scheduled",
                                         FsPmAction.due_on <= today + dt.timedelta(days=7)).count()
    low = db.query(FsInventoryLevel).filter(FsInventoryLevel.tenant_id == tenant_id, FsInventoryLevel.status == "good",
                                            FsInventoryLevel.min_qty > 0, FsInventoryLevel.on_hand < FsInventoryLevel.min_qty).count()
    repairs_open = db.query(FsRepair).filter(FsRepair.tenant_id == tenant_id, FsRepair.status.in_(fs_inventory.REPAIR_OPEN)).count()
    pending = charges_out(db, tenant_id, status="pending")["pending"]
    by_status: dict[str, int] = {}
    for w in open_wos:
        st = "unscheduled" if not acts[w.id].planned_start and w.status == "scheduled" and not assigned_technician(db, w) else w.status
        by_status[st] = by_status.get(st, 0) + 1
    return {
        "open_requests": len(reqs),
        "unverified": sum(1 for r in reqs if not r["verified"]),
        "breached": sum(1 for r in reqs if r["sla"] == "breached"),
        "at_risk": sum(1 for r in reqs if r["sla"] == "at_risk"),
        "open_activities": len(open_wos),
        "unscheduled": len(unscheduled),
        "today": len(todays),
        "in_progress": sum(1 for w in open_wos if w.status in ("en_route", "on_site")),
        "done_today": len(done_today),
        "first_time_fix": ftf,
        "pm_due": pm_due,
        "parts_below_min": low,
        "repairs_open": repairs_open,
        "pending_charges": pending,
        "by_status": by_status,
        "attention": [r for r in reqs if r["sla"] in ("breached", "at_risk") or not r["verified"]][:6],
    }


# ------------------------------------------------------------ sample setup

def sample_setup(db: Session, tenant_id: int, timezone: str = "UTC") -> dict[str, Any]:
    """A starter field service setup - only what is missing is added."""
    from app.models.field_service import FsInventoryLevel, FsPmPlan

    made: list[str] = []
    if not regions(db, tenant_id):
        for name in ("North", "South"):
            db.add(FsServiceRegion(tenant_id=tenant_id, name=name, timezone=(timezone or "UTC")[:50], day_start="08:00", day_end="17:00",
                                   work_days="0,1,2,3,4", labor_rate=45.0, travel_minutes=30))
        db.flush()
        made.append("service regions North and South (08:00-17:00, Mon-Fri, 45/h)")
    warehouse = db.query(FsInventoryLocation).filter(FsInventoryLocation.tenant_id == tenant_id, FsInventoryLocation.kind == "warehouse").first()
    if warehouse is None:
        warehouse = FsInventoryLocation(tenant_id=tenant_id, name="Central Warehouse", kind="warehouse")
        db.add(warehouse)
        db.flush()
        stock = [("ONT fiber modem", 40, 6, 40, 80, 120), ("Router CPE", 25, 5, 25, 95, 150), ("SFP module 1G", 30, 5, 30, 40, 65),
                 ("Fiber patch cord", 200, 30, 200, 3, 8), ("Splice protector", 300, 50, 300, 0.5, 2)]
        for product, qty, mn, mx, cost, price in stock:
            db.add(FsInventoryLevel(tenant_id=tenant_id, location_id=warehouse.id, product_name=product, status="good", on_hand=qty,
                                    min_qty=mn, max_qty=mx, unit_cost=cost, unit_price=price))
        db.flush()
        made.append("Central Warehouse with starter stock")
    if not db.query(FsInventoryLocation).filter(FsInventoryLocation.tenant_id == tenant_id, FsInventoryLocation.kind == "repair_center").first():
        db.add(FsInventoryLocation(tenant_id=tenant_id, name="Repair Center", kind="repair_center", parent_id=warehouse.id))
        made.append("Repair Center")
    for tech in db.query(Technician).filter(Technician.tenant_id == tenant_id).all():
        profile = engineer_profile(db, tenant_id, tech)
        if profile.region_id is None:
            region = region_by_name(db, tenant_id, tech.region)
            profile.region_id = region.id if region else None
        if fs_inventory.trunk_of(db, tenant_id, tech.id) is None:
            trunk = FsInventoryLocation(tenant_id=tenant_id, name=f"{tech.name} - trunk", kind="trunk", technician_id=tech.id,
                                        parent_id=warehouse.id, region_id=profile.region_id)
            db.add(trunk)
            db.flush()
            profile.trunk_location_id = trunk.id
            for product, qty, mn, mx in (("ONT fiber modem", 2, 1, 3), ("Router CPE", 2, 1, 3), ("Fiber patch cord", 10, 4, 12)):
                lv = fs_inventory.level(db, tenant_id, trunk.id, product)
                lv.min_qty, lv.max_qty = mn, mx
                if lv.on_hand < qty:
                    fs_inventory.move(db, tenant_id, "transfer", product, qty - lv.on_hand, source_id=warehouse.id, target_id=trunk.id,
                                      note="Initial trunk stock")
            made.append(f"trunk stock for {tech.name}")
    db.flush()
    templates = [
        {"name": "Fiber installation", "category": "installation", "duration_minutes": 120, "priority": 3, "skills": ["fiber_install"],
         "steps": ["Confirm the site survey", "Pull and splice the drop fiber", "Install and power the ONT", "Light test and record levels",
                   "Walk the customer through the service"],
         "items": [{"product": "ONT fiber modem", "quantity": 1}, {"product": "Fiber patch cord", "quantity": 2}, {"product": "Splicer", "quantity": 1, "tool": True}]},
        {"name": "Connectivity fault repair", "category": "repair", "duration_minutes": 90, "priority": 2, "skills": ["network"],
         "steps": ["Check alarms on the ONT/CPE", "Test the line end to end", "Replace the faulty part", "Confirm service with the customer"],
         "items": [{"product": "Fiber patch cord", "quantity": 1}, {"product": "SFP module 1G", "quantity": 1}]},
        {"name": "CPE swap", "category": "repair", "duration_minutes": 45, "priority": 2, "skills": ["network"],
         "steps": ["Back up the CPE configuration", "Swap the CPE", "Restore the configuration", "Test the services"],
         "items": [{"product": "Router CPE", "quantity": 1}]},
        {"name": "Preventive maintenance visit", "category": "preventive_maintenance", "duration_minutes": 60, "priority": 4, "skills": [],
         "steps": ["Inspect equipment and cabling", "Clean connectors", "Record readings", "Update firmware if due"], "items": []},
    ]
    have = {_key(t.name) for t in db.query(FsActivityTemplate).filter(FsActivityTemplate.tenant_id == tenant_id).all()}
    for t in templates:
        if _key(t["name"]) not in have:
            save_template(db, tenant_id, t)
            made.append(f"template '{t['name']}'")
    if not db.query(FsWarranty).filter(FsWarranty.tenant_id == tenant_id).first():
        save_warranty(db, tenant_id, {"name": "Standard equipment warranty (1 year)", "products": ["ONT fiber modem", "Router CPE"],
                                      "duration_days": 365, "covers_parts": True, "covers_labor": True, "transferable": True,
                                      "provider": "Manufacturer"})
        made.append("1-year equipment warranty")
    if not db.query(FsPmPlan).filter(FsPmPlan.tenant_id == tenant_id).first():
        pm_template = db.query(FsActivityTemplate).filter(FsActivityTemplate.tenant_id == tenant_id,
                                                          FsActivityTemplate.name.ilike("Preventive maintenance visit")).first()
        db.add(FsPmPlan(tenant_id=tenant_id, name="Quarterly CPE health check", priority=3, products="Router CPE,ONT fiber modem",
                        triggers_json=_dump([{"type": "interval", "every_days": 90}]), template_id=pm_template.id if pm_template else None,
                        active=False))
        made.append("PM plan 'Quarterly CPE health check' (inactive until you switch it on)")
    db.flush()
    return {"created": made}

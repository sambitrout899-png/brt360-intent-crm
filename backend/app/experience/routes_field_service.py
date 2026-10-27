"""Field Service API - service requests, activities, dispatch, the
engineer's job card, inventory, assets, preventive maintenance and repairs
(crm_core/fs_*.py). Everything needs the Field Service page (Admin ->
Roles); changing anything needs an admin or agent (viewers stay read-only).
Customers' service requests and assets follow the caller's data scope;
dispatch, engineers and inventory are shared by the whole field team."""

from __future__ import annotations

from typing import Any, Callable

from fastapi import APIRouter, Body, Depends, HTTPException
from sqlalchemy.orm import Session

from app.access import check_party, require_page, visible_parties
from app.auth import require_roles
from app.crm_core import fs_inventory, fs_pm, fs_schedule, fs_service as fs
from app.crm_core.access_service import Access
from app.db import get_db
from app.models.assurance import TroubleTicket
from app.models.auth import UserRole
from app.models.field_service import (
    FsAbsence,
    FsActivityTemplate,
    FsEntitlement,
    FsInventoryLevel,
    FsInventoryLocation,
    FsPmPlan,
    FsRepair,
    FsWarranty,
)
from app.models.fulfillment import Asset
from app.models.assurance import Technician
from app.tenancy import get_current_tenant_id

_PAGE = require_page("field")
router = APIRouter(prefix="/api/field-service", tags=["field-service"], dependencies=[Depends(_PAGE)])
_write = Depends(require_roles(UserRole.admin, UserRole.agent))
Payload = dict[str, Any]


def _do(db: Session, fn: Callable[[], Any]) -> Any:
    """Run a change, commit it, and turn a business-rule refusal into a 400."""
    try:
        result = fn()
        db.commit()
        return result
    except (ValueError, TypeError, KeyError) as exc:
        db.rollback()
        raise HTTPException(400, str(exc).strip("'\"") or "That can't be done") from exc


def _read(db: Session, fn: Callable[[], Any]) -> Any:
    """Reads may fill in field details for older rows (ensure()); keep them."""
    try:
        result = fn()
        db.commit()
        return result
    except ValueError as exc:
        db.rollback()
        raise HTTPException(404, str(exc)) from exc


def _int(value: Any) -> int | None:
    return int(value) if value not in (None, "", 0, "0") else None


# ------------------------------------------------------------- overview

@router.get("/overview")
def overview(db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)):
    return _read(db, lambda: fs.overview(db, tenant_id))


@router.post("/setup/sample", dependencies=[_write])
def sample_setup(payload: Payload = Body(default={}), db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)):
    return _do(db, lambda: fs.sample_setup(db, tenant_id, str(payload.get("timezone") or "UTC")))


# ------------------------------------------------------- service requests

@router.get("/requests")
def list_requests(status: str = "open", party_id: int | None = None, db: Session = Depends(get_db),
                  tenant_id: int = Depends(get_current_tenant_id), access: Access = Depends(_PAGE)):
    parties = visible_parties(db, access)
    return _read(db, lambda: [r for r in fs.requests(db, tenant_id, status, party_id)
                              if parties is None or not r["party_id"] or r["party_id"] in parties])


@router.post("/requests", dependencies=[_write])
def create_request(payload: Payload = Body(...), db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)):
    def run():
        if not str(payload.get("description") or "").strip():
            raise ValueError("Describe the problem")
        t = fs.create_request(db, tenant_id, description=str(payload["description"]).strip(), party_id=_int(payload.get("party_id")),
                              asset_id=_int(payload.get("asset_id")), severity=payload.get("severity"))
        if payload.get("verify", True):
            fs.verify(db, tenant_id, t.id)
        return fs.request_out(db, tenant_id, t, full=True)

    return _do(db, run)


def _ticket(db: Session, tenant_id: int, ticket_id: int) -> TroubleTicket:
    t = db.get(TroubleTicket, ticket_id)
    if t is None or t.tenant_id != tenant_id:
        raise HTTPException(404, "Service request not found")
    return t


@router.get("/requests/{ticket_id}")
def get_request(ticket_id: int, db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id),
                access: Access = Depends(_PAGE)):
    t = _ticket(db, tenant_id, ticket_id)
    if t.party_id:
        check_party(db, access, t.party_id)
    return _read(db, lambda: fs.request_out(db, tenant_id, t, full=True))


@router.post("/requests/{ticket_id}/verify", dependencies=[_write])
def verify_request(ticket_id: int, payload: Payload = Body(default={}), db: Session = Depends(get_db),
                   tenant_id: int = Depends(get_current_tenant_id)):
    asset = payload.get("asset_id")
    return _do(db, lambda: fs.verify(db, tenant_id, ticket_id, asset_id=(int(asset) if asset else 0) if "asset_id" in payload else None,
                                     entitlement_id=_int(payload.get("entitlement_id"))))


@router.post("/requests/{ticket_id}/activities", dependencies=[_write])
def add_activity(ticket_id: int, payload: Payload = Body(default={}), db: Session = Depends(get_db),
                 tenant_id: int = Depends(get_current_tenant_id)):
    def run():
        act = fs.create_activity(db, tenant_id, ticket_id=ticket_id, template_id=_int(payload.get("template_id")),
                                 subject=str(payload.get("subject") or ""), category=payload.get("category"),
                                 priority=_int(payload.get("priority")), duration_minutes=_int(payload.get("duration_minutes")),
                                 skills=payload.get("skills"), billable=payload.get("billable"))
        return fs.activity_out(db, act, full=True)

    return _do(db, run)


@router.post("/requests/{ticket_id}/close", dependencies=[_write])
def close_request(ticket_id: int, payload: Payload = Body(default={}), db: Session = Depends(get_db),
                  tenant_id: int = Depends(get_current_tenant_id)):
    return _do(db, lambda: fs.request_out(db, tenant_id, fs.close_request(db, tenant_id, ticket_id, str(payload.get("resolution_code") or ""),
                                                                          str(payload.get("notes") or "")), full=True))


@router.post("/requests/{ticket_id}/invoice", dependencies=[_write])
def invoice_request(ticket_id: int, db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)):
    _ticket(db, tenant_id, ticket_id)
    return _do(db, lambda: fs.invoice(db, tenant_id, ticket_id=ticket_id))


# ------------------------------------------------------------ activities

@router.get("/activities")
def list_activities(status: str = "open", technician_id: int | None = None, day: str | None = None, ticket_id: int | None = None,
                    db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)):
    return _read(db, lambda: fs.activities(db, tenant_id, status, technician_id, day, ticket_id))


@router.post("/activities", dependencies=[_write])
def create_activity(payload: Payload = Body(...), db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)):
    def run():
        act = fs.create_activity(db, tenant_id, ticket_id=_int(payload.get("ticket_id")), party_id=_int(payload.get("party_id")),
                                 asset_id=_int(payload.get("asset_id")), template_id=_int(payload.get("template_id")),
                                 subject=str(payload.get("subject") or ""), category=payload.get("category"),
                                 priority=_int(payload.get("priority")), duration_minutes=_int(payload.get("duration_minutes")),
                                 skills=payload.get("skills"), earliest_start=payload.get("earliest_start"), due_at=payload.get("due_at"),
                                 region_id=_int(payload.get("region_id")), address=str(payload.get("address") or ""),
                                 billable=payload.get("billable"), instructions=str(payload.get("instructions") or ""))
        return fs.activity_out(db, act, full=True)

    return _do(db, run)


@router.get("/work-orders/{work_order_id}/activity")
def activity_of_work_order(work_order_id: int, db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)):
    return _read(db, lambda: fs.activity_out(db, fs.activity_for_work_order(db, tenant_id, work_order_id), full=True))


@router.get("/activities/{activity_id}")
def get_activity(activity_id: int, db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)):
    return _read(db, lambda: fs.activity_out(db, fs.activity(db, tenant_id, activity_id)[0], full=True))


def _act(db: Session, tenant_id: int, activity_id: int) -> dict[str, Any]:
    return fs.activity_out(db, fs.activity(db, tenant_id, activity_id)[0], full=True)


@router.patch("/activities/{activity_id}", dependencies=[_write])
def update_activity(activity_id: int, payload: Payload = Body(...), db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)):
    return _do(db, lambda: (fs.update_activity(db, tenant_id, activity_id, payload), _act(db, tenant_id, activity_id))[1])


@router.put("/activities/{activity_id}/steps", dependencies=[_write])
def set_steps(activity_id: int, payload: Payload = Body(...), db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)):
    return _do(db, lambda: (fs.set_steps(db, tenant_id, activity_id, payload.get("steps") or []), _act(db, tenant_id, activity_id))[1])


@router.post("/activities/{activity_id}/status", dependencies=[_write])
def set_status(activity_id: int, payload: Payload = Body(...), db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)):
    return _do(db, lambda: (fs.set_status(db, tenant_id, activity_id, str(payload.get("status") or "")), _act(db, tenant_id, activity_id))[1])


@router.post("/activities/{activity_id}/time", dependencies=[_write])
def log_time(activity_id: int, payload: Payload = Body(...), db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)):
    return _do(db, lambda: (fs.log_time(db, tenant_id, activity_id, str(payload.get("kind") or "work"), float(payload.get("minutes") or 0),
                                        payload.get("billable"), str(payload.get("note") or "")), _act(db, tenant_id, activity_id))[1])


@router.post("/activities/{activity_id}/expenses", dependencies=[_write])
def add_expense(activity_id: int, payload: Payload = Body(...), db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)):
    return _do(db, lambda: (fs.add_expense(db, tenant_id, activity_id, str(payload.get("kind") or "other"), float(payload.get("amount") or 0),
                                           payload.get("billable"), str(payload.get("note") or "")), _act(db, tenant_id, activity_id))[1])


@router.post("/activities/{activity_id}/parts", dependencies=[_write])
def use_part(activity_id: int, payload: Payload = Body(...), db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)):
    return _do(db, lambda: (fs.use_part(db, tenant_id, activity_id, str(payload.get("product_name") or ""), float(payload.get("quantity") or 1),
                                        _int(payload.get("location_id")), payload.get("billable"), str(payload.get("serial_number") or "")),
                            _act(db, tenant_id, activity_id))[1])


@router.post("/activities/{activity_id}/parts/remove", dependencies=[_write])
def remove_part(activity_id: int, payload: Payload = Body(...), db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)):
    return _do(db, lambda: (fs.remove_part(db, tenant_id, activity_id, str(payload.get("product_name") or ""), float(payload.get("quantity") or 1),
                                           _int(payload.get("location_id")), bool(payload.get("defective", True)),
                                           str(payload.get("serial_number") or "")), _act(db, tenant_id, activity_id))[1])


@router.post("/activities/{activity_id}/swap", dependencies=[_write])
def swap_asset(activity_id: int, payload: Payload = Body(...), db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)):
    return _do(db, lambda: (fs.swap_asset(db, tenant_id, activity_id, str(payload.get("new_serial") or ""), payload.get("product_name"),
                                          _int(payload.get("location_id"))), _act(db, tenant_id, activity_id))[1])


@router.post("/activities/{activity_id}/complete", dependencies=[_write])
def complete(activity_id: int, payload: Payload = Body(default={}), db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)):
    return _do(db, lambda: (fs.complete(db, tenant_id, activity_id, str(payload.get("resolution_code") or ""), str(payload.get("notes") or ""),
                                        str(payload.get("signature") or "")), _act(db, tenant_id, activity_id))[1])


@router.post("/activities/{activity_id}/charges", dependencies=[_write])
def recalculate_charges(activity_id: int, db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)):
    return _do(db, lambda: (fs.charge_activity(db, tenant_id, activity_id), _act(db, tenant_id, activity_id))[1])


@router.post("/activities/{activity_id}/invoice", dependencies=[_write])
def invoice_activity(activity_id: int, db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)):
    return _do(db, lambda: fs.invoice(db, tenant_id, activity_id=activity_id))


@router.post("/measurements/{measurement_id}/readings", dependencies=[_write])
def add_reading(measurement_id: int, payload: Payload = Body(...), db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)):
    def run():
        r = fs.record_reading(db, tenant_id, measurement_id, float(payload["value"]), _int(payload.get("activity_id")), payload.get("read_at"))
        return {"id": r.id, "value": r.value, "read_at": r.read_at}

    return _do(db, run)


# ---------------------------------------------------- scheduling/dispatch

@router.get("/activities/{activity_id}/candidates")
def candidates(activity_id: int, days: int = 7, db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)):
    return _read(db, lambda: fs_schedule.rank(db, tenant_id, activity_id, days=max(1, min(days, 28))))


@router.get("/activities/{activity_id}/slots")
def slots(activity_id: int, days: int = 7, heuristic: str = "earliest", technician_id: int | None = None, start_day: str | None = None,
          limit: int = 8, db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)):
    return _read(db, lambda: fs_schedule.slots(db, tenant_id, activity_id, start_day, max(1, min(days, 28)),
                                               heuristic if heuristic in fs_schedule.HEURISTICS else "earliest", max(1, min(limit, 30)),
                                               technician_id))


@router.post("/activities/{activity_id}/book", dependencies=[_write])
def book(activity_id: int, payload: Payload = Body(...), db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)):
    return _do(db, lambda: (fs_schedule.book(db, tenant_id, activity_id, int(payload["technician_id"]), payload.get("start"),
                                             bool(payload.get("force"))), _act(db, tenant_id, activity_id))[1])


@router.post("/activities/{activity_id}/unassign", dependencies=[_write])
def unassign(activity_id: int, db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)):
    return _do(db, lambda: (fs_schedule.unassign(db, tenant_id, activity_id), _act(db, tenant_id, activity_id))[1])


@router.get("/board")
def board(day: str | None = None, region_id: int | None = None, db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)):
    return _read(db, lambda: fs_schedule.board(db, tenant_id, day, region_id))


@router.post("/auto-schedule", dependencies=[_write])
def auto_schedule(payload: Payload = Body(default={}), db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)):
    return _do(db, lambda: fs_schedule.auto_schedule(db, tenant_id, max(1, min(int(payload.get("days") or 5), 28)), _int(payload.get("region_id"))))


# ----------------------------------------------------- engineers/regions

@router.get("/engineers")
def list_engineers(region_id: int | None = None, db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)):
    return _read(db, lambda: [fs.engineer_out(db, t, p) for t, p in fs.engineers(db, tenant_id, region_id)])


@router.post("/engineers", dependencies=[_write])
def create_engineer(payload: Payload = Body(...), db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)):
    def run():
        tech = fs.save_engineer(db, tenant_id, None, payload)
        return fs.engineer_out(db, tech, fs.engineer_profile(db, tenant_id, tech))

    return _do(db, run)


@router.put("/engineers/{technician_id}", dependencies=[_write])
def update_engineer(technician_id: int, payload: Payload = Body(...), db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)):
    def run():
        tech = fs.save_engineer(db, tenant_id, technician_id, payload)
        return fs.engineer_out(db, tech, fs.engineer_profile(db, tenant_id, tech))

    return _do(db, run)


@router.get("/engineers/{technician_id}/absences")
def list_absences(technician_id: int, db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)):
    rows = db.query(FsAbsence).filter(FsAbsence.tenant_id == tenant_id, FsAbsence.technician_id == technician_id).order_by(FsAbsence.starts_at.desc()).all()
    return [{"id": a.id, "starts_at": a.starts_at, "ends_at": a.ends_at, "reason": a.reason} for a in rows]


@router.post("/engineers/{technician_id}/absences", dependencies=[_write])
def add_absence(technician_id: int, payload: Payload = Body(...), db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)):
    def run():
        a = fs_schedule.add_absence(db, tenant_id, technician_id, payload.get("starts_at"), payload.get("ends_at"), str(payload.get("reason") or ""))
        return {"id": a.id, "starts_at": a.starts_at, "ends_at": a.ends_at, "reason": a.reason}

    return _do(db, run)


@router.delete("/absences/{absence_id}", dependencies=[_write])
def delete_absence(absence_id: int, db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)):
    def run():
        a = fs._own(db, FsAbsence, absence_id, tenant_id, "Absence")
        db.delete(a)
        return {"deleted": absence_id}

    return _do(db, run)


@router.get("/regions")
def list_regions(db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)):
    return [fs.region_out(r) for r in fs.regions(db, tenant_id)]


@router.post("/regions", dependencies=[_write])
def create_region(payload: Payload = Body(...), db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)):
    return _do(db, lambda: fs.region_out(fs.save_region(db, tenant_id, payload)))


@router.put("/regions/{region_id}", dependencies=[_write])
def update_region(region_id: int, payload: Payload = Body(...), db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)):
    return _do(db, lambda: fs.region_out(fs.save_region(db, tenant_id, payload, region_id)))


# ------------------------------------------------------------- templates

@router.get("/templates")
def list_templates(db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)):
    rows = db.query(FsActivityTemplate).filter(FsActivityTemplate.tenant_id == tenant_id).order_by(FsActivityTemplate.name).all()
    return [fs.template_out(t) for t in rows]


@router.post("/templates", dependencies=[_write])
def create_template(payload: Payload = Body(...), db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)):
    return _do(db, lambda: fs.template_out(fs.save_template(db, tenant_id, payload)))


@router.put("/templates/{template_id}", dependencies=[_write])
def update_template(template_id: int, payload: Payload = Body(...), db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)):
    return _do(db, lambda: fs.template_out(fs.save_template(db, tenant_id, payload, template_id)))


# ------------------------------------------------- entitlements/warranties

@router.get("/entitlements")
def list_entitlements(party_id: int | None = None, db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)):
    q = db.query(FsEntitlement).filter(FsEntitlement.tenant_id == tenant_id)
    if party_id:
        q = q.filter(FsEntitlement.party_id == party_id)
    return [fs.entitlement_out(db, e) for e in q.order_by(FsEntitlement.id.desc()).all()]


@router.post("/entitlements", dependencies=[_write])
def create_entitlement(payload: Payload = Body(...), db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)):
    return _do(db, lambda: fs.entitlement_out(db, fs.save_entitlement(db, tenant_id, payload)))


@router.put("/entitlements/{entitlement_id}", dependencies=[_write])
def update_entitlement(entitlement_id: int, payload: Payload = Body(...), db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)):
    return _do(db, lambda: fs.entitlement_out(db, fs.save_entitlement(db, tenant_id, payload, entitlement_id)))


@router.get("/warranties")
def list_warranties(db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)):
    return [fs.warranty_out(w) for w in db.query(FsWarranty).filter(FsWarranty.tenant_id == tenant_id).order_by(FsWarranty.name).all()]


@router.post("/warranties", dependencies=[_write])
def create_warranty(payload: Payload = Body(...), db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)):
    return _do(db, lambda: fs.warranty_out(fs.save_warranty(db, tenant_id, payload)))


@router.put("/warranties/{warranty_id}", dependencies=[_write])
def update_warranty(warranty_id: int, payload: Payload = Body(...), db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)):
    return _do(db, lambda: fs.warranty_out(fs.save_warranty(db, tenant_id, payload, warranty_id)))


# ---------------------------------------------------------------- assets

@router.get("/assets")
def list_assets(party_id: int | None = None, q: str = "", db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id),
                access: Access = Depends(_PAGE)):
    parties = visible_parties(db, access)
    return _read(db, lambda: [a for a in fs.assets(db, tenant_id, party_id, q)
                              if parties is None or not a["party_id"] or a["party_id"] in parties])


@router.post("/assets", dependencies=[_write])
def register_asset(payload: Payload = Body(...), db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)):
    def run():
        a = fs.register_asset(db, tenant_id, party_id=int(payload["party_id"]), product_name=str(payload.get("product_name") or ""),
                              serial_number=str(payload.get("serial_number") or ""), installed_on=payload.get("installed_on"),
                              service_account_id=_int(payload.get("service_account_id")), parent_asset_id=_int(payload.get("parent_asset_id")),
                              address=str(payload.get("address") or ""), region_id=_int(payload.get("region_id")))
        return fs.asset_out(db, tenant_id, a, full=True)

    return _do(db, run)


def _asset(db: Session, tenant_id: int, asset_id: int) -> Asset:
    a = db.get(Asset, asset_id)
    if a is None or a.tenant_id != tenant_id:
        raise HTTPException(404, "Asset not found")
    return a


@router.get("/assets/{asset_id}")
def get_asset(asset_id: int, db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id),
              access: Access = Depends(_PAGE)):
    a = _asset(db, tenant_id, asset_id)
    check_party(db, access, fs.party_of_asset(db, a))
    return _read(db, lambda: fs.asset_out(db, tenant_id, a, full=True))


@router.put("/assets/{asset_id}", dependencies=[_write])
def update_asset(asset_id: int, payload: Payload = Body(...), db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)):
    return _do(db, lambda: fs.asset_out(db, tenant_id, fs.update_asset(db, tenant_id, asset_id, payload), full=True))


@router.post("/assets/{asset_id}/warranties", dependencies=[_write])
def apply_warranties(asset_id: int, db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)):
    return _do(db, lambda: (fs.apply_warranties(db, tenant_id, asset_id), fs.asset_out(db, tenant_id, _asset(db, tenant_id, asset_id), full=True))[1])


@router.post("/assets/{asset_id}/measurements", dependencies=[_write])
def add_measurement(asset_id: int, payload: Payload = Body(...), db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)):
    return _do(db, lambda: (fs.add_measurement(db, tenant_id, asset_id, payload), fs.asset_out(db, tenant_id, _asset(db, tenant_id, asset_id), full=True))[1])


# ------------------------------------------------------------- inventory

@router.get("/inventory/locations")
def list_locations(db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)):
    return fs_inventory.locations(db, tenant_id)


@router.post("/inventory/locations", dependencies=[_write])
def create_location(payload: Payload = Body(...), db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)):
    def run():
        kind = payload.get("kind") if payload.get("kind") in fs_inventory.LOCATION_KINDS else "warehouse"
        name = str(payload.get("name") or "").strip()
        if not name:
            raise ValueError("Name the location")
        loc = FsInventoryLocation(tenant_id=tenant_id, name=name, kind=kind, parent_id=_int(payload.get("parent_id")),
                                  technician_id=_int(payload.get("technician_id")), region_id=_int(payload.get("region_id")),
                                  address=str(payload.get("address") or ""))
        db.add(loc)
        db.flush()
        if loc.kind == "trunk" and loc.technician_id:
            tech = db.get(Technician, loc.technician_id)
            if tech is not None and tech.tenant_id == tenant_id:
                fs.engineer_profile(db, tenant_id, tech).trunk_location_id = loc.id
        return fs_inventory.location_out(db, loc)

    return _do(db, run)


@router.put("/inventory/locations/{location_id}", dependencies=[_write])
def update_location(location_id: int, payload: Payload = Body(...), db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)):
    def run():
        loc = fs._own(db, FsInventoryLocation, location_id, tenant_id, "Location")
        for f in ("name", "address"):
            if payload.get(f) is not None:
                setattr(loc, f, str(payload[f]))
        for f in ("parent_id", "region_id", "technician_id"):
            if f in payload:
                setattr(loc, f, _int(payload[f]))
        if payload.get("active") is not None:
            loc.active = bool(payload["active"])
        db.flush()
        return fs_inventory.location_out(db, loc)

    return _do(db, run)


@router.post("/inventory/levels", dependencies=[_write])
def save_level(payload: Payload = Body(...), db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)):
    """The authorized stock list of a location: product, min/max, cost and price."""
    def run():
        if payload.get("id"):
            lv = fs._own(db, FsInventoryLevel, int(payload["id"]), tenant_id, "Inventory level")
        else:
            loc = fs_inventory.location(db, tenant_id, _int(payload.get("location_id")))
            if loc is None or not str(payload.get("product_name") or "").strip():
                raise ValueError("Pick a location and a product")
            lv = fs_inventory.level(db, tenant_id, loc.id, str(payload["product_name"]), str(payload.get("status") or "good"))
        for f in ("min_qty", "max_qty", "unit_cost", "unit_price"):
            if payload.get(f) not in (None, ""):
                setattr(lv, f, max(0.0, float(payload[f])))
        if payload.get("locator") is not None:
            lv.locator = str(payload["locator"])
        db.flush()
        return fs_inventory.level_out(lv)

    return _do(db, run)


@router.get("/inventory/parts")
def part_browser(q: str = "", db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)):
    return fs_inventory.part_browser(db, tenant_id, q)


@router.get("/inventory/transactions")
def list_transactions(limit: int = 100, db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)):
    return fs_inventory.transactions(db, tenant_id, max(1, min(limit, 500)))


@router.post("/inventory/transactions", dependencies=[_write])
def create_transaction(payload: Payload = Body(...), db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)):
    def run():
        t = fs_inventory.move(db, tenant_id, str(payload.get("kind") or "transfer"), str(payload.get("product_name") or ""),
                              float(payload.get("quantity") or 0), source_id=_int(payload.get("source_id")), target_id=_int(payload.get("target_id")),
                              source_status=str(payload.get("source_status") or "good"), target_status=str(payload.get("target_status") or "good"),
                              note=str(payload.get("note") or ""))
        return {"id": t.id}

    return _do(db, run)


@router.post("/inventory/count", dependencies=[_write])
def cycle_count(payload: Payload = Body(...), db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)):
    def run():
        t = fs_inventory.adjust(db, tenant_id, int(payload["location_id"]), str(payload.get("product_name") or ""), float(payload["counted"]),
                                str(payload.get("status") or "good"))
        return {"adjusted": t is not None, "transaction_id": t.id if t else None}

    return _do(db, run)


@router.post("/inventory/replenish", dependencies=[_write])
def replenish(payload: Payload = Body(default={}), db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)):
    return _do(db, lambda: fs_inventory.replenish(db, tenant_id, bool(payload.get("apply")), _int(payload.get("location_id"))))


# --------------------------------------------------------------- repairs

@router.get("/repairs")
def list_repairs(status: str = "open", db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)):
    q = db.query(FsRepair).filter(FsRepair.tenant_id == tenant_id)
    if status == "open":
        q = q.filter(FsRepair.status.in_(fs_inventory.REPAIR_OPEN))
    return [fs_inventory.repair_out(db, r) for r in q.order_by(FsRepair.id.desc()).limit(300).all()]


@router.post("/repairs", dependencies=[_write])
def receive_repair(payload: Payload = Body(...), db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)):
    def run():
        asset_id = _int(payload.get("asset_id"))
        product, serial, party_id, under = str(payload.get("product_name") or ""), str(payload.get("serial_number") or ""), _int(payload.get("party_id")), False
        if asset_id:
            asset = fs._own(db, Asset, asset_id, tenant_id, "Asset")
            product = product or fs.product_of(db, asset)
            detail = fs.asset_detail(db, tenant_id, asset)
            serial = serial or (detail.serial_number if detail else asset.identifier)
            party_id = party_id or fs.party_of_asset(db, asset)
            under = fs.warranty_check(db, tenant_id, asset_id) is not None
        if not product.strip():
            raise ValueError("Which product is being repaired?")
        r = fs_inventory.receive_repair(db, tenant_id, product_name=product, location_id=int(payload.get("location_id") or 0), party_id=party_id,
                                        asset_id=asset_id, ticket_id=_int(payload.get("ticket_id")), serial_number=serial,
                                        symptoms=str(payload.get("symptoms") or ""), kind=str(payload.get("kind") or "internal"),
                                        vendor=str(payload.get("vendor") or ""), under_warranty=under)
        return fs_inventory.repair_out(db, r)

    return _do(db, run)


@router.post("/repairs/{repair_id}/action", dependencies=[_write])
def repair_action(repair_id: int, payload: Payload = Body(...), db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)):
    return _do(db, lambda: fs_inventory.repair_out(db, fs_inventory.repair_action(db, tenant_id, repair_id, str(payload.get("action") or ""),
                                                                                  str(payload.get("vendor") or ""), str(payload.get("resolution") or ""),
                                                                                  _int(payload.get("location_id")))))


# --------------------------------------------------- preventive maintenance

@router.get("/pm/plans")
def list_plans(db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)):
    return [fs_pm.plan_out(db, p) for p in db.query(FsPmPlan).filter(FsPmPlan.tenant_id == tenant_id).order_by(FsPmPlan.priority, FsPmPlan.name).all()]


@router.post("/pm/plans", dependencies=[_write])
def create_plan(payload: Payload = Body(...), db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)):
    return _do(db, lambda: fs_pm.plan_out(db, fs_pm.save_plan(db, tenant_id, payload)))


@router.put("/pm/plans/{plan_id}", dependencies=[_write])
def update_plan(plan_id: int, payload: Payload = Body(...), db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)):
    return _do(db, lambda: fs_pm.plan_out(db, fs_pm.save_plan(db, tenant_id, payload, plan_id)))


@router.post("/pm/run", dependencies=[_write])
def run_pm(payload: Payload = Body(default={}), db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)):
    return _do(db, lambda: fs_pm.run(db, tenant_id, payload.get("until"), _int(payload.get("plan_id")), _int(payload.get("asset_id"))))


@router.get("/pm/actions")
def list_pm_actions(status: str | None = None, db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)):
    return _read(db, lambda: fs_pm.actions(db, tenant_id, status))


"""Mobile subscribers API (crm_core/comm_mobile.py). Everything needs the Mobile page (Admin ->
Roles). Changes to lines go through the intents (INT-129..148) so they are checked, audited and
can trigger Flow Studio flows; these routes read, and keep the tenant's catalog and vouchers.

  /api/mobile/overview                        counts and recent line events
  /api/mobile/lines                           search lines; /lines/{id} - everything about one
  /api/mobile/catalog                         the mobile plans and packs on sale (catalog products) and the policy
  /api/mobile/policy                          PUT: the operating policy (admin)
  /api/mobile/fulfillment-pattern             POST: set up the 'Mobile activation' fulfilment pattern (admin)
  /api/mobile/vouchers/batches                voucher batches; /{batch}/pins - the PINs, once (admin);
                                              /{batch}/block, /{batch}/unblock (admin)
  /api/mobile/partners, /settlements          wholesale partners and their statements
  /api/mobile/housekeeping                    run the scheduler's job now (admin)
"""

from __future__ import annotations

from typing import Any, Callable

from fastapi import APIRouter, Body, Depends, HTTPException
from fastapi.responses import PlainTextResponse
from sqlalchemy.orm import Session

from app.access import require_page
from app.auth import require_roles
from app.crm_core import comm_mobile as mb, comm_service as cs
from app.db import get_db
from app.models.auth import UserRole
from app.models.mobile import BARRINGS, LINE_STATUSES, PACK_KINDS, PARTNER_KINDS, MbLine
from app.tenancy import get_current_tenant_id

router = APIRouter(prefix="/api/mobile", tags=["mobile"], dependencies=[Depends(require_page("mobile"))])
_admin = Depends(require_roles(UserRole.admin))
Payload = dict[str, Any]


def _do(db: Session, fn: Callable[[], Any]) -> Any:
    try:
        result = fn()
        db.commit()
        return result
    except (ValueError, TypeError, KeyError) as exc:
        db.rollback()
        raise HTTPException(400, str(exc).strip("'\"") or "That can't be done") from exc


@router.get("/overview")
def overview(db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)) -> Payload:
    return {**mb.overview(db, tenant_id), "statuses": list(LINE_STATUSES), "barrings": list(BARRINGS)}


@router.get("/lines")
def list_lines(q: str = "", status: str = "", payment_type: str = "", party_id: int | None = None, db: Session = Depends(get_db),
               tenant_id: int = Depends(get_current_tenant_id)) -> list[Payload]:
    return mb.lines(db, tenant_id, q, status, payment_type, party_id)


@router.get("/lines/{line_id}")
def line_detail(line_id: int, db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)) -> Payload:
    line = db.get(MbLine, line_id)
    if line is None or line.tenant_id != tenant_id:
        raise HTTPException(404, "Line not found")
    return mb.line_out(db, line, full=True)


@router.get("/catalog")
def catalog(db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)) -> Payload:
    """The plans and packs on sale (catalog products - Product Modeller) and the operating policy."""
    return {**mb.catalog(db, tenant_id), "policy": mb.settings(db, tenant_id), "pack_kinds": list(PACK_KINDS)}


@router.put("/policy", dependencies=[_admin])
def save_policy(data: Payload = Body(...), db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)) -> Payload:
    """Operating policy only - plans and packs are designed in the Product Modeller."""
    def run() -> Payload:
        current = mb.settings(db, tenant_id)
        unknown = [k for k in data if k not in current]
        if unknown:
            raise ValueError(f"Unknown setting(s): {', '.join(unknown)} - plans and packs are products: design them in the Product Modeller")
        for key, value in data.items():
            if isinstance(current[key], (int, float)) and (isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0):
                raise ValueError(f"{key} must be a number, 0 or more")
            if isinstance(current[key], str) and not isinstance(value, str):
                raise ValueError(f"{key} must be text")
        cs.save_settings(db, tenant_id, {"mobile": {**current, **data}})
        return mb.settings(db, tenant_id)
    return _do(db, run)


@router.post("/sample-catalog", dependencies=[_admin])
def sample_catalog(db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)) -> Payload:
    """Typical mobile plans and packs as catalog products with their product models - to edit in the Product Modeller."""
    return _do(db, lambda: mb.sample_catalog(db, tenant_id))


@router.post("/fulfillment-pattern", dependencies=[_admin])
def mobile_pattern(db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)) -> Payload:
    """Set up (or refresh) the 'Mobile activation' fulfilment pattern for every mobile plan on sale: number and
    SIM from Inventory, network activation, asset, billing - no field work."""
    import json

    from app.models.fulfillment_orders import FulfillmentPattern
    from app.orchestration import fulfillment_engine

    def run() -> Payload:
        plans = [p["name"] for p in mb.catalog(db, tenant_id)["plans"]]
        if not plans:
            raise ValueError("No mobile plans on sale yet - launch one on the 'mobile' product line first")
        row = db.query(FulfillmentPattern).filter(FulfillmentPattern.tenant_id == tenant_id, FulfillmentPattern.name == "Mobile activation").first()
        if row is None:
            row = FulfillmentPattern(tenant_id=tenant_id, name="Mobile activation", order_type="new", priority=200, mode="deterministic")
            db.add(row)
        row.description = "Mobile lines: number and SIM from Inventory, network activation, asset (the line), billing - no field work."
        row.products, row.graph_json, row.is_active = ", ".join(plans), json.dumps(fulfillment_engine.mobile_graph()), True
        db.flush()
        return {"pattern_id": row.id, "name": row.name, "products": plans}
    return _do(db, run)

@router.get("/vouchers/batches")
def batches(db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)) -> list[Payload]:
    return mb.voucher_batches(db, tenant_id)


@router.get("/vouchers/batches/{batch}/pins", dependencies=[_admin], response_class=PlainTextResponse)
def batch_pins(batch: str, db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)) -> PlainTextResponse:
    text = _do(db, lambda: mb.take_pins(db, tenant_id, batch))
    return PlainTextResponse(text, media_type="text/csv", headers={"Content-Disposition": f'attachment; filename="{batch}-pins.csv"',
                                                                   "Cache-Control": "no-store"})


@router.post("/vouchers/batches/{batch}/block", dependencies=[_admin])
def block(batch: str, db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)) -> Payload:
    return {"blocked": _do(db, lambda: mb.block_batch(db, tenant_id, batch, True))}


@router.post("/vouchers/batches/{batch}/unblock", dependencies=[_admin])
def unblock(batch: str, db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)) -> Payload:
    return {"unblocked": _do(db, lambda: mb.block_batch(db, tenant_id, batch, False))}


@router.get("/partners")
def partners(db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)) -> Payload:
    return {"partners": mb.partners(db, tenant_id), "kinds": list(PARTNER_KINDS)}


@router.get("/settlements")
def settlements(db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)) -> list[Payload]:
    return mb.settlements(db, tenant_id)


@router.post("/housekeeping", dependencies=[_admin])
def housekeeping(db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)) -> Payload:
    return _do(db, lambda: mb.housekeeping(db, tenant_id))

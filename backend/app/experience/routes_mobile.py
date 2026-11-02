"""Mobile subscribers API (crm_core/comm_mobile.py). Everything needs the Mobile page (Admin ->
Roles). Changes to lines go through the intents (INT-129..148) so they are checked, audited and
can trigger Flow Studio flows; these routes read, and keep the tenant's catalog and vouchers.

  /api/mobile/overview                        counts and recent line events
  /api/mobile/lines                           search lines; /lines/{id} - everything about one
  /api/mobile/catalog                         plans, packs and policy (PUT: admin)
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
    return {**mb.settings(db, tenant_id), "pack_kinds": list(PACK_KINDS)}


@router.put("/catalog", dependencies=[_admin])
def save_catalog(data: Payload = Body(...), db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)) -> Payload:
    def run() -> Payload:
        current = mb.settings(db, tenant_id)
        unknown = [k for k in data if k not in current]
        if unknown:
            raise ValueError(f"Unknown setting(s): {', '.join(unknown)}")
        mb.check_catalog(data)
        for key, value in data.items():
            if key not in ("plans", "packs") and isinstance(current[key], (int, float)) and not isinstance(value, (int, float)):
                raise ValueError(f"{key} must be a number")
        cs.save_settings(db, tenant_id, {"mobile": {**current, **data}})
        return {**mb.settings(db, tenant_id), "pack_kinds": list(PACK_KINDS)}
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

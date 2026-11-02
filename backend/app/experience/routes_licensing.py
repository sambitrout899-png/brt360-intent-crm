"""Licensing & billing API (crm_core/subscription_service.py).

/api/platform/licensing/*   platform admin: catalog (products, editions, prices), settings, every
                            organization's subscription, lines, seats, status, invoices, requests,
                            history, and the revenue overview.
/api/licensing/*            an organization's administrator: their contract, seats (assign /
                            release), limits and usage, invoices, requests for more.
"""

from __future__ import annotations

from typing import Any, Callable

from fastapi import APIRouter, Body, Depends, HTTPException
from sqlalchemy.orm import Session

from app.auth import get_current_user, require_roles
from app.crm_core import subscription_service as subs
from app.db import get_db
from app.experience.routes_tenants import seed_if_gained
from app.models.auth import User, UserRole
from app.models.subscriptions import LicenseEdition, LicenseInvoice, LicenseRequest
from app.models.tenant import Tenant
from app.tenancy import get_current_tenant_id

platform_router = APIRouter(prefix="/api/platform/licensing", tags=["platform-licensing"],
                            dependencies=[Depends(require_roles(UserRole.platform_admin))])
router = APIRouter(prefix="/api/licensing", tags=["licensing"], dependencies=[Depends(require_roles(UserRole.admin))])
Payload = dict[str, Any]


def _actor(user: User) -> str:
    return f"{user.name} <{user.email}>"


def _do(db: Session, fn: Callable[[], Any]) -> Any:
    try:
        result = fn()
        db.commit()
        return result
    except LookupError as exc:
        db.rollback()
        raise HTTPException(404, str(exc).strip("'\"")) from exc
    except (ValueError, TypeError) as exc:
        db.rollback()
        raise HTTPException(400, str(exc)) from exc


def _sub(db: Session, tenant_id: int):
    sub = subs.subscription(db, tenant_id)
    if sub is None:
        raise LookupError("This organization has no subscription yet")
    return sub


def _synced(db: Session, tenant_id: int, actor: str) -> dict[str, Any]:
    seed_if_gained(db, tenant_id, subs.sync(db, tenant_id, actor))
    return subs.subscription_out(db, _sub(db, tenant_id))


# ================================================================== platform: catalog and settings


@platform_router.get("/catalog")
def catalog(db: Session = Depends(get_db)) -> dict[str, Any]:
    from app.licensing import MODULES

    subs.ensure_catalog(db)
    db.commit()
    conf = subs.settings(db)
    return {"products": [subs.product_out(p) for p in subs.products(db)],
            "editions": [subs.edition_out(db, e) for e in db.query(LicenseEdition).order_by(LicenseEdition.sort_order, LicenseEdition.id)],
            "settings": subs.settings_out(conf), "modules": [{"key": k, **v} for k, v in MODULES.items()],
            "limit_labels": subs.LIMIT_LABELS}


@platform_router.post("/products")
def create_product(payload: Payload = Body(...), db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return _do(db, lambda: subs.product_out(subs.save_product(db, payload, _actor(user))))


@platform_router.put("/products/{code}")
def update_product(code: str, payload: Payload = Body(...), db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return _do(db, lambda: subs.product_out(subs.save_product(db, payload, _actor(user), code)))


@platform_router.post("/editions")
def create_edition(payload: Payload = Body(...), db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return _do(db, lambda: subs.edition_out(db, subs.save_edition(db, payload, _actor(user))))


@platform_router.put("/editions/{code}")
def update_edition(code: str, payload: Payload = Body(...), db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return _do(db, lambda: subs.edition_out(db, subs.save_edition(db, payload, _actor(user), code)))


@platform_router.put("/settings")
def save_settings(payload: Payload = Body(...), db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return _do(db, lambda: subs.settings_out(subs.save_settings(db, payload, _actor(user))))


@platform_router.get("/overview")
def overview(db: Session = Depends(get_db)):
    return subs.overview(db)


@platform_router.get("/tenants")
def tenants(db: Session = Depends(get_db)):
    return subs.tenants_list(db)


# ================================================================== platform: one organization's subscription


@platform_router.get("/tenants/{tenant_id}")
def tenant_subscription(tenant_id: int, db: Session = Depends(get_db)):
    tenant = db.get(Tenant, tenant_id)
    if tenant is None:
        raise HTTPException(404, "Tenant not found")
    view = subs.tenant_view(db, tenant_id)
    sub = subs.subscription(db, tenant_id)
    if sub is not None:
        view["subscription"] = subs.subscription_out(db, sub, internal=True)
    view["events"] = subs.events(db, tenant_id, 200)
    return view


@platform_router.put("/tenants/{tenant_id}")
def save_tenant_subscription(tenant_id: int, payload: Payload = Body(...), db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    def run():
        subs.save_subscription(db, tenant_id, payload, _actor(user))
        return _synced(db, tenant_id, _actor(user))
    return _do(db, run)


@platform_router.post("/tenants/{tenant_id}/edition")
def apply_edition(tenant_id: int, payload: Payload = Body(...), db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    def run():
        subs.apply_edition(db, _sub(db, tenant_id), str(payload.get("edition_code") or ""), _actor(user))
        return _synced(db, tenant_id, _actor(user))
    return _do(db, run)


@platform_router.put("/tenants/{tenant_id}/lines")
def save_line(tenant_id: int, payload: Payload = Body(...), db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    def run():
        subs.save_line(db, _sub(db, tenant_id), payload, _actor(user))
        return _synced(db, tenant_id, _actor(user))
    return _do(db, run)


@platform_router.delete("/tenants/{tenant_id}/lines/{code}")
def remove_line(tenant_id: int, code: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    def run():
        subs.remove_line(db, _sub(db, tenant_id), code, _actor(user))
        return _synced(db, tenant_id, _actor(user))
    return _do(db, run)


@platform_router.post("/tenants/{tenant_id}/status")
def set_status(tenant_id: int, payload: Payload = Body(...), db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    def run():
        subs.set_status(db, _sub(db, tenant_id), str(payload.get("status") or ""), str(payload.get("reason") or ""), _actor(user))
        return _synced(db, tenant_id, _actor(user))
    return _do(db, run)


@platform_router.post("/tenants/{tenant_id}/renew")
def renew(tenant_id: int, payload: Payload = Body(default={}), db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    def run():
        subs.renew(db, _sub(db, tenant_id), _actor(user), int(payload["months"]) if payload.get("months") else None)
        return _synced(db, tenant_id, _actor(user))
    return _do(db, run)


@platform_router.post("/tenants/{tenant_id}/seats/fill")
def fill_seats(tenant_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return _do(db, lambda: {"assigned": subs.auto_fill(db, tenant_id, _actor(user)), "seats": subs.seat_summary(db, tenant_id)})


@platform_router.post("/tenants/{tenant_id}/invoices")
def generate_invoice(tenant_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return _do(db, lambda: subs.invoice_out(subs.generate_invoice(db, _sub(db, tenant_id), _actor(user))))


@platform_router.get("/invoices")
def all_invoices(db: Session = Depends(get_db)):
    names = {t.id: t.display_name or t.name for t in db.query(Tenant).all()}
    return [{**subs.invoice_out(i), "tenant_name": names.get(i.tenant_id, "")}
            for i in db.query(LicenseInvoice).order_by(LicenseInvoice.id.desc()).limit(500)]


@platform_router.post("/invoices/{invoice_id}/status")
def invoice_status(invoice_id: int, payload: Payload = Body(...), db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    def run():
        inv = db.get(LicenseInvoice, invoice_id)
        if inv is None:
            raise LookupError("Invoice not found")
        return subs.invoice_out(subs.set_invoice_status(db, inv, str(payload.get("status") or ""), _actor(user), str(payload.get("payment_ref") or "")))
    return _do(db, run)


@platform_router.get("/requests")
def all_requests(status: str = "open", db: Session = Depends(get_db)):
    names = {t.id: t.display_name or t.name for t in db.query(Tenant).all()}
    catalog = subs.product_map(db)
    q = db.query(LicenseRequest)
    if status != "all":
        q = q.filter(LicenseRequest.status == status)
    return [{**subs.request_out(r, catalog), "tenant_name": names.get(r.tenant_id, "")} for r in q.order_by(LicenseRequest.id.desc()).limit(500)]


@platform_router.post("/requests/{request_id}")
def resolve_request(request_id: int, payload: Payload = Body(...), db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    def run():
        req = db.get(LicenseRequest, request_id)
        if req is None:
            raise LookupError("Request not found")
        subs.resolve_request(db, req, bool(payload.get("approve")), str(payload.get("response") or ""), _actor(user), bool(payload.get("apply", True)))
        seed_if_gained(db, req.tenant_id, subs.sync(db, req.tenant_id, _actor(user)))
        return subs.request_out(req, subs.product_map(db))
    return _do(db, run)


@platform_router.get("/events")
def all_events(db: Session = Depends(get_db)):
    return subs.events(db, None, 300)


# ================================================================== an organization's administrator


@router.get("")
def my_licenses(db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)):
    return subs.tenant_view(db, tenant_id)


@router.post("/seats")
def assign_seat(payload: Payload = Body(...), db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id),
                user: User = Depends(get_current_user)):
    def run():
        subs.assign(db, tenant_id, int(payload.get("user_id") or 0), str(payload.get("product_code") or ""), _actor(user))
        return subs.tenant_view(db, tenant_id)
    return _do(db, run)


@router.delete("/seats/{user_id}/{code}")
def release_seat(user_id: int, code: str, db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id),
                 user: User = Depends(get_current_user)):
    def run():
        subs.unassign(db, tenant_id, user_id, code, _actor(user))
        return subs.tenant_view(db, tenant_id)
    return _do(db, run)


@router.post("/requests")
def request_more(payload: Payload = Body(...), db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id),
                 user: User = Depends(get_current_user)):
    def run():
        subs.create_request(db, tenant_id, payload, _actor(user))
        return subs.tenant_view(db, tenant_id)
    return _do(db, run)

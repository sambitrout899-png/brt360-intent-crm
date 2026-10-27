"""Order Management reads and setup (crm_core/om_service.py).

Changing a quote or an order happens through its intents (INT-51..71) like
every other record change; these routes show the full order/quote and let
an admin set up tax rules, shipping methods, favorites, recommendations,
promotions and customers' credit limits.
"""

from __future__ import annotations

import datetime as dt
import json
from typing import Any, Callable

from fastapi import APIRouter, Body, Depends, HTTPException
from sqlalchemy.orm import Session

from app.access import require_page, visible_parties
from app.auth import require_roles
from app.crm_core import access_service, om_service as om
from app.crm_core.access_service import Access
from app.db import get_db
from app.models.auth import UserRole
from app.models.crm import Party
from app.models.order_mgmt import (
    OmAgreement,
    OmCreditProfile,
    OmFavorite,
    OmPromotion,
    OmRecommendation,
    OmShippingMethod,
    OmTaxRule,
)
from app.models.orders import Order
from app.tenancy import get_current_tenant_id

router = APIRouter(prefix="/api/order-management", tags=["order-management"])
admin_router = APIRouter(prefix="/api/admin/order-management", tags=["admin"], dependencies=[Depends(require_roles(UserRole.admin))])
Payload = dict[str, Any]


def _read(db: Session, fn: Callable[[], Any]) -> Any:
    try:
        out = fn()
        db.commit()  # views may fill in details for older records
        return out
    except ValueError as exc:
        db.rollback()
        raise HTTPException(404, str(exc)) from exc


def _do(db: Session, fn: Callable[[], Any]) -> Any:
    try:
        out = fn()
        db.commit()
        return out
    except (ValueError, TypeError, KeyError) as exc:
        db.rollback()
        raise HTTPException(400, str(exc).strip("'\"") or "That can't be done") from exc


# the pages that show orders and quotes (Admin -> Roles); records follow the caller's data scope
_PAGES = require_page("orders", "pipeline", "customer")


def _visible(db: Session, access: Access, entity_type: str, entity_id: int, label: str) -> None:
    if not access_service.can_see(db, access, entity_type, entity_id):
        raise ValueError(f"{label} not found")


@router.get("/orders/{order_id}")
def order_view(order_id: int, db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id), access: Access = Depends(_PAGES)):
    def run():
        order = om.order_of(db, tenant_id, order_id)
        _visible(db, access, "order", order.id, "Order")
        return om.order_view(db, tenant_id, order)

    return _read(db, run)


@router.get("/orders/{order_id}/channels")
def order_channels(order_id: int, db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id), access: Access = Depends(_PAGES)):
    """A B2C order's omnichannel timeline - every touch, on every channel."""
    from app.crm_core import b2c_order_service as b2c

    def run():
        order = om.order_of(db, tenant_id, order_id)
        _visible(db, access, "order", order.id, "Order")
        return b2c.timeline(db, tenant_id, order_id=order.id)

    return _read(db, run)


@admin_router.get("/b2c-journey")
def b2c_journey(db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)):
    """The B2C order journey switch (vanilla: B2C goes straight from prospect to order) and its channels."""
    from app.crm_core import b2c_order_service as b2c

    return {"settings": b2c.settings(db, tenant_id), "channels": [{"key": k, "label": v} for k, v in b2c.CHANNELS.items()]}


@admin_router.put("/b2c-journey")
def save_b2c_journey(payload: Payload = Body(...), db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)):
    from app.crm_core import b2c_order_service as b2c

    out = _do(db, lambda: b2c.save(db, tenant_id, payload))
    return {"settings": out, "channels": [{"key": k, "label": v} for k, v in b2c.CHANNELS.items()]}


@router.get("/quotes/{quote_id}")
def quote_view(quote_id: int, db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id), access: Access = Depends(_PAGES)):
    def run():
        quote = om.quote_of(db, tenant_id, quote_id)
        _visible(db, access, "quote", quote.id, "Quote")
        return om.quote_view(db, tenant_id, quote)

    return _read(db, run)


@router.get("/orders")
def order_summaries(db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id), access: Access = Depends(_PAGES)):
    """Order number, sales-order status and money for every order (the Orders page adds these to its cards)."""
    def run():
        out = {}
        q = access_service.apply(db.query(Order).filter(Order.tenant_id == tenant_id), Order, db, access, "order")
        for order in q.order_by(Order.id.desc()).limit(500).all():
            d = om.order_detail(db, tenant_id, order)
            t = om.totals(db, tenant_id, "order", order)
            out[order.id] = {"order_number": d.order_number, "revision": d.revision, "om_status": d.om_status, "hold": d.hold,
                             "lines": t["lines"], "due_now": t["due_now"], "recurring": t["recurring"], "requested_date": d.requested_date,
                             "follow_on_of": d.source_order_id}
        return out

    return _read(db, run)


@router.get("/opportunities/{opportunity_id}/revenue")
def revenue(opportunity_id: int, db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id),
            access: Access = Depends(_PAGES)):
    if not access_service.can_see(db, access, "opportunity", opportunity_id):
        raise HTTPException(404, "Opportunity not found")
    return om.opportunity_revenue(db, tenant_id, opportunity_id)


@router.get("/agreements")
def agreements(party_id: int | None = None, db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id),
               access: Access = Depends(_PAGES)):
    q = db.query(OmAgreement).filter(OmAgreement.tenant_id == tenant_id)
    parties = visible_parties(db, access)
    if parties is not None:
        q = q.filter(OmAgreement.party_id.in_(parties or {-1}))
    if party_id:
        q = q.filter(OmAgreement.party_id == party_id)
    out = []
    for a in q.order_by(OmAgreement.id.desc()).all():
        party = db.get(Party, a.party_id)
        out.append({"id": a.id, "agreement_number": a.agreement_number, "name": a.name, "party_id": a.party_id,
                    "party_name": party.name if party else None, "quote_id": a.quote_id, "status": a.status, "start_date": a.start_date,
                    "end_date": a.end_date, "term_months": a.term_months, "monthly_value": a.monthly_value, "one_time_value": a.one_time_value,
                    "response_hours": a.response_hours, "coverage": a.coverage, "payment_terms": a.payment_terms,
                    "entitlement_id": a.entitlement_id, "lines": json.loads(a.lines_json or "[]")})
    return out


@router.get("/catalog", dependencies=[Depends(_PAGES)])
def catalog(db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)):
    """What sellers pick from: favorites, active promotions and shipping methods."""
    return {
        "favorites": [{"name": f.name, "items": json.loads(f.items_json or "[]")}
                      for f in db.query(OmFavorite).filter(OmFavorite.tenant_id == tenant_id, OmFavorite.active.is_(True))],
        "promotions": [{"name": p.name, "code": p.code, "discount_pct": p.discount_pct, "commitment_months": p.commitment_months}
                       for p in db.query(OmPromotion).filter(OmPromotion.tenant_id == tenant_id, OmPromotion.active.is_(True))],
        "shipping_methods": [{"name": m.name, "carrier": m.carrier, "days": m.days}
                             for m in db.query(OmShippingMethod).filter(OmShippingMethod.tenant_id == tenant_id, OmShippingMethod.active.is_(True))],
    }


# ------------------------------------------------------------------ admin setup

_SETUP: dict[str, tuple[type, list[str]]] = {
    "tax-rules": (OmTaxRule, ["jurisdiction", "name", "rate_pct", "applies_to", "active"]),
    "shipping-methods": (OmShippingMethod, ["name", "carrier", "base_charge", "per_unit", "free_over", "days", "active"]),
    "favorites": (OmFavorite, ["name", "items", "active"]),
    "recommendations": (OmRecommendation, ["product", "recommended_product", "kind", "score", "message", "active"]),
    "promotions": (OmPromotion, ["name", "code", "products", "discount_pct", "discount_amount", "commitment_months",
                                 "early_termination_fee", "valid_from", "valid_to", "active"]),
}


def _row_out(kind: str, row: Any) -> dict[str, Any]:
    _, fields = _SETUP[kind]
    out = {"id": row.id}
    for f in fields:
        if f == "items":
            out["items"] = json.loads(row.items_json or "[]")
        elif f == "products":
            out["products"] = [p.strip() for p in (row.products or "").split(",") if p.strip()]
        else:
            out[f] = getattr(row, f)
    return out


def _apply(kind: str, row: Any, data: Payload) -> None:
    _, fields = _SETUP[kind]
    for f in fields:
        if f not in data:
            continue
        v = data[f]
        if f == "items":
            row.items_json = json.dumps([{"product": str(i.get("product")).strip(), "quantity": float(i.get("quantity") or 1)}
                                         for i in (v or []) if i.get("product")])
        elif f == "products":
            row.products = ",".join(str(p).strip() for p in (v or []) if str(p).strip())
        elif f in ("valid_from", "valid_to"):
            setattr(row, f, dt.date.fromisoformat(str(v)[:10]) if v else None)
        elif f == "free_over":
            row.free_over = float(v) if v not in (None, "") else None
        elif f in ("rate_pct", "base_charge", "per_unit", "score", "discount_pct", "discount_amount", "early_termination_fee"):
            setattr(row, f, float(v or 0))
        elif f in ("days", "commitment_months"):
            setattr(row, f, int(v or 0))
        elif f == "active":
            row.active = bool(v)
        else:
            setattr(row, f, str(v or "").strip())
    if kind == "tax-rules" and row.applies_to not in ("all", "service", "equipment", "one_time", "recurring"):
        raise ValueError("A tax applies to all, service, equipment, one_time or recurring")
    if kind == "recommendations" and row.kind not in ("cross_sell", "up_sell"):
        raise ValueError("A recommendation is cross_sell or up_sell")
    if not (getattr(row, "name", None) or getattr(row, "product", None)):
        raise ValueError("Give it a name")


@admin_router.get("/{kind}")
def list_setup(kind: str, db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)):
    if kind not in _SETUP:
        raise HTTPException(404, "Unknown setup list")
    model, _ = _SETUP[kind]
    return [_row_out(kind, r) for r in db.query(model).filter(model.tenant_id == tenant_id).order_by(model.id).all()]


@admin_router.post("/{kind}")
def create_setup(kind: str, payload: Payload = Body(...), db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)):
    if kind not in _SETUP:
        raise HTTPException(404, "Unknown setup list")
    model, _ = _SETUP[kind]

    def run():
        row = model(tenant_id=tenant_id)
        if kind == "tax-rules":
            row.applies_to, row.jurisdiction = "all", "ALL"
        if kind == "recommendations":
            row.kind = "cross_sell"
        _apply(kind, row, payload)
        db.add(row)
        db.flush()
        return _row_out(kind, row)

    return _do(db, run)


@admin_router.put("/{kind}/{row_id}")
def update_setup(kind: str, row_id: int, payload: Payload = Body(...), db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)):
    if kind not in _SETUP:
        raise HTTPException(404, "Unknown setup list")
    model, _ = _SETUP[kind]
    row = db.get(model, row_id)
    if row is None or row.tenant_id != tenant_id:
        raise HTTPException(404, "Not found")
    return _do(db, lambda: (_apply(kind, row, payload), _row_out(kind, row))[1])


@admin_router.delete("/{kind}/{row_id}")
def delete_setup(kind: str, row_id: int, db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)):
    if kind not in _SETUP:
        raise HTTPException(404, "Unknown setup list")
    model, _ = _SETUP[kind]
    row = db.get(model, row_id)
    if row is None or row.tenant_id != tenant_id:
        raise HTTPException(404, "Not found")
    db.delete(row)
    db.commit()
    return {"deleted": row_id}


@admin_router.get("/credit/profiles")
def credit_profiles(db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)):
    out = []
    for p in db.query(OmCreditProfile).filter(OmCreditProfile.tenant_id == tenant_id).all():
        party = db.get(Party, p.party_id)
        out.append({"party_id": p.party_id, "party_name": party.name if party else None, "credit_limit": p.credit_limit,
                    "payment_terms": p.payment_terms, "on_hold": p.on_hold, "rating": p.rating,
                    "exposure": om.exposure(db, tenant_id, p.party_id)})
    return out


@admin_router.put("/credit/profiles/{party_id}")
def save_credit_profile(party_id: int, payload: Payload = Body(...), db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)):
    party = db.get(Party, party_id)
    if party is None or party.tenant_id != tenant_id:
        raise HTTPException(404, "Customer not found")

    def run():
        p = om.credit_profile(db, tenant_id, party_id) or OmCreditProfile(tenant_id=tenant_id, party_id=party_id)
        db.add(p)
        if payload.get("credit_limit") is not None:
            p.credit_limit = max(0.0, float(payload["credit_limit"]))
        if payload.get("payment_terms") is not None:
            p.payment_terms = str(payload["payment_terms"])[:50]
        if payload.get("on_hold") is not None:
            p.on_hold = bool(payload["on_hold"])
        if payload.get("rating") is not None:
            p.rating = str(payload["rating"])[:20]
        db.flush()
        return {"party_id": party_id, "credit_limit": p.credit_limit, "payment_terms": p.payment_terms, "on_hold": p.on_hold,
                "exposure": om.exposure(db, tenant_id, party_id)}

    return _do(db, run)


@admin_router.post("/setup/sample")
def sample(db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)):
    return _do(db, lambda: om.sample_setup(db, tenant_id))


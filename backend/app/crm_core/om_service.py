"""Order Management - quotes and sales orders through their whole life
(Siebel Order Management, chapters 14-17).

  pre-order     quote lines (catalog, equipment, write-ins, favorites),
                recommendations, promotions, manual discounts, quote
                verification and expiry, update the opportunity, AutoContract
  order entry   lines carried from the quote, availability to promise (with
                line splitting), ship-to and freight, tax, payment (card
                authorisation, purchase order, on account), credit check,
                verification, submit (approval above discount limits,
                acknowledgement, stock reservation, exploding trackable lines)
  post-order    hold / release, cancel, supplemental order (revise an order in
                flight, action codes Add / Update / Delete / -), follow-on
                order after the point of no return, shipments and delivery
                (trackable lines become assets), returns (RMA into the repair
                loop, refund), activity plans (field activities), resume a
                suspended service, order status

A quote's or order's own columns stay the source of truth for its main
product; om_lines mirror that product and hold every other line.
"""

from __future__ import annotations

import datetime as dt
import json
import re
from typing import Any

from sqlalchemy.orm import Session

from app.models.catalog import OfferingStatus, ProductOffering
from app.models.crm import Opportunity, Party, Quote, QuoteItem, QuoteStatus
from app.models.order_mgmt import (
    OmAgreement,
    OmCreditCheck,
    OmCreditProfile,
    OmFavorite,
    OmLine,
    OmLineChange,
    OmOpportunityRevenue,
    OmOrderDetail,
    OmOrderRevision,
    OmPayment,
    OmPromotion,
    OmQuoteDetail,
    OmRecommendation,
    OmShipment,
    OmShipmentLine,
    OmShippingMethod,
    OmTaxRule,
)
from app.models.orders import BillingAccount, Order, OrderItem, OrderStatus, OrderType
from app.timeutils import utcnow

QUOTE_VALID_DAYS = 30
APPROVAL_DISCOUNT_PCT = 20.0  # a line discount above this sends the order for approval
JUSTIFY_DISCOUNT_PCT = 10.0  # above this a reason is needed
BACKORDER_DAYS = 14
OPEN_OM = ("booked", "awaiting_shipping", "partially_shipped")
CANCELLABLE = ("pending", "awaiting_approval", "booked", "awaiting_shipping")
PAST_PONR_OM = ("partially_shipped", "shipped", "delivered", "fulfilled", "cancel_pending", "cancelled")
LIVE = (OrderStatus.active, OrderStatus.changed, OrderStatus.suspended)
# the order's service is in place - a completed order's lives on as its asset
SERVICE_IN_PLACE = LIVE + (OrderStatus.completed,)
ACTIONS = ("Add", "Update", "Delete", "-", "Suspend", "Resume")
CARD_NUMBER = re.compile(r"(?:\d[ -]?){12,19}")


def _key(text: Any) -> str:
    return re.sub(r"[^a-z0-9]", "", str(text or "").lower())


def _r(x: float) -> float:
    return round(float(x or 0), 2)


def _date(value: Any) -> dt.date | None:
    if value in (None, ""):
        return None
    if isinstance(value, dt.datetime):
        return value.date()
    if isinstance(value, dt.date):
        return value
    text = str(value).strip().lower()
    today = utcnow().date()
    if text == "today":
        return today
    if text == "tomorrow":
        return today + dt.timedelta(days=1)
    if m := re.match(r"in\s+(\d+)\s+(day|week)s?", text):
        return today + dt.timedelta(days=int(m.group(1)) * (7 if m.group(2) == "week" else 1))
    try:
        return dt.date.fromisoformat(text[:10])
    except ValueError as exc:
        raise ValueError(f"'{value}' is not a date - use YYYY-MM-DD") from exc


def _j(text: str | None, default: Any) -> Any:
    try:
        v = json.loads(text or "")
        return v if isinstance(v, type(default)) else default
    except (ValueError, TypeError):
        return default


# ------------------------------------------------------------------ records

def quote_of(db: Session, tenant_id: int, quote_id: Any) -> Quote:
    q = db.get(Quote, int(quote_id or 0))
    if q is None or q.tenant_id != tenant_id:
        raise ValueError(f"Quote {quote_id} not found")
    return q


def order_of(db: Session, tenant_id: int, order_id: Any) -> Order:
    o = db.get(Order, int(order_id or 0))
    if o is None or o.tenant_id != tenant_id:
        raise ValueError(f"Order {order_id} not found")
    return o


def party_of_quote(db: Session, quote: Quote) -> Party | None:
    opp = db.get(Opportunity, quote.opportunity_id)
    lead = opp.lead if opp is not None else None
    return db.get(Party, lead.party_id) if lead is not None and lead.party_id else None


def context(db: Session, tenant_id: int, entities: dict[str, Any]) -> tuple[str, Any]:
    """The quote or order an intent works on (order wins when both are given)."""
    if entities.get("order_id"):
        return "order", order_of(db, tenant_id, entities["order_id"])
    if entities.get("quote_id"):
        return "quote", quote_of(db, tenant_id, entities["quote_id"])
    raise ValueError("Pick the quote or the order")


def quote_detail(db: Session, tenant_id: int, quote: Quote) -> OmQuoteDetail:
    d = db.query(OmQuoteDetail).filter(OmQuoteDetail.quote_id == quote.id).first()
    if d is None:
        start = (quote.created_at or utcnow()).date()
        d = OmQuoteDetail(tenant_id=tenant_id, quote_id=quote.id, quote_number=f"Q-{quote.id:06d}", effective_from=start,
                          effective_through=start + dt.timedelta(days=QUOTE_VALID_DAYS))
        db.add(d)
        db.flush()
    return d


_CORE_TO_OM = {
    OrderStatus.created: "pending", OrderStatus.pending_approval: "awaiting_approval", OrderStatus.rejected: "cancelled",
    OrderStatus.activating: "booked", OrderStatus.active: "fulfilled", OrderStatus.changed: "fulfilled", OrderStatus.completed: "fulfilled",
    OrderStatus.suspended: "fulfilled", OrderStatus.pending_cancellation: "fulfilled", OrderStatus.cancelled: "cancelled",
    OrderStatus.terminated: "cancelled",
}


def order_detail(db: Session, tenant_id: int, order: Order) -> OmOrderDetail:
    d = db.query(OmOrderDetail).filter(OmOrderDetail.order_id == order.id).first()
    if d is None:
        profile = db.query(OmCreditProfile).filter(OmCreditProfile.party_id == order.party_id).first()
        d = OmOrderDetail(tenant_id=tenant_id, order_id=order.id, order_number=f"SO-{order.id:06d}",
                          om_status=_CORE_TO_OM.get(order.status, "pending"), payment_terms=profile.payment_terms if profile else "")
        db.add(d)
        db.flush()
    elif order.status in (OrderStatus.cancelled, OrderStatus.terminated, OrderStatus.rejected) and d.om_status not in ("cancelled",):
        d.om_status = "cancelled"
    elif d.om_status in ("booked", "delivered") and order.status in SERVICE_IN_PLACE:
        d.om_status = "fulfilled"  # nothing left to ship and the service is live
    return d


def party_id_of(db: Session, ctx_type: str, rec: Any) -> int | None:
    if ctx_type == "order":
        return rec.party_id
    p = party_of_quote(db, rec)
    return p.id if p else None


# ------------------------------------------------------------------- lines

def _offering_name(db: Session, offering_id: int) -> str:
    o = db.get(ProductOffering, offering_id)
    return o.name if o else f"Product {offering_id}"


def lines(db: Session, tenant_id: int, ctx_type: str, rec: Any, include_deleted: bool = True) -> list[OmLine]:
    """Every line of the quote/order - the main product mirrored from the record itself."""
    rows = db.query(OmLine).filter(OmLine.tenant_id == tenant_id, OmLine.context_type == ctx_type, OmLine.context_id == rec.id) \
        .order_by(OmLine.line_number, OmLine.id).all()
    fresh = not rows
    core_items = list(rec.items)
    for item in core_items:
        mirror = next((r for r in rows if r.core_item_id == item.id), None)
        qty = item.quantity or 1
        if ctx_type == "quote":
            unit_net = _r(rec.total_price / qty) if len(core_items) == 1 else _r(item.unit_price * (1 - (rec.requested_discount_pct or 0) / 100))
            list_price = item.unit_price
        else:
            unit_net = _r((rec.monthly_price or 0) / qty) if len(core_items) == 1 else 0.0
            list_price = unit_net
        if mirror is None:
            mirror = OmLine(tenant_id=tenant_id, context_type=ctx_type, context_id=rec.id, core_item_id=item.id,
                            line_number=len(rows) + 1, product_name=_offering_name(db, item.product_offering_id),
                            product_offering_id=item.product_offering_id, kind="service", price_type="recurring",
                            quantity=qty, list_price=list_price, net_price=unit_net,
                            discount_pct=(rec.requested_discount_pct or 0) if ctx_type == "quote" else 0.0,
                            action_code="Add" if getattr(rec, "order_type", None) in (None, OrderType.new) else "Update",
                            status="open" if ctx_type == "order" else "pending", integration_id=f"SVC-{rec.id}-{item.id}")
            db.add(mirror)
            rows.append(mirror)
        elif mirror.action_code != "Delete":
            mirror.quantity, mirror.net_price = qty, unit_net
            if ctx_type == "quote":
                mirror.list_price, mirror.discount_pct = list_price, rec.requested_discount_pct or 0
    if fresh and ctx_type == "order" and rec.quote_id:
        # the quote's other lines come with it to the order
        for q in db.query(OmLine).filter(OmLine.context_type == "quote", OmLine.context_id == rec.quote_id,
                                         OmLine.core_item_id.is_(None), OmLine.action_code != "Delete").order_by(OmLine.line_number).all():
            rows.append(_copy_line(db, q, "order", rec.id, len(rows) + 1))
    db.flush()
    return [r for r in rows if include_deleted or r.action_code != "Delete"]


def _copy_line(db: Session, src: OmLine, ctx_type: str, ctx_id: int, number: int, action: str | None = None) -> OmLine:
    row = OmLine(tenant_id=src.tenant_id, context_type=ctx_type, context_id=ctx_id, line_number=number, product_name=src.product_name,
                 product_offering_id=src.product_offering_id, kind=src.kind, price_type=src.price_type, quantity=src.quantity,
                 list_price=src.list_price, discount_pct=src.discount_pct, discount_amount=src.discount_amount,
                 discount_reason=src.discount_reason, net_price=src.net_price, action_code=action or src.action_code,
                 status="pending", requested_date=src.requested_date, forecastable=src.forecastable, trackable=src.trackable,
                 integration_id=src.integration_id, revised_line_id=src.id, commitment_months=src.commitment_months, note=src.note)
    db.add(row)
    db.flush()
    return row


def _history(db: Session, line: OmLine, revision: int, field: str, old: Any, new: Any) -> None:
    if str(old) == str(new):
        return
    db.add(OmLineChange(tenant_id=line.tenant_id, line_id=line.id, context_type=line.context_type, context_id=line.context_id,
                        revision=revision, field=field, old_value=str(old if old is not None else "")[:300],
                        new_value=str(new if new is not None else "")[:300]))


def _revision(db: Session, ctx_type: str, rec: Any) -> int:
    if ctx_type == "order":
        return order_detail(db, rec.tenant_id, rec).revision
    return rec.revision_number or 1


def _reprice(line: OmLine) -> None:
    gross = line.list_price
    net = gross * (1 - (line.discount_pct or 0) / 100) - (line.discount_amount or 0)
    line.net_price = _r(max(net, 0.0))


def find_line(db: Session, tenant_id: int, ctx_type: str, rec: Any, ref: Any) -> OmLine:
    rows = lines(db, tenant_id, ctx_type, rec, include_deleted=False)
    text = str(ref or "").strip()
    if not text:
        if len(rows) == 1:
            return rows[0]
        raise ValueError("Which line? Give its product or line number")
    if text.isdigit():
        hit = next((r for r in rows if r.line_number == int(text) or r.id == int(text)), None)
        if hit:
            return hit
    hits = [r for r in rows if _key(r.product_name) == _key(text)] or [r for r in rows if _key(text) in _key(r.product_name)]
    if not hits:
        raise ValueError(f"No line '{text}' on this {ctx_type} - lines: {', '.join(r.product_name for r in rows) or 'none'}")
    return hits[0]


def editable(db: Session, tenant_id: int, ctx_type: str, rec: Any) -> None:
    """Lines may be changed in place only before submission."""
    if ctx_type == "quote":
        if rec.status != QuoteStatus.draft:
            raise ValueError(f"Quote {rec.id} is {rec.status.value} - make a new revision to change its lines")
        return
    d = order_detail(db, tenant_id, rec)
    if d.om_status not in ("pending",):
        raise ValueError(f"Order {d.order_number} is {d.om_status.replace('_', ' ')} - revise it with a supplemental order instead")


def resolve_product(db: Session, tenant_id: int, ctx_type: str, rec: Any, product: str, unit_price: Any = None,
                    price_type: str | None = None, quantity: Any = 1) -> dict[str, Any]:
    """A catalog product (priced from the customer's pricelist - its volume
    tier for the quantity), stocked equipment, or a write-in."""
    from app.crm_core import fs_inventory, product_config_service as pc, product_model_service as pm

    name = str(product or "").strip()
    if not name:
        raise ValueError("Which product?")
    offering = pm.offering_of(db, tenant_id, name)
    if offering is not None and offering.status == OfferingStatus.active:
        party = db.get(Party, party_id_of(db, ctx_type, rec) or 0)
        try:
            price_list = pc.price_list_for(db, tenant_id, party)
            base, _ = pc.base_price(db, tenant_id, price_list, offering, quantity)
        except Exception:  # noqa: BLE001 - fall back to the catalog price
            base = offering.price
        price = float(unit_price) if unit_price not in (None, "") else base
        return {"product_name": offering.name, "product_offering_id": offering.id, "kind": "service",
                "price_type": price_type or "recurring", "list_price": price, "trackable": False}
    stocked = fs_inventory.unit_price(db, tenant_id, name)
    from app.models.field_service import FsInventoryLevel

    level = db.query(FsInventoryLevel).filter(FsInventoryLevel.tenant_id == tenant_id, FsInventoryLevel.product_name.ilike(name)).first()
    if level is not None:
        price = float(unit_price) if unit_price not in (None, "") else stocked
        return {"product_name": level.product_name, "product_offering_id": None, "kind": "equipment", "price_type": price_type or "one_time",
                "list_price": price, "trackable": True}
    if unit_price in (None, ""):
        raise ValueError(f"'{name}' is not in the catalog or in stock - give a price to add it as a write-in")
    return {"product_name": name, "product_offering_id": None, "kind": "write_in", "price_type": price_type or "one_time",
            "list_price": float(unit_price), "trackable": False}


def add_line(db: Session, tenant_id: int, ctx_type: str, rec: Any, product: str, quantity: Any = 1, unit_price: Any = None,
             price_type: str | None = None, requested_date: Any = None, note: str = "", check: bool = True,
             action: str = "Add") -> OmLine:
    if check:
        editable(db, tenant_id, ctx_type, rec)
    qty = float(quantity or 1)
    if qty <= 0:
        raise ValueError("Quantity must be more than zero")
    if price_type and price_type not in ("recurring", "one_time", "usage"):
        raise ValueError("Price type is recurring, one_time or usage")
    spec = resolve_product(db, tenant_id, ctx_type, rec, product, unit_price, price_type, qty)
    rows = lines(db, tenant_id, ctx_type, rec)
    line = OmLine(tenant_id=tenant_id, context_type=ctx_type, context_id=rec.id, line_number=max([r.line_number for r in rows] or [0]) + 1,
                  quantity=qty, net_price=spec["list_price"], action_code=action, requested_date=_date(requested_date), note=note[:300],
                  status="pending", **spec)
    db.add(line)
    db.flush()
    line.integration_id = f"{'EQP' if line.kind == 'equipment' else 'LIN'}-{rec.id}-{line.id}"
    _history(db, line, _revision(db, ctx_type, rec), "line", "", f"added {qty:g} x {line.product_name}")
    db.flush()
    return line


def add_favorite(db: Session, tenant_id: int, ctx_type: str, rec: Any, name: str) -> list[OmLine]:
    fav = db.query(OmFavorite).filter(OmFavorite.tenant_id == tenant_id, OmFavorite.name.ilike(str(name).strip()), OmFavorite.active.is_(True)).first()
    if fav is None:
        names = [f.name for f in db.query(OmFavorite).filter(OmFavorite.tenant_id == tenant_id, OmFavorite.active.is_(True))]
        raise ValueError(f"No favorite '{name}' - favorites: {', '.join(names) or 'none yet'}")
    return [add_line(db, tenant_id, ctx_type, rec, i.get("product"), i.get("quantity") or 1) for i in _j(fav.items_json, [])]


def remove_line(db: Session, tenant_id: int, ctx_type: str, rec: Any, ref: Any) -> OmLine:
    editable(db, tenant_id, ctx_type, rec)
    line = find_line(db, tenant_id, ctx_type, rec, ref)
    if line.core_item_id:
        raise ValueError("The main product line stays - change or cancel the record itself")
    _history(db, line, _revision(db, ctx_type, rec), "line", f"{line.quantity:g} x {line.product_name}", "removed")
    line.action_code, line.status = "Delete", "cancelled"
    db.flush()
    return line


def discount_line(db: Session, tenant_id: int, ctx_type: str, rec: Any, ref: Any, pct: Any = None, amount: Any = None,
                  justification: str = "") -> OmLine:
    editable(db, tenant_id, ctx_type, rec)
    line = find_line(db, tenant_id, ctx_type, rec, ref)
    if line.core_item_id:
        raise ValueError("The main product's discount is set on the quote itself (revise the quote's discount)")
    pct_v = float(pct) if pct not in (None, "") else 0.0
    amt_v = float(amount) if amount not in (None, "") else 0.0
    if pct_v < 0 or pct_v > 100 or amt_v < 0:
        raise ValueError("A discount is 0-100% or a positive amount")
    if not pct_v and not amt_v:
        raise ValueError("Give a discount % or an amount")
    effective = pct_v if pct_v else (100 * amt_v / line.list_price if line.list_price else 0)
    if effective > JUSTIFY_DISCOUNT_PCT and not str(justification or "").strip():
        raise ValueError(f"A discount above {JUSTIFY_DISCOUNT_PCT:g}% needs a reason")
    rev = _revision(db, ctx_type, rec)
    old = line.net_price
    line.discount_pct, line.discount_amount, line.discount_reason = pct_v, amt_v, str(justification or "")[:300]
    _reprice(line)
    _history(db, line, rev, "net_price", old, line.net_price)
    db.flush()
    return line


def line_out(line: OmLine) -> dict[str, Any]:
    return {"id": line.id, "line_number": line.line_number, "product_name": line.product_name, "kind": line.kind,
            "price_type": line.price_type, "quantity": line.quantity, "list_price": line.list_price, "discount_pct": line.discount_pct,
            "discount_amount": line.discount_amount, "discount_reason": line.discount_reason, "net_price": line.net_price,
            "extended": _r(line.net_price * line.quantity), "action_code": line.action_code, "status": line.status,
            "requested_date": line.requested_date, "atp_status": line.atp_status, "atp_date": line.atp_date,
            "shipped_qty": line.shipped_qty, "forecastable": line.forecastable, "trackable": line.trackable, "asset_id": line.asset_id,
            "integration_id": line.integration_id, "main": bool(line.core_item_id), "commitment_months": line.commitment_months,
            "note": line.note, "revised_line_id": line.revised_line_id}


def totals(db: Session, tenant_id: int, ctx_type: str, rec: Any) -> dict[str, Any]:
    rows = lines(db, tenant_id, ctx_type, rec, include_deleted=False)
    recurring = _r(sum(r.net_price * r.quantity for r in rows if r.price_type == "recurring"))
    one_time = _r(sum(r.net_price * r.quantity for r in rows if r.price_type == "one_time"))
    usage = _r(sum(r.net_price * r.quantity for r in rows if r.price_type == "usage"))
    discount = _r(sum((r.list_price - r.net_price) * r.quantity for r in rows if not r.core_item_id))
    freight = tax = tax_recurring = 0.0
    if ctx_type == "order":
        d = order_detail(db, tenant_id, rec)
        freight, tax, tax_recurring = d.freight, d.tax_amount, d.tax_recurring
    return {"recurring": recurring, "one_time": one_time, "usage": usage, "line_discounts": discount, "freight": _r(freight),
            "tax": _r(tax), "tax_recurring": _r(tax_recurring), "due_now": _r(one_time + freight + tax),
            "monthly_with_tax": _r(recurring + tax_recurring), "lines": len(rows)}


def recommendations(db: Session, tenant_id: int, ctx_type: str, rec: Any) -> list[dict[str, Any]]:
    have = {_key(r.product_name) for r in lines(db, tenant_id, ctx_type, rec, include_deleted=False)}
    out = []
    for r in db.query(OmRecommendation).filter(OmRecommendation.tenant_id == tenant_id, OmRecommendation.active.is_(True)) \
            .order_by(OmRecommendation.score.desc()).all():
        if _key(r.product) in have and _key(r.recommended_product) not in have:
            out.append({"product": r.recommended_product, "kind": r.kind, "because": r.product, "score": r.score,
                        "message": r.message or f"Customers buying {r.product} often add {r.recommended_product}"})
    return out[:5]


# ------------------------------------------------------------ promotions

def apply_promotion(db: Session, tenant_id: int, ctx_type: str, rec: Any, promotion: str) -> dict[str, Any]:
    editable(db, tenant_id, ctx_type, rec)
    key = str(promotion or "").strip()
    promo = db.query(OmPromotion).filter(OmPromotion.tenant_id == tenant_id, OmPromotion.active.is_(True)) \
        .filter((OmPromotion.code.ilike(key)) | (OmPromotion.name.ilike(key))).first()
    if promo is None:
        raise ValueError(f"No active promotion '{key}'")
    today = utcnow().date()
    if (promo.valid_from and today < promo.valid_from) or (promo.valid_to and today > promo.valid_to):
        raise ValueError(f"Promotion {promo.name} is not valid today")
    rows = lines(db, tenant_id, ctx_type, rec, include_deleted=False)
    needed = [p.strip() for p in (promo.products or "").split(",") if p.strip()]
    missing = [p for p in needed if not any(_key(p) == _key(r.product_name) for r in rows)]
    if missing:
        raise ValueError(f"Promotion {promo.name} needs {', '.join(missing)} on the {ctx_type} - add them first")
    targets = [r for r in rows if not r.core_item_id and (not needed or any(_key(p) == _key(r.product_name) for p in needed))]
    rev = _revision(db, ctx_type, rec)
    for line in targets:
        old = line.net_price
        line.discount_pct = max(line.discount_pct, promo.discount_pct)
        line.discount_amount = max(line.discount_amount, promo.discount_amount)
        line.discount_reason = f"Promotion: {promo.name}"
        line.commitment_months = max(line.commitment_months, promo.commitment_months)
        _reprice(line)
        _history(db, line, rev, "net_price", old, line.net_price)
    detail = quote_detail(db, tenant_id, rec) if ctx_type == "quote" else order_detail(db, tenant_id, rec)
    detail.promotion_id = promo.id
    db.flush()
    return {"promotion": promo.name, "lines": [line_out(t) for t in targets], "commitment_months": promo.commitment_months,
            "early_termination_fee": promo.early_termination_fee}


# ------------------------------------------------------- availability (ATP)

def _stock_locations(db: Session, tenant_id: int, product: str) -> list[tuple[Any, float]]:
    from app.models.field_service import FsInventoryLevel, FsInventoryLocation

    out = []
    for lv in db.query(FsInventoryLevel).filter(FsInventoryLevel.tenant_id == tenant_id, FsInventoryLevel.status == "good",
                                                FsInventoryLevel.product_name.ilike(product)).all():
        loc = db.get(FsInventoryLocation, lv.location_id)
        if loc is not None and loc.active and loc.kind in ("warehouse", "field_office"):
            out.append((loc, lv.on_hand - lv.reserved))
    return sorted(out, key=lambda x: -x[1])


def check_atp(db: Session, tenant_id: int, ctx_type: str, rec: Any, split: bool = False) -> list[dict[str, Any]]:
    today = utcnow().date()
    out = []
    for line in [r for r in lines(db, tenant_id, ctx_type, rec, include_deleted=False)]:
        if line.kind != "equipment":
            line.atp_status, line.atp_date = "not_tracked", line.requested_date or today
            out.append({"line": line.line_number, "product_name": line.product_name, "atp_status": "not_tracked", "detail": "service - no stock needed"})
            continue
        need = line.quantity - line.shipped_qty
        stock = _stock_locations(db, tenant_id, line.product_name)
        best = stock[0] if stock else (None, 0.0)
        available = max(best[1], 0.0)
        ship_by = max(line.requested_date or today, today + dt.timedelta(days=1))
        if available >= need:
            line.atp_status, line.atp_date, line.atp_location_id = "available", ship_by, best[0].id
            detail = f"{need:g} available at {best[0].name}"
        elif available > 0 and split and line.status == "pending":
            rest = need - available
            line.quantity = line.shipped_qty + available
            line.atp_status, line.atp_date, line.atp_location_id = "available", ship_by, best[0].id
            back = _copy_line(db, line, ctx_type, rec.id, max(r.line_number for r in lines(db, tenant_id, ctx_type, rec)) + 1)
            back.quantity, back.revised_line_id = rest, None
            back.atp_status, back.atp_date = "backorder", today + dt.timedelta(days=BACKORDER_DAYS)
            back.requested_date = back.atp_date
            back.integration_id = f"EQP-{rec.id}-{back.id}"
            _history(db, line, _revision(db, ctx_type, rec), "quantity", need, available)
            detail = f"split: {available:g} now from {best[0].name}, {rest:g} on backorder (line {back.line_number}, {back.atp_date})"
        elif available > 0:
            line.atp_status, line.atp_date, line.atp_location_id = "partial", ship_by, best[0].id
            detail = f"only {available:g} of {need:g} in stock at {best[0].name} - split the line to ship what is there"
        else:
            line.atp_status, line.atp_date, line.atp_location_id = "backorder", today + dt.timedelta(days=BACKORDER_DAYS), None
            detail = f"out of stock - expected {line.atp_date}"
        out.append({"line": line.line_number, "product_name": line.product_name, "quantity": need, "atp_status": line.atp_status,
                    "atp_date": line.atp_date, "detail": detail})
    db.flush()
    return out


# --------------------------------------------------------- shipping & tax

def set_shipping(db: Session, tenant_id: int, order: Order, address: str = "", method: str = "", requested_date: Any = None,
                 carrier: str = "", terms: str = "", name: str = "", region: str = "") -> dict[str, Any]:
    d = order_detail(db, tenant_id, order)
    if d.om_status not in ("pending", "awaiting_approval", "booked", "awaiting_shipping"):
        raise ValueError(f"Order {d.order_number} is {d.om_status.replace('_', ' ')} - shipping can no longer change")
    if address:
        d.ship_to_address = address[:400]
    if name:
        d.ship_to_name = name[:200]
    if region:
        d.ship_region = region[:100]
    if requested_date not in (None, ""):
        d.requested_date = _date(requested_date)
        for line in lines(db, tenant_id, "order", order, include_deleted=False):
            if line.requested_date is None:
                line.requested_date = d.requested_date
    if terms:
        d.shipping_terms = terms[:50]
    m = None
    if method:
        m = db.query(OmShippingMethod).filter(OmShippingMethod.tenant_id == tenant_id, OmShippingMethod.active.is_(True),
                                              OmShippingMethod.name.ilike(method.strip())).first()
        if m is None:
            names = [x.name for x in db.query(OmShippingMethod).filter(OmShippingMethod.tenant_id == tenant_id, OmShippingMethod.active.is_(True))]
            raise ValueError(f"No shipping method '{method}' - methods: {', '.join(names) or 'none set up'}")
        d.shipping_method_id = m.id
        d.carrier = carrier or m.carrier
    elif carrier:
        d.carrier = carrier[:100]
    m = m or (db.get(OmShippingMethod, d.shipping_method_id) if d.shipping_method_id else None)
    d.freight = freight(db, tenant_id, order, m)
    db.flush()
    return {"order_number": d.order_number, "ship_to": d.ship_to_address, "requested_date": d.requested_date, "method": m.name if m else None,
            "carrier": d.carrier, "freight": d.freight, "deliver_in_days": m.days if m else None}


def freight(db: Session, tenant_id: int, order: Order, method: OmShippingMethod | None) -> float:
    if method is None:
        return 0.0
    rows = [r for r in lines(db, tenant_id, "order", order, include_deleted=False) if r.kind == "equipment"]
    units = sum(r.quantity for r in rows)
    goods = sum(r.net_price * r.quantity for r in rows)
    if not units or (method.free_over is not None and goods >= method.free_over):
        return 0.0
    return _r(method.base_charge + method.per_unit * units)


def _jurisdiction(db: Session, tenant_id: int, ctx_type: str, rec: Any, given: str = "") -> str:
    """Where tax is due: as given, else the ship-to region, else the region the deal was sold in."""
    if given:
        return given
    opp = None
    if ctx_type == "order":
        d = order_detail(db, tenant_id, rec)
        if d.ship_region:
            return d.ship_region
        quote = db.get(Quote, rec.quote_id) if rec.quote_id else None
        opp = db.get(Opportunity, quote.opportunity_id) if quote else None
    else:
        opp = db.get(Opportunity, rec.opportunity_id)
    region = opp.lead.region if opp is not None and opp.lead is not None else None
    return region if region and region.upper() != "ALL" else "ALL"


def calculate_tax(db: Session, tenant_id: int, ctx_type: str, rec: Any, jurisdiction: str = "") -> dict[str, Any]:
    where = _jurisdiction(db, tenant_id, ctx_type, rec, jurisdiction)
    rules = [r for r in db.query(OmTaxRule).filter(OmTaxRule.tenant_id == tenant_id, OmTaxRule.active.is_(True)).all()
             if _key(r.jurisdiction) in (_key(where), "all")]
    specific = [r for r in rules if _key(r.jurisdiction) == _key(where)]
    rules = specific or rules  # a jurisdiction's own rules replace the general ones
    rows = lines(db, tenant_id, ctx_type, rec, include_deleted=False)
    one_time = recurring = 0.0
    breakdown = []
    for rule in rules:
        def applies(line: OmLine) -> bool:
            return rule.applies_to == "all" or rule.applies_to == line.price_type or \
                (rule.applies_to == "service" and line.kind == "service") or (rule.applies_to == "equipment" and line.kind == "equipment")

        base_once = sum(l.net_price * l.quantity for l in rows if l.price_type == "one_time" and applies(l))
        base_month = sum(l.net_price * l.quantity for l in rows if l.price_type == "recurring" and applies(l))
        t_once, t_month = _r(base_once * rule.rate_pct / 100), _r(base_month * rule.rate_pct / 100)
        one_time += t_once
        recurring += t_month
        breakdown.append({"tax": rule.name, "rate_pct": rule.rate_pct, "one_time": t_once, "monthly": t_month})
    if ctx_type == "order":
        d = order_detail(db, tenant_id, rec)
        d.tax_jurisdiction, d.tax_amount, d.tax_recurring = where, _r(one_time), _r(recurring)
        db.flush()
    return {"jurisdiction": where, "one_time_tax": _r(one_time), "monthly_tax": _r(recurring), "breakdown": breakdown,
            "note": "" if rules else "No tax rules set up for this jurisdiction - nothing charged"}


# ----------------------------------------------------- payment & credit

def credit_profile(db: Session, tenant_id: int, party_id: int) -> OmCreditProfile | None:
    return db.query(OmCreditProfile).filter(OmCreditProfile.tenant_id == tenant_id, OmCreditProfile.party_id == party_id).first()


def exposure(db: Session, tenant_id: int, party_id: int, exclude_order: int | None = None) -> float:
    account = db.query(BillingAccount).filter(BillingAccount.party_id == party_id).first()
    owed = max(account.balance, 0.0) if account else 0.0
    open_value = 0.0
    for d in db.query(OmOrderDetail).filter(OmOrderDetail.tenant_id == tenant_id, OmOrderDetail.om_status.in_(OPEN_OM)).all():
        order = db.get(Order, d.order_id)
        if order is None or order.party_id != party_id or order.id == exclude_order:
            continue
        open_value += totals(db, tenant_id, "order", order)["due_now"]
    return _r(owed + open_value)


def credit_check(db: Session, tenant_id: int, party_id: int, amount: float, ctx_type: str = "order", ctx_id: int | None = None) -> OmCreditCheck:
    profile = credit_profile(db, tenant_id, party_id)
    exp = exposure(db, tenant_id, party_id, ctx_id if ctx_type == "order" else None)
    limit = profile.credit_limit if profile else 0.0
    total = _r(exp + amount)
    if profile is None:
        result, reason = "review", "No credit line on file for this customer - a credit manager must review"
    elif profile.on_hold:
        result, reason = "declined", "The customer's credit is on hold"
    elif total <= limit:
        result, reason = "approved", f"Exposure {total:,.2f} within the {limit:,.2f} credit limit"
    elif total <= limit * 1.1:
        result, reason = "review", f"Exposure {total:,.2f} is just over the {limit:,.2f} limit"
    else:
        result, reason = "declined", f"Exposure {total:,.2f} is over the {limit:,.2f} credit limit"
    row = OmCreditCheck(tenant_id=tenant_id, party_id=party_id, context_type=ctx_type, context_id=ctx_id, amount=_r(amount), exposure=exp,
                        credit_limit=limit, result=result, reason=reason)
    db.add(row)
    db.flush()
    return row


def credit_out(c: OmCreditCheck) -> dict[str, Any]:
    return {"result": c.result, "reason": c.reason, "amount": c.amount, "exposure": c.exposure, "credit_limit": c.credit_limit,
            "checked_at": c.created_at}


def add_payment(db: Session, tenant_id: int, order: Order, method: str, amount: Any = None, card_last4: str = "", card_brand: str = "",
                po_number: str = "", payment_terms: str = "", reference: str = "") -> dict[str, Any]:
    d = order_detail(db, tenant_id, order)
    if d.om_status in ("cancelled", "fulfilled"):
        raise ValueError(f"Order {d.order_number} is {d.om_status} - no new payment")
    method = str(method or "").strip().lower().replace(" ", "_")
    method = {"card": "credit_card", "creditcard": "credit_card", "po": "purchase_order", "bank": "bank_transfer", "transfer": "bank_transfer",
              "account": "on_account", "on_account": "on_account", "check": "cheque"}.get(method, method)
    if method not in ("credit_card", "purchase_order", "bank_transfer", "cash", "cheque", "on_account"):
        raise ValueError("Payment method: credit_card, purchase_order, bank_transfer, cash, cheque or on_account")
    for value in (card_last4, reference, po_number):
        if value and CARD_NUMBER.search(str(value)):
            raise ValueError("Never enter a full card number - only the last 4 digits; the card itself is taken by the payment provider")
    due = totals(db, tenant_id, "order", order)["due_now"]
    amt = _r(float(amount)) if amount not in (None, "") else due
    if amt < 0:
        raise ValueError("The amount can't be negative")
    pay = OmPayment(tenant_id=tenant_id, context_type="order", context_id=order.id, party_id=order.party_id, method=method, amount=amt,
                    reference=str(reference or "")[:100])
    credit = None
    if method == "credit_card":
        last4 = re.sub(r"\D", "", str(card_last4 or ""))
        if len(last4) != 4:
            raise ValueError("Give the card's last 4 digits")
        pay.card_last4, pay.card_brand = last4, str(card_brand or "card")[:20]
        if last4 == "0000":
            pay.status = "declined"
        else:
            pay.status, pay.auth_code = "authorized", f"AUTH-{order.id:04d}{last4}"
    elif method in ("purchase_order", "on_account"):
        if method == "purchase_order" and not str(po_number or "").strip():
            raise ValueError("Give the purchase order number")
        pay.po_number = str(po_number or "")[:60]
        credit = credit_check(db, tenant_id, order.party_id, amt, "order", order.id)
        pay.status = "authorized" if credit.result == "approved" else "pending"
        d.payment_terms = payment_terms or d.payment_terms or (credit_profile(db, tenant_id, order.party_id).payment_terms
                                                                if credit_profile(db, tenant_id, order.party_id) else "Net 30")
    else:
        pay.status = "pending"
    if payment_terms:
        d.payment_terms = str(payment_terms)[:50]
    db.add(pay)
    db.flush()
    return {"payment": payment_out(pay), "credit_check": credit_out(credit) if credit else None, "due_now": due,
            "paid_or_authorized": _r(sum(p.amount for p in payments(db, tenant_id, order) if p.status in ("authorized", "captured")))}


def payments(db: Session, tenant_id: int, order: Order) -> list[OmPayment]:
    return db.query(OmPayment).filter(OmPayment.tenant_id == tenant_id, OmPayment.context_type == "order", OmPayment.context_id == order.id) \
        .order_by(OmPayment.id).all()


def payment_out(p: OmPayment) -> dict[str, Any]:
    return {"id": p.id, "method": p.method, "amount": p.amount, "status": p.status, "card": f"{p.card_brand} •••• {p.card_last4}" if p.card_last4 else "",
            "auth_code": p.auth_code, "po_number": p.po_number, "reference": p.reference, "created_at": p.created_at}


# ------------------------------------------------------------ verification

def verify(db: Session, tenant_id: int, ctx_type: str, rec: Any) -> dict[str, Any]:
    issues: list[dict[str, str]] = []

    def add(level: str, text: str) -> None:
        issues.append({"level": level, "text": text})

    rows = lines(db, tenant_id, ctx_type, rec, include_deleted=False)
    if not rows:
        add("error", "No lines")
    for r in rows:
        if r.product_offering_id:
            o = db.get(ProductOffering, r.product_offering_id)
            if o is not None and o.status != OfferingStatus.active:
                add("error", f"Line {r.line_number}: {r.product_name} is no longer sold ({o.status.value})")
        if r.net_price <= 0 and r.kind != "service":
            add("warning", f"Line {r.line_number}: {r.product_name} has no price")
        if r.kind == "write_in":
            add("warning", f"Line {r.line_number}: {r.product_name} is a write-in - check the price")
        if r.atp_status == "backorder":
            add("warning", f"Line {r.line_number}: {r.product_name} is on backorder until {r.atp_date}")
        if r.atp_status == "partial":
            add("warning", f"Line {r.line_number}: only part of {r.product_name} is in stock - split the line")
    from app.crm_core import account_service

    blocked, watch = account_service.standing(db, tenant_id, party_id_of(db, ctx_type, rec))
    for text in blocked:
        add("error", text)
    for text in watch:
        add("warning", text)
    if ctx_type == "quote":
        d = quote_detail(db, tenant_id, rec)
        if rec.status in (QuoteStatus.cancelled, QuoteStatus.superseded, QuoteStatus.rejected):
            add("error", f"The quote is {rec.status.value}")
        if d.effective_through < utcnow().date():
            add("error", f"The quote expired on {d.effective_through}")
        elif (d.effective_through - utcnow().date()).days <= 3:
            add("warning", f"The quote expires on {d.effective_through}")
        d.verified_at, d.issues_json = utcnow(), json.dumps(issues)
    else:
        d = order_detail(db, tenant_id, rec)
        t = totals(db, tenant_id, "order", rec)
        equipment = [r for r in rows if r.kind == "equipment"]
        if d.hold:
            add("error", f"The order is on hold ({d.hold_reason or 'no reason given'})")
        if equipment and not d.ship_to_address:
            add("error", "Equipment to ship but no ship-to address")
        if equipment and not d.shipping_method_id:
            add("warning", "No shipping method - freight not calculated")
        if t["one_time"] > 0 and not d.tax_jurisdiction:
            add("warning", "Tax not calculated yet")
        pays = payments(db, tenant_id, rec)
        covered = sum(p.amount for p in pays if p.status in ("authorized", "captured"))
        if any(p.status == "declined" for p in pays) and covered < t["due_now"]:
            add("error", "A card payment was declined")
        if t["due_now"] > 0 and covered < t["due_now"] - 0.01 and not d.payment_terms:
            add("error", f"{t['due_now'] - covered:,.2f} due now is not covered by a payment or payment terms")
        if any(p.method in ("purchase_order", "on_account") and p.status == "pending" for p in pays):
            add("warning", "A purchase order / on-account payment is waiting for credit review")
        quote = db.get(Quote, rec.quote_id) if rec.quote_id else None
        if quote is not None:
            qd = quote_detail(db, tenant_id, quote)
            if qd.effective_through < (rec.created_at or utcnow()).date():
                add("warning", f"Ordered from a quote that had expired on {qd.effective_through}")
        d.verified_at, d.issues_json = utcnow(), json.dumps(issues)
    db.flush()
    return {"ok": not any(i["level"] == "error" for i in issues), "issues": issues}


# ------------------------------------------------------------ submit & life

def _reserve(db: Session, tenant_id: int, line: OmLine, sign: int = 1) -> None:
    from app.crm_core import fs_inventory

    if line.kind != "equipment" or not line.atp_location_id:
        return
    lv = fs_inventory.level(db, tenant_id, line.atp_location_id, line.product_name, "good", create=False)
    if lv is not None:
        qty = max(line.quantity - line.shipped_qty, 0.0)
        lv.reserved = max(0.0, lv.reserved + sign * qty)


def submit(
    db: Session, tenant_id: int, order: Order, decision: str | None = None, actor_role: str | None = None,
    actor_user_id: int | None = None, reasoning: str = "",
) -> dict[str, Any]:
    from app.crm_core import approval_service

    d = order_detail(db, tenant_id, order)
    rows = lines(db, tenant_id, "order", order, include_deleted=False)
    if d.om_status == "awaiting_approval":
        if decision not in ("approve", "reject"):
            raise ValueError(f"Order {d.order_number} is waiting for approval - approve or reject it")
        instance = approval_service.pending_embedded(db, "om_order", order.id)
        if instance is not None:  # the approval matrix decides who approves
            approval_service.decide(db, instance, decision, reasoning, actor_user_id)
            return discount_decided(db, tenant_id, order, instance.status.value)
        if actor_role not in (None, "admin"):
            raise ValueError("Only a manager (admin) can approve an order above the discount limit")
        return discount_decided(db, tenant_id, order, "approved" if decision == "approve" else "rejected")
    if d.om_status != "pending":
        raise ValueError(f"Order {d.order_number} is already {d.om_status.replace('_', ' ')}")
    if any(r.kind == "equipment" and not r.atp_status for r in rows):
        check_atp(db, tenant_id, "order", order)
    if d.tax_jurisdiction == "" and totals(db, tenant_id, "order", order)["one_time"] > 0:
        calculate_tax(db, tenant_id, "order", order)
    result = verify(db, tenant_id, "order", order)
    errors = [i["text"] for i in result["issues"] if i["level"] == "error"]
    if errors:
        raise ValueError("Can't submit yet: " + "; ".join(errors))
    # explode: every unit of a trackable line becomes its own line, so each can become its own asset
    for r in list(rows):
        if r.trackable and r.quantity > 1 and r.quantity == int(r.quantity):
            for _ in range(int(r.quantity) - 1):
                twin = _copy_line(db, r, "order", order.id, max(x.line_number for x in lines(db, tenant_id, "order", order)) + 1)
                twin.quantity, twin.revised_line_id = 1, r.id
                twin.atp_status, twin.atp_date, twin.atp_location_id = r.atp_status, r.atp_date, r.atp_location_id
                twin.integration_id = f"EQP-{order.id}-{twin.id}"
            _history(db, r, d.revision, "quantity", r.quantity, 1)
            r.quantity = 1
    rows = lines(db, tenant_id, "order", order, include_deleted=False)
    top = max((r.discount_pct for r in rows if not r.core_item_id), default=0.0)
    sums = totals(db, tenant_id, "order", order)
    instance = approval_service.open_embedded(
        db, tenant_id, "om_order", order.id, discount_pct=top, amount=_r(sums["one_time"] + sums["recurring"]),
        summary=f"Order {d.order_number}: line discount up to {top:g}%", requested_by=actor_user_id,
    )
    if instance is not None:
        d.om_status = "awaiting_approval"
        for r in rows:
            r.status = "awaiting_approval"
        db.flush()
        return {"order_number": d.order_number, "om_status": d.om_status, "approval_needed": True,
                "waiting_for": approval_service.waiting_for(instance),
                "why": f"the approval rule '{instance.template.name}' covers a {top:g}% discount",
                "warnings": [i["text"] for i in result["issues"]]}
    if any(not r.core_item_id and r.discount_pct > APPROVAL_DISCOUNT_PCT for r in rows) and actor_role not in ("admin",):
        d.om_status = "awaiting_approval"
        for r in rows:
            r.status = "awaiting_approval"
        db.flush()
        return {"order_number": d.order_number, "om_status": d.om_status, "approval_needed": True,
                "why": f"a line discount is above {APPROVAL_DISCOUNT_PCT:g}%", "warnings": [i["text"] for i in result["issues"]]}
    out = _book(db, tenant_id, order, d, rows)
    out["warnings"] = [i["text"] for i in result["issues"]]
    return out


def discount_decided(db: Session, tenant_id: int, order: Order, status: str) -> dict[str, Any]:
    """What an approval decision on an awaiting order leads to: still
    waiting for the next tier, back to pending (rejected) or booked."""
    from app.crm_core import approval_service

    d = order_detail(db, tenant_id, order)
    rows = lines(db, tenant_id, "order", order, include_deleted=False)
    if d.om_status != "awaiting_approval":
        return {"order_number": d.order_number, "om_status": d.om_status}
    if status == "pending":
        instance = approval_service.pending_embedded(db, "om_order", order.id)
        return {"order_number": d.order_number, "om_status": d.om_status, "approved": None,
                "waiting_for": approval_service.waiting_for(instance) if instance else None}
    if status == "rejected":
        d.om_status = "pending"
        for r in rows:
            r.status = "pending"
        db.flush()
        return {"order_number": d.order_number, "om_status": d.om_status, "approved": False}
    return _book(db, tenant_id, order, d, rows, approved=True)


def _book(db: Session, tenant_id: int, order: Order, d: OmOrderDetail, rows: list[OmLine], approved: bool = False) -> dict[str, Any]:
    now = utcnow()
    for r in rows:
        if r.status in ("pending", "awaiting_approval"):
            r.status = "open"
        _reserve(db, tenant_id, r)
    d.om_status = "awaiting_shipping" if any(r.kind == "equipment" and r.shipped_qty < r.quantity for r in rows) else "booked"
    d.submitted_at = d.submitted_at or now
    d.acknowledged_at = now
    db.flush()
    party = db.get(Party, order.party_id)
    return {"order_number": d.order_number, "om_status": d.om_status, "approved": approved or None, "submitted_at": now,
            "acknowledgement": f"Order {d.order_number} confirmation sent to {party.email if party and party.email else 'the customer'}",
            "totals": totals(db, tenant_id, "order", order)}


def hold(db: Session, tenant_id: int, order: Order, action: str, reason: str = "") -> dict[str, Any]:
    d = order_detail(db, tenant_id, order)
    if action == "hold":
        if d.om_status in ("shipped", "fulfilled", "cancelled"):
            raise ValueError(f"Order {d.order_number} is {d.om_status} - nothing to hold")
        if not str(reason or "").strip():
            raise ValueError("Why is the order on hold?")
        d.hold, d.hold_reason, d.held_at = True, str(reason)[:200], utcnow()
    elif action == "release":
        if not d.hold:
            raise ValueError(f"Order {d.order_number} is not on hold")
        d.hold, d.hold_reason = False, ""
    else:
        raise ValueError("Hold or release?")
    db.flush()
    return {"order_number": d.order_number, "hold": d.hold, "hold_reason": d.hold_reason, "om_status": d.om_status}


def _snapshot(db: Session, tenant_id: int, order: Order) -> list[dict[str, Any]]:
    return [line_out(r) for r in lines(db, tenant_id, "order", order)]


def cancel(db: Session, tenant_id: int, order: Order, reason: str) -> dict[str, Any]:
    from app.crm_core import ponr_service

    d = order_detail(db, tenant_id, order)
    if not str(reason or "").strip():
        raise ValueError("Give a cancellation reason")
    if d.om_status not in CANCELLABLE:
        raise ValueError(f"Order {d.order_number} is {d.om_status.replace('_', ' ')} - "
                         + ("a live service is ended with 'Suspend or cancel a service'" if d.om_status == "fulfilled" else "it can no longer be cancelled; return it instead"))
    if ponr_service.has_reached_ponr(db, tenant_id, "order", order.status.value):
        raise ValueError(f"Order {d.order_number} has passed the point of no return - end the service with 'Suspend or cancel a service'")
    snap = _snapshot(db, tenant_id, order)
    d.revision += 1
    changes = []
    for r in lines(db, tenant_id, "order", order, include_deleted=False):
        _reserve(db, tenant_id, r, -1)
        _history(db, r, d.revision, "action_code", r.action_code, "Delete")
        changes.append({"line": r.line_number, "product": r.product_name, "action": "Delete"})
        r.action_code, r.status = "Delete", "cancelled"
    voided = 0
    for p in payments(db, tenant_id, order):
        if p.status in ("authorized", "pending"):
            p.status = "voided"
            voided += 1
    db.add(OmOrderRevision(tenant_id=tenant_id, order_id=order.id, revision=d.revision, kind="cancel", reason=str(reason)[:300],
                           snapshot_json=json.dumps(snap, default=str), changes_json=json.dumps(changes)))
    d.om_status, d.cancel_reason = "cancelled", str(reason)[:200]
    if order.status in (OrderStatus.created, OrderStatus.pending_approval):
        order.status = OrderStatus.cancelled
    db.flush()
    return {"order_number": d.order_number, "revision": d.revision, "om_status": d.om_status, "cancel_reason": d.cancel_reason,
            "payments_voided": voided}


_CHANGE = re.compile(
    r"(?P<verb>add|remove|delete|drop|change|set|update)\s+(?:(?P<qty>\d+(?:\.\d+)?)\s*(?:x|×)?\s+)?(?P<product>[^,;]+?)"
    r"(?:\s+to\s+(?P<to>\d+(?:\.\d+)?))?(?=\s*(?:[,;]|\band\b|$))", re.I)


def parse_changes(text: Any) -> list[dict[str, Any]]:
    """'add 2 x Router CPE, remove Static IP, change SFP module 1G to 3' -> ops."""
    if isinstance(text, list):
        return text
    ops = []
    for m in _CHANGE.finditer(str(text or "")):
        verb = m.group("verb").lower()
        product = m.group("product").strip(" .")
        if verb == "add":
            ops.append({"action": "Add", "product": product, "quantity": float(m.group("qty") or 1)})
        elif verb in ("remove", "delete", "drop"):
            ops.append({"action": "Delete", "product": product})
        else:
            qty = m.group("to") or m.group("qty")
            if qty is None:
                continue
            ops.append({"action": "Update", "product": product, "quantity": float(qty)})
    if not ops:
        raise ValueError("Say what changes, e.g. 'add 2 x Router CPE, remove Static IP, change SFP module 1G to 3'")
    return ops


def _apply_ops(db: Session, tenant_id: int, order: Order, ops: list[dict[str, Any]], revision: int) -> list[dict[str, Any]]:
    changes = []
    for op in ops:
        if op["action"] == "Add":
            line = add_line(db, tenant_id, "order", order, op["product"], op.get("quantity") or 1, op.get("unit_price"), check=False)
            line.status = "open"
            changes.append({"line": line.line_number, "line_id": line.id, "product": line.product_name, "action": "Add", "quantity": line.quantity})
            continue
        line = find_line(db, tenant_id, "order", order, op["product"])
        if line.core_item_id and op["action"] == "Delete":
            raise ValueError("The main product can't be removed from the order - cancel the order instead")
        if line.shipped_qty and op["action"] == "Delete":
            raise ValueError(f"{line.product_name} has already shipped - return it instead")
        if op["action"] == "Delete":
            _reserve(db, tenant_id, line, -1)
            _history(db, line, revision, "action_code", line.action_code, "Delete")
            line.action_code, line.status = "Delete", "cancelled"
            changes.append({"line": line.line_number, "product": line.product_name, "action": "Delete"})
        else:
            new_qty = float(op["quantity"])
            if new_qty < line.shipped_qty:
                raise ValueError(f"{line.shipped_qty:g} of {line.product_name} already shipped")
            if line.core_item_id:
                item = db.get(OrderItem, line.core_item_id)
                per = (order.monthly_price or 0) / (item.quantity or 1)
                item.quantity = int(new_qty)
                order.monthly_price = _r(per * new_qty)
            _reserve(db, tenant_id, line, -1)
            _history(db, line, revision, "quantity", line.quantity, new_qty)
            changes.append({"line": line.line_number, "line_id": line.id, "product": line.product_name, "action": "Update", "old": line.quantity, "new": new_qty})
            line.quantity = new_qty
            if line.action_code == "-":
                line.action_code = "Update"
            line.atp_status = ""
    db.flush()
    return changes


def supplemental(db: Session, tenant_id: int, order: Order, changes_text: Any, reason: str = "") -> dict[str, Any]:
    """Revise an order in flight: a new revision of the same order number replaces it in fulfillment."""
    from app.crm_core import ponr_service

    d = order_detail(db, tenant_id, order)
    if d.om_status in PAST_PONR_OM or ponr_service.has_reached_ponr(db, tenant_id, "order", order.status.value):
        raise ValueError(f"Order {d.order_number} is past the point of no return ({d.om_status.replace('_', ' ')}) - "
                         "create a follow-on order instead")
    if d.om_status == "pending":
        raise ValueError(f"Order {d.order_number} hasn't been submitted - change its lines directly")
    ops = parse_changes(changes_text)
    snap = _snapshot(db, tenant_id, order)
    d.revision += 1
    applied = _apply_ops(db, tenant_id, order, ops, d.revision)
    atp = check_atp(db, tenant_id, "order", order)
    touched = {c.get("line_id") for c in applied if c["action"] in ("Add", "Update")}
    for r in lines(db, tenant_id, "order", order, include_deleted=False):
        if r.id in touched and r.status == "open" and r.kind == "equipment":
            _reserve(db, tenant_id, r)  # only what this revision added or changed
    if d.tax_jurisdiction:
        calculate_tax(db, tenant_id, "order", order, d.tax_jurisdiction)
    if d.shipping_method_id:
        d.freight = freight(db, tenant_id, order, db.get(OmShippingMethod, d.shipping_method_id))
    db.add(OmOrderRevision(tenant_id=tenant_id, order_id=order.id, revision=d.revision, kind="supplemental", reason=str(reason or "")[:300],
                           snapshot_json=json.dumps(snap, default=str), changes_json=json.dumps(applied)))
    db.flush()
    return {"order_number": d.order_number, "revision": d.revision, "changes": applied, "availability": atp,
            "totals": totals(db, tenant_id, "order", order)}


def follow_on(db: Session, tenant_id: int, order: Order, changes_text: Any, requested_date: Any = None) -> dict[str, Any]:
    """A future-dated order for the same service, starting from the original order."""
    src = order_detail(db, tenant_id, order)
    if src.om_status == "cancelled":
        raise ValueError(f"Order {src.order_number} was cancelled - start a new order")
    ops = parse_changes(changes_text)
    when = _date(requested_date) or utcnow().date() + dt.timedelta(days=1)
    if when < utcnow().date():
        raise ValueError("A follow-on order is dated today or later")
    new = Order(tenant_id=tenant_id, quote_id=order.quote_id, party_id=order.party_id, service_account_id=order.service_account_id,
                order_type=OrderType.change, status=OrderStatus.created, contract_months=order.contract_months,
                monthly_price=order.monthly_price)
    db.add(new)
    db.flush()
    for item in order.items:
        db.add(OrderItem(order_id=new.id, product_offering_id=item.product_offering_id, quantity=item.quantity))
    db.flush()
    db.refresh(new)
    d = order_detail(db, tenant_id, new)
    d.source_order_id, d.requested_date = order.id, when
    d.ship_to_address, d.ship_to_name, d.ship_region = src.ship_to_address, src.ship_to_name, src.ship_region
    d.shipping_method_id, d.carrier, d.payment_terms = src.shipping_method_id, src.carrier, src.payment_terms
    main = lines(db, tenant_id, "order", new)
    for m in main:
        m.action_code = "-"
    for r in lines(db, tenant_id, "order", order, include_deleted=False):
        if not r.core_item_id and r.kind != "equipment":
            _copy_line(db, r, "order", new.id, max(x.line_number for x in lines(db, tenant_id, "order", new)) + 1, action="-")
    changes = _apply_ops(db, tenant_id, new, ops, d.revision)
    for r in lines(db, tenant_id, "order", new):
        if r.requested_date is None:
            r.requested_date = when
        r.status = "pending"
    db.flush()
    return {"order_id": new.id, "order_number": d.order_number, "follow_on_of": src.order_number, "requested_date": when,
            "changes": changes, "totals": totals(db, tenant_id, "order", new)}


# --------------------------------------------------------- ship & deliver

def ship(db: Session, tenant_id: int, order: Order, location: str = "", carrier: str = "", tracking_number: str = "",
         line_refs: Any = None) -> dict[str, Any]:
    from app.crm_core import fs_inventory
    from app.models.field_service import FsInventoryLocation

    d = order_detail(db, tenant_id, order)
    if d.om_status not in ("booked", "awaiting_shipping", "partially_shipped"):
        raise ValueError(f"Order {d.order_number} is {d.om_status.replace('_', ' ')} - only a submitted order ships")
    if d.hold:
        raise ValueError(f"Order {d.order_number} is on hold ({d.hold_reason})")
    rows = [r for r in lines(db, tenant_id, "order", order, include_deleted=False) if r.kind == "equipment" and r.shipped_qty < r.quantity]
    if line_refs:
        wanted = [find_line(db, tenant_id, "order", order, ref).id for ref in (line_refs if isinstance(line_refs, list) else [line_refs])]
        rows = [r for r in rows if r.id in wanted]
    if not rows:
        raise ValueError("Nothing left to ship on this order")
    src = None
    if location:
        src = db.query(FsInventoryLocation).filter(FsInventoryLocation.tenant_id == tenant_id, FsInventoryLocation.name.ilike(location.strip())).first()
        if src is None:
            raise ValueError(f"No stock location '{location}'")
    shipment = OmShipment(tenant_id=tenant_id, order_id=order.id, carrier=carrier or d.carrier,
                          method=(db.get(OmShippingMethod, d.shipping_method_id).name if d.shipping_method_id else ""),
                          tracking_number=tracking_number or "", from_location_id=src.id if src else None)
    db.add(shipment)
    db.flush()
    shipment.shipment_number = f"SH-{shipment.id:06d}"
    shipment.tracking_number = shipment.tracking_number or f"TRK{order.id:05d}{shipment.id:04d}"
    shipped = []
    for r in rows:
        loc_id = src.id if src else r.atp_location_id
        if loc_id is None:
            stock = _stock_locations(db, tenant_id, r.product_name)
            loc_id = stock[0][0].id if stock else None
        if loc_id is None:
            raise ValueError(f"No stock of {r.product_name} anywhere - check availability first")
        qty = r.quantity - r.shipped_qty
        if r.atp_location_id == loc_id:
            _reserve(db, tenant_id, r, -1)
        fs_inventory.move(db, tenant_id, "consume", r.product_name, qty, source_id=loc_id, reference_type="order", reference_id=order.id,
                          note=f"Shipped on {d.order_number} / {shipment.shipment_number}")
        r.shipped_qty += qty
        r.status = "shipped"
        db.add(OmShipmentLine(tenant_id=tenant_id, shipment_id=shipment.id, line_id=r.id, quantity=qty))
        shipped.append({"line": r.line_number, "product": r.product_name, "quantity": qty})
    left = [r for r in lines(db, tenant_id, "order", order, include_deleted=False) if r.kind == "equipment" and r.shipped_qty < r.quantity]
    d.om_status = "partially_shipped" if left else "shipped"
    db.flush()
    return {"order_number": d.order_number, "shipment_number": shipment.shipment_number, "tracking_number": shipment.tracking_number,
            "carrier": shipment.carrier, "shipped": shipped, "om_status": d.om_status}


def deliver(db: Session, tenant_id: int, order: Order, shipment_number: str = "") -> dict[str, Any]:
    """Proof of delivery: trackable lines become the customer's assets; one-time charges are billed."""
    from app.crm_core import fs_service
    from app.integration import order_adapter
    from app.models.fulfillment import Asset, AssetStatus
    from app.models.field_service import FsAssetDetail

    d = order_detail(db, tenant_id, order)
    q = db.query(OmShipment).filter(OmShipment.tenant_id == tenant_id, OmShipment.order_id == order.id, OmShipment.status == "shipped")
    if shipment_number:
        q = q.filter(OmShipment.shipment_number.ilike(shipment_number.strip()))
    shipments = q.all()
    if not shipments:
        raise ValueError("No shipment in transit on this order")
    now = utcnow()
    assets = []
    for s in shipments:
        s.status, s.delivered_at = "delivered", now
        for sl in db.query(OmShipmentLine).filter(OmShipmentLine.shipment_id == s.id).all():
            line = db.get(OmLine, sl.line_id)
            if line is None:
                continue
            line.status = "delivered" if line.shipped_qty >= line.quantity else line.status
            if line.trackable and line.asset_id is None:
                asset = Asset(tenant_id=tenant_id, service_account_id=order.service_account_id, asset_type=line.product_name,
                              identifier=line.integration_id, status=AssetStatus.active, attributes=json.dumps({"order": d.order_number}))
                db.add(asset)
                db.flush()
                db.add(FsAssetDetail(tenant_id=tenant_id, asset_id=asset.id, party_id=order.party_id, product_name=line.product_name,
                                     serial_number=line.integration_id, installed_on=now.date(), address=d.ship_to_address))
                db.flush()
                fs_service.apply_warranties(db, tenant_id, asset.id)
                line.asset_id = asset.id
                assets.append({"asset_id": asset.id, "product": line.product_name, "integration_id": line.integration_id})
    rows = lines(db, tenant_id, "order", order, include_deleted=False)
    equipment_done = all(r.status == "delivered" for r in rows if r.kind == "equipment")
    billed = 0.0
    if equipment_done:
        t = totals(db, tenant_id, "order", order)
        already = any(r.kind == "deliver_bill" for r in db.query(OmOrderRevision).filter(OmOrderRevision.order_id == order.id).all())
        if t["due_now"] > 0 and not already:
            account = order_adapter.get_or_create_billing_account(db, tenant_id, order.party_id)
            order_adapter.apply_billing_adjustment(db, account.id, order.id, t["due_now"],
                                                   f"{d.order_number}: equipment and one-time charges (incl. freight and tax)")
            for p in payments(db, tenant_id, order):
                if p.status == "authorized":
                    p.status = "captured"
            db.add(OmOrderRevision(tenant_id=tenant_id, order_id=order.id, revision=d.revision, kind="deliver_bill",
                                   reason="Billed on delivery", changes_json=json.dumps({"amount": t["due_now"]})))
            billed = t["due_now"]
        # the equipment is with the customer; the order is fulfilled once its service is live too
        d.om_status = "fulfilled" if order.status in SERVICE_IN_PLACE else "delivered"
    db.flush()
    return {"order_number": d.order_number, "delivered": [s.shipment_number for s in shipments], "assets": assets, "billed": billed,
            "om_status": d.om_status}


def return_items(db: Session, tenant_id: int, order: Order, product: str, quantity: Any = 1, reason: str = "",
                 refund: bool = True, location: str = "") -> dict[str, Any]:
    """RMA: delivered equipment comes back - into the repair loop - and is credited."""
    from app.crm_core import fs_inventory
    from app.integration import order_adapter
    from app.models.field_service import FsInventoryLocation
    from app.models.fulfillment import Asset, AssetStatus

    d = order_detail(db, tenant_id, order)
    line = find_line(db, tenant_id, "order", order, product)
    if line.kind != "equipment" or line.shipped_qty <= 0:
        raise ValueError(f"{line.product_name} has not been shipped - cancel or revise instead")
    qty = float(quantity or 1)
    if qty > line.shipped_qty:
        raise ValueError(f"Only {line.shipped_qty:g} of {line.product_name} were shipped")
    if not str(reason or "").strip():
        raise ValueError("Why is it coming back?")
    if location:
        rc = db.query(FsInventoryLocation).filter(FsInventoryLocation.tenant_id == tenant_id, FsInventoryLocation.name.ilike(location.strip())).first()
    else:
        rc = db.query(FsInventoryLocation).filter(FsInventoryLocation.tenant_id == tenant_id, FsInventoryLocation.kind == "repair_center").first() \
            or db.query(FsInventoryLocation).filter(FsInventoryLocation.tenant_id == tenant_id, FsInventoryLocation.kind == "warehouse").first()
    if rc is None:
        raise ValueError("No repair center or warehouse to receive the return - set one up under Field Service")
    repairs = []
    for _ in range(int(qty)):
        r = fs_inventory.receive_repair(db, tenant_id, product_name=line.product_name, location_id=rc.id, party_id=order.party_id,
                                        asset_id=line.asset_id, serial_number=line.integration_id, symptoms=f"Returned on {d.order_number}: {reason}")
        repairs.append(r.repair_number)
    if line.asset_id:
        asset = db.get(Asset, line.asset_id)
        if asset is not None:
            asset.status = AssetStatus.decommissioned
    credit = 0.0
    if refund:
        credit = _r(line.net_price * qty)
        account = order_adapter.get_or_create_billing_account(db, tenant_id, order.party_id)
        order_adapter.apply_billing_adjustment(db, account.id, order.id, -credit, f"Return of {qty:g} x {line.product_name} on {d.order_number}")
    _history(db, line, d.revision, "returned", "", f"{qty:g} returned ({reason})")
    line.status = "returned" if qty >= line.shipped_qty else line.status
    db.add(OmOrderRevision(tenant_id=tenant_id, order_id=order.id, revision=d.revision, kind="return", reason=str(reason)[:300],
                           changes_json=json.dumps({"line": line.line_number, "product": line.product_name, "quantity": qty, "credit": credit,
                                                    "repairs": repairs})))
    db.flush()
    return {"order_number": d.order_number, "returned": f"{qty:g} x {line.product_name}", "repairs": repairs, "received_at": rc.name,
            "credit": credit}


def activity_plan(db: Session, tenant_id: int, order: Order, template: str, line_ref: Any = None) -> dict[str, Any]:
    from app.crm_core import fs_service
    from app.models.assurance import WorkOrder
    from app.models.field_service import FsActivityTemplate

    d = order_detail(db, tenant_id, order)
    tpl = db.query(FsActivityTemplate).filter(FsActivityTemplate.tenant_id == tenant_id, FsActivityTemplate.name.ilike(str(template).strip())).first()
    if tpl is None:
        names = [t.name for t in db.query(FsActivityTemplate).filter(FsActivityTemplate.tenant_id == tenant_id, FsActivityTemplate.active.is_(True))]
        raise ValueError(f"No activity template '{template}' - templates: {', '.join(names) or 'none (Field Service -> Setup)'}")
    line = find_line(db, tenant_id, "order", order, line_ref) if line_ref else None
    act = fs_service.create_activity(db, tenant_id, party_id=order.party_id, asset_id=line.asset_id if line else None, template_id=tpl.id,
                                     subject=f"{tpl.name} - {d.order_number}" + (f" ({line.product_name})" if line else ""),
                                     earliest_start=dt.datetime.combine(d.requested_date, dt.time(8)) if d.requested_date else None,
                                     address=d.ship_to_address)
    wo = db.get(WorkOrder, act.work_order_id)
    wo.order_id = order.id
    db.flush()
    return {"order_number": d.order_number, "activity_id": act.id, "work_order_id": wo.id, "template": tpl.name,
            "steps": len(_j(act.steps_json, [])), "status": "unscheduled - book it on the Field Service dispatch board"}


# ------------------------------------------------------------- quotes

def to_agreement(db: Session, tenant_id: int, quote: Quote, term_months: Any = None, response_hours: Any = None, coverage: str = "",
                 name: str = "") -> dict[str, Any]:
    if quote.status in (QuoteStatus.cancelled, QuoteStatus.superseded, QuoteStatus.rejected):
        raise ValueError(f"Quote {quote.id} is {quote.status.value} - no agreement from it")
    party = party_of_quote(db, quote)
    if party is None:
        raise ValueError("The quote has no customer")
    term = int(term_months or 12)
    if term <= 0:
        raise ValueError("The term is at least one month")
    rows = lines(db, tenant_id, "quote", quote, include_deleted=False)
    t = totals(db, tenant_id, "quote", quote)
    start = utcnow().date()
    end = start + dt.timedelta(days=round(term * 30.44))
    ag = OmAgreement(tenant_id=tenant_id, party_id=party.id, quote_id=quote.id, name=(name or f"{party.name} - {rows[0].product_name if rows else 'service'}")[:200],
                     start_date=start, end_date=end, term_months=term, monthly_value=t["recurring"], one_time_value=t["one_time"],
                     response_hours=float(response_hours) if response_hours not in (None, "") else None,
                     coverage=coverage if coverage in ("business", "24x7") else "business",
                     payment_terms=(credit_profile(db, tenant_id, party.id).payment_terms if credit_profile(db, tenant_id, party.id) else "Net 30"),
                     lines_json=json.dumps([line_out(r) for r in rows], default=str))
    db.add(ag)
    db.flush()
    ag.agreement_number = f"AG-{ag.id:06d}"
    entitlement = None
    if ag.response_hours:
        from app.crm_core import fs_service

        ent = fs_service.save_entitlement(db, tenant_id, {"party_id": party.id, "name": f"{ag.agreement_number} service level",
                                                          "response_hours": ag.response_hours, "coverage": ag.coverage,
                                                          "starts_on": start.isoformat(), "ends_on": end.isoformat()})
        ag.entitlement_id = ent.id
        entitlement = ent.name
    db.flush()
    return {"agreement_number": ag.agreement_number, "agreement_id": ag.id, "customer": party.name, "start_date": start, "end_date": end,
            "monthly_value": ag.monthly_value, "one_time_value": ag.one_time_value, "entitlement": entitlement}


def update_opportunity(db: Session, tenant_id: int, quote: Quote) -> dict[str, Any]:
    opp = db.get(Opportunity, quote.opportunity_id)
    if opp is None:
        raise ValueError("The quote has no opportunity")
    db.query(OmOpportunityRevenue).filter(OmOpportunityRevenue.opportunity_id == opp.id, OmOpportunityRevenue.quote_id == quote.id) \
        .delete(synchronize_session=False)
    rows = [r for r in lines(db, tenant_id, "quote", quote, include_deleted=False) if r.forecastable]
    months = 12
    out = []
    for r in rows:
        amount = _r(r.net_price * r.quantity * (months if r.price_type == "recurring" else 1))
        db.add(OmOpportunityRevenue(tenant_id=tenant_id, opportunity_id=opp.id, quote_id=quote.id, line_id=r.id, product_name=r.product_name,
                                    revenue_type=r.price_type, quantity=r.quantity, amount=amount, forecastable=True))
        out.append({"product": r.product_name, "revenue_type": r.price_type, "amount": amount})
    quote_detail(db, tenant_id, quote).opportunity_updated_at = utcnow()
    db.flush()
    return {"opportunity_id": opp.id, "quote_id": quote.id, "revenue_lines": out, "forecast_total": _r(sum(o["amount"] for o in out)),
            "basis": f"recurring lines x {months} months + one-time lines"}


def opportunity_revenue(db: Session, tenant_id: int, opportunity_id: int) -> list[dict[str, Any]]:
    return [{"quote_id": r.quote_id, "product": r.product_name, "revenue_type": r.revenue_type, "quantity": r.quantity, "amount": r.amount}
            for r in db.query(OmOpportunityRevenue).filter(OmOpportunityRevenue.tenant_id == tenant_id,
                                                           OmOpportunityRevenue.opportunity_id == opportunity_id).all()]


# ------------------------------------------------------------ services

def resume(db: Session, tenant_id: int, order: Order) -> dict[str, Any]:
    from app.crm_core import sla_service
    from app.integration import order_adapter

    if order.status != OrderStatus.suspended:
        raise ValueError(f"Order {order.id} is {order.status.value} - only a suspended service resumes")
    order_adapter.set_order_status(db, order.id, OrderStatus.active)
    account = order_adapter.get_or_create_billing_account(db, tenant_id, order.party_id)
    order_adapter.set_billing_account_status(db, account.id, "active")
    sla_service.complete_clock(db, tenant_id, "suspension_grace", "order", order.id)
    d = order_detail(db, tenant_id, order)
    for r in lines(db, tenant_id, "order", order, include_deleted=False):
        if r.core_item_id:
            _history(db, r, d.revision, "asset_status", "Suspended", "Active")
            r.action_code = "Resume"
    db.flush()
    return {"order_id": order.id, "status": OrderStatus.active.value, "billing_account": "active"}


# --------------------------------------------------------------- views

def order_view(db: Session, tenant_id: int, order: Order) -> dict[str, Any]:
    d = order_detail(db, tenant_id, order)
    rows = lines(db, tenant_id, "order", order)
    method = db.get(OmShippingMethod, d.shipping_method_id) if d.shipping_method_id else None
    party = db.get(Party, order.party_id)
    ships = db.query(OmShipment).filter(OmShipment.order_id == order.id).order_by(OmShipment.id).all()
    revs = db.query(OmOrderRevision).filter(OmOrderRevision.order_id == order.id).order_by(OmOrderRevision.id).all()
    history = db.query(OmLineChange).filter(OmLineChange.context_type == "order", OmLineChange.context_id == order.id).order_by(OmLineChange.id).all()
    checks = db.query(OmCreditCheck).filter(OmCreditCheck.context_type == "order", OmCreditCheck.context_id == order.id) \
        .order_by(OmCreditCheck.id.desc()).first()
    return {
        "order_id": order.id, "order_number": d.order_number, "revision": d.revision, "order_class": d.order_class, "om_status": d.om_status,
        "status": order.status.value, "order_type": order.order_type.value, "party_id": order.party_id, "party_name": party.name if party else None,
        "quote_id": order.quote_id, "requested_date": d.requested_date, "ship_to_name": d.ship_to_name, "ship_to_address": d.ship_to_address,
        "ship_region": d.ship_region, "shipping_method": method.name if method else None, "carrier": d.carrier, "shipping_terms": d.shipping_terms,
        "payment_terms": d.payment_terms, "hold": d.hold, "hold_reason": d.hold_reason, "cancel_reason": d.cancel_reason,
        "submitted_at": d.submitted_at, "acknowledged_at": d.acknowledged_at, "verified_at": d.verified_at, "issues": _j(d.issues_json, []),
        "source_order_id": d.source_order_id, "tax_jurisdiction": d.tax_jurisdiction,
        "lines": [line_out(r) for r in rows], "totals": totals(db, tenant_id, "order", order),
        "payments": [payment_out(p) for p in payments(db, tenant_id, order)], "credit_check": credit_out(checks) if checks else None,
        "shipments": [{"shipment_number": s.shipment_number, "carrier": s.carrier, "method": s.method, "tracking_number": s.tracking_number,
                       "status": s.status, "shipped_at": s.shipped_at, "delivered_at": s.delivered_at,
                       "lines": [{"line_id": x.line_id, "quantity": x.quantity} for x in db.query(OmShipmentLine).filter(OmShipmentLine.shipment_id == s.id)]}
                      for s in ships],
        "revisions": [{"revision": r.revision, "kind": r.kind, "reason": r.reason, "changes": _j(r.changes_json, []) if r.changes_json.startswith("[") else _j(r.changes_json, {}),
                       "created_at": r.created_at} for r in revs],
        "history": [{"line_id": h.line_id, "revision": h.revision, "field": h.field, "old": h.old_value, "new": h.new_value, "at": h.changed_at}
                    for h in history],
        "milestones": [{"label": m.label, "reached_at": m.reached_at} for m in order.milestones],
        "recommendations": recommendations(db, tenant_id, "order", order),
        "next_steps": next_steps(db, tenant_id, order, d, rows),
        # B2C omnichannel: where it came in, where the service goes, every touch since, and the Order Module's feasibility
        **_b2c_view(db, tenant_id, order, d),
    }


def _b2c_view(db: Session, tenant_id: int, order: Order, d: OmOrderDetail) -> dict[str, Any]:
    from app.crm_core import b2c_order_service as b2c
    from app.models.feasibility import FeasibilityAssessment

    assessment = db.query(FeasibilityAssessment).filter(FeasibilityAssessment.tenant_id == tenant_id, FeasibilityAssessment.order_id == order.id) \
        .order_by(FeasibilityAssessment.id.desc()).first()
    return {"channel": d.channel, "channel_label": b2c.CHANNELS.get(d.channel or "", d.channel), "channel_reference": d.channel_reference,
            "installation_address": d.installation_address, "channel_timeline": b2c.timeline(db, tenant_id, order_id=order.id),
            "feasibility": None if assessment is None else {
                "outcome": assessment.outcome.value, "checked_at": assessment.created_at,
                "checks": [{"check": r.check_type_name, "outcome": r.outcome.value, "detail": r.detail} for r in assessment.results]}}


def next_steps(db: Session, tenant_id: int, order: Order, d: OmOrderDetail, rows: list[OmLine]) -> list[str]:
    live = [r for r in rows if r.action_code != "Delete"]
    steps = []
    if d.om_status == "pending":
        if any(r.kind == "equipment" for r in live) and not d.ship_to_address:
            steps.append("Set the ship-to address and shipping method")
        if any(r.kind == "equipment" and not r.atp_status for r in live):
            steps.append("Check availability")
        if not d.tax_jurisdiction and any(r.price_type == "one_time" for r in live):
            steps.append("Calculate tax")
        if not payments(db, tenant_id, order) and not d.payment_terms:
            steps.append("Add a payment")
        steps.append("Submit the order")
    elif d.om_status == "awaiting_approval":
        steps.append("A manager approves the order")
    elif d.om_status in ("booked", "awaiting_shipping", "partially_shipped"):
        if any(r.kind == "equipment" and r.shipped_qty < r.quantity for r in live):
            steps.append("Ship the equipment")
    elif d.om_status == "shipped":
        steps.append("Confirm delivery")
    elif d.om_status == "delivered":
        steps.append("Equipment delivered - the service goes live through its fulfilment steps")
    if d.hold:
        steps.insert(0, "Release the hold")
    return steps


def quote_view(db: Session, tenant_id: int, quote: Quote) -> dict[str, Any]:
    d = quote_detail(db, tenant_id, quote)
    party = party_of_quote(db, quote)
    rows = lines(db, tenant_id, "quote", quote)
    promo = db.get(OmPromotion, d.promotion_id) if d.promotion_id else None
    agreements = db.query(OmAgreement).filter(OmAgreement.quote_id == quote.id).all()
    return {"quote_id": quote.id, "quote_number": d.quote_number, "revision": quote.revision_number, "status": quote.status.value,
            "party_id": party.id if party else None, "party_name": party.name if party else None, "opportunity_id": quote.opportunity_id,
            "effective_from": d.effective_from, "effective_through": d.effective_through, "expired": d.effective_through < utcnow().date(),
            "verified_at": d.verified_at, "issues": _j(d.issues_json, []), "promotion": promo.name if promo else None,
            "opportunity_updated_at": d.opportunity_updated_at, "lines": [line_out(r) for r in rows], "totals": totals(db, tenant_id, "quote", quote),
            "recommendations": recommendations(db, tenant_id, "quote", quote),
            "agreements": [{"agreement_number": a.agreement_number, "status": a.status, "end_date": a.end_date} for a in agreements],
            "history": [{"line_id": h.line_id, "field": h.field, "old": h.old_value, "new": h.new_value, "at": h.changed_at}
                        for h in db.query(OmLineChange).filter(OmLineChange.context_type == "quote", OmLineChange.context_id == quote.id)]}


def track(db: Session, tenant_id: int, order: Order) -> dict[str, Any]:
    v = order_view(db, tenant_id, order)
    return {k: v[k] for k in ("order_number", "revision", "om_status", "status", "hold", "hold_reason", "requested_date", "totals",
                              "shipments", "milestones", "next_steps")} | {
        "lines": [{"line": line["line_number"], "product": line["product_name"], "quantity": line["quantity"], "status": line["status"],
                   "action": line["action_code"], "atp": line["atp_status"]} for line in v["lines"]]}


# ------------------------------------------------------------- setup

def sample_setup(db: Session, tenant_id: int) -> dict[str, Any]:
    made = []
    if not db.query(OmTaxRule).filter(OmTaxRule.tenant_id == tenant_id).first():
        db.add(OmTaxRule(tenant_id=tenant_id, jurisdiction="ALL", name="VAT", rate_pct=15.0, applies_to="all"))
        made.append("VAT 15% on everything")
    if not db.query(OmShippingMethod).filter(OmShippingMethod.tenant_id == tenant_id).first():
        db.add(OmShippingMethod(tenant_id=tenant_id, name="Standard", carrier="Aramex", base_charge=50, per_unit=10, free_over=2000, days=3))
        db.add(OmShippingMethod(tenant_id=tenant_id, name="Express", carrier="DHL", base_charge=150, per_unit=20, days=1))
        made.append("Standard and Express shipping")
    if not db.query(OmFavorite).filter(OmFavorite.tenant_id == tenant_id).first():
        db.add(OmFavorite(tenant_id=tenant_id, name="Branch office kit",
                          items_json=json.dumps([{"product": "Router CPE", "quantity": 1}, {"product": "Fiber patch cord", "quantity": 2}])))
        made.append("favorite 'Branch office kit'")
    if not db.query(OmRecommendation).filter(OmRecommendation.tenant_id == tenant_id).first():
        db.add(OmRecommendation(tenant_id=tenant_id, product="ONT fiber modem", recommended_product="Router CPE", kind="cross_sell", score=80,
                                message="A managed router completes the fibre install"))
        db.add(OmRecommendation(tenant_id=tenant_id, product="Router CPE", recommended_product="SFP module 1G", kind="up_sell", score=60,
                                message="Add an SFP for a gigabit uplink"))
        made.append("2 product recommendations")
    if not db.query(OmPromotion).filter(OmPromotion.tenant_id == tenant_id).first():
        db.add(OmPromotion(tenant_id=tenant_id, name="Router bundle", code="ROUTER10", products="Router CPE", discount_pct=10,
                           commitment_months=12, early_termination_fee=500))
        made.append("promotion ROUTER10")
    db.flush()
    return {"created": made}

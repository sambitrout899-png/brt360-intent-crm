"""Quote price override (INT-116) - the negotiated total a sales rep agrees
with the customer, replacing the price the system worked out.

  - Only a draft quote can be overridden: one waiting for approval, or
    approved, has a price someone signed off - revise it (INT-30) and
    override the new revision instead.
  - The override is the quote's total_price, so everything downstream (the
    proposal PDF, the PO match, the order's monthly price) uses it as is.
    What the system priced it at is kept beside it, so removing the
    override puts that back.
  - Its effective discount against the undiscounted list price is what the
    approval matrix routes on (INT-06) - a deep negotiated cut needs the
    same sign-off as a deep discount. The products' price floors still hold.
  - Re-pricing the quote (INT-19, a pricelist applied) ends the override:
    the price it was negotiated against no longer exists.
  - Every round is kept, so the negotiation can be read back.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.crm_core import lifecycle_service, ponr_service
from app.models.catalog import ProductOffering
from app.models.crm import Quote, QuotePriceOverride, QuoteStatus
from app.timeutils import utcnow


def active(db: Session, quote_id: int) -> QuotePriceOverride | None:
    return (
        db.query(QuotePriceOverride)
        .filter(QuotePriceOverride.quote_id == quote_id, QuotePriceOverride.active.is_(True))
        .order_by(QuotePriceOverride.id.desc())
        .first()
    )


def list_price(db: Session, quote: Quote) -> float:
    """The quote's total before any discount: each product's configured
    price (pricelist base + its pricing attributes), else its catalog price."""
    from app.crm_core import product_config_service as product_config
    from app.models.product_config import QuoteItemConfig

    total = 0.0
    for item in quote.items:
        config = db.query(QuoteItemConfig).filter(QuoteItemConfig.quote_item_id == item.id).first()
        unit = (product_config.loads(config.price_breakdown_json) or {}).get("configured") if config is not None else None
        if unit is None:
            offering = db.get(ProductOffering, item.product_offering_id)
            unit = offering.price if offering is not None else item.unit_price
        total += float(unit) * (item.quantity or 1)
    return round(total, 2)


def _price_floor(db: Session, quote: Quote) -> float:
    floor = 0.0
    for item in quote.items:
        offering = db.get(ProductOffering, item.product_offering_id)
        floor += (offering.price_floor or 0.0) * (item.quantity or 1) if offering is not None else 0.0
    return round(floor, 2)


def _discount_pct(list_total: float, price: float) -> float:
    if list_total <= 0:
        return 0.0
    return round(max(0.0, (1 - price / list_total) * 100), 2)


def approval_discount_pct(db: Session, quote: Quote) -> float:
    """The discount the approval matrix routes on - the negotiated price's
    effective discount when that is deeper than the requested one."""
    override = active(db, quote.id)
    if override is None:
        return quote.requested_discount_pct or 0.0
    return max(quote.requested_discount_pct or 0.0, override.effective_discount_pct)


def _check_editable(db: Session, tenant_id: int, quote: Quote) -> None:
    state = lifecycle_service.effective_state(db, tenant_id, "quote", quote)
    if quote.status == QuoteStatus.draft and not ponr_service.has_reached_ponr(db, tenant_id, "quote", state):
        return
    if quote.status == QuoteStatus.pending_approval:
        raise ValueError(f"Quote {quote.id} is waiting for approval at its current price - have it rejected, "
                         "or cancel it and requote, before negotiating a new price")
    if quote.status in (QuoteStatus.cancelled, QuoteStatus.superseded):
        raise ValueError(f"Quote {quote.id} is {quote.status.value} - its price can no longer change")
    raise ValueError(f"Quote {quote.id} is locked (state '{state}') - create a new revision (INT-30) and override its price")


def _end(row: QuotePriceOverride, why: str) -> None:
    row.active = False
    row.ended_reason = why
    row.ended_at = utcnow()


def apply(db: Session, tenant_id: int, quote: Quote, override_price: float, reason: str, user_id: int | None) -> dict[str, Any]:
    _check_editable(db, tenant_id, quote)
    if not quote.items:
        raise ValueError(f"Quote {quote.id} has no product to price")
    if override_price <= 0:
        raise ValueError("The negotiated price must be more than zero")
    reason = (reason or "").strip()
    if not reason:
        raise ValueError("Say why the price is being overridden - the customer's ask, the competing offer, the deal context")
    floor = _price_floor(db, quote)
    if override_price < floor:
        raise ValueError(f"{override_price:,.2f} is below the products' price floor ({floor:,.2f}) - no override can go under it")

    current = active(db, quote.id)
    computed = current.computed_price if current is not None else quote.total_price
    if current is not None:
        _end(current, "replaced")
    list_total = list_price(db, quote)
    row = QuotePriceOverride(
        tenant_id=tenant_id, quote_id=quote.id, list_price=list_total, computed_price=computed,
        override_price=round(override_price, 2), effective_discount_pct=_discount_pct(list_total, override_price),
        reason=reason, created_by_user_id=user_id,
    )
    db.add(row)
    quote.total_price = row.override_price
    db.flush()
    return {"quote_id": quote.id, "total_price": quote.total_price, "computed_price": computed, "list_price": list_total,
            "override_price": row.override_price, "adjustment": round(row.override_price - computed, 2),
            "effective_discount_pct": row.effective_discount_pct, "round": rounds(db, quote.id)}


def remove(db: Session, tenant_id: int, quote: Quote) -> dict[str, Any]:
    _check_editable(db, tenant_id, quote)
    current = active(db, quote.id)
    if current is None:
        raise ValueError(f"Quote {quote.id} has no price override to remove")
    _end(current, "removed")
    quote.total_price = current.computed_price
    db.flush()
    return {"quote_id": quote.id, "total_price": quote.total_price, "override_removed": True}


def end_on_reprice(db: Session, quote: Quote) -> bool:
    """The quote is being re-priced - its negotiated price no longer applies.
    The caller sets the new total."""
    current = active(db, quote.id)
    if current is None:
        return False
    _end(current, "repriced")
    db.flush()
    return True


def carry_over(db: Session, old: Quote, new: Quote) -> None:
    """A new revision priced as its predecessor keeps the negotiated price."""
    current = active(db, old.id)
    if current is None:
        return
    db.add(QuotePriceOverride(
        tenant_id=current.tenant_id, quote_id=new.id, list_price=current.list_price, computed_price=current.computed_price,
        override_price=current.override_price, effective_discount_pct=current.effective_discount_pct,
        reason=current.reason, created_by_user_id=current.created_by_user_id,
    ))
    db.flush()


def rounds(db: Session, quote_id: int) -> int:
    """How many negotiated prices the quote has had."""
    return db.query(QuotePriceOverride).filter(QuotePriceOverride.quote_id == quote_id).count()


def describe(db: Session, quote_id: int) -> dict[str, Any] | None:
    """The quote's current override and its negotiation history (None = never overridden)."""
    rows = db.query(QuotePriceOverride).filter(QuotePriceOverride.quote_id == quote_id).order_by(QuotePriceOverride.id).all()
    if not rows:
        return None

    def row(r: QuotePriceOverride) -> dict[str, Any]:
        return {"override_price": r.override_price, "computed_price": r.computed_price, "list_price": r.list_price,
                "effective_discount_pct": r.effective_discount_pct, "reason": r.reason, "by_user_id": r.created_by_user_id,
                "at": r.created_at, "active": r.active, "ended_reason": r.ended_reason}

    current = next((r for r in reversed(rows) if r.active), None)
    return {"active": row(current) if current is not None else None, "history": [row(r) for r in rows]}

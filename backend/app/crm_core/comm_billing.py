"""Communications billing, credit and fraud (Siebel Communications Guide,
chapters 10, 13 and 14).

  usage        rated usage events (voice / sms / data, international,
               premium-rate numbers), allowances (free minutes, messages,
               data - a balance group), usage history
  bills        the bill run: recurring charges for live services, one-time
               charges and credits already on the ledger, usage above the
               allowances, tax (unless the account is exempt) - one invoice
               per billing period; prepaid accounts draw down their balance and
               top up automatically; unbilled charges; a bill explained against
               the previous one; duplicate bills
  payments     at account level (oldest bill first), against one or several
               bills, or a billing profile - card / bank account by last 4
               digits only; left-over money stays on account as credit
  adjustments  on a whole bill, one bill line, unbilled usage or the account;
               small credits post at once, bigger ones wait for approval; the
               customer's decision on the outcome
  arrangements an instalment plan or a deferral, approved / countered /
               rejected by policy, its schedule paid down by payments
  collections  overdue bills open a credit alert and step through the
               collections ladder (reminders, letters, late fee, suspension,
               outside agency); paying up closes it
  fraud        thresholds per account (daily value, international, sensitive
               numbers, call length and count, simultaneous calls from two
               places) raise fraud alerts; work them with the customer

The ledger behind all of it is the existing billing account
(order_adapter.apply_billing_adjustment): every charge, credit and payment
moves its balance, so the balance is always what the customer owes.
"""

from __future__ import annotations

import datetime as dt
import json
import re
from typing import Any

from sqlalchemy.orm import Session

from app.crm_core import approval_service, comm_service as cs, om_service as om
from app.integration import order_adapter
from app.models.comms import (
    CmAccountDetail,
    CmAdjustmentRequest,
    CmAllowance,
    CmBillingProfile,
    CmCreditAlert,
    CmDuplicateRequest,
    CmFraudAlert,
    CmInvoice,
    CmInvoiceLine,
    CmPayment,
    CmPaymentArrangement,
    CmUsage,
)
from app.models.crm import Party
from app.models.order_mgmt import OmPayment, OmTaxRule
from app.models.orders import BillingAccount, BillingAdjustment, Order, OrderStatus
from app.timeutils import utcnow

_r = cs._r
_j = cs._j
_bool = cs._bool

ACTION_TYPES = ("no_action", "no_action_impose_late_fee", "no_action_write_off", "courtesy_phone_call", "demanding_phone_call",
                "courtesy_email_sms", "courtesy_dunning_letter", "harsh_dunning_letter", "suspend_services", "terminate_services",
                "refer_to_outside_agency", "payment_arrangement_requested", "payment_arrangement_agreed", "payment_received")
_FREQ_MONTHS = {"monthly": 1, "quarterly": 3, "annual": 12}


def _today() -> dt.date:
    return utcnow().date()


def _account(db: Session, tenant_id: int, party_id: int) -> BillingAccount:
    return order_adapter.get_or_create_billing_account(db, tenant_id, party_id)


def balance(db: Session, tenant_id: int, party_id: int) -> float:
    acct = db.query(BillingAccount).filter(BillingAccount.party_id == party_id).first()
    return _r(acct.balance) if acct else 0.0


def _post(db: Session, tenant_id: int, party_id: int, amount: float, reason: str, order_id: int | None = None) -> BillingAdjustment:
    acct = _account(db, tenant_id, party_id)
    return order_adapter.apply_billing_adjustment(db, acct.id, order_id, _r(amount), reason)


# ------------------------------------------------------------------ usage


def rate(db: Session, tenant_id: int, event_type: str, quantity: float, international: bool = False, premium: bool = False) -> float:
    rates = cs.settings(db, tenant_id)["usage_rates"]
    unit = float(rates.get(event_type, 0.0))
    mult = float(rates["premium_multiplier"]) if premium else float(rates["international_multiplier"]) if international else 1.0
    return _r(unit * quantity * mult)


def record_usage(db: Session, tenant_id: int, party_id: int, events: list[dict[str, Any]]) -> list[CmUsage]:
    cs._own(db, Party, party_id, tenant_id, "Customer")
    out = []
    for ev in events:
        etype = str(ev.get("event_type") or "voice").lower()
        if etype not in ("voice", "sms", "data"):
            raise ValueError("Usage is voice, sms or data")
        qty = float(ev.get("quantity") or 0)
        intl, prem = _bool(ev.get("international")), _bool(ev.get("premium"))
        at = ev.get("at")
        when = at if isinstance(at, dt.datetime) else dt.datetime.fromisoformat(str(at)[:19]) if at else utcnow()
        u = CmUsage(tenant_id=tenant_id, party_id=party_id, asset_id=ev.get("asset_id") or None, service=str(ev.get("service") or ""),
                    event_type=etype, at=when, quantity=qty, destination=str(ev.get("destination") or ""),
                    origin_location=str(ev.get("origin_location") or ""), destination_location=str(ev.get("destination_location") or ""),
                    international=intl, premium=prem, rate_plan="international" if intl else "premium" if prem else str(ev.get("rate_plan") or "standard"),
                    amount=float(ev["amount"]) if ev.get("amount") not in (None, "") else rate(db, tenant_id, etype, qty, intl, prem))
        db.add(u)
        out.append(u)
    db.flush()
    return out


def usage_out(u: CmUsage) -> dict[str, Any]:
    return {"id": u.id, "at": u.at, "event_type": u.event_type, "service": u.service, "quantity": u.quantity, "destination": u.destination,
            "origin_location": u.origin_location, "destination_location": u.destination_location, "international": u.international,
            "premium": u.premium, "rate_plan": u.rate_plan, "amount": u.amount, "billed": u.invoice_id is not None, "invoice_id": u.invoice_id}


def usage_history(db: Session, tenant_id: int, party_id: int | None = None, asset_ids: list[int] | None = None,
                  months: int = 6) -> list[dict[str, Any]]:
    """Month by month usage (quantity and charge per type)."""
    since = utcnow() - dt.timedelta(days=31 * months)
    q = db.query(CmUsage).filter(CmUsage.tenant_id == tenant_id, CmUsage.at >= since)
    if party_id:
        q = q.filter(CmUsage.party_id == party_id)
    if asset_ids is not None:
        q = q.filter(CmUsage.asset_id.in_(asset_ids or [0]))
    months_: dict[str, dict[str, Any]] = {}
    for u in q.all():
        key = u.at.strftime("%Y-%m")
        m = months_.setdefault(key, {"month": key, "voice": 0.0, "sms": 0.0, "data": 0.0, "amount": 0.0, "international": 0.0})
        m[u.event_type] = round(m[u.event_type] + u.quantity, 2)
        m["amount"] = _r(m["amount"] + u.amount)
        if u.international:
            m["international"] = _r(m["international"] + u.amount)
    return [months_[k] for k in sorted(months_)]


def allowances(db: Session, tenant_id: int, party_id: int, on: dt.date | None = None) -> list[CmAllowance]:
    on = on or _today()
    return [a for a in db.query(CmAllowance).filter(CmAllowance.tenant_id == tenant_id, CmAllowance.party_id == party_id).all()
            if a.valid_from <= on and (a.valid_to is None or a.valid_to >= on)]


def balance_groups(db: Session, tenant_id: int, party_id: int) -> list[dict[str, Any]]:
    """Each allowance with what is used this billing period and what is left."""
    start, end = current_period(db, tenant_id, party_id)
    out = []
    for a in allowances(db, tenant_id, party_id):
        used = sum(u.quantity for u in _usage(db, tenant_id, party_id, start, end) if u.event_type == a.event_type and not u.international and not u.premium)
        out.append({"id": a.id, "name": a.name, "event_type": a.event_type, "unit": a.unit, "granted": a.granted, "used": round(used, 2),
                    "remaining": round(max(0.0, a.granted - used), 2), "valid_from": a.valid_from, "valid_to": a.valid_to,
                    "period": [start, end]})
    return out


def _usage(db: Session, tenant_id: int, party_id: int, start: dt.date, end: dt.date, unbilled_only: bool = False) -> list[CmUsage]:
    q = db.query(CmUsage).filter(CmUsage.tenant_id == tenant_id, CmUsage.party_id == party_id,
                                 CmUsage.at >= dt.datetime.combine(start, dt.time.min), CmUsage.at <= dt.datetime.combine(end, dt.time.max))
    if unbilled_only:
        q = q.filter(CmUsage.invoice_id.is_(None))
    return q.order_by(CmUsage.at).all()


def _rated_usage(db: Session, tenant_id: int, party_id: int, rows: list[CmUsage], on: dt.date) -> list[dict[str, Any]]:
    """Usage per type after the allowances: [{event_type, quantity, free, amount}]"""
    out = []
    for etype in ("voice", "sms", "data"):
        domestic = [u for u in rows if u.event_type == etype and not u.international and not u.premium]
        special = [u for u in rows if u.event_type == etype and (u.international or u.premium)]
        free = sum(a.granted for a in allowances(db, tenant_id, party_id, on) if a.event_type == etype)
        qty = sum(u.quantity for u in domestic)
        amount = sum(u.amount for u in domestic)
        billable = amount * (max(0.0, qty - free) / qty) if qty > 0 else 0.0
        if domestic:
            out.append({"event_type": etype, "label": f"{etype.title()} usage", "quantity": round(qty, 2), "free": round(min(qty, free), 2),
                        "amount": _r(billable)})
        if special:
            out.append({"event_type": etype, "label": f"{etype.title()} - international / premium", "quantity": round(sum(u.quantity for u in special), 2),
                        "free": 0.0, "amount": _r(sum(u.amount for u in special))})
    return out


# ------------------------------------------------------------------ periods and unbilled charges


def _months_between(start: dt.date, end: dt.date) -> float:
    return round(((end - start).days + 1) / (365.0 / 12), 4)


def current_period(db: Session, tenant_id: int, party_id: int) -> tuple[dt.date, dt.date]:
    last = invoices_of(db, tenant_id, party_id)
    start = last[0].period_end + dt.timedelta(days=1) if last else _first_service_day(db, tenant_id, party_id)
    profile = cs.primary_profile(db, tenant_id, party_id)
    months = _FREQ_MONTHS.get(profile.frequency if profile else "monthly", 1)
    end = _add_months(start, months) - dt.timedelta(days=1)
    return start, end


def _first_service_day(db: Session, tenant_id: int, party_id: int) -> dt.date:
    first = db.query(Order).filter(Order.tenant_id == tenant_id, Order.party_id == party_id).order_by(Order.created_at).first()
    return first.created_at.date() if first else _today().replace(day=1)


def _add_months(d: dt.date, months: int) -> dt.date:
    m = d.month - 1 + months
    y, m = d.year + m // 12, m % 12 + 1
    last_day = [31, 29 if y % 4 == 0 and (y % 100 != 0 or y % 400 == 0) else 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31][m - 1]
    return dt.date(y, m, min(d.day, last_day))


def _settled_ledger_ids(db: Session, tenant_id: int, party_id: int) -> set[int]:
    """Ledger entries already on a bill, or that are a payment or an adjustment
    made against a bill (so they never land on another bill)."""
    ids = {x.billing_adjustment_id for x in db.query(CmInvoiceLine).join(CmInvoice, CmInvoice.id == CmInvoiceLine.invoice_id)
           .filter(CmInvoice.party_id == party_id, CmInvoiceLine.billing_adjustment_id.isnot(None))}
    ids |= {p.billing_adjustment_id for p in db.query(CmPayment).filter(CmPayment.party_id == party_id, CmPayment.billing_adjustment_id.isnot(None))}
    ids |= {a.billing_adjustment_id for a in db.query(CmAdjustmentRequest).filter(
        CmAdjustmentRequest.party_id == party_id, CmAdjustmentRequest.billing_adjustment_id.isnot(None),
        CmAdjustmentRequest.level.in_(("invoice", "line", "account")))}
    return ids


def _open_ledger(db: Session, tenant_id: int, party_id: int) -> list[BillingAdjustment]:
    acct = db.query(BillingAccount).filter(BillingAccount.party_id == party_id).first()
    if acct is None:
        return []
    done = _settled_ledger_ids(db, tenant_id, party_id)
    return [a for a in db.query(BillingAdjustment).filter(BillingAdjustment.billing_account_id == acct.id).order_by(BillingAdjustment.id)
            if a.id not in done]


def _tax_pct(db: Session, tenant_id: int, party_id: int) -> float:
    if cs.tax_exempt(db, tenant_id, party_id):
        return 0.0
    detail = db.query(CmAccountDetail).filter(CmAccountDetail.party_id == party_id).first()
    region = (detail.region if detail else "") or ""
    rules = db.query(OmTaxRule).filter(OmTaxRule.tenant_id == tenant_id, OmTaxRule.active.is_(True)).all()
    regional = [r for r in rules if region and cs._key(r.jurisdiction) == cs._key(region)]
    use = regional or [r for r in rules if r.jurisdiction.upper() == "ALL"]
    return sum(r.rate_pct for r in use if r.applies_to in ("all", "recurring", "service"))


def unbilled(db: Session, tenant_id: int, party_id: int) -> dict[str, Any]:
    start, end = current_period(db, tenant_id, party_id)
    today = min(_today(), end)
    months = _months_between(start, today) if today >= start else 0.0
    recurring = []
    for o in cs.live_orders(db, tenant_id, party_id):
        if o.status != OrderStatus.suspended and o.monthly_price:
            recurring.append({"order_id": o.id, "description": _service_name(db, o), "amount": _r(o.monthly_price * months)})
    ledger = [{"id": a.id, "description": a.reason, "amount": _r(a.amount)} for a in _open_ledger(db, tenant_id, party_id)]
    usage = _rated_usage(db, tenant_id, party_id, _usage(db, tenant_id, party_id, start, end, unbilled_only=True), today)
    rec_total = _r(sum(x["amount"] for x in recurring))
    usage_total = _r(sum(x["amount"] for x in usage))
    one_time = _r(sum(x["amount"] for x in ledger))
    surcharges = _r((rec_total + usage_total) * _tax_pct(db, tenant_id, party_id) / 100)
    return {"period_start": start, "period_end": end, "next_invoice_date": end + dt.timedelta(days=1), "recurring": rec_total,
            "non_recurring": one_time, "usage": usage_total, "surcharges": surcharges, "total": _r(rec_total + one_time + usage_total + surcharges),
            "items": {"recurring": recurring, "ledger": ledger, "usage": usage}}


def _service_name(db: Session, order: Order) -> str:
    names = [om._offering_name(db, i.product_offering_id) for i in order.items]
    return f"{', '.join(n for n in names if n) or 'Service'} (order {order.id})"


# ------------------------------------------------------------------ invoices


def invoices_of(db: Session, tenant_id: int, party_id: int) -> list[CmInvoice]:
    return (db.query(CmInvoice).filter(CmInvoice.tenant_id == tenant_id, CmInvoice.party_id == party_id)
            .order_by(CmInvoice.period_end.desc(), CmInvoice.id.desc()).all())


def due(inv: CmInvoice) -> float:
    return _r(inv.total - inv.paid - inv.credited)


def refresh_status(inv: CmInvoice, today: dt.date | None = None) -> None:
    today = today or _today()
    if inv.status == "written_off":
        return
    left = due(inv)
    if left <= 0.005:
        inv.status = "paid"
    elif inv.due_date < today:
        inv.status = "overdue"
    elif inv.paid + inv.credited > 0:
        inv.status = "partially_paid"
    else:
        inv.status = "open"


def invoice_of(db: Session, tenant_id: int, ref: Any, party_id: int | None = None) -> CmInvoice:
    text = str(ref or "").strip()
    if not text and party_id:
        rows = invoices_of(db, tenant_id, party_id)
        if not rows:
            raise ValueError("This customer has no bills yet - run a bill first")
        return rows[0]
    if text.upper().startswith("INV"):
        row = db.query(CmInvoice).filter(CmInvoice.tenant_id == tenant_id, CmInvoice.invoice_number == text.upper()).first()
        if row is None:
            raise ValueError(f"Bill {text} not found")
        return row
    return cs._own(db, CmInvoice, text, tenant_id, "Bill")


def invoice_out(db: Session, inv: CmInvoice, full: bool = False) -> dict[str, Any]:
    refresh_status(inv)
    out = {"id": inv.id, "billing_profile_id": inv.billing_profile_id, "invoice_number": inv.invoice_number, "party_id": inv.party_id, "customer": cs.party_name(db, inv.party_id),
           "period_start": inv.period_start, "period_end": inv.period_end, "issue_date": inv.issue_date, "due_date": inv.due_date,
           "recurring": inv.recurring, "one_time": inv.one_time, "usage": inv.usage, "adjustments": inv.adjustments, "taxes": inv.taxes,
           "total": inv.total, "paid": _r(inv.paid), "credited": _r(inv.credited), "balance_due": due(inv), "status": inv.status,
           "bill_media": inv.bill_media}
    if full:
        out["lines"] = [{"id": x.id, "kind": x.kind, "description": x.description, "service": x.service, "quantity": x.quantity,
                         "amount": x.amount} for x in db.query(CmInvoiceLine).filter(CmInvoiceLine.invoice_id == inv.id).order_by(CmInvoiceLine.id)]
        out["payments"] = [p for p in (payment_out(db, p) for p in db.query(CmPayment).filter(CmPayment.party_id == inv.party_id))
                           if any(a["invoice_id"] == inv.id for a in p["applied"])]
        out["adjustments_requested"] = [adjustment_out(a) for a in db.query(CmAdjustmentRequest).filter(CmAdjustmentRequest.invoice_id == inv.id)]
        out["arrangements"] = [arrangement_out(a) for a in db.query(CmPaymentArrangement).filter(CmPaymentArrangement.invoice_id == inv.id)]
        out["duplicates"] = [{"media": d.media, "address": d.address, "at": d.created_at}
                             for d in db.query(CmDuplicateRequest).filter(CmDuplicateRequest.invoice_id == inv.id)]
    return out


def bill_run(db: Session, tenant_id: int, party_id: int, period_end: Any = None) -> dict[str, Any]:
    party = cs._own(db, Party, party_id, tenant_id, "Customer")
    profile = cs.primary_profile(db, tenant_id, party.id, create=True)
    start, natural_end = current_period(db, tenant_id, party.id)
    end = cs._date(period_end) or min(natural_end, _today())
    if end < start:
        raise ValueError(f"{party.name} is billed up to {start - dt.timedelta(days=1)} - nothing to bill before {start}")
    months = _months_between(start, end)
    lines: list[dict[str, Any]] = []
    # 1. recurring charges for live services (suspended services are not charged)
    for o in cs.live_orders(db, tenant_id, party.id):
        if o.status == OrderStatus.suspended or not o.monthly_price:
            continue
        since = max(start, o.created_at.date())
        if since > end:
            continue
        amount = _r(o.monthly_price * _months_between(since, end))
        led = _post(db, tenant_id, party.id, amount, f"Monthly charges {since} - {end}: {_service_name(db, o)}", o.id)
        lines.append({"kind": "recurring", "description": f"{_service_name(db, o)} {since:%d %b} - {end:%d %b %Y}", "service": _service_name(db, o),
                      "quantity": round(_months_between(since, end), 2), "amount": amount, "order_id": o.id, "billing_adjustment_id": led.id})
    created_ids = {x["billing_adjustment_id"] for x in lines}
    # 2. one-time charges and credits already on the ledger
    for led in _open_ledger(db, tenant_id, party.id):
        if led.id in created_ids:
            continue
        lines.append({"kind": "one_time" if led.amount >= 0 else "adjustment", "description": led.reason, "service": "", "quantity": 1,
                      "amount": _r(led.amount), "order_id": led.order_id, "billing_adjustment_id": led.id})
    # 3. usage above the allowances
    rows = _usage(db, tenant_id, party.id, start, end, unbilled_only=True)
    for u in _rated_usage(db, tenant_id, party.id, rows, end):
        if u["amount"] <= 0 and not u["quantity"]:
            continue
        label = f"{u['label']}: {u['quantity']:g}" + (f" ({u['free']:g} included)" if u["free"] else "")
        led = _post(db, tenant_id, party.id, u["amount"], f"{label} {start} - {end}") if u["amount"] else None
        lines.append({"kind": "usage", "description": label, "service": "", "quantity": u["quantity"], "amount": u["amount"],
                      "order_id": None, "billing_adjustment_id": led.id if led else None})
    # 4. tax on recurring and usage charges
    pct = _tax_pct(db, tenant_id, party.id)
    taxable = sum(x["amount"] for x in lines if x["kind"] in ("recurring", "usage"))
    if pct and taxable > 0:
        tax = _r(taxable * pct / 100)
        led = _post(db, tenant_id, party.id, tax, f"Taxes {pct:g}% {start} - {end}")
        lines.append({"kind": "tax", "description": f"Taxes and surcharges ({pct:g}%)", "service": "", "quantity": 1, "amount": tax,
                      "order_id": None, "billing_adjustment_id": led.id})
    if not lines:
        raise ValueError(f"Nothing to bill for {party.name} from {start} to {end}")
    issue = _today()
    inv = CmInvoice(tenant_id=tenant_id, party_id=party.id, billing_profile_id=profile.id, period_start=start, period_end=end, issue_date=issue,
                    due_date=issue + dt.timedelta(days=profile.payment_terms_days), bill_media=profile.bill_media)
    db.add(inv)
    db.flush()
    inv.invoice_number = f"INV-{inv.id:06d}"
    for x in lines:
        db.add(CmInvoiceLine(tenant_id=tenant_id, invoice_id=inv.id, **x))
    for u in rows:
        u.invoice_id = inv.id
    inv.recurring = _r(sum(x["amount"] for x in lines if x["kind"] == "recurring"))
    inv.one_time = _r(sum(x["amount"] for x in lines if x["kind"] == "one_time"))
    inv.usage = _r(sum(x["amount"] for x in lines if x["kind"] == "usage"))
    inv.adjustments = _r(sum(x["amount"] for x in lines if x["kind"] == "adjustment"))
    inv.taxes = _r(sum(x["amount"] for x in lines if x["kind"] == "tax"))
    inv.total = _r(sum(x["amount"] for x in lines))
    db.flush()
    out: dict[str, Any] = {}
    # 5. order payments already captured settle what they paid for
    captured = _record_order_payments(db, tenant_id, party.id)
    # 6. prepaid accounts pay from their balance; credit on account is used up
    if profile.payment_type == "prepaid":
        out["prepaid"] = _draw_prepaid(db, tenant_id, profile, inv)
    _apply_credit(db, tenant_id, party.id)
    refresh_status(inv)
    db.flush()
    return {"party_id": party.id, "invoice_id": inv.id, "invoice": invoice_out(db, inv, full=True), "order_payments_applied": captured, **out}


def _record_order_payments(db: Session, tenant_id: int, party_id: int) -> float:
    done = {p.reference for p in db.query(CmPayment).filter(CmPayment.party_id == party_id, CmPayment.method == "order_payment")}
    total = 0.0
    for p in db.query(OmPayment).filter(OmPayment.tenant_id == tenant_id, OmPayment.party_id == party_id, OmPayment.status == "captured").all():
        ref = f"om:{p.id}"
        if ref in done or p.amount <= 0:
            continue
        _make_payment(db, tenant_id, party_id, p.amount, "order_payment", reference=ref, card_brand=p.card_brand, card_last4=p.card_last4)
        total += p.amount
    return _r(total)


def _draw_prepaid(db: Session, tenant_id: int, profile: CmBillingProfile, inv: CmInvoice) -> dict[str, Any]:
    out: dict[str, Any] = {"drawn": 0.0, "topped_up": 0.0}
    if profile.prepaid_balance < inv.total and profile.auto_topup and profile.topup_amount > 0:
        need = inv.total - profile.prepaid_balance
        n = max(1, int(-(-need // profile.topup_amount)))
        profile.prepaid_balance += n * profile.topup_amount
        out["topped_up"] = _r(n * profile.topup_amount)
    draw = _r(min(profile.prepaid_balance, due(inv)))
    if draw > 0:
        profile.prepaid_balance = _r(profile.prepaid_balance - draw)
        _make_payment(db, tenant_id, inv.party_id, draw, "prepaid_balance", invoices=[inv], reference="prepaid", billing_profile_id=profile.id)
        out["drawn"] = draw
    if profile.auto_topup and profile.prepaid_balance <= profile.topup_threshold and profile.topup_amount > 0:
        profile.prepaid_balance += profile.topup_amount
        out["topped_up"] = _r(out["topped_up"] + profile.topup_amount)
    out["prepaid_balance"] = _r(profile.prepaid_balance)
    return out


def explain(db: Session, tenant_id: int, e: dict[str, Any]) -> dict[str, Any]:
    """A bill explained: its lines by kind, against the previous bill, the usage behind it."""
    party = cs.party_of(db, tenant_id, e, required=False)
    inv = invoice_of(db, tenant_id, e.get("invoice"), party.id if party else None) if (e.get("invoice") or party) else None
    if inv is None:
        raise ValueError("Which bill (or customer)?")
    rows = invoices_of(db, tenant_id, inv.party_id)
    prev = next((r for r in rows if r.period_end < inv.period_start), None)
    changes = []
    if prev:
        for kind in ("recurring", "one_time", "usage", "adjustments", "taxes", "total"):
            a, b = getattr(prev, kind), getattr(inv, kind)
            if abs(b - a) >= 0.01:
                changes.append({"part": kind, "previous": a, "now": b, "change": _r(b - a),
                                "pct": round((b - a) / a * 100) if a else None})
    usage = [usage_out(u) for u in db.query(CmUsage).filter(CmUsage.invoice_id == inv.id).order_by(CmUsage.amount.desc()).limit(200)]
    top: dict[str, dict[str, Any]] = {}
    for u in usage:
        if u["destination"]:
            t = top.setdefault(u["destination"], {"destination": u["destination"], "events": 0, "quantity": 0.0, "amount": 0.0,
                                                   "international": u["international"], "location": u["destination_location"]})
            t["events"] += 1
            t["quantity"] = round(t["quantity"] + u["quantity"], 2)
            t["amount"] = _r(t["amount"] + u["amount"])
    notes = []
    if prev and inv.total > prev.total * 1.3 and prev.total > 0:
        notes.append(f"This bill is {round((inv.total / prev.total - 1) * 100)}% higher than the last one")
    intl = sum(u["amount"] for u in usage if u["international"])
    if intl:
        notes.append(f"{intl:,.2f} of international usage")
    if inv.taxes and cs.tax_exempt(db, tenant_id, inv.party_id):
        notes.append("Taxes were charged but the account is now tax exempt - consider an adjustment")
    return {"party_id": inv.party_id, "invoice_id": inv.id, "invoice": invoice_out(db, inv, full=True), "previous": invoice_out(db, prev) if prev else None,
            "changes": changes, "top_destinations": sorted(top.values(), key=lambda x: -x["amount"])[:8], "usage": usage[:50],
            "notes": notes, "unbilled": unbilled(db, tenant_id, inv.party_id)}


def duplicate(db: Session, tenant_id: int, e: dict[str, Any]) -> dict[str, Any]:
    party = cs.party_of(db, tenant_id, e, required=False)
    inv = invoice_of(db, tenant_id, e.get("invoice"), party.id if party else None)
    media = str(e.get("bill_media") or inv.bill_media or "email").lower()
    if media not in ("paper", "email", "portal", "sms"):
        raise ValueError("Send it by paper, email, portal or sms")
    address = str(e.get("address") or "")
    if not address:
        profile = cs.primary_profile(db, tenant_id, inv.party_id)
        address = (profile.email if media == "email" else profile.address) if profile else ""
    if media in ("email", "paper") and not address:
        raise ValueError("Where to send the copy (an address or email)?")
    row = CmDuplicateRequest(tenant_id=tenant_id, invoice_id=inv.id, media=media, address=address, comments=str(e.get("comments") or "")[:300])
    db.add(row)
    db.flush()
    return {"invoice_id": inv.id, "invoice_number": inv.invoice_number, "media": media, "sent_to": address, "request_id": row.id}


# ------------------------------------------------------------------ payments


def payment_out(db: Session, p: CmPayment) -> dict[str, Any]:
    return {"id": p.id, "billing_profile_id": p.billing_profile_id, "payment_number": p.payment_number, "party_id": p.party_id, "amount": p.amount, "method": p.method,
            "card": f"{p.card_brand or 'card'} ending {p.card_last4}" if p.card_last4 else "", "bank": f"ending {p.bank_last4}" if p.bank_last4 else "",
            "reference": p.reference, "location": p.location, "status": p.status, "unapplied": p.unapplied, "received_at": p.received_at,
            "applied": [{"invoice_id": a["invoice_id"], "invoice_number": (db.get(CmInvoice, a["invoice_id"]).invoice_number
                                                                          if db.get(CmInvoice, a["invoice_id"]) else ""), "amount": a["amount"]}
                        for a in _j(p.applied_json, [])]}


def _make_payment(db: Session, tenant_id: int, party_id: int, amount: float, method: str, invoices: list[CmInvoice] | None = None,
                  **extra: Any) -> CmPayment:
    amount = _r(amount)
    p = CmPayment(tenant_id=tenant_id, party_id=party_id, amount=amount, method=method, **extra)
    db.add(p)
    db.flush()
    p.payment_number = f"PMT-{p.id:06d}"
    # money in: the balance goes down
    led = _post(db, tenant_id, party_id, -amount, f"Payment {p.payment_number} ({method.replace('_', ' ')})")
    p.billing_adjustment_id = led.id
    left = amount
    applied = []
    targets = invoices if invoices is not None else [i for i in reversed(invoices_of(db, tenant_id, party_id)) if due(i) > 0]
    for inv in targets:
        if left <= 0:
            break
        take = _r(min(left, due(inv)))
        if take <= 0:
            continue
        inv.paid = _r(inv.paid + take)
        refresh_status(inv)
        applied.append({"invoice_id": inv.id, "amount": take})
        left = _r(left - take)
    p.applied_json = json.dumps(applied)
    p.unapplied = left
    db.flush()
    return p


def _apply_credit(db: Session, tenant_id: int, party_id: int) -> None:
    """Money left on account pays the oldest open bills."""
    for p in db.query(CmPayment).filter(CmPayment.party_id == party_id, CmPayment.unapplied > 0.005, CmPayment.status == "posted").order_by(CmPayment.id):
        applied = _j(p.applied_json, [])
        for inv in reversed(invoices_of(db, tenant_id, party_id)):
            if p.unapplied <= 0.005:
                break
            take = _r(min(p.unapplied, due(inv)))
            if take <= 0:
                continue
            inv.paid = _r(inv.paid + take)
            refresh_status(inv)
            applied.append({"invoice_id": inv.id, "amount": take})
            p.unapplied = _r(p.unapplied - take)
        p.applied_json = json.dumps(applied)
    db.flush()


_PAY_METHODS = {"card": "credit_card", "credit card": "credit_card", "credit_card": "credit_card", "debit card": "credit_card",
                "eft": "eft", "bank": "eft", "direct debit": "eft", "auto_debit": "eft", "bank transfer": "bank_transfer",
                "transfer": "bank_transfer", "bank_transfer": "bank_transfer", "cash": "cash", "cheque": "cheque", "check": "cheque",
                "reference": "reference", "post office": "reference", "bank counter": "reference"}


def take_payment(db: Session, tenant_id: int, e: dict[str, Any]) -> dict[str, Any]:
    cs.no_card_numbers(e.get("card_last4"), e.get("bank_last4"), e.get("reference"))
    party = cs.party_of(db, tenant_id, e, required=False)
    invoices: list[CmInvoice] | None = None
    refs = [x for x in re.split(r"[,\s]+(?:and\s+)?", str(e.get("invoice") or "").strip()) if x and x.lower() != "and"]
    if refs:
        invoices = [invoice_of(db, tenant_id, r) for r in refs]
        if party is None:
            party = db.get(Party, invoices[0].party_id)
        if any(i.party_id != party.id for i in invoices):
            raise ValueError("Those bills belong to different customers")
    alert = None
    if e.get("credit_alert"):
        alert = credit_alert_of(db, tenant_id, e["credit_alert"])
        party = party or db.get(Party, alert.party_id)
    if party is None:
        raise ValueError("Which customer (or bill) is paying?")
    method = _PAY_METHODS.get(str(e.get("payment_method") or "").strip().lower().replace("_", " "),
                              _PAY_METHODS.get(str(e.get("payment_method") or "").strip().lower()))
    if method is None:
        raise ValueError("Payment method: credit_card, eft, bank_transfer, cash, cheque or reference")
    card = cs.last4(e.get("card_last4"), "Card") if e.get("card_last4") not in (None, "") else ""
    bank = cs.last4(e.get("bank_last4"), "Bank account") if e.get("bank_last4") not in (None, "") else ""
    profile = None
    if method == "credit_card" and not card:
        profile = cs.primary_profile(db, tenant_id, party.id)
        if profile and profile.card_last4 and not profile.card_blocked:
            card = profile.card_last4
        else:
            raise ValueError("Which card? (its last 4 digits)")
    if method == "eft" and not bank:
        profile = cs.primary_profile(db, tenant_id, party.id)
        if profile and profile.bank_last4:
            bank = profile.bank_last4
        else:
            raise ValueError("Which bank account? (its last 4 digits)")
    if method == "reference" and not e.get("reference"):
        raise ValueError("A payment made at a bank or post office needs its reference number")
    if e.get("amount") not in (None, ""):
        amount = _r(float(e["amount"]))
    elif invoices:
        amount = _r(sum(due(i) for i in invoices))
    else:
        amount = max(0.0, balance(db, tenant_id, party.id))
    if amount <= 0:
        raise ValueError(f"{party.name} owes nothing - give an amount to pay in advance")
    p = _make_payment(db, tenant_id, party.id, amount, method, invoices=invoices, card_brand=str(e.get("card_brand") or "").lower()[:20],
                      card_last4=card, bank_last4=bank, reference=str(e.get("reference") or "")[:100], location=str(e.get("location") or "")[:100],
                      credit_alert_id=alert.id if alert else None)
    if invoices is not None and p.unapplied > 0:
        _apply_credit(db, tenant_id, party.id)
    plan = _pay_arrangements(db, tenant_id, party.id, amount)
    closed = _check_collections_exit(db, tenant_id, party.id)
    if alert and alert.status in ("open", "pending"):
        _alert_history(alert, "payment_received", f"{amount:,.2f} paid ({p.payment_number})")
        alert.action_type = "payment_received"
    db.flush()
    return {"party_id": party.id, "payment_id": p.id, "confirmation": p.payment_number, "payment": payment_out(db, p),
            "balance": balance(db, tenant_id, party.id), "arrangement_installments_paid": plan, "collections_closed": closed}


# ------------------------------------------------------------------ adjustments


def adjustment_out(a: CmAdjustmentRequest) -> dict[str, Any]:
    return {"id": a.id, "request_number": a.request_number, "party_id": a.party_id, "level": a.level, "invoice_id": a.invoice_id,
            "invoice_line_id": a.invoice_line_id, "type": a.adjustment_type, "reason": a.reason, "comments": a.comments,
            "original_amount": a.original_amount, "requested_amount": a.requested_amount, "requested_pct": a.requested_pct,
            "approved_amount": a.approved_amount, "status": a.status, "status_description": a.status_description,
            "customer_decision": a.customer_decision, "created_at": a.created_at}


_REASONS = ("billing_error", "partner_code", "special_credit", "goodwill", "service_outage", "tax_exemption", "write_off", "other")


def request_adjustment(db: Session, tenant_id: int, e: dict[str, Any]) -> dict[str, Any]:
    party = cs.party_of(db, tenant_id, e, required=False)
    if e.get("adjustment") and e.get("decision"):
        return approve_adjustment(db, tenant_id, e)
    inv = invoice_of(db, tenant_id, e["invoice"]) if e.get("invoice") else None
    if inv is not None:
        party = party or db.get(Party, inv.party_id)
    if party is None:
        raise ValueError("Which customer (or bill)?")
    level, line, usage = "account", None, None
    original = max(0.0, balance(db, tenant_id, party.id))
    if e.get("usage_id"):
        usage = cs._own(db, CmUsage, e["usage_id"], tenant_id, "Usage event")
        if usage.invoice_id:
            inv = db.get(CmInvoice, usage.invoice_id)
        else:
            level, original = "unbilled", usage.amount
    if inv is not None and level != "unbilled":
        level, original = "invoice", inv.total
        if e.get("line"):
            line = _find_invoice_line(db, inv, e["line"])
            level, original = "line", line.amount
    kind = str(e.get("adjustment_type") or "credit").lower()
    if kind not in ("credit", "debit"):
        raise ValueError("A credit or a debit adjustment?")
    reason = str(e.get("reason") or "billing_error").strip().lower().replace(" ", "_")
    reason = {"error": "billing_error", "billing": "billing_error", "outage": "service_outage", "exempt": "tax_exemption",
              "tax": "tax_exemption", "goodwill_credit": "goodwill"}.get(reason, reason)
    if reason not in _REASONS:
        reason = "other"
    pct = float(e["percent"]) if e.get("percent") not in (None, "") else None
    if e.get("amount") not in (None, ""):
        amount = _r(float(e["amount"]))
    elif pct is not None:
        amount = _r(original * pct / 100)
    else:
        amount = _r(original)
    if amount <= 0:
        raise ValueError("Adjust by how much?")
    if kind == "credit" and level in ("invoice", "line", "unbilled") and amount > original + 0.005:
        raise ValueError(f"A credit can't be more than the {original:,.2f} it adjusts")
    is_admin = e.get("_actor_role") == "admin"
    write_off_rule = reason == "write_off" and not is_admin and kind == "credit" and \
        approval_service.resolve_monetary(db, tenant_id, "adjustment", 100.0, None, amount) is not None
    if reason == "write_off" and not is_admin and not write_off_rule:
        raise ValueError("Only a supervisor (admin) can write a balance off")
    a = CmAdjustmentRequest(tenant_id=tenant_id, party_id=party.id, level=level, invoice_id=inv.id if inv else None,
                            invoice_line_id=line.id if line else None, usage_id=usage.id if usage else None, adjustment_type=kind, reason=reason,
                            comments=str(e.get("comments") or "")[:400], original_amount=_r(original), requested_amount=amount, requested_pct=pct)
    if e.get("credit_alert"):
        a.credit_alert_id = credit_alert_of(db, tenant_id, e["credit_alert"]).id
    db.add(a)
    db.flush()
    a.request_number = f"ADJ-{a.id:06d}"
    cfg = cs.settings(db, tenant_id)
    share = amount / original * 100 if original else 100.0
    auto = kind == "debit" or is_admin or (amount <= cfg["adjustment_auto_amount"] and share <= cfg["adjustment_auto_pct"])
    if write_off_rule:
        auto = False
    chain = None
    if not auto:
        # the approval matrix names the approver when the tenant has a rule for adjustments
        chain = approval_service.open_embedded(
            db, tenant_id, "adjustment", a.id, discount_pct=100.0 if write_off_rule else min(share, 100.0), amount=amount,
            summary=f"{a.request_number}: {kind} {amount:,.2f} ({reason.replace('_', ' ')}) for {party.name}",
            requested_by=int(e["_actor_user_id"]) if e.get("_actor_user_id") else None,
        )
    if auto:
        _post_adjustment(db, tenant_id, a, amount)
    elif chain is not None:
        a.status = "pending_approval"
        a.status_description = f"Waiting for {approval_service.waiting_for(chain)} ({chain.template.name})"
    else:
        a.status = "pending_approval"
        a.status_description = (f"{amount:,.2f} ({share:.0f}%) is over the {cfg['adjustment_auto_amount']:,.0f} / "
                                f"{cfg['adjustment_auto_pct']:g}% limit an agent can credit - a supervisor decides")
    db.flush()
    return {"party_id": party.id, "adjustment_id": a.id, "adjustment": adjustment_out(a), "balance": balance(db, tenant_id, party.id),
            "invoice": invoice_out(db, inv) if inv else None}


def _find_invoice_line(db: Session, inv: CmInvoice, ref: Any) -> CmInvoiceLine:
    rows = db.query(CmInvoiceLine).filter(CmInvoiceLine.invoice_id == inv.id).order_by(CmInvoiceLine.id).all()
    text = str(ref).strip()
    if text.isdigit():
        n = int(text)
        hit = next((x for x in rows if x.id == n), None) or (rows[n - 1] if 0 < n <= len(rows) else None)
        if hit:
            return hit
    key = cs._key(text)
    hit = next((x for x in rows if key and (key in cs._key(x.description) or key == cs._key(x.kind))), None)
    if hit is None:
        raise ValueError(f"No line '{text}' on {inv.invoice_number}: " + "; ".join(f"{i + 1}. {x.description}" for i, x in enumerate(rows)))
    return hit


def _post_adjustment(db: Session, tenant_id: int, a: CmAdjustmentRequest, amount: float) -> None:
    amount = _r(amount)
    sign = -1 if a.adjustment_type == "credit" else 1
    label = {"invoice": f"bill {db.get(CmInvoice, a.invoice_id).invoice_number}" if a.invoice_id else "",
             "line": f"a line of bill {db.get(CmInvoice, a.invoice_id).invoice_number}" if a.invoice_id else "",
             "unbilled": "unbilled usage", "account": "the account"}[a.level]
    led = _post(db, tenant_id, a.party_id, sign * amount, f"{a.adjustment_type.title()} {a.request_number} on {label} ({a.reason.replace('_', ' ')})")
    a.billing_adjustment_id = led.id
    a.approved_amount = amount
    a.approved_at = utcnow()
    a.status = "posted" if amount >= a.requested_amount - 0.005 else "partially_posted"
    a.status_description = a.status_description if a.status == "partially_posted" else ""
    if a.adjustment_type == "credit" and a.invoice_id and a.level in ("invoice", "line"):
        inv = db.get(CmInvoice, a.invoice_id)
        inv.credited = _r(inv.credited + amount)
        if a.reason == "write_off":
            inv.status = "written_off"
        refresh_status(inv)
    elif a.adjustment_type == "credit" and a.level == "account":
        # an account credit pays the oldest bills down
        left = amount
        for inv in reversed(invoices_of(db, tenant_id, a.party_id)):
            take = _r(min(left, due(inv)))
            if take <= 0:
                continue
            inv.credited = _r(inv.credited + take)
            if a.reason == "write_off":
                inv.status = "written_off"
            refresh_status(inv)
            left = _r(left - take)
            if left <= 0:
                break
    _check_collections_exit(db, tenant_id, a.party_id)


def approve_adjustment(db: Session, tenant_id: int, e: dict[str, Any]) -> dict[str, Any]:
    a = _adjustment_of(db, tenant_id, e["adjustment"])
    chain = approval_service.pending_embedded(db, "adjustment", a.id)
    if chain is None and e.get("_actor_role") != "admin":
        raise ValueError("Only a supervisor (admin) decides an adjustment waiting for approval")
    if a.status != "pending_approval":
        raise ValueError(f"{a.request_number} is {a.status.replace('_', ' ')} - nothing to decide")
    decision = str(e.get("decision")).lower()
    if chain is not None:
        approval_service.decide(db, chain, "reject" if decision.startswith("reject") else "approve",
                                str(e.get("comments") or e.get("reasoning") or ""), int(e["_actor_user_id"]) if e.get("_actor_user_id") else None)
        if chain.status.value == "pending":
            a.status_description = f"Waiting for {approval_service.waiting_for(chain)} ({chain.template.name})"
            db.flush()
            return {"party_id": a.party_id, "adjustment_id": a.id, "adjustment": adjustment_out(a), "balance": balance(db, tenant_id, a.party_id)}
    if decision.startswith("reject"):
        a.status, a.status_description = "not_posted", str(e.get("comments") or "Rejected by the supervisor")[:300]
    else:
        amount = _r(float(e["amount"])) if e.get("amount") not in (None, "") else a.requested_amount
        if amount <= 0 or amount > a.requested_amount + 0.005:
            raise ValueError(f"Approve between 0 and {a.requested_amount:,.2f}")
        if amount < a.requested_amount:
            a.status_description = f"Approved {amount:,.2f} of the {a.requested_amount:,.2f} asked for"
        _post_adjustment(db, tenant_id, a, amount)
    db.flush()
    return {"party_id": a.party_id, "adjustment_id": a.id, "adjustment": adjustment_out(a), "balance": balance(db, tenant_id, a.party_id)}


def adjustment_decided(db: Session, tenant_id: int, chain: Any) -> dict[str, Any]:
    """An adjustment's approval decided from the approvals queue (Approve or reject)."""
    a = cs._own(db, CmAdjustmentRequest, chain.entity_id, tenant_id, "Adjustment")
    if a.status == "pending_approval":
        if chain.status.value == "approved":
            _post_adjustment(db, tenant_id, a, a.requested_amount)
        else:
            step = next((s for s in reversed(chain.steps) if s.decided_at), None)
            a.status, a.status_description = "not_posted", ((step.reasoning if step else "") or f"Rejected ({chain.template.name})")[:300]
        db.flush()
    return {"party_id": a.party_id, "adjustment_id": a.id, "adjustment": adjustment_out(a)}


def _adjustment_of(db: Session, tenant_id: int, ref: Any) -> CmAdjustmentRequest:
    text = str(ref or "").strip()
    if text.upper().startswith("ADJ"):
        row = db.query(CmAdjustmentRequest).filter(CmAdjustmentRequest.tenant_id == tenant_id, CmAdjustmentRequest.request_number == text.upper()).first()
        if row is None:
            raise ValueError(f"Adjustment {text} not found")
        return row
    return cs._own(db, CmAdjustmentRequest, text, tenant_id, "Adjustment")


# ------------------------------------------------------------------ payment arrangements


def arrangement_out(a: CmPaymentArrangement) -> dict[str, Any]:
    return {"id": a.id, "number": a.arrangement_number, "party_id": a.party_id, "invoice_id": a.invoice_id, "plan_type": a.plan_type,
            "installments": a.installments, "original_amount": a.original_amount, "installment_amount": a.installment_amount,
            "interest_charge": a.interest_charge, "total_due": a.total_due, "start_date": a.start_date, "end_date": a.end_date,
            "status": a.status, "outcome": a.outcome, "customer_decision": a.customer_decision, "schedule": _j(a.schedule_json, []),
            "comments": a.comments}


def request_arrangement(db: Session, tenant_id: int, e: dict[str, Any]) -> dict[str, Any]:
    party = cs.party_of(db, tenant_id, e, required=False)
    inv = invoice_of(db, tenant_id, e["invoice"]) if e.get("invoice") else None
    alert = credit_alert_of(db, tenant_id, e["credit_alert"]) if e.get("credit_alert") else None
    party = party or (db.get(Party, inv.party_id) if inv else db.get(Party, alert.party_id) if alert else None)
    if party is None:
        raise ValueError("Which customer (or bill)?")
    if e.get("arrangement") and e.get("decision"):
        return decide_arrangement(db, tenant_id, e)
    owed = _r(float(e["amount"])) if e.get("amount") not in (None, "") else due(inv) if inv else alert.amount_owed if alert else max(0.0, balance(db, tenant_id, party.id))
    if owed <= 0:
        raise ValueError(f"{party.name} owes nothing to arrange")
    active = db.query(CmPaymentArrangement).filter(CmPaymentArrangement.party_id == party.id,
                                                   CmPaymentArrangement.status.in_(("pending", "approved", "countered", "active"))).first()
    if active:
        raise ValueError(f"{party.name} already has arrangement {active.arrangement_number} ({active.status})")
    cfg = cs.settings(db, tenant_id)
    plan = str(e.get("plan_type") or "").lower() or ("deferral" if str(e.get("installments") or "") in ("", "1") and e.get("start_date") else "installment")
    if plan not in ("installment", "deferral"):
        raise ValueError("Plan: installment or deferral")
    n = 1 if plan == "deferral" else max(2, int(float(e.get("installments") or 3)))
    start = cs._date(e.get("start_date")) or (_today() + dt.timedelta(days=30 if plan == "deferral" else 7))
    a = CmPaymentArrangement(tenant_id=tenant_id, party_id=party.id, invoice_id=inv.id if inv else None, credit_alert_id=alert.id if alert else None,
                             plan_type=plan, installments=n, original_amount=owed, original_due_date=inv.due_date if inv else None,
                             start_date=start, end_date=start, comments=str(e.get("comments") or "")[:400])
    db.add(a)
    db.flush()
    a.arrangement_number = f"PA-{a.id:06d}"
    broken = db.query(CmPaymentArrangement).filter(CmPaymentArrangement.party_id == party.id, CmPaymentArrangement.status == "broken",
                                                   CmPaymentArrangement.created_at >= utcnow() - dt.timedelta(days=365)).count()
    if broken:
        a.status, a.outcome = "rejected", "A payment arrangement was broken in the last 12 months - pay the balance or ask a supervisor"
    elif plan == "deferral" and (start - _today()).days > cfg["arrangement_max_deferral_days"]:
        start = _today() + dt.timedelta(days=int(cfg["arrangement_max_deferral_days"]))
        a.status, a.outcome = "countered", f"Deferral offered until {start} (at most {cfg['arrangement_max_deferral_days']} days)"
    elif n > cfg["arrangement_max_installments"]:
        n = int(cfg["arrangement_max_installments"])
        a.status, a.outcome = "countered", f"Offered {n} instalments - the most the policy allows"
    elif owed > cfg["arrangement_auto_amount"] and e.get("_actor_role") != "admin":
        a.status, a.outcome = "pending", f"Over {cfg['arrangement_auto_amount']:,.0f} - a supervisor decides"
        chain = approval_service.open_embedded(
            db, tenant_id, "arrangement", a.id, amount=owed, summary=f"{a.arrangement_number}: {plan} of {owed:,.2f} for {party.name}",
            requested_by=int(e["_actor_user_id"]) if e.get("_actor_user_id") else None,
        )
        if chain is not None:
            a.outcome = f"Over {cfg['arrangement_auto_amount']:,.0f} - waiting for {approval_service.waiting_for(chain)}"
    else:
        a.status, a.outcome = "approved", "Approved by policy"
    _schedule(a, n, start, owed, float(cfg["arrangement_interest_pct"]))
    if alert:
        alert.action_type, alert.status = "payment_arrangement_requested", "pending"
        _alert_history(alert, "payment_arrangement_requested", f"{a.arrangement_number}: {a.outcome}")
    db.flush()
    return {"party_id": party.id, "arrangement_id": a.id, "arrangement": arrangement_out(a)}


def _schedule(a: CmPaymentArrangement, n: int, start: dt.date, owed: float, interest_pct: float) -> None:
    a.installments = n
    a.start_date = start
    a.interest_charge = _r(owed * interest_pct / 100 * (n - 1)) if n > 1 else 0.0
    a.total_due = _r(owed + a.interest_charge)
    each = _r(a.total_due / n)
    rows = []
    for i in range(n):
        amount = each if i < n - 1 else _r(a.total_due - each * (n - 1))
        rows.append({"due": _add_months(start, i).isoformat(), "amount": amount, "paid": 0.0})
    a.schedule_json = json.dumps(rows)
    a.installment_amount = each
    a.end_date = _add_months(start, n - 1)


def decide_arrangement(db: Session, tenant_id: int, e: dict[str, Any]) -> dict[str, Any]:
    a = _arrangement_of(db, tenant_id, e["arrangement"])
    chain = approval_service.pending_embedded(db, "arrangement", a.id)
    if chain is None and e.get("_actor_role") != "admin":
        raise ValueError("Only a supervisor (admin) decides an arrangement")
    if a.status != "pending":
        raise ValueError(f"{a.arrangement_number} is {a.status} - nothing to decide")
    if chain is not None:
        approval_service.decide(db, chain, "reject" if str(e.get("decision")).lower().startswith("reject") else "approve",
                                str(e.get("comments") or e.get("reasoning") or ""), int(e["_actor_user_id"]) if e.get("_actor_user_id") else None)
        if chain.status.value == "pending":
            a.outcome = f"Waiting for {approval_service.waiting_for(chain)}"
            db.flush()
            return {"party_id": a.party_id, "arrangement_id": a.id, "arrangement": arrangement_out(a)}
    if str(e.get("decision")).lower().startswith("reject"):
        a.status, a.outcome = "rejected", str(e.get("comments") or "Rejected by the supervisor")[:300]
    else:
        a.status, a.outcome = "approved", "Approved by the supervisor"
    db.flush()
    return {"party_id": a.party_id, "arrangement_id": a.id, "arrangement": arrangement_out(a)}


def arrangement_decided(db: Session, tenant_id: int, chain: Any) -> dict[str, Any]:
    """An arrangement's approval decided from the approvals queue (Approve or reject)."""
    a = cs._own(db, CmPaymentArrangement, chain.entity_id, tenant_id, "Arrangement")
    if a.status == "pending":
        if chain.status.value == "approved":
            a.status, a.outcome = "approved", f"Approved ({chain.template.name})"
        else:
            step = next((s for s in reversed(chain.steps) if s.decided_at), None)
            a.status, a.outcome = "rejected", ((step.reasoning if step else "") or f"Rejected ({chain.template.name})")[:300]
        db.flush()
    return {"party_id": a.party_id, "arrangement_id": a.id, "arrangement": arrangement_out(a)}


def _arrangement_of(db: Session, tenant_id: int, ref: Any) -> CmPaymentArrangement:
    text = str(ref or "").strip()
    if text.upper().startswith("PA"):
        row = db.query(CmPaymentArrangement).filter(CmPaymentArrangement.tenant_id == tenant_id,
                                                    CmPaymentArrangement.arrangement_number == text.upper()).first()
        if row is None:
            raise ValueError(f"Arrangement {text} not found")
        return row
    return cs._own(db, CmPaymentArrangement, text, tenant_id, "Arrangement")


def _pay_arrangements(db: Session, tenant_id: int, party_id: int, amount: float) -> int:
    paid = 0
    for a in db.query(CmPaymentArrangement).filter(CmPaymentArrangement.party_id == party_id, CmPaymentArrangement.status == "active").all():
        rows = _j(a.schedule_json, [])
        left = amount
        for r in rows:
            need = _r(r["amount"] - r["paid"])
            if need <= 0 or left <= 0:
                continue
            take = _r(min(need, left))
            r["paid"] = _r(r["paid"] + take)
            left = _r(left - take)
            if r["paid"] >= r["amount"] - 0.005:
                paid += 1
        a.schedule_json = json.dumps(rows)
        if all(r["paid"] >= r["amount"] - 0.005 for r in rows):
            a.status = "completed"
    return paid


def record_decision(db: Session, tenant_id: int, e: dict[str, Any]) -> dict[str, Any]:
    """The customer accepts or rejects the outcome of an adjustment or an arrangement."""
    decision = str(e.get("customer_decision") or "").lower()
    decision = "accepted" if decision.startswith("accept") or decision in ("yes", "agree", "agreed") else "rejected" if decision.startswith(("reject", "no", "decline")) else ""
    if not decision:
        raise ValueError("Did the customer accept or reject it?")
    if e.get("adjustment"):
        a = _adjustment_of(db, tenant_id, e["adjustment"])
        if a.status in ("submitted", "pending_approval"):
            raise ValueError(f"{a.request_number} has no outcome yet")
        a.customer_decision, a.decided_at = decision, utcnow()
        db.flush()
        return {"party_id": a.party_id, "adjustment_id": a.id, "adjustment": adjustment_out(a),
                "next": "Discuss the options again and submit another request" if decision == "rejected" else ""}
    if e.get("arrangement"):
        a = _arrangement_of(db, tenant_id, e["arrangement"])
        if a.status not in ("approved", "countered"):
            raise ValueError(f"{a.arrangement_number} is {a.status} - only an approved or countered plan is accepted or rejected")
        a.customer_decision, a.decided_at = decision, utcnow()
        if decision == "accepted":
            a.status = "active"
            for alert in db.query(CmCreditAlert).filter(CmCreditAlert.party_id == a.party_id, CmCreditAlert.status.in_(("open", "pending"))).all():
                alert.action_type, alert.status = "payment_arrangement_agreed", "pending"
                _alert_history(alert, "payment_arrangement_agreed", a.arrangement_number)
        else:
            a.status = "rejected"
        db.flush()
        return {"party_id": a.party_id, "arrangement_id": a.id, "arrangement": arrangement_out(a),
                "next": "Discuss the options again and submit another request" if decision == "rejected" else ""}
    raise ValueError("Which adjustment or arrangement?")


# ------------------------------------------------------------------ credit alerts and collections


def credit_alert_out(db: Session, a: CmCreditAlert) -> dict[str, Any]:
    return {"id": a.id, "alert_number": a.alert_number, "party_id": a.party_id, "customer": cs.party_name(db, a.party_id),
            "invoice_id": a.invoice_id, "amount_owed": a.amount_owed, "days_overdue": a.days_overdue, "priority": a.priority, "status": a.status,
            "sub_status": a.sub_status, "action_type": a.action_type, "due_date": a.due_date, "source": a.source, "owner": a.owner_name,
            "comments": a.comments, "history": _j(a.history_json, []), "created_at": a.created_at, "closed_at": a.closed_at,
            "last_synced_at": a.last_synced_at}


def credit_alert_of(db: Session, tenant_id: int, ref: Any) -> CmCreditAlert:
    text = str(ref or "").strip()
    if text.upper().startswith("CA"):
        row = db.query(CmCreditAlert).filter(CmCreditAlert.tenant_id == tenant_id, CmCreditAlert.alert_number == text.upper()).first()
        if row is None:
            raise ValueError(f"Credit alert {text} not found")
        return row
    return cs._own(db, CmCreditAlert, text, tenant_id, "Credit alert")


def _alert_history(a: CmCreditAlert, action: str, note: str = "") -> None:
    rows = _j(a.history_json, [])
    rows.append({"at": utcnow().isoformat(timespec="seconds"), "action": action, "note": note})
    a.history_json = json.dumps(rows[-50:])


def overdue_amount(db: Session, tenant_id: int, party_id: int, grace: int = 0) -> float:
    today = _today()
    return _r(sum(due(i) for i in invoices_of(db, tenant_id, party_id) if i.status != "written_off" and due(i) > 0
                  and i.due_date + dt.timedelta(days=grace) < today))


def _set_profile_status(db: Session, tenant_id: int, party_id: int, entered: bool) -> None:
    for p in cs.billing_profiles(db, tenant_id, party_id):
        p.status, p.sub_status = ("yellow", "entered_collections") if entered else ("green", "exited_collections")


def run_collections(db: Session, tenant_id: int, party_id: int | None = None, as_of: Any = None) -> dict[str, Any]:
    today = cs._date(as_of) or _today()
    cfg = cs.settings(db, tenant_id)
    ladder = sorted((int(d), str(a)) for d, a in cfg["collections_ladder"])
    q = db.query(CmInvoice).filter(CmInvoice.tenant_id == tenant_id)
    if party_id:
        q = q.filter(CmInvoice.party_id == party_id)
    parties = sorted({i.party_id for i in q.all()})
    opened, escalated, closed, fees = [], [], [], []
    for pid in parties:
        invs = [i for i in invoices_of(db, tenant_id, pid)]
        for i in invs:
            refresh_status(i, today)
        late = [i for i in invs if i.status == "overdue" and i.due_date + dt.timedelta(days=int(cfg["grace_days"])) < today]
        alert = (db.query(CmCreditAlert).filter(CmCreditAlert.party_id == pid, CmCreditAlert.source == "collections",
                                                CmCreditAlert.status.in_(("open", "pending"))).first())
        if not late:
            if alert is not None and overdue_amount(db, tenant_id, pid) <= 0:
                _close_alert(db, tenant_id, alert, "Exited collections - nothing overdue")
                closed.append(alert.alert_number)
            continue
        oldest = min(i.due_date for i in late)
        days = (today - oldest).days
        owed = _r(sum(due(i) for i in late))
        # a late fee once per bill
        for i in late:
            if not i.late_fee_applied and (today - i.due_date).days >= int(cfg["late_fee_days"]):
                fee = _r(max(float(cfg["late_fee_min"]), due(i) * float(cfg["late_fee_pct"]) / 100))
                _post(db, tenant_id, pid, fee, f"Late payment fee on {i.invoice_number}")
                i.late_fee_applied = True
                fees.append({"invoice": i.invoice_number, "fee": fee})
        if alert is None:
            alert = CmCreditAlert(tenant_id=tenant_id, party_id=pid, source="collections", sub_status="entered_collections", status="open",
                                  invoice_id=late[-1].id, priority="medium")
            profile = cs.primary_profile(db, tenant_id, pid)
            alert.billing_profile_id = profile.id if profile else None
            db.add(alert)
            db.flush()
            alert.alert_number = f"CA-{alert.id:06d}"
            _alert_history(alert, "entered_collections", f"{owed:,.2f} overdue")
            _set_profile_status(db, tenant_id, pid, True)
            opened.append(alert.alert_number)
        alert.amount_owed, alert.days_overdue = owed, days
        alert.priority = "high" if days >= 45 or owed >= 5000 else "medium"
        # an agreed plan holds the ladder - unless it is broken
        plan = db.query(CmPaymentArrangement).filter(CmPaymentArrangement.party_id == pid, CmPaymentArrangement.status == "active").first()
        if plan is not None:
            missed = [r for r in _j(plan.schedule_json, []) if r["paid"] < r["amount"] - 0.005
                      and dt.date.fromisoformat(r["due"]) + dt.timedelta(days=int(cfg["grace_days"])) < today]
            if not missed:
                continue
            plan.status = "broken"
            _alert_history(alert, "arrangement_broken", plan.arrangement_number)
        step = None
        for d_, action in ladder:
            if days >= d_:
                step = action
        if step and step != alert.action_type and alert.action_type not in ("payment_arrangement_agreed",):
            alert.action_type = step
            alert.due_date = today + dt.timedelta(days=3)
            alert.status = "open"
            _alert_history(alert, step, f"{days} days overdue, {owed:,.2f}")
            escalated.append({"alert": alert.alert_number, "party_id": pid, "action": step, "days_overdue": days})
    db.flush()
    return {"as_of": today, "opened": opened, "escalated": escalated, "closed": closed, "late_fees": fees,
            "suspend": [x for x in escalated if x["action"] in ("suspend_services", "terminate_services")]}


def _close_alert(db: Session, tenant_id: int, alert: CmCreditAlert, note: str, status: str = "closed") -> None:
    alert.status, alert.closed_at = status, utcnow()
    if alert.source == "collections":
        alert.sub_status = "exited_collections"
        _set_profile_status(db, tenant_id, alert.party_id, False)
    alert.last_synced_at = utcnow()
    _alert_history(alert, status, note)


def _check_collections_exit(db: Session, tenant_id: int, party_id: int) -> list[str]:
    if overdue_amount(db, tenant_id, party_id) > 0:
        return []
    out = []
    for alert in db.query(CmCreditAlert).filter(CmCreditAlert.party_id == party_id, CmCreditAlert.source == "collections",
                                                CmCreditAlert.status.in_(("open", "pending"))).all():
        _close_alert(db, tenant_id, alert, "Paid up - exited collections")
        out.append(alert.alert_number)
    return out


def work_credit_alert(db: Session, tenant_id: int, e: dict[str, Any]) -> dict[str, Any]:
    """Raise an internal credit alert, or move one on: a collection action, a status, a note."""
    if e.get("credit_alert"):
        alert = credit_alert_of(db, tenant_id, e["credit_alert"])
    else:
        party = cs.party_of(db, tenant_id, e)
        alert = db.query(CmCreditAlert).filter(CmCreditAlert.party_id == party.id, CmCreditAlert.status.in_(("open", "pending"))).first()
        if alert is None:
            owed = _r(float(e["amount"])) if e.get("amount") not in (None, "") else max(overdue_amount(db, tenant_id, party.id), balance(db, tenant_id, party.id))
            alert = CmCreditAlert(tenant_id=tenant_id, party_id=party.id, source="internal", status="open", amount_owed=owed,
                                  priority=str(e.get("priority") or "medium"))
            profile = cs.primary_profile(db, tenant_id, party.id)
            alert.billing_profile_id = profile.id if profile else None
            db.add(alert)
            db.flush()
            alert.alert_number = f"CA-{alert.id:06d}"
            _alert_history(alert, "raised", str(e.get("comments") or "Raised by a CSR"))
    if alert.status in ("closed", "rejected", "cancelled") and not e.get("status"):
        raise ValueError(f"{alert.alert_number} is {alert.status}")
    out: dict[str, Any] = {}
    if e.get("action_type"):
        act = str(e["action_type"]).strip().lower().replace(" ", "_").replace("-", "_").replace("/", "_")
        act = {"late_fee": "no_action_impose_late_fee", "write_off": "no_action_write_off", "dunning_letter": "courtesy_dunning_letter",
               "reminder": "courtesy_email_sms", "phone_call": "courtesy_phone_call", "call": "courtesy_phone_call", "suspend": "suspend_services",
               "terminate": "terminate_services", "agency": "refer_to_outside_agency", "collection_agency": "refer_to_outside_agency"}.get(act, act)
        if act not in ACTION_TYPES:
            raise ValueError(f"Action: {', '.join(ACTION_TYPES)}")
        if act == "no_action_write_off":
            if e.get("_actor_role") != "admin":
                raise ValueError("Only a supervisor (admin) writes a balance off")
            owed = overdue_amount(db, tenant_id, alert.party_id) or alert.amount_owed
            if owed > 0:
                out["write_off"] = request_adjustment(db, tenant_id, {"party_id": alert.party_id, "amount": owed, "reason": "write_off",
                                                                      "comments": f"Written off from {alert.alert_number}", "_actor_role": "admin"})["adjustment"]
        if act == "no_action_impose_late_fee":
            fee = _r(max(float(cs.settings(db, tenant_id)["late_fee_min"]), alert.amount_owed * float(cs.settings(db, tenant_id)["late_fee_pct"]) / 100))
            _post(db, tenant_id, alert.party_id, fee, f"Late payment fee ({alert.alert_number})")
            out["late_fee"] = fee
        if act in ("suspend_services", "terminate_services"):
            out["services"] = [{"order_id": o.id, "service": _service_name(db, o), "status": o.status.value}
                               for o in cs.live_orders(db, tenant_id, alert.party_id) if o.status != OrderStatus.suspended]
        alert.action_type = act
        _alert_history(alert, act, str(e.get("comments") or ""))
    if e.get("owner"):
        alert.owner_name = str(e["owner"])[:200]
    if e.get("comments") and not e.get("action_type"):
        _alert_history(alert, "note", str(e["comments"]))
    if e.get("comments"):
        alert.comments = (alert.comments + "\n" if alert.comments else "") + str(e["comments"])[:500]
    if e.get("status"):
        st = str(e["status"]).lower()
        if st not in ("open", "pending", "closed", "rejected", "cancelled"):
            raise ValueError("Status: open, pending, closed, rejected or cancelled")
        if st == "closed" and alert.source == "collections" and overdue_amount(db, tenant_id, alert.party_id) > 0 \
                and alert.action_type not in ("payment_arrangement_agreed", "no_action_write_off"):
            raise ValueError(f"{cs.party_name(db, alert.party_id)} still has {overdue_amount(db, tenant_id, alert.party_id):,.2f} overdue - "
                             "take a payment, agree a payment arrangement or write it off before closing")
        if st in ("closed", "rejected", "cancelled"):
            _close_alert(db, tenant_id, alert, str(e.get("comments") or ""), st)
        else:
            alert.status = st
    alert.last_synced_at = utcnow()  # status passed to the billing / collections system
    db.flush()
    return {"party_id": alert.party_id, "credit_alert_id": alert.id, "alert": credit_alert_out(db, alert), **out}


# ------------------------------------------------------------------ fraud


def fraud_out(db: Session, a: CmFraudAlert, full: bool = False) -> dict[str, Any]:
    out = {"id": a.id, "alert_number": a.alert_number, "party_id": a.party_id, "customer": cs.party_name(db, a.party_id), "fraud_type": a.fraud_type,
           "fraud_event": a.fraud_event, "level": a.level, "threshold": a.threshold, "observed": a.observed, "threshold_overflow": a.threshold_overflow,
           "service": a.service, "status": a.status, "contact_attempts": a.contact_attempts, "owner": a.owner_name, "comments": a.comments,
           "created_at": a.created_at, "closed_at": a.closed_at}
    if full:
        ids = _j(a.usage_ids_json, [])
        out["events"] = [usage_out(u) for u in db.query(CmUsage).filter(CmUsage.id.in_(ids or [0])).order_by(CmUsage.at)]
    return out


def fraud_alert_of(db: Session, tenant_id: int, ref: Any) -> CmFraudAlert:
    text = str(ref or "").strip()
    if text.upper().startswith("FA"):
        row = db.query(CmFraudAlert).filter(CmFraudAlert.tenant_id == tenant_id, CmFraudAlert.alert_number == text.upper()).first()
        if row is None:
            raise ValueError(f"Fraud alert {text} not found")
        return row
    return cs._own(db, CmFraudAlert, text, tenant_id, "Fraud alert")


def _new_fraud(db: Session, tenant_id: int, party_id: int, **kw: Any) -> CmFraudAlert:
    a = CmFraudAlert(tenant_id=tenant_id, party_id=party_id, **kw)
    db.add(a)
    db.flush()
    a.alert_number = f"FA-{a.id:06d}"
    return a


def fraud_check(db: Session, tenant_id: int, party_id: int | None = None, day: Any = None) -> dict[str, Any]:
    """Scan a day's usage against each account's thresholds."""
    q = db.query(CmUsage).filter(CmUsage.tenant_id == tenant_id)
    if party_id:
        q = q.filter(CmUsage.party_id == party_id)
    on = cs._date(day)
    if on is None:
        latest = q.order_by(CmUsage.at.desc()).first()
        on = latest.at.date() if latest else _today()
    rows = q.filter(CmUsage.at >= dt.datetime.combine(on, dt.time.min), CmUsage.at <= dt.datetime.combine(on, dt.time.max)).all()
    by_party: dict[int, list[CmUsage]] = {}
    for u in rows:
        by_party.setdefault(u.party_id, []).append(u)
    raised = []
    for pid, events in by_party.items():
        t = cs.fraud_thresholds(db, tenant_id, pid)
        over = 1 + float(t.get("overflow_pct", 0)) / 100
        open_events = {(a.fraud_event, a.created_at.date()) for a in db.query(CmFraudAlert).filter(
            CmFraudAlert.party_id == pid, CmFraudAlert.status.in_(("open", "investigating")))}
        checks = [
            ("daily_value", float(t["daily_threshold"]), sum(u.amount for u in events), [u.id for u in events]),
            ("international", float(t["international_threshold"]), sum(u.amount for u in events if u.international),
             [u.id for u in events if u.international]),
            ("domestic", float(t["domestic_threshold"]), sum(u.amount for u in events if not u.international),
             [u.id for u in events if not u.international]),
            ("sensitive_numbers", float(t["sensitive_threshold"]), float(sum(1 for u in events if u.premium)), [u.id for u in events if u.premium]),
            ("call_count", float(t["max_calls"]), float(sum(1 for u in events if u.event_type == "voice")), []),
            ("call_duration", float(t["max_call_minutes"]), max((u.quantity for u in events if u.event_type == "voice"), default=0.0),
             [u.id for u in events if u.event_type == "voice" and u.quantity > float(t["max_call_minutes"])]),
        ]
        for event, limit, seen, ids in checks:
            if limit > 0 and seen > limit * over and (event, utcnow().date()) not in open_events:
                overflow = _r(seen - limit)
                level = "likely" if seen > limit * 2 else "possible"
                raised.append(_new_fraud(db, tenant_id, pid, fraud_type="exceeds_threshold", fraud_event=event, level=level, threshold=limit,
                                         observed=_r(seen), threshold_overflow=overflow, usage_ids_json=json.dumps(ids[:200]),
                                         service=events[0].service))
        if _bool(t.get("block_international")) and any(u.international for u in events) and ("blocked_international", utcnow().date()) not in open_events:
            ids = [u.id for u in events if u.international]
            raised.append(_new_fraud(db, tenant_id, pid, fraud_type="invalid_calls", fraud_event="blocked_international", level="likely",
                                     observed=float(len(ids)), usage_ids_json=json.dumps(ids)))
        # cloning: calls from two places within minutes of each other
        window = dt.timedelta(minutes=float(t["simultaneous_minutes"]))
        voice = sorted((u for u in events if u.event_type == "voice" and u.origin_location), key=lambda u: u.at)
        pair = next(((a, b) for a, b in zip(voice, voice[1:]) if b.at - a.at <= window and cs._key(a.origin_location) != cs._key(b.origin_location)
                     and a.service == b.service), None)
        if pair and ("simultaneous_calls", utcnow().date()) not in open_events:
            raised.append(_new_fraud(db, tenant_id, pid, fraud_type="invalid_calls", fraud_event="simultaneous_calls", level="certain",
                                     observed=2.0, usage_ids_json=json.dumps([pair[0].id, pair[1].id]), service=pair[0].service,
                                     comments=f"Calls from {pair[0].origin_location} and {pair[1].origin_location} "
                                              f"{int((pair[1].at - pair[0].at).total_seconds() // 60)} minutes apart - possible cloning"))
    db.flush()
    return {"day": on, "checked_accounts": len(by_party), "raised": [fraud_out(db, a) for a in raised]}


def recheck_fraud_alerts(db: Session, tenant_id: int, party_id: int) -> list[str]:
    """New thresholds can clear threshold alerts that are now within them."""
    t = cs.fraud_thresholds(db, tenant_id, party_id)
    key = {"daily_value": "daily_threshold", "international": "international_threshold", "domestic": "domestic_threshold",
           "sensitive_numbers": "sensitive_threshold", "call_count": "max_calls", "call_duration": "max_call_minutes"}
    out = []
    for a in db.query(CmFraudAlert).filter(CmFraudAlert.party_id == party_id, CmFraudAlert.fraud_type == "exceeds_threshold",
                                           CmFraudAlert.status.in_(("open", "investigating"))).all():
        limit = float(t.get(key.get(a.fraud_event, ""), 0) or 0)
        if limit and a.observed <= limit * (1 + float(t.get("overflow_pct", 0)) / 100):
            a.status, a.closed_at = "closed", utcnow()
            a.comments = (a.comments + "\n" if a.comments else "") + f"Cleared - threshold raised to {limit:g}"
            out.append(a.alert_number)
    return out


def raise_fraud(db: Session, tenant_id: int, e: dict[str, Any]) -> dict[str, Any]:
    if _bool(e.get("scan")) or not (e.get("fraud_type") or e.get("comments")):
        party = cs.party_of(db, tenant_id, e, required=False)
        out = fraud_check(db, tenant_id, party.id if party else None, e.get("day"))
        return {"party_id": party.id if party else None, **out}
    party = cs.party_of(db, tenant_id, e)
    ftype = str(e.get("fraud_type") or "stolen_handset").lower().replace(" ", "_")
    ftype = {"stolen": "stolen_handset", "stolen_phone": "stolen_handset", "lost_phone": "stolen_handset", "clone": "invalid_calls",
             "cloned": "invalid_calls", "cloning": "invalid_calls", "subscription_fraud": "subscription", "identity": "subscription"}.get(ftype, ftype)
    if ftype not in ("exceeds_threshold", "invalid_calls", "stolen_handset", "subscription", "returned_mail"):
        raise ValueError("Fraud type: stolen_handset, invalid_calls, subscription, returned_mail or exceeds_threshold")
    a = _new_fraud(db, tenant_id, party.id, fraud_type=ftype, fraud_event=str(e.get("fraud_event") or ftype), level=str(e.get("level") or "likely"),
                   service=str(e.get("service") or ""), comments=str(e.get("comments") or ""), owner_name=str(e.get("owner") or ""))
    db.flush()
    return {"party_id": party.id, "fraud_alert_id": a.id, "alert": fraud_out(db, a)}


def work_fraud(db: Session, tenant_id: int, e: dict[str, Any]) -> dict[str, Any]:
    a = fraud_alert_of(db, tenant_id, e.get("fraud_alert"))
    if a.status in ("closed", "false_positive") and not e.get("fraud_outcome"):
        raise ValueError(f"{a.alert_number} is {a.status}")
    outcome = str(e.get("fraud_outcome") or "").lower().replace(" ", "_")
    outcome = {"confirmed": "confirm", "fraud": "confirm", "not_fraud": "false_positive", "genuine": "false_positive", "ok": "false_positive",
               "no_contact": "no_answer", "unreachable": "no_answer", "disconnect_service": "disconnect", "cut_off": "disconnect",
               "close": "close"}.get(outcome, outcome)
    if outcome and outcome not in ("confirm", "false_positive", "no_answer", "disconnect", "investigate", "close"):
        raise ValueError("Outcome: confirm, false_positive, no_answer, investigate, disconnect or close")
    out: dict[str, Any] = {}
    detail = cs.account_detail(db, tenant_id, db.get(Party, a.party_id))
    if outcome in ("no_answer", "confirm", "false_positive"):
        a.contact_attempts += 1
    if outcome == "investigate":
        a.status = "investigating"
    elif outcome == "confirm":
        a.status = "confirmed"
        detail.good_standing, detail.block_reason = False, f"Fraud confirmed ({a.alert_number})"
        out["disconnect"] = True
    elif outcome == "false_positive":
        a.status, a.closed_at = "false_positive", utcnow()
        if detail.block_reason.startswith("Fraud"):
            detail.good_standing, detail.block_reason = True, ""
    elif outcome == "no_answer":
        a.status = "investigating"
        if a.contact_attempts >= int(cs.settings(db, tenant_id)["fraud_contact_attempts"]):
            out["disconnect"] = True
            out["why"] = f"{a.contact_attempts} attempts to reach the customer failed"
    elif outcome == "disconnect":
        a.status = "confirmed"
        out["disconnect"] = True
    elif outcome == "close":
        a.status, a.closed_at = "closed", utcnow()
    if out.get("disconnect"):
        out["services"] = [{"order_id": o.id, "service": _service_name(db, o), "status": o.status.value}
                           for o in cs.live_orders(db, tenant_id, a.party_id) if o.status != OrderStatus.suspended]
    for f, col in (("owner", "owner_name"),):
        if e.get(f):
            setattr(a, col, str(e[f])[:200])
    if e.get("comments"):
        a.comments = (a.comments + "\n" if a.comments else "") + str(e["comments"])[:500]
    db.flush()
    return {"party_id": a.party_id, "fraud_alert_id": a.id, "alert": fraud_out(db, a, full=True), **out}


# ------------------------------------------------------------------ the billing picture of an account


def summary(db: Session, tenant_id: int, party_id: int, all_records: bool = False) -> dict[str, Any]:
    invs = invoices_of(db, tenant_id, party_id)
    for i in invs:
        refresh_status(i)
    return {
        "balance": balance(db, tenant_id, party_id), "overdue": overdue_amount(db, tenant_id, party_id),
        "invoices": [invoice_out(db, i) for i in (invs if all_records else invs[:12])],
        "payments": [payment_out(db, p) for p in db.query(CmPayment).filter(CmPayment.tenant_id == tenant_id, CmPayment.party_id == party_id).order_by(CmPayment.id.desc()).limit(None if all_records else 12)],
        "adjustments": [adjustment_out(a) for a in db.query(CmAdjustmentRequest).filter(CmAdjustmentRequest.party_id == party_id)
                        .order_by(CmAdjustmentRequest.id.desc()).limit(12)],
        "arrangements": [arrangement_out(a) for a in db.query(CmPaymentArrangement).filter(CmPaymentArrangement.party_id == party_id)
                         .order_by(CmPaymentArrangement.id.desc()).limit(6)],
        "credit_alerts": [credit_alert_out(db, a) for a in db.query(CmCreditAlert).filter(CmCreditAlert.party_id == party_id)
                          .order_by(CmCreditAlert.id.desc()).limit(6)],
        "fraud_alerts": [fraud_out(db, a) for a in db.query(CmFraudAlert).filter(CmFraudAlert.party_id == party_id)
                         .order_by(CmFraudAlert.id.desc()).limit(6)],
        "balance_groups": balance_groups(db, tenant_id, party_id),
        "usage_history": usage_history(db, tenant_id, party_id),
    }


def portal(db: Session, tenant_id: int, party_id: int) -> dict[str, Any]:
    """The billing portal: summary plus what is not billed yet."""
    from app.models.orders import BillingAccount
    account = db.query(BillingAccount).filter_by(tenant_id=tenant_id, party_id=party_id).first()
    return {**summary(db, tenant_id, party_id, all_records=True), "billing_account": {"id": account.id, "party_id": party_id, "status": account.status} if account else None,
            "unbilled": unbilled(db, tenant_id, party_id),
            "billing_profiles": [cs.profile_out(p) for p in cs.billing_profiles(db, tenant_id, party_id)]}


def overview(db: Session, tenant_id: int, parties: set[int] | None = None) -> dict[str, Any]:
    """parties: only these customers (the caller's data scope); None = all."""

    def scoped(model):
        q = db.query(model).filter(model.tenant_id == tenant_id)
        return q if parties is None else q.filter(model.party_id.in_(parties or {-1}))

    invs = scoped(CmInvoice).all()
    for i in invs:
        refresh_status(i)
    return {
        "billed_this_month": _r(sum(i.total for i in invs if i.issue_date.replace(day=1) == _today().replace(day=1))),
        "open_receivables": _r(sum(due(i) for i in invs if i.status != "written_off")),
        "overdue": _r(sum(due(i) for i in invs if i.status == "overdue")),
        "invoices": len(invs),
        "credit_alerts_open": scoped(CmCreditAlert).filter(CmCreditAlert.status.in_(("open", "pending"))).count(),
        "fraud_alerts_open": scoped(CmFraudAlert).filter(CmFraudAlert.status.in_(("open", "investigating"))).count(),
        "adjustments_waiting": scoped(CmAdjustmentRequest).filter(CmAdjustmentRequest.status == "pending_approval").count(),
        "arrangements_waiting": scoped(CmPaymentArrangement).filter(CmPaymentArrangement.status == "pending").count(),
    }

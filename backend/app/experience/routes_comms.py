"""Communications API - accounts and their profiles, premises, assets,
billing (bills, payments, adjustments, arrangements), credit and collections,
fraud, ticket details, agreements and provider work orders
(crm_core/comm_service.py, comm_billing.py).

Business changes go through their intents (INT-72..102) like every other
record change; these routes read, let an agent set a ticket's group / contact /
premises, record equipment, feed usage and update a provider work order, and
let an admin set up the policy, providers, solutions and allowances.

Access: each route needs one of the pages that show its data (Admin -> Roles),
a direct change needs one of the intents that make it (Admin -> Intents), and
customer data is limited to what the caller's position can see.
"""

from __future__ import annotations

import datetime as dt
from typing import Any, Callable

from fastapi import APIRouter, Body, Depends, HTTPException
from sqlalchemy.orm import Session

from app.access import check_party, require_intent, require_page, visible_parties
from app.auth import require_roles
from app.crm_core import access_service, comm_billing as cb, comm_sample, comm_service as cs, fs_service
from app.crm_core.access_service import Access, visible_ids
from app.db import get_db
from app.models.assurance import TroubleTicket
from app.models.auth import UserRole
from app.models.comms import (
    CmAdjustmentRequest,
    CmAllowance,
    CmContact,
    CmCreditAlert,
    CmFraudAlert,
    CmInvoice,
    CmOpportunityProfile,
    CmPayment,
    CmPaymentArrangement,
    CmPremise,
    CmProvider,
    CmProviderWorkOrder,
    CmSiteItem,
    CmSolution,
    CmTicketDetail,
    CmUsage,
)
from app.models.crm import Party
from app.models.fulfillment import Asset
from app.models.order_mgmt import OmAgreement
from app.tenancy import get_current_tenant_id

router = APIRouter(prefix="/api/comms", tags=["communications"])
admin_router = APIRouter(prefix="/api/admin/comms", tags=["admin"], dependencies=[Depends(require_roles(UserRole.admin))])
_write = Depends(require_roles(UserRole.admin, UserRole.agent))
Payload = dict[str, Any]

# the pages that show each kind of data
_ACCOUNTS = require_page("customer", "orders")
_BILLING = require_page("orders", "customer")
_WORKBENCH = require_page("orders")
_TICKETS = require_page("tickets", "customer", "field")
_SALES = require_page("pipeline", "customer")


def _scoped(q, model, parties: set[int] | None):
    return q if parties is None else q.filter(model.party_id.in_(parties or {-1}))


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


def _party(db: Session, tenant_id: int, party_id: int) -> Party:
    p = db.get(Party, party_id)
    if p is None or p.tenant_id != tenant_id:
        raise HTTPException(404, "Customer not found")
    return p


# ------------------------------------------------------------------ overview


@router.get("/overview")
def overview(db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id), access: Access = Depends(_WORKBENCH)):
    parties = visible_parties(db, access)
    return _read(db, lambda: {**cb.overview(db, tenant_id, parties), "tickets": cs.ticket_analysis(db, tenant_id, parties=parties)})


# ------------------------------------------------------------------ accounts


@router.get("/accounts/{party_id}")
def account(party_id: int, db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id), access: Access = Depends(_ACCOUNTS)):
    party = _party(db, tenant_id, party_id)
    check_party(db, access, party_id)
    return _read(db, lambda: cs.account_view(db, tenant_id, party))


@router.get("/accounts/{party_id}/billing")
def billing_portal(party_id: int, db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id),
                   access: Access = Depends(_ACCOUNTS)):
    _party(db, tenant_id, party_id)
    check_party(db, access, party_id)
    return _read(db, lambda: cb.portal(db, tenant_id, party_id))


@router.get("/explorer")
def explorer(account_class: str = "", db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id),
             access: Access = Depends(_ACCOUNTS)):
    parties = visible_parties(db, access)
    return _read(db, lambda: [r for r in cs.explorer(db, tenant_id, account_class) if parties is None or r["party_id"] in parties])


@router.get("/contacts")
def contacts(party_id: int | None = None, q: str = "", db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id),
             access: Access = Depends(_ACCOUNTS)):
    if party_id:
        check_party(db, access, party_id)
        return cs.contacts_of(db, tenant_id, party_id)
    parties = visible_parties(db, access)
    rows = db.query(CmContact).filter(CmContact.tenant_id == tenant_id).order_by(CmContact.id.desc()).limit(300).all()
    if q:
        rows = [c for c in rows if cs._key(q) in cs._key(f"{c.first_name} {c.last_name} {c.email} {c.phone}")]
    out = [cs.contact_out(db, c) for c in rows]
    return [c for c in out if parties is None or any(a["party_id"] in parties for a in c["accounts"])]


def _site_item_scope(db: Session, tenant_id: int, access: Access, payload: Payload | None, item_id: int | None = None) -> None:
    if item_id is not None:
        check_party(db, access, cs._own(db, CmSiteItem, item_id, tenant_id, "Item").party_id)
    if payload and payload.get("party_id"):
        check_party(db, access, payload["party_id"])


_SITE_ITEMS = [_write, Depends(require_intent("INT-78", "INT-80"))]


@router.post("/site-items", dependencies=_SITE_ITEMS)
def add_site_item(payload: Payload = Body(...), db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id),
                  access: Access = Depends(_ACCOUNTS)):
    def run():
        _site_item_scope(db, tenant_id, access, payload)
        return cs.site_item_out(cs.save_site_item(db, tenant_id, payload))

    return _do(db, run)


@router.put("/site-items/{item_id}", dependencies=_SITE_ITEMS)
def edit_site_item(item_id: int, payload: Payload = Body(...), db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id),
                   access: Access = Depends(_ACCOUNTS)):
    def run():
        _site_item_scope(db, tenant_id, access, payload, item_id)
        return cs.site_item_out(cs.save_site_item(db, tenant_id, payload, item_id))

    return _do(db, run)


@router.delete("/site-items/{item_id}", dependencies=_SITE_ITEMS)
def delete_site_item(item_id: int, db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id),
                     access: Access = Depends(_ACCOUNTS)):
    def run():
        item = cs._own(db, CmSiteItem, item_id, tenant_id, "Item")
        check_party(db, access, item.party_id)
        db.delete(item)
        return {"deleted": item_id}

    return _do(db, run)


# ------------------------------------------------------------------ premises and assets


@router.get("/premises")
def premises(party_id: int | None = None, q: str = "", db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id),
             access: Access = Depends(_ACCOUNTS)):
    parties = visible_parties(db, access)
    return [r for r in cs.premises(db, tenant_id, party_id, q) if parties is None or r["party_id"] in parties]


@router.get("/premises/{premise_id}")
def premise(premise_id: int, db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id), access: Access = Depends(_ACCOUNTS)):
    def run():
        row = cs._own(db, CmPremise, premise_id, tenant_id, "Premises")
        check_party(db, access, row.party_id)
        return cs.premise_view(db, tenant_id, row)

    return _read(db, run)


@router.get("/assets/{asset_id}")
def asset(asset_id: int, db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id), access: Access = Depends(_ACCOUNTS)):
    def run():
        row = cs._own(db, Asset, asset_id, tenant_id, "Asset")
        check_party(db, access, fs_service.party_of_asset(db, row))
        return cs.asset_view(db, tenant_id, row)

    return _read(db, run)


# ------------------------------------------------------------------ billing


@router.get("/invoices")
def invoices(party_id: int | None = None, status: str = "", db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id),
             access: Access = Depends(_BILLING)):
    parties = visible_parties(db, access)

    def run():
        q = _scoped(db.query(CmInvoice).filter(CmInvoice.tenant_id == tenant_id), CmInvoice, parties)
        if party_id:
            q = q.filter(CmInvoice.party_id == party_id)
        rows = [cb.invoice_out(db, i) for i in q.order_by(CmInvoice.id.desc()).limit(300).all()]
        return [r for r in rows if not status or r["status"] == status]

    return _read(db, run)


@router.get("/invoices/{ref}")
def invoice(ref: str, db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id), access: Access = Depends(_BILLING)):
    def run():
        check_party(db, access, cb.invoice_of(db, tenant_id, ref).party_id)
        return cb.explain(db, tenant_id, {"invoice": ref})

    return _read(db, run)


@router.get("/payments")
def payments(party_id: int | None = None, db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id),
             access: Access = Depends(_BILLING)):
    q = _scoped(db.query(CmPayment).filter(CmPayment.tenant_id == tenant_id), CmPayment, visible_parties(db, access))
    if party_id:
        q = q.filter(CmPayment.party_id == party_id)
    return [cb.payment_out(db, p) for p in q.order_by(CmPayment.id.desc()).limit(300)]


@router.get("/adjustments")
def adjustments(status: str = "", db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id),
                access: Access = Depends(_BILLING)):
    q = _scoped(db.query(CmAdjustmentRequest).filter(CmAdjustmentRequest.tenant_id == tenant_id), CmAdjustmentRequest,
                visible_parties(db, access))
    if status:
        q = q.filter(CmAdjustmentRequest.status == status)
    return [{**cb.adjustment_out(a), "customer": cs.party_name(db, a.party_id),
             "invoice_number": db.get(CmInvoice, a.invoice_id).invoice_number if a.invoice_id and db.get(CmInvoice, a.invoice_id) else None}
            for a in q.order_by(CmAdjustmentRequest.id.desc()).limit(300)]


@router.get("/arrangements")
def arrangements(status: str = "", db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id),
                 access: Access = Depends(_BILLING)):
    q = _scoped(db.query(CmPaymentArrangement).filter(CmPaymentArrangement.tenant_id == tenant_id), CmPaymentArrangement,
                visible_parties(db, access))
    if status:
        q = q.filter(CmPaymentArrangement.status == status)
    return [{**cb.arrangement_out(a), "customer": cs.party_name(db, a.party_id)} for a in q.order_by(CmPaymentArrangement.id.desc()).limit(300)]


@router.get("/usage")
def usage(party_id: int, unbilled: bool = False, db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id),
          access: Access = Depends(_BILLING)):
    _party(db, tenant_id, party_id)
    check_party(db, access, party_id)
    q = db.query(CmUsage).filter(CmUsage.tenant_id == tenant_id, CmUsage.party_id == party_id)
    if unbilled:
        q = q.filter(CmUsage.invoice_id.is_(None))
    return [cb.usage_out(u) for u in q.order_by(CmUsage.at.desc()).limit(500)]


@router.post("/usage", dependencies=[_write, Depends(require_intent("INT-84", "INT-93"))])
def add_usage(payload: Payload = Body(...), db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id),
              access: Access = Depends(_BILLING)):
    """Rated usage from the network / mediation: {party_id, events: [{event_type, quantity, at, destination, ...}]}"""
    check_party(db, access, payload.get("party_id"))
    events = payload.get("events") or []
    if not isinstance(events, list) or not events:
        raise HTTPException(400, "Send events: [{event_type, quantity, at, ...}]")
    return _do(db, lambda: {"recorded": len(cb.record_usage(db, tenant_id, int(payload.get("party_id") or 0), events))})


# ------------------------------------------------------------------ credit and fraud


@router.get("/credit-alerts")
def credit_alerts(status: str = "", db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id),
                  access: Access = Depends(_BILLING)):
    q = _scoped(db.query(CmCreditAlert).filter(CmCreditAlert.tenant_id == tenant_id), CmCreditAlert, visible_parties(db, access))
    if status == "active":
        q = q.filter(CmCreditAlert.status.in_(("open", "pending")))
    elif status:
        q = q.filter(CmCreditAlert.status == status)
    return [cb.credit_alert_out(db, a) for a in q.order_by(CmCreditAlert.id.desc()).limit(300)]


@router.get("/fraud-alerts")
def fraud_alerts(status: str = "", db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id),
                 access: Access = Depends(_BILLING)):
    q = _scoped(db.query(CmFraudAlert).filter(CmFraudAlert.tenant_id == tenant_id), CmFraudAlert, visible_parties(db, access))
    if status == "active":
        q = q.filter(CmFraudAlert.status.in_(("open", "investigating", "confirmed")))
    elif status:
        q = q.filter(CmFraudAlert.status == status)
    return [cb.fraud_out(db, a) for a in q.order_by(CmFraudAlert.id.desc()).limit(300)]


@router.get("/fraud-alerts/{ref}")
def fraud_alert(ref: str, db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id), access: Access = Depends(_BILLING)):
    def run():
        alert = cb.fraud_alert_of(db, tenant_id, ref)
        check_party(db, access, alert.party_id)
        return cb.fraud_out(db, alert, full=True)

    return _read(db, run)


# ------------------------------------------------------------------ tickets


def _ticket_scope(db: Session, tenant_id: int, access: Access, ticket_id: int) -> TroubleTicket:
    ticket = cs._own(db, TroubleTicket, ticket_id, tenant_id, "Ticket")
    if ticket.party_id:
        check_party(db, access, ticket.party_id)
    return ticket


@router.get("/tickets")
def ticket_details(db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id), access: Access = Depends(_TICKETS)):
    """The communications side of every ticket, by ticket id (the Tickets page adds it to its cards)."""
    parties = visible_parties(db, access)
    hidden = set() if parties is None else {
        tid for (tid, pid) in db.query(TroubleTicket.id, TroubleTicket.party_id).filter(TroubleTicket.tenant_id == tenant_id)
        if pid and pid not in parties
    }
    out = {}
    for d in db.query(CmTicketDetail).filter(CmTicketDetail.tenant_id == tenant_id).all():
        if d.ticket_id in hidden:
            continue
        out[d.ticket_id] = {"number": d.ticket_number, "origin": d.origin, "parent_ticket_id": d.parent_ticket_id, "group": d.group_name,
                            "owner": d.owner_name, "sub_status": d.sub_status, "service_item": d.service_item, "order_id": d.order_id,
                            "error_code": d.error_code, "closed_at": d.closed_at,
                            "children": db.query(CmTicketDetail).filter(CmTicketDetail.parent_ticket_id == d.ticket_id).count()}
    return out


@router.get("/tickets/analysis")
def ticket_analysis(days: int = 30, db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id),
                    access: Access = Depends(_TICKETS)):
    return cs.ticket_analysis(db, tenant_id, max(1, min(days, 365)), parties=visible_parties(db, access))


@router.get("/tickets/{ticket_id}")
def ticket(ticket_id: int, db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id), access: Access = Depends(_TICKETS)):
    return _read(db, lambda: cs.ticket_view(db, tenant_id, _ticket_scope(db, tenant_id, access, ticket_id)))


@router.put("/tickets/{ticket_id}", dependencies=[_write, Depends(require_intent("INT-10", "INT-96"))])
def update_ticket(ticket_id: int, payload: Payload = Body(...), db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id),
                  access: Access = Depends(_TICKETS)):
    def run():
        _ticket_scope(db, tenant_id, access, ticket_id)
        return cs.update_ticket(db, tenant_id, ticket_id, payload)

    return _do(db, run)


@router.get("/solutions", dependencies=[Depends(_TICKETS)])
def solutions(q: str = "", db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)):
    if q:
        return cs.search_solutions(db, tenant_id, q, limit=20)
    return [_solution_out(s) for s in db.query(CmSolution).filter(CmSolution.tenant_id == tenant_id, CmSolution.published.is_(True))
            .order_by(CmSolution.uses.desc(), CmSolution.id).limit(100)]


# ------------------------------------------------------------------ sales, agreements, work orders


@router.get("/opportunities/{opportunity_id}")
def opportunity(opportunity_id: int, db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id),
                access: Access = Depends(_SALES)):
    def run():
        opp = cs._opportunity(db, tenant_id, opportunity_id)
        if not access_service.can_see(db, access, "opportunity", opp.id):
            raise HTTPException(404, "Opportunity not found")
        prof = db.query(CmOpportunityProfile).filter(CmOpportunityProfile.opportunity_id == opp.id).first()
        return {"links": cs.opportunity_links(db, tenant_id, opp.id),
                "profile": {"answers": cs._j(prof.answers_json, {}), "score": prof.score, "target_market": prof.target_market} if prof else None,
                "questions": cs.settings(db, tenant_id)["qualification_questions"]}

    return _read(db, run)


@router.get("/agreements/{ref}")
def agreement(ref: str, db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id), access: Access = Depends(_BILLING)):
    def run():
        row = cs.agreement_of(db, tenant_id, ref)
        check_party(db, access, row.party_id)
        return cs.agreement_view(db, tenant_id, row)

    return _read(db, run)


@router.get("/agreements")
def agreements(party_id: int | None = None, db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id),
               access: Access = Depends(_BILLING)):
    parties = visible_parties(db, access)

    def run():
        q = _scoped(db.query(OmAgreement).filter(OmAgreement.tenant_id == tenant_id), OmAgreement, parties)
        if party_id:
            q = q.filter(OmAgreement.party_id == party_id)
        out = []
        for a in q.order_by(OmAgreement.id.desc()).limit(200).all():
            d = cs.agreement_detail(db, tenant_id, a)
            out.append({"id": a.id, "agreement_number": a.agreement_number, "name": a.name, "customer": cs.party_name(db, a.party_id),
                        "status": a.status, "revision": d.revision, "parent_agreement_id": d.parent_agreement_id, "end_date": a.end_date,
                        "monthly_value": a.monthly_value, "document": bool(d.document), "signed": bool(d.signed_at)})
        return out

    return _read(db, run)


@router.get("/provider-work-orders")
def provider_work_orders(order_id: int | None = None, db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id),
                         access: Access = Depends(_BILLING)):
    orders = visible_ids(db, access, "order")
    return [w for w in cs.provider_work_orders(db, tenant_id, order_id) if orders is None or w["order_id"] in orders]


@router.put("/provider-work-orders/{wo_id}", dependencies=[_write, Depends(require_intent("INT-102"))])
def update_provider_work_order(wo_id: int, payload: Payload = Body(...), db: Session = Depends(get_db),
                               tenant_id: int = Depends(get_current_tenant_id), access: Access = Depends(_BILLING)):
    def run():
        wo = cs._own(db, CmProviderWorkOrder, wo_id, tenant_id, "Provider work order")
        if not access_service.can_see(db, access, "order", wo.order_id):
            raise HTTPException(404, "Provider work order not found")
        return cs.update_provider_wo(db, tenant_id, wo_id, payload)

    return _do(db, run)


# ------------------------------------------------------------------ admin setup


@admin_router.get("/settings")
def get_settings(db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)):
    return cs.settings(db, tenant_id)


@admin_router.put("/settings")
def put_settings(payload: Payload = Body(...), db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)):
    return _do(db, lambda: cs.save_settings(db, tenant_id, payload))


def _solution_out(s: CmSolution) -> dict[str, Any]:
    return {"id": s.id, "title": s.title, "problem": s.problem, "resolution": s.resolution, "product_line": s.product_line,
            "keywords": s.keywords, "uses": s.uses, "published": s.published}


def _provider_out(p: CmProvider) -> dict[str, Any]:
    return {"id": p.id, "name": p.name, "keywords": p.keywords, "lead_days": p.lead_days, "contact": p.contact, "active": p.active}


def _allowance_out(a: CmAllowance) -> dict[str, Any]:
    return {"id": a.id, "party_id": a.party_id, "customer": "", "name": a.name, "event_type": a.event_type, "granted": a.granted,
            "unit": a.unit, "valid_from": a.valid_from, "valid_to": a.valid_to, "shared": a.shared}


_SETUP: dict[str, tuple[type, Callable[[Any], dict[str, Any]], tuple[str, ...]]] = {
    "solutions": (CmSolution, _solution_out, ("title", "problem", "resolution", "product_line", "keywords", "published")),
    "providers": (CmProvider, _provider_out, ("name", "keywords", "lead_days", "contact", "active")),
    "allowances": (CmAllowance, _allowance_out, ("party_id", "name", "event_type", "granted", "unit", "valid_from", "valid_to", "shared")),
}


def _apply(db: Session, tenant_id: int, kind: str, row: Any, data: Payload) -> None:
    _, _, fields = _SETUP[kind]
    for f in fields:
        if f not in data:
            continue
        v = data[f]
        if f in ("published", "active", "shared"):
            setattr(row, f, bool(v))
        elif f in ("lead_days",):
            setattr(row, f, max(0, int(v or 0)))
        elif f == "granted":
            row.granted = max(0.0, float(v or 0))
        elif f == "party_id":
            row.party_id = _party(db, tenant_id, int(v or 0)).id
        elif f in ("valid_from", "valid_to"):
            setattr(row, f, dt.date.fromisoformat(str(v)[:10]) if v else None)
        else:
            setattr(row, f, str(v or "").strip())
    if kind == "allowances":
        if row.event_type not in ("voice", "sms", "data"):
            raise ValueError("An allowance is voice, sms or data")
        row.valid_from = row.valid_from or dt.date.today().replace(day=1)
        if not row.party_id:
            raise ValueError("Pick the customer")
    if not getattr(row, "name", None) and not getattr(row, "title", None):
        raise ValueError("Give it a name")


@admin_router.get("/{kind}")
def list_setup(kind: str, db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)):
    if kind not in _SETUP:
        raise HTTPException(404, "Unknown setup list")
    model, out, _ = _SETUP[kind]
    rows = [out(r) for r in db.query(model).filter(model.tenant_id == tenant_id).order_by(model.id).all()]
    if kind == "allowances":
        for r in rows:
            r["customer"] = cs.party_name(db, r["party_id"])
    return rows


@admin_router.post("/{kind}")
def create_setup(kind: str, payload: Payload = Body(...), db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)):
    if kind not in _SETUP:
        raise HTTPException(404, "Unknown setup list")
    model, out, _ = _SETUP[kind]

    def run():
        row = model(tenant_id=tenant_id)
        _apply(db, tenant_id, kind, row, payload)
        db.add(row)
        db.flush()
        return out(row)

    return _do(db, run)


@admin_router.put("/{kind}/{row_id}")
def update_setup(kind: str, row_id: int, payload: Payload = Body(...), db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)):
    if kind not in _SETUP:
        raise HTTPException(404, "Unknown setup list")
    model, out, _ = _SETUP[kind]
    row = db.get(model, row_id)
    if row is None or row.tenant_id != tenant_id:
        raise HTTPException(404, "Not found")
    return _do(db, lambda: (_apply(db, tenant_id, kind, row, payload), out(row))[1])


@admin_router.delete("/{kind}/{row_id}")
def delete_setup(kind: str, row_id: int, db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)):
    if kind not in _SETUP:
        raise HTTPException(404, "Unknown setup list")
    model, _, _ = _SETUP[kind]
    row = db.get(model, row_id)
    if row is None or row.tenant_id != tenant_id:
        raise HTTPException(404, "Not found")
    db.delete(row)
    db.commit()
    return {"deleted": row_id}


@admin_router.post("/setup/sample")
def sample(db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)):
    return _do(db, lambda: comm_sample.sample_setup(db, tenant_id))


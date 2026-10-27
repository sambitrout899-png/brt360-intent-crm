"""Customer 360 overview - one customer at a glance: who they are, where they
are in the journey (prospect -> lead -> opportunity -> quote -> order ->
live), the next best actions (each an intent, prefilled), their live
services and numbers, and a timeline of everything that happened.

Also the customer list with each customer's status, segment, MRR and
open issues, so the list itself says who needs attention.
"""

from __future__ import annotations

import datetime as dt
from collections import defaultdict
from typing import Any

from sqlalchemy.orm import Session

from app.crm_core import access_service, process_service
from app.crm_core.access_service import Access
from app.models.approval import ApprovalChainInstance
from app.models.assurance import TroubleTicket
from app.models.auth import User
from app.models.catalog import ProductOffering
from app.models.crm import ApprovalStatus, Lead, LeadStatus, Opportunity, OpportunityStage, Party, Quote, QuoteStatus
from app.models.identity import IdentityVerification
from app.models.lifecycle import EntityRecordMeta, EntityStateHistory, Position
from app.models.orders import BillingAccount, Order, OrderItem, OrderStatus
from app.models.process import PriceList
from app.timeutils import utcnow

LIVE = (OrderStatus.active, OrderStatus.changed)
OPEN_OPP = (OpportunityStage.draft, OpportunityStage.qualified, OpportunityStage.configured, OpportunityStage.in_feasibility,
            OpportunityStage.feasibility_failed, OpportunityStage.feasible, OpportunityStage.quoted)


def _status(party: Party) -> str:
    s = process_service.account_status(party)
    if s:
        return s
    return "prospect" if party.party_type == "prospect" else "account"


def customer_list(db: Session, tenant_id: int, access: Access, q: str | None = None) -> list[dict[str, Any]]:
    query = access_service.apply(db.query(Party).filter(Party.tenant_id == tenant_id), Party, db, access, "party")
    if q:
        query = query.filter(Party.name.ilike(f"%{q}%"))
    parties = query.order_by(Party.name).all()
    ids = [p.id for p in parties] or [-1]
    mrr: dict[int, float] = defaultdict(float)
    services: dict[int, int] = defaultdict(int)
    from app.crm_core import abo_service
    from app.models.fulfillment import AssetStatus
    for party_id in ids:
        for asset in abo_service.customer_assets(db, tenant_id, party_id):
            if asset.status == AssetStatus.active:
                mrr[party_id] += abo_service.recurring_price(asset)
                services[party_id] += 1
    for o in db.query(Order).filter(Order.party_id.in_(ids), Order.status.in_(LIVE)):
        mrr[o.party_id] += o.monthly_price
        services[o.party_id] += 1
    tickets: dict[int, int] = defaultdict(int)
    urgent: dict[int, int] = defaultdict(int)
    for t in db.query(TroubleTicket).filter(TroubleTicket.party_id.in_(ids), TroubleTicket.status != "resolved"):
        tickets[t.party_id] += 1
        urgent[t.party_id] += t.severity in ("critical", "high")
    open_deals: dict[int, int] = defaultdict(int)
    for lead in db.query(Lead).filter(Lead.party_id.in_(ids)):
        if lead.status == LeadStatus.pending_qualification or (lead.opportunity is not None and lead.opportunity.stage in OPEN_OPP):
            open_deals[lead.party_id] += 1
    return [{
        "id": p.id, "name": p.name, "brt_id": p.brt_id, "party_type": p.party_type, "status": _status(p),
        "segment": process_service.party_segment(p) or ("B2C" if p.party_type == "individual" else None),
        "mrr": round(mrr[p.id], 2), "services": services[p.id], "open_deals": open_deals[p.id], "open_tickets": tickets[p.id],
        "urgent_tickets": urgent[p.id], "since": p.created_at,
    } for p in parties]


def _b2c_progress(db: Session, tenant_id: int, orders: list[Order]) -> dict[str, Any]:
    """Where a B2C customer's direct orders are: the Order Module's
    feasibility, booking and fulfilment - and what to do about each order."""
    from app.models.feasibility import FeasibilityAssessment, FeasibilityOutcome
    from app.models.order_mgmt import OmOrderDetail

    out: dict[str, Any] = {"actions": []}
    feasible, booked, fulfilment = [], [], []
    for o in orders:
        d = db.query(OmOrderDetail).filter(OmOrderDetail.order_id == o.id).first()
        a = db.query(FeasibilityAssessment).filter(FeasibilityAssessment.tenant_id == tenant_id, FeasibilityAssessment.order_id == o.id) \
            .order_by(FeasibilityAssessment.id.desc()).first()
        if a is not None and a.outcome == FeasibilityOutcome.passed:
            feasible.append(a.created_at)
        if d is not None and d.submitted_at:
            booked.append(d.submitted_at)
        if o.status not in (OrderStatus.created, OrderStatus.pending_approval, OrderStatus.rejected, OrderStatus.cancelled):
            fulfilment.append(o.updated_at)
        if o.status == OrderStatus.created and d is not None and d.hold:
            out["actions"].append(("INT-60", "Release the held order", f"Order #{o.id} is on hold - {d.hold_reason}",
                                   {"order_id": o.id, "hold_action": "release"}, 90))
        elif o.status == OrderStatus.created and d is not None and d.om_status in ("booked", "awaiting_shipping"):
            out["actions"].append(("INT-25", "Start fulfilment", f"Order #{o.id} is booked - installation is next", {"order_id": o.id}, 88))
        elif o.status == OrderStatus.created and d is not None and d.om_status == "pending":
            out["actions"].append(("INT-59", "Submit the order", f"Order #{o.id} isn't submitted yet", {"order_id": o.id}, 80))
    out.update(feasible=min(feasible, default=None), booked=min(booked, default=None), fulfilment=min(fulfilment, default=None))
    return out


def overview(db: Session, tenant_id: int, access: Access, party: Party) -> dict[str, Any]:
    now = utcnow()
    leads = db.query(Lead).filter(Lead.party_id == party.id).order_by(Lead.id).all()
    opps = [lead.opportunity for lead in leads if lead.opportunity is not None]
    opp_ids = [o.id for o in opps] or [-1]
    quotes = db.query(Quote).filter(Quote.opportunity_id.in_(opp_ids)).order_by(Quote.id).all()
    orders = db.query(Order).filter(Order.party_id == party.id).order_by(Order.id).all()
    tickets = db.query(TroubleTicket).filter(TroubleTicket.party_id == party.id).order_by(TroubleTicket.id).all()
    verifications = db.query(IdentityVerification).filter(IdentityVerification.party_id == party.id).order_by(IdentityVerification.id).all()
    live = [o for o in orders if o.status in LIVE]
    from app.crm_core import abo_service
    from app.models.fulfillment import AssetStatus
    assets = abo_service.customer_assets(db, tenant_id, party.id)
    live_assets = [a for a in assets if a.status == AssetStatus.active]
    offering = {o.id: o.name for o in db.query(ProductOffering).filter(ProductOffering.tenant_id == tenant_id)}
    billing = db.query(BillingAccount).filter(BillingAccount.party_id == party.id).first()

    # ---------------- facts
    meta = db.query(EntityRecordMeta).filter(EntityRecordMeta.tenant_id == tenant_id, EntityRecordMeta.entity_type == "party",
                                             EntityRecordMeta.entity_id == party.id).first()
    owner = db.get(User, meta.owner_user_id) if meta and meta.owner_user_id else (db.get(User, party.sys_created_by) if party.sys_created_by else None)
    position = db.get(Position, meta.owner_position_id) if meta and meta.owner_position_id else None
    price_list = process_service.price_list_of(db, tenant_id, party)
    profile = party.profile
    kyc = verifications[-1].status.value if verifications else None
    facts = {
        "status": _status(party), "segment": process_service.party_segment(party) or ("B2C" if party.party_type == "individual" else None),
        "party_type": party.party_type, "industry": profile.industry if profile else None, "brt_id": party.brt_id, "since": party.created_at,
        "owner": (owner.name or owner.email) if owner else None, "owner_position": position.name if position else None,
        "price_list": price_list.name if isinstance(price_list, PriceList) else None, "kyc": kyc,
        "email": party.email, "phone": party.phone,
    }

    # ---------------- numbers
    open_quotes = [q for q in quotes if q.status in (QuoteStatus.draft, QuoteStatus.pending_approval, QuoteStatus.approved)
                   and not any(o.quote_id == q.id for o in orders)]
    numbers = {
        "mrr": round(sum(o.monthly_price for o in live) + sum(abo_service.recurring_price(a) for a in live_assets), 2), "services": len(live) + len(live_assets), "orders": len(orders),
        "pipeline": round(sum(q.total_price for q in open_quotes), 2), "open_deals": sum(1 for o in opps if o.stage in OPEN_OPP),
        "open_tickets": sum(1 for t in tickets if t.status != "resolved"), "balance": round(billing.balance, 2) if billing else None,
        "tenure_days": (now - party.created_at).days,
    }

    # ---------------- journey
    def first(items, pred=lambda _: True):
        hits = [i.created_at for i in items if pred(i)]
        return min(hits) if hits else None

    # A step once reached stays reached: revising an approved quote (it becomes
    # "superseded", the revision a new draft) doesn't undo the approval.
    quote_ids = [q.id for q in quotes] or [-1]
    approved_at = [h.created_at for h in db.query(EntityStateHistory).filter(
        EntityStateHistory.tenant_id == tenant_id, EntityStateHistory.entity_type == "quote",
        EntityStateHistory.entity_id.in_(quote_ids), EntityStateHistory.to_state == "approved")]
    approved_at += [i.decided_at or i.created_at for i in db.query(ApprovalChainInstance).filter(
        ApprovalChainInstance.entity_type == "quote", ApprovalChainInstance.entity_id.in_(quote_ids),
        ApprovalChainInstance.status == ApprovalStatus.approved)]
    approved_now = first(quotes, lambda q: q.status == QuoteStatus.approved or any(o.quote_id == q.id for o in orders))
    approved = min([t for t in [approved_now, *approved_at] if t is not None], default=None)
    from app.crm_core import b2c_order_service

    # B2C direct order (the vanilla B2C journey): no lead, opportunity or quote - prospect -> order -> live
    direct_b2c = b2c_order_service.is_b2c(party) and b2c_order_service.direct(db, tenant_id)
    b2c = _b2c_progress(db, tenant_id, orders) if direct_b2c else {}
    steps = [
        ("Prospect", party.created_at),
        ("Order placed", first(orders)),
        ("Feasible", b2c.get("feasible")),
        ("Booked", b2c.get("booked")),
        ("In fulfilment", b2c.get("fulfilment")),
        ("Live", min([o.updated_at for o in live] + [a.created_at for a in assets], default=None)),
    ] if direct_b2c else [
        ("Prospect", party.created_at),
        ("Lead", first(leads)),
        ("Qualified", first(leads, lambda lead: lead.status == LeadStatus.qualified) or (first(opps) if opps else None)),
        ("Opportunity", first(opps)),
        ("Quoted", first(quotes)),
        ("Approved", approved),
        ("Ordered", first(orders)),
        ("Live", min([o.updated_at for o in live] + [a.created_at for a in assets], default=None)),
    ]
    reached = [s for s in steps if s[1] is not None]
    journey = {"steps": [{"label": s, "at": at, "reached": at is not None} for s, at in steps],
               "current": reached[-1][0] if reached else "Prospect", "kind": "b2c_direct" if direct_b2c else "sales"}

    # ---------------- next best actions (each an intent, prefilled)
    actions: list[dict[str, Any]] = []

    def add(code: str, label: str, why: str, entities: dict[str, Any], weight: int) -> None:
        actions.append({"code": code, "label": label, "why": why, "entities": entities, "weight": weight})

    urgent = [t for t in tickets if t.status != "resolved" and t.severity in ("critical", "high")]
    if urgent:
        add("INT-11", "Diagnose the urgent ticket", f"Ticket #{urgent[0].id} is {urgent[0].severity}", {"ticket_id": urgent[0].id}, 100)
    pending_leads = [lead for lead in leads if lead.status == LeadStatus.pending_qualification]
    if direct_b2c:
        for note in b2c.get("actions", []):
            add(*note)
    if pending_leads and not direct_b2c:
        add("INT-17", "Qualify the lead", f"Lead #{pending_leads[-1].id} is waiting to be qualified", {"lead_id": pending_leads[-1].id}, 80)
    for o in opps:
        if o.stage in (OpportunityStage.draft, OpportunityStage.qualified) and facts["segment"] != "B2C" and not any(q.opportunity_id == o.id for q in quotes):
            add("INT-37", "Configure the product", f"{o.name} has no configuration yet", {"opportunity_id": o.id, "offering_name": o.offering_name}, 70)
        elif o.stage == OpportunityStage.configured:
            add("INT-38", "Send to Presales", f"{o.name} is configured - feasibility is next", {"opportunity_id": o.id}, 70)
        elif o.stage == OpportunityStage.feasible:
            add("INT-05", "Prepare the quote", f"{o.name} is feasible", {"opportunity_id": o.id, "offering_name": o.offering_name}, 75)
    for q in quotes:
        if any(o.quote_id == q.id for o in orders):
            continue
        if q.status == QuoteStatus.pending_approval:
            from app.crm_core import approval_service

            chain = approval_service.get_chain_instance(db, "quote", q.id)
            if chain is not None and approval_service.can_decide(db, chain, access.user.id)[0]:
                add("INT-36", "Decide the quote approval", f"Quote #{q.id} ({q.total_price:,.0f}) waits for your approval",
                    {"entity_type": "quote", "entity_id": q.id}, 85)
        elif q.status == QuoteStatus.approved and q.purchase_order is None and facts["segment"] != "B2C":
            from app.crm_core import quote_documents
            from app.models.fulfillment import Proposal

            if quote_documents.enabled(db, tenant_id, "proposal") and quote_documents.latest(db, q.id, "proposal") is None \
                    and db.query(Proposal.id).filter(Proposal.quote_id == q.id).first() is None:
                add("INT-109", "Generate the proposal", f"Quote #{q.id} is approved - its proposal is next", {"quote_id": q.id}, 76)
            add("INT-39", "Attach the customer's PO", f"Quote #{q.id} is approved - the PO is next", {"quote_id": q.id}, 75)
        elif q.status == QuoteStatus.approved and q.purchase_order is not None and q.purchase_order.matched_at is None:
            add("INT-40", "Match all & create the order", f"PO {q.purchase_order.po_number} is attached to quote #{q.id} - Sales Ops Head", {"quote_id": q.id}, 78)
        elif q.status == QuoteStatus.approved:
            add("INT-07", "Activate the order", f"Quote #{q.id} is approved", {"quote_id": q.id}, 74)
        elif q.status == QuoteStatus.draft:
            add("INT-06", "Submit the quote for approval", f"Quote #{q.id} is still a draft", {"quote_id": q.id}, 60)
    if direct_b2c and not orders:
        add("INT-104", "Place their order", "B2C goes straight to an order - no lead or quote", {"party_id": party.id}, 65)
    elif not leads and not orders:
        add("INT-46", "Generate a lead", "No lead yet for this customer", {"party_id": party.id}, 65)
    if kyc != "verified":
        add("INT-13", "Verify their identity", "Identity not verified yet" if not kyc else f"Last check: {kyc}", {"party_id": party.id}, 40)
    if live:
        add("INT-50", "Change a live service", f"{len(live)} live service(s) - upgrade or reconfigure", {"order_id": live[-1].id}, 30)
    if live_assets:
        add("INT-107", "Change an installed asset", "Create a separate asset-based change order", {"asset_id": live_assets[-1].id, "asset_operation": "modify"}, 31)
        if direct_b2c:
            add("INT-104", "Order more products", "Another order straight away - no lead for B2C", {"party_id": party.id}, 25)
        else:
            add("INT-46", "Grow the account", "Open a new lead for more products", {"party_id": party.id}, 25)
    add("INT-49", "Check what they can buy", "Products they qualify for", {"party_id": party.id}, 20)
    add("INT-10", "Log a complaint", "Open a ticket for this customer", {"company_name": party.name}, 10)
    seen_codes: set[str] = set()
    next_actions = []
    for a in sorted(actions, key=lambda a: -a["weight"]):
        if a["code"] in seen_codes:
            continue
        seen_codes.add(a["code"])
        next_actions.append({k: v for k, v in a.items() if k != "weight"})

    # ---------------- live services
    services = [{"order_id": o.id, "product": offering.get(o.items[0].product_offering_id, "-") if o.items else "-", "monthly": o.monthly_price,
                 "status": o.status.value, "since": o.created_at, "brt_id": o.brt_id} for o in live]

    # ---------------- timeline
    events: list[dict[str, Any]] = [{"at": party.created_at, "icon": "👤", "title": f"Became a {'prospect' if party.party_type == 'prospect' else 'customer'}",
                                     "detail": party.name, "entity_type": "party", "entity_id": party.id}]
    for lead in leads:
        events.append({"at": lead.created_at, "icon": "🌱", "title": f"Lead #{lead.id} opened", "detail": f"from {lead.source} · {lead.interested_offering_name or 'no product yet'}",
                       "entity_type": "lead", "entity_id": lead.id})
    for o in opps:
        events.append({"at": o.created_at, "icon": "💼", "title": "Opportunity opened", "detail": o.name, "entity_type": "opportunity", "entity_id": o.id})
    for q in quotes:
        events.append({"at": q.created_at, "icon": "🧾", "title": f"Quote #{q.id}{f' (revision {q.revision_number})' if q.revision_number > 1 else ''}",
                       "detail": f"{q.total_price:,.0f} / month · {q.status.value.replace('_', ' ')}", "entity_type": "quote", "entity_id": q.id})
    for o in orders:
        events.append({"at": o.created_at, "icon": "📦", "title": f"Order #{o.id}", "detail": f"{o.monthly_price:,.0f} / month · {o.status.value.replace('_', ' ')}",
                       "entity_type": "order", "entity_id": o.id})
    for t in tickets:
        events.append({"at": t.created_at, "icon": "🆘", "title": f"Ticket #{t.id} ({t.severity})", "detail": (t.description or "")[:90],
                       "entity_type": "ticket", "entity_id": t.id})
    for v in verifications:
        events.append({"at": v.created_at, "icon": "🪪", "title": "Identity check", "detail": v.status.value, "entity_type": "party", "entity_id": party.id})
    from app.models.order_mgmt import OmChannelInteraction

    for i in db.query(OmChannelInteraction).filter(OmChannelInteraction.tenant_id == tenant_id, OmChannelInteraction.party_id == party.id):
        events.append({"at": i.at, "icon": "📡", "title": f"{b2c_order_service.CHANNELS.get(i.channel, i.channel)}: {i.kind.replace('_', ' ')}",
                       "detail": i.note or i.reference, "entity_type": "order" if i.order_id else "party", "entity_id": i.order_id or party.id})
    tracked = {("party", party.id)} | {("lead", x.id) for x in leads} | {("opportunity", x.id) for x in opps} | {("quote", x.id) for x in quotes} | \
              {("order", x.id) for x in orders}
    for h in db.query(EntityStateHistory).filter(EntityStateHistory.tenant_id == tenant_id).order_by(EntityStateHistory.id.desc()).limit(2000):
        if (h.entity_type, h.entity_id) in tracked and h.from_state:
            events.append({"at": h.created_at, "icon": "➡️", "title": f"{h.entity_type.title()} #{h.entity_id}: {h.from_state.replace('_', ' ')} → {h.to_state.replace('_', ' ')}",
                           "detail": "", "entity_type": h.entity_type, "entity_id": h.entity_id})
    events.sort(key=lambda e: e["at"], reverse=True)

    services.extend({"order_id": a.order_item_id and db.get(OrderItem, a.order_item_id).order_id,
                     "product": a.asset_type, "monthly": abo_service.recurring_price(a), "status": a.status.value,
                     "since": a.created_at, "brt_id": a.brt_id, "asset_id": a.id} for a in live_assets)
    return {"party": {"id": party.id, "name": party.name}, "facts": facts, "numbers": numbers, "journey": journey,
            "next_actions": next_actions[:6], "services": services, "timeline": events[:40]}

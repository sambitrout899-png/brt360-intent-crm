"""CRM Core system-of-record browsing API (HLD Layer 3: Lead/Opportunity/Quote,
Order, Customer 360, Ticket). Deliberately read-only/navigational - per the
HLD's design principle ("the unit of design is the stated outcome, not the
screen or the form"), state changes happen through POST /api/intents, not
through PATCH/PUT forms on these records (Phase 7's in-place edits are
INT-29, gated by the record's state model - see routes_records.py). These endpoints exist so a user can
*find* the record (and its id) to state an intent against, and see the
system-of-record view a traditional CRM would call "Customer 360".
"""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.access import get_access
from app.auth import get_current_user
from app.crm_core import access_service, approval_service, quote_documents, quote_override_service
from app.crm_core.access_service import Access
from app.db import get_db
from app.models.accounts import ServiceAccount
from app.models.assurance import Technician, TroubleTicket, WorkOrder
from app.models.auth import User, UserRole
from app.models.catalog import ProductOffering
from app.models.crm import Lead, Opportunity, Party, Quote
from app.models.fulfillment import Asset, Proposal
from app.models.identity import IdentityVerification
from app.models.orders import BillingAccount, Order
from app.schemas.crm import (
    AssetOut,
    BillingAccountOut,
    BillingAdjustmentOut,
    DashboardSummaryOut,
    IdentityVerificationOut,
    LeadOut,
    OpportunityOut,
    OrderOut,
    Party360Out,
    PartyOut,
    ProposalOut,
    QuoteItemOut,
    QuoteOut,
    ResourceOut,
    RootCauseOut,
    ServiceAccountOut,
    ServiceOrderOut,
    TechnicianOut,
    TroubleTicketOut,
    WorkOrderOut,
)
from app.tenancy import get_current_tenant_id

def _frozen(db, order, offering):
    from app.crm_core import instance_service

    return instance_service.design_for(db, order.tenant_id, "order", order.id, offering.name)


router = APIRouter(prefix="/api", tags=["crm"])


def _require_visible(db: Session, access: Access, entity_type: str, entity_id: int, label: str) -> None:
    # Same 404 as a missing record - never confirm that a hidden one exists.
    if not access_service.can_see(db, access, entity_type, entity_id):
        raise HTTPException(404, f"{label} not found")


def _visible_party_filter(db: Session, access: Access, column):
    ids = access_service.visible_ids(db, access, "party")
    return None if ids is None else column.in_(ids or {-1})


# ---------- converters (handle the fields that need a join/lookup) ----------


def _approval_chain_dict(instance) -> dict | None:
    """Shared by quotes, leads and orders - every polymorphic
    ApprovalChainInstance (entity_type "quote"/"lead"/"order") renders
    the same way."""
    if instance is None:
        return None
    current_tier = (
        instance.steps[instance.current_sequence - 1].approver_tier
        if instance.status.value == "pending" and instance.current_sequence <= len(instance.steps)
        else None
    )
    return {
        "template_name": instance.template.name,
        "status": instance.status.value,
        "current_tier": current_tier,
        "steps": [
            {"sequence": s.sequence, "approver_tier": s.approver_tier, "status": s.status.value, "reasoning": s.reasoning or None}
            for s in instance.steps
        ],
    }


def _quote_configuration(db: Session, quote: Quote) -> dict | None:
    from app.crm_core import product_config_service as product_config
    from app.models.process import PriceList
    from app.models.product_config import QuoteItemConfig, QuoteTerms

    item = quote.items[0] if quote.items else None
    row = db.query(QuoteItemConfig).filter(QuoteItemConfig.quote_item_id == item.id).first() if item else None
    terms = db.query(QuoteTerms).filter(QuoteTerms.quote_id == quote.id).first()
    if row is None and terms is None:
        return None
    own = db.get(PriceList, terms.price_list_id) if terms and terms.price_list_id else None
    return {
        "pricing_attributes": product_config.loads(row.pricing_attributes_json) if row else {},
        "technical_attributes": product_config.loads(row.technical_attributes_json) if row else {},
        "price_breakdown": product_config.loads(row.price_breakdown_json) if row else {},
        "quote_price_list": own.name if own else None,
    }


def _order_configuration(db: Session, order: Order) -> list[dict]:
    from app.crm_core import product_config_service as product_config

    out = []
    for item in order.items:
        attrs = product_config.order_item_attributes(db, item.id)
        if attrs is None and item.unit_price is None and item.status != "completed":
            continue
        attrs = attrs or ({}, {})
        offering = db.get(ProductOffering, item.product_offering_id)
        out.append({"order_item_id": item.id, "offering_name": offering.name if offering else None,
                    "status": item.status,
                    "quantity": item.quantity, "unit_price": item.unit_price, "discount_pct": item.discount_pct,
                    "net_unit_price": item.net_unit_price,
                    "pricing_attributes": attrs[0], "technical_attributes": attrs[1],
                    "missing": product_config.missing_technical(db, order.tenant_id, offering.name, attrs[0], attrs[1],
                                                               _frozen(db, order, offering)) if offering else []})
    return out


def _quote_out(db: Session, quote: Quote, offering_names: dict[int, str]) -> QuoteOut:
    items = [
        QuoteItemOut(
            id=i.id,
            product_offering_id=i.product_offering_id,
            product_offering_name=offering_names.get(i.product_offering_id, f"#{i.product_offering_id}"),
            quantity=i.quantity,
            unit_price=i.unit_price,
        )
        for i in quote.items
    ]
    proposals = (
        db.query(Proposal).filter(Proposal.quote_id == quote.id).order_by(Proposal.sent_at.desc()).all()
    )
    return QuoteOut(
        id=quote.id,
        opportunity_id=quote.opportunity_id,
        status=quote.status.value,
        total_price=quote.total_price,
        requested_discount_pct=quote.requested_discount_pct,
        discount_justification=quote.discount_justification,
        created_at=quote.created_at,
        items=items,
        approval_chain=_approval_chain_dict(quote.chain_instance),
        feasible=quote.feasibility_result.feasible if quote.feasibility_result else None,
        proposals=[ProposalOut.model_validate(p) for p in proposals],
        revision_number=quote.revision_number,
        revision_of_quote_id=quote.revision_of_quote_id,
        purchase_order=(
            {"po_number": quote.purchase_order.po_number, "amount": quote.purchase_order.amount,
             "document_ref": quote.purchase_order.document_ref, "received_at": quote.purchase_order.received_at,
             "matched_at": quote.purchase_order.matched_at, "match_notes": quote.purchase_order.match_notes}
            if quote.purchase_order is not None else None
        ),
        configuration=_quote_configuration(db, quote),
        documents=quote_documents.listing(db, quote.id),
        order_id=db.query(Order.id).filter(Order.quote_id == quote.id).order_by(Order.id).limit(1).scalar(),
        price_override=quote_override_service.describe(db, quote.id),
    )


def _lead_out(lead: Lead) -> LeadOut:
    return LeadOut(
        id=lead.id,
        party_id=lead.party_id,
        source=lead.source,
        region=lead.region,
        owner_name=lead.owner_name,
        propensity_score=lead.propensity_score,
        status=lead.status.value,
        interested_offering_name=lead.interested_offering_name,
        created_at=lead.created_at,
        approval_chain=_approval_chain_dict(lead.chain_instance),
    )


def _order_out(db: Session, order: Order, party_name: str, service_account_name: str) -> OrderOut:
    service_orders = [
        ServiceOrderOut(
            id=so.id,
            site_label=so.site_label,
            status=so.status.value,
            resources=[ResourceOut(id=r.id, resource_type=r.resource_type, identifier=r.identifier, status=r.status.value) for r in so.resources],
        )
        for so in order.service_orders
    ]
    fulfillment_work_orders = (
        db.query(WorkOrder)
        .filter(WorkOrder.order_id == order.id, WorkOrder.kind != "field_dispatch")
        .order_by(WorkOrder.created_at)
        .all()
    )
    return OrderOut(
        id=order.id,
        party_id=order.party_id,
        party_name=party_name,
        service_account_id=order.service_account_id,
        service_account_name=service_account_name,
        quote_id=order.quote_id,
        order_type=order.order_type.value,
        status=order.status.value,
        monthly_price=order.monthly_price,
        contract_months=order.contract_months,
        created_at=order.created_at,
        service_orders=service_orders,
        order_control_chain=_approval_chain_dict(order.chain_instance),
        fulfillment_work_orders=[WorkOrderOut.model_validate(w) for w in fulfillment_work_orders],
        milestones=[{"code": m.code, "label": m.label, "detail": m.detail, "reached_at": m.reached_at} for m in order.milestones],
        segment=order.profile.segment if order.profile is not None else None,
        discount_approval=_approval_chain_dict(approval_service.get_chain_instance(db, "order", order.id, "discount")),
        configuration=_order_configuration(db, order),
    )


def _billing_account_out(account: BillingAccount, party_name: str) -> BillingAccountOut:
    return BillingAccountOut(
        id=account.id,
        party_id=account.party_id,
        party_name=party_name,
        status=account.status,
        rate_plan=account.rate_plan,
        balance=account.balance,
        adjustments=[
            BillingAdjustmentOut(id=a.id, order_id=a.order_id, amount=a.amount, reason=a.reason, created_at=a.created_at)
            for a in account.adjustments
        ],
    )


def _ticket_out(ticket: TroubleTicket) -> TroubleTicketOut:
    root_cause = None
    if ticket.root_cause_analysis:
        rca = ticket.root_cause_analysis
        root_cause = RootCauseOut(
            probable_cause=rca.probable_cause,
            confidence=rca.confidence,
            remediation_suggestion=rca.remediation_suggestion,
            field_dispatch_required=rca.field_dispatch_required,
            escalated_to_human=rca.escalated_to_human,
        )
    return TroubleTicketOut(
        id=ticket.id,
        party_id=ticket.party_id,
        category=ticket.category,
        severity=ticket.severity,
        product_line=ticket.product_line,
        sla_class=ticket.sla_class,
        priority=ticket.priority,
        status=ticket.status,
        description=ticket.description,
        created_at=ticket.created_at,
        root_cause=root_cause,
    )


def _work_order_out(wo: WorkOrder) -> WorkOrderOut:
    evidence = None
    if wo.completion_evidence:
        evidence = {
            "checklist": wo.completion_evidence.checklist,
            "signature": wo.completion_evidence.signature,
            "completed_at": wo.completion_evidence.completed_at,
        }
    return WorkOrderOut(
        id=wo.id,
        ticket_id=wo.ticket_id,
        order_id=wo.order_id,
        scheduled_window=wo.scheduled_window,
        status=wo.status,
        technician_name=wo.assignment.technician.name if wo.assignment else None,
        completion_evidence=evidence,
    )


def _offering_names(db: Session, tenant_id: int) -> dict[int, str]:
    return dict(db.query(ProductOffering.id, ProductOffering.name).filter(ProductOffering.tenant_id == tenant_id).all())


# ---------- Parties / Customer 360 ----------


@router.get("/parties", response_model=list[PartyOut])
def list_parties(
    q: str | None = None, db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id), access: Access = Depends(get_access)
) -> list[Party]:
    query = access_service.apply(db.query(Party).filter(Party.tenant_id == tenant_id), Party, db, access, "party")
    if q:
        query = query.filter(Party.name.ilike(f"%{q}%"))
    return query.order_by(Party.name).all()


@router.get("/customers")
def customers(q: str | None = None, db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id),
              access: Access = Depends(get_access)) -> list[dict]:
    """The customers the caller can see, each with status, segment, MRR, open deals and open issues."""
    from app.crm_core import customer_overview

    return customer_overview.customer_list(db, tenant_id, access, q)


@router.get("/customers/{party_id}/overview")
def customer_overview_view(party_id: int, db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id),
                           access: Access = Depends(get_access)) -> dict:
    """One customer at a glance: facts, numbers, journey, next best actions, live services, timeline."""
    from app.crm_core import customer_overview

    party = db.get(Party, party_id)
    if party is None or party.tenant_id != tenant_id:
        raise HTTPException(404, "Customer not found")
    _require_visible(db, access, "party", party_id, "Customer")
    return customer_overview.overview(db, tenant_id, access, party)


@router.get("/parties/{party_id}/360", response_model=Party360Out)
def party_360(party_id: int, db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id), access: Access = Depends(get_access)) -> Party360Out:
    party = db.get(Party, party_id)
    if party is None or party.tenant_id != tenant_id:
        raise HTTPException(404, "Party not found")
    _require_visible(db, access, "party", party_id, "Party")
    offering_names = _offering_names(db, tenant_id)

    # Within a visible customer, each lead/opportunity/quote/order is still
    # filtered on its own - a customer shared by two reps shows each rep
    # only their own deals.
    leads = access_service.apply(db.query(Lead).filter(Lead.tenant_id == tenant_id, Lead.party_id == party_id), Lead, db, access, "lead").all()
    all_lead_ids = [lid for (lid,) in db.query(Lead.id).filter(Lead.tenant_id == tenant_id, Lead.party_id == party_id).all()]
    opportunities = (
        access_service.apply(
            db.query(Opportunity).filter(Opportunity.tenant_id == tenant_id, Opportunity.lead_id.in_(all_lead_ids)), Opportunity, db, access, "opportunity"
        ).all()
        if all_lead_ids
        else []
    )
    all_opp_ids = [oid for (oid,) in db.query(Opportunity.id).filter(Opportunity.lead_id.in_(all_lead_ids)).all()] if all_lead_ids else []
    quotes = (
        access_service.apply(db.query(Quote).filter(Quote.tenant_id == tenant_id, Quote.opportunity_id.in_(all_opp_ids)), Quote, db, access, "quote").all()
        if all_opp_ids
        else []
    )
    orders = access_service.apply(db.query(Order).filter(Order.tenant_id == tenant_id, Order.party_id == party_id), Order, db, access, "order").all()
    billing_account = db.query(BillingAccount).filter(BillingAccount.tenant_id == tenant_id, BillingAccount.party_id == party_id).first()
    tickets = db.query(TroubleTicket).filter(TroubleTicket.tenant_id == tenant_id, TroubleTicket.party_id == party_id).all()
    identity_verifications = (
        db.query(IdentityVerification)
        .filter(IdentityVerification.tenant_id == tenant_id, IdentityVerification.party_id == party_id)
        .order_by(IdentityVerification.created_at.desc())
        .all()
    )

    service_accounts = (
        db.query(ServiceAccount).filter(ServiceAccount.tenant_id == tenant_id,
            or_(ServiceAccount.party_id == party_id,
                (ServiceAccount.party_id.is_(None) & (ServiceAccount.billing_account_id == (billing_account.id if billing_account else -1))))).all()
    )
    service_account_names = {sa.id: sa.name for sa in service_accounts}
    orders_by_service_account: dict[int, list[Order]] = {}
    for o in orders:
        orders_by_service_account.setdefault(o.service_account_id, []).append(o)

    return Party360Out(
        party=PartyOut.model_validate(party),
        leads=[_lead_out(l) for l in leads],
        opportunities=[_opportunity_out(o) for o in opportunities],
        quotes=[_quote_out(db, q, offering_names) for q in quotes],
        orders=[_order_out(db, o, party.name, service_account_names.get(o.service_account_id, "?")) for o in orders],
        service_accounts=[
            ServiceAccountOut(
                id=sa.id,
                party_id=sa.party_id or party_id,
                billing_account_id=sa.billing_account_id,
                name=sa.name,
                status=sa.status,
                created_at=sa.created_at,
                orders=[_order_out(db, o, party.name, sa.name) for o in orders_by_service_account.get(sa.id, [])],
                assets=[
                    AssetOut.model_validate(a)
                    for a in db.query(Asset).filter(Asset.tenant_id == tenant_id, Asset.service_account_id == sa.id, Asset.superseded_by_asset_id.is_(None)).order_by(Asset.created_at).all()
                ],
            )
            for sa in service_accounts
        ],
        identity_verifications=[IdentityVerificationOut.model_validate(v) for v in identity_verifications],
        billing_account=_billing_account_out(billing_account, party.name) if billing_account else None,
        tickets=[_ticket_out(t) for t in tickets],
    )


# ---------- Sales Pipeline (Leads / Opportunities / Quotes) ----------


@router.get("/leads", response_model=list[LeadOut])
def list_leads(db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id), access: Access = Depends(get_access)) -> list[LeadOut]:
    leads = access_service.apply(db.query(Lead).filter(Lead.tenant_id == tenant_id), Lead, db, access, "lead").order_by(Lead.created_at.desc()).all()
    return [_lead_out(lead) for lead in leads]


@router.get("/leads/{lead_id}", response_model=LeadOut)
def lead_detail(lead_id: int, db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id), access: Access = Depends(get_access)) -> LeadOut:
    lead = db.get(Lead, lead_id)
    if lead is None or lead.tenant_id != tenant_id:
        raise HTTPException(404, "Lead not found")
    _require_visible(db, access, "lead", lead_id, "Lead")
    return _lead_out(lead)


def _opportunity_out(opportunity: Opportunity) -> OpportunityOut:
    return OpportunityOut(
        id=opportunity.id,
        lead_id=opportunity.lead_id,
        name=opportunity.name,
        stage=opportunity.stage.value,
        offering_name=opportunity.offering_name,
        quantity=opportunity.quantity or 1,
        created_at=opportunity.created_at,
        updated_at=opportunity.updated_at,
        feasibility_assessments=[
            {
                "id": a.id,
                "outcome": a.outcome.value,
                "created_at": a.created_at,
                "resolved_at": a.resolved_at,
                "results": [
                    {"check_type_name": r.check_type_name, "outcome": r.outcome.value, "detail": r.detail}
                    for r in a.results
                ],
            }
            for a in sorted(opportunity.feasibility_assessments, key=lambda a: a.created_at, reverse=True)
        ],
        config=(
            {"pricing_attributes": json.loads(opportunity.config.pricing_attributes_json or "{}"),
             "site_address": opportunity.config.site_address, "technical_parameters": opportunity.config.technical_parameters,
             "feasibility_notes": opportunity.config.feasibility_notes,
             "account_manager_user_id": opportunity.config.account_manager_user_id,
             "technical_attributes": json.loads(opportunity.config.technical_attributes_json or "{}"),
             "price_list_id": opportunity.config.price_list_id}
            if opportunity.config is not None else None
        ),
    )


@router.get("/opportunities", response_model=list[OpportunityOut])
def list_opportunities(
    stage: str | None = None, db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id), access: Access = Depends(get_access)
):
    query = access_service.apply(db.query(Opportunity).filter(Opportunity.tenant_id == tenant_id), Opportunity, db, access, "opportunity")
    if stage:
        query = query.filter(Opportunity.stage == stage)
    opportunities = query.order_by(Opportunity.updated_at.desc()).all()
    return [_opportunity_out(o) for o in opportunities]


@router.get("/opportunities/{opportunity_id}", response_model=OpportunityOut)
def opportunity_detail(
    opportunity_id: int, db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id), access: Access = Depends(get_access)
) -> OpportunityOut:
    opportunity = db.get(Opportunity, opportunity_id)
    if opportunity is None or opportunity.tenant_id != tenant_id:
        raise HTTPException(404, "Opportunity not found")
    _require_visible(db, access, "opportunity", opportunity_id, "Opportunity")
    return _opportunity_out(opportunity)


@router.get("/quotes", response_model=list[QuoteOut])
def list_quotes(status: str | None = None, db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id), access: Access = Depends(get_access)):
    offering_names = _offering_names(db, tenant_id)
    query = access_service.apply(db.query(Quote).filter(Quote.tenant_id == tenant_id), Quote, db, access, "quote")
    if status:
        query = query.filter(Quote.status == status)
    return [_quote_out(db, q, offering_names) for q in query.order_by(Quote.created_at.desc()).all()]


@router.get("/quotes/{quote_id}", response_model=QuoteOut)
def quote_detail(quote_id: int, db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id), access: Access = Depends(get_access)) -> QuoteOut:
    offering_names = _offering_names(db, tenant_id)
    quote = db.get(Quote, quote_id)
    if quote is None or quote.tenant_id != tenant_id:
        raise HTTPException(404, "Quote not found")
    _require_visible(db, access, "quote", quote_id, "Quote")
    return _quote_out(db, quote, offering_names)


# ---------- The PDFs of a quote (proposal, PO, agreement, others) ----------


class QuoteDocumentIn(BaseModel):
    kind: str = "other"
    file_name: str = Field(min_length=1, max_length=200)
    content_base64: str = Field(min_length=1)
    title: str = Field(default="", max_length=200)
    note: str = Field(default="", max_length=300)


def _visible_quote(db: Session, access: Access, tenant_id: int, quote_id: int) -> Quote:
    quote = db.get(Quote, quote_id)
    if quote is None or quote.tenant_id != tenant_id:
        raise HTTPException(404, "Quote not found")
    _require_visible(db, access, "quote", quote_id, "Quote")
    return quote


@router.get("/quotes/{quote_id}/documents")
def quote_documents_list(quote_id: int, db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id),
                         access: Access = Depends(get_access)) -> list[dict]:
    _visible_quote(db, access, tenant_id, quote_id)
    return quote_documents.listing(db, quote_id)


@router.get("/quotes/{quote_id}/documents/{document_id}/file")
def quote_document_file(quote_id: int, document_id: int, download: bool = False, db: Session = Depends(get_db),
                        tenant_id: int = Depends(get_current_tenant_id), access: Access = Depends(get_access)) -> Response:
    _visible_quote(db, access, tenant_id, quote_id)
    doc = db.get(quote_documents.QuoteDocument, document_id)
    if doc is None or doc.tenant_id != tenant_id or doc.quote_id != quote_id:
        raise HTTPException(404, "Document not found")
    disposition = "attachment" if download else "inline"
    return Response(doc.content, media_type=doc.content_type or "application/pdf",
                    headers={"Content-Disposition": f'{disposition}; filename="{doc.file_name}"', "Cache-Control": "no-store"})


@router.get("/quotes/{quote_id}/documents/preview/{kind}")
def quote_document_preview(quote_id: int, kind: str, db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id),
                           access: Access = Depends(get_access)) -> Response:
    """The proposal / agreement as it would be generated for this quote now - the
    tenant's template filled in with the quote. Nothing is saved."""
    quote = _visible_quote(db, access, tenant_id, quote_id)
    if kind not in quote_documents.DEFAULT_TEMPLATES:
        raise HTTPException(404, "Unknown document")
    content, _ = quote_documents.render(db, tenant_id, quote, kind)
    return Response(content, media_type="application/pdf",
                    headers={"Content-Disposition": f'inline; filename="{kind}-preview-Q{quote_id}.pdf"', "Cache-Control": "no-store"})


@router.post("/quotes/{quote_id}/documents")
def quote_document_upload(quote_id: int, payload: QuoteDocumentIn, db: Session = Depends(get_db),
                          tenant_id: int = Depends(get_current_tenant_id), access: Access = Depends(get_access),
                          user: User = Depends(get_current_user)) -> dict:
    if user.role == UserRole.viewer:
        raise HTTPException(403, "Viewers can't attach documents")
    quote = _visible_quote(db, access, tenant_id, quote_id)
    try:
        doc = quote_documents.upload(db, tenant_id, quote, payload.kind, payload.file_name, payload.content_base64, user.id,
                                     payload.title, payload.note)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    db.commit()
    return quote_documents.out(db, doc)


# ---------- Orders / Billing ----------


@router.get("/orders", response_model=list[OrderOut])
def list_orders(status: str | None = None, db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id), access: Access = Depends(get_access)):
    query = access_service.apply(db.query(Order).filter(Order.tenant_id == tenant_id), Order, db, access, "order")
    if status:
        query = query.filter(Order.status == status)
    orders = query.order_by(Order.created_at.desc()).all()
    party_names = {p.id: p.name for p in db.query(Party.id, Party.name).filter(Party.tenant_id == tenant_id).all()}
    service_account_names = {
        sa.id: sa.name for sa in db.query(ServiceAccount.id, ServiceAccount.name).filter(ServiceAccount.tenant_id == tenant_id).all()
    }
    return [_order_out(db, o, party_names.get(o.party_id, "?"), service_account_names.get(o.service_account_id, "?")) for o in orders]


@router.get("/orders/{order_id}", response_model=OrderOut)
def order_detail(order_id: int, db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id), access: Access = Depends(get_access)) -> OrderOut:
    order = db.get(Order, order_id)
    if order is None or order.tenant_id != tenant_id:
        raise HTTPException(404, "Order not found")
    _require_visible(db, access, "order", order_id, "Order")
    party = db.get(Party, order.party_id)
    service_account = db.get(ServiceAccount, order.service_account_id)
    return _order_out(db, order, party.name if party else "?", service_account.name if service_account else "?")


@router.get("/billing-accounts", response_model=list[BillingAccountOut])
def list_billing_accounts(db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id), access: Access = Depends(get_access)):
    query = db.query(BillingAccount).filter(BillingAccount.tenant_id == tenant_id)
    party_filter = _visible_party_filter(db, access, BillingAccount.party_id)
    accounts = (query if party_filter is None else query.filter(party_filter)).all()
    party_names = {p.id: p.name for p in db.query(Party.id, Party.name).filter(Party.tenant_id == tenant_id).all()}
    return [_billing_account_out(a, party_names.get(a.party_id, "?")) for a in accounts]


# ---------- Trouble Tickets / Field Service ----------


@router.get("/tickets", response_model=list[TroubleTicketOut])
def list_tickets(
    status: str | None = None,
    severity: str | None = None,
    db: Session = Depends(get_db),
    tenant_id: int = Depends(get_current_tenant_id), access: Access = Depends(get_access),
):
    query = db.query(TroubleTicket).filter(TroubleTicket.tenant_id == tenant_id)
    party_filter = _visible_party_filter(db, access, TroubleTicket.party_id)
    if party_filter is not None:
        query = query.filter(or_(TroubleTicket.party_id.is_(None), party_filter))
    if status:
        query = query.filter(TroubleTicket.status == status)
    if severity:
        query = query.filter(TroubleTicket.severity == severity)
    return [_ticket_out(t) for t in query.order_by(TroubleTicket.created_at.desc()).all()]


@router.get("/tickets/{ticket_id}", response_model=TroubleTicketOut)
def ticket_detail(
    ticket_id: int, db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)
) -> TroubleTicketOut:
    ticket = db.get(TroubleTicket, ticket_id)
    if ticket is None or ticket.tenant_id != tenant_id:
        raise HTTPException(404, "Ticket not found")
    return _ticket_out(ticket)


@router.get("/work-orders", response_model=list[WorkOrderOut])
def list_work_orders(
    status: str | None = None, db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)
):
    query = db.query(WorkOrder).filter(WorkOrder.tenant_id == tenant_id)
    if status:
        query = query.filter(WorkOrder.status == status)
    return [_work_order_out(w) for w in query.order_by(WorkOrder.created_at.desc()).all()]


@router.get("/technicians", response_model=list[TechnicianOut])
def list_technicians(db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)) -> list[Technician]:
    return db.query(Technician).filter(Technician.tenant_id == tenant_id).all()


# ---------- Dashboard ----------


@router.get("/dashboard/insights")
def dashboard_insights(days: int = 30, db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id),
                       access: Access = Depends(get_access)) -> dict:
    """KPIs with trends, funnel, pipeline, revenue mix, what needs attention,
    intent activity, the team and plain-language insights - over what the
    caller can see."""
    from app.crm_core import dashboard_service

    return dashboard_service.insights(db, tenant_id, access, max(7, min(days, 365)))


@router.get("/dashboard/summary", response_model=DashboardSummaryOut)
def dashboard_summary(db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id), access: Access = Depends(get_access)) -> DashboardSummaryOut:
    """Every figure is computed over the records the caller can see, so the
    same dashboard reads "my numbers" for an executive and "the department's
    numbers" for its head."""
    from app.models.crm import LeadStatus, OpportunityStage, QuoteStatus
    from app.models.orders import OrderStatus

    def seen(query, model, entity_type):
        return access_service.apply(query, model, db, access, entity_type)

    total_parties = seen(db.query(Party).filter(Party.tenant_id == tenant_id), Party, "party").count()
    open_opportunities = seen(db.query(Opportunity), Opportunity, "opportunity").filter(
        Opportunity.tenant_id == tenant_id,
        Opportunity.stage.in_([OpportunityStage.draft, OpportunityStage.qualified, OpportunityStage.quoted]),
    ).count()
    pipeline_quotes = seen(db.query(Quote), Quote, "quote").filter(
        Quote.tenant_id == tenant_id, Quote.status.in_([QuoteStatus.draft, QuoteStatus.pending_approval])
    ).all()
    pipeline_value = sum(q.total_price for q in pipeline_quotes)
    pending_approvals = seen(db.query(Quote), Quote, "quote").filter(Quote.tenant_id == tenant_id, Quote.status == QuoteStatus.pending_approval).count()
    pending_lead_qualifications = seen(db.query(Lead), Lead, "lead").filter(
        Lead.tenant_id == tenant_id, Lead.status == LeadStatus.pending_qualification
    ).count()
    active_orders_list = seen(db.query(Order), Order, "order").filter(
        Order.tenant_id == tenant_id, Order.status.in_([OrderStatus.active, OrderStatus.changed])
    ).all()
    active_orders = len(active_orders_list)
    mrr = sum(o.monthly_price for o in active_orders_list)
    from app.crm_core import abo_service
    from app.models.fulfillment import AssetStatus
    visible_parties = access_service.apply(db.query(Party).filter(Party.tenant_id == tenant_id), Party, db, access, "party").all()
    active_assets = [a for p in visible_parties for a in abo_service.customer_assets(db, tenant_id, p.id) if a.status == AssetStatus.active]
    mrr += sum(abo_service.recurring_price(a) for a in active_assets)
    active_orders += len(active_assets)

    severities = ["critical", "high", "medium", "low"]
    ticket_query = db.query(TroubleTicket).filter(TroubleTicket.tenant_id == tenant_id, TroubleTicket.status != "resolved")
    party_filter = _visible_party_filter(db, access, TroubleTicket.party_id)
    if party_filter is not None:
        ticket_query = ticket_query.filter(or_(TroubleTicket.party_id.is_(None), party_filter))
    open_tickets_by_severity = {sev: ticket_query.filter(TroubleTicket.severity == sev).count() for sev in severities}
    open_work_orders = db.query(WorkOrder).filter(WorkOrder.tenant_id == tenant_id, WorkOrder.status.notin_(("complete", "cancelled"))).count()

    return DashboardSummaryOut(
        total_parties=total_parties,
        open_opportunities=open_opportunities,
        pipeline_value=round(pipeline_value, 2),
        pending_approvals=pending_approvals,
        pending_lead_qualifications=pending_lead_qualifications,
        active_orders=active_orders,
        monthly_recurring_revenue=round(mrr, 2),
        open_tickets_by_severity=open_tickets_by_severity,
        open_work_orders=open_work_orders,
    )

"""CRM Core system-of-record browsing API (HLD Layer 3: Lead/Opportunity/Quote,
Order, Customer 360, Ticket). Deliberately read-only/navigational - per the
HLD's design principle ("the unit of design is the stated outcome, not the
screen or the form"), state changes happen through POST /api/intents, not
through PATCH/PUT forms on these records. These endpoints exist so a user can
*find* the record (and its id) to state an intent against, and see the
system-of-record view a traditional CRM would call "Customer 360".
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db import get_db
from app.models.accounts import ServiceAccount
from app.models.assurance import Technician, TroubleTicket, WorkOrder
from app.models.catalog import ProductOffering
from app.models.crm import Lead, Opportunity, Party, Quote
from app.models.orders import BillingAccount, Order
from app.schemas.crm import (
    BillingAccountOut,
    BillingAdjustmentOut,
    DashboardSummaryOut,
    LeadOut,
    OpportunityOut,
    OrderOut,
    Party360Out,
    PartyOut,
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

router = APIRouter(prefix="/api", tags=["crm"])


# ---------- converters (handle the fields that need a join/lookup) ----------


def _quote_out(quote: Quote, offering_names: dict[int, str]) -> QuoteOut:
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
    approval_chain = None
    if quote.chain_instance:
        instance = quote.chain_instance
        current_tier = (
            instance.steps[instance.current_sequence - 1].approver_tier
            if instance.status.value == "pending" and instance.current_sequence <= len(instance.steps)
            else None
        )
        approval_chain = {
            "template_name": instance.template.name,
            "status": instance.status.value,
            "current_tier": current_tier,
            "steps": [
                {"sequence": s.sequence, "approver_tier": s.approver_tier, "status": s.status.value, "reasoning": s.reasoning or None}
                for s in instance.steps
            ],
        }
    return QuoteOut(
        id=quote.id,
        opportunity_id=quote.opportunity_id,
        status=quote.status.value,
        total_price=quote.total_price,
        requested_discount_pct=quote.requested_discount_pct,
        discount_justification=quote.discount_justification,
        created_at=quote.created_at,
        items=items,
        approval_chain=approval_chain,
        feasible=quote.feasibility_result.feasible if quote.feasibility_result else None,
    )


def _order_out(order: Order, party_name: str, service_account_name: str) -> OrderOut:
    service_orders = [
        ServiceOrderOut(
            id=so.id,
            status=so.status.value,
            resources=[ResourceOut(id=r.id, resource_type=r.resource_type, identifier=r.identifier, status=r.status.value) for r in so.resources],
        )
        for so in order.service_orders
    ]
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
    q: str | None = None, db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)
) -> list[Party]:
    query = db.query(Party).filter(Party.tenant_id == tenant_id)
    if q:
        query = query.filter(Party.name.ilike(f"%{q}%"))
    return query.order_by(Party.name).all()


@router.get("/parties/{party_id}/360", response_model=Party360Out)
def party_360(party_id: int, db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)) -> Party360Out:
    party = db.get(Party, party_id)
    if party is None or party.tenant_id != tenant_id:
        raise HTTPException(404, "Party not found")
    offering_names = _offering_names(db, tenant_id)

    leads = db.query(Lead).filter(Lead.tenant_id == tenant_id, Lead.party_id == party_id).all()
    lead_ids = [l.id for l in leads]
    opportunities = db.query(Opportunity).filter(Opportunity.tenant_id == tenant_id, Opportunity.lead_id.in_(lead_ids)).all() if lead_ids else []
    opp_ids = [o.id for o in opportunities]
    quotes = db.query(Quote).filter(Quote.tenant_id == tenant_id, Quote.opportunity_id.in_(opp_ids)).all() if opp_ids else []
    orders = db.query(Order).filter(Order.tenant_id == tenant_id, Order.party_id == party_id).all()
    billing_account = db.query(BillingAccount).filter(BillingAccount.tenant_id == tenant_id, BillingAccount.party_id == party_id).first()
    tickets = db.query(TroubleTicket).filter(TroubleTicket.tenant_id == tenant_id, TroubleTicket.party_id == party_id).all()

    service_accounts = (
        db.query(ServiceAccount).filter(ServiceAccount.billing_account_id == billing_account.id).all()
        if billing_account
        else []
    )
    service_account_names = {sa.id: sa.name for sa in service_accounts}
    orders_by_service_account: dict[int, list[Order]] = {}
    for o in orders:
        orders_by_service_account.setdefault(o.service_account_id, []).append(o)

    return Party360Out(
        party=PartyOut.model_validate(party),
        leads=[LeadOut.model_validate(l) for l in leads],
        opportunities=[OpportunityOut(id=o.id, lead_id=o.lead_id, name=o.name, stage=o.stage.value, created_at=o.created_at, updated_at=o.updated_at) for o in opportunities],
        quotes=[_quote_out(q, offering_names) for q in quotes],
        orders=[_order_out(o, party.name, service_account_names.get(o.service_account_id, "?")) for o in orders],
        service_accounts=[
            ServiceAccountOut(
                id=sa.id,
                billing_account_id=sa.billing_account_id,
                name=sa.name,
                status=sa.status,
                created_at=sa.created_at,
                orders=[_order_out(o, party.name, sa.name) for o in orders_by_service_account.get(sa.id, [])],
            )
            for sa in service_accounts
        ],
        billing_account=_billing_account_out(billing_account, party.name) if billing_account else None,
        tickets=[_ticket_out(t) for t in tickets],
    )


# ---------- Sales Pipeline (Leads / Opportunities / Quotes) ----------


@router.get("/leads", response_model=list[LeadOut])
def list_leads(db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)) -> list[Lead]:
    return db.query(Lead).filter(Lead.tenant_id == tenant_id).order_by(Lead.created_at.desc()).all()


@router.get("/opportunities", response_model=list[OpportunityOut])
def list_opportunities(
    stage: str | None = None, db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)
):
    query = db.query(Opportunity).filter(Opportunity.tenant_id == tenant_id)
    if stage:
        query = query.filter(Opportunity.stage == stage)
    opportunities = query.order_by(Opportunity.updated_at.desc()).all()
    return [
        OpportunityOut(id=o.id, lead_id=o.lead_id, name=o.name, stage=o.stage.value, created_at=o.created_at, updated_at=o.updated_at)
        for o in opportunities
    ]


@router.get("/quotes", response_model=list[QuoteOut])
def list_quotes(status: str | None = None, db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)):
    offering_names = _offering_names(db, tenant_id)
    query = db.query(Quote).filter(Quote.tenant_id == tenant_id)
    if status:
        query = query.filter(Quote.status == status)
    return [_quote_out(q, offering_names) for q in query.order_by(Quote.created_at.desc()).all()]


@router.get("/quotes/{quote_id}", response_model=QuoteOut)
def quote_detail(quote_id: int, db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)) -> QuoteOut:
    offering_names = _offering_names(db, tenant_id)
    quote = db.get(Quote, quote_id)
    if quote is None or quote.tenant_id != tenant_id:
        raise HTTPException(404, "Quote not found")
    return _quote_out(quote, offering_names)


# ---------- Orders / Billing ----------


@router.get("/orders", response_model=list[OrderOut])
def list_orders(status: str | None = None, db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)):
    query = db.query(Order).filter(Order.tenant_id == tenant_id)
    if status:
        query = query.filter(Order.status == status)
    orders = query.order_by(Order.created_at.desc()).all()
    party_names = {p.id: p.name for p in db.query(Party.id, Party.name).filter(Party.tenant_id == tenant_id).all()}
    service_account_names = {
        sa.id: sa.name for sa in db.query(ServiceAccount.id, ServiceAccount.name).filter(ServiceAccount.tenant_id == tenant_id).all()
    }
    return [_order_out(o, party_names.get(o.party_id, "?"), service_account_names.get(o.service_account_id, "?")) for o in orders]


@router.get("/orders/{order_id}", response_model=OrderOut)
def order_detail(order_id: int, db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)) -> OrderOut:
    order = db.get(Order, order_id)
    if order is None or order.tenant_id != tenant_id:
        raise HTTPException(404, "Order not found")
    party = db.get(Party, order.party_id)
    service_account = db.get(ServiceAccount, order.service_account_id)
    return _order_out(order, party.name if party else "?", service_account.name if service_account else "?")


@router.get("/billing-accounts", response_model=list[BillingAccountOut])
def list_billing_accounts(db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)):
    accounts = db.query(BillingAccount).filter(BillingAccount.tenant_id == tenant_id).all()
    party_names = {p.id: p.name for p in db.query(Party.id, Party.name).filter(Party.tenant_id == tenant_id).all()}
    return [_billing_account_out(a, party_names.get(a.party_id, "?")) for a in accounts]


# ---------- Trouble Tickets / Field Service ----------


@router.get("/tickets", response_model=list[TroubleTicketOut])
def list_tickets(
    status: str | None = None,
    severity: str | None = None,
    db: Session = Depends(get_db),
    tenant_id: int = Depends(get_current_tenant_id),
):
    query = db.query(TroubleTicket).filter(TroubleTicket.tenant_id == tenant_id)
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


@router.get("/dashboard/summary", response_model=DashboardSummaryOut)
def dashboard_summary(db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)) -> DashboardSummaryOut:
    from app.models.crm import OpportunityStage, QuoteStatus
    from app.models.orders import OrderStatus

    total_parties = db.query(Party).filter(Party.tenant_id == tenant_id).count()
    open_opportunities = db.query(Opportunity).filter(
        Opportunity.tenant_id == tenant_id,
        Opportunity.stage.in_([OpportunityStage.draft, OpportunityStage.qualified, OpportunityStage.quoted]),
    ).count()
    pipeline_quotes = db.query(Quote).filter(
        Quote.tenant_id == tenant_id, Quote.status.in_([QuoteStatus.draft, QuoteStatus.pending_approval])
    ).all()
    pipeline_value = sum(q.total_price for q in pipeline_quotes)
    pending_approvals = db.query(Quote).filter(Quote.tenant_id == tenant_id, Quote.status == QuoteStatus.pending_approval).count()
    active_orders_list = db.query(Order).filter(
        Order.tenant_id == tenant_id, Order.status.in_([OrderStatus.active, OrderStatus.changed])
    ).all()
    active_orders = len(active_orders_list)
    mrr = sum(o.monthly_price for o in active_orders_list)

    severities = ["critical", "high", "medium", "low"]
    open_tickets_by_severity = {
        sev: db.query(TroubleTicket)
        .filter(TroubleTicket.tenant_id == tenant_id, TroubleTicket.severity == sev, TroubleTicket.status != "resolved")
        .count()
        for sev in severities
    }
    open_work_orders = db.query(WorkOrder).filter(WorkOrder.tenant_id == tenant_id, WorkOrder.status != "complete").count()

    return DashboardSummaryOut(
        total_parties=total_parties,
        open_opportunities=open_opportunities,
        pipeline_value=round(pipeline_value, 2),
        pending_approvals=pending_approvals,
        active_orders=active_orders,
        monthly_recurring_revenue=round(mrr, 2),
        open_tickets_by_severity=open_tickets_by_severity,
        open_work_orders=open_work_orders,
    )

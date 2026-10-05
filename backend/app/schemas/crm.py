from __future__ import annotations

import datetime as dt

from pydantic import BaseModel


class PartyOut(BaseModel):
    id: int
    name: str
    party_type: str
    email: str | None
    phone: str | None
    created_at: dt.datetime

    model_config = {"from_attributes": True}


class LeadOut(BaseModel):
    id: int
    party_id: int
    source: str
    region: str
    owner_name: str
    propensity_score: float
    created_at: dt.datetime

    model_config = {"from_attributes": True}


class OpportunityOut(BaseModel):
    id: int
    lead_id: int
    name: str
    stage: str
    created_at: dt.datetime
    updated_at: dt.datetime

    model_config = {"from_attributes": True}


class QuoteItemOut(BaseModel):
    id: int
    product_offering_id: int
    product_offering_name: str
    quantity: int
    unit_price: float

    model_config = {"from_attributes": True}


class ApprovalStepOut(BaseModel):
    sequence: int
    approver_tier: str
    status: str
    reasoning: str | None

    model_config = {"from_attributes": True}


class ApprovalChainOut(BaseModel):
    template_name: str
    status: str
    current_tier: str | None
    steps: list[ApprovalStepOut]

    model_config = {"from_attributes": True}


class QuoteOut(BaseModel):
    id: int
    opportunity_id: int
    status: str
    total_price: float
    requested_discount_pct: float
    discount_justification: str | None
    created_at: dt.datetime
    items: list[QuoteItemOut] = []
    approval_chain: ApprovalChainOut | None = None
    feasible: bool | None = None

    model_config = {"from_attributes": True}


class ResourceOut(BaseModel):
    id: int
    resource_type: str
    identifier: str
    status: str

    model_config = {"from_attributes": True}


class ServiceOrderOut(BaseModel):
    id: int
    status: str
    resources: list[ResourceOut] = []

    model_config = {"from_attributes": True}


class OrderOut(BaseModel):
    id: int
    party_id: int
    party_name: str
    service_account_id: int
    service_account_name: str
    quote_id: int | None
    order_type: str
    status: str
    monthly_price: float
    contract_months: int
    created_at: dt.datetime
    service_orders: list[ServiceOrderOut] = []

    model_config = {"from_attributes": True}


class ServiceAccountOut(BaseModel):
    id: int
    billing_account_id: int
    name: str
    status: str
    created_at: dt.datetime
    orders: list[OrderOut] = []

    model_config = {"from_attributes": True}


class BillingAdjustmentOut(BaseModel):
    id: int
    order_id: int | None
    amount: float
    reason: str
    created_at: dt.datetime

    model_config = {"from_attributes": True}


class BillingAccountOut(BaseModel):
    id: int
    party_id: int
    party_name: str
    status: str
    rate_plan: str
    balance: float
    adjustments: list[BillingAdjustmentOut] = []

    model_config = {"from_attributes": True}


class RootCauseOut(BaseModel):
    probable_cause: str
    confidence: float
    remediation_suggestion: str
    field_dispatch_required: bool
    escalated_to_human: bool

    model_config = {"from_attributes": True}


class TroubleTicketOut(BaseModel):
    id: int
    party_id: int | None
    category: str
    severity: str
    product_line: str
    sla_class: str
    priority: str
    status: str
    description: str
    created_at: dt.datetime
    root_cause: RootCauseOut | None = None

    model_config = {"from_attributes": True}


class CompletionEvidenceOut(BaseModel):
    checklist: str
    signature: str
    completed_at: dt.datetime

    model_config = {"from_attributes": True}


class WorkOrderOut(BaseModel):
    id: int
    ticket_id: int | None
    order_id: int | None
    scheduled_window: str
    status: str
    technician_name: str | None = None
    completion_evidence: CompletionEvidenceOut | None = None

    model_config = {"from_attributes": True}


class TechnicianOut(BaseModel):
    id: int
    name: str
    skills: str
    region: str
    status: str

    model_config = {"from_attributes": True}


class Party360Out(BaseModel):
    party: PartyOut
    leads: list[LeadOut]
    opportunities: list[OpportunityOut]
    quotes: list[QuoteOut]
    orders: list[OrderOut]
    service_accounts: list[ServiceAccountOut]
    billing_account: BillingAccountOut | None
    tickets: list[TroubleTicketOut]


class DashboardSummaryOut(BaseModel):
    total_parties: int
    open_opportunities: int
    pipeline_value: float
    pending_approvals: int
    active_orders: int
    monthly_recurring_revenue: float
    open_tickets_by_severity: dict[str, int]
    open_work_orders: int

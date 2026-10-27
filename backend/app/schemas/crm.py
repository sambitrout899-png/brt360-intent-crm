from __future__ import annotations

import datetime as dt

from pydantic import BaseModel


class PartyProfileOut(BaseModel):
    segment: str | None = None
    account_status: str | None = None
    price_list_id: int | None = None
    industry: str | None = None

    model_config = {"from_attributes": True}


class PartyOut(BaseModel):
    id: int
    name: str
    party_type: str
    email: str | None
    phone: str | None
    created_at: dt.datetime
    # Sales-to-Order details (None for parties that never took those steps)
    profile: PartyProfileOut | None = None

    model_config = {"from_attributes": True}


class LeadOut(BaseModel):
    id: int
    party_id: int
    source: str
    region: str
    owner_name: str
    propensity_score: float
    status: str
    interested_offering_name: str | None
    created_at: dt.datetime
    approval_chain: dict | None = None

    model_config = {"from_attributes": True}


class OpportunityOut(BaseModel):
    id: int
    lead_id: int
    name: str
    stage: str
    offering_name: str | None = None
    quantity: int = 1
    created_at: dt.datetime
    updated_at: dt.datetime
    feasibility_assessments: list[dict] = []
    # Sales-to-Order: configuration + Presales hand-off (None = not used)
    config: dict | None = None

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


class ProposalOut(BaseModel):
    id: int
    quote_id: int
    status: str
    document_ref: str
    sent_at: dt.datetime
    decided_at: dt.datetime | None

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
    proposals: list[ProposalOut] = []
    revision_number: int = 1
    revision_of_quote_id: int | None = None
    # Sales-to-Order: the customer's PO and Sales Ops' match
    purchase_order: dict | None = None
    # Product configuration: chosen attributes, price breakdown, the quote's own pricelist
    configuration: dict | None = None
    # The PDFs on the quote - proposal, the customer's PO, the agreement, others (no file content)
    documents: list[dict] = []
    # The order the quote became, once it is ordered
    order_id: int | None = None
    # The negotiated price (INT-116): {"active": {...} | None, "history": [...]} - None = never overridden
    price_override: dict | None = None

    model_config = {"from_attributes": True}


class ResourceOut(BaseModel):
    id: int
    resource_type: str
    identifier: str
    status: str

    model_config = {"from_attributes": True}


class ServiceOrderOut(BaseModel):
    id: int
    site_label: str
    status: str
    resources: list[ResourceOut] = []

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
    order_item_id: int | None = None
    kind: str = "field_dispatch"
    technical_parameters: str = ""
    scheduled_window: str
    status: str
    technician_name: str | None = None
    completion_evidence: CompletionEvidenceOut | None = None

    model_config = {"from_attributes": True}


class AssetOut(BaseModel):
    order_history: list[dict] = []
    instance_json: str = "{}"
    version: int = 1
    id: int
    service_account_id: int
    order_item_id: int | None
    asset_type: str
    identifier: str
    status: str
    inventory_system_ref: str | None
    created_at: dt.datetime

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
    # Phase 5 fulfillment chain - order_control_chain is the Order Control
    # Unit's polymorphic approval chain (None if auto-approved/unconfigured
    # or not yet approved), fulfillment_work_orders is the
    # Installation/Connectivity Test/Acceptance Letter chain (kept
    # separate from the ticket-driven field_dispatch work orders a
    # TroubleTicket raises).
    order_control_chain: ApprovalChainOut | None = None
    fulfillment_work_orders: list[WorkOrderOut] = []
    # Fulfilment milestones reported back (Fulfillment), and - for a consumer order
    # waiting on a discount approval - that approval.
    milestones: list[dict] = []
    segment: str | None = None
    # Product configuration per ordered product: pricing + technical attributes
    configuration: list[dict] = []
    discount_approval: ApprovalChainOut | None = None

    model_config = {"from_attributes": True}


class ServiceAccountOut(BaseModel):
    party_id: int | None = None
    id: int
    billing_account_id: int
    name: str
    status: str
    created_at: dt.datetime
    orders: list[OrderOut] = []
    assets: list[AssetOut] = []

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


class TechnicianOut(BaseModel):
    id: int
    name: str
    skills: str
    region: str
    status: str

    model_config = {"from_attributes": True}


class IdentityVerificationOut(BaseModel):
    id: int
    provider_name: str
    id_type: str
    id_last4: str
    status: str
    detail: str
    created_at: dt.datetime
    resolved_at: dt.datetime | None

    model_config = {"from_attributes": True}


class Party360Out(BaseModel):
    party: PartyOut
    leads: list[LeadOut]
    opportunities: list[OpportunityOut]
    quotes: list[QuoteOut]
    orders: list[OrderOut]
    service_accounts: list[ServiceAccountOut]
    identity_verifications: list[IdentityVerificationOut]
    billing_account: BillingAccountOut | None
    tickets: list[TroubleTicketOut]


class DashboardSummaryOut(BaseModel):
    total_parties: int
    open_opportunities: int
    pipeline_value: float
    pending_approvals: int
    pending_lead_qualifications: int
    active_orders: int
    monthly_recurring_revenue: float
    open_tickets_by_severity: dict[str, int]
    open_work_orders: int

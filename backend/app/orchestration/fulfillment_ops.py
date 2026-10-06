"""Phase 5 order-fulfillment operations catalog.

Every function here is a small, named, independently-callable, fully
deterministic unit of work - the actual business logic. They are called
two ways:

1. Directly, in a fixed sequence, by actions_fulfillment.py's
   build_intXX_steps functions - the "deterministic" orchestration mode
   (default, and the only mode when no OrchestrationPolicy opts a tenant
   into "llm_planned" - see app/models/orchestration_policy.py).
2. By app/orchestration/llm_planner.py, which asks an LLM to choose
   *which* of these to call, in what order, with what parameters - the
   "llm_planned" mode. The LLM only ever selects from CATALOG below and
   every call is validated against each function's declared parameter
   names before it runs; it can never execute anything else. This is the
   hard safety boundary for billing/network-affecting actions - exactly
   the same boundary every other adapter-based intent in this codebase
   already has, just made explicit and reusable here.

Each function takes `db: Session, tenant_id: int` plus its own
JSON-serializable params, and returns a JSON-serializable dict result -
required so the LLM planner can round-trip real results back into
later steps' parameters within one plan.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.crm_core import account_service, approval_service
from app.integration import (
    billing_initiation_adapter,
    document_adapter,
    inventory_adapter,
    network_activation_adapter,
    order_adapter,
    orchestration_adapter,
)
from app.models.assurance import CompletionEvidence, WorkOrder
from app.models.crm import Opportunity, Party, Quote
from app.models.fulfillment import Asset, AssetStatus, Proposal, ProposalStatus
from app.models.orders import Order, OrderStatus, OrderType
from app.timeutils import utcnow


def op_generate_proposal(db: Session, tenant_id: int, quote_id: int) -> dict[str, Any]:
    quote = db.get(Quote, quote_id)
    if quote is None or quote.tenant_id != tenant_id:
        raise ValueError(f"Quote {quote_id} not found")
    if quote.status.value != "approved":
        raise ValueError(f"Quote {quote_id} is not approved (status={quote.status.value}) - cannot propose it")
    document_ref = document_adapter.store_document_reference("proposal", f"quote-{quote_id}")
    proposal = Proposal(tenant_id=tenant_id, quote_id=quote_id, status=ProposalStatus.sent, document_ref=document_ref)
    db.add(proposal)
    db.flush()
    return {"proposal_id": proposal.id, "quote_id": quote_id, "status": proposal.status.value, "document_ref": document_ref}


def op_record_customer_decision(db: Session, tenant_id: int, proposal_id: int, decision: str) -> dict[str, Any]:
    proposal = db.get(Proposal, proposal_id)
    if proposal is None or proposal.tenant_id != tenant_id:
        raise ValueError(f"Proposal {proposal_id} not found")
    proposal.status = ProposalStatus.customer_approved if decision == "approve" else ProposalStatus.customer_rejected
    proposal.decided_at = utcnow()
    db.flush()
    return {"proposal_id": proposal.id, "status": proposal.status.value}


def op_generate_order_from_quote(db: Session, tenant_id: int, quote_id: int) -> dict[str, Any]:
    quote = db.get(Quote, quote_id)
    if quote is None or quote.tenant_id != tenant_id:
        raise ValueError(f"Quote {quote_id} not found")
    if quote.status.value != "approved":
        raise ValueError(f"Quote {quote_id} is not approved (status={quote.status.value})")
    approved_proposal = (
        db.query(Proposal)
        .filter(Proposal.quote_id == quote_id, Proposal.status == ProposalStatus.customer_approved)
        .first()
    )
    if approved_proposal is None:
        raise ValueError(f"Quote {quote_id} has no customer-approved proposal yet - generate and approve one first")

    opportunity = db.get(Opportunity, quote.opportunity_id)
    party_id = opportunity.lead.party_id
    billing_account = order_adapter.get_or_create_billing_account(db, tenant_id, party_id)
    first_item = quote.items[0] if quote.items else None
    service_account_name = f"Order for Quote {quote_id}"
    service_account = account_service.get_or_create_service_account(db, tenant_id, billing_account.id, service_account_name)

    order = order_adapter.create_order(
        db,
        tenant_id=tenant_id,
        party_id=party_id,
        service_account_id=service_account.id,
        quote_id=quote_id,
        order_type=OrderType.new,
        monthly_price=quote.total_price,
    )
    order_item_ids = []
    for item in quote.items:
        order_item = order_adapter.add_order_item(db, order.id, item.product_offering_id, item.quantity)
        order_item_ids.append(order_item.id)
    return {
        "order_id": order.id,
        "order_status": order.status.value,
        "service_account_id": service_account.id,
        "order_item_ids": order_item_ids,
    }


def op_approve_order(db: Session, tenant_id: int, order_id: int, decision: str = "approve", reasoning: str = "") -> dict[str, Any]:
    """Order Control Unit gate - opt-in, same two-phase shape as the
    lead-qualification gate (INT-04/17): no "order" chain template
    configured means this auto-approves on the spot.

    Guarded on order.status == "created": that status never changes
    until the order is actually submitted to orchestration (which only
    happens once the chain is fully approved - see
    actions_fulfillment.py's build_int25_steps), including through every
    intermediate tier of a multi-tier chain, so this guard admits every
    legitimate call and only rejects calling INT-25 again on an order
    that has already been submitted - without it, a repeat call would
    silently resubmit to orchestration and raise a second, duplicate
    Installation work order.
    """
    order = db.get(Order, order_id)
    if order is None or order.tenant_id != tenant_id:
        raise ValueError(f"Order {order_id} not found")
    if order.status.value != "created":
        raise ValueError(f"Order {order_id} has already been submitted for fulfillment (status={order.status.value})")

    instance = approval_service.get_chain_instance(db, "order", order.id)
    if instance is None:
        template = approval_service.resolve_order_control_template(db, tenant_id)
        if template is None:
            return {"order_id": order.id, "chain_status": "approved", "auto_approved": True}
        instance = approval_service.instantiate_chain(db, "order", order.id, template)

    outcome = approval_service.record_step_decision(db, instance, decision, reasoning)
    return {"order_id": order.id, "auto_approved": False, **outcome}


def op_submit_order_to_orchestration(db: Session, tenant_id: int, order_id: int) -> dict[str, Any]:
    order = db.get(Order, order_id)
    if order is None or order.tenant_id != tenant_id:
        raise ValueError(f"Order {order_id} not found")
    decomposition = orchestration_adapter.decompose_order(order_id)
    order.status = OrderStatus.activating
    db.flush()
    # "order_status" (not "status") - this result merges with work-order
    # results in the same accumulated dict (see actions_fulfillment.py's
    # _run_consequence), which have their own "work_order_status"; a bare
    # "status" key would silently collide and whichever merged last would
    # clobber the other in the final saga result.
    return {"order_id": order.id, "order_status": order.status.value, **decomposition}


def op_create_work_order(db: Session, tenant_id: int, order_id: int, kind: str, order_item_id: int | None = None) -> dict[str, Any]:
    work_order = WorkOrder(tenant_id=tenant_id, order_id=order_id, order_item_id=order_item_id, kind=kind, status="scheduled")
    db.add(work_order)
    db.flush()
    return {"work_order_id": work_order.id, "order_id": order_id, "kind": kind, "work_order_status": work_order.status}


def op_record_technical_parameters(db: Session, tenant_id: int, work_order_id: int, parameters: str) -> dict[str, Any]:
    work_order = db.get(WorkOrder, work_order_id)
    if work_order is None or work_order.tenant_id != tenant_id:
        raise ValueError(f"Work order {work_order_id} not found")
    work_order.technical_parameters = parameters
    db.flush()
    return {"work_order_id": work_order.id, "technical_parameters": parameters}


def op_close_work_order(db: Session, tenant_id: int, work_order_id: int) -> dict[str, Any]:
    """Keys are prefixed "closed_*" (except order_id/order_item_id, which
    are just pass-through context for the next step) - a later
    create_work_order call in the same accumulated saga result has its
    own "work_order_id"/"kind" for the newly-raised *next* work order,
    and an unprefixed key here would silently collide with (and in the
    final result, be clobbered by) that one.
    """
    work_order = db.get(WorkOrder, work_order_id)
    if work_order is None or work_order.tenant_id != tenant_id:
        raise ValueError(f"Work order {work_order_id} not found")
    work_order.status = "complete"
    db.flush()
    return {
        "closed_work_order_id": work_order.id,
        "closed_work_order_kind": work_order.kind,
        "order_id": work_order.order_id,
        "order_item_id": work_order.order_item_id,
        "closed_work_order_status": "complete",
    }


def op_activate_network(db: Session, tenant_id: int, order_item_id: int) -> dict[str, Any]:
    ref = network_activation_adapter.activate_connection(order_item_id)
    return {"order_item_id": order_item_id, "network_activation_ref": ref}


def op_record_connectivity_acceptance(db: Session, tenant_id: int, work_order_id: int, signature: str) -> dict[str, Any]:
    work_order = db.get(WorkOrder, work_order_id)
    if work_order is None or work_order.tenant_id != tenant_id:
        raise ValueError(f"Work order {work_order_id} not found")
    evidence = CompletionEvidence(
        work_order_id=work_order_id, checklist="Connectivity verified with customer", signature=signature
    )
    db.add(evidence)
    db.flush()
    return {"work_order_id": work_order_id, "signature": signature}


def op_attach_acceptance_letter(db: Session, tenant_id: int, work_order_id: int, document_ref: str | None = None) -> dict[str, Any]:
    work_order = db.get(WorkOrder, work_order_id)
    if work_order is None or work_order.tenant_id != tenant_id:
        raise ValueError(f"Work order {work_order_id} not found")
    if not document_ref:
        document_ref = document_adapter.store_document_reference("acceptance_letter", f"work_order-{work_order_id}")
    evidence = CompletionEvidence(
        work_order_id=work_order_id, checklist=f"Acceptance letter attached: {document_ref}", signature=""
    )
    db.add(evidence)
    db.flush()
    return {"work_order_id": work_order_id, "document_ref": document_ref}


def op_initiate_billing(db: Session, tenant_id: int, service_account_id: int) -> dict[str, Any]:
    ref = billing_initiation_adapter.initiate_billing(service_account_id)
    return {"service_account_id": service_account_id, "billing_initiation_ref": ref}


def op_create_asset(
    db: Session, tenant_id: int, service_account_id: int, order_item_id: int | None, asset_type: str, identifier: str
) -> dict[str, Any]:
    asset = Asset(
        tenant_id=tenant_id,
        service_account_id=service_account_id,
        order_item_id=order_item_id,
        asset_type=asset_type,
        identifier=identifier,
        status=AssetStatus.active,
    )
    db.add(asset)
    db.flush()
    inventory_ref = inventory_adapter.sync_asset_to_inventory(asset_type, identifier)
    asset.inventory_system_ref = inventory_ref
    db.flush()
    return {"asset_id": asset.id, "inventory_system_ref": inventory_ref}


def op_activate_order(db: Session, tenant_id: int, order_id: int) -> dict[str, Any]:
    order = db.get(Order, order_id)
    if order is None or order.tenant_id != tenant_id:
        raise ValueError(f"Order {order_id} not found")
    order.status = OrderStatus.active
    db.flush()
    return {"order_id": order.id, "order_status": order.status.value}


def op_convert_prospect_to_customer(db: Session, tenant_id: int, party_id: int) -> dict[str, Any]:
    """Fires once fulfillment actually completes (the third work order
    closes) - whatever this Party was called before (INT-15's literal
    "prospect", or the "organization" default INT-04's Qualify-a-lead
    path has always used), having a live billed Asset makes it a
    customer. Idempotent: already "customer" is left alone.
    """
    party = db.get(Party, party_id)
    if party is None or party.tenant_id != tenant_id:
        raise ValueError(f"Party {party_id} not found")
    party.party_type = "customer"
    db.flush()
    return {"party_id": party.id, "party_type": party.party_type}


# The registered catalog an LLM planner (or anything else) is allowed to
# invoke by name - see app/orchestration/llm_planner.py. Keys are the
# names an LLM plan must use; values are the real functions above.
CATALOG: dict[str, Any] = {
    "generate_proposal": op_generate_proposal,
    "record_customer_decision": op_record_customer_decision,
    "generate_order_from_quote": op_generate_order_from_quote,
    "approve_order": op_approve_order,
    "submit_order_to_orchestration": op_submit_order_to_orchestration,
    "create_work_order": op_create_work_order,
    "record_technical_parameters": op_record_technical_parameters,
    "close_work_order": op_close_work_order,
    "activate_network": op_activate_network,
    "record_connectivity_acceptance": op_record_connectivity_acceptance,
    "attach_acceptance_letter": op_attach_acceptance_letter,
    "initiate_billing": op_initiate_billing,
    "create_asset": op_create_asset,
    "activate_order": op_activate_order,
    "convert_prospect_to_customer": op_convert_prospect_to_customer,
}

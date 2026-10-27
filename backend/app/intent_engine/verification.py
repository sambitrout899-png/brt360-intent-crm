"""Verification & Notification (HLD 4.5).

Checks each step's post-condition against the original intent - e.g. is the
offering *actually* active with the expected version, not just "saga said ok".
Only after this passes does the caller get a completion notification. This is
deliberately separate from the mid-saga "notify_channels" step in
orchestration/actions.py, which announces the change to Sales/Care - this
step verifies the change actually took effect before telling the *requester*
it's done.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from app.models.assurance import TroubleTicket, WorkOrder
from app.models.catalog import OfferingStatus, ProductOffering
from app.models.crm import Lead, Opportunity, OpportunityStage, Party, Quote, QuoteStatus
from app.models.feasibility import FeasibilityAssessment
from app.models.fulfillment import Proposal
from app.models.identity import IdentityVerification
from app.models.orders import Order, OrderStatus


@dataclass
class VerificationResult:
    ok: bool
    detail: str


def _require(result: dict[str, Any] | None, key: str) -> Any | None:
    return result.get(key) if result else None


def verify(db: Session, intent_type: str, result: dict[str, Any] | None) -> VerificationResult:
    if intent_type == "INT-106":
        if _require(result, "configuration_request"):
            return VerificationResult(not _require(result, "order_id"), "Configuration requested; no order submitted yet.")
        order = db.get(Order, _require(result, "order_id") or 0)
        item_id = _require(result, "order_item_id")
        valid = order is not None and any(item.id == item_id for item in order.items) and order.status in (
            OrderStatus.active, OrderStatus.completed, OrderStatus.activating, OrderStatus.pending_approval)
        return VerificationResult(valid, "Order and order line submitted through the provisioning/approval sequence." if valid else "Order or order line was not submitted.")
    if intent_type in ("INT-01", "INT-02", "INT-03"):
        offering_id = _require(result, "product_offering_id")
        if offering_id is None:
            return VerificationResult(False, "No product_offering_id in the saga result to verify.")
        offering = db.get(ProductOffering, offering_id)
        if offering is None:
            return VerificationResult(False, f"Offering {offering_id} not found after execution.")

        if intent_type in ("INT-01", "INT-02"):
            if offering.status != OfferingStatus.active:
                return VerificationResult(False, f"Expected status 'active', found '{offering.status.value}'.")
            return VerificationResult(True, f"Offering {offering.id} v{offering.version} confirmed active.")

        if offering.status != OfferingStatus.retired:
            return VerificationResult(False, f"Expected status 'retired', found '{offering.status.value}'.")
        if offering.replacement_offering_id is None:
            return VerificationResult(False, "Offering retired but no replacement_offering_id recorded.")
        return VerificationResult(True, f"Offering {offering.id} confirmed retired with a migration path on file.")

    if intent_type == "INT-04":
        lead_id = _require(result, "lead_id")
        lead = db.get(Lead, lead_id) if lead_id else None
        if lead is None:
            return VerificationResult(False, "No lead created.")
        opportunity_id = _require(result, "opportunity_id")
        if opportunity_id is None:
            # No Sales Director gate configured for this tenant, or the
            # gate is configured and the lead is awaiting INT-17 - either
            # way, the lead itself existing is enough to verify here.
            return VerificationResult(True, f"Lead {lead.id} created, status '{lead.status.value}'.")
        opportunity = db.get(Opportunity, opportunity_id)
        if opportunity is None:
            return VerificationResult(False, "No opportunity created.")
        return VerificationResult(True, f"Lead qualified, opportunity {opportunity.id} created in draft stage.")

    if intent_type == "INT-05":
        quote_id = _require(result, "quote_id")
        quote = db.get(Quote, quote_id) if quote_id else None
        if quote is None:
            return VerificationResult(False, "No quote created.")
        if quote.total_price <= 0:
            return VerificationResult(False, f"Quote {quote.id} has a non-positive total price.")
        return VerificationResult(True, f"Quote {quote.id} confirmed at {quote.total_price}.")

    if intent_type == "INT-06":
        # A multi-tier chain can legitimately leave the quote at
        # pending_approval after an intermediate tier's decision - only
        # the absence of any recorded decision is a verification failure.
        quote_id = _require(result, "quote_id")
        quote = db.get(Quote, quote_id) if quote_id else None
        if quote is None:
            return VerificationResult(False, "Quote not found after approval decision.")
        if quote.status not in (QuoteStatus.approved, QuoteStatus.rejected, QuoteStatus.pending_approval):
            return VerificationResult(
                False, f"Expected quote status approved/rejected/pending_approval, found '{quote.status.value}'."
            )
        instance = quote.chain_instance
        if instance is not None and result.get("waiting_for") and quote.status == QuoteStatus.pending_approval:
            return VerificationResult(True, f"Quote {quote.id} submitted for approval - waiting for {result['waiting_for']}.")
        if instance is None or not any(s.status.value != "pending" for s in instance.steps):
            return VerificationResult(False, "No approval chain decision was recorded for this quote.")
        return VerificationResult(True, f"Quote {quote.id} chain status: {quote.status.value} (chain {instance.status.value}).")

    if intent_type in ("INT-07", "INT-08", "INT-09"):
        order_id = _require(result, "order_id")
        order = db.get(Order, order_id) if order_id else None
        if order is None:
            return VerificationResult(False, "No order found after execution.")
        # A multi-site order (Phase 3 B2B process pack) deliberately stays
        # "activating" after INT-07 - each site provisions independently
        # via INT-14, so only a single-site INT-07 call must reach "active".
        if intent_type == "INT-07" and order.status == OrderStatus.pending_approval and result.get("waiting_for"):
            return VerificationResult(True, f"Order {order.id} placed - its discount waits for {result['waiting_for']}.")
        if intent_type == "INT-07" and order.status not in (OrderStatus.active, OrderStatus.completed, OrderStatus.activating):
            return VerificationResult(False, f"Expected order status 'active' (or 'activating' for a multi-site order), found '{order.status.value}'.")
        return VerificationResult(True, f"Order {order.id} confirmed status '{order.status.value}'.")

    if intent_type == "INT-14":
        order_id = _require(result, "order_id")
        order = db.get(Order, order_id) if order_id else None
        if order is None:
            return VerificationResult(False, "No order found after site provisioning.")
        return VerificationResult(
            True, f"Site '{result.get('site_label')}' provisioned on order {order.id} (order status: {order.status.value})."
        )

    if intent_type == "INT-10":
        ticket_id = _require(result, "ticket_id")
        ticket = db.get(TroubleTicket, ticket_id) if ticket_id else None
        if ticket is None:
            return VerificationResult(False, "No trouble ticket created.")
        return VerificationResult(True, f"Ticket {ticket.id} created with priority {ticket.priority}.")

    if intent_type == "INT-11":
        ticket_id = _require(result, "ticket_id")
        ticket = db.get(TroubleTicket, ticket_id) if ticket_id else None
        if ticket is None:
            return VerificationResult(False, "Ticket not found after diagnosis.")
        if ticket.root_cause_analysis is None:
            return VerificationResult(False, "No root cause analysis recorded.")
        return VerificationResult(True, f"Diagnosis recorded for ticket {ticket.id} (confidence {ticket.root_cause_analysis.confidence}).")

    if intent_type == "INT-13":
        verification_id = _require(result, "verification_id")
        record = db.get(IdentityVerification, verification_id) if verification_id else None
        if record is None:
            return VerificationResult(False, "No identity verification record created.")
        return VerificationResult(True, f"Identity verification {record.id} recorded: {record.status.value}.")

    if intent_type == "INT-12":
        work_order_id = _require(result, "work_order_id")
        wo = db.get(WorkOrder, work_order_id) if work_order_id else None
        if wo is None:
            return VerificationResult(False, "No work order found after execution.")
        return VerificationResult(True, f"Work order {wo.id} confirmed status '{wo.status}'.")

    if intent_type == "INT-15":
        party_id = _require(result, "party_id")
        party = db.get(Party, party_id) if party_id else None
        if party is None:
            return VerificationResult(False, "No prospect party created.")
        return VerificationResult(True, f"Prospect {party.id} ('{party.name}') on file.")

    if intent_type == "INT-16":
        lead_id = _require(result, "lead_id")
        lead = db.get(Lead, lead_id) if lead_id else None
        if lead is None:
            return VerificationResult(False, "No lead found after adding product interest.")
        if not lead.interested_offering_name:
            return VerificationResult(False, f"Lead {lead.id} has no interested_offering_name recorded.")
        return VerificationResult(True, f"Lead {lead.id} now interested in '{lead.interested_offering_name}'.")

    if intent_type == "INT-17":
        lead_id = _require(result, "lead_id")
        lead = db.get(Lead, lead_id) if lead_id else None
        if lead is None:
            return VerificationResult(False, "Lead not found after qualification decision.")
        if lead.status.value == "qualified" and _require(result, "opportunity_id") is None:
            return VerificationResult(False, f"Lead {lead.id} qualified but no opportunity_id in the saga result.")
        return VerificationResult(True, f"Lead {lead.id} chain decision recorded, status '{lead.status.value}'.")

    if intent_type == "INT-18":
        assessment_id = _require(result, "assessment_id")
        assessment = db.get(FeasibilityAssessment, assessment_id) if assessment_id else None
        if assessment is None:
            return VerificationResult(False, "No feasibility assessment recorded.")
        return VerificationResult(True, f"Feasibility assessment {assessment.id} outcome: {assessment.outcome.value}.")

    if intent_type in ("INT-19", "INT-20", "INT-116"):
        quote_id = _require(result, "quote_id")
        quote = db.get(Quote, quote_id) if quote_id else None
        if quote is None:
            return VerificationResult(False, "Quote not found after revision/cancellation.")
        if intent_type == "INT-20" and quote.status != QuoteStatus.cancelled:
            return VerificationResult(False, f"Expected quote status 'cancelled', found '{quote.status.value}'.")
        if intent_type == "INT-116" and abs(quote.total_price - (result.get("total_price") or 0)) > 0.009:
            return VerificationResult(False, f"Expected quote total {result.get('total_price')}, found {quote.total_price}.")
        return VerificationResult(True, f"Quote {quote.id} confirmed status '{quote.status.value}'.")

    if intent_type == "INT-21":
        order_id = _require(result, "order_id")
        order = db.get(Order, order_id) if order_id else None
        if order is None:
            return VerificationResult(False, "No order found after revision.")
        return VerificationResult(True, f"Order {order.id} revised, monthly_price now {order.monthly_price}.")

    if intent_type == "INT-22":
        proposal_id = _require(result, "proposal_id")
        proposal = db.get(Proposal, proposal_id) if proposal_id else None
        if proposal is None:
            return VerificationResult(False, "No proposal created.")
        return VerificationResult(True, f"Proposal {proposal.id} generated for quote {proposal.quote_id}.")

    if intent_type == "INT-23":
        proposal_id = _require(result, "proposal_id")
        proposal = db.get(Proposal, proposal_id) if proposal_id else None
        if proposal is None:
            return VerificationResult(False, "Proposal not found after recording the customer's decision.")
        return VerificationResult(True, f"Proposal {proposal.id} customer decision recorded: {proposal.status.value}.")

    if intent_type == "INT-24":
        order_id = _require(result, "order_id")
        order = db.get(Order, order_id) if order_id else None
        if order is None:
            return VerificationResult(False, "No order generated from the quote.")
        if not order.items:
            return VerificationResult(False, f"Order {order.id} has no order items.")
        return VerificationResult(True, f"Order {order.id} generated with {len(order.items)} item(s).")

    if intent_type == "INT-25":
        order_id = _require(result, "order_id")
        order = db.get(Order, order_id) if order_id else None
        if order is None:
            return VerificationResult(False, "Order not found after Order Control Unit decision.")
        chain_status = _require(result, "chain_status")
        if chain_status == "approved" and result.get("fulfillment_order"):  # a fulfillment pattern took it: COM -> SOM -> TOM
            from app.models.fulfillment_orders import FulfillmentOrder

            com = db.query(FulfillmentOrder).filter(FulfillmentOrder.crm_order_id == order.id, FulfillmentOrder.layer == "COM").first()
            if com is None:
                return VerificationResult(False, f"Order {order.id} approved but orchestration has no customer order for it.")
            return VerificationResult(True, f"Order {order.id} approved and received by orchestration as {com.number} ({com.status}).")
        if chain_status == "approved" and _require(result, "work_order_id") is None:
            return VerificationResult(False, f"Order {order.id} approved but no installation work order was raised.")
        return VerificationResult(True, f"Order {order.id} Order Control Unit decision recorded: {chain_status}.")

    if intent_type in ("INT-26", "INT-27", "INT-28"):
        closed_work_order_id = _require(result, "closed_work_order_id")
        work_order = db.get(WorkOrder, closed_work_order_id) if closed_work_order_id else None
        if work_order is None:
            return VerificationResult(False, "Closed work order not found after execution.")
        if work_order.status != "complete":
            return VerificationResult(False, f"Expected work order status 'complete', found '{work_order.status}'.")
        return VerificationResult(True, f"Work order {work_order.id} ('{work_order.kind}') confirmed closed.")

    if intent_type == "INT-30":
        new_quote = db.get(Quote, _require(result, "new_quote_id") or 0)
        old_quote = db.get(Quote, _require(result, "quote_id") or 0)
        if new_quote is None or old_quote is None:
            return VerificationResult(False, "Revision or original quote not found after execution.")
        if old_quote.status.value != "superseded" or new_quote.revision_of_quote_id != old_quote.id:
            return VerificationResult(False, f"Quote {old_quote.id} was not superseded by quote {new_quote.id}.")
        return VerificationResult(True, f"Quote {old_quote.id} superseded by revision {new_quote.revision_number} (quote {new_quote.id}).")

    if intent_type in ("INT-29", "INT-31", "INT-32"):
        from app.crm_core import lifecycle_service

        entity_type, entity_id = _require(result, "entity_type"), _require(result, "entity_id")
        obj = db.get(lifecycle_service.ENTITY_MODELS[entity_type], entity_id) if entity_type in lifecycle_service.ENTITY_MODELS else None
        if obj is None:
            return VerificationResult(False, "Record not found after execution.")
        if intent_type == "INT-31" and _require(result, "approval_status") == "pending":
            return VerificationResult(True, f"Approval requested - waiting for {_require(result, 'waiting_for')}.")
        if intent_type == "INT-31":
            state = lifecycle_service.effective_state(db, obj.tenant_id, entity_type, obj)
            if state != _require(result, "to_state"):
                return VerificationResult(False, f"Expected state '{_require(result, 'to_state')}', found '{state}'.")
            return VerificationResult(True, f"{entity_type.title()} {obj.id} confirmed in state '{state}'.")
        if intent_type == "INT-32":
            meta = lifecycle_service.get_meta(db, obj.tenant_id, entity_type, obj.id, create=False)
            if meta is None or (meta.owner_user_id, meta.owner_position_id) != (
                _require(result, "owner_user_id"), _require(result, "owner_position_id")
            ):
                return VerificationResult(False, "Ownership was not recorded as requested.")
            return VerificationResult(True, f"{entity_type.title()} {obj.id} ownership transferred.")
        return VerificationResult(True, f"{entity_type.title()} {obj.id} updated: {', '.join(_require(result, 'updated_fields') or {})}.")

    if intent_type == "INT-33":
        from app.models.c360 import C360Layout

        version = _require(result, "c360_version")
        layout_id = _require(result, "c360_layout_id")
        row = db.get(C360Layout, layout_id) if layout_id else None
        if row is None or row.version != version:
            return VerificationResult(False, "The new Customer 360 layout was not saved.")
        return VerificationResult(True, f"Customer 360 layout version {version} is live.")

    if intent_type == "INT-35":
        party = db.get(Party, _require(result, "party_id") or 0)
        if party is None:
            return VerificationResult(False, "Customer not found after conversion.")
        if _require(result, "approval_status") == "pending":
            return VerificationResult(True, f"{party.name}: account approval requested - waiting for {_require(result, 'waiting_for')}.")
        if party.profile is None or party.profile.account_status != "account":
            return VerificationResult(False, f"{party.name} is still not an account.")
        return VerificationResult(True, f"{party.name} is now an account.")

    if intent_type in ("INT-37", "INT-38"):
        opportunity = db.get(Opportunity, _require(result, "opportunity_id") or 0)
        expected = {"INT-37": OpportunityStage.configured, "INT-38": OpportunityStage.in_feasibility}[intent_type]
        if opportunity is None or opportunity.stage != expected:
            return VerificationResult(False, f"Expected the opportunity to be {expected.value.replace('_', ' ')}.")
        return VerificationResult(True, f"Opportunity {opportunity.id} is {expected.value.replace('_', ' ')}.")

    if intent_type == "INT-39":
        quote = db.get(Quote, _require(result, "quote_id") or 0)
        if quote is None or quote.purchase_order is None:
            return VerificationResult(False, "No PO recorded on the quote.")
        return VerificationResult(True, f"PO {quote.purchase_order.po_number} attached to quote {quote.id}.")

    if intent_type == "INT-40":
        quote = db.get(Quote, _require(result, "quote_id") or 0)
        order = db.get(Order, _require(result, "order_id") or 0)
        if quote is None or quote.purchase_order is None or quote.purchase_order.matched_at is None:
            return VerificationResult(False, "The quote was not matched.")
        if order is None or not order.items:
            return VerificationResult(False, "No order was created from the matched quote.")
        return VerificationResult(True, f"Quote {quote.id} matched; order {order.id} created.")

    if intent_type == "INT-41":
        party = db.get(Party, _require(result, "party_id") or 0)
        if party is None:
            return VerificationResult(False, "No customer created.")
        return VerificationResult(True, f"Consumer {party.name} added.")

    if intent_type == "INT-42":
        changes = _require(result, "changes") or []
        if not changes:
            return VerificationResult(False, "No attribute was changed.")
        return VerificationResult(True, f"{_require(result, 'offering_name')}: {'; '.join(changes)}.")

    if intent_type == "INT-43":
        from app.models.process import PriceList, PriceListItem

        row = (db.query(PriceListItem).join(PriceList, PriceList.id == PriceListItem.price_list_id)
               .filter(PriceList.name == _require(result, "price_list"), PriceListItem.offering_name == _require(result, "offering_name")).first())
        if row is None or row.price != _require(result, "price"):
            return VerificationResult(False, "The pricelist price was not recorded.")
        return VerificationResult(True, f"{row.offering_name} costs {row.price:,.2f} on {_require(result, 'price_list')}.")

    if intent_type == "INT-44":
        if not _require(result, "price_list"):
            return VerificationResult(False, "No pricelist applied.")
        return VerificationResult(True, f"{_require(result, 'entity_type')} {_require(result, 'entity_id')} now priced from {_require(result, 'price_list')}.")

    if intent_type == "INT-45":
        if not _require(result, "technical_attributes"):
            return VerificationResult(False, "No technical attributes recorded.")
        return VerificationResult(True, f"Order {_require(result, 'order_id')} technical attributes recorded.")

    if intent_type == "INT-47":
        if not _require(result, "configuration"):
            return VerificationResult(False, "No configuration was produced.")
        return VerificationResult(True, f"Configured at {_require(result, 'monthly_price')} a month.")

    if intent_type == "INT-48":
        if not _require(result, "model_version"):
            return VerificationResult(False, "No model version was released.")
        return VerificationResult(True, f"{_require(result, 'offering_name')} model v{_require(result, 'model_version')} released.")

    if intent_type == "INT-49":
        return VerificationResult(True, f"{len(_require(result, 'eligible') or [])} product(s) can be sold to this customer.")

    if intent_type == "INT-50":
        if _require(result, "status") != "changed":
            return VerificationResult(False, "The service was not changed.")
        return VerificationResult(True, f"Order {_require(result, 'order_id')} reconfigured; now {_require(result, 'new_monthly_price')} a month.")

    if intent_type == "INT-46":
        lead =db.get(Lead, _require(result, "lead_id") or 0)
        if lead is None:
            return VerificationResult(False, "No lead was created.")
        wanted = ", ".join(p["offering_name"] for p in _require(result, "interested_products") or [])
        return VerificationResult(True, f"Lead {lead.id} opened for {lead.party.name}" + (f", interested in {wanted}." if wanted else "."))

    if intent_type == "INT-36":
        if _require(result, "chain_status") is None and _require(result, "status") is None and _require(result, "order_status") is None:
            return VerificationResult(False, "No decision was recorded.")
        return VerificationResult(True, f"Decision recorded: {_require(result, 'decision')}.")

    if intent_type == "INT-34":
        valid = all(isinstance(result.get(key), int) and result[key] >= 0 for key in ("created", "updated", "deactivated"))
        return VerificationResult(valid, "Inventory check type sync completed." if valid else "Missing sync result counts.")

    return VerificationResult(True, "No verification rule defined for this intent type; passing through.")

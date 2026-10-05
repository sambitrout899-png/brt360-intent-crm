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
from app.models.crm import Opportunity, Quote, QuoteStatus
from app.models.orders import Order, OrderStatus


@dataclass
class VerificationResult:
    ok: bool
    detail: str


def _require(result: dict[str, Any] | None, key: str) -> Any | None:
    return result.get(key) if result else None


def verify(db: Session, intent_type: str, result: dict[str, Any] | None) -> VerificationResult:
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
        opportunity_id = _require(result, "opportunity_id")
        opportunity = db.get(Opportunity, opportunity_id) if opportunity_id else None
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
        if instance is None or not any(s.status.value != "pending" for s in instance.steps):
            return VerificationResult(False, "No approval chain decision was recorded for this quote.")
        return VerificationResult(True, f"Quote {quote.id} chain status: {quote.status.value} (chain {instance.status.value}).")

    if intent_type in ("INT-07", "INT-08", "INT-09"):
        order_id = _require(result, "order_id")
        order = db.get(Order, order_id) if order_id else None
        if order is None:
            return VerificationResult(False, "No order found after execution.")
        if intent_type == "INT-07" and order.status != OrderStatus.active:
            return VerificationResult(False, f"Expected order status 'active', found '{order.status.value}'.")
        return VerificationResult(True, f"Order {order.id} confirmed status '{order.status.value}'.")

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

    if intent_type == "INT-12":
        work_order_id = _require(result, "work_order_id")
        wo = db.get(WorkOrder, work_order_id) if work_order_id else None
        if wo is None:
            return VerificationResult(False, "No work order found after execution.")
        return VerificationResult(True, f"Work order {wo.id} confirmed status '{wo.status}'.")

    return VerificationResult(True, "No verification rule defined for this intent type; passing through.")

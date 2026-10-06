"""Reasoning & Planning Engine (HLD 4.3).

Per the implementation plan's design decision: the orchestration *steps* for
INT-01/02/03 are deterministic (see orchestration/actions.py), taken directly
from HLD Section 7. This module's job is the part that genuinely benefits
from reasoning: deciding whether the classified intent has enough to safely
auto-execute, and producing the confidence + rationale that AI Governance
(HLD 8.5) requires to accompany every action.

Plans at or above auto_execute_confidence_threshold (app.config) with no
missing required fields execute automatically; everything else is returned
with status "needs_review" for a human to approve before the Orchestrator
is invoked.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.config import get_settings


@dataclass
class ReasoningResult:
    confidence: float
    rationale: str
    auto_execute: bool
    missing_fields: list[str]


_REQUIRED_FIELDS: dict[str, list[str]] = {
    "INT-01": ["name", "composition", "list_price"],
    "INT-02": ["offering_name"],
    "INT-03": ["offering_name", "replacement_offering_name"],
    "INT-04": ["company_name", "source"],
    "INT-05": ["opportunity_id", "offering_name"],
    "INT-06": ["quote_id", "decision"],
    "INT-07": ["quote_id"],
    "INT-08": ["order_id"],
    "INT-09": ["order_id", "action"],
    "INT-10": ["description"],
    "INT-11": ["ticket_id"],
    "INT-13": ["party_id", "id_number"],
    "INT-14": ["order_id", "site_label"],
    "INT-15": ["name"],
    "INT-16": ["lead_id", "offering_name"],
    "INT-17": ["lead_id", "decision"],
    "INT-18": ["opportunity_id"],
    "INT-19": ["quote_id"],
    "INT-20": ["quote_id"],
    "INT-21": ["order_id"],
    "INT-22": ["quote_id"],
    "INT-23": ["proposal_id", "decision"],
    "INT-24": ["quote_id"],
    "INT-25": ["order_id", "decision"],
    "INT-26": ["work_order_id"],
    "INT-27": ["work_order_id"],
    "INT-28": ["work_order_id"],
}


def _missing_fields(intent_type: str, entities: dict[str, Any]) -> list[str]:
    required = _REQUIRED_FIELDS.get(intent_type, [])
    missing = []
    for field_name in required:
        value = entities.get(field_name)
        if value is None or value == "" or value == []:
            missing.append(field_name)
    if intent_type == "INT-12" and not (entities.get("ticket_id") or entities.get("order_id") or entities.get("work_order_id")):
        missing.append("ticket_id or order_id or work_order_id")
    return missing


def _context_missing(intent_type: str, context: dict[str, Any], entities: dict[str, Any]) -> list[str]:
    missing = []
    if intent_type in ("INT-02", "INT-03") and context.get("current_offering") is None:
        missing.append("current_offering (not found or not active)")
    if intent_type == "INT-03" and context.get("replacement_offering") is None:
        missing.append("replacement_offering (not found or not active)")
    if intent_type == "INT-05":
        if context.get("opportunity") is None:
            missing.append("opportunity (not found)")
        if context.get("offering") is None:
            missing.append("offering (not found or not active)")
    if intent_type in ("INT-06", "INT-07") and context.get("quote") is None:
        missing.append("quote (not found)")
    if intent_type == "INT-07" and context.get("quote") is not None and context["quote"]["status"] != "approved":
        missing.append(f"quote is not approved (status={context['quote']['status']})")
    if intent_type in ("INT-08", "INT-09"):
        if context.get("order") is None:
            missing.append("order (not found)")
        elif context["order"]["status"] in ("cancelled", "terminated"):
            missing.append(f"order already {context['order']['status']}")
    if intent_type == "INT-11" and context.get("ticket") is None:
        missing.append("ticket (not found)")
    if intent_type == "INT-13":
        if context.get("party") is None:
            missing.append("party (not found)")
        if context.get("vertical_blocked"):
            missing.append(context["vertical_blocked"])
    if intent_type == "INT-14":
        if context.get("order") is None:
            missing.append("order (not found)")
        elif entities.get("site_label") not in context.get("order_site_labels", []):
            missing.append(f"site_label '{entities.get('site_label')}' not found under this order")
        if context.get("vertical_blocked"):
            missing.append(context["vertical_blocked"])
    if intent_type == "INT-12":
        if context.get("work_order") is None and context.get("ticket") is None and context.get("order") is None:
            missing.append("none of ticket/order/work_order resolved")
    if intent_type in ("INT-19", "INT-20"):
        if context.get("quote") is None:
            missing.append("quote (not found)")
        elif context.get("ponr_reached"):
            missing.append(f"quote has passed its PONR (status={context['quote']['status']})")
    if intent_type == "INT-21":
        if context.get("order") is None:
            missing.append("order (not found)")
        elif context.get("ponr_reached"):
            missing.append(f"order has passed its PONR (status={context['order']['status']})")
    if intent_type == "INT-16" and context.get("lead") is None:
        missing.append("lead (not found)")
    if intent_type == "INT-16" and context.get("offering") is None:
        missing.append("offering (not found or not active)")
    if intent_type == "INT-17" and context.get("lead") is None:
        missing.append("lead (not found)")
    if intent_type == "INT-18" and context.get("opportunity") is None:
        missing.append("opportunity (not found)")
    if intent_type == "INT-22":
        if context.get("quote") is None:
            missing.append("quote (not found)")
        elif context["quote"]["status"] != "approved":
            missing.append(f"quote is not approved (status={context['quote']['status']}) - cannot propose it")
    if intent_type == "INT-23" and context.get("proposal") is None:
        missing.append("proposal (not found)")
    if intent_type == "INT-24":
        if context.get("quote") is None:
            missing.append("quote (not found)")
        elif context["quote"]["status"] != "approved":
            missing.append(f"quote is not approved (status={context['quote']['status']})")
        elif context.get("customer_approved_proposal") is None:
            missing.append("quote has no customer-approved proposal on file (see INT-22/23)")
    if intent_type == "INT-25":
        if context.get("order") is None:
            missing.append("order (not found)")
        elif context["order"]["status"] != "created":
            missing.append(f"order has already been submitted for fulfillment (status={context['order']['status']})")
    if intent_type in ("INT-26", "INT-27", "INT-28") and context.get("work_order") is None:
        missing.append("work order (not found)")
    return missing


def plan(
    intent_type: str | None,
    entities: dict[str, Any],
    context: dict[str, Any],
    classifier_confidence: float,
    classifier_rationale: str,
) -> ReasoningResult:
    settings = get_settings()

    if intent_type is None:
        return ReasoningResult(0.0, "Could not resolve a known intent from the input.", False, ["intent_type"])

    missing = _missing_fields(intent_type, entities) + _context_missing(intent_type, context, entities)
    confidence = classifier_confidence
    if missing:
        confidence = min(confidence, 0.5)

    auto_execute = confidence >= settings.auto_execute_confidence_threshold and not missing

    rationale_parts = [classifier_rationale]
    if missing:
        rationale_parts.append(f"Missing/unresolved: {', '.join(missing)} - routed to human review.")
    elif auto_execute:
        rationale_parts.append(
            f"Confidence {confidence:.2f} meets the auto-execute threshold "
            f"({settings.auto_execute_confidence_threshold}); executing the fixed {intent_type} step sequence."
        )
    else:
        rationale_parts.append(
            f"Confidence {confidence:.2f} below the auto-execute threshold "
            f"({settings.auto_execute_confidence_threshold}); routed to human review."
        )

    return ReasoningResult(confidence, " ".join(rationale_parts), auto_execute, missing)

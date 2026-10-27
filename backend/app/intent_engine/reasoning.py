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
from app.intent_engine import abo_intents, b2c_intents, comm_intents, config_intents, om_intents, fulfillment_intents, inventory_intents, quote_intents, sto_intents


@dataclass
class ReasoningResult:
    confidence: float
    rationale: str
    auto_execute: bool
    missing_fields: list[str]


_REQUIRED_FIELDS: dict[str, list[str]] = {
    "INT-34": [],
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
    "INT-29": ["entity_type", "entity_id", "fields"],
    "INT-30": ["quote_id"],
    "INT-31": ["entity_type", "entity_id"],
    "INT-32": ["entity_type", "entity_id"],
    "INT-33": ["instruction"],
    "INT-35": ["party_id"],
    "INT-36": ["entity_type", "entity_id", "decision"],
    "INT-37": ["opportunity_id"],
    "INT-38": ["opportunity_id"],
    "INT-39": ["quote_id", "po_number"],
    "INT-40": ["quote_id"],
    "INT-41": ["name"],
    "INT-42": ["offering_name", "design"],
    "INT-43": ["price_list", "offering_name", "price"],
    "INT-44": ["entity_type", "entity_id", "price_list"],
    "INT-45": ["order_id", "technical_attributes"],
    "INT-46": ["party_id"],
    "INT-47": ["opportunity_id", "need"],
    "INT-48": ["offering_name"],
    "INT-49": ["party_id"],
    "INT-50": ["order_id", "pricing_attributes"],
}
_REQUIRED_FIELDS.update(om_intents.REQUIRED)
_REQUIRED_FIELDS.update(comm_intents.REQUIRED)
_REQUIRED_FIELDS.update(config_intents.REQUIRED)
_REQUIRED_FIELDS.update(b2c_intents.REQUIRED)
_REQUIRED_FIELDS.update(sto_intents.REQUIRED)
_REQUIRED_FIELDS.update(abo_intents.REQUIRED)
_REQUIRED_FIELDS.update(quote_intents.REQUIRED)
_REQUIRED_FIELDS.update(fulfillment_intents.REQUIRED)
_REQUIRED_FIELDS.update(inventory_intents.REQUIRED)


def _consumer_order(intent_type: str, entities: dict[str, Any]) -> bool:
    """INT-07 placing a consumer order (customer + product, no quote)."""
    return intent_type == "INT-07" and not entities.get("quote_id") and bool(entities.get("party_id") and entities.get("offering_name"))


def required_fields_for(intent_type: str) -> list[str]:
    """Public accessor for the Expression Builder's live field checklist
    (POST /api/intents/assist) - the same required-field list auto-
    execution gates on, just surfaced before submission instead of after.
    """
    return list(_REQUIRED_FIELDS.get(intent_type, []))


def _missing_fields(intent_type: str, entities: dict[str, Any], required: list[str] | None = None) -> list[str]:
    required = _REQUIRED_FIELDS.get(intent_type, []) if required is None else required
    missing = []
    for field_name in required:
        value = entities.get(field_name)
        if (value is None or value == "" or value == []) and not (field_name == "quote_id" and _consumer_order(intent_type, entities)) \
                and not (intent_type == "INT-42" and field_name == "design" and entities.get("instruction")):
            missing.append(field_name)
    if intent_type == "INT-12" and not (entities.get("ticket_id") or entities.get("order_id") or entities.get("work_order_id")):
        missing.append("ticket_id or order_id or work_order_id")
    if intent_type == "INT-31" and not (entities.get("action") or entities.get("to_state")):
        missing.append("action or to_state")
    if intent_type in om_intents.CODES:
        missing.extend(om_intents.missing(intent_type, entities))
    if intent_type in comm_intents.CODES:
        missing.extend(comm_intents.missing(intent_type, entities))
    if intent_type in config_intents.CODES:
        missing.extend(config_intents.missing(intent_type, entities))
    if intent_type in b2c_intents.CODES:
        missing.extend(b2c_intents.missing(intent_type, entities))
    if intent_type in abo_intents.CODES:
        missing.extend(abo_intents.missing(intent_type, entities))
    if intent_type in quote_intents.CODES:
        missing.extend(quote_intents.missing(intent_type, entities))
    if intent_type == "INT-32" and not (entities.get("to_user") or entities.get("to_position") or entities.get("to_department")):
        missing.append("to_user or to_position or to_department")
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
    if intent_type in ("INT-06", "INT-07") and context.get("quote") is None and not _consumer_order(intent_type, entities):
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
        elif context.get("customer_approved_proposal") is None and not context.get("po_matched"):
            missing.append("quote has no customer-approved proposal on file - send the proposal and record the customer's answer, or attach and match their PO")
    if intent_type == "INT-25":
        if context.get("order") is None:
            missing.append("order (not found)")
        elif context["order"]["status"] != "created":
            missing.append(f"order has already been submitted for fulfillment (status={context['order']['status']})")
    if intent_type in ("INT-26", "INT-27", "INT-28") and context.get("work_order") is None:
        missing.append("work order (not found)")
    if intent_type in ("INT-29", "INT-31", "INT-32") and context.get("record") is None:
        missing.append(f"{entities.get('entity_type') or 'record'} {entities.get('entity_id')} (not found)")
    if intent_type == "INT-29" and context.get("record") is not None and not context.get("editable"):
        missing.append(
            f"record is locked in state '{context['record']['state']}' - make a new revision or move it to another stage instead"
        )
    if intent_type == "INT-31" and context.get("record") is not None and context.get("transition_error"):
        missing.append(context["transition_error"])
    if intent_type == "INT-32":
        if context.get("unknown_user"):
            missing.append(f"user '{context['unknown_user']}' (not found)")
        if context.get("unknown_position"):
            missing.append(f"position '{context['unknown_position']}' (not found)")
    if intent_type == "INT-35" and context.get("party") is None:
        missing.append("customer (not found)")
    if intent_type in ("INT-37", "INT-38") and context.get("opportunity") is None:
        missing.append("opportunity (not found)")
    if intent_type in ("INT-39", "INT-40", "INT-109") and context.get("quote") is None:
        missing.append("quote (not found)")
    if intent_type == "INT-109" and context.get("quote") is not None and context["quote"]["status"] != "approved":
        missing.append(f"quote is not approved (status={context['quote']['status']}) - the proposal comes once its discount is approved")
    if intent_type == "INT-36":
        if context.get("record") is None:
            missing.append(f"{entities.get('entity_type') or 'record'} {entities.get('entity_id')} (not found)")
        elif not context.get("approval_pending"):
            missing.append("this record is not waiting for any approval")
    if intent_type in ("INT-42", "INT-43") and context.get("offering") is None:
        missing.append("product (not found, or retired)")
    if intent_type in ("INT-43", "INT-44") and context.get("price_list") is None:
        missing.append(f"pricelist '{entities.get('price_list')}' (not found)")
    if intent_type == "INT-44" and context.get("record") is None:
        missing.append(f"{entities.get('entity_type') or 'record'} {entities.get('entity_id')} (not found)")
    if intent_type == "INT-45" and context.get("order") is None:
        missing.append("order (not found)")
    if intent_type == "INT-30":
        if context.get("quote") is None:
            missing.append("quote (not found)")
        elif context.get("quote_editable"):
            missing.append(f"quote is still editable (status={context['quote']['status']}) - revise it in place instead")
    return missing


def plan(
    intent_type: str | None,
    entities: dict[str, Any],
    context: dict[str, Any],
    classifier_confidence: float,
    classifier_rationale: str,
    required_fields: list[str] | None = None,
) -> ReasoningResult:
    """required_fields overrides the built-in table - used for tenant-
    defined FLOW-<id> intents, whose requirements live on the flow."""
    settings = get_settings()

    if intent_type is None:
        return ReasoningResult(0.0, "Could not resolve a known intent from the input.", False, ["intent_type"])

    missing = _missing_fields(intent_type, entities, required_fields) + _context_missing(intent_type, context, entities)
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

"""Per-intent orchestration step sequences for INT-04/05/06 (Sprint 2,
Lead-to-Quote) - directly from HLD Section 7's "Orchestration Steps" -
plus Phase 4's B2B sales-journey extensions: a Sales Director approval
gate between Lead and Opportunity (INT-17), product interest capture at
the lead stage (INT-16), and quote revision/cancellation respecting a
configurable PONR (INT-19/INT-20).
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.crm_core import approval_service, catalog_service, lead_service, ponr_service
from app.data_intelligence import event_bus
from app.integration import party_adapter, tmf620_adapter
from app.models.catalog import ProductOffering
from app.models.crm import FeasibilityResult, Lead, LeadStatus, Opportunity, OpportunityStage, Quote, QuoteItem, QuoteStatus
from app.orchestration.orchestrator import SagaStep


def build_int04_steps(db: Session, tenant_id: int, execution_id: int, entities: dict[str, Any]) -> list[SagaStep]:
    """INT-04 - Qualify this lead.

    Creates the Lead (and, against an existing prospect Party of the same
    name - see INT-15 - reuses it rather than duplicating). Whether this
    also immediately creates the Opportunity depends on whether the
    tenant has configured a lead-qualification approval chain
    (approval_service.resolve_lead_qualification_template): no chain
    configured keeps the original immediate-qualification behavior
    unchanged; a chain configured means the lead instead awaits a Sales
    Director's decision via INT-17 ("Approve this lead").
    """

    company_name = entities["company_name"]
    source = entities.get("source", "unknown")
    region = entities.get("region", "ALL")
    interested_offering_name = entities.get("offering_name")

    state: dict[str, Any] = {}

    def step1() -> dict[str, Any]:
        enrichment = lead_service.enrich_lead(company_name)
        state["enrichment"] = enrichment
        return enrichment

    def step2() -> dict[str, Any]:
        score = lead_service.score_lead(state["enrichment"])
        state["score"] = score
        return {"propensity_score": score}

    def step3() -> dict[str, Any]:
        owner = lead_service.assign_territory_owner(db, tenant_id, region)
        state["owner"] = owner
        return {"owner_name": owner}

    def step4() -> dict[str, Any]:
        party = party_adapter.get_or_create_party(db, tenant_id, company_name)
        lead = Lead(
            tenant_id=tenant_id,
            party_id=party.id,
            source=source,
            region=region,
            owner_name=state["owner"],
            propensity_score=state["score"],
            interested_offering_name=interested_offering_name,
        )
        db.add(lead)
        db.flush()

        gate_template = approval_service.resolve_lead_qualification_template(db, tenant_id)
        if gate_template is None:
            lead.status = LeadStatus.qualified
            opportunity = Opportunity(
                tenant_id=tenant_id, lead_id=lead.id, name=f"{company_name} - initial opportunity", stage=OpportunityStage.draft
            )
            db.add(opportunity)
            db.flush()
            event_bus.publish(db, execution_id, "lead.qualified", {"lead_id": lead.id, "opportunity_id": opportunity.id})
            return {"lead_id": lead.id, "opportunity_id": opportunity.id, "party_id": party.id, "status": lead.status.value}

        event_bus.publish(db, execution_id, "lead.pending_qualification", {"lead_id": lead.id})
        return {"lead_id": lead.id, "party_id": party.id, "status": lead.status.value}

    return [
        SagaStep("enrich_lead", step1),
        SagaStep("score_lead", step2),
        SagaStep("assign_territory_owner", step3),
        SagaStep("create_lead", step4),
    ]


def build_int16_steps(db: Session, tenant_id: int, execution_id: int, entities: dict[str, Any]) -> list[SagaStep]:
    """INT-16 - Add a product to this lead."""

    lead_id = entities["lead_id"]
    offering_name = entities["offering_name"]

    def step1() -> dict[str, Any]:
        lead = db.get(Lead, lead_id)
        if lead is None:
            raise ValueError(f"Lead {lead_id} not found")
        offering = tmf620_adapter.get_active_offering_by_name(db, tenant_id, offering_name)
        if offering is None:
            raise ValueError(f"No active offering named '{offering_name}' found")
        lead.interested_offering_name = offering_name
        db.flush()
        event_bus.publish(db, execution_id, "lead.product_interest_added", {"lead_id": lead.id, "offering_name": offering_name})
        return {"lead_id": lead.id, "offering_name": offering_name}

    return [SagaStep("add_product_to_lead", step1)]


def build_int17_steps(db: Session, tenant_id: int, execution_id: int, entities: dict[str, Any]) -> list[SagaStep]:
    """INT-17 - Approve this lead (Sales Director qualification gate).

    Only reachable for a lead whose tenant has a "lead" approval chain
    configured (see INT-04) - otherwise the lead was already qualified
    and its Opportunity already created in the same call.
    """

    lead_id = entities["lead_id"]
    decision = entities.get("decision", "approve")
    reasoning_text = entities.get("reasoning", "")

    state: dict[str, Any] = {}

    def step1() -> dict[str, Any]:
        lead = db.get(Lead, lead_id)
        if lead is None:
            raise ValueError(f"Lead {lead_id} not found")
        state["lead"] = lead
        return {"lead_id": lead.id, "status": lead.status.value}

    def step2() -> dict[str, Any]:
        lead = state["lead"]
        instance = approval_service.get_chain_instance(db, "lead", lead.id)
        if instance is None:
            template = approval_service.resolve_lead_qualification_template(db, tenant_id)
            if template is None:
                raise ValueError("No lead-qualification approval chain is configured for this tenant")
            instance = approval_service.instantiate_chain(db, "lead", lead.id, template)
        state["instance"] = instance
        return {"chain_template": instance.template.name, "current_tier": instance.steps[instance.current_sequence - 1].approver_tier}

    def step3() -> dict[str, Any]:
        outcome = approval_service.record_step_decision(db, state["instance"], decision, reasoning_text or "")
        state["outcome"] = outcome
        return outcome

    def step4() -> dict[str, Any]:
        lead = state["lead"]
        outcome = state["outcome"]
        if outcome["chain_status"] == "approved":
            lead.status = LeadStatus.qualified
            opportunity = Opportunity(
                tenant_id=tenant_id, lead_id=lead.id, name=f"{lead.party.name} - initial opportunity", stage=OpportunityStage.draft
            )
            db.add(opportunity)
            db.flush()
            event_bus.publish(db, execution_id, "lead.qualified", {"lead_id": lead.id, "opportunity_id": opportunity.id})
            return {"lead_id": lead.id, "opportunity_id": opportunity.id, "status": lead.status.value}
        elif outcome["chain_status"] == "rejected":
            lead.status = LeadStatus.rejected
            db.flush()
            event_bus.publish(db, execution_id, "lead.rejected", {"lead_id": lead.id})
            return {"lead_id": lead.id, "status": lead.status.value}
        return {"lead_id": lead.id, "status": lead.status.value}

    return [
        SagaStep("resolve_lead", step1),
        SagaStep("load_or_instantiate_chain", step2),
        SagaStep("capture_decision", step3),
        SagaStep("finalize_lead_status", step4),
    ]


def build_int05_steps(db: Session, tenant_id: int, execution_id: int, entities: dict[str, Any]) -> list[SagaStep]:
    """INT-05 - Configure & quote this bundle."""

    opportunity_id = entities["opportunity_id"]
    offering_name = entities["offering_name"]
    quantity = int(entities.get("quantity", 1))
    requested_discount_pct = float(entities.get("requested_discount_pct", 0))
    justification = entities.get("discount_justification")

    state: dict[str, Any] = {}

    def step1() -> dict[str, Any]:
        offering = tmf620_adapter.get_active_offering_by_name(db, tenant_id, offering_name)
        if offering is None:
            raise ValueError(f"No active offering named '{offering_name}' found")
        state["offering"] = offering
        return {"offering_id": offering.id, "list_price": offering.price}

    def step2() -> dict[str, Any]:
        offering = state["offering"]
        ceiling = offering.price_plan.discount_ceiling_pct if offering.price_plan else 20.0
        unit_price = catalog_service.compute_price(offering.price, requested_discount_pct, ceiling, offering.price_floor)
        state["unit_price"] = unit_price
        return {"unit_price": unit_price, "discount_applied_pct": min(requested_discount_pct, ceiling)}

    def step3() -> dict[str, Any]:
        rep_authority_pct = approval_service.get_rep_authority_ceiling_pct(db, tenant_id, state["offering"].segment)
        if requested_discount_pct > rep_authority_pct and not justification:
            raise ValueError(
                f"Discount {requested_discount_pct}% exceeds rep authority ({rep_authority_pct}%) and requires a justification"
            )
        return {"discount_justification": justification}

    def step4() -> dict[str, Any]:
        total_price = state["unit_price"] * quantity
        quote = Quote(
            tenant_id=tenant_id,
            opportunity_id=opportunity_id,
            status=QuoteStatus.draft,
            total_price=total_price,
            requested_discount_pct=requested_discount_pct,
            discount_justification=justification,
        )
        db.add(quote)
        db.flush()
        db.add(QuoteItem(quote_id=quote.id, product_offering_id=state["offering"].id, quantity=quantity, unit_price=state["unit_price"]))
        opportunity = db.get(Opportunity, opportunity_id)
        opportunity.stage = OpportunityStage.quoted
        db.flush()
        return {"quote_id": quote.id, "total_price": total_price, "status": quote.status.value}

    return [
        SagaStep("configure_product", step1),
        SagaStep("compute_price", step2),
        SagaStep("capture_discount_justification", step3),
        SagaStep("generate_quote", step4),
    ]


def build_int06_steps(db: Session, tenant_id: int, execution_id: int, entities: dict[str, Any]) -> list[SagaStep]:
    """INT-06 - Check feasibility & approve.

    A quote's chain can take more than one "Approve this quote" call to
    resolve (one per tier). The first call instantiates the chain from the
    tenant's configured templates (approval_service.resolve_template) and
    decides tier 1; each subsequent call decides whichever tier is next,
    reusing the same ApprovalChainInstance rather than re-resolving it, so
    re-matching never contradicts a chain already in progress.
    """

    quote_id = entities["quote_id"]
    decision = entities.get("decision", "approve")
    reasoning_text = entities.get("reasoning", "")

    state: dict[str, Any] = {}

    def step1() -> dict[str, Any]:
        quote = db.get(Quote, quote_id)
        if quote is None:
            raise ValueError(f"Quote {quote_id} not found")
        state["quote"] = quote
        # Stand-in network feasibility check (HLD: "fiber route, capacity via OSS") -
        # deterministic so this is testable without a live OSS. Only run once per
        # quote - FeasibilityResult has a unique quote_id, and a multi-tier chain
        # calls this step more than once.
        if quote.feasibility_result is not None:
            state["feasible"] = quote.feasibility_result.feasible
        else:
            feasible = "unavailable" not in (quote.discount_justification or "").lower()
            db.add(FeasibilityResult(quote_id=quote.id, feasible=feasible, details="Stub OSS feasibility check"))
            db.flush()
            state["feasible"] = feasible
        return {"feasible": state["feasible"]}

    def step2() -> dict[str, Any]:
        quote = state["quote"]
        instance = approval_service.get_chain_instance(db, "quote", quote.id)
        if instance is None:
            item = quote.items[0] if quote.items else None
            offering = db.get(ProductOffering, item.product_offering_id) if item else None
            segment = offering.segment if offering else None
            template = approval_service.resolve_template(db, tenant_id, quote.requested_discount_pct, segment)
            if template is None:
                raise ValueError("No approval chain is configured for this discount band - an admin must add one")
            instance = approval_service.instantiate_chain(db, "quote", quote.id, template)
            quote.status = QuoteStatus.pending_approval
            db.flush()
        state["instance"] = instance
        return {"chain_template": instance.template.name, "current_tier": instance.steps[instance.current_sequence - 1].approver_tier}

    def step3() -> dict[str, Any]:
        if not state["feasible"] and decision == "approve":
            raise ValueError("Cannot approve a quote that failed the feasibility check")
        outcome = approval_service.record_step_decision(db, state["instance"], decision, reasoning_text or "")
        state["outcome"] = outcome
        return outcome

    def step4() -> dict[str, Any]:
        quote = state["quote"]
        outcome = state["outcome"]
        if outcome["chain_status"] == "approved":
            quote.status = QuoteStatus.approved
        elif outcome["chain_status"] == "rejected":
            quote.status = QuoteStatus.rejected
        db.flush()
        if quote.status in (QuoteStatus.approved, QuoteStatus.rejected):
            event_bus.publish(
                db, execution_id, "quote.approved" if quote.status == QuoteStatus.approved else "quote.rejected", {"quote_id": quote.id}
            )
        return {"quote_id": quote.id, "status": quote.status.value, "decision": decision}

    return [
        SagaStep("run_feasibility_check", step1),
        SagaStep("resolve_approval_chain", step2),
        SagaStep("capture_decision", step3),
        SagaStep("finalize_quote_status", step4),
    ]


def build_int19_steps(db: Session, tenant_id: int, execution_id: int, entities: dict[str, Any]) -> list[SagaStep]:
    """INT-19 - Revise this quote. Blocked once the quote has reached its
    configured PONR (default: once approved) - cancel and requote instead.
    """

    quote_id = entities["quote_id"]
    new_discount_pct = entities.get("requested_discount_pct")
    new_quantity = entities.get("quantity")

    state: dict[str, Any] = {}

    def step1() -> dict[str, Any]:
        quote = db.get(Quote, quote_id)
        if quote is None:
            raise ValueError(f"Quote {quote_id} not found")
        if ponr_service.has_reached_ponr(db, tenant_id, "quote", quote.status.value):
            raise ValueError(f"Quote {quote.id} has passed its PONR (status={quote.status.value}) - cannot be revised")
        state["quote"] = quote
        return {"quote_id": quote.id, "status": quote.status.value}

    def step2() -> dict[str, Any]:
        quote = state["quote"]
        item = quote.items[0] if quote.items else None
        if new_discount_pct is not None:
            quote.requested_discount_pct = float(new_discount_pct)
        if item is not None and new_quantity is not None:
            item.quantity = int(new_quantity)
        if item is not None:
            quote.total_price = round(item.unit_price * item.quantity * (1 - quote.requested_discount_pct / 100), 2)
        db.flush()
        event_bus.publish(db, execution_id, "quote.revised", {"quote_id": quote.id, "total_price": quote.total_price})
        return {"quote_id": quote.id, "total_price": quote.total_price, "requested_discount_pct": quote.requested_discount_pct}

    return [
        SagaStep("check_ponr", step1),
        SagaStep("apply_revision", step2),
    ]


def build_int20_steps(db: Session, tenant_id: int, execution_id: int, entities: dict[str, Any]) -> list[SagaStep]:
    """INT-20 - Cancel this quote (withdrawn by the requester, distinct
    from a reject decision by an approver). Same PONR gate as INT-19.
    """

    quote_id = entities["quote_id"]
    reasoning_text = entities.get("reasoning", "")

    def step1() -> dict[str, Any]:
        quote = db.get(Quote, quote_id)
        if quote is None:
            raise ValueError(f"Quote {quote_id} not found")
        if ponr_service.has_reached_ponr(db, tenant_id, "quote", quote.status.value):
            raise ValueError(f"Quote {quote.id} has passed its PONR (status={quote.status.value}) - cannot be cancelled")
        quote.status = QuoteStatus.cancelled
        db.flush()
        event_bus.publish(db, execution_id, "quote.cancelled", {"quote_id": quote.id, "reasoning": reasoning_text})
        return {"quote_id": quote.id, "status": quote.status.value}

    return [SagaStep("cancel_quote", step1)]

"""Per-intent orchestration step sequences for INT-04/05/06 (Sprint 2,
Lead-to-Quote) - directly from HLD Section 7's "Orchestration Steps".
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.crm_core import approval_service, catalog_service, lead_service
from app.data_intelligence import event_bus
from app.integration import party_adapter, tmf620_adapter
from app.models.approval import ApprovalChainInstance
from app.models.catalog import ProductOffering
from app.models.crm import FeasibilityResult, Opportunity, OpportunityStage, Quote, QuoteItem, QuoteStatus
from app.orchestration.orchestrator import SagaStep


def build_int04_steps(db: Session, tenant_id: int, execution_id: int, entities: dict[str, Any]) -> list[SagaStep]:
    """INT-04 - Qualify this lead."""

    company_name = entities["company_name"]
    source = entities.get("source", "unknown")
    region = entities.get("region", "ALL")

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
        from app.models.crm import Lead

        lead = Lead(
            tenant_id=tenant_id,
            party_id=party.id,
            source=source,
            region=region,
            owner_name=state["owner"],
            propensity_score=state["score"],
        )
        db.add(lead)
        db.flush()
        opportunity = Opportunity(
            tenant_id=tenant_id, lead_id=lead.id, name=f"{company_name} - initial opportunity", stage=OpportunityStage.draft
        )
        db.add(opportunity)
        db.flush()
        event_bus.publish(db, execution_id, "lead.qualified", {"lead_id": lead.id, "opportunity_id": opportunity.id})
        return {"lead_id": lead.id, "opportunity_id": opportunity.id, "party_id": party.id}

    return [
        SagaStep("enrich_lead", step1),
        SagaStep("score_lead", step2),
        SagaStep("assign_territory_owner", step3),
        SagaStep("create_lead_and_opportunity", step4),
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
        instance = db.query(ApprovalChainInstance).filter(ApprovalChainInstance.quote_id == quote.id).first()
        if instance is None:
            item = quote.items[0] if quote.items else None
            offering = db.get(ProductOffering, item.product_offering_id) if item else None
            segment = offering.segment if offering else None
            template = approval_service.resolve_template(db, tenant_id, quote.requested_discount_pct, segment)
            if template is None:
                raise ValueError("No approval chain is configured for this discount band - an admin must add one")
            instance = approval_service.instantiate_chain(db, quote, template)
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

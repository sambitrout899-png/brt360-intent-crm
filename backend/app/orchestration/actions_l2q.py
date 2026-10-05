"""Per-intent orchestration step sequences for INT-04/05/06 (Sprint 2,
Lead-to-Quote) - directly from HLD Section 7's "Orchestration Steps".
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.crm_core import approval_service, catalog_service, lead_service
from app.data_intelligence import event_bus
from app.integration import party_adapter, tmf620_adapter
from app.models.crm import ApprovalDecision, ApprovalRequest, ApprovalStatus, FeasibilityResult, Opportunity, OpportunityStage, Quote, QuoteItem, QuoteStatus
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
        rep_authority_pct = approval_service.DISCOUNT_MATRIX[0][0]  # "Auto-Approved" ceiling
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
    """INT-06 - Check feasibility & approve."""

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
        # deterministic so this is testable without a live OSS.
        feasible = "unavailable" not in (quote.discount_justification or "").lower()
        fr = FeasibilityResult(quote_id=quote.id, feasible=feasible, details="Stub OSS feasibility check")
        db.add(fr)
        db.flush()
        state["feasible"] = feasible
        return {"feasible": feasible}

    def step2() -> dict[str, Any]:
        tier = approval_service.determine_approver_tier(state["quote"].requested_discount_pct)
        state["tier"] = tier
        return {"approver_tier": tier}

    def step3() -> dict[str, Any]:
        ar = ApprovalRequest(
            quote_id=quote_id,
            approver_tier=state["tier"],
            deal_size=state["quote"].total_price,
            status=ApprovalStatus.pending,
        )
        db.add(ar)
        db.flush()
        state["approval_request_id"] = ar.id
        return {"approval_request_id": ar.id, "approver_tier": state["tier"]}

    def step4() -> dict[str, Any]:
        # Routing to the queue is represented by the ApprovalRequest row
        # created above staying "pending" until step 5 below.
        return {"routed": True}

    def step5() -> dict[str, Any]:
        if not state["feasible"] and decision == "approve":
            raise ValueError("Cannot approve a quote that failed the feasibility check")

        ad = ApprovalDecision(approval_request_id=state["approval_request_id"], decision=decision, reasoning=reasoning_text or "")
        db.add(ad)
        ar = db.get(ApprovalRequest, state["approval_request_id"])
        ar.status = ApprovalStatus(decision if decision in ("approved", "rejected", "conditional") else {"approve": "approved", "reject": "rejected"}.get(decision, "conditional"))
        quote = state["quote"]
        quote.status = QuoteStatus.approved if decision == "approve" else QuoteStatus.rejected
        db.flush()
        event_bus.publish(db, execution_id, "quote.approved" if decision == "approve" else "quote.rejected", {"quote_id": quote.id})
        return {"quote_id": quote.id, "status": quote.status.value, "decision": decision}

    return [
        SagaStep("run_feasibility_check", step1),
        SagaStep("evaluate_discount_matrix", step2),
        SagaStep("assemble_approval_case", step3),
        SagaStep("route_to_approver_queue", step4),
        SagaStep("capture_decision", step5),
    ]

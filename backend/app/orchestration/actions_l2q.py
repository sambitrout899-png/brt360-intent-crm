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

from app.crm_core import approval_service, catalog_service, lead_service, ponr_service, process_service
from app.crm_core import product_config_service as product_config
from app.data_intelligence import event_bus
from app.integration import party_adapter, tmf620_adapter
from app.models.catalog import ProductOffering
from app.models.product_config import QuoteItemConfig, QuoteTerms
from app.models.crm import FeasibilityResult, Lead, LeadStatus, Opportunity, OpportunityStage, Quote, QuoteItem, QuoteStatus
from app.orchestration.orchestrator import SagaStep


def _actor(entities: dict[str, Any]) -> int | None:
    value = entities.get("_actor_user_id")
    return int(value) if value else None


def _decide_or_submit(db: Session, state: dict[str, Any], entities: dict[str, Any], decision: str, reasoning_text: str) -> dict[str, Any]:
    """Decide the chain's current tier - unless its position is held by
    other people: then a chain opened by this very call has just been
    submitted to them, and one already open can't be decided by this user."""
    instance = state["instance"]
    allowed, why = approval_service.can_decide(db, instance, _actor(entities))
    if not allowed:
        if state.get("new"):
            tier = approval_service.waiting_for(instance)
            return {"chain_status": "pending", "decided_tier": None, "next_tier": tier, "waiting_for": tier, "submitted": True}
        raise ValueError(why)
    return approval_service.record_step_decision(db, instance, decision, reasoning_text or "", _actor(entities))


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
    interested_offering_name = entities.get("offering_name") or None
    quantity = int(entities.get("quantity") or 1)

    state: dict[str, Any] = {}

    def step1() -> dict[str, Any]:
        if interested_offering_name and tmf620_adapter.get_active_offering_by_name(db, tenant_id, interested_offering_name) is None:
            raise ValueError(f"No active product named '{interested_offering_name}' - pick one from the catalog")
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
                tenant_id=tenant_id, lead_id=lead.id, name=f"{company_name} - initial opportunity", stage=OpportunityStage.draft,
                offering_name=interested_offering_name, quantity=quantity,
            )
            db.add(opportunity)
            db.flush()
            process_service.set_account_manager(opportunity, _actor(entities))
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
        opportunity = db.query(Opportunity).filter(Opportunity.lead_id == lead.id).order_by(Opportunity.id.desc()).first()
        if opportunity is not None and not opportunity.offering_name:
            opportunity.offering_name = offering_name
        db.flush()
        # The lead's list of interested products (INT-46) gets it too - for
        # a prospect with a segment, only products sold to that segment.
        from app.orchestration import actions_prospect

        wanted = actions_prospect.normalize_products(entities.get("products")) or [(offering_name, int(entities.get("quantity") or 1))]
        if process_service.party_segment(lead.party):
            wanted = actions_prospect.check_products(db, tenant_id, lead.party, wanted)
        products = actions_prospect.add_lead_products(db, lead, wanted)
        event_bus.publish(db, execution_id, "lead.product_interest_added", {"lead_id": lead.id, "offering_name": offering_name})
        return {"lead_id": lead.id, "offering_name": offering_name, "interested_products": products}

    return [SagaStep("add_product_to_lead", step1)]


def build_int17_steps(db: Session, tenant_id: int, execution_id: int, entities: dict[str, Any]) -> list[SagaStep]:
    """INT-17 - Approve this lead (Sales Director qualification gate).

    With a "lead" approval chain configured (see INT-04) this decides its
    current tier. Without one, the decision qualifies (or rejects) a lead
    still pending qualification directly - qualifying opens its
    Opportunity, exactly as INT-04 does when there is no gate.
    """

    lead_id = entities["lead_id"]
    decision = entities.get("decision", "approve")
    reasoning_text = entities.get("reasoning", "")

    state: dict[str, Any] = {}

    def step1() -> dict[str, Any]:
        lead = db.get(Lead, lead_id)
        if lead is None:
            raise ValueError(f"Lead {lead_id} not found")
        if decision == "approve":  # approving opens an opportunity - not for B2C (see b2c_order_service)
            from app.crm_core import b2c_order_service

            b2c_order_service.guard(db, tenant_id, lead.party, "opportunity")
        state["lead"] = lead
        return {"lead_id": lead.id, "status": lead.status.value}

    def step2() -> dict[str, Any]:
        lead = state["lead"]
        instance = approval_service.get_chain_instance(db, "lead", lead.id)
        if instance is None:
            template = approval_service.resolve_lead_qualification_template(db, tenant_id)
            if template is None:
                if lead.status != LeadStatus.pending_qualification:
                    raise ValueError(f"Lead {lead.id} is already {lead.status.value.replace('_', ' ')}")
                state["direct"] = True
                return {"approval_needed": False}
            # The request is the lead's creator's - whoever opens the approval here is often the approver.
            instance = approval_service.instantiate_chain(
                db, "lead", lead.id, template, summary=f"Qualify lead {lead.party.name}", requested_by_user_id=lead.sys_created_by
            )
            state["new"] = True
        state["instance"] = instance
        return {"chain_template": instance.template.name, "current_tier": instance.steps[instance.current_sequence - 1].approver_tier}

    def step3() -> dict[str, Any]:
        if state.get("direct"):
            outcome = {"chain_status": "rejected" if decision == "reject" else "approved"}
        else:
            outcome = _decide_or_submit(db, state, entities, decision, reasoning_text)
        state["outcome"] = outcome
        return outcome

    def step4() -> dict[str, Any]:
        lead = state["lead"]
        outcome = state["outcome"]
        if outcome["chain_status"] == "approved":
            lead.status = LeadStatus.qualified
            from app.models.pages import LeadProduct

            first = db.query(LeadProduct).filter(LeadProduct.lead_id == lead.id).order_by(LeadProduct.position, LeadProduct.id).first()
            opportunity = Opportunity(
                tenant_id=tenant_id, lead_id=lead.id, name=f"{lead.party.name} - initial opportunity", stage=OpportunityStage.draft,
                offering_name=lead.interested_offering_name, **({"quantity": first.quantity} if first is not None else {}),
            )
            db.add(opportunity)
            db.flush()
            process_service.set_account_manager(opportunity, _actor(entities))
            event_bus.publish(db, execution_id, "lead.qualified", {"lead_id": lead.id, "opportunity_id": opportunity.id})
            return {"lead_id": lead.id, "opportunity_id": opportunity.id, "status": lead.status.value}
        elif outcome["chain_status"] == "rejected":
            lead.status = LeadStatus.rejected
            db.flush()
            event_bus.publish(db, execution_id, "lead.rejected", {"lead_id": lead.id})
            return {"lead_id": lead.id, "status": lead.status.value}
        if outcome.get("waiting_for"):
            return {"lead_id": lead.id, "status": lead.status.value, "waiting_for": outcome["waiting_for"]}
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
        opportunity = db.get(Opportunity, opportunity_id)
        # Sales-to-Order: an opportunity sent down the Presales path is
        # quoted once Presales has cleared it.
        if opportunity is not None and opportunity.stage in (
            OpportunityStage.configured, OpportunityStage.in_feasibility, OpportunityStage.feasibility_failed
        ):
            raise ValueError(
                f"Opportunity {opportunity.id} is {opportunity.stage.value.replace('_', ' ')} - "
                "it can be quoted once Presales finds it feasible"
            )
        state["offering"] = offering
        state["opportunity"] = opportunity
        state["party"] = opportunity.lead.party if opportunity is not None and opportunity.lead is not None else None
        from app.crm_core import b2c_order_service

        b2c_order_service.guard(db, tenant_id, state["party"], "quote")
        return {"offering_id": offering.id, "list_price": offering.price}

    def step2() -> dict[str, Any]:
        offering = state["offering"]
        opportunity = state["opportunity"]
        ceiling = offering.price_plan.discount_ceiling_pct if offering.price_plan else 20.0
        # Pricelist (this quote's own, else the opportunity's, else the
        # customer's, else the default - a 0% list keeps the catalog price),
        # then the chosen pricing attributes' impacts (products without a
        # configuration design have none), then the discount.
        price_list = product_config.price_list_for(db, tenant_id, state["party"], opportunity, explicit=entities.get("price_list"))
        base_price, price_source = product_config.base_price(db, tenant_id, price_list, offering, quantity)
        opp_config = opportunity.config if opportunity is not None else None
        same_product = opp_config is not None and (opportunity.offering_name or "").lower() == offering.name.lower()
        # the opportunity's frozen product definition, else the catalog's as it is now
        from app.crm_core import instance_service

        source = instance_service.get(db, tenant_id, "opportunity", opportunity_id, offering.name)
        frozen = instance_service.design(source) or product_config.schema(db, tenant_id, offering.name)
        state.update(source_instance=source, frozen=frozen)
        pricing = product_config.loads(opp_config.pricing_attributes_json) if same_product else {}
        pricing.update(product_config.selection(db, tenant_id, offering.name, entities.get("pricing_attributes"), frozen))
        technical = product_config.loads(opp_config.technical_attributes_json) if same_product else {}
        configured = product_config.configure(db, tenant_id, offering.name, pricing, technical, base_price, party=state["party"], price_list=price_list,
                                              design=frozen)
        unit_price = catalog_service.compute_price(configured["configured"], requested_discount_pct, ceiling, offering.price_floor)
        state.update(unit_price=unit_price, configured=configured, price_list=price_list, price_source=price_source,
                     keep_config=configured["designed"] or bool(configured["pricing"] or configured["technical"])
                     or bool(entities.get("price_list")) or base_price != offering.price)
        out = {"unit_price": unit_price, "discount_applied_pct": min(requested_discount_pct, ceiling)}
        if base_price != offering.price:
            out.update(base_price=base_price, price_source=price_source)
        if configured["lines"]:
            out.update(price_breakdown=configured["lines"], configured_price=configured["configured"])
        return out

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
        item = QuoteItem(quote_id=quote.id, product_offering_id=state["offering"].id, quantity=quantity, unit_price=state["unit_price"])
        db.add(item)
        opportunity = db.get(Opportunity, opportunity_id)
        opportunity.stage = OpportunityStage.quoted
        db.flush()
        if entities.get("price_list"):
            db.add(QuoteTerms(quote_id=quote.id, price_list_id=state["price_list"].id))
        if state["keep_config"]:
            from app.orchestration import actions_config

            actions_config.save_quote_item_config(db, item, state["configured"], state["price_list"], state["price_source"])
        if state["keep_config"] or state["source_instance"] is not None:
            from app.crm_core import instance_service

            instance = instance_service.take(db, tenant_id, "quote", quote.id, state["offering"].name, state["source_instance"], item.id)
            if state["source_instance"] is None:  # the same definition the price was worked out on
                import json

                instance.definition_json = json.dumps(state["frozen"], default=str)
            instance_service.record(instance, state["configured"], quantity)
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
            # A negotiated price override routes on its effective discount.
            from app.crm_core import quote_override_service

            discount_pct = quote_override_service.approval_discount_pct(db, quote)
            template = approval_service.resolve_template(db, tenant_id, discount_pct, segment, quote.total_price)
            if template is None:
                raise ValueError("No approval chain is configured for this discount band - an admin must add one")
            override = quote_override_service.active(db, quote.id)
            negotiated = f" (negotiated from {override.computed_price:,.2f}: {override.reason})" if override is not None else ""
            instance = approval_service.instantiate_chain(
                db, "quote", quote.id, template,
                summary=f"Quote #{quote.id}: {discount_pct:g}% discount, total {quote.total_price:,.2f}{negotiated}",
                requested_by_user_id=_actor(entities),
            )
            quote.status = QuoteStatus.pending_approval
            db.flush()
            state["new"] = True
        state["instance"] = instance
        return {"chain_template": instance.template.name, "current_tier": instance.steps[instance.current_sequence - 1].approver_tier}

    def step3() -> dict[str, Any]:
        if not state["feasible"] and decision == "approve":
            raise ValueError("Cannot approve a quote that failed the feasibility check")
        outcome = _decide_or_submit(db, state, entities, decision, reasoning_text)
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
        if outcome.get("waiting_for"):
            return {"quote_id": quote.id, "status": quote.status.value, "decision": decision, "waiting_for": outcome["waiting_for"]}
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
        configured = item is not None and (
            entities.get("pricing_attributes") or db.query(QuoteItemConfig).filter(QuoteItemConfig.quote_item_id == item.id).first()
        )
        if configured:
            # A configured product is re-priced from its pricelist and
            # (changed) pricing attributes - adding, changing or removing
            # one changes the price.
            from app.orchestration import actions_config

            repriced = actions_config.reprice_quote(db, tenant_id, quote, entities.get("pricing_attributes"), new_quantity)
            db.flush()
            event_bus.publish(db, execution_id, "quote.revised", {"quote_id": quote.id, "total_price": quote.total_price})
            return {**repriced, "requested_discount_pct": quote.requested_discount_pct}
        from app.crm_core import quote_override_service

        override_ended = item is not None and quote_override_service.end_on_reprice(db, quote)
        if item is not None and new_quantity is not None:
            item.quantity = int(new_quantity)
        if item is not None:
            quote.total_price = round(item.unit_price * item.quantity * (1 - quote.requested_discount_pct / 100), 2)
        db.flush()
        event_bus.publish(db, execution_id, "quote.revised", {"quote_id": quote.id, "total_price": quote.total_price})
        out = {"quote_id": quote.id, "total_price": quote.total_price, "requested_discount_pct": quote.requested_discount_pct}
        return {**out, "price_override_ended": True} if override_ended else out

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


def build_int116_steps(db: Session, tenant_id: int, execution_id: int, entities: dict[str, Any]) -> list[SagaStep]:
    """INT-116 - Override the quote price: the total negotiated with the
    customer replaces the computed one (or, with remove_override, the
    computed one comes back). See quote_override_service."""

    from app.crm_core import quote_override_service

    quote_id = entities["quote_id"]
    remove = str(entities.get("remove_override") or "").lower() in ("true", "1", "yes")

    def step1() -> dict[str, Any]:
        quote = db.get(Quote, quote_id)
        if quote is None or quote.tenant_id != tenant_id:
            raise ValueError(f"Quote {quote_id} not found")
        if remove:
            result = quote_override_service.remove(db, tenant_id, quote)
            event_bus.publish(db, execution_id, "quote.price_override_removed", {"quote_id": quote.id, "total_price": quote.total_price})
            return result
        if entities.get("override_price") in (None, ""):
            raise ValueError("Give the negotiated monthly total - or tick 'Go back to the system price'")
        result = quote_override_service.apply(db, tenant_id, quote, float(entities["override_price"]), entities.get("reason") or "",
                                              _actor(entities))
        event_bus.publish(db, execution_id, "quote.price_overridden",
                          {"quote_id": quote.id, "total_price": quote.total_price, "effective_discount_pct": result["effective_discount_pct"]})
        return result

    return [SagaStep("remove_price_override" if remove else "override_quote_price", step1)]

"""B2B Sales-to-Order and B2C additions to the standard process.

The B2B Sales-to-Order (STO) process, with the intent that runs each step -
existing intents keep their meaning; the new steps get new intents:

     1  Account Manager  Create the prospect                       INT-15
     2  Account Manager  Convert it to an account + pricelist      INT-35  (new)
     3  Account Manager  Qualify a lead (with its product)         INT-04 / INT-16 / INT-17
     4                   -> the qualified lead opens an opportunity
     5  Account Manager  Configure product + pricing attributes    INT-37  (new)
     6  Account Manager  Send to Presales (site, tech parameters)  INT-38  (new)
     7  Presales         Feasibility: feasible -> 8, not -> 5      INT-18
     8  Account Manager  Quote, priced from the account pricelist  INT-05
     9  Approvers        Discount approval                         INT-06
   9a  Account Manager  Generate the proposal (PDF on the quote)  INT-109 (new)
   9b  Account Manager  Send it to the customer                   INT-22
    10  Account Manager  Attach the customer's PO -> Sales Ops     INT-39  (new; accepts the proposal)
    11  Sales Ops Head   Match all -> the order is created (auto)  INT-40  (new; + the agreement PDF)
    12  CPMO             Order approval                            INT-25
    13  BSD              WFM1 installation                         INT-26
    14  Fulfillment      WFM2 testing, ESAN initiated              INT-27
    15  Fulfillment      WFM3 ESAN signed -> billing, assets, live INT-28

Any approval - whatever the record - can also be decided with INT-36
("Approve or reject"); B2C adds a consumer customer with INT-41 and places
the order with INT-07. Records that never take these new steps follow the
original process unchanged.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from sqlalchemy.orm import Session

from app.crm_core import approval_service, lifecycle_service, process_service, quote_documents
from app.data_intelligence import event_bus
from app.integration import tmf620_adapter
from app.models.approval import ApprovalChainInstance
from app.models.crm import ApprovalStatus, Opportunity, OpportunityStage, Party, PartyProfile, Quote, QuotePurchaseOrder, QuoteStatus
from app.models.feasibility import FeasibilityAssessment, FeasibilityOutcome
from app.models.fulfillment import Proposal, ProposalStatus
from app.models.orders import Order, OrderMilestone, OrderStatus
from app.orchestration import fulfillment_ops
from app.orchestration.orchestrator import SagaStep
from app.timeutils import utcnow

# Opportunity stages that mean "on the Presales path, not cleared yet".
AWAITING_FEASIBILITY = (OpportunityStage.configured, OpportunityStage.in_feasibility, OpportunityStage.feasibility_failed)


def _get(db: Session, tenant_id: int, model, record_id, label: str):
    try:
        obj = db.get(model, int(record_id)) if record_id not in (None, "") else None
    except (TypeError, ValueError):
        obj = None
    if obj is None or obj.tenant_id != tenant_id:
        raise ValueError(f"{label} {record_id} not found")
    return obj


def actor(entities: dict[str, Any]) -> int | None:
    value = entities.get("_actor_user_id")
    return int(value) if value else None


def parse_attributes(raw: Any) -> dict[str, Any]:
    """'Bandwidth=100 Mbps, Contract term: 36 months' or a dict -> {name: value}."""
    if isinstance(raw, dict):
        return {str(k).strip(): v for k, v in raw.items() if str(k).strip() and v not in (None, "")}
    out: dict[str, Any] = {}
    for part in re.split(r"[,;\n]", str(raw or "")):
        if re.search(r"[=:]", part):
            key, value = re.split(r"\s*[=:]\s*", part.strip(), maxsplit=1)
            if key.strip() and value.strip():
                out[key.strip()] = value.strip()
    return out


def milestone(db: Session, order_id: int | None, code: str, label: str, detail: str = "") -> None:
    if order_id:
        db.add(OrderMilestone(order_id=order_id, code=code, label=label, detail=detail or ""))
        db.flush()


# ---------------------------------------------------------------- record approvals


def gate(
    db: Session, tenant_id: int, entity_type: str, entity_id: int, to_state: str, summary: str, requested_by: int | None
) -> ApprovalChainInstance | None:
    """The record approval a record needs to enter `to_state`, opened (or
    re-opened after a rejection) when the tenant has a rule for it. None =
    no approval needed, or already approved: the caller goes ahead."""
    rule = approval_service.resolve_record(db, tenant_id, entity_type, to_state)
    if rule is None:
        return None
    existing = approval_service.get_chain_instance(db, entity_type, entity_id, to_state)
    if existing is not None:
        if existing.status == ApprovalStatus.approved:
            return None
        if existing.status == ApprovalStatus.pending:
            return existing
        # Turned down before - ask again, from the first tier. The earlier
        # round is kept for the audit trail under a retired gate name.
        existing.gate = f"{to_state}~{existing.id}"[:50]
        db.flush()
    return approval_service.instantiate_chain(
        db, entity_type, entity_id, rule, gate=to_state, summary=summary, requested_by_user_id=requested_by
    )


def waiting(instance: ApprovalChainInstance) -> dict[str, Any]:
    return {"approval_id": instance.id, "approval_status": "pending", "waiting_for": approval_service.waiting_for(instance)}


# ---------------------------------------------------------------- INT-35 account


def _make_account(db: Session, tenant_id: int, party: Party) -> dict[str, Any]:
    profile = process_service.party_profile(party, create=True)
    profile.account_status = "account"
    profile.segment = profile.segment or "B2B"
    if party.party_type == "prospect":
        party.party_type = "organization"
    db.flush()
    price_list = process_service.price_list_of(db, tenant_id, party)
    return {"party_id": party.id, "account_status": "account", "price_list": price_list.name if price_list else None}


def build_int35_steps(db: Session, tenant_id: int, execution_id: int, entities: dict[str, Any]) -> list[SagaStep]:
    """INT-35 - Convert a prospect into an account and give it its pricelist."""

    state: dict[str, Any] = {}

    def step1() -> dict[str, Any]:
        party = _get(db, tenant_id, Party, entities.get("party_id"), "Customer")
        if process_service.account_status(party) == "account":
            raise ValueError(f"{party.name} is already an account")
        price_list = process_service.find_price_list(db, tenant_id, entities.get("price_list"))
        if entities.get("price_list") and price_list is None:
            raise ValueError(f"No active pricelist '{entities['price_list']}'")
        state.update(party=party, price_list=price_list)
        return {"party_id": party.id, "price_list": price_list.name if price_list else None}

    def step2() -> dict[str, Any]:
        party, price_list = state["party"], state["price_list"]
        profile = process_service.party_profile(party, create=True)
        if price_list is not None:
            profile.price_list_id = price_list.id  # recorded now, so an approver sees it
        if entities.get("industry"):
            profile.industry = entities["industry"]
        request = gate(db, tenant_id, "party", party.id, "account", f"Make {party.name} an account", actor(entities))
        if request is not None:
            db.flush()
            return {"party_id": party.id, **waiting(request)}
        result = _make_account(db, tenant_id, party)
        event_bus.publish(db, execution_id, "account.created", result)
        return result

    return [SagaStep("check_prospect", step1), SagaStep("convert_to_account", step2)]


# ---------------------------------------------------------------- INT-37 configure


def pricing_changed(opportunity: Opportunity, raw: Any) -> bool:
    """Whether these pricing attributes differ from the ones on the opportunity
    (the same values sent back unchanged are no change)."""
    given = parse_attributes(raw)
    config = process_service.opportunity_config(opportunity)
    stored = json.loads(config.pricing_attributes_json or "{}") if config is not None else {}
    return any(str(stored.get(k, "")).strip().lower() != str(v).strip().lower() for k, v in given.items())


def build_int37_steps(db: Session, tenant_id: int, execution_id: int, entities: dict[str, Any]) -> list[SagaStep]:
    """INT-37 - Configure the product and its pricing attributes on an opportunity."""

    def step1() -> dict[str, Any]:
        opportunity = _get(db, tenant_id, Opportunity, entities.get("opportunity_id"), "Opportunity")
        # the product picked on the lead comes along - nobody has to pick it again
        lead = opportunity.lead
        name = entities.get("offering_name") or opportunity.offering_name or (lead.interested_offering_name if lead is not None else None)
        if process_service.is_presales_only(db, tenant_id, actor(entities)) and name:
            changes = [what for what, changed in (
                ("the pricing attributes", pricing_changed(opportunity, entities.get("pricing_attributes"))),
                ("the product", bool(opportunity.offering_name) and name.lower() != opportunity.offering_name.lower()),
                ("the quantity", entities.get("quantity") not in (None, "") and int(entities["quantity"]) != (opportunity.quantity or 1)),
            ) if changed]
            if changes:
                raise ValueError(f"Presales sees {' and '.join(changes)} but doesn't change them - the Account Manager does. "
                                 "Presales updates the technical attributes (Send to Presales / Check feasibility).")
        allowed = (OpportunityStage.draft, OpportunityStage.qualified, OpportunityStage.configured, OpportunityStage.feasibility_failed)
        if opportunity.stage not in allowed:
            raise ValueError(
                f"Opportunity {opportunity.id} is {opportunity.stage.value.replace('_', ' ')} - "
                "the product can be configured before it goes to Presales, or after Presales hands it back"
            )
        if not name:
            raise ValueError("Pick the product to configure")
        offering = tmf620_adapter.get_active_offering_by_name(db, tenant_id, name)
        if offering is None:
            raise ValueError(f"No active product named '{name}'")
        from app.crm_core import product_config_service as product_config

        from app.crm_core import instance_service

        config = process_service.opportunity_config(opportunity, create=True)
        same_product = (opportunity.offering_name or "").lower() == offering.name.lower()
        # the product as defined in the catalog now, frozen into this opportunity
        instance = instance_service.ensure(db, tenant_id, "opportunity", opportunity.id, offering.name)
        frozen = instance_service.design(instance)
        attributes = json.loads(config.pricing_attributes_json or "{}") if same_product else {}
        attributes.update(product_config.selection(db, tenant_id, offering.name, entities.get("pricing_attributes"), frozen))
        technical = product_config.loads(config.technical_attributes_json) if same_product else {}
        technical.update(product_config.selection(db, tenant_id, offering.name, entities.get("technical_attributes"), frozen))
        # Checked and priced against the product's design (a product without
        # one takes the attributes as given at its base price).
        party = opportunity.lead.party if opportunity.lead is not None else None
        price_list = product_config.price_list_for(db, tenant_id, party, opportunity)
        base, source = product_config.base_price(db, tenant_id, price_list, offering)
        configured = product_config.configure(db, tenant_id, offering.name, attributes, technical, base, party=party, price_list=price_list,
                                              design=frozen)
        opportunity.offering_name = offering.name
        if entities.get("quantity"):
            opportunity.quantity = int(entities["quantity"])
        instance_service.record(instance, configured, opportunity.quantity)
        config.pricing_attributes_json = json.dumps(configured["pricing"])
        config.technical_attributes_json = json.dumps(configured["technical"]) if configured["technical"] else config.technical_attributes_json
        process_service.set_account_manager(opportunity, actor(entities))
        opportunity.stage = OpportunityStage.configured
        db.flush()
        event_bus.publish(db, execution_id, "opportunity.configured", {"opportunity_id": opportunity.id, "offering_name": offering.name})
        result = {
            "opportunity_id": opportunity.id, "offering_name": offering.name, "quantity": opportunity.quantity,
            "pricing_attributes": configured["pricing"], "stage": opportunity.stage.value,
        }
        if configured["technical"]:
            result["technical_attributes"] = configured["technical"]
        if configured["designed"]:
            result.update(base_price=base, price_source=source, price_breakdown=configured["lines"], configured_price=configured["configured"])
        if configured.get("default_fixes"):
            result["note"] = "; ".join(configured["default_fixes"])  # a stale product default that was not applied as-is
        return result

    return [SagaStep("configure_product", step1)]


# ---------------------------------------------------------------- INT-38 presales


def update_technical(db: Session, tenant_id: int, opportunity: Opportunity, raw: Any) -> dict[str, Any]:
    """Add or change technical attributes on the opportunity (checked against
    the product's design) - the Account Manager when sending to Presales,
    Presales during the feasibility check. Returns them all, or {} when none
    were given."""
    if not raw:
        return {}
    from app.crm_core import instance_service
    from app.crm_core import product_config_service as product_config

    config = process_service.opportunity_config(opportunity, create=True)
    frozen = instance_service.design_for(db, tenant_id, "opportunity", opportunity.id, opportunity.offering_name or "")
    given = product_config.check_technical(db, tenant_id, opportunity.offering_name or "", raw, frozen)
    merged = {**product_config.loads(config.technical_attributes_json), **given}
    config.technical_attributes_json = json.dumps(merged)
    db.flush()
    return merged


def build_int38_steps(db: Session, tenant_id: int, execution_id: int, entities: dict[str, Any]) -> list[SagaStep]:
    """INT-38 - Send the configured opportunity to Presales for feasibility."""

    def step1() -> dict[str, Any]:
        opportunity = _get(db, tenant_id, Opportunity, entities.get("opportunity_id"), "Opportunity")
        from app.crm_core import b2c_order_service

        b2c_order_service.guard(db, tenant_id, opportunity.lead.party if opportunity.lead else None, "Presales check")
        if opportunity.stage not in (OpportunityStage.configured, OpportunityStage.feasibility_failed):
            raise ValueError(
                f"Opportunity {opportunity.id} is {opportunity.stage.value.replace('_', ' ')} - configure the product first"
            )
        config = process_service.opportunity_config(opportunity, create=True)
        config.site_address = " ".join(str(entities.get("address") or "").split()).strip(" ,") or config.site_address
        if not config.site_address:
            raise ValueError("Give the site address Presales should check")
        config.technical_parameters = entities.get("technical_parameters") or config.technical_parameters
        update_technical(db, tenant_id, opportunity, entities.get("technical_attributes"))
        process_service.set_account_manager(opportunity, actor(entities))
        opportunity.stage = OpportunityStage.in_feasibility
        db.flush()
        presales_user = lifecycle_service.resolve_user(db, tenant_id, entities.get("to_user"))
        if entities.get("to_user") and presales_user is None:
            raise ValueError(f"No active user '{entities['to_user']}'")
        if presales_user is not None:
            handed = process_service.hand_to_user(db, tenant_id, "opportunity", opportunity.id, presales_user.id, "Feasibility check", "process")
        else:
            handed = process_service.hand_to_role(db, tenant_id, "opportunity", opportunity.id, "presales", "Feasibility check", "process")
        event_bus.publish(db, execution_id, "opportunity.sent_to_presales", {"opportunity_id": opportunity.id})
        return {
            "opportunity_id": opportunity.id, "stage": opportunity.stage.value, "site_address": config.site_address,
            "assigned_to_presales": bool(handed),
        }

    return [SagaStep("send_to_presales", step1)]


# ---------------------------------------------------------------- INT-18 on the Presales path


def presales_outcome(
    db: Session, tenant_id: int, opportunity: Opportunity, assessment_outcome: str, entities: dict[str, Any]
) -> dict[str, Any]:
    """INT-18 run on an opportunity Presales is assessing: Presales' own
    decision (when given) or else the checks decide. Feasible -> back to the
    Account Manager to quote (through a feasibility sign-off, if the tenant
    has one); not feasible -> back to the Account Manager to reconfigure."""
    decision = str(entities.get("decision") or "").strip().lower().replace(" ", "_")
    outcome = {"feasible": "passed", "approve": "passed", "passed": "passed",
               "not_feasible": "failed", "infeasible": "failed", "reject": "failed", "failed": "failed"}.get(decision, assessment_outcome)
    # Presales' manual override when Inventory is down or wrong: feasible without holding the network
    override = str(entities.get("inventory_override") or "").strip().lower() in ("true", "1", "yes", "on")
    reason = " ".join(str(entities.get("override_reason") or "").split())
    if override:
        if outcome == "failed" and decision:
            raise ValueError("The inventory override passes feasibility - it can't go with 'not feasible'")
        if not reason:
            raise ValueError("Give the reason for overriding the inventory check")
        outcome = "passed"
    config = process_service.opportunity_config(opportunity, create=True)
    if entities.get("notes"):
        config.feasibility_notes = entities["notes"]
    result: dict[str, Any] = {"presales_outcome": outcome}
    technical = update_technical(db, tenant_id, opportunity, entities.get("technical_attributes"))
    if technical:
        result["technical_attributes"] = technical
    from app.crm_core import inventory_service

    network = inventory_service.in_use(db, tenant_id)
    if outcome == "failed":
        if network:  # nothing stays held for a deal that can't be built
            released = inventory_service.release_opportunity(db, tenant_id, opportunity.id, source="presales", note="not feasible")
            if released:
                result["inventory_released"] = [r["identifier"] for r in released]
        opportunity.stage = OpportunityStage.feasibility_failed
        db.flush()
        process_service.hand_to_user(db, tenant_id, "opportunity", opportunity.id, process_service.account_manager_of(opportunity),
                                     "Not feasible - reconfigure with the customer", "process")
    elif outcome == "passed":
        if network and not entities.get("skip_reservation"):
            # feasible means buildable: what the deal needs is held at the location serving the site
            # (what Presales already reserved is kept), and moves to the order created from the deal
            try:
                with db.begin_nested():  # all of it or nothing - an override keeps none of a half-made hold
                    held = inventory_service.hold_for_opportunity(db, tenant_id, opportunity.id, address=str(entities.get("address") or entities.get("site") or ""))
            except Exception as exc:
                if not override:
                    if not isinstance(exc, ValueError):
                        raise
                    raise ValueError(f"Can't pass feasibility: {exc} - if Inventory is down or wrong, "
                                     "Presales can pass it with 'Override the inventory check' and a reason") from exc
                if not isinstance(exc, ValueError):
                    logging.getLogger(__name__).exception("Inventory hold failed for opportunity %s - overridden by Presales", opportunity.id)
                result["inventory_override"] = {"reason": reason, "inventory_error": str(exc) if isinstance(exc, ValueError)
                                                else "Inventory didn't answer"}
            else:
                result["inventory_held"] = [f"{r['type']} {r['identifier']}" for r in held["held"]]
                result["served_from"] = held["location"]
        if override:
            result.setdefault("inventory_override", {"reason": reason})
            note = f"Inventory check overridden by Presales: {reason}"
            config.feasibility_notes = f"{config.feasibility_notes}\n{note}" if config.feasibility_notes else note
        request = gate(db, tenant_id, "opportunity", opportunity.id, "feasible", f"Feasibility sign-off for {opportunity.name}", actor(entities))
        if request is not None:
            result.update(waiting(request))
        else:
            result.update(mark_feasible(db, tenant_id, opportunity))
    result["stage"] = opportunity.stage.value
    return result


def mark_feasible(db: Session, tenant_id: int, opportunity: Opportunity) -> dict[str, Any]:
    opportunity.stage = OpportunityStage.feasible
    db.flush()
    process_service.hand_to_user(db, tenant_id, "opportunity", opportunity.id, process_service.account_manager_of(opportunity),
                                 "Feasible - prepare the quote", "process")
    return {"opportunity_id": opportunity.id, "stage": opportunity.stage.value}


# ---------------------------------------------------------------- INT-39 PO


def build_int39_steps(db: Session, tenant_id: int, execution_id: int, entities: dict[str, Any]) -> list[SagaStep]:
    """INT-39 - Attach the customer's purchase order to the approved quote and
    hand it to Sales Ops."""

    def step1() -> dict[str, Any]:
        quote = _get(db, tenant_id, Quote, entities.get("quote_id"), "Quote")
        if quote.status != QuoteStatus.approved:
            raise ValueError(f"Quote {quote.id} is {quote.status.value.replace('_', ' ')} - attach the PO once the quote is approved")
        if process_service.is_matched(quote):
            raise ValueError(f"Quote {quote.id} is already matched - its order has been created")
        po_number = str(entities.get("po_number") or "").strip()
        if not po_number:
            raise ValueError("Give the PO number")
        po = quote.purchase_order or QuotePurchaseOrder(po_number=po_number)
        quote.purchase_order = po
        po.po_number = po_number
        po.document_ref = entities.get("document_ref") or po.document_ref
        po.amount = float(entities["po_amount"]) if entities.get("po_amount") not in (None, "") else quote.total_price
        po.received_at = utcnow()
        # the PO's PDF, uploaded on the quote (Documents) - linked when no other reference was given
        uploaded = quote_documents.latest(db, quote.id, "purchase_order")
        linked = uploaded is not None and not entities.get("document_ref")
        if linked:
            po.document_ref = quote_documents.ref(uploaded)
        # the PO is the customer's acceptance of the proposal they were sent
        proposal = db.query(Proposal).filter(Proposal.quote_id == quote.id).order_by(Proposal.id.desc()).first()
        accepted = proposal is not None and proposal.status == ProposalStatus.sent
        if accepted:
            proposal.status, proposal.decided_at = ProposalStatus.customer_approved, utcnow()
        db.flush()
        handed = process_service.hand_to_role(db, tenant_id, "quote", quote.id, "sales_ops", f"PO {po_number} attached", "process")
        event_bus.publish(db, execution_id, "quote.po_attached", {"quote_id": quote.id, "po_number": po_number})
        return {"quote_id": quote.id, "po_number": po_number, "po_amount": po.amount, "assigned_to_sales_ops": bool(handed),
                "proposal_accepted": accepted, **({"po_document_id": uploaded.id} if linked else {})}

    return [SagaStep("attach_po", step1)]


# ---------------------------------------------------------------- INT-109 proposal PDF


def build_int109_steps(db: Session, tenant_id: int, execution_id: int, entities: dict[str, Any]) -> list[SagaStep]:
    """INT-109 - Once the discount is approved, write the customer proposal as
    a PDF on the quote, to be checked and then sent (INT-22)."""

    def step1() -> dict[str, Any]:
        quote = _get(db, tenant_id, Quote, entities.get("quote_id"), "Quote")
        if quote.status != QuoteStatus.approved:
            raise ValueError(f"Quote {quote.id} is {quote.status.value.replace('_', ' ')} - the proposal comes once its discount is approved")
        if db.query(Order.id).filter(Order.quote_id == quote.id).first() is not None:
            raise ValueError(f"Quote {quote.id} is already ordered - its proposal is no longer needed")
        doc = quote_documents.generate(db, tenant_id, quote, "proposal", user_id=actor(entities), extra={"note": entities.get("notes")})
        event_bus.publish(db, execution_id, "quote.proposal_generated", {"quote_id": quote.id, "document_id": doc.id, "version": doc.version})
        return {"quote_id": quote.id, "document_id": doc.id, "version": doc.version, "file_name": doc.file_name, "title": doc.title}

    return [SagaStep("generate_proposal_pdf", step1)]


# ---------------------------------------------------------------- INT-40 match all


def _feasibility_ok(db: Session, opportunity: Opportunity | None, quote: Quote) -> tuple[bool, str]:
    if opportunity is None:
        return False, "no opportunity"
    if opportunity.stage in AWAITING_FEASIBILITY:
        return False, f"opportunity is {opportunity.stage.value.replace('_', ' ')}"
    if opportunity.stage == OpportunityStage.feasible:
        return True, "cleared by Presales"
    cleared = (
        db.query(lifecycle_service.EntityStateHistory)
        .filter(lifecycle_service.EntityStateHistory.entity_type == "opportunity",
                lifecycle_service.EntityStateHistory.entity_id == opportunity.id,
                lifecycle_service.EntityStateHistory.to_state == OpportunityStage.feasible.value)
        .first()
    )
    if cleared is not None:
        return True, "cleared by Presales"
    passed = (
        db.query(FeasibilityAssessment)
        .filter(FeasibilityAssessment.opportunity_id == opportunity.id, FeasibilityAssessment.outcome == FeasibilityOutcome.passed)
        .first()
    )
    if passed is not None:
        return True, "feasibility checks passed"
    if quote.feasibility_result is not None:
        return quote.feasibility_result.feasible, "network check at approval"
    return True, "not assessed"


def match_report(db: Session, tenant_id: int, quote: Quote) -> list[dict[str, Any]]:
    opportunity = db.get(Opportunity, quote.opportunity_id)
    party = opportunity.lead.party if opportunity is not None and opportunity.lead is not None else None
    feasible, feasibility_detail = _feasibility_ok(db, opportunity, quote)
    # Parties from before accounts existed count as accounts. A prospect is
    # fine too (New Connect): it becomes an account by itself when this
    # order is activated end to end.
    is_account = party is not None and (process_service.account_status(party) == "account" or party.party_type != "prospect")
    customer_detail = (party.name if is_account else f"{party.name} - a prospect, becomes an account when the order is activated") if party else "no customer"
    po = process_service.quote_po(quote)
    # a proposal the customer was sent must not have been turned down (the PO accepts it)
    proposal = db.query(Proposal).filter(Proposal.quote_id == quote.id).order_by(Proposal.id.desc()).first()
    accepted = [] if proposal is None else [
        {"check": "Customer accepted the proposal", "ok": proposal.status != ProposalStatus.customer_rejected,
         "detail": proposal.status.value.replace("_", " ")}]
    return accepted + [
        {"check": "PO attached", "ok": po is not None, "detail": po.po_number if po else "no PO"},
        {"check": "PO amount matches the quote",
         "ok": po is not None and po.amount is not None and abs(po.amount - quote.total_price) <= 0.01,
         "detail": f"PO {(po.amount if po and po.amount is not None else 0):,.2f} vs quote {quote.total_price:,.2f}"},
        {"check": "Customer known", "ok": party is not None, "detail": customer_detail},
        {"check": "Quote approved", "ok": quote.status == QuoteStatus.approved, "detail": quote.status.value},
        {"check": "Feasible", "ok": feasible, "detail": feasibility_detail},
        {"check": "Quote has products", "ok": bool(quote.items), "detail": f"{len(quote.items)} line(s)"},
    ]


def build_int40_steps(db: Session, tenant_id: int, execution_id: int, entities: dict[str, Any]) -> list[SagaStep]:
    """INT-40 - Sales Ops matches the PO against the quote; a full match
    creates the order automatically and sends it to CPMO."""

    state: dict[str, Any] = {}

    def step1() -> dict[str, Any]:
        quote = _get(db, tenant_id, Quote, entities.get("quote_id"), "Quote")
        po = process_service.quote_po(quote)
        if po is None:
            raise ValueError(f"Quote {quote.id} has no PO yet - the Account Manager attaches it first")
        if po.matched_at is not None:
            raise ValueError(f"Quote {quote.id} is already matched")
        report = match_report(db, tenant_id, quote)
        failed = [c for c in report if not c["ok"]]
        if failed:
            raise ValueError("Match failed: " + "; ".join(f"{c['check']} ({c['detail']})" for c in failed))
        po.matched_at = utcnow()
        po.match_notes = "; ".join(f"{c['check']}: {c['detail']}" for c in report)
        db.flush()
        state["quote"] = quote
        event_bus.publish(db, execution_id, "quote.matched", {"quote_id": quote.id})
        return {"quote_id": quote.id, "match": report}

    def step2() -> dict[str, Any]:
        quote = state["quote"]
        created = fulfillment_ops.op_generate_order_from_quote(db, tenant_id, quote.id)
        order = db.get(Order, created["order_id"])
        process_service.order_profile(order, create=True).segment = "B2B"
        db.flush()
        milestone(db, order.id, "order_created", "Order created from the matched PO")
        handed = process_service.hand_to_role(db, tenant_id, "order", order.id, "cpmo", "Order approval", "process")
        milestone(db, order.id, "cpmo_pending", "With CPMO for approval")
        event_bus.publish(db, execution_id, "order.generated_from_quote", created)
        state["order"] = order
        return {**created, "assigned_to_cpmo": bool(handed)}

    def step3() -> dict[str, Any]:
        """The quote, now an order, gets its agreement - a record (Agreements)
        and its PDF on the quote - unless the tenant switched it off."""
        quote, order = state["quote"], state["order"]
        if not quote_documents.enabled(db, tenant_id, "agreement"):
            return {"agreement": "off"}
        number = None
        try:
            with db.begin_nested():  # the agreement record is a bonus - the PDF and the order never depend on it
                from app.crm_core import om_service
                from app.models.order_mgmt import OmAgreement

                made = om_service.to_agreement(db, tenant_id, quote, term_months=order.contract_months or 12)
                row = db.get(OmAgreement, made["agreement_id"])
                row.order_id = order.id
                number = made["agreement_number"]
        except Exception:  # noqa: BLE001
            number = None
        doc = quote_documents.generate(db, tenant_id, quote, "agreement", user_id=actor(entities), order_id=order.id,
                                       extra={"order_id": order.id, "agreement_number": number})
        event_bus.publish(db, execution_id, "quote.agreement_generated",
                          {"quote_id": quote.id, "order_id": order.id, "document_id": doc.id, "agreement_number": number})
        return {"agreement_document_id": doc.id, "agreement_file_name": doc.file_name,
                **({"agreement_number": number} if number else {})}

    return [SagaStep("match_all", step1), SagaStep("auto_order", step2), SagaStep("agreement", step3)]


# ---------------------------------------------------------------- INT-41 consumer


def build_int41_steps(db: Session, tenant_id: int, execution_id: int, entities: dict[str, Any]) -> list[SagaStep]:
    """INT-41 - Add a consumer (B2C) customer."""

    def step1() -> dict[str, Any]:
        name = str(entities.get("name") or "").strip()
        if not name:
            raise ValueError("Give the customer's name")
        party = Party(tenant_id=tenant_id, name=name, party_type="individual",
                      email=entities.get("email") or None, phone=entities.get("phone") or None)
        party.profile = PartyProfile(segment="B2C", account_status="account")
        db.add(party)
        db.flush()
        event_bus.publish(db, execution_id, "consumer.added", {"party_id": party.id, "name": party.name})
        return {"party_id": party.id, "name": party.name, "segment": "B2C"}

    steps = [SagaStep("add_consumer", step1)]
    from app.orchestration import actions_prospect

    if actions_prospect.wants_order(entities):  # B2C direct order: the order comes straight after the customer
        steps.append(actions_prospect.b2c_order_step(db, tenant_id, execution_id, entities, steps[0]))
    return steps


# ---------------------------------------------------------------- INT-36 approve / reject


def _run(steps: list[SagaStep]) -> dict[str, Any]:
    merged: dict[str, Any] = {}
    for step in steps:
        merged.update(step.action() or {})
    return merged


def build_int36_steps(db: Session, tenant_id: int, execution_id: int, entities: dict[str, Any]) -> list[SagaStep]:
    """INT-36 - Approve or reject whatever approval a record is waiting for.
    A quote's discount, a lead's qualification and an order's order-control
    approval are decided by their own intents (INT-06/17/25), so the same
    business steps run whichever way the decision is made."""

    def step1() -> dict[str, Any]:
        from app.orchestration import actions_fulfillment, actions_l2q

        entity_type = lifecycle_service.normalize_entity_type(entities.get("entity_type")) or str(entities.get("entity_type") or "")
        try:
            entity_id = int(entities.get("entity_id"))
        except (TypeError, ValueError):
            raise ValueError("Pick the record to decide on") from None
        pending = [i for i in approval_service.latest_for(db, entity_type, entity_id)
                   if i.status == ApprovalStatus.pending and i.template and i.template.tenant_id == tenant_id]
        classic = approval_service.classic_waiting(db, tenant_id, entity_type, entity_id) if not pending else None
        if not pending and classic is None:
            raise ValueError(f"{entity_type.title()} {entity_id} is not waiting for any approval")
        gate_name = pending[-1].gate if pending else approval_service.CLASSIC_GATES[entity_type]
        instance = pending[-1] if pending else None
        decision = str(entities.get("decision") or "approve").strip().lower()
        decision = {"approved": "approve", "accept": "approve", "yes": "approve", "rejected": "reject",
                    "decline": "reject", "no": "reject"}.get(decision, decision)
        if decision not in ("approve", "reject"):
            raise ValueError("The decision is approve or reject")
        reasoning = entities.get("reasoning") or ""
        carried = {"decision": decision, "reasoning": reasoning, "_actor_user_id": entities.get("_actor_user_id")}
        if classic is not None:
            # Its approval opens on this first decision - and only someone the
            # first tier is for may make it.
            waiting = approval_service.waiting_on(db, tenant_id, entity_type, entity_id, actor(entities))
            if waiting is not None and not waiting["can_decide"]:
                raise ValueError(waiting["why"])
        if (entity_type, gate_name) == ("quote", "discount"):
            return {"decision": decision, **_run(actions_l2q.build_int06_steps(db, tenant_id, execution_id, {**carried, "quote_id": entity_id}))}
        if (entity_type, gate_name) == ("lead", "qualified"):
            return {"decision": decision, **_run(actions_l2q.build_int17_steps(db, tenant_id, execution_id, {**carried, "lead_id": entity_id}))}
        if (entity_type, gate_name) == ("order", "activating"):
            return {"decision": decision, **_run(actions_fulfillment.build_int25_steps(db, tenant_id, execution_id, {**carried, "order_id": entity_id}))}

        allowed, why = approval_service.can_decide(db, instance, actor(entities))
        if not allowed:
            raise ValueError(why)
        outcome = approval_service.record_step_decision(db, instance, decision, reasoning, actor(entities))
        result: dict[str, Any] = {"decision": decision, "entity_type": entity_type, "entity_id": entity_id, **outcome}
        if instance.status != ApprovalStatus.pending:
            handler = approval_service.decided_handler(instance)
            if handler is not None:
                result.update(handler(db, tenant_id, instance, execution_id) or {})
        event_bus.publish(db, execution_id, "approval.decided",
                          {"entity_type": entity_type, "entity_id": entity_id, "chain_status": outcome["chain_status"]})
        return result

    return [SagaStep("decide_approval", step1)]


# ---------------------------------------------------------------- what a decision leads to


def _account_decided(db: Session, tenant_id: int, instance: ApprovalChainInstance, execution_id) -> dict[str, Any]:
    party = db.get(Party, instance.entity_id)
    if instance.status == ApprovalStatus.approved:
        return _make_account(db, tenant_id, party)
    return {"party_id": party.id, "account_status": process_service.account_status(party) or "prospect"}


def _feasibility_decided(db: Session, tenant_id: int, instance: ApprovalChainInstance, execution_id) -> dict[str, Any]:
    opportunity = db.get(Opportunity, instance.entity_id)
    if instance.status == ApprovalStatus.approved:
        return mark_feasible(db, tenant_id, opportunity)
    opportunity.stage = OpportunityStage.feasibility_failed
    db.flush()
    process_service.hand_to_user(db, tenant_id, "opportunity", opportunity.id, process_service.account_manager_of(opportunity),
                                 "Feasibility sign-off refused - reconfigure", "process")
    return {"opportunity_id": opportunity.id, "stage": opportunity.stage.value}


def _consumer_order_decided(db: Session, tenant_id: int, instance: ApprovalChainInstance, execution_id) -> dict[str, Any]:
    from app.orchestration import actions_o2a

    order = db.get(Order, instance.entity_id)
    if instance.status == ApprovalStatus.approved:
        return _run(actions_o2a.build_int07_steps(db, tenant_id, execution_id, {"_approved_order_id": order.id}))
    order.status = OrderStatus.rejected
    db.flush()
    return {"order_id": order.id, "order_status": order.status.value}


def _state_decided(db: Session, tenant_id: int, instance: ApprovalChainInstance, execution_id) -> dict[str, Any]:
    """Any other record approval (an admin-chosen state, asked for through
    "Move to another stage"): approved moves the record there now."""
    if instance.status != ApprovalStatus.approved:
        return {"entity_type": instance.entity_type, "entity_id": instance.entity_id}
    obj = lifecycle_service.get_record(db, tenant_id, instance.entity_type, instance.entity_id)
    if obj is None:
        return {}
    return lifecycle_service.set_state(db, tenant_id, instance.entity_type, obj, instance.gate, f"approval:{instance.id}")


def _om_discount_decided(db: Session, tenant_id: int, instance: ApprovalChainInstance, execution_id) -> dict[str, Any]:
    from app.crm_core import om_service

    order = db.get(Order, instance.entity_id)
    return {"order_id": order.id, **om_service.discount_decided(db, tenant_id, order, instance.status.value)}


def _adjustment_decided(db: Session, tenant_id: int, instance: ApprovalChainInstance, execution_id) -> dict[str, Any]:
    from app.crm_core import comm_billing

    return comm_billing.adjustment_decided(db, tenant_id, instance)


def _arrangement_decided(db: Session, tenant_id: int, instance: ApprovalChainInstance, execution_id) -> dict[str, Any]:
    from app.crm_core import comm_billing

    return comm_billing.arrangement_decided(db, tenant_id, instance)


approval_service.on_decided("order", "om_discount", _om_discount_decided)
approval_service.on_decided("adjustment", "adjustment", _adjustment_decided)
approval_service.on_decided("arrangement", "arrangement", _arrangement_decided)
approval_service.on_decided("party", "account", _account_decided)
approval_service.on_decided("opportunity", "feasible", _feasibility_decided)
approval_service.on_decided("order", "discount", _consumer_order_decided)
approval_service.on_decided("*", "*", _state_decided)


# The intents this module carries out (app/capabilities.py).
BUILDERS = {
    "INT-35": build_int35_steps,
    "INT-36": build_int36_steps,
    "INT-37": build_int37_steps,
    "INT-38": build_int38_steps,
    "INT-39": build_int39_steps,
    "INT-40": build_int40_steps,
    "INT-41": build_int41_steps,
    "INT-109": build_int109_steps,
}

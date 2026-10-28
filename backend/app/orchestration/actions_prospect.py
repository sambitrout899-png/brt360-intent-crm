"""INT-15 - Add a prospect, and INT-46 - Generate a lead for a prospect.

The actual entry point of the customer journey, ahead of any Lead: a
Party can now exist as a bare prospect (party_type "prospect") with no
source/region/propensity data at all - those only make sense once
something is actually being sold to them, which is what "Qualify this
lead" (INT-04) still does, now optionally against an existing prospect
Party instead of always creating a brand new one.

New Connect journey: a prospect (B2B or B2C - its page in the Page
Designer decides which fields it has, which are mandatory and which rules
it must pass) -> a lead for it (INT-46), on which the Account Manager
records the products they are interested in from the catalog, without sales
eligibility restrictions -> qualification (INT-17) opens the opportunity. A prospect
becomes an account by itself when its first order is activated end to
end (process_service._prospects_become_accounts).
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.crm_core import lead_service, page_service, process_service
from app.data_intelligence import event_bus
from app.integration import party_adapter
from app.models.crm import Lead, LeadStatus, Party
from app.models.pages import LeadProduct
from app.orchestration.orchestrator import SagaStep


def _details(entities: dict[str, Any]) -> dict[str, Any]:
    details = entities.get("details")
    return dict(details) if isinstance(details, dict) else {}


def _actor(entities: dict[str, Any]) -> int | None:
    value = entities.get("_actor_user_id")
    return int(value) if value else None


def build_int15_steps(db: Session, tenant_id: int, execution_id: int, entities: dict[str, Any]) -> list[SagaStep]:
    name = entities["name"]
    email = entities.get("email")
    phone = entities.get("phone")
    segment = page_service.segment_of(entities.get("segment") or _details(entities).get("segment"))
    details = _details(entities)

    def check() -> dict[str, Any]:
        values = {**details, "name": name, "email": email, "phone": phone, "segment": segment}
        problems = page_service.validate(db, tenant_id, "party", segment, values, execution_id=execution_id)
        if problems:
            raise ValueError("; ".join(problems))
        return {"segment": segment, "page_checked": True}

    def step1() -> dict[str, Any]:
        party = party_adapter.get_or_create_party(db, tenant_id, name, party_type="prospect")
        if email:
            party.email = email
        if phone:
            party.phone = phone
        db.flush()
        if segment or details:
            profile = process_service.party_profile(party, create=True)
            profile.segment = segment or profile.segment
            profile.account_status = profile.account_status or "prospect"
            page_service.save_values(db, tenant_id, "party", party, {k: v for k, v in details.items() if k not in ("name", "segment")})
        event_bus.publish(db, execution_id, "prospect.added", {"party_id": party.id, "name": party.name})
        out = {"party_id": party.id, "name": party.name, "party_type": party.party_type}
        if segment:
            out["segment"] = segment
        started = page_service.fire_event(db, tenant_id, execution_id, "party", party, "after_save", details.get("_page_key"), segment,
                                          _actor(entities))
        if started:
            out["page_flows"] = started
        return out

    steps = [SagaStep("add_prospect", step1)] if segment is None and not details else \
        [SagaStep("check_prospect_page", check), SagaStep("add_prospect", step1)]
    if segment == "B2C" and wants_order(entities):
        steps.append(b2c_order_step(db, tenant_id, execution_id, entities, steps[-1]))
    return steps


def wants_order(entities: dict[str, Any]) -> bool:
    return bool(entities.get("products") or entities.get("offering_name"))


def b2c_order_step(db: Session, tenant_id: int, execution_id: int, entities: dict[str, Any], add_step: SagaStep) -> SagaStep:
    """B2C direct order: right after the prospect, the order (INT-104) - no
    lead, opportunity or quote. Runs only when the vanilla B2C journey is on."""
    from app.crm_core import b2c_order_service

    created: dict[str, Any] = {}
    original = add_step.action

    def add() -> dict[str, Any]:
        out = original()
        created.update(out)
        return out

    add_step.action = add

    def place() -> dict[str, Any]:
        if not b2c_order_service.direct(db, tenant_id):
            return {"next": "The B2C lead path is on for your organization - generate a lead for them"}
        order = b2c_order_service.place_order(db, tenant_id, {**entities, "party_id": created["party_id"]}, execution_id)
        return {**{k: v for k, v in order.items() if k != "party_id"}, "order_placed": True}

    return SagaStep("place_b2c_order", place)


def normalize_products(raw: Any) -> list[tuple[str, int]]:
    """[{"offering_name", "quantity"}] / ["Fiber 800"] / "Fiber 800 x 2, VOIP" -> [(name, qty)]."""
    items: list[tuple[str, int]] = []
    if isinstance(raw, str):
        raw = [p for p in raw.split(",")]
    for item in raw or []:
        if isinstance(item, dict):
            name = str(item.get("offering_name") or item.get("name") or "").strip()
            try:
                qty = max(1, int(float(item.get("quantity") or 1)))
            except (TypeError, ValueError):
                qty = 1
        else:
            text = str(item).strip()
            name, _, qty_text = text.rpartition(" x ")
            if not (name and qty_text.strip().isdigit()):
                name, qty_text = text, "1"
            qty = max(1, int(qty_text))
        if name and name.lower() not in {n.lower() for n, _ in items}:
            items.append((name.strip(), qty))
    return items


def check_products(db: Session, tenant_id: int, party: Party, products: list[tuple[str, int]]) -> list[tuple[str, int]]:
    segment = process_service.party_segment(party)
    sold = page_service.offerings_for_segment(db, tenant_id, segment)
    by_lower = {s.lower(): s for s in sold}
    out = []
    for name, qty in products:
        match = by_lower.get(name.lower())
        if match is None:
            who = f"{segment} customers" if segment else "customers"
            raise ValueError(f"'{name}' isn't an active product sold to {who} - pick from: {', '.join(sold) or 'none yet'}")
        out.append((match, qty))
    from app.crm_core import product_model_service

    _, why = product_model_service.eligible(db, tenant_id, [n for n, _ in out], party)
    if why:
        raise ValueError("; ".join(why.values()))
    return out


def select_lead_products(db: Session, tenant_id: int, products: list[tuple[str, int]]) -> list[tuple[str, int]]:
    """Record interest in catalog products without applying sales eligibility."""
    from app.models.catalog import OfferingStatus, ProductOffering

    rows = db.query(ProductOffering).filter(
        ProductOffering.tenant_id == tenant_id, ProductOffering.status != OfferingStatus.retired
    ).all()
    names = {row.name.lower(): row.name for row in rows}
    selected = []
    for name, quantity in products:
        if name.lower() not in names:
            raise ValueError(f"'{name}' isn't in the product catalog - select a product from the list")
        selected.append((names[name.lower()], quantity))
    return selected


def add_lead_products(db: Session, lead: Lead, products: list[tuple[str, int]]) -> list[dict[str, Any]]:
    existing = {p.offering_name.lower(): p for p in db.query(LeadProduct).filter(LeadProduct.lead_id == lead.id)}
    position = len(existing)
    for name, qty in products:
        row = existing.get(name.lower())
        if row is None:
            db.add(LeadProduct(lead_id=lead.id, offering_name=name, quantity=qty, position=position))
            position += 1
        else:
            row.quantity = qty
    db.flush()
    rows = db.query(LeadProduct).filter(LeadProduct.lead_id == lead.id).order_by(LeadProduct.position, LeadProduct.id).all()
    if rows and not lead.interested_offering_name:
        lead.interested_offering_name = rows[0].offering_name
    return [{"offering_name": r.offering_name, "quantity": r.quantity} for r in rows]


def build_int46_steps(db: Session, tenant_id: int, execution_id: int, entities: dict[str, Any]) -> list[SagaStep]:
    """INT-46 - Generate a lead for a prospect, with the products they're interested in."""

    state: dict[str, Any] = {}
    details = _details(entities)

    def step1() -> dict[str, Any]:
        party = db.get(Party, entities.get("party_id") or 0)
        if party is None or party.tenant_id != tenant_id:
            raise ValueError("Pick the prospect the lead is for")
        from app.crm_core import b2c_order_service

        b2c_order_service.guard(db, tenant_id, party, "lead")
        products = select_lead_products(db, tenant_id, normalize_products(entities.get("products")))
        segment = process_service.party_segment(party)
        values = {**details, "source": entities.get("source") or details.get("source"), "region": entities.get("region") or details.get("region")}
        problems = page_service.validate(db, tenant_id, "lead", segment, values, execution_id=execution_id)
        if problems:
            raise ValueError("; ".join(problems))
        state.update(party=party, products=products, segment=segment)
        return {"party_id": party.id, "segment": segment, "products": [n for n, _ in products]}

    def step2() -> dict[str, Any]:
        party = state["party"]
        region = entities.get("region") or details.get("region") or "ALL"
        enrichment = lead_service.enrich_lead(party.name)
        lead = Lead(
            tenant_id=tenant_id, party_id=party.id, source=entities.get("source") or details.get("source") or "direct", region=region,
            owner_name=lead_service.assign_territory_owner(db, tenant_id, region), propensity_score=lead_service.score_lead(enrichment),
            status=LeadStatus.pending_qualification,
        )
        db.add(lead)
        db.flush()
        products = add_lead_products(db, lead, state["products"])
        page_service.save_values(db, tenant_id, "lead", lead, {k: v for k, v in details.items() if k not in ("source", "region")})
        event_bus.publish(db, execution_id, "lead.generated", {"lead_id": lead.id, "party_id": party.id, "products": products})
        out = {"lead_id": lead.id, "party_id": party.id, "status": lead.status.value, "interested_products": products}
        started = page_service.fire_event(db, tenant_id, execution_id, "lead", lead, "after_save", details.get("_page_key"), state["segment"],
                                          _actor(entities))
        if started:
            out["page_flows"] = started
        return out

    return [SagaStep("check_lead", step1), SagaStep("generate_lead", step2)]


# The intents this module carries out (app/capabilities.py).
BUILDERS = {
    "INT-15": build_int15_steps,
    "INT-46": build_int46_steps,
}

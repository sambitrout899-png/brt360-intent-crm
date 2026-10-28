"""Product configuration intents (see app/crm_core/product_config_service.py).

    INT-42  Design product attributes   design time - plain English, the AI (or
                                         the rule parser) turns it into
                                         attributes, values, price impacts, rules
    INT-43  Add a product to a pricelist
    INT-44  Apply a pricelist            to a customer, an opportunity or a quote
                                         (the quote's own wins, then the
                                         opportunity's, then the customer's)
    INT-45  Set technical attributes     on an order - what provisioning needs

Pricing attributes are chosen with "Configure the product" (INT-37) and
priced into the quote (INT-05, re-priced by INT-19 / INT-44); technical
attributes ride along to the order, its work orders, provisioning and
inventory (fulfillment_ops).
"""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy.orm import Session

from app.crm_core import catalog_service, lifecycle_service, ponr_service, process_service
from app.crm_core import product_config_service as config
from app.data_intelligence import event_bus
from app.integration import tmf620_adapter
from app.models.assurance import WorkOrder
from app.models.catalog import ProductOffering
from app.models.crm import Opportunity, Party, Quote
from app.models.orders import Order, OrderStatus
from app.models.process import PriceListItem
from app.models.product_config import OrderItemConfig, QuoteItemConfig, QuoteTerms
from app.orchestration.orchestrator import SagaStep


def _offering(db: Session, tenant_id: int, name: Any) -> ProductOffering:
    """The product to design or price: on sale, or a draft not on sale yet
    (imported / created in the Product Modeller) - never a retired one."""
    from app.crm_core import product_model_service
    from app.models.catalog import OfferingStatus

    offering = tmf620_adapter.get_active_offering_by_name(db, tenant_id, str(name or "")) or \
        product_model_service.offering_of(db, tenant_id, str(name or ""))
    if offering is None or offering.status == OfferingStatus.retired:
        raise ValueError(f"No product named '{name}' that is on sale or in draft")
    return offering


# ---------------------------------------------------------------- quote pricing (shared)


def save_quote_item_config(db: Session, item, configured: dict[str, Any], price_list, base_source: str, technical: dict | None = None) -> None:
    row = db.query(QuoteItemConfig).filter(QuoteItemConfig.quote_item_id == item.id).first()
    if row is None:
        row = QuoteItemConfig(quote_item_id=item.id)
        db.add(row)
    row.pricing_attributes_json = json.dumps(configured["pricing"])
    if technical is not None or configured["technical"]:
        row.technical_attributes_json = json.dumps(technical if technical is not None else configured["technical"])
    row.price_breakdown_json = json.dumps({
        "price_list": price_list.name if price_list else None, "base_source": base_source, "base": configured["base"],
        "lines": configured["lines"], "configured": configured["configured"],
        # which released model version priced it (None = the product's workspace), one-time charges, recommendations
        "model_version": configured.get("model_version"), "one_time": configured.get("one_time", 0),
        "messages": configured.get("messages", []),
    })
    db.flush()


def reprice_quote(db: Session, tenant_id: int, quote: Quote, pricing_raw: Any = None, quantity: Any = None) -> dict[str, Any]:
    """Re-prices a quote's product from its configuration and pricelist:
    pricelist base + the chosen pricing attributes' impacts, then the
    quote's discount, times the quantity."""
    item = quote.items[0] if quote.items else None
    if item is None:
        raise ValueError(f"Quote {quote.id} has no product to price")
    offering = db.get(ProductOffering, item.product_offering_id)
    opportunity = db.get(Opportunity, quote.opportunity_id)
    party = opportunity.lead.party if opportunity is not None and opportunity.lead is not None else None
    row = db.query(QuoteItemConfig).filter(QuoteItemConfig.quote_item_id == item.id).first()
    # the quote's own frozen copy of the product (taken now from the opportunity's, or the catalog, if it has none yet)
    from app.crm_core import instance_service

    source = instance_service.get(db, tenant_id, "opportunity", quote.opportunity_id, offering.name)
    instance = instance_service.ensure(db, tenant_id, "quote", quote.id, offering.name, source, item.id)
    frozen = instance_service.design(instance)
    pricing = config.loads(row.pricing_attributes_json) if row else {}
    pricing.update(config.selection(db, tenant_id, offering.name, pricing_raw, frozen))
    # An attribute given empty is removed (and its price impact with it).
    if isinstance(pricing_raw, dict):
        for name, value in pricing_raw.items():
            if value in (None, ""):
                pricing = {k: v for k, v in pricing.items() if k.lower() != str(name).lower()}
    technical = config.loads(row.technical_attributes_json) if row else {}
    price_list = config.price_list_for(db, tenant_id, party, opportunity, quote)
    base, source = config.base_price(db, tenant_id, price_list, offering)
    configured = config.configure(db, tenant_id, offering.name, pricing, technical, base, party=party, price_list=price_list, design=frozen)
    if quantity not in (None, ""):
        item.quantity = int(quantity)
    instance_service.record(instance, configured, item.quantity)
    ceiling = offering.price_plan.discount_ceiling_pct if offering.price_plan else 20.0
    item.unit_price = catalog_service.compute_price(configured["configured"], quote.requested_discount_pct, ceiling, offering.price_floor)
    from app.crm_core import quote_override_service

    override_ended = quote_override_service.end_on_reprice(db, quote)  # negotiated against a price that no longer exists
    quote.total_price = round(item.unit_price * item.quantity, 2)
    save_quote_item_config(db, item, configured, price_list, source, technical)
    out = {"quote_id": quote.id, "base_price": base, "price_source": source, "configured_price": configured["configured"],
           "price_breakdown": configured["lines"], "unit_price": item.unit_price, "total_price": quote.total_price}
    return {**out, "price_override_ended": True} if override_ended else out


# ---------------------------------------------------------------- INT-42 design


def build_int42_steps(db: Session, tenant_id: int, execution_id: int, entities: dict[str, Any]) -> list[SagaStep]:
    """INT-42 - Design a product's attributes from a plain-English description."""

    def step1() -> dict[str, Any]:
        offering = _offering(db, tenant_id, entities.get("offering_name"))
        design = entities.get("design")
        if isinstance(design, list) and design:
            # The draft the user reviewed in the form (suggested / AI-edited / hand-edited).
            plan = {"ops": [op for op in design if isinstance(op, dict) and op.get("op")], "engine": "form"}
        else:
            instruction = str(entities.get("instruction") or (design if isinstance(design, str) else "") or "").strip()
            if not instruction:
                raise ValueError("Suggest attributes or describe what to add, change or remove")
            plan = config.plan_design(db, tenant_id, offering.name, instruction)
        if not plan["ops"]:
            raise ValueError("I couldn't turn that into attributes - try “Suggest attributes”, or e.g. "
                             "'Bandwidth: 100 Mbps +0, 1 Gbps +1500; technical: VLAN ID required number'")
        changes = config.apply_design(db, tenant_id, offering.name, plan["ops"])
        design = config.schema(db, tenant_id, offering.name, workspace=True)
        event_bus.publish(db, execution_id, "product.configuration_designed", {"offering_name": offering.name, "changes": changes})
        released = config.released_version(db, tenant_id, offering.name)
        extra = {"note": f"{offering.name} sells from released model v{released.version} - these changes are in its workspace "
                         "until you release the product model"} if released is not None else {}
        return {
            **extra,
            "offering_name": offering.name, "changes": changes, "engine": plan["engine"],
            "pricing_attributes": [a["name"] for a in design["attributes"] if a["kind"] == "pricing"],
            "technical_attributes": [a["name"] for a in design["attributes"] if a["kind"] == "technical"],
            "rules": len(design["rules"]),
        }

    return [SagaStep("design_product_attributes", step1)]


# ---------------------------------------------------------------- INT-43 pricelist item


def build_int43_steps(db: Session, tenant_id: int, execution_id: int, entities: dict[str, Any]) -> list[SagaStep]:
    """INT-43 - Put a product on a pricelist at a price."""

    def step1() -> dict[str, Any]:
        price_list = process_service.find_price_list(db, tenant_id, entities.get("price_list"))
        if price_list is None:
            raise ValueError(f"No active pricelist '{entities.get('price_list')}'")
        offering = _offering(db, tenant_id, entities.get("offering_name"))
        try:
            amount = float(entities.get("price"))
        except (TypeError, ValueError):
            raise ValueError("Give the monthly price for this product on the pricelist") from None
        item = db.query(PriceListItem).filter(PriceListItem.price_list_id == price_list.id,
                                              PriceListItem.offering_name.ilike(offering.name)).first()
        previous = item.price if item else None
        if item is None:
            item = PriceListItem(price_list_id=price_list.id, offering_name=offering.name, price=amount)
            db.add(item)
        item.price = amount
        db.flush()
        event_bus.publish(db, execution_id, "price_list.item_set", {"price_list": price_list.name, "offering_name": offering.name, "price": amount})
        return {"price_list": price_list.name, "offering_name": offering.name, "price": amount, "previous_price": previous,
                "list_price": offering.price}

    return [SagaStep("set_pricelist_price", step1)]


# ---------------------------------------------------------------- INT-44 apply a pricelist


def build_int44_steps(db: Session, tenant_id: int, execution_id: int, entities: dict[str, Any]) -> list[SagaStep]:
    """INT-44 - Apply a pricelist to a customer, an opportunity or a quote.
    A quote is re-priced on the spot (while it can still be revised)."""

    def step1() -> dict[str, Any]:
        entity_type = lifecycle_service.normalize_entity_type(entities.get("entity_type"))
        if entity_type not in ("party", "opportunity", "quote"):
            raise ValueError("A pricelist applies to a customer, an opportunity or a quote")
        obj = lifecycle_service.get_record(db, tenant_id, entity_type, entities.get("entity_id"))
        if obj is None:
            raise ValueError(f"{entity_type.title()} {entities.get('entity_id')} not found")
        price_list = process_service.find_price_list(db, tenant_id, entities.get("price_list"))
        if price_list is None:
            raise ValueError(f"No active pricelist '{entities.get('price_list')}'")
        result: dict[str, Any] = {"entity_type": entity_type, "entity_id": obj.id, "price_list": price_list.name}
        if entity_type == "party":
            process_service.party_profile(obj, create=True).price_list_id = price_list.id
        elif entity_type == "opportunity":
            process_service.opportunity_config(obj, create=True).price_list_id = price_list.id
        else:
            if ponr_service.has_reached_ponr(db, tenant_id, "quote", obj.status.value):
                raise ValueError(f"Quote {obj.id} is {obj.status.value} - make a new revision to change its pricelist")
            terms = db.query(QuoteTerms).filter(QuoteTerms.quote_id == obj.id).first()
            if terms is None:
                terms = QuoteTerms(quote_id=obj.id)
                db.add(terms)
            terms.price_list_id = price_list.id
            db.flush()
            result.update(reprice_quote(db, tenant_id, obj))
        db.flush()
        event_bus.publish(db, execution_id, "price_list.applied", {"entity_type": entity_type, "entity_id": obj.id, "price_list": price_list.name})
        return result

    return [SagaStep("apply_pricelist", step1)]


# ---------------------------------------------------------------- INT-45 technical attributes


def build_int45_steps(db: Session, tenant_id: int, execution_id: int, entities: dict[str, Any]) -> list[SagaStep]:
    """INT-45 - Set the technical attributes an order's product needs for
    provisioning. They update its open installation work order too."""

    def step1() -> dict[str, Any]:
        order = db.get(Order, int(entities.get("order_id") or 0))
        if order is None or order.tenant_id != tenant_id:
            raise ValueError(f"Order {entities.get('order_id')} not found")
        if order.status in (OrderStatus.active, OrderStatus.cancelled, OrderStatus.terminated):
            raise ValueError(f"Order {order.id} is {order.status.value} - its technical attributes can't change now")
        items = order.items
        if entities.get("offering_name"):
            items = [i for i in items if (o := db.get(ProductOffering, i.product_offering_id)) and o.name.lower() == str(entities["offering_name"]).lower()]
        if not items:
            raise ValueError("That order has no such product")
        item = items[0]
        offering = db.get(ProductOffering, item.product_offering_id)
        row = db.query(OrderItemConfig).filter(OrderItemConfig.order_item_id == item.id).first()
        if row is None:
            row = OrderItemConfig(order_item_id=item.id)
            db.add(row)
        pricing = config.loads(row.pricing_attributes_json)
        from app.crm_core import instance_service

        instance = instance_service.get(db, tenant_id, "order", order.id, offering.name)
        frozen = instance_service.design(instance)
        given = config.check_technical(db, tenant_id, offering.name, entities.get("technical_attributes"), frozen)
        if not given:
            raise ValueError("Give at least one technical attribute, e.g. 'VLAN ID: 120'")
        technical = {**config.loads(row.technical_attributes_json), **given}
        row.technical_attributes_json = json.dumps(technical)
        db.flush()
        open_wo = (db.query(WorkOrder).filter(WorkOrder.order_id == order.id, WorkOrder.kind == "installation", WorkOrder.status != "complete")
                   .order_by(WorkOrder.id.desc()).first())
        if open_wo is not None:
            open_wo.technical_parameters = config.technical_text(technical)
        missing = config.missing_technical(db, tenant_id, offering.name, pricing, technical, frozen)
        if instance is not None:
            instance_service.set_technical(instance, technical)
        event_bus.publish(db, execution_id, "order.technical_attributes_set", {"order_id": order.id, "attributes": technical})
        return {"order_id": order.id, "offering_name": offering.name, "technical_attributes": technical,
                "still_missing": missing, "work_order_updated": open_wo.id if open_wo else None}

    return [SagaStep("set_technical_attributes", step1)]


def party_of(db: Session, opportunity: Opportunity | None) -> Party | None:
    return opportunity.lead.party if opportunity is not None and opportunity.lead is not None else None


# The intents this module carries out (app/capabilities.py).
BUILDERS = {
    "INT-42": build_int42_steps,
    "INT-43": build_int43_steps,
    "INT-44": build_int44_steps,
    "INT-45": build_int45_steps,
}

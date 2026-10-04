"""Context Assembly Service (HLD 4.2).

Fans out to the systems of record relevant to the resolved intent and
returns one normalized context object, before any plan is proposed. HLD
specifies a parallel-fetch aggregator with a 60-120s short-lived cache to
avoid redundant calls within one intent execution; Sprint 1's "systems of
record" are all local-DB reads (no external OSS/BRM yet), so the cache here
is a simple in-process TTL dict - the interface is what matters for later
sprints, when some of these fetches become real outbound calls.
"""

from __future__ import annotations

import time
from typing import Any

from sqlalchemy.orm import Session

from app.integration import tmf620_adapter
from app.models.assurance import TroubleTicket, WorkOrder
from app.models.catalog import BundleCompatibilityRule, ProductOffering
from app.models.crm import Opportunity, Quote, TerritoryRule
from app.models.orders import Order

_TTL_SECONDS = 90
_cache: dict[str, tuple[float, Any]] = {}


def _cached(key: str, fetch) -> Any:
    now = time.time()
    hit = _cache.get(key)
    if hit and (now - hit[0]) < _TTL_SECONDS:
        return hit[1]
    value = fetch()
    _cache[key] = (now, value)
    return value


def assemble_context(db: Session, intent_type: str, entities: dict[str, Any]) -> dict[str, Any]:
    context: dict[str, Any] = {"intent_type": intent_type}

    if intent_type == "INT-01":
        segment = entities.get("segment", "B2C")
        context["compatibility_rules"] = _cached(
            "compat_rules",
            lambda: [
                {"primary": r.primary_product_line, "addon": r.addon_product_line, "max_addons": r.max_addons}
                for r in db.query(BundleCompatibilityRule).all()
            ],
        )
        context["existing_offerings_in_segment"] = [
            o.name for o in db.query(ProductOffering).filter(ProductOffering.segment == segment).all()
        ]

    elif intent_type in ("INT-02", "INT-03"):
        offering_name = entities.get("offering_name", "")
        current = tmf620_adapter.get_active_offering_by_name(db, offering_name) if offering_name else None
        context["current_offering"] = (
            {
                "id": current.id,
                "version": current.version,
                "price": current.price,
                "segment": current.segment,
                "composition": current.composition,
            }
            if current
            else None
        )
        if intent_type == "INT-03":
            replacement_name = entities.get("replacement_offering_name")
            replacement = tmf620_adapter.get_active_offering_by_name(db, replacement_name) if replacement_name else None
            context["replacement_offering"] = {"id": replacement.id, "name": replacement.name} if replacement else None

    elif intent_type == "INT-04":
        region = entities.get("region", "ALL")
        rule = db.query(TerritoryRule).filter(TerritoryRule.region == region).first()
        context["territory_owner"] = rule.owner_name if rule else None

    elif intent_type == "INT-05":
        opportunity = db.get(Opportunity, entities.get("opportunity_id")) if entities.get("opportunity_id") else None
        context["opportunity"] = {"id": opportunity.id, "stage": opportunity.stage.value} if opportunity else None
        offering = tmf620_adapter.get_active_offering_by_name(db, entities.get("offering_name", ""))
        context["offering"] = (
            {"id": offering.id, "price": offering.price, "price_floor": offering.price_floor} if offering else None
        )

    elif intent_type in ("INT-06", "INT-07"):
        quote = db.get(Quote, entities.get("quote_id")) if entities.get("quote_id") else None
        context["quote"] = (
            {"id": quote.id, "status": quote.status.value, "total_price": quote.total_price, "discount_pct": quote.requested_discount_pct}
            if quote
            else None
        )

    elif intent_type in ("INT-08", "INT-09"):
        order = db.get(Order, entities.get("order_id")) if entities.get("order_id") else None
        context["order"] = (
            {"id": order.id, "status": order.status.value, "monthly_price": order.monthly_price, "contract_months": order.contract_months}
            if order
            else None
        )

    elif intent_type == "INT-11":
        ticket = db.get(TroubleTicket, entities.get("ticket_id")) if entities.get("ticket_id") else None
        context["ticket"] = {"id": ticket.id, "status": ticket.status, "severity": ticket.severity} if ticket else None

    elif intent_type == "INT-12":
        if entities.get("work_order_id"):
            wo = db.get(WorkOrder, entities.get("work_order_id"))
            context["work_order"] = {"id": wo.id, "status": wo.status} if wo else None
        else:
            ticket = db.get(TroubleTicket, entities.get("ticket_id")) if entities.get("ticket_id") else None
            order = db.get(Order, entities.get("order_id")) if entities.get("order_id") else None
            context["ticket"] = {"id": ticket.id, "product_line": ticket.product_line} if ticket else None
            context["order"] = {"id": order.id} if order else None

    return context

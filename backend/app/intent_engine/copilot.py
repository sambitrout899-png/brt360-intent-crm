"""The command bar's copilot: does the tedious parts of running an intent.

prepare() - before anything runs, for one intent and whatever the user
has said or typed so far:
  * fills every field it can, and says where each value came from - what
    the user typed or said (read by the same classifier as a submission,
    LLM when configured), the record they are looking at and the records
    linked to it, the admin's defaults, the values this user usually
    picks, or the only sensible candidate (the one open quote awaiting
    approval, the one active product, ...);
  * resolves the options for every picklist (products, regions, users,
    positions, the order's sites, the record's allowed next states) and
    suggests records for every record field, best candidates first;
  * runs the real context + reasoning checks without executing, so
    blockers ("this quote isn't approved yet") show before Run - with the
    intent that would fix them, prefilled;
  * phrases the whole thing as one plain sentence to confirm.

lookup() - typeahead for record fields, limited to what the user's
position may see.

outcome() - after a run: cards for every record created or changed (with
their data and state change), a one-line summary, and the recommended
next steps, prefilled.

Everything here only reads; nothing is executed or logged.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from typing import Any, Callable

from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.crm_core import access_service, lifecycle_service, record_service
from app.crm_core.b2c_order_service import is_b2c
from app.crm_core.access_service import Access
from app.intent_engine import abo_intents, b2c_intents, classifier, comm_intents, config_intents, context_assembly, intent_registry, om_intents, fulfillment_intents, inventory_intents, quote_intents, reasoning, sto_intents, understanding
from app.models.assurance import TroubleTicket, WorkOrder
from app.models.auth import User
from app.models.catalog import OfferingStatus, ProductOffering
from app.models.crm import Lead, Opportunity, Party, Quote, TerritoryRule
from app.models.fulfillment import Proposal
from app.models.identity import IdentityVerificationProvider
from app.models.intent_log import IntentExecutionLog
from app.models.lifecycle import Position
from app.models.orders import Order, ServiceOrder

ENTITY_LABELS = {"party": "Customer", "lead": "Lead", "opportunity": "Opportunity", "quote": "Quote", "order": "Order",
                 "ticket": "Ticket", "work_order": "Work order", "proposal": "Proposal", "product_offering": "Product"}
RECORD_MODELS: dict[str, Any] = {"party": Party, "lead": Lead, "opportunity": Opportunity, "quote": Quote, "order": Order,
                                 "ticket": TroubleTicket, "work_order": WorkOrder, "proposal": Proposal,
                                 "product_offering": ProductOffering}
LIFECYCLE_TYPES = set(lifecycle_service.ENTITY_MODELS)

ORIGIN_NOTES = {
    "you": "entered by you",
    "understood": "understood from what you wrote",
    "context": "from the record you are viewing",
    "linked": "from the record you picked",
    "default": "default",
    "usual": "what you usually pick",
    "guess": "best guess",
    "account": "what this customer usually has",
    "business": "what your business usually picks",
}

# Fields whose value is a habit worth remembering per user.
_REMEMBERED = {"source", "region", "segment", "channel", "sla_class", "required_skill", "id_type", "provider", "quantity"}
# ... and per customer / across the business (an account's usual discount, its region)
_HABITS = _REMEMBERED | {"requested_discount_pct", "priority", "industry", "term_months", "billing_cycle", "payment_method"}


# ---------------------------------------------------------------- records


def _state(db: Session, tenant_id: int, record_type: str, obj) -> str:
    if record_type in LIFECYCLE_TYPES:
        return lifecycle_service.effective_state(db, tenant_id, record_type, obj)
    status = getattr(obj, "status", "")
    return getattr(status, "value", status) or ""


def record_label(db: Session, tenant_id: int, record_type: str, obj) -> tuple[str, str]:
    """(label, hint) - how a record reads in a picker or a card title."""
    state = _state(db, tenant_id, record_type, obj).replace("_", " ")
    if record_type == "party":
        return obj.name, f"{(obj.party_type or '').replace('_', ' ')} · #{obj.id}".strip(" ·")
    if record_type == "lead":
        party = db.get(Party, obj.party_id)
        return (party.name if party else f"Lead #{obj.id}"), f"Lead #{obj.id} · {state}"
    if record_type == "opportunity":
        return obj.name, f"Opportunity #{obj.id} · {state}"
    if record_type == "quote":
        opp = db.get(Opportunity, obj.opportunity_id)
        return f"Quote #{obj.id}" + (f" · {opp.name}" if opp else ""), f"{state} · {obj.total_price:,.2f}"
    if record_type == "order":
        party = db.get(Party, obj.party_id)
        return f"Order #{obj.id}" + (f" · {party.name}" if party else ""), f"{state} · {obj.monthly_price:,.2f}/mo"
    if record_type == "ticket":
        return f"Ticket #{obj.id} · {obj.category}", f"{state} · {obj.severity} · {(obj.description or '')[:40]}"
    if record_type == "work_order":
        return f"Work order #{obj.id} · {obj.kind.replace('_', ' ')}", state
    if record_type == "proposal":
        return f"Proposal #{obj.id} · Quote #{obj.quote_id}", state
    if record_type == "product_offering":
        return obj.name, f"{state} · {obj.segment}"
    return f"#{obj.id}", state


def _get(db: Session, tenant_id: int, record_type: str, record_id: Any):
    model = RECORD_MODELS.get(record_type)
    try:
        obj = db.get(model, int(record_id)) if model is not None else None
    except (TypeError, ValueError):
        return None
    return obj if obj is not None and getattr(obj, "tenant_id", None) == tenant_id else None


def _visible(db: Session, access: Access, record_type: str, obj) -> bool:
    if record_type in LIFECYCLE_TYPES:
        return access_service.can_see(db, access, record_type, obj.id)
    return True


# Which records make sense for an intent's record field - best candidates first.
_SUITABLE: dict[tuple[str, str], Callable[[Any], bool]] = {
    ("INT-05", "opportunity_id"): lambda o: o.stage.value not in ("won", "lost"),
    ("INT-18", "opportunity_id"): lambda o: o.stage.value not in ("won", "lost"),
    ("INT-06", "quote_id"): lambda q: q.status.value in ("draft", "pending_approval"),
    ("INT-19", "quote_id"): lambda q: q.status.value in ("draft", "pending_approval"),
    ("INT-20", "quote_id"): lambda q: q.status.value in ("draft", "pending_approval"),
    ("INT-116", "quote_id"): lambda q: q.status.value == "draft",
    ("INT-07", "quote_id"): lambda q: q.status.value == "approved",
    ("INT-22", "quote_id"): lambda q: q.status.value == "approved",
    ("INT-24", "quote_id"): lambda q: q.status.value == "approved",
    ("INT-30", "quote_id"): lambda q: q.status.value in ("approved", "rejected"),
    ("INT-23", "proposal_id"): lambda p: p.status.value == "sent",
    ("INT-25", "order_id"): lambda o: o.status.value == "created",
    ("INT-21", "order_id"): lambda o: o.status.value in ("created", "activating"),
    ("INT-14", "order_id"): lambda o: o.status.value == "activating",
    ("INT-08", "order_id"): lambda o: o.status.value in ("active", "changed"),
    ("INT-09", "order_id"): lambda o: o.status.value in ("active", "changed", "suspended", "pending_cancellation"),
    ("INT-11", "ticket_id"): lambda t: t.status != "resolved",
    ("INT-12", "ticket_id"): lambda t: t.status != "resolved",
    ("INT-16", "lead_id"): lambda lead: lead.status.value != "rejected",
    ("INT-17", "lead_id"): lambda lead: lead.status.value == "pending_qualification",
    ("INT-35", "party_id"): lambda p: p.profile is None or p.profile.account_status != "account",
    ("INT-37", "opportunity_id"): lambda o: o.stage.value in ("draft", "qualified", "configured", "feasibility_failed"),
    ("INT-38", "opportunity_id"): lambda o: o.stage.value in ("configured", "feasibility_failed"),
    ("INT-39", "quote_id"): lambda q: q.status.value == "approved" and q.purchase_order is None,
    ("INT-40", "quote_id"): lambda q: q.purchase_order is not None and q.purchase_order.matched_at is None,
    ("INT-109", "quote_id"): lambda q: q.status.value == "approved" and (q.purchase_order is None or q.purchase_order.matched_at is None),
    ("INT-07", "party_id"): is_b2c,
    ("INT-104", "party_id"): is_b2c,
    ("INT-26", "work_order_id"): lambda w: w.kind == "installation" and w.status != "complete",
    ("INT-27", "work_order_id"): lambda w: w.kind == "connectivity_test" and w.status != "complete",
    ("INT-28", "work_order_id"): lambda w: w.kind == "acceptance_letter" and w.status != "complete",
}


def _search(db: Session, tenant_id: int, record_type: str, q: str):
    model = RECORD_MODELS[record_type]
    query = db.query(model).filter(model.tenant_id == tenant_id)
    q = (q or "").strip().lstrip("#")
    if q.isdigit():
        return query.filter(model.id == int(q))
    if not q:
        return query
    like = f"%{q}%"
    if record_type == "party":
        return query.filter(model.name.ilike(like))
    if record_type == "lead":
        return query.join(Party, Party.id == Lead.party_id).filter(Party.name.ilike(like))
    if record_type == "opportunity":
        return query.filter(model.name.ilike(like))
    if record_type == "quote":
        return query.join(Opportunity, Opportunity.id == Quote.opportunity_id).filter(Opportunity.name.ilike(like))
    if record_type == "order":
        return query.join(Party, Party.id == Order.party_id).filter(Party.name.ilike(like))
    if record_type == "ticket":
        return query.filter(or_(model.description.ilike(like), model.category.ilike(like)))
    if record_type == "work_order":
        return query.filter(model.kind.ilike(like))
    if record_type == "product_offering":
        return query.filter(model.name.ilike(like))
    return query.filter(model.id == -1)


def lookup(
    db: Session, tenant_id: int, access: Access, record_type: str, q: str = "", intent_code: str | None = None,
    field_key: str | None = None, limit: int = 8,
) -> list[dict[str, Any]]:
    if record_type not in RECORD_MODELS:
        return []
    model = RECORD_MODELS[record_type]
    query = _search(db, tenant_id, record_type, q)
    if record_type in LIFECYCLE_TYPES:
        query = access_service.apply(query, model, db, access, record_type)
    rows = query.order_by(model.id.desc()).limit(200).all()
    suitable = _SUITABLE.get((intent_code or "", field_key or ""))
    if suitable:
        rows = sorted(rows, key=lambda r: 0 if suitable(r) else 1)  # stable: newest first within each group
    out = []
    for obj in rows[:limit]:
        label, hint = record_label(db, tenant_id, record_type, obj)
        out.append({"id": obj.id, "label": label, "hint": hint, "suggested": bool(suitable and suitable(obj))})
    return out


def linked_values(db: Session, tenant_id: int, values: dict[str, Any]) -> dict[str, Any]:
    """What a chosen record already says about the fields an intent asks
    for - the opportunity's product and quantity for its quote."""
    out: dict[str, Any] = {}
    opp = _get(db, tenant_id, "opportunity", values.get("opportunity_id")) if values.get("opportunity_id") else None
    if opp is not None:
        if opp.offering_name:
            out["offering_name"] = opp.offering_name
        out["quantity"] = opp.quantity or 1
    lead = _get(db, tenant_id, "lead", values.get("lead_id") or (opp.lead_id if opp else None))
    if lead is not None and lead.interested_offering_name:
        out.setdefault("offering_name", lead.interested_offering_name)
    return out


def fill_from_records(db: Session, tenant_id: int, code: str | None, entities: dict[str, Any]) -> list[str]:
    """Submission-time version of linked_values: "quote opportunity 6" uses
    the opportunity's product. Mutates entities; returns what it filled."""
    entry = intent_registry.entry(db, tenant_id, code)
    if entry is None:
        return []
    keys = {f["key"] for f in entry["fields"]}
    filled = []
    for key, value in linked_values(db, tenant_id, entities).items():
        if key in keys and _empty(entities.get(key)):
            entities[key] = value
            filled.append(key)
    return filled


def related_ids(db: Session, tenant_id: int, entity_type: str | None, entity_id: Any) -> dict[str, Any]:
    """The viewed record and the records linked to it, as entity keys -
    viewing a quote also tells us its opportunity, customer, proposal and
    order. Latest first where there are several."""
    obj = lifecycle_service.get_record(db, tenant_id, entity_type, entity_id) if entity_type else None
    if obj is None:
        return {}
    out: dict[str, Any] = {f"{entity_type}_id": obj.id}

    def latest(model, *conditions):
        return db.query(model).filter(model.tenant_id == tenant_id, *conditions).order_by(model.id.desc()).first()

    party = lead = opp = quote = order = None
    if entity_type == "party":
        party = obj
        lead = latest(Lead, Lead.party_id == obj.id)
        opp = latest(Opportunity, Opportunity.lead_id == lead.id) if lead else None
        quote = latest(Quote, Quote.opportunity_id == opp.id) if opp else None
        order = latest(Order, Order.party_id == obj.id)
    elif entity_type == "lead":
        lead = obj
        party = db.get(Party, obj.party_id)
        opp = latest(Opportunity, Opportunity.lead_id == obj.id)
        quote = latest(Quote, Quote.opportunity_id == opp.id) if opp else None
    elif entity_type == "opportunity":
        opp = obj
        lead = db.get(Lead, obj.lead_id)
        party = db.get(Party, lead.party_id) if lead else None
        quote = latest(Quote, Quote.opportunity_id == obj.id)
    elif entity_type == "quote":
        quote = obj
        opp = db.get(Opportunity, obj.opportunity_id)
        lead = db.get(Lead, opp.lead_id) if opp else None
        party = db.get(Party, lead.party_id) if lead else None
        order = latest(Order, Order.quote_id == obj.id)
    elif entity_type == "order":
        order = obj
        party = db.get(Party, obj.party_id)
        quote = db.get(Quote, obj.quote_id) if obj.quote_id else None
    for key, rec in (("party_id", party), ("lead_id", lead), ("opportunity_id", opp), ("quote_id", quote), ("order_id", order)):
        if rec is not None:
            out.setdefault(key, rec.id)
    if party is not None:
        out.setdefault("company_name", party.name)
    for key, value in linked_values(db, tenant_id, out).items():
        out.setdefault(key, value)
    if quote is not None:
        proposal = latest(Proposal, Proposal.quote_id == quote.id)
        if proposal:
            out.setdefault("proposal_id", proposal.id)
    if order is not None:
        wo = latest(WorkOrder, WorkOrder.order_id == order.id, WorkOrder.status != "complete")
        if wo:
            out.setdefault("work_order_id", wo.id)
    return out


# ---------------------------------------------------------------- options


def _options(db: Session, tenant_id: int, user: User, spec: dict[str, Any], values: dict[str, Any]) -> list[dict[str, Any]]:
    source = spec.get("source") or ""
    if spec["type"] == "transition":
        entity_type = lifecycle_service.normalize_entity_type(values.get("entity_type"))
        obj = lifecycle_service.get_record(db, tenant_id, entity_type, values.get("entity_id")) if entity_type else None
        if obj is None:
            return []
        return [
            {"value": t["to_state"], "label": t["to_label"], "hint": t["action"].replace("_", " "),
             "via": intent_registry.label_of(db, tenant_id, t["intent_type"]) if t["intent_type"] else None}
            for t in lifecycle_service.available_transitions(db, tenant_id, entity_type, obj, user.role.value, user.id)
        ]
    if not source:
        return [{"value": v, "label": v.replace("_", " ")} for v in spec.get("values") or []]
    if source == "@offerings":
        rows = db.query(ProductOffering).filter(ProductOffering.tenant_id == tenant_id, ProductOffering.status == OfferingStatus.active)
        party = _party_of_values(db, tenant_id, values) if user.role.value != "admin" else None
        if party is not None:
            # a business user picking a product for a customer: only what that customer may buy
            from app.crm_core import page_service, process_service, product_model_service

            allowed, _ = product_model_service.eligible(
                db, tenant_id, page_service.offerings_for_segment(db, tenant_id, process_service.party_segment(party)), party)
            rows = rows.filter(ProductOffering.name.in_(allowed or ["-"]))
        return [{"value": o.name, "label": o.name, "hint": f"{o.segment} · {o.price:,.2f}"} for o in rows.order_by(ProductOffering.name)]
    if source == "@offerings_all" and spec["type"] == "products" and user.role.value != "admin":
        # business users only ever see what the prospect may buy (published, their segment, the product rules);
        # administrators keep the whole catalog, drafts included
        source = "@segment_offerings"
    if source == "@offerings_all":
        # on sale and drafts (designing / pricing a product before it is launched for sale)
        latest: dict[str, ProductOffering] = {}
        for o in db.query(ProductOffering).filter(ProductOffering.tenant_id == tenant_id, ProductOffering.status != OfferingStatus.retired) \
                .order_by(ProductOffering.version):
            if o.status == OfferingStatus.active or o.name not in latest:
                latest[o.name] = o
        return [{"value": o.name, "label": o.name, "hint": f"{o.segment} · {o.price:,.2f}" + ("" if o.status == OfferingStatus.active else " · draft")}
                for o in sorted(latest.values(), key=lambda o: o.name.lower())]
    if source == "@regions":
        regions = sorted({r.region for r in db.query(TerritoryRule).filter(TerritoryRule.tenant_id == tenant_id)} - {"ALL"})
        return [{"value": "ALL", "label": "All regions"}] + [{"value": r, "label": r} for r in regions]
    if source == "@users":
        users = db.query(User).filter(User.tenant_id == tenant_id, User.is_active.is_(True)).order_by(User.name)
        return [{"value": u.email, "label": u.name, "hint": u.email} for u in users]
    if source == "@positions":
        rows = db.query(Position).filter(Position.tenant_id == tenant_id, Position.is_active.is_(True)).order_by(Position.name)
        return [{"value": p.name, "label": p.name} for p in rows]
    if source == "@departments":
        from app.models.org import Department

        rows = db.query(Department).filter(Department.tenant_id == tenant_id, Department.is_active.is_(True)).order_by(Department.name)
        return [{"value": d.name, "label": d.name} for d in rows]
    if source == "@pages":
        from app.crm_core import page_service

        return [{"value": p["page_key"], "label": p["name"], "hint": p["page_key"]} for p in page_service.pages(db, tenant_id)]
    if source == "@flows":
        from app.orchestration import flow_engine

        return [{"value": flow_engine.custom_intent_code(f), "label": f.name, "hint": "Flow Studio"}
                for f in flow_engine.custom_intent_flows(db, tenant_id)]
    if source == "@entity_types":
        return [{"value": t, "label": ENTITY_LABELS[t]} for t in lifecycle_service.ENTITY_MODELS]
    if source == "@id_providers":
        rows = db.query(IdentityVerificationProvider).filter(IdentityVerificationProvider.tenant_id == tenant_id, IdentityVerificationProvider.is_active.is_(True))
        return [{"value": p.name, "label": p.name} for p in rows]
    if source == "@order_sites":
        if not values.get("order_id"):
            return []
        rows = db.query(ServiceOrder).filter(ServiceOrder.order_id == values["order_id"])
        return [{"value": so.site_label, "label": so.site_label, "hint": so.status.value} for so in rows]
    if source == "@price_lists":
        from app.models.process import PriceList

        rows = db.query(PriceList).filter(PriceList.tenant_id == tenant_id, PriceList.is_active.is_(True)).order_by(PriceList.name)
        return [{"value": p.name, "label": p.name,
                 "hint": ("default · " if p.is_default else "") + (f"{p.adjustment_pct:+g}% on list" if p.adjustment_pct else "list price")}
                for p in rows]
    if source.startswith("@offering_attributes:"):
        from app.crm_core import product_config_service as product_config

        kind = source.split(":", 1)[1]
        design = product_config.schema(db, tenant_id, _offering_in(db, tenant_id, values))
        if design["designed"]:
            out = []
            if kind == "pricing":
                # child products: how many (mandatory ones are always in)
                for c in design.get("components", []):
                    charge = f"{c['price']:,.2f}{'/month' if c['charge_type'] == 'recurring' else ' one-time'}" if c["charge_type"] and c["price"] else ""
                    hint = " · ".join(x for x in ("child product", "mandatory" if c["mandatory"] else "optional", charge,
                                                  f"under {c['parent']}" if c["parent"] else "") if x)
                    choices = [str(n) for n in range(c["min_qty"], c["max_qty"] + 1)] if c["max_qty"] > 1 else (["Yes"] if c["mandatory"] else ["Yes", "No"])
                    out.append({"value": c["name"], "label": " › ".join(c["path"]), "hint": hint, "values": choices})
            for a in design["attributes"]:
                if a["kind"] != kind:
                    continue
                impacts = ", ".join(
                    f"{i['value']} {'+' if i['amount'] >= 0 else ''}{i['amount']:g}{'%' if i['type'] == 'percent' else ''}"
                    if i["type"] != "per_unit" else f"{i['amount']:g} each" for i in a["price_impacts"]
                )
                span = f"{a['min']:g}-{a['max']:g}" if a.get("min") is not None and a.get("max") is not None else ""
                hint = " · ".join(x for x in ("required" if a["required"] else "", span, a["unit"] or "", impacts,
                                              f"with {a['component']}" if a.get("component") else "",
                                              f"filled by provisioning ({a['reference_type']})" if a.get("reference_type") else "") if x)
                label = f"{a['component']} › {a['name']}" if a.get("component") else a["name"]
                out.append({"value": a["name"], "label": label, "hint": hint, "values": a["values"]})
            return out
        if kind == "technical":
            return []
        source = "@attributes"  # no design yet: the tenant's general attribute list
    if source == "@segment_offerings":
        # The products sold to the prospect's segment (B2B / B2C) - unknown segment: all active products.
        from app.crm_core import page_service, process_service

        party = _get(db, tenant_id, "party", values.get("party_id")) if values.get("party_id") else None
        if party is None and values.get("lead_id"):
            lead = _get(db, tenant_id, "lead", values.get("lead_id"))
            party = lead.party if lead is not None else None
        segment = process_service.party_segment(party) or page_service.segment_of(values.get("segment"))
        names = page_service.offerings_for_segment(db, tenant_id, segment)
        if party is not None:
            from app.crm_core import product_model_service

            names, _ = product_model_service.eligible(db, tenant_id, names, party)
        prices = {o.name: o for o in db.query(ProductOffering).filter(
            ProductOffering.tenant_id == tenant_id, ProductOffering.status == OfferingStatus.active, ProductOffering.name.in_(names or ["-"]))}
        return [{"value": n, "label": n, "hint": f"{prices[n].segment} · {prices[n].price:,.2f}" if n in prices else ""} for n in names]
    if source == "@attributes":
        from app.models.attributes import ProductAttributeDefinition

        rows = db.query(ProductAttributeDefinition).filter(ProductAttributeDefinition.tenant_id == tenant_id).order_by(ProductAttributeDefinition.name)
        return [{"value": a.name, "label": a.name, "hint": a.unit or "",
                 "values": [v.strip() for v in (a.allowed_values or "").split(",") if v.strip()]} for a in rows]
    return []


def _party_of_values(db: Session, tenant_id: int, values: dict[str, Any]):
    """The customer a form is about - named, or that of its lead / opportunity / quote."""
    if values.get("party_id"):
        return _get(db, tenant_id, "party", values["party_id"])
    lead = _get(db, tenant_id, "lead", values["lead_id"]) if values.get("lead_id") else None
    opp = _get(db, tenant_id, "opportunity", values["opportunity_id"]) if values.get("opportunity_id") else None
    if opp is None and values.get("quote_id"):
        quote = _get(db, tenant_id, "quote", values["quote_id"])
        opp = quote.opportunity if quote is not None else None
    lead = lead or (opp.lead if opp is not None else None)
    return lead.party if lead is not None else None


def _offering_in(db: Session, tenant_id: int, values: dict[str, Any]) -> str | None:
    """The product a form is about - named, or that of its opportunity / quote / order."""
    if values.get("asset_operation") == "product_change" and values.get("offering_name"):
        return str(values["offering_name"])  # configuring the product the service moves to
    if values.get("asset_id"):
        from app.models.fulfillment import Asset
        asset = db.get(Asset, int(values["asset_id"]))
        if asset and asset.tenant_id == tenant_id:
            return asset.asset_type
    if values.get("offering_name"):
        return str(values["offering_name"])
    opp = _get(db, tenant_id, "opportunity", values.get("opportunity_id")) if values.get("opportunity_id") else None
    if opp is not None and opp.offering_name:
        return opp.offering_name
    for record_type, key in (("quote", "quote_id"), ("order", "order_id")):
        obj = _get(db, tenant_id, record_type, values.get(key)) if values.get(key) else None
        if obj is not None and obj.items:
            offering = db.get(ProductOffering, obj.items[0].product_offering_id)
            if offering is not None:
                return offering.name
    return None


def _resolve_record_type(spec: dict[str, Any], values: dict[str, Any]) -> str | None:
    record_type = spec.get("record_type") or ""
    if record_type == "@entity_type":
        return lifecycle_service.normalize_entity_type(values.get("entity_type"))
    return record_type or None


# ---------------------------------------------------------------- prepare


def _remembered(db: Session, tenant_id: int, user: User, code: str) -> dict[str, Any]:
    logs = (
        db.query(IntentExecutionLog)
        .filter(IntentExecutionLog.tenant_id == tenant_id, IntentExecutionLog.user_id == user.id,
                IntentExecutionLog.intent_type == code, IntentExecutionLog.status == "executed")
        .order_by(IntentExecutionLog.id.desc())
        .limit(20)
        .all()
    )
    counts: dict[str, Counter] = {}
    for log in logs:
        try:
            entities = json.loads(log.entities_json or "{}")
        except json.JSONDecodeError:
            continue
        for key, value in entities.items():
            if key in _REMEMBERED and value not in (None, "", [], {}):
                counts.setdefault(key, Counter())[json.dumps(value)] += 1
    return {key: json.loads(c.most_common(1)[0][0]) for key, c in counts.items()}


def _account_of(db: Session, tenant_id: int, values: dict[str, Any]) -> Party | None:
    """The customer the form's records belong to."""
    refs = [(k[:-3], values.get(k)) for k in ("party_id", "lead_id", "opportunity_id", "quote_id", "order_id")]
    refs.append((lifecycle_service.normalize_entity_type(values.get("entity_type")), values.get("entity_id")))
    for entity_type, raw in refs:
        if entity_type and not _empty(raw):
            party_id = related_ids(db, tenant_id, entity_type, raw).get("party_id")
            if party_id:
                return db.get(Party, party_id)
    return None


def _habits(db: Session, tenant_id: int, code: str, party: Party | None, keys: set[str]) -> tuple[dict[str, Any], dict[str, Any]]:
    """From the action's past runs: what this customer usually has, and what
    the business usually picks (a value seen at least twice)."""
    logs = (
        db.query(IntentExecutionLog)
        .filter(IntentExecutionLog.tenant_id == tenant_id, IntentExecutionLog.intent_type == code, IntentExecutionLog.status == "executed")
        .order_by(IntentExecutionLog.id.desc())
        .limit(200)
        .all()
    )
    mine: dict[str, set[str]] = {}
    if party is not None:
        leads = [r.id for r in db.query(Lead.id).filter(Lead.tenant_id == tenant_id, Lead.party_id == party.id)]
        opps = [r.id for r in db.query(Opportunity.id).filter(Opportunity.lead_id.in_(leads))] if leads else []
        mine = {"party_id": {str(party.id)}, "lead_id": set(map(str, leads)), "opportunity_id": set(map(str, opps)),
                "quote_id": {str(r.id) for r in db.query(Quote.id).filter(Quote.opportunity_id.in_(opps))} if opps else set(),
                "order_id": {str(r.id) for r in db.query(Order.id).filter(Order.tenant_id == tenant_id, Order.party_id == party.id)}}
    account: dict[str, Counter] = {}
    business: dict[str, Counter] = {}
    for log in logs:
        try:
            entities = json.loads(log.entities_json or "{}")
        except json.JSONDecodeError:
            continue
        theirs = party is not None and (
            any(str(entities.get(k)) in ids for k, ids in mine.items() if not _empty(entities.get(k)))
            or str(entities.get("company_name") or "").strip().lower() == party.name.strip().lower())
        for key in keys:
            value = entities.get(key)
            if _empty(value):
                continue
            business.setdefault(key, Counter())[json.dumps(value)] += 1
            if theirs:
                account.setdefault(key, Counter())[json.dumps(value)] += 1
    pick = lambda c, least: {k: json.loads(v.most_common(1)[0][0]) for k, v in c.items() if v.most_common(1)[0][1] >= least}  # noqa: E731
    return pick(account, 1), pick(business, 2)


# The form is re-checked as the user edits it, but the sentence they started
# from doesn't change - read it once (a model call can take seconds), then
# reuse the reading for a while.
_UNDERSTOOD_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}
_UNDERSTOOD_TTL = 600.0


def _understood(db: Session, tenant_id: int, code: str, text: str, context: dict[str, Any] | None) -> dict[str, Any]:
    """Field values in what the user wrote, read for this intent."""
    if not text.strip():
        return {}
    import time

    from app.crm_core import llm

    # Same sentence, same tenant phrases/flows, same AI provider -> same reading.
    target = llm.resolve(db, tenant_id, live=True)
    key = json.dumps([tenant_id, code, text, context, classifier.custom_intent_specs(db, tenant_id), getattr(target, "label", None)],
                     sort_keys=True, default=str)
    hit = _UNDERSTOOD_CACHE.get(key)
    if hit is not None and time.monotonic() - hit[0] < _UNDERSTOOD_TTL:
        return dict(hit[1])
    entities = _read_text(db, tenant_id, code, text, context)
    if len(_UNDERSTOOD_CACHE) > 500:
        _UNDERSTOOD_CACHE.clear()
    _UNDERSTOOD_CACHE[key] = (time.monotonic(), entities)
    return dict(entities)


def _read_text(db: Session, tenant_id: int, code: str, text: str, context: dict[str, Any] | None) -> dict[str, Any]:
    result = classifier.classify(text, db, tenant_id, context, live=True)
    if result.intent_type == code:
        entities = result.entities
    else:
        loose = {**understanding.extract_loose_entities(text), **classifier._coerce_ids(classifier._extract_fields(text))}
        entities = understanding.entities_for(code, loose, text)
    return understanding.normalize_list_fields({k: v for k, v in entities.items() if v not in (None, "", [], {})})


def _empty(value: Any) -> bool:
    return value is None or value == "" or value == [] or value == {}


def _display(db: Session, tenant_id: int, spec: dict[str, Any], value: Any, values: dict[str, Any]) -> str:
    if _empty(value):
        return ""
    if spec["type"] == "record":
        record_type = _resolve_record_type(spec, values)
        obj = _get(db, tenant_id, record_type, value) if record_type else None
        return record_label(db, tenant_id, record_type, obj)[0] if obj is not None else f"#{value}"
    if spec["type"] == "percent":
        return f"{value}%"
    if spec["type"] == "money":
        try:
            return f"{float(value):,.2f}"
        except (TypeError, ValueError):
            return str(value)
    if spec["type"] == "list" and isinstance(value, list):
        return ", ".join(str(v) for v in value)
    if spec["type"] == "updates" and isinstance(value, dict):
        return ", ".join(f"{k} → {v}" for k, v in value.items())
    if spec["type"] == "picklist" and spec.get("source") == "@entity_types":
        return ENTITY_LABELS.get(str(value), str(value))
    return str(value).replace("_", " ") if spec["type"] in ("picklist", "transition") else str(value)


def _fixes_for(blocker: str, values: dict[str, Any]) -> dict[str, Any] | None:
    """The intent that would clear a blocker, prefilled."""
    quote = {"quote_id": values.get("quote_id")} if values.get("quote_id") else {}
    rules = [
        (r"quote is not approved", "INT-06", quote),
        (r"no customer-approved proposal", "INT-22", quote),
        (r"quote is still editable", "INT-19", quote),
        (r"record is locked", "INT-30" if values.get("entity_type") == "quote" else "INT-31",
         {"quote_id": values.get("entity_id")} if values.get("entity_type") == "quote" else
         {"entity_type": values.get("entity_type"), "entity_id": values.get("entity_id")}),
        (r"passed its PONR", "INT-30", quote) if values.get("quote_id") else (r"$^", "", {}),
    ]
    for pattern, code, entities in rules:
        if code and re.search(pattern, blocker, re.IGNORECASE):
            return {"code": code, "entities": {k: v for k, v in entities.items() if v is not None}}
    return None


def _humanize_blocker(message: str) -> str:
    message = re.sub(r"\b(\w+) \(not found\)", lambda m: f"That {m.group(1).replace('_', ' ')} could not be found", message)
    message = message.replace("(not found or not active)", "is not an active product")
    return message[:1].upper() + message[1:]


def _tips(code: str, values: dict[str, Any]) -> list[str]:
    tips = []
    try:
        discount = float(values.get("requested_discount_pct") or 0)
    except (TypeError, ValueError):
        discount = 0
    if code in ("INT-05", "INT-19", "INT-30") and discount > 10:
        tips.append("Discounts above 10% need a justification and may go to an approver.")
    if code == "INT-03" and values.get("offering_name") and values.get("offering_name") == values.get("replacement_offering_name"):
        tips.append("The replacement must be a different product.")
    if code == "INT-07" and isinstance(values.get("sites"), list) and len(values["sites"]) > 1:
        tips.append("Each site will be provisioned separately - use “Provision a site” for each one afterwards.")
    if code == "INT-09" and values.get("action") == "cancel":
        tips.append("Cancelling opens a retention window first - the service stays live until it lapses.")
    return tips


def prepare(
    db: Session,
    tenant_id: int,
    user: User,
    access: Access,
    code: str,
    entities: dict[str, Any] | None = None,
    text: str = "",
    context: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    entry = intent_registry.entry(db, tenant_id, code)
    if entry is None:
        return None
    specs = entry["fields"]
    keys = {s["key"] for s in specs}
    given = {k: v for k, v in (entities or {}).items() if k in keys and not _empty(v)}
    values: dict[str, Any] = dict(given)
    origins: dict[str, str] = {k: "you" for k in given}
    installed = None
    if code == "INT-107" and values.get("asset_id"):
        # the installed product configures a change - except a product change, where the product picked is the new one
        installed = _offering_in(db, tenant_id, {"asset_id": values["asset_id"]})
        if values.get("asset_operation") != "product_change":
            values["offering_name"] = installed
        elif values.get("offering_name") == installed:
            values.pop("offering_name")
            origins.pop("offering_name", None)

    def offer(key: str, value: Any, origin: str) -> None:
        if key in keys and _empty(values.get(key)) and not _empty(value):
            values[key] = value
            origins[key] = origin

    for key, value in _understood(db, tenant_id, code, text, context).items():
        offer(key, value, "understood")

    ctx_type = lifecycle_service.normalize_entity_type((context or {}).get("entity_type"))
    ctx_id = (context or {}).get("entity_id")
    if ctx_type and ctx_id:
        if "entity_type" in keys and _empty(values.get("entity_type")) and _empty(values.get("entity_id")):
            offer("entity_type", ctx_type, "context")
            offer("entity_id", ctx_id, "context")
        for key, value in related_ids(db, tenant_id, ctx_type, ctx_id).items():
            offer(key, value, "context")

    for key, value in linked_values(db, tenant_id, values).items():
        offer(key, value, "linked")

    for spec in specs:
        offer(spec["key"], spec.get("default"), "default")
    # history: this customer's usual values first (they beat a plain default), then the user's own habits
    open_keys = {k for k in keys & _HABITS if _empty(values.get(k)) or origins.get(k) == "default"}
    business: dict[str, Any] = {}
    if open_keys:
        account, business = _habits(db, tenant_id, code, _account_of(db, tenant_id, values), open_keys)
        for key, value in account.items():
            values.pop(key, None)
            offer(key, value, "account")
    for key, value in _remembered(db, tenant_id, user, code).items():
        if origins.get(key) == "default" or _empty(values.get(key)):
            values.pop(key, None)
            offer(key, value, "usual")
    for key, value in business.items():  # what the business usually picks - when nothing closer is known
        if _empty(values.get(key)):
            offer(key, value, "business")

    # Best guesses: the only candidate there is.
    if code == "INT-13":
        party = lifecycle_service.get_record(db, tenant_id, "party", values.get("party_id"))
        if party is not None and is_b2c(party):
            # Never carry a business document/provider into a consumer's form,
            # including remembered choices and stale values after switching customer.
            specs = [dict(s) for s in specs]
            for spec in specs:
                if spec["key"] == "id_type":
                    spec["values"] = [v for v in spec.get("values", []) if v != "commercial_registration"]
            if str(values.get("id_type", "")).lower().replace(" ", "_") == "commercial_registration":
                values["id_type"] = "national_id"
                origins["id_type"] = "default"
            if str(values.get("provider", "")).upper() == "WATHQ":
                values.pop("provider", None)
                origins.pop("provider", None)
    out_fields = []
    for spec in specs:
        key = spec["key"]
        options = _options(db, tenant_id, user, spec, values) if spec["type"] in ("picklist", "transition", "attributes", "products") else []
        if code == "INT-13" and key == "provider" and party is not None and is_b2c(party):
            options = [o for o in options if str(o["value"]).upper() != "WATHQ"]
        if spec["type"] == "transition":
            options = [o for o in options if not o.get("via")] + [o for o in options if o.get("via")]
        if _empty(values.get(key)) and text and options and spec["type"] not in ("attributes", "products"):
            # a known option named in the text ("quote Fiber 800 ...") - longest name wins
            lowered = f" {text.lower()} "
            named = [o for o in options if len(str(o["value"])) > 1 and re.search(rf"(?<![\w-]){re.escape(str(o['value']).lower())}(?![\w-])", lowered)]
            if named:
                offer(key, max(named, key=lambda o: len(str(o["value"])))["value"], "understood")
        if _empty(values.get(key)) and spec["required"] and spec["type"] not in ("attributes", "products"):
            usable = [o for o in options if not o.get("via")]
            if len(usable) == 1:
                offer(key, usable[0]["value"], "guess")
        suggestions: list[dict[str, Any]] = []
        record_type = _resolve_record_type(spec, values) if spec["type"] == "record" else None
        if record_type:
            suggestions = lookup(db, tenant_id, access, record_type, "", code, key, limit=6)
            fitting = [s for s in suggestions if s["suggested"]]
            if _empty(values.get(key)) and spec["required"] and len(fitting) == 1:
                offer(key, fitting[0]["id"], "guess")
        out_fields.append({**spec, "options": options, "suggestions": suggestions, "record_type": record_type or spec.get("record_type")})

    out_fields = _opportunity_view(db, tenant_id, user, code, values, origins, out_fields)
    change_errors: list[str] = []
    if installed:
        out_fields = _asset_change_view(values, origins, installed, out_fields)
        if values.get("asset_operation") == "product_change" and values.get("offering_name"):
            out_fields, change_errors = _product_change_line(db, tenant_id, values, origins, installed, out_fields)

    # Dynamic requirements.
    try:
        discount = float(values.get("requested_discount_pct") or 0)
    except (TypeError, ValueError):
        discount = 0
    dynamic_required = {"discount_justification"} if code == "INT-05" and discount > 10 else set()

    missing = [f["key"] for f in out_fields if (f["required"] or f["key"] in dynamic_required) and _empty(values.get(f["key"]))]
    for group in entry["one_of"]:
        if all(_empty(values.get(k)) for k in group):
            missing.append(" or ".join(group))
    if code == "INT-32" and all(_empty(values.get(k)) for k in ("to_user", "to_position", "to_department")) \
            and not any(m.startswith("to_user or to_position") for m in missing):
        missing.append("to_user or to_position or to_department")

    blockers: list[dict[str, Any]] = []
    reason = intent_registry.availability(db, tenant_id, user, code, values)
    if reason:
        blockers.append({"message": reason, "fix": None})
    for message in change_errors:  # the new product's line doesn't verify yet
        blockers.append({"message": f"{values.get('offering_name')}: {message}", "fix": None})
    for key, raw in values.items():
        spec = next((f for f in out_fields if f["key"] == key), None)
        if spec and spec["type"] == "record" and spec.get("record_type") in LIFECYCLE_TYPES:
            obj = _get(db, tenant_id, spec["record_type"], raw)
            if obj is None:
                blockers.append({"message": f"{ENTITY_LABELS.get(spec['record_type'], 'Record')} #{raw} could not be found.", "fix": None})
            elif not _visible(db, access, spec["record_type"], obj):
                blockers.append({"message": f"{ENTITY_LABELS[spec['record_type']]} #{raw} is outside the data your position can see.", "fix": None})
    if not missing and not blockers and code in intent_registry.BUILTIN:
        typed = _typed(values, out_fields)
        try:
            ctx = context_assembly.assemble_context(db, tenant_id, code, {**typed, "_actor_role": user.role.value, "_actor_user_id": user.id})
            plan = reasoning.plan(code, typed, ctx, 1.0, "")
            for message in plan.missing_fields:
                if message in keys or " or " in message and all(p in keys for p in message.split(" or ")):
                    continue
                fix = _fixes_for(message, typed)
                if fix:
                    fix["label"] = intent_registry.label_of(db, tenant_id, fix["code"])
                blockers.append({"message": _humanize_blocker(message), "fix": fix})
        except Exception as exc:  # a check that can't run must not break the form
            blockers.append({"message": f"Could not pre-check this: {exc}", "fix": None})

    for f in out_fields:
        key = f["key"]
        f["value"] = values.get(key)
        f["origin"] = origins.get(key)
        f["origin_note"] = ORIGIN_NOTES.get(origins.get(key, ""), "")
        f["display"] = _display(db, tenant_id, f, values.get(key), values)
        f["required"] = f["required"] or key in dynamic_required

    filled = [f for f in out_fields if f["display"]]
    summary = entry["label"] + (": " + " · ".join(f"{f['label']} {f['display']}" for f in filled[:6]) if filled else "")
    return {
        "code": code,
        "label": entry["label"],
        "icon": entry["icon"],
        "description": entry["description"],
        "target": entry["target"],
        "fields": out_fields,
        "values": values,
        "missing": missing,
        "blockers": blockers,
        "tips": _tips(code, values),
        "summary": summary,
        "ready": not missing and not blockers,
    }


_PRODUCT_CARRIED = {"INT-37", "INT-05", "INT-47"}  # the product comes from the lead - shown, not picked again
_PRESALES_STEPS = {"INT-38", "INT-18"}  # where the technical attributes are added / changed


def _opportunity_view(db: Session, tenant_id: int, user: User, code: str, values: dict[str, Any], origins: dict[str, str],
                      fields: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """B2B opportunity steps: the product picked on the lead is carried (locked,
    with a way to change it); the Account Manager configures the pricing
    attributes (Configure the product), the technical ones are added and
    changed on the way to and during Presales' check - where the pricing
    attributes are shown read-only. Presales never changes pricing."""
    if code not in _PRODUCT_CARRIED | _PRESALES_STEPS or not values.get("opportunity_id"):
        return fields
    opp = _get(db, tenant_id, "opportunity", values.get("opportunity_id"))
    if opp is None:
        return fields
    from app.crm_core import process_service
    from app.crm_core import product_config_service as product_config

    presales = process_service.is_presales_only(db, tenant_id, user.id)
    carried = opp.offering_name or (opp.lead.interested_offering_name if opp.lead is not None else None)
    config = opp.config
    pricing = product_config.loads(config.pricing_attributes_json) if config is not None else {}
    technical = product_config.loads(config.technical_attributes_json) if config is not None else {}
    keys = {f["key"] for f in fields}
    out = []
    for f in fields:
        key = f["key"]
        if key == "offering_name" and carried and code in _PRODUCT_CARRIED:
            if _empty(values.get(key)) or origins.get(key) != "you":
                values[key], origins[key] = carried, "linked"
            f = {**f, "locked": True, "locked_note": "Picked on the lead - carried to the opportunity",
                 "locked_change": not presales and code == "INT-37"}
        elif code == "INT-37" and key == "technical_attributes":
            continue  # added and changed when it goes to Presales
        elif code in ("INT-37", "INT-47") and presales and key in ("pricing_attributes", "quantity", "need", "max_monthly"):
            f = {**f, "readonly": True, "locked_note": "Presales sees this - the Account Manager changes it"}
            if key == "pricing_attributes" and _empty(values.get(key)) and pricing:
                values[key], origins[key] = pricing, "linked"
        elif code == "INT-37" and key == "pricing_attributes" and _empty(values.get(key)) and pricing:
            values[key], origins[key] = pricing, "linked"  # what is configured today, to change
        elif code in _PRESALES_STEPS and key == "technical_attributes":
            if _empty(values.get(key)) and technical:
                values[key], origins[key] = technical, "linked"
            f = {**f, "help": "Add, change or complete what provisioning needs - Presales owns these"}
        out.append(f)
    if code in _PRESALES_STEPS and "pricing_attributes" not in keys and pricing:
        # the Account Manager's configuration, for Presales to see (never sent back)
        values["pricing_attributes"] = pricing
        origins["pricing_attributes"] = "linked"
        out.append({"key": "pricing_attributes", "label": f"Pricing attributes{f' - {carried}' if carried else ''}", "type": "attributes",
                    "required": False, "help": "Configured by the Account Manager - Presales sees them but doesn't change them",
                    "placeholder": "", "values": [], "source": "", "record_type": "", "default": None, "show_when": {},
                    "options": [], "suggestions": [], "readonly": True})
    return out


def _asset_change_view(values: dict[str, Any], origins: dict[str, str], installed: str, fields: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Change an asset: a product change picks the NEW product - any other
    than the installed one, and it must be picked; every other change works
    on the installed product, shown locked."""
    out = []
    for f in fields:
        if f["key"] == "offering_name":
            if values.get("asset_operation") == "product_change":
                if values.get("offering_name") == installed:  # e.g. named in the text - not a change
                    values.pop("offering_name")
                    origins.pop("offering_name", None)
                f = {**f, "label": "New product", "required": True, "help": f"Now on {installed} - pick the product to move to",
                     "options": [o for o in f.get("options", []) if o.get("value") != installed]}
            else:
                f = {**f, "locked": True, "locked_note": "The installed product - use Product change to move to another"}
        out.append(f)
    return out


def _product_change_line(db: Session, tenant_id: int, values: dict[str, Any], origins: dict[str, str], installed: str,
                         fields: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[str]]:
    """A product change, as Siebel does it: the installed product's line is
    deleted and the new product's added, pre-configured - its defaults plus
    every setting of the old service it also has - and verified before it
    is submitted (what still stops it comes back as blockers)."""
    from app.crm_core import abo_service

    line = abo_service.product_change_preview(db, tenant_id, values["asset_id"], values)
    if line is None:
        return fields, []
    for key, kind in (("pricing_attributes", "pricing"), ("technical_attributes", "technical")):
        if _empty(values.get(key)) and line[kind]:
            values[key], origins[key] = line[kind], "linked"
    new = line["offering_name"]
    notes = {"pricing_attributes": f"{new}'s pricing - carried from {installed} where it fits, else its defaults",
             "technical_attributes": f"{new}'s technical settings - carried from {installed} where they fit, else its defaults"}
    out = [{**f, "help": notes[f["key"]]} if f["key"] in notes else f for f in fields]
    for f in out:
        if f["key"] == "offering_name" and line.get("configured_price") is not None:
            f["help"] = f"Deletes {installed} and adds {new} - {line['configured_price']:g} a month as configured"
    return out, line["errors"]


def _typed(values: dict[str, Any], specs: list[dict[str, Any]]) -> dict[str, Any]:
    """Form values in the shapes the intent steps expect."""
    out = dict(values)
    for spec in specs:
        value = out.get(spec["key"])
        if _empty(value):
            continue
        if spec["type"] in ("number", "money", "percent") or (spec["type"] == "record" and str(value).isdigit()):
            try:
                number = float(value)
                out[spec["key"]] = int(number) if number.is_integer() and spec["type"] in ("number", "record") else number
            except (TypeError, ValueError):
                pass
        elif spec["type"] == "list" and isinstance(value, str):
            out[spec["key"]] = [p.strip() for p in re.split(r"[,+]", value) if p.strip()]
    return understanding.normalize_list_fields(out)


def typed_entities(db: Session, tenant_id: int, code: str, values: dict[str, Any]) -> dict[str, Any]:
    entry = intent_registry.entry(db, tenant_id, code)
    return _typed(values, entry["fields"]) if entry else values


# ---------------------------------------------------------------- outcome

_RESULT_KEYS = {
    "party_id": "party", "lead_id": "lead", "opportunity_id": "opportunity", "quote_id": "quote", "new_quote_id": "quote",
    "order_id": "order", "ticket_id": "ticket", "work_order_id": "work_order", "proposal_id": "proposal",
    "product_offering_id": "product_offering", "offering_id": "product_offering",
}
_HIDDEN_FIELDS = {"tenant_id", "id", "built_in_status", "graph_json"}


def _record_refs(result: dict[str, Any]) -> list[tuple[str, int]]:
    refs: list[tuple[str, int]] = []

    def add(record_type: str | None, value: Any) -> None:
        if record_type and isinstance(value, int) and (record_type, value) not in refs:
            refs.append((record_type, value))

    def walk(obj: Any, depth: int = 0) -> None:
        if depth > 3:
            return
        if isinstance(obj, dict):
            if obj.get("entity_type") in LIFECYCLE_TYPES:
                add(obj["entity_type"], obj.get("entity_id"))
            for key, value in obj.items():
                if key in _RESULT_KEYS:
                    add(_RESULT_KEYS[key], value)
                elif key.endswith("_ids") and key[:-1] in _RESULT_KEYS and isinstance(value, list):
                    for v in value:
                        add(_RESULT_KEYS[key[:-1]], v)
                elif isinstance(value, (dict, list)) and key not in ("flows_run", "trace"):
                    walk(value, depth + 1)
        elif isinstance(obj, list):
            for item in obj:
                walk(item, depth + 1)

    walk(result)
    return refs


def _fields_of(db: Session, tenant_id: int, record_type: str, obj) -> list[dict[str, Any]]:
    if record_type in LIFECYCLE_TYPES:
        data = record_service.snapshot(db, tenant_id, record_type, obj)
    else:
        data = {c.key: getattr(obj, c.key) for c in obj.__table__.columns}
    out = []
    for key, value in data.items():
        if key in _HIDDEN_FIELDS or key.endswith("_json") or value in (None, ""):
            continue
        value = getattr(value, "value", value)
        if hasattr(value, "isoformat"):
            value = value.isoformat(timespec="minutes")
        label = record_service.CORE_FIELD_LABELS.get(key) or key.replace("_", " ").capitalize().replace(" id", " #").replace(" pct", " %")
        out.append({"key": key, "label": label, "value": value})
    return out


_NEXT: dict[str, list[tuple[str, str]]] = {
    # intent -> [(next intent, why)]
    # New Connect: prospect -> lead (with the products they want) -> qualify; the prospect
    # becomes an account by itself when its first order is activated.
    "INT-15": [("INT-46", "Generate a lead for them"), ("INT-13", "Verify their identity")],
    "INT-46": [("INT-17", "Qualify the lead"), ("INT-16", "Add another product they want")],
    "INT-47": [("INT-38", "Send to Presales for feasibility"), ("INT-05", "Quote it")],
    "INT-49": [("INT-46", "Generate a lead for what they can buy")],
    "INT-35": [("INT-04", "Qualify a lead for the account")],
    "INT-04": [("INT-37", "Configure the product"), ("INT-18", "Check feasibility for the new opportunity"), ("INT-05", "Quote a product"),
               ("INT-16", "Note the product they want")],
    "INT-16": [("INT-17", "Approve the lead")],
    "INT-17": [("INT-37", "Configure the product"), ("INT-18", "Check feasibility"), ("INT-05", "Quote a product")],
    "INT-37": [("INT-38", "Send it to Presales for feasibility")],
    "INT-38": [("INT-18", "Presales: record the feasibility check")],
    "INT-18": [("INT-05", "Feasible - prepare the quote")],
    "INT-05": [("INT-06", "Send the quote for approval"), ("INT-19", "Adjust discount or quantity"),
               ("INT-116", "Set the price negotiated with the customer")],
    "INT-19": [("INT-06", "Approve the revised quote")],
    "INT-30": [("INT-06", "Approve the new revision")],
    # B2B: discount approved -> proposal PDF on the quote (INT-109) -> sent (INT-22) -> PO (INT-39) -> Match all (INT-40)
    "INT-06": [("INT-109", "Generate the proposal - a PDF on the quote"), ("INT-39", "Attach the customer's PO"),
               ("INT-22", "Send the proposal to the customer"), ("INT-07", "Activate the order directly")],
    "INT-39": [("INT-40", "Sales Ops: match all")],
    "INT-40": [("INT-25", "Approve the order")],
    # B2C direct order: a consumer goes straight to the order (INT-104) - no lead, opportunity or quote
    "INT-41": [("INT-104", "Place their order"), ("INT-13", "Verify their identity")],
    "INT-42": [("INT-48", "Release the product model so sellers get it"), ("INT-43", "Price it on a pricelist")],
    "INT-08": [("INT-50", "Change its configured options instead (checked against the product's rules)")],
    "INT-24": [("INT-25", "Approve the order"), ("INT-45", "Set the technical attributes provisioning needs")],
    "INT-22": [("INT-23", "Record the customer's answer"), ("INT-39", "Attach the customer's PO - it accepts the proposal")],
    "INT-23": [("INT-24", "Create the order"), ("INT-39", "Attach the customer's PO")],
    "INT-25": [("INT-26", "Complete the installation")],
    "INT-26": [("INT-27", "Complete the connectivity test")],
    "INT-27": [("INT-28", "Attach the acceptance letter")],
    "INT-07": [("INT-14", "Provision each site"), ("INT-08", "Change the service later")],
    "INT-10": [("INT-11", "Diagnose the ticket")],
    "INT-11": [("INT-12", "Send a technician")],
    "INT-01": [("INT-02", "Adjust the new product"), ("INT-42", "Design its pricing and technical attributes")],
}
_NEXT.update({k: v for k, v in om_intents.NEXT.items() if k not in _NEXT})
_NEXT.update({k: v for k, v in comm_intents.NEXT.items() if k not in _NEXT})
_NEXT.update({k: v for k, v in config_intents.NEXT.items() if k not in _NEXT})
_NEXT.update({k: v for k, v in b2c_intents.NEXT.items() if k not in _NEXT})
_NEXT.update({k: v for k, v in sto_intents.NEXT.items() if k not in _NEXT})
_NEXT.update({k: v for k, v in abo_intents.NEXT.items() if k not in _NEXT})
_NEXT.update({k: v for k, v in quote_intents.NEXT.items() if k not in _NEXT})
_NEXT.update({k: v for k, v in fulfillment_intents.NEXT.items() if k not in _NEXT})
_NEXT.update({k: v for k, v in inventory_intents.NEXT.items() if k not in _NEXT})
# what a B2C customer is offered instead of the lead path (b2c_order_service)
_B2C_NEXT: dict[str, list[tuple[str, str]]] = {
    "INT-15": [("INT-104", "Place their order - no lead or quote for B2C"), ("INT-13", "Verify their identity")],
    "INT-49": [("INT-104", "Place the order for what they can buy")],
}
_B2C_NOT_FEASIBLE = [("INT-60", "Release the order once the issue is fixed"), ("INT-61", "Cancel the order"),
                     ("INT-105", "Tell the customer - log the contact on their channel")]


def _map_facts(db: Session, tenant_id: int, code: str, entities: dict[str, Any], result: dict[str, Any],
               merged: dict[str, Any]) -> dict[str, Any] | None:
    """What a condition on the Functional Map can test after a step ran:
    entities.<input>, result.<output> and record.<field> of the step's record.
    None (every link offered, as before) when the step's links have no
    conditions - nothing extra is read then."""
    from app.orchestration import function_map

    if not function_map.decides(db, tenant_id, code):
        return None
    from app.orchestration import flow_engine

    facts: dict[str, Any] = {"entities": {k: v for k, v in entities.items() if not str(k).startswith("_")},
                             "result": {k: v for k, v in result.items() if k != "flows_run"}, "record": {}, "customer": {}, "parent": {}}
    target = (intent_registry.entry(db, tenant_id, code) or {}).get("target")
    if target and target in LIFECYCLE_TYPES and merged.get(f"{target}_id"):
        obj =lifecycle_service.get_record(db, tenant_id, target, merged[f"{target}_id"])
        if obj is not None:
            facts["record"] = record_service.snapshot(db, tenant_id, target, obj)
            # the customer behind it and the records above it (lead, opportunity, quote) - as Flow Studio's conditions see them
            party = flow_engine.customer_of(db, target, obj)
            facts["customer"] = record_service.snapshot(db, tenant_id, "party", party) if party is not None else {}
            facts["parent"] = flow_engine.parent_snapshots(db, tenant_id, target, obj)
    return facts


def _auto_ran(result: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    """The intents the tenant's business rules (Flow Studio) ran after this
    one and completed, in order, with what each returned."""
    out: list[tuple[str, dict[str, Any]]] = []
    for run in result.get("flows_run") or []:
        if not isinstance(run, dict) or run.get("skipped"):
            continue
        for step in run.get("trace") or []:
            if isinstance(step, dict) and step.get("type") == "action" and step.get("intent") and not step.get("skipped"):
                output = step.get("output") if isinstance(step.get("output"), dict) else {}
                out.append((str(step["intent"]), output))
    return out


def _next_entities(db: Session, tenant_id: int, code: str, result: dict[str, Any], merged: dict[str, Any]) -> dict[str, Any]:
    """Prefill for a recommended next intent from what this run produced."""
    keys = {f["key"] for f in (intent_registry.entry(db, tenant_id, code) or {"fields": []})["fields"]}
    out = {k: v for k, v in merged.items() if k in keys and isinstance(v, (int, str)) and v != ""}
    if "quote_id" in keys and merged.get("new_quote_id"):
        out["quote_id"] = merged["new_quote_id"]
    if merged.get("opportunity_id") and ({"party_id", "offering_name"} & keys) - out.keys():
        # a step the map puts straight after the opportunity (e.g. Order an offering after feasibility): its customer and product
        from app.models.crm import Opportunity
        from app.orchestration import flow_engine

        opp = db.get(Opportunity, merged["opportunity_id"])
        if opp is not None and opp.tenant_id == tenant_id:
            party = flow_engine.customer_of(db, "opportunity", opp)
            if "party_id" in keys and party is not None:
                out.setdefault("party_id", party.id)
            if "offering_name" in keys and opp.offering_name:
                out.setdefault("offering_name", opp.offering_name)
    if code == "INT-23" and merged.get("quote_id"):
        proposal = db.query(Proposal).filter(Proposal.tenant_id == tenant_id, Proposal.quote_id == merged["quote_id"]).order_by(Proposal.id.desc()).first()
        if proposal:
            out["proposal_id"] = proposal.id
    if code in ("INT-26", "INT-27", "INT-28") and merged.get("order_id"):
        kind = {"INT-26": "installation", "INT-27": "connectivity_test", "INT-28": "acceptance_letter"}[code]
        wo = (db.query(WorkOrder).filter(WorkOrder.tenant_id == tenant_id, WorkOrder.order_id == merged["order_id"],
                                         WorkOrder.kind == kind, WorkOrder.status != "complete").order_by(WorkOrder.id.desc()).first())
        if wo:
            out["work_order_id"] = wo.id
        else:
            out.pop("work_order_id", None)
    if code == "INT-04" and merged.get("party_id"):
        party = db.get(Party, merged["party_id"])
        if party:
            out["company_name"] = party.name
    if code == "INT-02" and merged.get("name"):
        out["offering_name"] = merged["name"]
    return out


def recovery(db: Session, tenant_id: int, user: User, code: str | None, entities: dict[str, Any], error: str | None) -> list[dict[str, Any]]:
    """What fixes a run that stopped: the step that clears what it was missing."""
    actions: list[tuple[str, str, dict[str, Any]]] = []
    if code == "INT-25" and (error or "").startswith("Set the technical attributes") and entities.get("order_id"):
        # approving needs the technical attributes provisioning uses - one action per product still missing some
        from app.crm_core import feasibility_service, product_config_service

        order = db.get(Order, int(entities["order_id"]))
        if order is not None and order.tenant_id == tenant_id:
            overridden = feasibility_service.order_overridden(db, tenant_id, order)
            for product, missing in product_config_service.order_missing_by_product(db, tenant_id, order, overridden):
                names = ", ".join(m.removesuffix(" is required") for m in missing)
                actions.append(("INT-45", f"{product} still needs: {names}", {"order_id": order.id, "offering_name": product}))
    if code == "INT-18" and (error or "").startswith("Can't pass feasibility") and entities.get("opportunity_id"):
        # Inventory couldn't hold what the deal needs: Presales can still pass it, with a reason
        actions.append(("INT-18", "Inventory down or wrong? Pass feasibility with an inventory override and a reason",
                        {"opportunity_id": entities["opportunity_id"], "decision": "feasible", "inventory_override": True}))
    out = []
    for next_code, why, prefill in actions:
        if intent_registry.availability(db, tenant_id, user, next_code):
            continue
        entry = intent_registry.entry(db, tenant_id, next_code)
        out.append({"code": next_code, "label": entry["label"], "icon": entry["icon"], "reason": why, "entities": prefill})
    return out[:3]


# Records without a lifecycle that an intent creates.
_CREATES = {"INT-01": "product_offering", "INT-10": "ticket", "INT-12": "work_order", "INT-22": "proposal"}


def _change_kind(code: str | None, record_type: str, was_given: bool, change: dict[str, Any] | None) -> str:
    if was_given:
        return "updated"
    if change is not None:
        # sync_records sees a new lifecycle record with no previous state
        return "created" if not change.get("from_state") else "updated"
    return "created" if _CREATES.get(code or "") == record_type else "involved"


def outcome(
    db: Session, tenant_id: int, user: User, access: Access, code: str | None, entities: dict[str, Any], result: dict[str, Any] | None
) -> dict[str, Any]:
    result = result or {}
    state_changes = {(e["entity_type"], e["entity_id"]): e for e in result.get("state_entries", [])}
    given = {(rt, v) for k, v in entities.items() if (rt := _RESULT_KEYS.get(k)) and isinstance(v, int)}
    if entities.get("entity_type") in LIFECYCLE_TYPES and isinstance(entities.get("entity_id"), int):
        given.add((entities["entity_type"], entities["entity_id"]))

    records = []
    for record_type, record_id in _record_refs(result) + [k for k in state_changes if k not in _record_refs(result)]:
        obj = _get(db, tenant_id, record_type, record_id)
        if obj is None or not _visible(db, access, record_type, obj):
            continue
        label, hint = record_label(db, tenant_id, record_type, obj)
        change = state_changes.get((record_type, record_id))
        records.append({
            "entity_type": record_type,
            "entity_id": record_id,
            "brt_id": getattr(obj, "brt_id", None),
            "type_label": ENTITY_LABELS.get(record_type, record_type),
            "title": label,
            "hint": hint,
            "state": _state(db, tenant_id, record_type, obj),
            "change": _change_kind(code, record_type, (record_type, record_id) in given, change),
            "from_state": change.get("from_state") if change else None,
            "to_state": change.get("to_state") if change else None,
            "fields": _fields_of(db, tenant_id, record_type, obj),
            "openable": record_type in LIFECYCLE_TYPES,
        })
    created = [r for r in records if r["change"] == "created"]
    moved = [r for r in records if r["from_state"] and r["to_state"]]
    parts = []
    if created:
        parts.append("Created " + ", ".join(f"{r['type_label']} #{r['entity_id']}" for r in created))
    if moved:
        parts.append("Moved " + ", ".join(f"{r['type_label']} #{r['entity_id']} {r['from_state'].replace('_', ' ')} → {r['to_state'].replace('_', ' ')}" for r in moved))
    flows = [f["flow_name"] for f in result.get("flows_run", []) if not f.get("skipped")]
    if flows:
        parts.append("ran " + ", ".join(flows))

    label = intent_registry.label_of(db, tenant_id, code) or code or "Intent"
    merged: dict[str, Any] = {**entities, **{k: v for k, v in result.items() if isinstance(v, (int, str))}}
    for r in records:
        merged.setdefault(f"{r['entity_type']}_id", r["entity_id"])

    from app.orchestration import function_map

    next_actions = []
    # the tenant's Functional Map decides the next steps once it has one; the vanilla ones otherwise -
    # a link with a condition is offered only when it holds for what this step did
    candidates = function_map.next_steps(db, tenant_id, code or "", _NEXT.get(code or "", []),
                                         _map_facts(db, tenant_id, code or "", entities, result, merged))
    map_decides = function_map.decides(db, tenant_id, code or "")
    if code == "INT-42" and not result.get("note"):
        # never released: the design is live already - releasing would start versioning it
        candidates = [c for c in candidates if c[0] != "INT-48"]
    if code in ("INT-15", "INT-41", "INT-49") and merged.get("party_id"):
        from app.crm_core import b2c_order_service

        party = db.get(Party, merged["party_id"])
        if b2c_order_service.is_b2c(party) and b2c_order_service.direct(db, tenant_id):
            # B2C: straight to the order - the order is already placed when products came with the prospect
            candidates = _NEXT["INT-104"] if result.get("order_placed") else _B2C_NEXT.get(code, candidates)
            candidates = [c for c in candidates if c[0] not in ("INT-46", "INT-04", "INT-17", "INT-05", "INT-38", "INT-18")]
    if code in ("INT-104", "INT-15", "INT-41") and result.get("held"):
        candidates = _B2C_NOT_FEASIBLE
    if isinstance(result.get("note"), str):
        parts.append(result["note"])
    if map_decides:
        pass  # the conditions drawn on the map decide this step's outcomes
    elif code == "INT-18" and "presales_outcome" in result:
        # Presales' conclusion: feasible -> quote (or wherever the tenant's map re-wired it); not feasible -> back to configuring.
        if not (result["presales_outcome"] == "passed" and function_map.rewired(db, tenant_id, code)):
            candidates = {"passed": [("INT-05", "Feasible - prepare the quote")],
                          "failed": [("INT-37", "Not feasible - reconfigure with the customer")]}.get(result["presales_outcome"], [])
        if result.get("waiting_for"):
            candidates = []
    elif code == "INT-18" and result.get("outcome") != "passed":
        candidates = [("INT-31", "Not feasible - close the opportunity as lost")] if result.get("outcome") == "failed" else []
    if result.get("waiting_for") or result.get("approval_status") == "pending":
        candidates = []  # waiting for someone else's approval
    if not map_decides and code == "INT-06" and result.get("decision") == "reject":
        candidates = [("INT-30", "Rework the quote as a new revision")]
    if not map_decides and code in ("INT-23", "INT-17", "INT-25") and entities.get("decision") == "reject":
        candidates = []
    ran = _auto_ran(result)
    if ran:
        # Business rules carried the process further: what's next follows the
        # last step they completed, and never offers a step they already ran.
        last_code, last_out = ran[-1]
        merged.update({k: v for k, v in last_out.items() if isinstance(v, (int, str)) and k.endswith("_id")})
        candidates = function_map.next_steps(db, tenant_id, last_code, _NEXT.get(last_code, []),
                                             _map_facts(db, tenant_id, last_code, {}, last_out, merged))
        last_decides = function_map.decides(db, tenant_id, last_code)
        if (not last_decides and last_code == "INT-18" and "presales_outcome" in last_out
                and not (last_out["presales_outcome"] == "passed" and function_map.rewired(db, tenant_id, last_code))):
            candidates = {"passed": [("INT-05", "Feasible - prepare the quote")],
                          "failed": [("INT-37", "Not feasible - reconfigure with the customer")]}.get(last_out["presales_outcome"], [])
        if last_out.get("waiting_for") or last_out.get("approval_status") == "pending" or (not last_decides and last_out.get("decision") == "reject"):
            candidates = []
        done_codes = {c for c, _ in ran} | {code}
        candidates = [c for c in candidates if c[0] not in done_codes]
    for next_code, why in candidates:
        if intent_registry.availability(db, tenant_id, user, next_code):
            continue
        prefill = _next_entities(db, tenant_id, next_code, result, merged)
        if next_code == "INT-31":
            prefill = {"entity_type": "opportunity", "entity_id": merged.get("opportunity_id"), "to_state": "lost"}
        entry = intent_registry.entry(db, tenant_id, next_code)
        next_actions.append({"code": next_code, "label": entry["label"], "icon": entry["icon"], "reason": why, "entities": prefill})

    if code == "INT-33" and result.get("summary"):
        parts = [f"Customer 360 is now on version {result.get('c360_version')}: {result['summary']}"]
    return {
        "summary": result.get("message") if result.get("configuration_request") else f"{label} - done." + (" " + "; ".join(parts) + "." if parts else ""),
        "records": records,
        "next_actions": next_actions[:3],
    }

"""Network attributes at installation - the AI engine's defaults, the engineer's override.

Network-level attributes (VLAN, port, IP block, OLT / ONT, circuit id ...) are not
part of selling: the quote configures pricing and service attributes, and order
approval never asks for network ones (product_config_service.at_installation).
They are set during service provisioning, on the installation work order:

  propose()  the AI engine reads the order's products and the service configuration
             chosen on the quote (Access Medium above all), the product's network
             attributes modelled "set at installation", and how the tenant's network
             inventory is set up for it - the location serving the site, what is
             already held for the order, what is free there (pools / equipment), and
             the inventory item types and their attributes. It answers the network
             attributes this product over this access medium needs, each with a
             default value. Values that name an inventory resource can only be real
             ones (held for the order, or free at the serving location): anything else
             the model says is replaced by the real candidate, or left blank.
  save()     what the installation engineer confirmed - defaults kept or overridden -
             goes onto the order item's technical attributes (and its product instance),
             so it reaches the asset, the activation and Inventory like every other
             technical attribute; the work order's job sheet records which values were
             overridden and by whom.
  missing()  required "set at installation" attributes still empty - an installation
             work order can't be completed without them.

Defaults come from the AI engine only; with none set up, propose() says so (the
engineer can still type the values and save them).
"""

from __future__ import annotations

import json
import re
from typing import Any

from sqlalchemy.orm import Session

from app.crm_core import product_config_service as config

_key = config._key
_MEDIUM = re.compile(r"access\s*(medium|type|technology|method)|last\s*mile|medium|technology", re.I)

_PROMPT = """You are the service-provisioning assistant of a telecom operator. An installation engineer is about to provision
an order. You get, per ordered product: its name, the service configuration chosen on the quote (pricing and service
attributes - the ACCESS MEDIUM matters most), the product's network attributes modelled "set at installation", and the
operator's network inventory as it is set up: the location serving the site, resources already held for this order,
resources free at that location (with the next free value), and the inventory item types with their attributes.

Answer the NETWORK-LEVEL attributes the engineer must record to provision THIS product over THIS access medium - only
those (fibre/GPON: OLT, PON port, splitter port, ONT serial, VLAN ...; copper/DSL: DSLAM, line/port, VLAN ...; microwave:
BTS/hub, sector, frequency/channel, radio serial ...; dedicated/Ethernet: PE/switch, port, S-VLAN/C-VLAN, WAN IP block ...;
voice: SIP trunk IP, number range ...). Always include every modelled "set at installation" attribute, under its exact
name. Do not repeat sales/service attributes already chosen on the quote.

For each give a default value:
- an inventory resource: use EXACTLY an identifier from held_for_order (preferred) or the candidate from free_at_site
  for that resource type, with source "inventory" and resource_type set. Never invent identifiers.
- otherwise a sensible default from the configuration or the item type's picklist (source "default"), or "" when only the
  engineer can know it (e.g. a serial number read on site) with source "engineer".

Return JSON only:
{"summary": "one sentence", "products": [{"product": str, "attributes": [{"name": str, "value": str,
  "source": "inventory"|"default"|"engineer", "resource_type": str|null, "options": [str], "required": bool,
  "reason": "why, in one short sentence"}]}]}"""


def _work_order(db: Session, tenant_id: int, work_order_id: int):
    from app.models.assurance import WorkOrder
    from app.models.orders import Order

    wo = db.get(WorkOrder, work_order_id)
    if wo is None or wo.tenant_id != tenant_id:
        raise ValueError(f"Work order {work_order_id} not found")
    order = db.get(Order, wo.order_id) if wo.order_id else None
    if order is None or order.tenant_id != tenant_id:
        raise ValueError(f"Work order {work_order_id} isn't part of an order - network attributes belong to an order's installation")
    return wo, order


def _items(db: Session, tenant_id: int, order) -> list[dict[str, Any]]:
    """Per order item: product, frozen design, pricing / technical chosen, network attributes modelled."""
    from app.crm_core import instance_service
    from app.models.catalog import ProductOffering

    out = []
    for item in order.items:
        offering = db.get(ProductOffering, item.product_offering_id)
        if offering is None:
            continue
        attrs = config.order_item_attributes(db, item.id) or ({}, {})
        design = instance_service.design_for(db, tenant_id, "order", order.id, offering.name) or config.schema(db, tenant_id, offering.name)
        modelled = [a for a in design.get("attributes", []) if config.at_installation(a)]
        out.append({"item": item, "product": offering.name, "design": design, "pricing": attrs[0] or {}, "technical": attrs[1] or {},
                    "modelled": modelled})
    return out


def _medium(entry: dict[str, Any]) -> str | None:
    chosen = {**entry["pricing"], **entry["technical"]}
    return next((str(v) for k, v in chosen.items() if _MEDIUM.search(k) and v not in (None, "")), None)


def _inventory(db: Session, tenant_id: int, order, products: list[str]) -> dict[str, Any]:
    from app.crm_core import inventory_service
    from app.models.inventory import InvItem, InvItemType
    from app.orchestration import fulfillment_engine

    info = fulfillment_engine.order_data(db, order)["order"]
    resources, came_from = inventory_service.resources_for_product(db, tenant_id, products, info.get("segment") or "")
    availability = inventory_service.check_availability(db, tenant_id, info.get("address") or "", resources) if resources else \
        {"location": None, "checks": []}
    held = []
    for item in (db.query(InvItem).filter(InvItem.tenant_id == tenant_id, InvItem.order_id == order.id, InvItem.kind != "service",
                                          InvItem.lifecycle.in_(("reserved", "in_use"))).order_by(InvItem.id)):
        t = db.get(InvItemType, item.type_id)
        out = inventory_service.item_out(db, item, brief=True)
        held.append({"resource_type": (t.resource_type or t.code) if t else "", "identifier": item.identifier, "type": out["type_name"],
                     "location": out["location"], "parent": out["parent"]})
    wanted = set(resources) | {h["resource_type"] for h in held}
    types = [{"code": t.code, "name": t.name, "kind": t.kind, "resource_type": t.resource_type,
              "attributes": [{k: a.get(k) for k in ("key", "label", "type", "values") if a.get(k) not in (None, "", [])}
                             for a in inventory_service.attributes(db, t)][:15]}
             for t in db.query(InvItemType).filter(InvItemType.tenant_id == tenant_id, InvItemType.is_active.is_(True))
             if t.resource_type in wanted or t.kind == "physical" and not wanted]
    return {"address": info.get("address") or "", "segment": info.get("segment") or "", "serving_location": availability.get("location"),
            "resources_needed": resources, "resources_from": came_from, "held_for_order": held,
            "free_at_site": [{"resource_type": c["resource_type"], "kept_in_inventory": c["kept"], "candidate": c["candidate"],
                              "free": c["free"], "detail": c["detail"]} for c in availability.get("checks", [])],
            "item_types": types[:25]}


def propose(db: Session, tenant_id: int, work_order_id: int) -> dict[str, Any]:
    from app.crm_core import llm

    wo, order = _work_order(db, tenant_id, work_order_id)
    entries = _items(db, tenant_id, order)
    if not entries:
        raise ValueError(f"Order {order.id} has no products to provision")
    inventory = _inventory(db, tenant_id, order, [e["product"] for e in entries])
    base = _baseline(entries, inventory)
    target = llm.resolve(db, tenant_id)
    if target is None:
        return {**base, "engine": None,
                "notes": ["No AI engine is set up for this tenant (Admin → AI), so no defaults were generated - "
                          "the attributes the product models for installation are listed; type their values."]}
    context = {"order_id": order.id, "inventory": inventory, "products": [
        {"product": e["product"], "access_medium": _medium(e), "quote_configuration": {**e["pricing"], **e["technical"]},
         "modelled_installation_attributes": [{k: a.get(k) for k in ("name", "values", "required", "default", "reference_type", "unit", "description")}
                                              for a in e["modelled"]]}
        for e in entries]}
    try:
        reply = llm.chat_json(target, _PROMPT, json.dumps(context, default=str) + "\n\nGive the network attributes and their defaults.")
    except llm.LlmUnavailable as exc:
        return {**base, "engine": None, "notes": [f"The AI engine couldn't generate defaults just now ({exc}) - try again, or type the values."]}
    return {**_merge(base, reply, inventory), "engine": "ai", "model": target.label}


def _baseline(entries: list[dict[str, Any]], inventory: dict[str, Any]) -> dict[str, Any]:
    """What is known without the AI: the modelled installation attributes, with what was already saved."""
    products = []
    for e in entries:
        attrs = []
        for a in e["modelled"]:
            current = next((v for k, v in e["technical"].items() if _key(k) == _key(a["name"])), None)
            attrs.append({"name": a["name"], "value": "" if current is None else str(current), "default": a.get("default") or "",
                          "source": "model", "resource_type": None, "options": a.get("values") or [], "required": bool(a.get("required")),
                          "modelled": True, "saved": current is not None, "reason": a.get("description") or "Modelled to be set at installation"})
        # network attributes saved at an earlier visit that the product doesn't model (they came from the inventory set-up)
        known = {_key(a["name"]) for a in e["design"].get("attributes", [])} | {_key(c["name"]) for c in e["design"].get("components", [])}
        for k, v in e["technical"].items() if e["design"].get("designed") else ():
            if _key(k) not in known:
                attrs.append({"name": k, "value": str(v), "default": "", "source": "saved", "resource_type": None, "options": [],
                              "required": False, "modelled": False, "saved": True, "reason": "Saved at an earlier visit"})
        products.append({"order_item_id": e["item"].id, "product": e["product"], "access_medium": _medium(e), "attributes": attrs})
    return {"location": inventory["serving_location"], "address": inventory["address"], "products": products, "summary": "",
            "notes": []}


def _real_values(inventory: dict[str, Any]) -> tuple[dict[str, list[str]], set[str]]:
    """Per resource type, the identifiers a default may name: held for the order first, then the free candidate."""
    by_type: dict[str, list[str]] = {}
    for h in inventory["held_for_order"]:
        by_type.setdefault(_key(h["resource_type"]), []).append(h["identifier"])
    for f in inventory["free_at_site"]:
        if f.get("candidate"):
            by_type.setdefault(_key(f["resource_type"]), []).append(str(f["candidate"]))
    return by_type, {v for values in by_type.values() for v in values}


def _merge(base: dict[str, Any], reply: dict[str, Any], inventory: dict[str, Any]) -> dict[str, Any]:
    by_type, real = _real_values(inventory)
    notes: list[str] = []
    answered = {_key(str(p.get("product") or "")): p for p in reply.get("products") or [] if isinstance(p, dict)}
    for product in base["products"]:
        got = answered.get(_key(product["product"])) or (next(iter(answered.values())) if len(base["products"]) == 1 and answered else {})
        rows = {_key(a["name"]): a for a in product["attributes"]}
        for a in got.get("attributes") or []:
            if not isinstance(a, dict) or not str(a.get("name") or "").strip():
                continue
            name = str(a["name"]).strip()[:100]
            value = "" if a.get("value") is None else str(a["value"]).strip()[:200]
            source = str(a.get("source") or "default")
            rtype = str(a.get("resource_type") or "") or None
            if source == "inventory" or (value and rtype):
                allowed = by_type.get(_key(rtype or ""), [])
                if value not in real and value not in allowed:
                    fixed = allowed[0] if allowed else ""
                    notes.append(f"{name}: '{value}' isn't an inventory resource held or free for this order - "
                                 + (f"used {fixed}" if fixed else "left for you to fill"))
                    value, source = fixed, ("inventory" if fixed else "engineer")
            row = rows.get(_key(name))
            options = [str(o) for o in a.get("options") or [] if str(o).strip()][:40]
            if row is None:
                row = {"name": name, "value": "", "default": "", "source": source, "resource_type": rtype, "options": options,
                       "required": bool(a.get("required")), "modelled": False, "saved": False, "reason": str(a.get("reason") or "")[:300]}
                product["attributes"].append(row)
                rows[_key(name)] = row
            else:  # a modelled attribute: its own values and required flag win
                row.update(source=source, resource_type=rtype, reason=str(a.get("reason") or row["reason"])[:300])
                if row["options"] and value and _key(value) not in {_key(o) for o in row["options"]}:
                    notes.append(f"{row['name']}: '{value}' isn't one of its values ({', '.join(row['options'])})")
                    value = row["default"] if _key(row["default"]) in {_key(o) for o in row["options"]} else ""
            row["default"] = value
            if not row["saved"]:
                row["value"] = value
    return {**base, "summary": str(reply.get("summary") or "")[:400], "notes": notes}


def save(db: Session, tenant_id: int, work_order_id: int, products: list[dict[str, Any]], user_name: str = "") -> dict[str, Any]:
    """products: [{"order_item_id", "values": {name: value}, "defaults": {name: the AI default shown}}]."""
    from app.crm_core import instance_service
    from app.models.product_config import OrderItemConfig

    wo, order = _work_order(db, tenant_id, work_order_id)
    entries = {e["item"].id: e for e in _items(db, tenant_id, order)}
    overridden: list[str] = []
    saved: list[str] = []
    network: dict[int, set[str]] = {}  # per order item, the network attribute names (for the job sheet)
    for p in products:
        entry = entries.get(int(p.get("order_item_id") or 0))
        if entry is None:
            raise ValueError(f"Order {order.id} has no item {p.get('order_item_id')}")
        values = {str(k).strip(): str(v).strip() for k, v in (p.get("values") or {}).items() if str(k).strip()}
        modelled = {_key(a["name"]) for a in entry["modelled"]}
        # modelled attributes are checked against the product's design (values, ranges); the extra network
        # attributes the AI proposed from the inventory set-up are kept as given
        to_check = {k: v for k, v in values.items() if _key(k) in modelled and v}
        checked = config.check_technical(db, tenant_id, entry["product"], to_check, entry["design"]) if to_check else {}
        checked_by_key = {_key(k): (k, v) for k, v in checked.items()}
        technical = {k: v for k, v in entry["technical"].items() if _key(k) not in {_key(n) for n in values}}
        for name, value in values.items():
            if value:
                stored_name, stored_value = checked_by_key.get(_key(name), (name, value))
                technical[stored_name] = stored_value
        network[entry["item"].id] = modelled | {_key(n) for n in values}
        defaults = {_key(k): str(v) for k, v in (p.get("defaults") or {}).items()}
        for name, value in values.items():
            default = defaults.get(_key(name))
            if default is not None and default != "" and value != default:
                overridden.append(f"{entry['product']} {name}: {default} → {value or '(blank)'}")
        row = db.query(OrderItemConfig).filter(OrderItemConfig.order_item_id == entry["item"].id).first()
        if row is None:
            row = OrderItemConfig(order_item_id=entry["item"].id, pricing_attributes_json=json.dumps(entry["pricing"]), technical_attributes_json="{}")
            db.add(row)
        row.technical_attributes_json = json.dumps(technical)
        inst = instance_service.get(db, tenant_id, "order", order.id, entry["product"])
        if inst is not None:
            instance_service.set_technical(inst, technical)
        saved.append(entry["product"])
        entry["technical"] = technical
    sheet = [line for line in (wo.technical_parameters or "").splitlines() if not line.startswith(("Network attributes", "  "))]
    for item_id, names in network.items():
        e = entries[item_id]
        net = {k: v for k, v in e["technical"].items() if _key(k) in names}
        if net:
            sheet.append(f"Network attributes - {e['product']}: " + "; ".join(f"{k}: {v}" for k, v in net.items()))
    if overridden:
        sheet.append(f"Network attributes overridden{' by ' + user_name if user_name else ''}:")
        sheet += [f"  {o}" for o in overridden]
    wo.technical_parameters = "\n".join(sheet)
    db.flush()
    return {"saved": saved, "overridden": overridden, "missing": missing(db, tenant_id, order), "job_sheet": wo.technical_parameters}


def missing(db: Session, tenant_id: int, order) -> list[str]:
    """Required "set at installation" attributes the order's products still lack."""
    return [f"{e['product']}: {m}" for e in _items(db, tenant_id, order) if e["modelled"]
            for m in config.missing_at_installation(e["design"], e["pricing"], e["technical"])]


def missing_for_work_order(db: Session, tenant_id: int, work_order) -> list[str]:
    if not work_order.order_id:
        return []
    from app.models.orders import Order

    order = db.get(Order, work_order.order_id)
    return missing(db, tenant_id, order) if order is not None else []

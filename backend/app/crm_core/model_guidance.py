"""AI model guidance - the Product Modeller's "Guidance" button.

For a product being modelled for the first time, or an existing model (live or
not), the tenant's AI engine reads what the product is - its name, segment,
list price, child products, product lines and classes - and proposes the
attributes that belong to THAT product type only:

  pricing   what the sales rep chooses and what changes the price
  service   service-level technical attributes known while selling (Access
            Medium, Service Type, CPE Type ...) - kind "technical", captured
            at sales

Network-level attributes (VLAN, port, IP block, OLT / ONT, circuit id ...) are
deliberately left out of the selling model: the AI marks existing ones "move to
installation" (capture_stage = installation) and never adds new ones - the
installation engineer gets them during service provisioning, prefilled from
Inventory (crm_core/network_guidance.py).

Nothing is saved by propose(). The administrator keeps the proposals they want
and apply() writes them to the product's WORKSPACE. A live product keeps
selling its released model version, and quotes / orders keep the definition
frozen on their product instance, until a new version is released
(Configure & Release) - so guidance on a live model never changes a deal in
flight.

Guidance comes from the AI engine only (llm.resolve): with no model set up,
or when the model fails, propose() says so rather than guessing.
"""

from __future__ import annotations

import json
import re
from typing import Any

from sqlalchemy.orm import Session

from app.crm_core import product_config_service as config
from app.models.attributes import ProductAttributeDefinition
from app.models.product_config import IMPACT_TYPES

_key = config._key
ACTIONS = ("add", "update", "keep", "move_to_installation", "remove")
# A name that is plainly a network resource - the AI is told to leave these to installation; this is the guard
# when it doesn't. Service-level choices (Access Medium, CPE Type, Service Type) don't match.
NETWORK_NAME = re.compile(
    r"\b(vlan|s-?vlan|c-?vlan|port|interface|ip\s*(address|block|range|subnet|pool)|subnet|wan\s*ip|lan\s*ip|gateway|"
    r"olt|ont|onu|splitter|pon|odf|fdh|fdt|dp\b|strand|fib(re|er)\s*(pair|core)|circuit\s*id|service\s*id|network\s*element|"
    r"ne\s*id|node|bts|enodeb|gnodeb|sector|frequency|channel\s*id|mac\s*address|serial|imsi|iccid|msisdn|pseudowire|lsp|vrf|"
    r"route\s*distinguisher|rd\b|rt\b|asn|bgp\s*(peer|neighbou?r)|dslam|pop\s*device|switch|router\s*(name|id|hostname)|hostname)\b",
    re.I,
)

_PROMPT = """You are the product-modelling assistant of a telecom BSS. You get ONE product (name, segment, monthly list price,
child products, product lines, classes), the tenant's attribute library and the product's current design (may be empty).

Work out what TYPE of product it is (e.g. fixed broadband over fibre, dedicated internet access, MPLS VPN, SIP trunk,
mobile data plan, managed security add-on, cloud backup ...), then propose its attributes - ONLY the ones a product of
that type really has. Never pad with generic attributes that don't fit (no Bandwidth on a voice product, no Codec on
broadband, no SIM type on a fixed line).

Two kinds belong in the selling model:
- "pricing": chosen by the sales rep and changes the price - give values and price_impacts relative to the list price
  ("flat" = amount per month, "percent" = % of the list price, "per_unit" with value "*" = amount per unit of a number).
- "technical" service attributes known while selling (Access Medium, Service Type, CPE Type, Handoff, Number of sites,
  Codec ...): what the customer chooses or the service needs, with no price.
NETWORK-LEVEL attributes (VLAN, ports, interfaces, IP addresses/blocks, OLT/ONT/splitter, circuit or service ids, network
elements, BTS/sector/frequency, serial numbers, routing identifiers) do NOT belong in the selling model: they are filled
in at installation from the network inventory. Never "add" one; for an existing one answer "move_to_installation".

MOBILE products (product line "mobile" = a plan; "mobile_addon" = a data / voice / SMS pack or bolt-on; "mobile_vas" = a
value-added service such as caller tune or voicemail; "roaming" = a roaming pack) are run by the mobile engine, which reads
these attribute names - use them exactly when they apply:
- "Payment type" (technical enum Prepaid / Postpaid; plans only),
- "Validity" (technical number, unit days - how long the plan or pack lasts before it renews or ends),
- "Data allowance" (e.g. 10 GB, 50 GB, Unlimited), "Voice minutes" (e.g. 300, 1000, Unlimited), "SMS" (e.g. 100, Unlimited) -
  pricing enums with price impacts when the customer chooses between sizes, else technical with the one value as default,
- "SIM type" (technical enum Physical SIM / eSIM; plans only), "Shared allowance" (technical boolean - a business pool other
  lines can join), "Roaming" (technical boolean - a pack whose allowance is used abroad).
MSISDN, ICCID and IMSI are network-level: assigned at activation from the number pool and SIM stock - never in the selling model.

For every attribute in current_design answer exactly one action: "keep" (fine as it is), "update" (give the full
improved spec), "move_to_installation" (network-level) or "remove" (doesn't belong to this product type at all).
New attributes: action "add". Prefer an attribute_library name when it is the same concept. 4-8 pricing+service
attributes in all is typical; mark required only what a quote can't do without; give a default when one is usual.

Return JSON only:
{"product_type": str, "summary": "one or two sentences on what you proposed and why",
 "attributes": [{"action": "add"|"update"|"keep"|"move_to_installation"|"remove", "name": str,
   "kind": "pricing"|"technical", "component": str|null (a child product's name, null = the product itself),
   "data_type": "enum"|"number"|"string"|"boolean", "unit": str|null, "values": [str], "min": number|null, "max": number|null,
   "required": bool, "default": str|null, "price_impacts": [{"value": str, "type": "flat"|"percent"|"per_unit", "amount": number}],
   "description": str|null, "reason": "why, in one short sentence"}]}"""


def _offering(db: Session, tenant_id: int, name: str):
    from app.crm_core import product_model_service as model

    offering = model.offering_of(db, tenant_id, name)
    if offering is None:
        raise ValueError(f"No product named '{name}'")
    return offering


def live_state(db: Session, tenant_id: int, name: str) -> dict[str, Any]:
    """Whether the model is live: published, a released version selling, deals holding a frozen copy."""
    from app.models.product_model import ProductInstance

    offering = _offering(db, tenant_id, name)
    released = config.released_version(db, tenant_id, offering.name)
    instances = (db.query(ProductInstance).filter(ProductInstance.tenant_id == tenant_id, ProductInstance.offering_name.ilike(offering.name))
                 .count())
    live = offering.status.value == "active"
    if released is not None:
        note = (f"{offering.name} sells released version {released.version}. What you apply goes to the workspace - release a new "
                f"version (Configure & Release) for it to reach new quotes; the {instances} quote/order cop{'y' if instances == 1 else 'ies'} "
                "already taken keep their definition.")
    elif live:
        note = (f"{offering.name} is published and sells from its workspace, so what you apply reaches new quotes straight away"
                + (f"; the {instances} quote/order cop{'y' if instances == 1 else 'ies'} already taken keep their definition." if instances else "."))
    else:
        note = f"{offering.name} is a draft - nothing sells it yet."
    return {"status": offering.status.value, "released_version": released.version if released else None, "open_instances": instances,
            "live": live, "note": note}


def _context(db: Session, tenant_id: int, offering) -> dict[str, Any]:
    from app.models.catalog import ProductSpecification

    names = [n.strip() for n in (offering.composition or "").split(",") if n.strip()]
    lines = sorted({s.product_line for s in db.query(ProductSpecification).filter(ProductSpecification.tenant_id == tenant_id,
                                                                                   ProductSpecification.name.in_(names or [""]))
                    if s.product_line})
    design = config.schema(db, tenant_id, offering.name, workspace=True)
    current = [{k: a.get(k) for k in ("name", "kind", "component", "data_type", "unit", "values", "min", "max", "required", "default",
                                       "reference_type", "capture_stage", "price_impacts", "inherited_from")}
               for a in design["attributes"]]
    library = sorted({d.name for d in db.query(ProductAttributeDefinition).filter(ProductAttributeDefinition.tenant_id == tenant_id)})
    return {
        "product": offering.name, "segment": offering.segment, "monthly_list_price": offering.price, "description": getattr(offering, "description", "") or "",
        "bundled_products": names, "product_lines": lines, "classes": config.classes_of(db, tenant_id, offering.name),
        "child_products": [{"name": c["name"], "charge_type": c.get("charge_type"), "price": c.get("price")} for c in design["components"]],
        "attribute_library": library[:300], "current_design": current,
    }


def _clean_impacts(raw: Any, values: list[str]) -> list[dict[str, Any]]:
    out = []
    for i in raw if isinstance(raw, list) else []:
        if not isinstance(i, dict):
            continue
        kind = str(i.get("type") or "flat")
        try:
            amount = float(i.get("amount") or 0)
        except (TypeError, ValueError):
            continue
        value = str(i.get("value") or "*").strip()
        if kind not in IMPACT_TYPES or not amount:
            continue
        if value != "*" and values and _key(value) not in {_key(v) for v in values}:
            continue  # an impact on a value the attribute doesn't offer
        out.append({"value": value, "type": kind, "amount": amount})
    return out


def _spec_op(a: dict[str, Any], components: set[str]) -> dict[str, Any]:
    """The design operation for an AI attribute spec (add / update)."""
    values = [str(v).strip() for v in a.get("values") or [] if str(v).strip()][:40]
    kind = "pricing" if str(a.get("kind") or "").lower() == "pricing" else "technical"
    data_type = str(a.get("data_type") or ("enum" if values else "string")).lower()
    op: dict[str, Any] = {"op": "upsert_attribute", "name": str(a["name"]).strip()[:100], "kind": kind,
                          "affects_price": kind == "pricing", "affects_provisioning": kind == "technical",
                          "data_type": data_type if data_type in ("enum", "number", "string", "boolean") else "string",
                          "values": values, "required": bool(a.get("required")), "capture_stage": "sales"}
    if data_type == "boolean" and not values:
        op["values"], op["data_type"] = ["Yes", "No"], "enum"
    for f in ("min", "max"):
        if isinstance(a.get(f), (int, float)):
            op[f] = float(a[f])
    if a.get("unit"):
        op["unit"] = str(a["unit"])[:30]
    default = a.get("default")
    if default not in (None, "") and (not op["values"] or _key(str(default)) in {_key(v) for v in op["values"]}):
        op["default"] = str(default)[:200]
    component = a.get("component")
    op["component"] = next((c for c in components if component and _key(c) == _key(str(component))), None)
    if a.get("description"):
        op["description"] = str(a["description"])[:1000]
    if kind == "pricing":
        op["price_impacts"] = _clean_impacts(a.get("price_impacts"), op["values"])
    return op


def _describe(action: str, op: dict[str, Any] | None) -> str:
    if action == "move_to_installation":
        return "Set at installation (network-level - prefilled from Inventory, not asked on the quote or at order approval)"
    if action == "remove":
        return "Remove - not part of this product type"
    if action == "keep":
        return "Keep as it is"
    return config.describe_ops([op])[0] if op else ""


def propose(db: Session, tenant_id: int, name: str) -> dict[str, Any]:
    from app.crm_core import llm

    offering = _offering(db, tenant_id, name)
    target = llm.resolve(db, tenant_id)
    if target is None:
        raise ValueError("Model guidance comes from the AI engine, and none is set up for this tenant - choose a model in Admin → AI")
    context = _context(db, tenant_id, offering)
    try:
        reply = llm.chat_json(target, _PROMPT, json.dumps(context, default=str) + "\n\nPropose this product's attributes.")
    except llm.LlmUnavailable as exc:
        raise ValueError(f"The AI engine couldn't give guidance just now ({exc}) - try again") from exc

    design = config.schema(db, tenant_id, offering.name, workspace=True)
    existing = {_key(a["name"]): a for a in design["attributes"]}
    components = {c["name"] for c in design["components"]}
    proposals: list[dict[str, Any]] = []
    notes: list[str] = []
    seen: set[str] = set()
    for a in reply.get("attributes") or []:
        if not isinstance(a, dict) or not str(a.get("name") or "").strip():
            continue
        k = _key(str(a["name"]))
        if k in seen:
            continue
        seen.add(k)
        action = str(a.get("action") or "add").lower()
        if action not in ACTIONS:
            continue
        current = existing.get(k)
        if current is None and action in ("keep", "move_to_installation", "remove"):
            continue  # about an attribute the product doesn't have
        if current is not None and action == "add":
            action = "update"
        if action in ("add", "update") and current is None and NETWORK_NAME.search(str(a["name"])):
            notes.append(f"Left out {a['name']} - network-level; the installation engineer sets it from Inventory")
            continue
        if action in ("add", "update") and current is not None and NETWORK_NAME.search(current["name"]) and current.get("kind") == "technical":
            action = "move_to_installation"
        if current is not None and current.get("inherited_from") and action == "remove":
            notes.append(f"{current['name']} comes from class {current['inherited_from']} - change it on the class to remove it")
            continue
        op: dict[str, Any] | None
        if action in ("add", "update"):
            op = _spec_op(a, components)
            if current is not None:
                op["name"] = current["name"]  # keep the existing spelling
        elif action == "move_to_installation":
            if current.get("kind") == "pricing":
                notes.append(f"{current['name']} changes the price, so it stays a sales choice")
                continue
            if config.at_installation(current):
                action, op = "keep", None
            else:
                op = {"op": "upsert_attribute", "name": current["name"], "capture_stage": "installation"}
        elif action == "remove":
            op = {"op": "remove_attribute", "name": current["name"]}
        else:
            op = None
        stage = "installation" if action == "move_to_installation" or (action == "keep" and config.at_installation(current)) else "sales"
        proposals.append({
            "id": len(proposals) + 1, "action": action, "name": op["name"] if op else current["name"],
            "kind": (op or {}).get("kind") or (current or {}).get("kind") or "technical", "stage": stage,
            "group": "network" if stage == "installation" else ("pricing" if ((op or {}).get("kind") or (current or {}).get("kind")) == "pricing" else "service"),
            "op": op, "current": config.describe_ops([_as_op(current)])[0] if current else None,
            "change": _describe(action, op), "reason": str(a.get("reason") or "")[:300],
            "selected": action != "keep",
        })
    # existing attributes the model said nothing about are kept as they are
    for k, current in existing.items():
        if k not in seen:
            proposals.append({"id": len(proposals) + 1, "action": "keep", "name": current["name"], "kind": current["kind"],
                              "stage": "installation" if config.at_installation(current) else "sales",
                              "group": "network" if config.at_installation(current) else ("pricing" if current["kind"] == "pricing" else "service"),
                              "op": None, "current": config.describe_ops([_as_op(current)])[0], "change": "Keep as it is",
                              "reason": "", "selected": False})
    return {"product": offering.name, "product_type": str(reply.get("product_type") or "")[:120],
            "summary": str(reply.get("summary") or "")[:600], "engine": "ai", "model": target.label,
            "live": live_state(db, tenant_id, offering.name), "proposals": proposals, "notes": notes}


def _as_op(a: dict[str, Any]) -> dict[str, Any]:
    return {"op": "upsert_attribute", "name": a["name"], "kind": a.get("kind"), "values": a.get("values") or [],
            "price_impacts": a.get("price_impacts") or [], "required": a.get("required"), "default": a.get("default")}


_ALLOWED_FIELDS = {"op", "name", "kind", "affects_price", "affects_provisioning", "data_type", "values", "required", "capture_stage",
                   "min", "max", "unit", "default", "component", "description", "price_impacts"}


def apply(db: Session, tenant_id: int, name: str, ops: list[dict[str, Any]]) -> dict[str, Any]:
    """The kept proposals, each on its own (one that doesn't fit - e.g. widening what a class allows - is reported,
    the rest still apply). Only attribute changes - guidance never writes rules."""
    offering = _offering(db, tenant_id, name)
    changes: list[str] = []
    errors: list[str] = []
    for raw in ops:
        if not isinstance(raw, dict) or raw.get("op") not in ("upsert_attribute", "remove_attribute") or not raw.get("name"):
            errors.append(f"Not an attribute change: {str(raw)[:80]}")
            continue
        op = {k: v for k, v in raw.items() if k in _ALLOWED_FIELDS}
        savepoint = db.begin_nested()
        try:
            changes += config.apply_design(db, tenant_id, offering.name, [op])
            if op.get("capture_stage") == "installation" and op["op"] == "upsert_attribute" and len(op) <= 3:
                changes[-1] = f"{op['name']} is now set at installation"
            savepoint.commit()
        except (ValueError, TypeError) as exc:
            savepoint.rollback()
            errors.append(f"{op.get('name')}: {exc}")
    db.flush()
    return {"applied": changes, "errors": errors, "live": live_state(db, tenant_id, offering.name)}

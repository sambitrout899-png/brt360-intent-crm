"""Product configuration engine (see app/models/product_config.py).

- schema(): a product's designed attributes and rules, as plain data.
- configure(): validates chosen pricing/technical attributes against the
  design and prices the pricing ones on top of a base price.
- price_list_for(): which pricelist applies - the quote's own, else the
  opportunity's, else the customer's, else the tenant default.
- apply_design(): design-time changes (add/update/remove an attribute, its
  values and price impacts, rules) - what the "Design product attributes"
  intent and the admin screen run, whether a person or the AI wrote them.

A product with no design accepts any attributes as given and is priced at
its base price - exactly how products behaved before configuration existed.
"""

from __future__ import annotations

import json
import re
from typing import Any

from sqlalchemy.orm import Session

from app.crm_core import price_formula
from app.models.attributes import ProductAttributeDefinition
from app.models.catalog import ProductOffering
from app.models.crm import Opportunity, Party, Quote
from app.models.process import PriceList
from app.models.product_config import (
    ATTRIBUTE_KINDS,
    IMPACT_TYPES,
    RULE_EFFECTS,
    AttributePriceImpact,
    OfferingAttribute,
    OfferingAttributeRule,
    QuoteTerms,
)


class ConfigurationError(ValueError):
    """The chosen attributes don't fit the product's design."""


def _key(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", str(name).lower())


def _split_values(raw: str | None) -> list[str]:
    return [v.strip() for v in (raw or "").split(",") if v.strip()]


def _num(value: Any) -> float | None:
    m = re.search(r"-?\d+(?:\.\d+)?", str(value).replace(",", ""))
    return float(m.group(0)) if m else None


def loads(raw: str | None) -> dict[str, Any]:
    try:
        value = json.loads(raw or "{}")
    except json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}


# ---------------------------------------------------------------- design (read)


def _rows(db: Session, tenant_id: int, offering_name: str) -> list[OfferingAttribute]:
    return (
        db.query(OfferingAttribute)
        .filter(OfferingAttribute.tenant_id == tenant_id, OfferingAttribute.offering_name.ilike(offering_name or ""),
                OfferingAttribute.is_active.is_(True))
        .order_by(OfferingAttribute.sequence, OfferingAttribute.id)
        .all()
    )


def _attribute_out(row: OfferingAttribute, components: dict[int, dict[str, Any]] | None = None) -> dict[str, Any]:
    d = row.definition
    values = _split_values(row.allowed_values) if row.allowed_values else _split_values(d.allowed_values)
    component = (components or {}).get(row.component_id) if row.component_id else None
    pricing = row.affects_price if row.affects_price is not None else row.kind == "pricing"
    provisioning = row.affects_provisioning if row.affects_provisioning is not None else row.kind == "technical"
    return {
        "id": row.id,
        "definition_id": d.id,
        "name": d.name,
        "kind": row.kind,
        "affects_price": bool(pricing),
        "affects_provisioning": bool(provisioning),
        "component": component["name"] if component else None,
        "component_id": row.component_id,
        "min": row.min_value,
        "max": row.max_value,
        "reference_type": row.reference_type,
        "description": row.description,
        "data_type": "reference" if row.reference_type else d.data_type,
        "unit": row.unit or d.unit,
        "values": values,
        "required": row.required,
        "default": row.default_value,
        "help": row.help,
        "visible_when": loads(row.visible_when_json) or None,
        "sequence": row.sequence,
        "price_impacts": [{"value": i.value, "type": i.impact_type, "amount": i.amount} for i in row.price_impacts],
    }


def components_of(db: Session, tenant_id: int, offering_name: str) -> list[dict[str, Any]]:
    """The product's child products, parents before children, with their path."""
    from app.models.product_model import ProductComponent

    rows = (db.query(ProductComponent)
            .filter(ProductComponent.tenant_id == tenant_id, ProductComponent.offering_name.ilike(offering_name or ""),
                    ProductComponent.is_active.is_(True))
            .order_by(ProductComponent.sequence, ProductComponent.id).all())
    by_id = {r.id: r for r in rows}

    def path(r) -> list[str]:
        out, seen = [r.name], {r.id}
        while r.parent_id and r.parent_id in by_id and r.parent_id not in seen:
            r = by_id[r.parent_id]
            seen.add(r.id)
            out.insert(0, r.name)
        return out

    slots = slots_of(db, tenant_id, list(by_id))
    out = [{"id": r.id, "name": r.name, "parent_id": r.parent_id if r.parent_id in by_id else None,
            "parent": by_id[r.parent_id].name if r.parent_id in by_id else None, "path": path(r), "description": r.description,
            "min_qty": r.min_qty, "max_qty": r.max_qty, "default_qty": r.default_qty, "mandatory": r.min_qty > 0,
            "charge_type": r.charge_type, "price": r.price, "affects_provisioning": r.affects_provisioning, "sequence": r.sequence,
            "slot": slots.get(r.id)}
           for r in rows]
    return sorted(out, key=lambda c: (len(c["path"]), c["sequence"], c["id"]))


def class_members(db: Session, tenant_id: int, class_name: str, seen: set[str] | None = None) -> list[str]:
    """Products of a class and of its sub-classes."""
    from app.models.product_model import ProductClass

    seen = seen or set()
    if _key(class_name) in seen:
        return []
    seen.add(_key(class_name))
    rows = db.query(ProductClass).filter(ProductClass.tenant_id == tenant_id).all()
    out: list[str] = []
    for r in rows:
        if _key(r.name) == _key(class_name):
            out += _split_values(r.members)
        elif r.parent_name and _key(r.parent_name) == _key(class_name):
            out += class_members(db, tenant_id, r.name, seen)
    return list(dict.fromkeys(out))


def _merge(parent: dict[str, Any], row: OfferingAttribute, child: dict[str, Any], parent_level: str) -> dict[str, Any]:
    """One attribute, as a sub-class / product sees it - object-oriented
    inheritance: everything the parent level says applies unless this level
    says otherwise, and a level may only NARROW what it inherits:
      values      a subset of the parent's (a value the parent doesn't offer is dropped)
      required    once required, always required
      min / max   only tighter
      default     must be one of the values left
      price       this level's impact per value wins, the parent's apply to the rest
    """
    out = dict(parent)
    parent_values = parent.get("values") or []
    if row.allowed_values and parent_values:
        allowed = {_key(v) for v in parent_values}
        out["values"] = [v for v in child["values"] if _key(v) in allowed]
    elif row.allowed_values:
        out["values"] = child["values"]
    out["narrowed"] = bool(row.allowed_values) and len(out["values"]) < len(parent_values)
    out["required"] = bool(parent.get("required")) or bool(row.required)
    lows = [x for x in (parent.get("min"), row.min_value) if x is not None]
    highs = [x for x in (parent.get("max"), row.max_value) if x is not None]
    out["min"], out["max"] = (max(lows) if lows else None), (min(highs) if highs else None)
    keys = {_key(v) for v in out["values"]}
    default = row.default_value if row.default_value not in (None, "") else parent.get("default")
    out["default"] = default if default in (None, "") or not keys or _key(default) in keys else None
    for field, value in (("help", row.help), ("description", row.description), ("reference_type", row.reference_type),
                         ("unit", row.unit), ("visible_when", child.get("visible_when")), ("component", child.get("component")),
                         ("component_id", row.component_id)):
        if value not in (None, ""):
            out[field] = value
    out["kind"] = row.kind
    if row.affects_price is not None:
        out["affects_price"] = bool(row.affects_price)
    if row.affects_provisioning is not None:
        out["affects_provisioning"] = bool(row.affects_provisioning)
    mine = {_key(i["value"]): i for i in child["price_impacts"]}
    impacts = [mine.pop(_key(i["value"]), i) for i in parent.get("price_impacts", [])] + list(mine.values())
    out["price_impacts"] = [i for i in impacts if i["value"] == "*" or not keys or _key(i["value"]) in keys]
    out.update(id=row.id, sequence=row.sequence, overrides=parent_level, class_values=parent_values)
    out.pop("inherited_from", None)
    return out


def class_chain(db: Session, tenant_id: int, offering_name: str) -> list[str]:
    """The classes a product (or class) inherits from, farthest ancestor first."""
    from app.models.product_model import ProductClass

    if offering_name.lower().startswith("class:"):
        rows = {_key(r.name): r for r in db.query(ProductClass).filter(ProductClass.tenant_id == tenant_id)}
        out: list[str] = []
        current = rows.get(_key(offering_name[6:]))
        current = rows.get(_key(current.parent_name or "")) if current is not None else None
        while current is not None and current.name not in out:
            out.insert(0, current.name)
            current = rows.get(_key(current.parent_name or ""))
        return out
    return list(reversed(classes_of(db, tenant_id, offering_name)))


def resolved_attributes(db: Session, tenant_id: int, offering_name: str, components: list[dict[str, Any]] | None = None,
                        upto: str | None = None) -> list[dict[str, Any]]:
    """The attributes a product (or class) really has: its classes', from the
    farthest ancestor down, each level narrowing the one above, then its own.
    `upto` stops before that level (what the level inherits)."""
    levels = [f"class:{c}" for c in class_chain(db, tenant_id, offering_name)] + [offering_name]
    if upto is not None:
        levels = levels[: next((i for i, lv in enumerate(levels) if _key(lv) == _key(upto)), len(levels))]
    by_comp = {c["id"]: c for c in (components or [])}
    merged: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for level in levels:
        own_level = _key(level) == _key(offering_name)
        for row in _rows(db, tenant_id, level):
            child = _attribute_out(row, by_comp if own_level else {})
            k = _key(child["name"])
            if k in merged:
                merged[k] = _merge(merged[k], row, child, merged[k].get("inherited_from") or merged[k].get("overrides") or level[6:])
                if not own_level:
                    merged[k]["inherited_from"] = level[6:]
            else:
                merged[k] = {**child, "inherited_from": level[6:]} if not own_level else child
                order.append(k)
    own = [k for k in order if not merged[k].get("inherited_from")]
    return [merged[k] for k in own] + [merged[k] for k in order if merged[k].get("inherited_from")]


def classes_of(db: Session, tenant_id: int, offering_name: str) -> list[str]:
    """The product's classes, nearest first, then their parents."""
    from app.models.product_model import ProductClass

    rows = db.query(ProductClass).filter(ProductClass.tenant_id == tenant_id).all()
    by_key = {_key(r.name): r for r in rows}
    out: list[str] = []
    for r in rows:
        if _key(offering_name) in {_key(m) for m in _split_values(r.members)}:
            current = r
            while current is not None and current.name not in out:
                out.append(current.name)
                current = by_key.get(_key(current.parent_name or ""))
    return out


def slots_of(db: Session, tenant_id: int, component_ids: list[int]) -> dict[int, dict[str, Any]]:
    """Relationship slots: which catalog products may fill each (with their price)."""
    from app.models.catalog import OfferingStatus
    from app.models.product_model import ProductSlotDomain

    if not component_ids:
        return {}
    out: dict[int, dict[str, Any]] = {}
    for row in db.query(ProductSlotDomain).filter(ProductSlotDomain.component_id.in_(component_ids)):
        names = class_members(db, tenant_id, row.class_name) if row.domain_type == "class" and row.class_name else _split_values(row.products)
        offers = {o.name.lower(): o for o in db.query(ProductOffering).filter(
            ProductOffering.tenant_id == tenant_id, ProductOffering.status == OfferingStatus.active)}
        products = [{"name": offers[n.lower()].name, "price": offers[n.lower()].price} for n in names if n.lower() in offers]
        out[row.component_id] = {"type": row.domain_type, "class": row.class_name, "products": products,
                                 "listed": names, "default": row.default_product}
    return out


def constraints_of(db: Session, tenant_id: int, offering_name: str) -> list[dict[str, Any]]:
    from app.models.product_model import ProductRule

    rows = (db.query(ProductRule)
            .filter(ProductRule.tenant_id == tenant_id, ProductRule.offering_name.ilike(offering_name or ""),
                    ProductRule.rule_type == "constraint", ProductRule.is_active.is_(True))
            .order_by(ProductRule.id).all())
    return [{"id": r.id, "message": r.message, "text": r.source_text, **loads(r.definition_json)} for r in rows]


def released_version(db: Session, tenant_id: int, offering_name: str, at=None):
    """The released model version valid at `at` (now), or None."""
    from app.models.product_model import ProductModelVersion
    from app.timeutils import utcnow

    at = at or utcnow()
    return (db.query(ProductModelVersion)
            .filter(ProductModelVersion.tenant_id == tenant_id, ProductModelVersion.offering_name.ilike(offering_name or ""),
                    ProductModelVersion.status == "released", ProductModelVersion.valid_from <= at)
            .order_by(ProductModelVersion.valid_from.desc(), ProductModelVersion.version.desc()).first())


def schema(db: Session, tenant_id: int, offering_name: str | None, workspace: bool = False) -> dict[str, Any]:
    """A product's design. Selling uses its released version valid today
    (workspace=False); a product never released, and the modeller
    (workspace=True), use the workspace - what is being edited."""
    if not offering_name:
        return {"offering_name": None, "designed": False, "attributes": [], "rules": [], "components": [], "constraints": [], "price_rules": []}
    if not workspace:
        version = released_version(db, tenant_id, offering_name)
        if version is not None:
            design = loads(version.snapshot_json)
            design["version"] = version.version
            # slot products keep their current catalog prices
            live = {c["name"]: c.get("slot") for c in components_of(db, tenant_id, offering_name)}
            for c in design.get("components", []):
                if c.get("slot") and live.get(c["name"]):
                    c["slot"]["products"] = [p for p in live[c["name"]]["products"]
                                             if _key(p["name"]) in {_key(n) for n in c["slot"].get("listed", [])} or c["slot"].get("type") == "class"]
            return design
    components = components_of(db, tenant_id, offering_name)
    # class inheritance (attribute library -> class -> product): see resolved_attributes
    attributes = resolved_attributes(db, tenant_id, offering_name, components)
    rules = (
        db.query(OfferingAttributeRule)
        .filter(OfferingAttributeRule.tenant_id == tenant_id, OfferingAttributeRule.offering_name.ilike(offering_name),
                OfferingAttributeRule.is_active.is_(True))
        .order_by(OfferingAttributeRule.id)
        .all()
    )
    return {
        "offering_name": offering_name,
        "designed": bool(attributes or components),
        "attributes": attributes,
        "components": components,
        "constraints": constraints_of(db, tenant_id, offering_name),
        "price_rules": price_formula.rules_of(db, tenant_id, offering_name),
        "version": None,
        "rules": [{"id": r.id, "if_attribute": r.if_attribute, "operator": r.operator, "if_value": r.if_value,
                   "then_attribute": r.then_attribute, "effect": r.effect, "then_value": r.then_value, "message": r.message}
                  for r in rules],
    }


def _find(attrs: list[dict[str, Any]], name: str) -> dict[str, Any] | None:
    k = _key(name)
    return next((a for a in attrs if _key(a["name"]) == k), None)


def component_qty(design: dict[str, Any], chosen: dict[str, Any], name: str | None) -> int:
    """How many of a child product the selection has (0 = not chosen) -
    a child of an unchosen child is not chosen either."""
    if not name:
        return 1
    comp = next((c for c in design.get("components", []) if _key(c["name"]) == _key(name)), None)
    if comp is None:
        return 0
    if comp["parent"] and component_qty(design, chosen, comp["parent"]) == 0:
        return 0
    value = next((v for k, v in chosen.items() if _key(k) == _key(comp["name"])), None)
    if comp.get("slot"):
        product = slot_product(comp, value)
        if value not in (None, ""):
            return 1 if product else 0
        return 1 if (comp["min_qty"] > 0 or comp["slot"].get("default")) else 0
    qty = _qty(value)
    return qty if qty is not None else max(comp["min_qty"], comp["default_qty"])


def slot_product(comp: dict[str, Any], value: Any) -> dict[str, Any] | None:
    """The catalog product filling a slot (by name; None = left empty)."""
    slot = comp.get("slot") or {}
    if value in (None, ""):
        value = slot.get("default")
    return next((p for p in slot.get("products", []) if _key(p["name"]) == _key(value or "")), None)


def _qty(value: Any) -> int | None:
    if value in (None, ""):
        return None
    text = str(value).strip().lower()
    if text in ("yes", "y", "true", "include", "included", "with"):
        return 1
    if text in ("no", "n", "false", "exclude", "excluded", "without", "none"):
        return 0
    number = _num(text)
    return int(number) if number is not None else None


_RATE = {"bps": 1 / 1000, "kbps": 1, "kb/s": 1, "kbs": 1, "k": 1, "mbps": 1000, "mb/s": 1000, "mbs": 1000, "m": 1000,
         "gbps": 1_000_000, "gb/s": 1_000_000, "g": 1_000_000}


def in_unit(value: Any, unit: str | None) -> float | None:
    """A number in the attribute's own unit - '4 Mbps' is 4096 for a Kbps range
    (1024 per step, as bandwidth is sold), plain numbers are taken as given."""
    m = re.match(r"^\s*(-?\d[\d,]*(?:\.\d+)?)\s*([a-zA-Z/]*)\s*$", str(value))
    if not m:
        return None
    number = float(m.group(1).replace(",", ""))
    given, target = m.group(2).lower(), (unit or "").lower().replace("(", "").replace(")", "")
    if given and given in _RATE and target in _RATE and _RATE[given] != _RATE[target]:
        steps = {1 / 1000: -1, 1: 0, 1000: 1, 1_000_000: 2}
        return number * (1024 ** (steps[_RATE[given]] - steps[_RATE[target]]))
    return number


def _visible(attr: dict[str, Any], chosen: dict[str, Any], design: dict[str, Any] | None = None) -> bool:
    if attr.get("component") and design is not None and component_qty(design, chosen, attr["component"]) == 0:
        return False
    cond = attr.get("visible_when")
    if not cond or not cond.get("attribute"):
        return True
    value = next((v for k, v in chosen.items() if _key(k) == _key(cond["attribute"])), None)
    return value is not None and _key(value) == _key(cond.get("equals", ""))


# ---------------------------------------------------------------- runtime


def parse_selection(raw: Any) -> dict[str, Any]:
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


def parse_against(attrs: list[dict[str, Any]], raw: str) -> dict[str, Any]:
    """'VLAN ID 120, CPE type Cisco' - plain words, matched to the design's
    attribute names (longest first); 'name = value' / 'name: value' too."""
    names = sorted((a["name"] for a in attrs), key=len, reverse=True)
    out: dict[str, Any] = {}
    for part in re.split(r"[,;\n]", raw or ""):
        part = part.strip()
        if not part:
            continue
        name = next((n for n in names if _key(part).startswith(_key(n)) and re.match(re.escape(n.split()[0]), part, re.I)), None)
        if name is not None:
            words = len(name.split())
            rest = " ".join(part.split()[words:]) if part.lower().startswith(name.lower()) else re.split(r"\s*[=:]\s*", part, maxsplit=1)[-1]
            value = re.sub(r"^\s*(?:is|=|:|to)\s*", "", rest).strip()
            if value:
                out[name] = value
                continue
        out.update(parse_selection(part))
    return out


def selection(db: Session, tenant_id: int, offering_name: str | None, raw: Any, design: dict[str, Any] | None = None) -> dict[str, Any]:
    """A selection as {name: value} - plain words read against the product's design."""
    if isinstance(raw, str):
        design = design or schema(db, tenant_id, offering_name)
        return parse_against(design["attributes"], raw) if design["designed"] else parse_selection(raw)
    return parse_selection(raw)


def _normalize(attrs: list[dict[str, Any]], raw: Any, kind: str, designed: bool, errors: list[str],
               components: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    out: dict[str, Any] = {}
    components = components or []
    named = attrs + [{"name": c["name"]} for c in components]
    selection = parse_against(named, raw) if designed and isinstance(raw, str) else parse_selection(raw)
    for name, value in selection.items():
        if not designed:
            out[name] = value
            continue
        comp = next((c for c in components if _key(c["name"]) == _key(name)), None)
        if comp is not None and comp.get("slot") and _find(attrs, name) is None:
            if _qty(value) == 0 and str(value).strip().lower() in ("no", "none", "0", "false", "without"):
                if comp["min_qty"] > 0:
                    errors.append(f"{comp['name']} is mandatory in this product")
                else:
                    out[comp["name"]] = "No"
                continue
            product = slot_product(comp, value)
            if product is None:
                names = ", ".join(p["name"] for p in comp["slot"]["products"]) or "none available"
                errors.append(f"{comp['name']} can be {names} - not '{value}'")
            else:
                out[comp["name"]] = product["name"]
            continue
        if comp is not None and _find(attrs, name) is None:
            qty = _qty(value)
            if qty is None:
                errors.append(f"{comp['name']}: say how many (or yes / no) - not '{value}'")
            elif qty and not comp["min_qty"] <= qty <= max(comp["max_qty"], comp["min_qty"]):
                errors.append(f"{comp['name']}: between {comp['min_qty']} and {comp['max_qty']}")
            elif qty == 0 and comp["min_qty"] > 0:
                errors.append(f"{comp['name']} is mandatory in this product")
            else:
                out[comp["name"]] = str(qty)
            continue
        attr = _find(attrs, name)
        if attr is None:
            names = ", ".join(a["name"] for a in attrs if a["kind"] == kind) or "none"
            errors.append(f"'{name}' is not a {kind} attribute of this product (its {kind} attributes: {names})")
            continue
        if attr["kind"] != kind and not (kind == "technical" and attr.get("affects_provisioning")):
            errors.append(f"'{attr['name']}' is a {attr['kind']} attribute - set it there")
            continue
        text = str(value).strip()
        if attr.get("min") is not None or attr.get("max") is not None:
            number = in_unit(text, attr.get("unit"))
            unit = f" {attr['unit']}" if attr.get("unit") else ""
            if number is None:
                errors.append(f"{attr['name']} must be a number{unit} - not '{text}'")
                continue
            if (attr.get("min") is not None and number < attr["min"]) or (attr.get("max") is not None and number > attr["max"]):
                errors.append(f"{attr['name']} must be between {attr['min']:g} and {attr['max']:g}{unit} - not '{text}'")
                continue
            out[attr["name"]] = text
            continue
        if attr["values"]:
            match = next((v for v in attr["values"] if _key(v) == _key(text)), None)
            if match is None:
                errors.append(f"{attr['name']} can be {', '.join(attr['values'])} - not '{text}'")
                continue
            text = match
        elif attr["data_type"] == "number" and _num(text) is None:
            errors.append(f"{attr['name']} must be a number - not '{text}'")
            continue
        out[attr["name"]] = text
    return out


def _compare(op: str, actual: Any, expected: Any) -> bool:
    if op == "any":
        return actual not in (None, "")
    if actual in (None, ""):
        return False
    a, b = _num(actual), _num(expected)
    if op in ("gt", "gte", "lt", "lte") and a is not None and b is not None:
        return {"gt": a > b, "gte": a >= b, "lt": a < b, "lte": a <= b}[op]
    same = _key(actual) == _key(expected)
    return same if op == "eq" else (not same if op == "ne" else False)


def validate(design: dict[str, Any], pricing: dict[str, Any], technical: dict[str, Any], require_technical: bool) -> list[str]:
    errors: list[str] = []
    if not design["designed"]:
        return errors
    chosen = {**pricing, **technical}
    for attr in design["attributes"]:
        if not attr["required"] or not _visible(attr, chosen, design):
            continue
        if attr["kind"] == "technical" and not require_technical:
            continue
        if chosen.get(attr["name"]) in (None, ""):
            errors.append(f"{attr['name']} is required")
    for rule in design["rules"]:
        actual = next((v for k, v in chosen.items() if _key(k) == _key(rule["if_attribute"])), None)
        if not _compare(rule["operator"], actual, rule["if_value"]):
            continue
        target = _find(design["attributes"], rule["then_attribute"])
        if target is not None and target["kind"] == "technical" and not require_technical:
            continue
        then = next((v for k, v in chosen.items() if _key(k) == _key(rule["then_attribute"])), None)
        why = rule["message"] or f"{rule['if_attribute']} {rule['if_value'] or ''}".strip()
        if rule["effect"] == "required" and then in (None, ""):
            errors.append(f"{rule['then_attribute']} is required when {why}")
        elif rule["effect"] == "requires" and (then is None or _key(then) != _key(rule["then_value"])):
            errors.append(f"{rule['then_attribute']} must be {rule['then_value']} when {why}")
        elif rule["effect"] == "excludes" and then is not None and _key(then) == _key(rule["then_value"]):
            errors.append(f"{rule['then_attribute']} can't be {rule['then_value']} when {why}")
    errors += constraint_errors(design, chosen, require_technical)
    return errors


def resource_use(design: dict[str, Any], chosen: dict[str, Any]) -> dict[str, dict[str, float]]:
    """Resource pools: {resource: {"provided": n, "used": n}} - child products
    (or the product itself) provide or consume a resource (ports, slots ...)."""
    pools: dict[str, dict[str, float]] = {}
    for rule in design.get("constraints", []):
        name = re.sub(r"(?<=[^s])s$", "", rule.get("resource") or "")
        if not name or not all(test_condition(design, chosen, c) for c in rule.get("when") or []):
            continue
        qty = component_qty(design, chosen, rule.get("by")) if rule.get("by") else 1
        pool = pools.setdefault(name, {"provided": 0.0, "used": 0.0})
        pool["provided" if rule.get("provide") is not None else "used"] += qty * float(rule.get("provide") if rule.get("provide") is not None else rule.get("consume") or 0)
    return pools


def resource_errors(design: dict[str, Any], chosen: dict[str, Any]) -> list[str]:
    return [f"{name}: {pool['used']:g} needed but only {pool['provided']:g} provided"
            for name, pool in resource_use(design, chosen).items() if pool["used"] > pool["provided"]]


def soft_messages(design: dict[str, Any], chosen: dict[str, Any]) -> list[str]:
    """Recommendations and informational messages (never block)."""
    out = []
    for rule in design.get("constraints", []):
        if not (rule.get("recommend") or rule.get("say")):
            continue
        if not all(test_condition(design, chosen, c) for c in rule.get("when") or []):
            continue
        if rule.get("recommend"):
            if _value_of(design, chosen, rule["recommend"]) in (None, "", 0, "No"):
                out.append(rule.get("message") or f"Recommended: {rule['recommend']} ({rule.get('text') or 'product rule'})")
        else:
            out.append(rule["say"])
    return out


def _value_of(design: dict[str, Any], chosen: dict[str, Any], name: str) -> Any:
    comp = next((c for c in design.get("components", []) if _key(c["name"]) == _key(name)), None)
    if comp is not None and not _find(design["attributes"], name):
        qty = component_qty(design, chosen, name)
        if comp.get("slot"):  # a slot's value is the product filling it
            product = slot_product(comp, next((v for k, v in chosen.items() if _key(k) == _key(name)), None))
            return product["name"] if product and qty else 0
        return qty
    return next((v for k, v in chosen.items() if _key(k) == _key(name)), None)


def test_condition(design: dict[str, Any], chosen: dict[str, Any], cond: dict[str, Any]) -> bool:
    """A constraint condition on an attribute or a child product (its quantity)."""
    name = str(cond.get("field") or "")
    actual = _value_of(design, chosen, name)
    op, expected = cond.get("op", "eq"), cond.get("value")
    attr = _find(design["attributes"], name)
    if op == "empty":
        return actual in (None, "", 0)
    if op == "not_empty":
        return actual not in (None, "", 0)
    if actual in (None, ""):
        return False
    if op in ("in", "not_in"):
        options = {_key(o) for o in (expected if isinstance(expected, list) else str(expected).split(","))}
        return (_key(actual) in options) == (op == "in")
    if op in ("gt", "gte", "lt", "lte"):
        unit = attr.get("unit") if attr else None
        a, b = in_unit(actual, unit), in_unit(expected, unit)
        if a is None or b is None:
            return False
        return {"gt": a > b, "gte": a >= b, "lt": a < b, "lte": a <= b}[op]
    a, b = _num(actual), _num(expected)
    same = (a == b) if isinstance(actual, int) and b is not None else _key(actual) == _key(expected)
    return same if op == "eq" else not same


def _is_technical_only(design: dict[str, Any], name: str) -> bool:
    attr = _find(design["attributes"], name)
    return attr is not None and attr["kind"] == "technical"


def constraint_errors(design: dict[str, Any], chosen: dict[str, Any], require_technical: bool = True) -> list[str]:
    errors: list[str] = resource_errors(design, chosen)
    for rule in design.get("constraints", []):
        if rule.get("resource") or rule.get("recommend") or rule.get("say"):
            continue
        if not all(test_condition(design, chosen, c) for c in rule.get("when") or []):
            continue
        for then in rule.get("then") or []:
            target = then.get("require") or then.get("exclude") or then.get("field")
            if not target or (not require_technical and _is_technical_only(design, target)):
                continue
            value = _value_of(design, chosen, target)
            if "require" in then and value in (None, "", 0):
                errors.append(rule.get("message") or f"{target} is required here ({rule.get('text') or 'product rule'})")
            elif "exclude" in then and value not in (None, "", 0):
                errors.append(rule.get("message") or f"{target} can't be chosen here ({rule.get('text') or 'product rule'})")
            elif "field" in then and value not in (None, "") and not test_condition(design, chosen, then):
                errors.append(rule.get("message") or f"{rule.get('text') or target + ' is not allowed here'}")
    return list(dict.fromkeys(errors))


def price(base: float, design: dict[str, Any], pricing: dict[str, Any]) -> tuple[float, list[dict[str, Any]]]:
    """Base price plus every chosen value's impact: flat add-ons, a % of the
    base, or an amount per unit of a numeric value."""
    lines: list[dict[str, Any]] = []
    total = base
    for comp in design.get("components", []):
        if comp.get("slot"):
            product = slot_product(comp, next((v for k, v in pricing.items() if _key(k) == _key(comp["name"])), None))
            if product and component_qty(design, pricing, comp["name"]) > 0 and product["price"]:
                lines.append({"label": f"{comp['name']}: {product['name']}", "amount": round(product["price"], 2)})
                total += product["price"]
            continue
        if not comp["charge_type"] or not comp["price"]:
            continue
        qty = component_qty(design, pricing, comp["name"])
        if qty <= 0:
            continue
        amount = round(comp["price"] * qty, 2)
        if comp["charge_type"] == "one_time":
            lines.append({"label": f"{comp['name']}{f' x {qty}' if qty > 1 else ''} (one-time)", "amount": amount, "one_time": True})
        else:
            lines.append({"label": f"{comp['name']}{f' x {qty}' if qty > 1 else ''}", "amount": amount})
            total += amount
    for attr in design["attributes"]:
        if attr["kind"] != "pricing" or attr["name"] not in pricing:
            continue
        value = pricing[attr["name"]]
        for impact in attr["price_impacts"]:
            if impact["value"] != "*" and _key(impact["value"]) != _key(value):
                continue
            if impact["type"] == "percent":
                amount = round(base * impact["amount"] / 100, 2)
            elif impact["type"] == "per_unit":
                units = in_unit(value, attr.get("unit")) if attr.get("unit") else None
                amount = round((units if units is not None else (_num(value) or 0)) * impact["amount"], 2)
            else:
                amount = round(impact["amount"], 2)
            if amount:
                lines.append({"label": f"{attr['name']}: {value}", "amount": amount})
                total += amount
    # price formulas (price_formula.py) - impacts worked out of several attributes / child products
    total = price_formula.apply(design, pricing, base, total, lines)
    return round(total, 2), lines


def _valid_default(attr: dict[str, Any], offering_name: str, notes: list[str]) -> str | None:
    """The attribute's default when it is one of its values. A stale default (the values were edited
    after it was set - e.g. 'Local' when Access Medium is now Wimax / Microwave / Fiber / GPON) is not
    applied as-is: the value the product's own name names is used ('Fiber' for Fiber 500 Bundle), else
    the first value when the attribute is required, else none - and the note says so."""
    default = str(attr["default"])
    values = [str(v) for v in attr.get("values") or [] if str(v).strip()]
    if not values or _key(default) in {_key(v) for v in values}:
        return default
    product = _key(offering_name or "")
    named = next((v for v in values if _key(v) and _key(v) in product), None)
    chosen = named or (values[0] if attr.get("required") else None)
    why = f"{attr['name']}: its default '{default}' is not one of its values ({', '.join(values)})"
    notes.append(f"{why} - used '{chosen}'{' (named in the product)' if named else ''}; fix the default in the Product Modeller"
                 if chosen else f"{why} - left it empty; fix the default in the Product Modeller")
    return chosen


def configure(
    db: Session, tenant_id: int, offering_name: str, pricing_raw: Any, technical_raw: Any, base: float,
    require_technical: bool = False, party: Party | None = None, others: list[str] | None = None, price_list: PriceList | None = None,
    design: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Validated, defaulted, priced configuration - raises ConfigurationError.
    With the customer known, the product's eligibility and compatibility
    rules are checked too (others = other products on the same quote/order).
    `design` is the product instance's frozen definition when the record has
    one (instance_service) - else the catalog's live definition."""
    design = priced_slots(db, tenant_id, design or schema(db, tenant_id, offering_name), price_list)
    errors: list[str] = []
    pricing = _normalize(design["attributes"], pricing_raw, "pricing", design["designed"], errors, design.get("components"))
    technical = _normalize(design["attributes"], technical_raw, "technical", design["designed"], errors)
    for comp in design.get("components", []):
        if comp["name"] in pricing or component_qty(design, pricing, comp["parent"]) == 0:
            continue
        if comp.get("slot"):
            default = slot_product(comp, None)
            if default is not None:
                pricing[comp["name"]] = default["name"]
            elif comp["min_qty"] > 0:
                errors.append(f"Choose the {comp['name']}: " + (", ".join(p["name"] for p in comp["slot"]["products"]) or "no product is available for it"))
        elif max(comp["min_qty"], comp["default_qty"]) > 0:
            pricing[comp["name"]] = str(max(comp["min_qty"], comp["default_qty"]))
    default_fixes: list[str] = []
    for attr in design["attributes"]:
        if attr["default"] not in (None, "") and attr["name"] not in pricing and attr["name"] not in technical \
                and _visible(attr, {**pricing, **technical}, design):
            value = _valid_default(attr, offering_name, default_fixes)
            if value is not None:
                (pricing if attr["kind"] == "pricing" else technical)[attr["name"]] = value
    errors += validate(design, pricing, technical, require_technical)
    if party is not None:
        from app.crm_core import product_model_service

        errors += product_model_service.customer_rule_errors(db, tenant_id, offering_name, party, others or [])
    if errors:
        raise ConfigurationError("; ".join(errors))
    configured, lines = price(base, design, pricing)
    return {"designed": design["designed"], "pricing": pricing, "technical": technical, "base": base,
            "lines": lines, "configured": configured, "model_version": design.get("version"),
            "messages": soft_messages(design, {**pricing, **technical}) + default_fixes, "default_fixes": default_fixes,
            "one_time": round(sum(line["amount"] for line in lines if line.get("one_time")), 2)}


def check_technical(db: Session, tenant_id: int, offering_name: str, technical_raw: Any, design: dict[str, Any] | None = None) -> dict[str, Any]:
    """Technical attribute values checked against the design (names and
    allowed values only - which ones are still missing is reported apart)."""
    design = design or schema(db, tenant_id, offering_name)
    errors: list[str] = []
    technical = _normalize(design["attributes"], technical_raw, "technical", design["designed"], errors)
    if errors:
        raise ConfigurationError("; ".join(errors))
    return technical


def missing_technical(db: Session, tenant_id: int, offering_name: str, pricing: dict[str, Any], technical: dict[str, Any],
                      design: dict[str, Any] | None = None) -> list[str]:
    design = design or schema(db, tenant_id, offering_name)
    return [e for e in validate(design, pricing, technical, True) if e not in validate(design, pricing, technical, False)]


def technical_text(technical: dict[str, Any]) -> str:
    return "; ".join(f"{k}: {v}" for k, v in technical.items())


def carry_to_order(db: Session, tenant_id: int, quote_item, order_item, opportunity: Opportunity | None = None) -> dict[str, Any] | None:
    """Copies what a quoted product was configured with onto the order item
    (technical attributes captured on the opportunity included). Nothing is
    written for a product that was sold without any configuration."""
    from app.models.product_config import OrderItemConfig, QuoteItemConfig

    item_config = db.query(QuoteItemConfig).filter(QuoteItemConfig.quote_item_id == quote_item.id).first() if quote_item else None
    pricing = loads(item_config.pricing_attributes_json) if item_config else {}
    technical = loads(opportunity.config.technical_attributes_json) if opportunity is not None and opportunity.config else {}
    technical.update(loads(item_config.technical_attributes_json) if item_config else {})
    offering = db.get(ProductOffering, order_item.product_offering_id)
    # the order gets its own instance - a copy of the quote's frozen definition
    from app.crm_core import instance_service

    quote_inst = instance_service.get(db, tenant_id, "quote", quote_item.quote_id, offering.name) if quote_item is not None and offering else None
    order_inst = None
    if offering is not None and (quote_inst is not None or schema(db, tenant_id, offering.name)["designed"]):
        order_inst = instance_service.ensure(db, tenant_id, "order", order_item.order_id, offering.name, source=quote_inst, item_id=order_item.id)
    frozen = instance_service.design(order_inst)
    if offering is not None and pricing:
        design = frozen or schema(db, tenant_id, offering.name)
        for name, value in pricing.items():
            attr = _find(design["attributes"], name)
            comp = next((c for c in design.get("components", []) if _key(c["name"]) == _key(name)), None)
            if (attr is not None and attr.get("affects_provisioning")) or (comp is not None and comp["affects_provisioning"] and _qty(value)):
                technical.setdefault(name, value)
    if order_inst is not None:
        instance_service.set_technical(order_inst, technical)
    if not pricing and not technical:
        offering = db.get(ProductOffering, order_item.product_offering_id)
        if offering is None or not (frozen or schema(db, tenant_id, offering.name))["designed"]:
            return None
    db.add(OrderItemConfig(order_item_id=order_item.id, pricing_attributes_json=json.dumps(pricing),
                           technical_attributes_json=json.dumps(technical)))
    db.flush()
    return {"pricing": pricing, "technical": technical}


def order_item_attributes(db: Session, order_item_id: int | None) -> tuple[dict[str, Any], dict[str, Any]] | None:
    """(pricing, technical) for an order item, or None when it carries none."""
    from app.models.product_config import OrderItemConfig

    row = db.query(OrderItemConfig).filter(OrderItemConfig.order_item_id == order_item_id).first() if order_item_id else None
    return (loads(row.pricing_attributes_json), loads(row.technical_attributes_json)) if row else None


def order_missing_by_product(db: Session, tenant_id: int, order, overridden: bool = False) -> list[tuple[str, list[str]]]:
    """(product, what it still lacks) for each of an order's products missing required technical attributes.
    overridden: Presales passed the deal over the inventory check (feasibility_service.order_overridden), so
    the attributes provisioning fills from Inventory (inventory references) are assigned at fulfilment - they
    aren't asked for here."""
    from app.crm_core import instance_service

    out: list[tuple[str, list[str]]] = []
    for item in order.items:
        attrs = order_item_attributes(db, item.id)
        offering = db.get(ProductOffering, item.product_offering_id)
        if attrs is None or offering is None:
            continue
        frozen = instance_service.design_for(db, tenant_id, "order", order.id, offering.name)
        missing = missing_technical(db, tenant_id, offering.name, attrs[0], attrs[1], frozen)
        if overridden and missing:
            references = [a["name"] for a in (frozen or schema(db, tenant_id, offering.name))["attributes"] if a.get("reference_type")]
            missing = [m for m in missing if not any(m.startswith(f"{name} is required") for name in references)]
        if missing:
            out.append((offering.name, missing))
    return out


def order_missing_technical(db: Session, tenant_id: int, order, overridden: bool = False) -> list[str]:
    """Required technical attributes an order's products still lack."""
    return [f"{name}: {m}" for name, missing in order_missing_by_product(db, tenant_id, order, overridden) for m in missing]


# ---------------------------------------------------------------- pricelists


def price_list_for(
    db: Session, tenant_id: int, party: Party | None = None, opportunity: Opportunity | None = None,
    quote: Quote | None = None, explicit: Any = None,
) -> PriceList | None:
    """Precedence: the one named now > the quote's > the opportunity's > the customer's > the default."""
    from app.crm_core import process_service

    def active(price_list_id: int | None) -> PriceList | None:
        found = db.get(PriceList, price_list_id) if price_list_id else None
        return found if found is not None and found.tenant_id == tenant_id and found.in_effect() else None

    if explicit not in (None, ""):
        found = process_service.find_price_list(db, tenant_id, explicit)
        if found is None:
            raise ValueError(f"No active pricelist '{explicit}' in effect today")
        return found
    if quote is not None:
        terms = db.query(QuoteTerms).filter(QuoteTerms.quote_id == quote.id).first()
        if terms is not None and (found := active(terms.price_list_id)):
            return found
    if opportunity is not None and opportunity.config is not None and (found := active(opportunity.config.price_list_id)):
        return found
    return process_service.price_list_of(db, tenant_id, party)


def priced_slots(db: Session, tenant_id: int, design: dict[str, Any], price_list: PriceList | None) -> dict[str, Any]:
    """Products filling a slot are priced from the same pricelist as the product."""
    from app.models.catalog import OfferingStatus

    if price_list is None or not any(c.get("slot") for c in design.get("components", [])):
        return design
    design = json.loads(json.dumps(design, default=str))
    for comp in design["components"]:
        for p in (comp.get("slot") or {}).get("products", []):
            offering = (db.query(ProductOffering).filter(ProductOffering.tenant_id == tenant_id, ProductOffering.name.ilike(p["name"]),
                                                         ProductOffering.status == OfferingStatus.active)
                        .order_by(ProductOffering.version.desc()).first())
            if offering is not None:
                p["price"] = base_price(db, tenant_id, price_list, offering)[0]
    return design


def base_price(db: Session, tenant_id: int, price_list: PriceList | None, offering: ProductOffering,
               quantity: Any = 1) -> tuple[float, str]:
    """A unit's base price: the pricelist's price for the product at this
    quantity (its volume tier), else the list price adjusted by the list."""
    from app.crm_core import process_service

    if price_list is None:
        return offering.price, "list price"
    item = process_service.price_item(db, price_list, offering.name, quantity)
    if item is not None:
        return item.price, process_service.tier_source(price_list, item)
    if price_list.adjustment_pct:
        return round(offering.price * (1 + price_list.adjustment_pct / 100), 2), f"pricelist '{price_list.name}' ({price_list.adjustment_pct:+g}%)"
    return offering.price, "list price"


# ---------------------------------------------------------------- design (write)


def _definition(db: Session, tenant_id: int, name: str, data_type: str, unit: str | None, values: list[str]) -> ProductAttributeDefinition:
    rows = db.query(ProductAttributeDefinition).filter(ProductAttributeDefinition.tenant_id == tenant_id).all()
    found = next((d for d in rows if _key(d.name) == _key(name)), None)
    if found is None:
        found = ProductAttributeDefinition(tenant_id=tenant_id, name=name.strip(), data_type=data_type, unit=unit,
                                           allowed_values=",".join(values) or None)
        db.add(found)
        db.flush()
    elif values:
        # The shared attribute offers every value any product uses.
        merged = _split_values(found.allowed_values)
        merged += [v for v in values if _key(v) not in {_key(m) for m in merged}]
        found.allowed_values = ",".join(merged)
        if found.data_type not in ("enum",) and data_type == "enum":
            found.data_type = "enum"
    if unit and not found.unit:
        found.unit = unit
    return found


def _upsert_attribute(db: Session, tenant_id: int, offering_name: str, op: dict[str, Any]) -> str:
    name = str(op.get("name") or "").strip()
    if not name:
        raise ValueError("An attribute needs a name")
    kind = str(op.get("kind") or "").lower() or None
    if kind is None and op.get("affects_price") is not None:
        kind = "pricing" if op["affects_price"] else "technical"
    if kind is not None and kind not in ATTRIBUTE_KINDS:
        raise ValueError(f"{name}: kind is pricing or technical")
    values = [str(v).strip() for v in (op.get("values") or []) if str(v).strip()]
    inherited = _find(resolved_attributes(db, tenant_id, offering_name, upto=offering_name), name)
    if inherited is not None:
        # object-oriented: a sub-class / product narrows what it inherits, never widens it
        level = inherited.get("inherited_from") or "its class"
        allowed = inherited.get("values") or []
        bad = [v for v in values if allowed and _key(v) not in {_key(a) for a in allowed}]
        if bad:
            raise ValueError(f"{name}: {', '.join(bad)} {'is' if len(bad) == 1 else 'are'} not offered by class {level} "
                             f"({', '.join(allowed)}) - a product can only narrow the values it inherits; change the class to offer more")
        default = op.get("default")
        if default not in (None, "") and (values or allowed) and _key(default) not in {_key(v) for v in (values or allowed)}:
            raise ValueError(f"{name}: the default {default} isn't one of its values")
        # (required is OR-ed in resolved_attributes - a class's required attribute stays required)
        if kind is None:
            kind = inherited["kind"]
    data_type = str(op.get("data_type") or ("enum" if values else "string")).lower()
    if data_type not in ("enum", "number", "string", "boolean"):
        data_type = "string"
    definition = _definition(db, tenant_id, name, data_type, op.get("unit") or None, values)
    row = (
        db.query(OfferingAttribute)
        .filter(OfferingAttribute.tenant_id == tenant_id, OfferingAttribute.offering_name.ilike(offering_name),
                OfferingAttribute.attribute_definition_id == definition.id)
        .first()
    )
    created = row is None
    if row is None:
        count = db.query(OfferingAttribute).filter(OfferingAttribute.tenant_id == tenant_id,
                                                   OfferingAttribute.offering_name.ilike(offering_name)).count()
        row = OfferingAttribute(tenant_id=tenant_id, offering_name=offering_name, attribute_definition_id=definition.id,
                                kind=kind or "pricing", sequence=count)
        db.add(row)
    row.is_active = True
    if kind:
        row.kind = kind
    if values:
        row.allowed_values = ",".join(values)
    for field in ("required",):
        if op.get(field) is not None:
            setattr(row, field, bool(op[field]))
    if "default" in op:
        row.default_value = str(op["default"]) if op["default"] not in (None, "") else None
    if "help" in op:
        row.help = op["help"] or None
    if "visible_when" in op:
        row.visible_when_json = json.dumps(op["visible_when"]) if op["visible_when"] else None
    if "component" in op:
        component = None
        if op["component"]:
            component = next((c for c in components_of(db, tenant_id, offering_name) if _key(c["name"]) == _key(op["component"])), None)
            if component is None:
                raise ValueError(f"{name}: {offering_name} has no child product '{op['component']}'")
        row.component_id = component["id"] if component else None
    for field, column in (("affects_price", "affects_price"), ("affects_provisioning", "affects_provisioning")):
        if field in op:
            setattr(row, column, None if op[field] is None else bool(op[field]))
    for field, column in (("min", "min_value"), ("max", "max_value")):
        if field in op:
            setattr(row, column, None if op[field] in (None, "") else float(op[field]))
    if "reference_type" in op:
        row.reference_type = op["reference_type"] or None
    if op.get("unit") and op["unit"] != definition.unit:
        row.unit = op["unit"]
    if "description" in op:
        row.description = op["description"] or None
    if op.get("price_impacts") is not None:
        impacts = []
        for impact in op["price_impacts"]:
            impact_type = str(impact.get("type") or "flat")
            if impact_type not in IMPACT_TYPES:
                raise ValueError(f"{name}: price impact type is one of {', '.join(IMPACT_TYPES)}")
            impacts.append(AttributePriceImpact(value=str(impact.get("value") or "*"), impact_type=impact_type,
                                                amount=float(impact.get("amount") or 0)))
        row.price_impacts.clear()
        db.flush()  # old impacts go before the new ones arrive (one per value)
        row.price_impacts.extend(impacts)
    db.flush()
    return f"{'Added' if created else 'Updated'} {row.kind} attribute {definition.name}"


def apply_design(db: Session, tenant_id: int, offering_name: str, ops: list[dict[str, Any]]) -> list[str]:
    """Runs design operations; returns what changed, in words."""
    changes: list[str] = []
    for op in ops:
        kind = op.get("op")
        if kind == "upsert_attribute":
            changes.append(_upsert_attribute(db, tenant_id, offering_name, op))
        elif kind == "remove_attribute":
            target = _find([_attribute_out(r) for r in _rows(db, tenant_id, offering_name)], op.get("name") or "")
            if target is None:
                raise ValueError(f"{offering_name} has no attribute '{op.get('name')}'")
            db.get(OfferingAttribute, target["id"]).is_active = False
            changes.append(f"Removed attribute {target['name']}")
        elif kind == "add_rule":
            effect = op.get("effect") or "required"
            if effect not in RULE_EFFECTS:
                raise ValueError(f"A rule's effect is one of {', '.join(RULE_EFFECTS)}")
            db.add(OfferingAttributeRule(
                tenant_id=tenant_id, offering_name=offering_name, if_attribute=str(op["if_attribute"]).strip(),
                operator=op.get("operator") or "eq", if_value=op.get("if_value"), then_attribute=str(op["then_attribute"]).strip(),
                effect=effect, then_value=op.get("then_value"), message=op.get("message"),
            ))
            changes.append(f"Added rule: if {op['if_attribute']} {op.get('if_value') or ''} then {op['then_attribute']} "
                           f"{'is required' if effect == 'required' else ('must be ' if effect == 'requires' else 'must not be ') + str(op.get('then_value'))}")
        elif kind == "remove_rule":
            rule = db.get(OfferingAttributeRule, int(op["id"]))
            if rule is None or rule.tenant_id != tenant_id:
                raise ValueError("Rule not found")
            rule.is_active = False
            changes.append("Removed a rule")
        else:
            raise ValueError(f"Unknown design operation '{kind}'")
    db.flush()
    return changes


# ---------------------------------------------------------------- AI-assisted design


_PROMPT = """You turn a telecom product manager's description into product-configuration operations.
Return JSON: {"ops": [...], "reply": "one sentence"}. Operations:
{"op":"upsert_attribute","name":str,"kind":"pricing"|"technical","data_type":"enum"|"number"|"string"|"boolean",
 "unit":str|null,"values":[str],"required":bool,"default":str|null,"help":str|null,
 "visible_when":{"attribute":str,"equals":str}|null,
 "price_impacts":[{"value":str ("*" = any value),"type":"flat"|"percent"|"per_unit","amount":number}]}
{"op":"remove_attribute","name":str}
{"op":"add_rule","if_attribute":str,"operator":"eq"|"ne"|"gt"|"gte"|"lt"|"lte"|"any","if_value":str|null,
 "then_attribute":str,"effect":"required"|"requires"|"excludes","then_value":str|null}
Pricing attributes change the monthly price (flat add-on, % of base, or amount per unit of a number);
technical attributes are what network provisioning needs (VLAN, CPE, IP block, port, circuit ID ...) and have no price.
Only include fields the user stated or that clearly follow. Keep names as the user wrote them."""

_IMPACT = re.compile(r"^(?P<value>.+?)\s*[\(\[@]?\s*(?P<sign>[+-])\s*(?:rs\.?|inr|₹)?\s*(?P<num>\d[\d,]*(?:\.\d+)?)\s*(?P<pct>%)?\s*[\)\]]?$", re.I)
_PER_UNIT = re.compile(r"(?:₹|rs\.?|inr)?\s*(?P<num>\d[\d,]*(?:\.\d+)?)\s*(?:each|per\s+(?:unit|ip|site|line|user|port|\w+))", re.I)
_RULE = re.compile(
    r"^if\s+(?P<a>.+?)\s+(?:is|=|==)\s+(?P<av>.+?)\s*(?:,|then)\s*(?:then\s+)?(?P<b>.+?)\s+"
    r"(?:(?P<req>is\s+required|is\s+mandatory|required|mandatory)|(?P<neg>must\s+not\s+be|cannot\s+be|can't\s+be|is\s+not)\s+(?P<nv>.+)|"
    r"(?:must\s+be|should\s+be|is|=)\s+(?P<pv>.+))$",
    re.I,
)


def _parse_attribute(clause: str, kind: str | None) -> dict[str, Any] | None:
    flags = r"\b(required|mandatory|optional|number|numeric|text|default)\b"
    if ":" in clause:
        name, rest = clause.split(":", 1)
    else:
        m = re.search(flags + r"|\(|" + _PER_UNIT.pattern, clause, re.I)
        name, rest = (clause[: m.start()], clause[m.start():]) if m else (clause, "")
    name = re.sub(r"\(.*?\)", "", name).strip(" -")
    if not name or len(name) > 60:
        return None
    unit = None
    if m := re.search(r"\(([^)]*)\)", clause.split(":", 1)[0]):
        unit = m.group(1).strip() or None
    rest = rest.strip()
    op: dict[str, Any] = {"op": "upsert_attribute", "name": name}
    lowered = rest.lower()
    if re.search(r"\b(required|mandatory)\b", lowered):
        op["required"] = True
    elif re.search(r"\boptional\b", lowered):
        op["required"] = False
    if m := re.search(r"\bdefault\s+(?:is\s+)?([^,;]+)", rest, re.I):
        op["default"] = m.group(1).strip()
        rest = rest[: m.start()] + rest[m.end():]
    if m := _PER_UNIT.search(rest):
        op.update(data_type="number", price_impacts=[{"value": "*", "type": "per_unit", "amount": float(m.group("num").replace(",", ""))}])
        kind = "pricing"
    else:
        body = re.sub(r"\b(required|mandatory|optional|number|numeric|text)\b", "", rest, flags=re.I).strip(" ,()")
        tokens = [t.strip() for t in re.split(r",|\bor\b", body) if t.strip()]
        if len(tokens) == 1 and re.search(r"[A-Za-z]/[A-Za-z]", tokens[0]):
            tokens = [t.strip() for t in re.split(r"(?<=[A-Za-z])/(?=[A-Za-z])", tokens[0]) if t.strip()]  # "Cisco/Huawei"
        values, impacts = [], []
        for token in tokens:
            im = _IMPACT.match(token)
            if im:
                value = im.group("value").strip(" (")
                amount = float(im.group("num").replace(",", "")) * (-1 if im.group("sign") == "-" else 1)
                values.append(value)
                impacts.append({"value": value, "type": "percent" if im.group("pct") else "flat", "amount": amount})
            else:
                values.append(token)
        if values:
            op["values"] = values
            op["data_type"] = "enum"
        elif re.search(r"\b(number|numeric)\b", lowered):
            op["data_type"] = "number"
        if impacts:
            op["price_impacts"] = impacts
            kind = "pricing"  # a value with a price is a pricing attribute, whatever heading it sits under
    if unit:
        op["unit"] = unit
    op["kind"] = kind or "pricing"
    return op


_VERB = re.compile(r"^(?:please\s+)?(make|set|change|increase|decrease|reduce|raise|lower|add|give|put|mark|remove|delete|drop|charge)\b", re.I)
_AMOUNT = r"(?:₹|rs\.?|inr)?\s*(?P<num>\d[\d,]*(?:\.\d+)?)\s*(?P<pct>%)?"


def _as_op(attr: dict[str, Any]) -> dict[str, Any]:
    return {"op": "upsert_attribute", "name": attr["name"], "kind": attr.get("kind", "pricing"), "data_type": attr.get("data_type"),
            "unit": attr.get("unit"), "values": list(attr.get("values") or []), "required": attr.get("required", False),
            "default": attr.get("default"), "price_impacts": [dict(i) for i in attr.get("price_impacts") or []]}


def _edit(clause: str, known: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Everyday edits to attributes the product (or the draft) already has:
    'make 1 Gbps 1500 more', 'make 36 months 10% cheaper', 'add a 60 months
    option to Contract term at -15%', 'remove 12 months from Contract term',
    'make VLAN ID required' / 'SLA optional'."""
    def attr_named(name: str) -> dict[str, Any] | None:
        return next((a for a in known if _key(a["name"]) == _key(name)), None)

    def attr_with_value(value: str) -> tuple[dict[str, Any], str] | None:
        for a in known:
            match = next((v for v in a.get("values") or [] if _key(v) == _key(value)), None)
            if match:
                return a, match
        return None

    if m := re.match(r"^(?:make|set|mark)\s+(?:the\s+)?(?P<a>.+?)\s+(?P<req>required|mandatory|optional)$", clause, re.I):
        attr = attr_named(m.group("a"))
        if attr:
            return {**_as_op(attr), "required": m.group("req").lower() != "optional"}
    m = re.match(rf"^(?:make|set|charge)\s+(?P<v>.+?)\s+{_AMOUNT}\s+(?P<dir>more|extra|higher|dearer|less|cheaper|lower|off)$", clause, re.I) \
        or re.match(rf"^(?:increase|raise)\s+(?P<v>.+?)\s+by\s+{_AMOUNT}$", clause, re.I) \
        or re.match(rf"^(?:decrease|reduce|lower)\s+(?P<v>.+?)\s+by\s+{_AMOUNT}$", clause, re.I)
    if m:
        found = attr_with_value(m.group("v"))
        if found:
            attr, value = found
            amount = float(m.group("num").replace(",", ""))
            if (m.groupdict().get("dir") or "").lower() in ("less", "cheaper", "lower", "off") or clause.lower().startswith(("decrease", "reduce", "lower")):
                amount = -amount
            op = _as_op(attr)
            op["price_impacts"] = [i for i in op["price_impacts"] if _key(i["value"]) != _key(value)] + [
                {"value": value, "type": "percent" if m.group("pct") else "flat", "amount": amount}]
            op["kind"] = "pricing"
            return op
    if m := re.match(rf"^(?:add|put)\s+(?:an?\s+)?(?:option\s+)?(?P<v>.+?)\s+(?:option\s+)?(?:to|in|on)\s+(?P<a>.+?)(?:\s+(?:at|for|with)\s+(?P<sign>[+-])?{_AMOUNT})?$", clause, re.I):
        attr = attr_named(m.group("a"))
        if attr:
            op = _as_op(attr)
            value = m.group("v").strip()
            op["values"] = [v for v in op["values"] if _key(v) != _key(value)] + [value]
            if m.group("num"):
                amount = float(m.group("num").replace(",", "")) * (-1 if m.group("sign") == "-" else 1)
                op["price_impacts"] = [i for i in op["price_impacts"] if _key(i["value"]) != _key(value)] + [
                    {"value": value, "type": "percent" if m.group("pct") else "flat", "amount": amount}]
            return op
    if m := re.match(r"^(?:remove|delete|drop)\s+(?P<v>.+?)\s+from\s+(?P<a>.+)$", clause, re.I):
        attr = attr_named(m.group("a"))
        if attr:
            op = _as_op(attr)
            op["values"] = [v for v in op["values"] if _key(v) != _key(m.group("v"))]
            op["price_impacts"] = [i for i in op["price_impacts"] if _key(i["value"]) != _key(m.group("v"))]
            return op
    if m := re.match(r"^(?:add)\s+(?:an?\s+|the\s+)?(?P<k>pricing|price|technical|tech|provisioning)?\s*(?:attribute\s+)?(?P<rest>.+)$", clause, re.I):
        kind = m.group("k")
        rest = re.sub(r"^attribute\s+", "", m.group("rest").strip(), flags=re.I)
        kind = "technical" if kind and kind.lower() in ("technical", "tech", "provisioning") else ("pricing" if kind else None)
        return _parse_attribute(rest, kind)
    return None


def _rules(instruction: str, known: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    ops: list[dict[str, Any]] = []
    kind: str | None = None
    known = [dict(a) for a in known or []]
    for raw in re.split(r"[;\n]+|\.\s+|\s+and\s+(?=(?:make|set|add|remove|delete|increase|decrease|reduce|mark)\b)", instruction):
        clause = (raw or "").strip(" .")
        if not clause:
            continue
        # the latest version of each attribute first: edits build on earlier edits in the same sentence
        edit = _edit(clause, [op for op in reversed(ops) if op.get("op") == "upsert_attribute"] + known)
        if edit is not None:
            ops = [o for o in ops if not (o.get("op") == "upsert_attribute" and _key(o.get("name", "")) == _key(edit.get("name", "")))] + [edit]
            continue
        header = re.match(r"^(?P<k>pricing|price|commercial|technical|tech|provisioning|network)\s*(?:attributes?)?\s*[:-]?\s*", clause, re.I)
        if header:
            kind = "technical" if header.group("k").lower() in ("technical", "tech", "provisioning", "network") else "pricing"
            clause = clause[header.end():].strip()
            if not clause:
                continue
        if m := re.match(r"^(?:remove|delete|drop)\s+(?:the\s+)?(?:attribute\s+)?(?P<n>.+?)(?:\s+attribute)?$", clause, re.I):
            ops.append({"op": "remove_attribute", "name": m.group("n").strip()})
            continue
        if m := _RULE.match(clause):
            rule: dict[str, Any] = {"op": "add_rule", "if_attribute": m.group("a").strip(), "operator": "eq",
                                    "if_value": m.group("av").strip(), "then_attribute": m.group("b").strip()}
            if m.group("req"):
                rule["effect"] = "required"
            elif m.group("neg"):
                rule.update(effect="excludes", then_value=m.group("nv").strip())
            else:
                rule.update(effect="requires", then_value=m.group("pv").strip())
            ops.append(rule)
            continue
        if _VERB.match(clause) and ":" not in clause:
            continue  # an instruction I couldn't read - never turn it into an attribute called "make ..."
        attribute = _parse_attribute(clause, kind)
        if attribute is not None:
            ops.append(attribute)
    return ops


def plan_design(db: Session, tenant_id: int, offering_name: str, instruction: str, draft: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """Design operations for a plain-English instruction: the tenant's AI
    model when one is set up, else (or when it fails) the rule parser. draft
    = changes proposed but not saved yet - edits can refer to them too."""
    from app.crm_core import llm

    current = schema(db, tenant_id, offering_name, workspace=True)
    draft = [o for o in draft or [] if isinstance(o, dict)]
    removed = {_key(o.get("name", "")) for o in draft if o.get("op") == "remove_attribute"}
    known = {_key(a["name"]): a for a in current["attributes"] if _key(a["name"]) not in removed}
    for o in draft:
        if o.get("op") == "upsert_attribute" and o.get("name"):
            known[_key(o["name"])] = {**known.get(_key(o["name"]), {}), **o}
    ops: list[dict[str, Any]] = []
    engine, reply = "rules", ""
    target = llm.resolve(db, tenant_id)
    if target is not None:
        try:
            context = json.dumps({"product": offering_name, "current_design": current, "unsaved_draft": draft})
            parsed = llm.chat_json(target, _PROMPT, f"{context}\n\nRequest: {instruction}")
            ops = [o for o in parsed.get("ops") or [] if isinstance(o, dict) and o.get("op")]
            reply, engine = str(parsed.get("reply") or ""), "ai"
        except Exception:  # noqa: BLE001 - the rule parser takes over
            ops = []
    if not ops:
        ops, engine = _rules(instruction, list(known.values())), "rules"
    return {"ops": ops, "engine": engine, "reply": reply}


_SUGGEST_PROMPT = _PROMPT + """

Here the user wants SUGGESTIONS for a product that may have no design yet: propose the pricing
attributes a telecom sales rep would configure for it (with sensible values and price impacts relative
to its list price) and the technical attributes network provisioning would need. 3-5 of each kind,
mark the ones provisioning can't do without as required, and add a rule only when it's clearly sensible.
Never repeat an attribute the product already has (current_design)."""


def _templates(offering: ProductOffering, lines: set[str]) -> list[dict[str, Any]]:
    """Built-in suggestions by product line, for when no AI is set up."""
    text = f"{offering.name} {offering.composition}".lower()
    price = offering.price or 0
    unit = max(round(price * 0.05 / 10) * 10, 50)

    def pricing(name, values, impacts, **extra):
        return {"op": "upsert_attribute", "name": name, "kind": "pricing", "data_type": "enum" if values else "number",
                "values": values, "price_impacts": impacts, **extra}

    def technical(name, values=None, **extra):
        return {"op": "upsert_attribute", "name": name, "kind": "technical", "data_type": "enum" if values else extra.pop("data_type", "string"),
                "values": values or [], **extra}

    term = pricing("Contract term", ["12 months", "24 months", "36 months"],
                   [{"value": "24 months", "type": "percent", "amount": -5}, {"value": "36 months", "type": "percent", "amount": -10}],
                   default="12 months", required=True)
    if "voip" in lines or re.search(r"\b(voip|voice|sip|pri)\b", text):
        return [
            pricing("Channels", [], [{"value": "*", "type": "per_unit", "amount": unit}], required=True, default="2"),
            pricing("DID numbers", [], [{"value": "*", "type": "per_unit", "amount": round(unit / 5) or 10}]),
            pricing("Call plan", ["Local", "National", "International"],
                    [{"value": "National", "type": "percent", "amount": 10}, {"value": "International", "type": "percent", "amount": 30}], default="Local"),
            term,
            technical("SIP trunk IP", required=True),
            technical("Codec", ["G.711", "G.729"], required=True, default="G.711"),
            technical("Number range"),
        ]
    if "mobile" in lines or re.search(r"\b(mobile|sim|5g|4g|lte|iot)\b", text):
        return [
            pricing("Data allowance", ["10 GB", "50 GB", "Unlimited"],
                    [{"value": "50 GB", "type": "percent", "amount": 40}, {"value": "Unlimited", "type": "percent", "amount": 100}],
                    required=True, default="10 GB"),
            pricing("Lines", [], [{"value": "*", "type": "per_unit", "amount": unit}], default="1"),
            term,
            technical("SIM type", ["Physical SIM", "eSIM"], required=True, default="Physical SIM"),
            technical("APN"),
        ]
    if not ({"connectivity"} & lines) and re.search(r"\b(security|backup|cloud|addon|add-on|licen[cs]e)\b", text):
        return [
            pricing("Users", [], [{"value": "*", "type": "per_unit", "amount": unit}], required=True, default="10"),
            term,
            technical("Admin email", required=True),
        ]
    return [
        pricing("Bandwidth", ["100 Mbps", "500 Mbps", "1 Gbps"],
                [{"value": "500 Mbps", "type": "percent", "amount": 40}, {"value": "1 Gbps", "type": "percent", "amount": 100}],
                required=True, default="100 Mbps"),
        term,
        pricing("SLA", ["99.5%", "99.9%", "99.99%"],
                [{"value": "99.9%", "type": "percent", "amount": 10}, {"value": "99.99%", "type": "percent", "amount": 25}], default="99.5%"),
        pricing("Static IPs", [], [{"value": "*", "type": "per_unit", "amount": unit}]),
        technical("VLAN ID", data_type="number", required=True),
        technical("CPE type", ["Cisco", "Huawei", "Juniper"], required=True),
        technical("Handoff interface", ["Copper RJ45", "Fiber SFP"], required=True),
        technical("IP block", ["/30", "/29", "/28"]),
        technical("Circuit ID"),
    ]


def suggest_design(db: Session, tenant_id: int, offering_name: str) -> dict[str, Any]:
    """A starting design for a product: the AI's proposal, or the built-in
    telecom templates by product line. Attributes it already has are skipped."""
    from app.crm_core import llm
    from app.models.catalog import ProductSpecification

    offering = db.query(ProductOffering).filter(ProductOffering.tenant_id == tenant_id, ProductOffering.name.ilike(offering_name)) \
        .order_by(ProductOffering.version.desc()).first()
    if offering is None:
        raise ValueError(f"No product named '{offering_name}'")
    names = [n.strip() for n in (offering.composition or "").split(",") if n.strip()]
    specs = db.query(ProductSpecification).filter(ProductSpecification.tenant_id == tenant_id, ProductSpecification.name.in_(names)).all()
    lines = {s.product_line for s in specs}
    current = schema(db, tenant_id, offering.name, workspace=True)
    have = {_key(a["name"]) for a in current["attributes"]}
    ops: list[dict[str, Any]] = []
    engine, reply = "rules", ""
    target = llm.resolve(db, tenant_id)
    if target is not None:
        try:
            library = [d.name for d in db.query(ProductAttributeDefinition).filter(ProductAttributeDefinition.tenant_id == tenant_id).all()]
            context = json.dumps({"product": offering.name, "components": names, "product_lines": sorted(lines), "segment": offering.segment,
                                  "monthly_list_price": offering.price, "attribute_library": library, "current_design": current})
            parsed = llm.chat_json(target, _SUGGEST_PROMPT, f"{context}\n\nRequest: suggest the pricing and technical attributes for this product")
            ops = [o for o in parsed.get("ops") or [] if isinstance(o, dict) and o.get("op") == "upsert_attribute" and o.get("name")]
            reply, engine = str(parsed.get("reply") or ""), "ai"
        except Exception:  # noqa: BLE001 - the templates take over
            ops = []
    if not ops:
        ops, engine = _templates(offering, lines), "rules"
        reply = f"A typical starting point for {offering.name} - adjust anything before you save it."
    ops = [o for o in ops if _key(o.get("name", "")) not in have]
    return {"ops": ops, "engine": engine, "reply": reply}


def describe_ops(ops: list[dict[str, Any]]) -> list[str]:
    """The planned changes in words - the preview shown before applying."""
    out = []
    for op in ops:
        if op.get("op") == "upsert_attribute":
            bits = [f"{op.get('kind', 'pricing')} attribute {op.get('name')}"]
            if op.get("values"):
                impacts = {i["value"]: i for i in op.get("price_impacts") or []}
                vals = []
                for v in op["values"]:
                    i = impacts.get(v)
                    vals.append(f"{v} ({'+' if i['amount'] >= 0 else ''}{i['amount']:g}{'%' if i['type'] == 'percent' else ''})" if i else v)
                bits.append("values " + ", ".join(vals))
            for i in op.get("price_impacts") or []:
                if i.get("value") == "*" and i.get("type") == "per_unit":
                    bits.append(f"{i['amount']:g} per unit")
            if op.get("required"):
                bits.append("required")
            if op.get("default"):
                bits.append(f"default {op['default']}")
            out.append("Set " + ", ".join(bits))
        elif op.get("op") == "remove_attribute":
            out.append(f"Remove attribute {op.get('name')}")
        elif op.get("op") == "add_rule":
            effect = {"required": "is required", "requires": f"must be {op.get('then_value')}",
                      "excludes": f"must not be {op.get('then_value')}"}.get(op.get("effect"), "")
            out.append(f"Rule: if {op.get('if_attribute')} is {op.get('if_value')}, {op.get('then_attribute')} {effect}")
        else:
            out.append(str(op.get("op")))
    return out

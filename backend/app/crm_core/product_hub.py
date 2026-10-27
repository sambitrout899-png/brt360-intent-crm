"""One picture of every product - what the Catalog page, the Product
Modeller, Product Attributes and the product intents all show and act on.

A product is the same thing everywhere, keyed by its name:
  catalog      ProductOffering versions (INT-01 launch, INT-02 change,
               INT-03 retire), its composition and launch eligibility
  model        attributes / child products / slots / rules
               (Product Modeller, INT-42 design, import), its released
               versions (INT-48) and whether the workspace has changes
               not released yet
  pricing      list price and its price on every pricelist (INT-43)
  library      the shared attribute definitions it uses (Product Attributes)
"""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy.orm import Session

from app.crm_core import product_config_service as pc
from app.crm_core import product_model_service as pm
from app.models.attributes import AttributeConstraintRule, ProductAttributeDefinition, ProductAttributeValue
from app.models.catalog import BundleEligibilityRule, OfferingStatus, ProductOffering
from app.models.process import PriceList
from app.models.product_config import OfferingAttribute
from app.models.product_model import ProductModelTest, ProductRule

_key = pc._key
_VOLATILE = {"id", "definition_id", "component_id", "parent_id", "version", "sequence", "price"}


def _normal(design: Any) -> Any:
    """A design without ids / live prices - what matters for "changed?"."""
    if isinstance(design, dict):
        return {k: _normal(v) for k, v in sorted(design.items()) if k not in _VOLATILE and k != "products"}
    if isinstance(design, list):
        return [_normal(v) for v in design]
    return design


def model_status(db: Session, tenant_id: int, name: str) -> dict[str, Any]:
    workspace = pc.schema(db, tenant_id, name, workspace=True)
    released = pc.released_version(db, tenant_id, name)
    if not workspace["designed"] and released is None:
        return {"status": "not_modelled", "label": "Not modelled - sold at its list price", "released_version": None, "changed": False}
    if released is None:
        return {"status": "workspace", "label": "Modelled, never released - selling uses the workspace as it is edited",
                "released_version": None, "changed": False}
    snapshot = pc.loads(released.snapshot_json)
    changed = json.dumps(_normal({k: snapshot.get(k) for k in ("attributes", "components", "rules", "constraints")}), sort_keys=True, default=str) != \
        json.dumps(_normal({k: workspace.get(k) for k in ("attributes", "components", "rules", "constraints")}), sort_keys=True, default=str) \
        or _normal(snapshot.get("price_rules") or []) != _normal(workspace.get("price_rules") or [])  # older snapshots have no price formulas
    return {"status": "released_changed" if changed else "released",
            "label": f"Selling released model v{released.version}" + (" - the workspace has changes not released yet" if changed else ""),
            "released_version": released.version, "valid_from": released.valid_from, "changed": changed}


def launch_eligibility(db: Session, offering: ProductOffering) -> list[dict[str, str]]:
    rows = db.query(BundleEligibilityRule).filter(BundleEligibilityRule.product_offering_id == offering.id).all()
    return [{"segment": r.segment, "channel": r.channel, "region": r.region} for r in rows]


def prices(db: Session, tenant_id: int, offering: ProductOffering) -> list[dict[str, Any]]:
    out = [{"price_list": None, "price": offering.price, "source": "list price"}]
    for pl in db.query(PriceList).filter(PriceList.tenant_id == tenant_id, PriceList.is_active.is_(True)).order_by(PriceList.name):
        price, source = pc.base_price(db, tenant_id, pl, offering)
        out.append({"price_list": pl.name, "price": price, "source": source, "default": pl.is_default})
    return out


def summary(db: Session, tenant_id: int, name: str, detail: bool = False) -> dict[str, Any]:
    versions = (db.query(ProductOffering).filter(ProductOffering.tenant_id == tenant_id, ProductOffering.name.ilike(name))
                .order_by(ProductOffering.version.desc()).all())
    if not versions:
        raise ValueError(f"No product named '{name}'")
    current = pm.offering_of(db, tenant_id, name)
    design = pc.schema(db, tenant_id, current.name, workspace=True)
    rules = pm.rules_of(db, tenant_id, current.name)
    by_type = {t: sum(1 for r in rules if r.rule_type == t) for t in ("eligibility", "compatibility", "constraint")}
    out: dict[str, Any] = {
        "name": current.name, "status": current.status.value, "segment": current.segment, "price": current.price,
        "brt_id": current.brt_id, "catalog_version": current.version, "composition": current.composition,
        "versions": [{"id": v.id, "brt_id": v.brt_id, "version": v.version, "status": v.status.value, "price": v.price, "segment": v.segment,
                      "composition": v.composition, "replacement_offering_id": v.replacement_offering_id, "effective_date": v.effective_date}
                     for v in versions],
        "launch_eligibility": launch_eligibility(db, current),
        "model": {
            **model_status(db, tenant_id, current.name),
            "pricing_attributes": sum(1 for a in design["attributes"] if a["affects_price"]),
            "technical_attributes": sum(1 for a in design["attributes"] if a["affects_provisioning"]),
            "attributes": len(design["attributes"]), "children": len(design["components"]),
            "slots": sum(1 for c in design["components"] if c.get("slot")), "rules": by_type,
            "attribute_rules": len(design["rules"]),
            "tests": db.query(ProductModelTest).filter(ProductModelTest.tenant_id == tenant_id, ProductModelTest.offering_name.ilike(current.name)).count(),
            "classes": pc.classes_of(db, tenant_id, current.name),
        },
        "prices": prices(db, tenant_id, current),
    }
    if detail:
        out["tree"] = pm.tree(db, tenant_id, current.name)
        out["rules"] = [pm.rule_out(r) for r in rules]
    return out


def catalog(db: Session, tenant_id: int) -> list[dict[str, Any]]:
    names = sorted({o.name for o in db.query(ProductOffering).filter(ProductOffering.tenant_id == tenant_id)}, key=str.lower)
    return [summary(db, tenant_id, n) for n in names]


def launch_rule(db: Session, tenant_id: int, name: str) -> ProductRule:
    """The launch eligibility (segment / region set when the product was
    launched) as an enforced eligibility rule of the product."""
    from app.crm_core import page_service

    offering = pm.offering_of(db, tenant_id, name)
    if offering is None:
        raise ValueError(f"No product named '{name}'")
    rows = launch_eligibility(db, offering)
    if not rows:
        raise ValueError(f"{offering.name} has no launch eligibility to turn into a rule")
    segments = sorted({page_service.segment_of(r["segment"]) or r["segment"] for r in rows})
    regions = sorted({r["region"] for r in rows if r["region"] and r["region"].upper() != "ALL"})
    when: list[dict[str, Any]] = [{"field": "segment", "op": "in", "value": segments}]
    if regions:
        when.append({"field": "region", "op": "in", "value": regions})
    text = f"only for {' or '.join(segments)} customers" + (f" in {', '.join(regions)}" if regions else "") + " (from the launch)"
    row = ProductRule(tenant_id=tenant_id, offering_name=offering.name, rule_type="eligibility", source_text=text,
                      definition_json=json.dumps({"mode": "only", "when": when}), message="")
    db.add(row)
    db.flush()
    return row


# ---------------------------------------------------------------- the attribute library


def attribute_usage(db: Session, tenant_id: int) -> list[dict[str, Any]]:
    """Every shared attribute and where it is used - products (and their
    child products), classes, atomic product values and composition rules."""
    defs = db.query(ProductAttributeDefinition).filter(ProductAttributeDefinition.tenant_id == tenant_id).order_by(ProductAttributeDefinition.name).all()
    rows = db.query(OfferingAttribute).filter(OfferingAttribute.tenant_id == tenant_id, OfferingAttribute.is_active.is_(True)).all()
    out = []
    for d in defs:
        used = [r for r in rows if r.attribute_definition_id == d.id]
        library = pc._split_values(d.allowed_values)
        in_use: list[str] = []
        for r in used:
            in_use += pc._split_values(r.allowed_values) if r.allowed_values else library
        spec_values = [v.value for v in db.query(ProductAttributeValue).filter(ProductAttributeValue.attribute_definition_id == d.id)]
        in_use += spec_values
        out.append({
            "id": d.id, "brt_id": d.brt_id, "name": d.name, "data_type": d.data_type, "unit": d.unit, "values": library,
            "products": [{"offering_name": r.offering_name, "kind": r.kind, "values": pc._split_values(r.allowed_values) or library,
                          "own_values": bool(r.allowed_values), "unit": r.unit or d.unit, "required": r.required}
                         for r in used if not r.offering_name.startswith("class:")],
            "classes": [r.offering_name.removeprefix("class:") for r in used if r.offering_name.startswith("class:")],
            "spec_values": len(spec_values),
            "composition_rules": db.query(AttributeConstraintRule).filter(AttributeConstraintRule.if_attribute_id == d.id).count(),
            "unused_values": [v for v in library if _key(v) not in {_key(x) for x in in_use}],
        })
    return out


def where_used(db: Session, tenant_id: int, definition_id: int) -> list[str]:
    entry = next((u for u in attribute_usage(db, tenant_id) if u["id"] == definition_id), None)
    if entry is None:
        return []
    return ([f"product {p['offering_name']}" for p in entry["products"]] + [f"class {c}" for c in entry["classes"]]
            + ([f"{entry['spec_values']} atomic product value(s)"] if entry["spec_values"] else [])
            + ([f"{entry['composition_rules']} composition rule(s)"] if entry["composition_rules"] else []))


def tidy_values(db: Session, tenant_id: int, definition_id: int) -> list[str]:
    """Drops library values no product, class or atomic product uses."""
    entry = next((u for u in attribute_usage(db, tenant_id) if u["id"] == definition_id), None)
    if entry is None:
        raise ValueError("Attribute not found")
    d = db.get(ProductAttributeDefinition, definition_id)
    keep = [v for v in entry["values"] if v not in entry["unused_values"]]
    d.allowed_values = ",".join(keep) or None
    db.flush()
    return entry["unused_values"]

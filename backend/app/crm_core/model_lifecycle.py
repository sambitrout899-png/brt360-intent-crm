"""Product model lifecycle - workspace -> tests -> regression -> release.

- The workspace is what the Product Modeller edits (live tables).
- Model tests: given picks, expect valid / invalid, a price, or which values
  an attribute may still take.
- Regression: recent quotes and live orders of the product re-checked
  against the workspace - what a release would break.
- Release: an immutable, effective-dated version (the compiled design as
  JSON - model-as-code), gated by the tests and the model QA. Selling uses
  the version valid on the day; quotes record which one priced them.
- Model-as-code: any version or the workspace downloads as JSON, and JSON
  uploads into the workspace.
"""

from __future__ import annotations

import datetime as dt
import json
from typing import Any

from sqlalchemy.orm import Session

from app.crm_core import configurator as cfg
from app.crm_core import product_config_service as pc
from app.models.catalog import ProductOffering
from app.models.product_model import ProductModelTest, ProductModelVersion
from app.timeutils import utcnow

_key = pc._key


def workspace(db: Session, tenant_id: int, name: str) -> dict[str, Any]:
    return pc.schema(db, tenant_id, name, workspace=True)


def base_of(db: Session, tenant_id: int, name: str) -> float:
    from app.crm_core import product_model_service as pm

    offering = pm.offering_of(db, tenant_id, name)
    return offering.price if offering else 0.0


# ---------------------------------------------------------------- tests


def test_out(t: ProductModelTest) -> dict[str, Any]:
    return {"id": t.id, "brt_id": t.brt_id, "name": t.name, "picks": pc.loads(t.picks_json), "expect": t.expect,
            "expect_price": t.expect_price, "expect_domains": pc.loads(t.expect_domains_json)}


def tests_of(db: Session, tenant_id: int, name: str) -> list[ProductModelTest]:
    return (db.query(ProductModelTest).filter(ProductModelTest.tenant_id == tenant_id, ProductModelTest.offering_name.ilike(name))
            .order_by(ProductModelTest.id).all())


def run_tests(db: Session, tenant_id: int, name: str, design: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    design = design or workspace(db, tenant_id, name)
    base = base_of(db, tenant_id, name)
    out = []
    for t in tests_of(db, tenant_id, name):
        result = cfg.session(design, base, pc.loads(t.picks_json), explain=False)
        problems = []
        valid = result["valid"]
        if t.expect == "valid" and not valid:
            problems.append(result["conflict"]["message"] if result["conflict"] else "; ".join(result["problems"]))
        if t.expect == "invalid" and valid:
            problems.append("expected these picks to be refused, but they are allowed")
        if t.expect_price is not None and valid and abs(result["price"]["monthly"] - t.expect_price) > 0.005:
            problems.append(f"monthly price {result['price']['monthly']:,.2f}, expected {t.expect_price:,.2f}")
        for var_name, expected in pc.loads(t.expect_domains_json).items():
            var = next((v for v in result["variables"] if _key(v["name"]) == _key(var_name)), None)
            allowed = sorted(x["value"] for x in var["values"] if x["state"] != "excluded" and x["value"]) if var else []
            if sorted(expected) != allowed:
                problems.append(f"{var_name} allows {', '.join(allowed) or 'nothing'}, expected {', '.join(expected)}")
        out.append({**test_out(t), "passed": not problems, "problems": problems})
    return out


# ---------------------------------------------------------------- regression


def _check(design: dict[str, Any], base: float, pricing: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    try:
        chosen = pc._normalize(design["attributes"], pricing, "pricing", design["designed"], errors, design.get("components"))
        errors += pc.validate(design, chosen, {}, False)
    except Exception as exc:  # noqa: BLE001 - a record that can't even be read counts as broken
        errors.append(str(exc))
    return errors


def regression(db: Session, tenant_id: int, name: str, design: dict[str, Any] | None = None, limit: int = 200) -> dict[str, Any]:
    """Recent quotes and live orders of the product against the candidate model."""
    from app.models.crm import QuoteItem
    from app.models.orders import Order, OrderItem, OrderStatus
    from app.models.product_config import OrderItemConfig, QuoteItemConfig

    design = design or workspace(db, tenant_id, name)
    base = base_of(db, tenant_id, name)
    ids = [o.id for o in db.query(ProductOffering.id).filter(ProductOffering.tenant_id == tenant_id, ProductOffering.name.ilike(name))]
    quotes = (db.query(QuoteItemConfig, QuoteItem).join(QuoteItem, QuoteItem.id == QuoteItemConfig.quote_item_id)
              .filter(QuoteItem.product_offering_id.in_(ids or [-1])).order_by(QuoteItemConfig.id.desc()).limit(limit).all())
    orders = (db.query(OrderItemConfig, OrderItem, Order).join(OrderItem, OrderItem.id == OrderItemConfig.order_item_id)
              .join(Order, Order.id == OrderItem.order_id)
              .filter(OrderItem.product_offering_id.in_(ids or [-1]), Order.status.in_([OrderStatus.active, OrderStatus.changed]))
              .order_by(OrderItemConfig.id.desc()).limit(limit).all())
    broken_quotes = [{"quote_id": item.quote_id, "errors": errs} for config, item in quotes
                     if (errs := _check(design, base, pc.loads(config.pricing_attributes_json)))]
    broken_orders = [{"order_id": order.id, "errors": errs} for config, item, order in orders
                     if (errs := _check(design, base, pc.loads(config.pricing_attributes_json)))]
    # a completed order's service lives on as an asset - its installed configuration is checked too
    from app.models.fulfillment import Asset, AssetStatus
    assets = (db.query(Asset).filter(Asset.tenant_id == tenant_id, Asset.superseded_by_asset_id.is_(None),
                                     Asset.status != AssetStatus.decommissioned, Asset.asset_type.ilike(name))
              .order_by(Asset.id.desc()).limit(limit).all())
    for asset in assets:
        data = json.loads(asset.instance_json or "{}")
        if errs := _check(design, base, (data.get("configuration") or {}).get("pricing") or {}):
            broken_orders.append({"order_id": data.get("order_id"), "asset_id": asset.id, "errors": errs})
    return {"quotes_checked": len(quotes), "quotes_broken": broken_quotes[:20], "quotes_broken_count": len(broken_quotes),
            "assets_checked": len(orders) + len(assets), "assets_affected": broken_orders[:20], "assets_affected_count": len(broken_orders)}


# ---------------------------------------------------------------- versions


def versions(db: Session, tenant_id: int, name: str) -> list[dict[str, Any]]:
    current = pc.released_version(db, tenant_id, name)
    rows = (db.query(ProductModelVersion).filter(ProductModelVersion.tenant_id == tenant_id, ProductModelVersion.offering_name.ilike(name))
            .order_by(ProductModelVersion.version.desc()).all())
    return [{"id": v.id, "brt_id": v.brt_id, "version": v.version, "status": v.status, "valid_from": v.valid_from, "notes": v.notes,
             "current": current is not None and current.id == v.id, "report": pc.loads(v.report_json), "released_by": v.sys_created_by,
             "created_at": v.created_at} for v in rows]


def check_release(db: Session, tenant_id: int, name: str) -> dict[str, Any]:
    design = workspace(db, tenant_id, name)
    tests = run_tests(db, tenant_id, name, design)
    qa = cfg.lint(design)
    reg = regression(db, tenant_id, name, design)
    blockers = [f"Test \"{t['name']}\" fails: {'; '.join(t['problems'])}" for t in tests if not t["passed"]]
    blockers += [f"Model QA: {f['text']}" for f in qa if f["level"] == "error"]
    warnings = [f"{reg['quotes_broken_count']} of the last {reg['quotes_checked']} quotes would no longer be valid"] if reg["quotes_broken_count"] else []
    warnings += [f"{reg['assets_affected_count']} live services would need changes if reconfigured"] if reg["assets_affected_count"] else []
    warnings += [f"Model QA: {f['text']}" for f in qa if f["level"] == "warning"]
    return {"design": design, "tests": tests, "qa": qa, "regression": reg, "blockers": blockers, "warnings": warnings,
            "ready": not blockers and design["designed"]}


def release(db: Session, tenant_id: int, name: str, valid_from: dt.datetime | None = None, notes: str = "",
            accept_regressions: bool = True) -> ProductModelVersion:
    from app.crm_core import product_model_service as pm

    offering = pm.offering_of(db, tenant_id, name)
    if offering is None:
        raise ValueError(f"No product named '{name}'")
    check = check_release(db, tenant_id, offering.name)
    if not check["design"]["designed"]:
        raise ValueError(f"{offering.name} has no model to release yet")
    if check["blockers"]:
        raise ValueError("Not released: " + "; ".join(check["blockers"]))
    if not accept_regressions and (check["regression"]["quotes_broken_count"] or check["regression"]["assets_affected_count"]):
        raise ValueError("Not released: " + "; ".join(check["warnings"]))
    last = db.query(ProductModelVersion).filter(ProductModelVersion.tenant_id == tenant_id, ProductModelVersion.offering_name.ilike(offering.name)) \
        .order_by(ProductModelVersion.version.desc()).first()
    design = {k: v for k, v in check["design"].items() if k != "version"}
    row = ProductModelVersion(
        tenant_id=tenant_id, offering_name=offering.name, version=(last.version + 1) if last else 1, status="released",
        valid_from=valid_from or utcnow(), snapshot_json=json.dumps(design, default=str), notes=notes,
        report_json=json.dumps({"tests": len(check["tests"]), "tests_passed": sum(t["passed"] for t in check["tests"]),
                                "warnings": check["warnings"], "regression": {k: v for k, v in check["regression"].items() if k.endswith("count") or k.endswith("checked")}},
                               default=str),
    )
    db.add(row)
    db.flush()
    return row


def retire_version(db: Session, tenant_id: int, version_id: int) -> ProductModelVersion:
    row = db.get(ProductModelVersion, version_id)
    if row is None or row.tenant_id != tenant_id:
        raise ValueError("Version not found")
    row.status = "retired"
    db.flush()
    return row


# ---------------------------------------------------------------- model-as-code


def model_json(db: Session, tenant_id: int, name: str, version: int | None = None) -> dict[str, Any]:
    if version is None:
        design = workspace(db, tenant_id, name)
        label = "workspace"
    else:
        row = db.query(ProductModelVersion).filter(ProductModelVersion.tenant_id == tenant_id, ProductModelVersion.offering_name.ilike(name),
                                                   ProductModelVersion.version == version).first()
        if row is None:
            raise ValueError(f"{name} has no version {version}")
        design, label = pc.loads(row.snapshot_json), f"v{row.version}"
    from app.crm_core import product_model_service as pm

    others = [pm.rule_out(r) for r in pm.rules_of(db, tenant_id, name) if r.rule_type != "constraint"]
    return {
        "offering": name, "source": label, "format": "brt360-product-model/1",
        "attributes": [{k: a[k] for k in ("name", "kind", "affects_price", "affects_provisioning", "component", "min", "max", "reference_type",
                                           "description", "unit", "values", "required", "default", "price_impacts")} for a in design["attributes"]
                       if not a.get("inherited_from")],
        "components": [{"name": c["name"], "parent": c["parent"], "description": c["description"], "min_qty": c["min_qty"], "max_qty": c["max_qty"],
                        "default_qty": c["default_qty"], "charge_type": c["charge_type"], "price": c["price"],
                        "affects_provisioning": c["affects_provisioning"],
                        "slot": ({"type": c["slot"]["type"], "products": c["slot"].get("listed"), "class": c["slot"].get("class"),
                                  "default": c["slot"].get("default")} if c.get("slot") else None)} for c in design["components"]],
        "attribute_rules": design["rules"],
        "constraints": [{k: v for k, v in r.items() if k not in ("id",)} for r in design.get("constraints", [])],
        "rules": [{"rule_type": r["rule_type"], "text": r["source_text"], "definition": r["definition"], "message": r["message"]} for r in others],
        "tests": [test_out(t) for t in tests_of(db, tenant_id, name)],
    }


def load_model_json(db: Session, tenant_id: int, name: str, data: dict[str, Any]) -> list[str]:
    """A model JSON into the workspace (additive: names match, nothing is removed)."""
    from app.crm_core import product_model_service as pm
    from app.models.product_model import ProductRule

    changes: list[str] = []
    for c in data.get("components") or []:
        changes.append(pm.upsert_component(db, tenant_id, name, c))
        if c.get("slot"):
            changes.append(set_slot(db, tenant_id, name, c["name"], c["slot"].get("type", "products"), c["slot"].get("products") or [],
                                    c["slot"].get("class"), c["slot"].get("default")))
    for a in data.get("attributes") or []:
        changes.append(pc._upsert_attribute(db, tenant_id, name, {**a, "kind": a.get("kind") or ("pricing" if a.get("affects_price") else "technical")}))
    for r in data.get("attribute_rules") or []:
        changes += pc.apply_design(db, tenant_id, name, [{"op": "add_rule", **{k: r.get(k) for k in ("if_attribute", "operator", "if_value", "then_attribute", "effect", "then_value", "message")}}])
    for r in data.get("constraints") or []:
        definition = {k: v for k, v in r.items() if k not in ("message", "text")}
        db.add(ProductRule(tenant_id=tenant_id, offering_name=name, rule_type="constraint", source_text=r.get("text") or "",
                           definition_json=json.dumps(definition), message=r.get("message") or ""))
        changes.append(f"Constraint: {r.get('text') or definition}")
    for r in data.get("rules") or []:
        db.add(ProductRule(tenant_id=tenant_id, offering_name=name, rule_type=r["rule_type"], source_text=r.get("text") or "",
                           definition_json=json.dumps(r.get("definition") or {}), message=r.get("message") or ""))
        changes.append(f"{r['rule_type'].title()} rule: {r.get('text')}")
    for t in data.get("tests") or []:
        db.add(ProductModelTest(tenant_id=tenant_id, offering_name=name, name=t["name"], picks_json=json.dumps(t.get("picks") or {}),
                                expect=t.get("expect", "valid"), expect_price=t.get("expect_price"),
                                expect_domains_json=json.dumps(t.get("expect_domains") or {})))
        changes.append(f"Test: {t['name']}")
    db.flush()
    return changes


# ---------------------------------------------------------------- slots and classes


def set_slot(db: Session, tenant_id: int, name: str, component: str, domain_type: str, products: list[str], class_name: str | None,
             default: str | None) -> str:
    from app.crm_core import product_model_service as pm
    from app.models.product_model import ProductSlotDomain

    comp = pm._component_row(db, tenant_id, name, component)
    if comp is None:
        raise ValueError(f"{name} has no child product '{component}'")
    if domain_type not in ("products", "class", "none"):
        raise ValueError("A slot is filled from a product list or a product class")
    row = db.query(ProductSlotDomain).filter(ProductSlotDomain.component_id == comp.id).first()
    if domain_type == "none":
        if row is not None:
            db.delete(row)
        db.flush()
        return f"{component} is a fixed child product again"
    if row is None:
        row = ProductSlotDomain(component_id=comp.id)
        db.add(row)
    row.domain_type, row.products = domain_type, ",".join(p.strip() for p in products if p and p.strip())
    row.class_name, row.default_product = (class_name or None), (default or None)
    db.flush()
    return f"{component} is filled by " + (f"any product of class {class_name}" if domain_type == "class" else ", ".join(products) or "no product yet")


def classes(db: Session, tenant_id: int) -> list[dict[str, Any]]:
    from app.models.product_model import ProductClass

    rows = db.query(ProductClass).filter(ProductClass.tenant_id == tenant_id).order_by(ProductClass.name).all()
    return [{"id": r.id, "brt_id": r.brt_id, "name": r.name, "parent": r.parent_name, "description": r.description,
             "members": pc._split_values(r.members), "all_members": pc.class_members(db, tenant_id, r.name),
             "attributes": [pc._attribute_out(a) for a in pc._rows(db, tenant_id, f"class:{r.name}")]} for r in rows]


def save_class(db: Session, tenant_id: int, name: str, parent: str | None, description: str, members: list[str]) -> dict[str, Any]:
    from app.models.product_model import ProductClass

    name = name.strip()
    if not name:
        raise ValueError("A class needs a name")
    if parent and _key(parent) == _key(name):
        raise ValueError("A class can't be its own parent")
    row = next((r for r in db.query(ProductClass).filter(ProductClass.tenant_id == tenant_id) if _key(r.name) == _key(name)), None)
    if row is None:
        row = ProductClass(tenant_id=tenant_id, name=name)
        db.add(row)
    members = list(dict.fromkeys(m.strip() for m in members if m and m.strip()))
    # a product is bound to one class (like an object to its class) - binding it here moves it out of any other
    moved = []
    for other in db.query(ProductClass).filter(ProductClass.tenant_id == tenant_id, ProductClass.id != (row.id or 0)):
        kept = [m for m in pc._split_values(other.members) if _key(m) not in {_key(x) for x in members}]
        if len(kept) != len(pc._split_values(other.members)):
            moved += [f"{m} (from {other.name})" for m in pc._split_values(other.members) if _key(m) in {_key(x) for x in members}]
            other.members = ",".join(kept)
    row.parent_name, row.description, row.members = (parent or None), description, ",".join(members)
    db.flush()
    out = next(c for c in classes(db, tenant_id) if c["id"] == row.id)
    return {**out, "moved": moved}


def bind_class(db: Session, tenant_id: int, offering_name: str, class_name: str | None) -> dict[str, Any]:
    """Bind a product to a class (None unbinds it) - it then inherits the
    class's attributes, narrowed to the values the class allows."""
    from app.models.product_model import ProductClass

    rows = db.query(ProductClass).filter(ProductClass.tenant_id == tenant_id).all()
    target = next((r for r in rows if _key(r.name) == _key(class_name or "")), None) if class_name else None
    if class_name and target is None:
        raise ValueError(f"No product class '{class_name}'")
    for r in rows:
        if r is not target:
            r.members = ",".join(m for m in pc._split_values(r.members) if _key(m) != _key(offering_name))
    if target is not None and _key(offering_name) not in {_key(m) for m in pc._split_values(target.members)}:
        target.members = ",".join(pc._split_values(target.members) + [offering_name])
    db.flush()
    return inheritance(db, tenant_id, offering_name)


def inheritance(db: Session, tenant_id: int, offering_name: str) -> dict[str, Any]:
    """How a product's attributes come about: library -> class(es) -> product."""
    from app.models.attributes import ProductAttributeDefinition

    chain = pc.class_chain(db, tenant_id, offering_name)
    library = {_key(d.name): pc._split_values(d.allowed_values) for d in db.query(ProductAttributeDefinition).filter(ProductAttributeDefinition.tenant_id == tenant_id)}
    attrs = pc.resolved_attributes(db, tenant_id, offering_name, pc.components_of(db, tenant_id, offering_name))
    return {
        "offering_name": offering_name, "class": chain[-1] if chain else None, "chain": chain,
        "attributes": [{
            "name": a["name"], "kind": a["kind"], "values": a["values"], "library_values": library.get(_key(a["name"]), []),
            "class_values": a.get("class_values") if a.get("overrides") else (a["values"] if a.get("inherited_from") else None),
            "source": "class" if a.get("inherited_from") else "override" if a.get("overrides") else "own",
            "from": a.get("inherited_from") or a.get("overrides"), "narrowed": bool(a.get("narrowed")),
            "required": a["required"], "default": a["default"], "price_impacts": a["price_impacts"],
        } for a in attrs],
    }

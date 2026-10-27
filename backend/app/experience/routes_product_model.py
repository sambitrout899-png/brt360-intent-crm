"""Product modeller (crm_core/product_model_service.py): product
hierarchies, spreadsheet import / export, and eligibility / compatibility /
constraint rules. Designing is for administrators; anyone signed in can ask
which products a customer is eligible for."""

from __future__ import annotations

import base64
import binascii
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.auth import get_current_user, require_roles
from app.crm_core import product_config_service as config
from app.crm_core import product_model_service as model
from app.db import get_db
from app.models.auth import UserRole
from app.models.crm import Party
from app.models.product_model import ProductRule
from app.tenancy import get_current_tenant_id

router = APIRouter(prefix="/api/catalog", tags=["catalog"], dependencies=[Depends(get_current_user)])
admin_router = APIRouter(prefix="/api/admin/product-model", tags=["admin-product-model"], dependencies=[Depends(require_roles(UserRole.admin))])


def _bad(exc: Exception) -> HTTPException:
    return HTTPException(400, str(exc))


def _root(db: Session, tenant_id: int, name: str) -> str:
    offering = model.offering_of(db, tenant_id, name)
    if offering is None:
        raise HTTPException(404, f"No product named '{name}'")
    return offering.name


@router.get("/products")
def hub_catalog(db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)) -> list[dict[str, Any]]:
    """Every product as one picture: catalog versions, launch eligibility,
    model (attributes, child products, rules, release status) and prices."""
    from app.crm_core import product_hub

    return product_hub.catalog(db, tenant_id)


@router.get("/products/{name}")
def hub_product(name: str, db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)) -> dict[str, Any]:
    from app.crm_core import product_hub

    try:
        return product_hub.summary(db, tenant_id, name, detail=True)
    except ValueError as exc:
        raise HTTPException(404, str(exc))


@router.get("/eligibility")
def eligibility(party_id: int, names: str = "", db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)) -> dict[str, Any]:
    party = db.get(Party, party_id)
    if party is None or party.tenant_id != tenant_id:
        raise HTTPException(404, "Customer not found")
    wanted = [n.strip() for n in names.split(",") if n.strip()] or [p["name"] for p in model.products(db, tenant_id) if p["status"] == "active"]
    ok, why = model.eligible(db, tenant_id, wanted, party)
    return {"eligible": ok, "not_eligible": why, "owns": model.owned_products(db, tenant_id, party)}


@router.get("/not-offered")
def not_offered(party_id: int | None = None, segment: str | None = None, db: Session = Depends(get_db),
                tenant_id: int = Depends(get_current_tenant_id)) -> list[dict[str, Any]]:
    """Products a lead / quote for this customer can't pick, and why - draft
    (not published), sold to the other segment, or not eligible."""
    from app.crm_core import page_service, process_service
    from app.models.catalog import OfferingStatus, ProductOffering

    party = db.get(Party, party_id) if party_id else None
    if party is not None and party.tenant_id != tenant_id:
        party = None
    seg = process_service.party_segment(party) if party is not None else None
    seg = seg or page_service.segment_of(segment)
    offered = set(page_service.offerings_for_segment(db, tenant_id, seg))
    _, why = model.eligible(db, tenant_id, sorted(offered), party)
    out: dict[str, dict[str, Any]] = {n: {"name": n, "reason": r, "fix": "rule"} for n, r in why.items()}
    latest: dict[str, ProductOffering] = {}
    for o in db.query(ProductOffering).filter(ProductOffering.tenant_id == tenant_id).order_by(ProductOffering.version):
        latest[o.name] = o
    active = {o.name for o in db.query(ProductOffering).filter(ProductOffering.tenant_id == tenant_id, ProductOffering.status == OfferingStatus.active)}
    for name, o in sorted(latest.items()):
        if name in offered or name in out or o.status == OfferingStatus.retired:
            continue
        if name not in active:
            out[name] = {"name": name, "reason": "draft - not published yet, so it can't be sold", "fix": "publish"}
        elif seg and page_service.segment_of(o.segment) not in (None, seg):
            out[name] = {"name": name, "reason": f"sold to {o.segment} customers - this customer is {seg}", "fix": "segment"}
    return list(out.values())


# ---------------------------------------------------------------- products and their hierarchy


class ProductIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    segment: str = "B2B"
    price: float = 0
    status: str = "draft"


@admin_router.get("")
def list_products(db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)) -> list[dict[str, Any]]:
    return model.products(db, tenant_id)


@admin_router.post("")
def new_product(payload: ProductIn, db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)) -> dict[str, Any]:
    if model.offering_of(db, tenant_id, payload.name) is not None:
        raise HTTPException(400, f"There is already a product '{payload.name}'")
    offering, _ = model.ensure_offering(db, tenant_id, payload.name.strip(), payload.segment, payload.price, payload.status)
    db.commit()
    return model.tree(db, tenant_id, offering.name)


@admin_router.get("/{name}")
def product_tree(name: str, db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)) -> dict[str, Any]:
    try:
        return model.tree(db, tenant_id, name)
    except ValueError as exc:
        raise HTTPException(404, str(exc))


@admin_router.post("/{name}/publish")
def publish(name: str, db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)) -> dict[str, Any]:
    try:
        offering = model.publish(db, tenant_id, name)
    except ValueError as exc:
        raise _bad(exc)
    db.commit()
    return model.tree(db, tenant_id, offering.name)


class ComponentIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    parent: str | None = None
    description: str | None = None
    mandatory: bool | None = None
    min_qty: int | None = None
    max_qty: int | None = None
    default_qty: int | None = None
    charge_type: str | None = None
    price: float | None = None
    affects_provisioning: bool | None = None
    rename_from: str | None = None


@admin_router.post("/{name}/components")
def save_component(name: str, payload: ComponentIn, db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)) -> dict[str, Any]:
    root = _root(db, tenant_id, name)
    data = payload.model_dump(exclude_unset=True)
    try:
        if payload.rename_from and model._key(payload.rename_from) != model._key(payload.name):
            row = model._component_row(db, tenant_id, root, payload.rename_from)
            if row is None:
                raise ValueError(f"No child product '{payload.rename_from}'")
            if model._component_row(db, tenant_id, root, payload.name) is not None:
                raise ValueError(f"There is already a child product '{payload.name}'")
            row.name = payload.name.strip()
        change = model.upsert_component(db, tenant_id, root, {k: v for k, v in data.items() if k != "rename_from"})
    except ValueError as exc:
        db.rollback()
        raise _bad(exc)
    db.commit()
    return {"change": change, "tree": model.tree(db, tenant_id, root)}


@admin_router.delete("/{name}/components/{component}")
def delete_component(name: str, component: str, db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)) -> dict[str, Any]:
    root = _root(db, tenant_id, name)
    try:
        change = model.remove_component(db, tenant_id, root, component)
    except ValueError as exc:
        raise _bad(exc)
    db.commit()
    return {"change": change, "tree": model.tree(db, tenant_id, root)}


class AttributeIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    component: str | None = None
    affects_price: bool = False
    affects_provisioning: bool = False
    required: bool = False
    data_type: str | None = None
    values: list[str] = []
    unit: str | None = None
    min: float | None = None
    max: float | None = None
    reference_type: str | None = None
    default: str | None = None
    description: str | None = None
    price_impacts: list[dict[str, Any]] | None = None


@admin_router.post("/{name}/attributes")
def save_attribute(name: str, payload: AttributeIn, db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)) -> dict[str, Any]:
    root = _root(db, tenant_id, name)
    op = payload.model_dump()
    op["kind"] = "pricing" if payload.affects_price else "technical"
    if payload.min is not None or payload.max is not None:
        op["data_type"] = "number"
    try:
        change = config._upsert_attribute(db, tenant_id, root, op)
    except ValueError as exc:
        db.rollback()
        raise _bad(exc)
    db.commit()
    return {"change": change, "tree": model.tree(db, tenant_id, root)}


@admin_router.delete("/{name}/attributes/{attribute}")
def delete_attribute(name: str, attribute: str, db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)) -> dict[str, Any]:
    root = _root(db, tenant_id, name)
    try:
        changes = config.apply_design(db, tenant_id, root, [{"op": "remove_attribute", "name": attribute}])
    except ValueError as exc:
        raise _bad(exc)
    db.commit()
    return {"change": changes[0], "tree": model.tree(db, tenant_id, root)}


# ---------------------------------------------------------------- import / export


class ImportIn(BaseModel):
    file_name: str = Field(min_length=1, max_length=200)
    content_base64: str = Field(min_length=1)
    dry_run: bool = True


IMPORT_AI_SECONDS = 80.0


@admin_router.post("/import/file")
def import_file(payload: ImportIn, db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)) -> dict[str, Any]:
    """A dry run says what would change; then the same file is imported."""
    try:
        content = base64.b64decode(payload.content_base64, validate=True)
        sheets = model.read_sheets(content, payload.file_name)
        product_rows, rule_rows, class_rows = sheets["products"], sheets["rules"], sheets["classes"]
    except (binascii.Error, ValueError, KeyError) as exc:
        raise HTTPException(400, f"Could not read {payload.file_name}: {exc}")
    if not product_rows and not rule_rows and not class_rows:
        raise HTTPException(400, "No rows found - the first row must be the headings (Product Element, Type, Parent, Description ...)")
    plan = model.plan_import(db, tenant_id, product_rows, rule_rows, class_rows)
    if payload.dry_run:
        return {"dry_run": True, "preview": plan["preview"], "errors": plan["errors"], "operations": len(plan["ops"])}
    from app.crm_core import llm

    # The AI time an import may use - well inside nginx's 120 s; past it, the rules the built-in reader can't
    # read are reported as rows to retry (one at a time in the Rules tab) instead of the page timing out.
    with llm.budget(IMPORT_AI_SECONDS):
        result = model.apply_import(db, tenant_id, plan)
    db.commit()
    return {"dry_run": False, **result}


@admin_router.get("/export/file")
def export_file(names: str = "", format: str = "xlsx", template: bool = False, db: Session = Depends(get_db),
                tenant_id: int = Depends(get_current_tenant_id)) -> Response:
    wanted = [] if template else ([n.strip() for n in names.split(",") if n.strip()] or [p["name"] for p in model.products(db, tenant_id)])
    try:
        rows, rules = model.export_rows(db, tenant_id, wanted)
    except ValueError as exc:
        raise _bad(exc)
    if template:
        rows += [["Business Broadband Internet", "Root Product", "", "Main parent product", "M", "", "", "", "", "", "", "", "1000", "", "", "", "", "B2B", "draft"],
                 ["Bandwidth (Kbps)", "Product Attribute", "Business Broadband Internet", "512 Kbps to 4Mbps", "M", "Y", "Y"],
                 ["Access Medium", "Product Attribute", "Business Broadband Internet", "Fiber,DSL,Microwave", "M", "Y", "Y"],
                 ["POP Location", "Product Attribute", "Business Broadband Internet", "Place", "M", "", "Y"],
                 ["CPE", "Child Product", "Business Broadband Internet", "", "O", "", "Y"],
                 ["CPE Type", "Product Attribute", "CPE", "Customer,Provider", "O", "Y", "Y"],
                 ["BBI-MRC", "Child Product", "Business Broadband Internet", "This would capture the monthly recurring charges", "M", "", "", "", "", "", "", "", "0", "recurring"]]
        rules += [["Business Broadband Internet", "eligibility", "only for B2B customers", "", "Y"],
                  ["Business Broadband Internet", "constraint", "if Access Medium is DSL then Bandwidth must be at most 2048", "", "Y"]]
    stem = "product_template" if template else ("products" if len(wanted) != 1 else wanted[0].replace(" ", "_"))
    if format == "csv":
        return Response(model.to_csv(rows), media_type="text/csv", headers={"Content-Disposition": f'attachment; filename="{stem}.csv"'})
    from app.crm_core import model_lifecycle

    # the classes these products belong to, and those their slots are filled from
    used = set()
    for name in wanted:
        used |= {model._key(c) for c in model.config.classes_of(db, tenant_id, name)}
        used |= {model._key(c["slot"]["class"]) for c in model.config.schema(db, tenant_id, name, workspace=True)["components"]
                 if c.get("slot") and c["slot"].get("class")}
    classes = [model.CLASS_COLUMNS] + [[c["name"], c["parent"] or "", c["description"], ",".join(c["members"])]
                                       for c in model_lifecycle.classes(db, tenant_id) if template or model._key(c["name"]) in used]
    data = model.write_xlsx([("Products", rows), ("Rules", rules), ("Classes", classes)])
    return Response(data, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": f'attachment; filename="{stem}.xlsx"'})


# ---------------------------------------------------------------- rules


class RuleIn(BaseModel):
    rule_type: str
    text: str = Field(min_length=1, max_length=2000)
    message: str = Field(default="", max_length=300)


@admin_router.post("/{name}/rules/from-launch")
def rule_from_launch(name: str, db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)) -> dict[str, Any]:
    """Turn the segment / region the product was launched for into an enforced eligibility rule."""
    from app.crm_core import product_hub

    try:
        row = product_hub.launch_rule(db, tenant_id, name)
    except ValueError as exc:
        raise _bad(exc)
    db.commit()
    return model.rule_out(row)


@admin_router.post("/{name}/rules/compile")
def compile_rule(name: str, payload: RuleIn, db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)) -> dict[str, Any]:
    root = _root(db, tenant_id, name)
    try:
        return model.compile_rule(db, tenant_id, root, payload.rule_type, payload.text)
    except ValueError as exc:
        raise _bad(exc)


@admin_router.post("/{name}/rules")
def add_rule(name: str, payload: RuleIn, db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)) -> dict[str, Any]:
    root = _root(db, tenant_id, name)
    try:
        row = model.save_rule(db, tenant_id, root, payload.rule_type, payload.text, payload.message)
    except ValueError as exc:
        db.rollback()
        raise _bad(exc)
    db.commit()
    return model.rule_out(row)


# ---------------------------------------------------------------- price formulas (crm_core/price_formula.py)


class FormulaCheckIn(BaseModel):
    text: str = Field(min_length=1, max_length=2000)  # a formula, or the price impact in plain English
    label: str | None = Field(default=None, max_length=120)
    charge: str | None = None
    picks: dict[str, Any] | None = None  # "try it": the amount for these values
    rule_id: int | None = None  # the formula being edited (left out of "the price so far")
    use_ai: bool = True


@admin_router.get("/{name}/price-formulas/vocabulary")
def formula_vocabulary(name: str, db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)) -> dict[str, Any]:
    """What a formula of this product can use - for the builder's palette."""
    from app.crm_core import price_formula

    root = _root(db, tenant_id, name)
    design, base = price_formula._context(db, tenant_id, root)
    return {
        "base": base,
        "attributes": [{"name": a["name"], "values": a["values"], "unit": a.get("unit"), "min": a.get("min"), "max": a.get("max"),
                        "default": a.get("default"), "component": a.get("component")}
                       for a in design["attributes"] if a["kind"] == "pricing"],
        "technical": [a["name"] for a in design["attributes"] if a["kind"] != "pricing"],
        "components": [{"name": c["name"], "max_qty": c.get("max_qty") or 1, "parent": c.get("parent")} for c in design["components"]],
        "functions": list(price_formula.FUNCTIONS), "variables": price_formula.VARIABLES, "examples": price_formula.EXAMPLES,
    }


@admin_router.post("/{name}/price-formulas/check")
def check_formula(name: str, payload: FormulaCheckIn, db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)) -> dict[str, Any]:
    from app.crm_core import price_formula

    root = _root(db, tenant_id, name)
    try:
        return price_formula.compile_text(db, tenant_id, root, payload.text, payload.label, payload.charge, payload.picks, payload.rule_id, payload.use_ai)
    except ValueError as exc:
        raise _bad(exc)


class FormulaIn(BaseModel):
    expression: str = Field(min_length=1, max_length=2000)
    label: str | None = Field(default=None, max_length=120)
    charge: str = "recurring"
    described_as: str = Field(default="", max_length=2000)
    rule_id: int | None = None


@admin_router.post("/{name}/price-formulas")
def save_formula(name: str, payload: FormulaIn, db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)) -> dict[str, Any]:
    from app.crm_core import price_formula

    root = _root(db, tenant_id, name)
    if payload.rule_id is not None:
        _rule(db, tenant_id, payload.rule_id)
    try:
        row = price_formula.save(db, tenant_id, root, payload.expression, payload.label, payload.charge, payload.described_as, payload.rule_id)
    except ValueError as exc:
        db.rollback()
        raise _bad(exc)
    db.commit()
    return model.rule_out(row)


def _rule(db: Session, tenant_id: int, rule_id: int) -> ProductRule:
    row = db.get(ProductRule, rule_id)
    if row is None or row.tenant_id != tenant_id:
        raise HTTPException(404, "Rule not found")
    return row


@admin_router.put("/rules/{rule_id}")
def update_rule(rule_id: int, payload: RuleIn, db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)) -> dict[str, Any]:
    row = _rule(db, tenant_id, rule_id)
    try:
        row = model.save_rule(db, tenant_id, row.offering_name, payload.rule_type, payload.text, payload.message, rule_id)
    except ValueError as exc:
        db.rollback()
        raise _bad(exc)
    db.commit()
    return model.rule_out(row)


@admin_router.patch("/rules/{rule_id}/active")
def toggle_rule(rule_id: int, active: bool, db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)) -> dict[str, Any]:
    row = _rule(db, tenant_id, rule_id)
    row.is_active = active
    db.commit()
    return model.rule_out(row)


@admin_router.delete("/rules/{rule_id}", status_code=204)
def delete_rule(rule_id: int, db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)) -> None:
    db.delete(_rule(db, tenant_id, rule_id))
    db.commit()

"""Configurator APIs (crm_core/configurator.py, model_lifecycle.py).

- Native session API (any channel - the UI, intents, integrations): one
  stateless request carries the picks; the answer is every option's state,
  forced choices, conflicts with repairs, messages and the price.
- TM Forum: TMF760 check / query product configuration, TMF679 product
  offering qualification.
- Admin: model tests, QA, regression, release / versions, model-as-code
  JSON, relationship slots and product classes.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.auth import get_current_user, require_roles
from app.crm_core import configurator as cfg
from app.crm_core import lifecycle_service
from app.crm_core import model_lifecycle as lc
from app.crm_core import product_config_service as pc
from app.crm_core import product_model_service as pm
from app.db import get_db
from app.models.auth import User, UserRole
from app.models.crm import Opportunity, Party, Quote
from app.models.product_model import ProductModelTest
from app.tenancy import get_current_tenant_id

router = APIRouter(prefix="/api/configurator", tags=["configurator"], dependencies=[Depends(get_current_user)])
tmf_router = APIRouter(prefix="/tmf-api", tags=["tmf"], dependencies=[Depends(get_current_user)])
admin_router = APIRouter(prefix="/api/admin/product-model", tags=["admin-product-model"], dependencies=[Depends(require_roles(UserRole.admin))])


class Context(BaseModel):
    picks: dict[str, Any] = {}
    mode: str = "sales"  # sales | order
    opportunity_id: int | None = None
    quote_id: int | None = None
    party_id: int | None = None
    price_list: str | None = None
    workspace: bool = False  # admins: try the workspace instead of the released version
    objective: str | None = None  # cost | default -> complete the configuration
    max_monthly: float | None = None


def _setup(db: Session, tenant_id: int, user: User, name: str, ctx: Context) -> tuple[dict[str, Any], float, str, Party | None]:
    offering = pm.offering_of(db, tenant_id, name)
    if offering is None:
        raise HTTPException(404, f"No product named '{name}'")
    if ctx.workspace and getattr(user.role, "value", user.role) != "admin":
        raise HTTPException(403, "Only administrators can try the workspace")
    design = pc.schema(db, tenant_id, offering.name, workspace=ctx.workspace)
    quote = lifecycle_service.get_record(db, tenant_id, "quote", ctx.quote_id) if ctx.quote_id else None
    opportunity = (db.get(Opportunity, quote.opportunity_id) if isinstance(quote, Quote)
                   else lifecycle_service.get_record(db, tenant_id, "opportunity", ctx.opportunity_id) if ctx.opportunity_id else None)
    if not ctx.workspace:
        # in a quote's / opportunity's context the product is its frozen instance, not today's catalog
        from app.crm_core import instance_service

        frozen = (instance_service.design_for(db, tenant_id, "quote", quote.id, offering.name) if isinstance(quote, Quote) else None) or \
            (instance_service.design_for(db, tenant_id, "opportunity", opportunity.id, offering.name) if isinstance(opportunity, Opportunity) else None)
        if frozen is not None:
            design = frozen
    party = (opportunity.lead.party if isinstance(opportunity, Opportunity) and opportunity.lead is not None
             else db.get(Party, ctx.party_id) if ctx.party_id else None)
    if party is not None and party.tenant_id != tenant_id:
        party = None
    try:
        price_list = pc.price_list_for(db, tenant_id, party, opportunity if isinstance(opportunity, Opportunity) else None,
                                       quote if isinstance(quote, Quote) else None, ctx.price_list)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    base, source = pc.base_price(db, tenant_id, price_list, offering)
    return pc.priced_slots(db, tenant_id, design, price_list), base, source, party


def _run(db: Session, tenant_id: int, name: str, design: dict[str, Any], base: float, source: str, party: Party | None, ctx: Context) -> dict[str, Any]:
    result = cfg.session(design, base, ctx.picks, ctx.mode, complete_objective=ctx.objective, max_monthly=ctx.max_monthly)
    result["price"]["source"] = source
    result["customer_errors"] = pm.customer_rule_errors(db, tenant_id, design["offering_name"] or name, party) if party is not None else []
    if result["customer_errors"]:
        result["valid"] = False
    return result


@router.post("/{name}/session")
def session(name: str, ctx: Context, db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id),
            user: User = Depends(get_current_user)) -> dict[str, Any]:
    design, base, source, party = _setup(db, tenant_id, user, name, ctx)
    return _run(db, tenant_id, name, design, base, source, party, ctx)


@router.post("/{name}/validate")
def validate(name: str, ctx: Context, db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id),
             user: User = Depends(get_current_user)) -> dict[str, Any]:
    """The authoritative check a quote / order runs (configure)."""
    design, base, source, party = _setup(db, tenant_id, user, name, ctx)
    pricing = {k: v for k, v in ctx.picks.items() if not pc._find([a for a in design["attributes"] if a["kind"] == "technical"], k)}
    technical = {k: v for k, v in ctx.picks.items() if k not in pricing}
    try:
        configured = pc.configure(db, tenant_id, design["offering_name"], pricing, technical, base, require_technical=ctx.mode == "order", party=party,
                                  price_list=pc.price_list_for(db, tenant_id, party, explicit=ctx.price_list), design=design)
    except pc.ConfigurationError as exc:
        return {"ok": False, "errors": str(exc).split("; ")}
    return {"ok": True, "errors": [], "configured_price": configured["configured"], "one_time": configured["one_time"],
            "lines": configured["lines"], "messages": configured["messages"], "model_version": configured["model_version"], "price_source": source}


class ExplainIn(Context):
    name: str
    value: str


@router.post("/{product}/explain")
def explain(product: str, payload: ExplainIn, db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id),
            user: User = Depends(get_current_user)) -> dict[str, Any]:
    design, base, _, _ = _setup(db, tenant_id, user, product, payload)
    model = cfg.compile_model(design, base, payload.picks, payload.mode)
    picks, _ = cfg.normalize_picks(model, payload.picks)
    var = next((v for v in model.vars.values() if pc._key(v.name) == pc._key(payload.name)), None)
    if var is None:
        raise HTTPException(404, f"No choice named '{payload.name}'")
    trial = {**{k: v for k, v in picks.items() if k != var.name}, var.name: payload.value}
    if cfg.solve(model, trial) is not None:
        return {"possible": True, "why": f"{var.label} = {payload.value} is possible with your other choices"}
    return {"possible": False, "why": cfg.why_not(model, picks, var.name, payload.value)}


class AiIn(Context):
    text: str = Field(min_length=1, max_length=2000)


@router.post("/{name}/ai")
def ai(name: str, payload: AiIn, db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id),
       user: User = Depends(get_current_user)) -> dict[str, Any]:
    design, base, source, party = _setup(db, tenant_id, user, name, payload)
    result = cfg.ai_configure(db, tenant_id, design, base, payload.text, payload.picks)
    result["price"]["source"] = source
    result["customer_errors"] = pm.customer_rule_errors(db, tenant_id, design["offering_name"], party) if party is not None else []
    return result


class DeltaIn(BaseModel):
    before: dict[str, Any]
    after: dict[str, Any]


@router.post("/delta")
def delta(payload: DeltaIn) -> list[dict[str, Any]]:
    return cfg.delta(payload.before, payload.after)


# ---------------------------------------------------------------- TM Forum


def _characteristics(raw: Any) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for c in raw or []:
        if isinstance(c, dict) and c.get("name"):
            value = c.get("value")
            if isinstance(value, dict):
                value = value.get("value")
            out[str(c["name"])] = value
    return out


def _tmf_item(db: Session, tenant_id: int, user: User, item: dict[str, Any], party_id: int | None, query: bool) -> dict[str, Any]:
    config = item.get("productConfiguration") or item
    offering = (config.get("productOffering") or {}).get("name") or (config.get("productOffering") or {}).get("id")
    picks = _characteristics(config.get("productCharacteristic") or config.get("configurationCharacteristic"))
    for child in config.get("productConfigurationItem") or config.get("bundledProduct") or []:
        name = (child.get("productOffering") or {}).get("name") or child.get("name")
        if name:
            picks[name] = child.get("quantity", 1)
    ctx = Context(picks=picks, party_id=party_id)
    design, base, source, party = _setup(db, tenant_id, user, str(offering or ""), ctx)
    result = _run(db, tenant_id, str(offering), design, base, source, party, ctx)
    reasons = ([{"code": "conflict", "label": result["conflict"]["message"]}] if result["conflict"] else []) + \
              [{"code": "invalid", "label": p} for p in result["problems"]] + [{"code": "ineligible", "label": e} for e in result["customer_errors"]] + \
              [{"code": "incomplete", "label": f"{m} is required"} for m in result["missing"]]
    out: dict[str, Any] = {
        "@type": "QueryProductConfigurationItem" if query else "CheckProductConfigurationItem",
        "state": "approved" if result["valid"] and not result["missing"] else "rejected",
        "stateReason": reasons,
        "productConfiguration": {
            "@type": "ProductConfiguration",
            "productOffering": {"name": design["offering_name"], "version": str(design.get("version") or "workspace")},
            "productCharacteristic": [{"name": k, "value": v} for k, v in result["instance"].items()],
            "productConfigurationPrice": [
                {"priceType": "recurring", "recurringChargePeriod": "month", "price": {"taxIncludedAmount": {"unit": "INR", "value": result["price"]["monthly"]}}},
                {"priceType": "oneTime", "price": {"taxIncludedAmount": {"unit": "INR", "value": result["price"]["one_time"]}}},
            ],
        },
        "alternateProductConfigurationProposal": [
            {"name": r["label"], "productCharacteristic": [{"name": k, "value": v} for k, v in r["changes"].items()]}
            for r in (result["conflict"] or {}).get("repairs", [])
        ],
    }
    if query:
        out["productConfiguration"]["configurationCharacteristic"] = [
            {"name": v["name"], "minCardinality": 1 if v["required"] else 0, "maxCardinality": 1,
             "configurationCharacteristicValue": [{"value": x["value"], "isSelectable": x["state"] != "excluded", "isSelected": x["state"] == "selected",
                                                   **({"stateReason": x["reason"]} if x.get("reason") else {})}
                                                  for x in v["values"] if x["value"] not in ("",)]}
            for v in result["variables"]
        ]
    return out


def _party_from(body: dict[str, Any]) -> int | None:
    for p in body.get("relatedParty") or []:
        try:
            return int(p.get("id"))
        except (TypeError, ValueError):
            continue
    return None


@tmf_router.post("/productConfigurationManagement/v5/checkProductConfiguration")
def tmf760_check(body: dict[str, Any], db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id),
                 user: User = Depends(get_current_user)) -> dict[str, Any]:
    items = body.get("checkProductConfigurationItem") or [body]
    out = [_tmf_item(db, tenant_id, user, i, _party_from(body), False) for i in items]
    return {"@type": "CheckProductConfiguration", "state": "done", "checkProductConfigurationItem": out}


@tmf_router.post("/productConfigurationManagement/v5/queryProductConfiguration")
def tmf760_query(body: dict[str, Any], db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id),
                 user: User = Depends(get_current_user)) -> dict[str, Any]:
    items = body.get("requestProductConfigurationItem") or body.get("queryProductConfigurationItem") or [body]
    out = [_tmf_item(db, tenant_id, user, i, _party_from(body), True) for i in items]
    return {"@type": "QueryProductConfiguration", "state": "done", "computedProductConfigurationItem": out}


@tmf_router.post("/productOfferingQualification/v5/checkProductOfferingQualification")
def tmf679(body: dict[str, Any], db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)) -> dict[str, Any]:
    party_id = _party_from(body)
    party = db.get(Party, party_id) if party_id else None
    if party is None or party.tenant_id != tenant_id:
        raise HTTPException(400, "relatedParty must name a customer of this organization (id)")
    names = [((i.get("productOffering") or {}).get("name")) for i in body.get("checkProductOfferingQualificationItem") or []]
    names = [n for n in names if n] or [p["name"] for p in pm.products(db, tenant_id) if p["status"] == "active"]
    eligible, why = pm.eligible(db, tenant_id, names, party)
    return {
        "@type": "CheckProductOfferingQualification", "state": "done", "qualificationResult": "qualified" if eligible else "unqualified",
        "checkProductOfferingQualificationItem": [
            {"productOffering": {"name": n}, "qualificationItemResult": "qualified" if n in eligible else "unqualified",
             **({"eligibilityUnavailabilityReason": [{"code": "eligibility", "label": why[n]}]} if n in why else {})}
            for n in names
        ],
    }


# ---------------------------------------------------------------- admin: tests, QA, release, versions, JSON


def _name(db: Session, tenant_id: int, name: str) -> str:
    offering = pm.offering_of(db, tenant_id, name)
    if offering is None:
        raise HTTPException(404, f"No product named '{name}'")
    return offering.name


class TestIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    picks: dict[str, Any] = {}
    expect: str = "valid"
    expect_price: float | None = None
    expect_domains: dict[str, list[str]] = {}


@admin_router.get("/{name}/tests")
def tests(name: str, run: bool = True, db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)) -> list[dict[str, Any]]:
    name = _name(db, tenant_id, name)
    return lc.run_tests(db, tenant_id, name) if run else [lc.test_out(t) for t in lc.tests_of(db, tenant_id, name)]


@admin_router.post("/{name}/tests")
def add_test(name: str, payload: TestIn, db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)) -> dict[str, Any]:
    import json

    name = _name(db, tenant_id, name)
    if payload.expect not in ("valid", "invalid"):
        raise HTTPException(400, "expect is valid or invalid")
    row = ProductModelTest(tenant_id=tenant_id, offering_name=name, name=payload.name, picks_json=json.dumps(payload.picks), expect=payload.expect,
                           expect_price=payload.expect_price, expect_domains_json=json.dumps(payload.expect_domains))
    db.add(row)
    db.commit()
    return lc.test_out(row)


@admin_router.delete("/tests/{test_id}", status_code=204)
def delete_test(test_id: int, db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)) -> None:
    row = db.get(ProductModelTest, test_id)
    if row is None or row.tenant_id != tenant_id:
        raise HTTPException(404, "Test not found")
    db.delete(row)
    db.commit()


@admin_router.get("/{name}/qa")
def qa(name: str, db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)) -> list[dict[str, str]]:
    return cfg.lint(lc.workspace(db, tenant_id, _name(db, tenant_id, name)))


@admin_router.get("/{name}/release-check")
def release_check(name: str, db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)) -> dict[str, Any]:
    out = lc.check_release(db, tenant_id, _name(db, tenant_id, name))
    out.pop("design", None)
    return out


class ReleaseIn(BaseModel):
    valid_from: dt.datetime | None = None
    notes: str = ""
    accept_regressions: bool = True


@admin_router.post("/{name}/release")
def release(name: str, payload: ReleaseIn, db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)) -> dict[str, Any]:
    try:
        row = lc.release(db, tenant_id, _name(db, tenant_id, name), payload.valid_from, payload.notes, payload.accept_regressions)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    db.commit()
    return next(v for v in lc.versions(db, tenant_id, row.offering_name) if v["id"] == row.id)


@admin_router.get("/{name}/versions")
def versions(name: str, db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)) -> list[dict[str, Any]]:
    return lc.versions(db, tenant_id, _name(db, tenant_id, name))


@admin_router.post("/versions/{version_id}/retire")
def retire(version_id: int, db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)) -> dict[str, Any]:
    try:
        row = lc.retire_version(db, tenant_id, version_id)
    except ValueError as exc:
        raise HTTPException(404, str(exc))
    db.commit()
    return {"id": row.id, "status": row.status}


@admin_router.get("/{name}/model.json")
def model_json(name: str, version: int | None = None, db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)) -> dict[str, Any]:
    try:
        return lc.model_json(db, tenant_id, _name(db, tenant_id, name), version)
    except ValueError as exc:
        raise HTTPException(404, str(exc))


@admin_router.post("/{name}/model.json")
def load_model_json(name: str, body: dict[str, Any], db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)) -> dict[str, Any]:
    try:
        changes = lc.load_model_json(db, tenant_id, _name(db, tenant_id, name), body)
    except (ValueError, KeyError, TypeError) as exc:
        db.rollback()
        raise HTTPException(400, f"Could not load the model: {exc}")
    db.commit()
    return {"changes": changes}


class SlotIn(BaseModel):
    domain_type: str = "products"  # products | class | none
    products: list[str] = []
    class_name: str | None = None
    default: str | None = None


@admin_router.put("/{name}/components/{component}/slot")
def set_slot(name: str, component: str, payload: SlotIn, db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)) -> dict[str, Any]:
    name = _name(db, tenant_id, name)
    try:
        change = lc.set_slot(db, tenant_id, name, component, payload.domain_type, payload.products, payload.class_name, payload.default)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    db.commit()
    return {"change": change, "tree": pm.tree(db, tenant_id, name)}


class ClassIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    parent: str | None = None
    description: str = ""
    members: list[str] = []


classes_router = APIRouter(prefix="/api/admin/product-classes", tags=["admin-product-model"], dependencies=[Depends(require_roles(UserRole.admin))])


@classes_router.get("")
def list_classes(db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)) -> list[dict[str, Any]]:
    return lc.classes(db, tenant_id)


@classes_router.post("")
def save_class(payload: ClassIn, db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)) -> dict[str, Any]:
    try:
        out = lc.save_class(db, tenant_id, payload.name, payload.parent, payload.description, payload.members)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    db.commit()
    return out


class ClassAttributeIn(BaseModel):
    name: str
    values: list[str] = []
    default: str | None = None
    affects_price: bool = False
    affects_provisioning: bool = True
    required: bool = False
    unit: str | None = None
    min: float | None = None
    max: float | None = None


@classes_router.post("/{class_name}/attributes")
def class_attribute(class_name: str, payload: ClassAttributeIn, db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)) -> dict[str, Any]:
    op = {**payload.model_dump(), "kind": "pricing" if payload.affects_price else "technical"}
    if payload.min is not None or payload.max is not None:
        op["data_type"] = "number"
    try:
        pc._upsert_attribute(db, tenant_id, f"class:{class_name}", op)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    db.commit()
    return next((c for c in lc.classes(db, tenant_id) if pc._key(c["name"]) == pc._key(class_name)), {})


# ---------------------------------------------------------------- product instances (frozen definitions in a deal)


@router.get("/instances")
def instances(context_type: str, context_id: int, db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)) -> list[dict[str, Any]]:
    """The products in an opportunity / quote / order, as frozen when picked."""
    from app.crm_core import instance_service
    from app.models.product_model import ProductInstance

    rows = (db.query(ProductInstance).filter(ProductInstance.tenant_id == tenant_id, ProductInstance.context_type == context_type,
                                             ProductInstance.context_id == context_id).order_by(ProductInstance.id).all())
    return [instance_service.describe(db, tenant_id, r) for r in rows]


@router.get("/instances/{instance_id}/definition")
def instance_definition(instance_id: int, db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)) -> dict[str, Any]:
    from app.crm_core import instance_service
    from app.models.product_model import ProductInstance

    row = db.get(ProductInstance, instance_id)
    if row is None or row.tenant_id != tenant_id:
        raise HTTPException(404, "Product instance not found")
    return {**instance_service.describe(db, tenant_id, row), "definition": instance_service.design(row)}


@router.post("/instances/{instance_id}/refresh")
def refresh_instance(instance_id: int, db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)) -> dict[str, Any]:
    """Take the catalog's current definition into this deal (an explicit
    upgrade) - only while the opportunity / quote / order can still change."""
    from app.crm_core import instance_service, ponr_service
    from app.models.orders import Order
    from app.models.product_model import ProductInstance

    row = db.get(ProductInstance, instance_id)
    if row is None or row.tenant_id != tenant_id:
        raise HTTPException(404, "Product instance not found")
    record = lifecycle_service.get_record(db, tenant_id, row.context_type, row.context_id) if row.context_type != "order" else db.get(Order, row.context_id)
    status = getattr(getattr(record, "status", None), "value", None) or getattr(getattr(record, "stage", None), "value", "")
    if row.context_type == "quote" and ponr_service.has_reached_ponr(db, tenant_id, "quote", status):
        raise HTTPException(409, f"Quote {row.context_id} is {status} - make a new revision to take the new definition")
    if row.context_type == "order" and status not in ("created", "pending_approval"):
        raise HTTPException(409, f"Order {row.context_id} is {status} - its product definition is fixed")
    instance_service.refresh(db, tenant_id, row)
    db.commit()
    return instance_service.describe(db, tenant_id, row)


# ---------------------------------------------------------------- inheritance: attribute library -> class -> product


class BindIn(BaseModel):
    class_name: str | None = None


@admin_router.get("/{name}/inheritance")
def inheritance(name: str, db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)) -> dict[str, Any]:
    return lc.inheritance(db, tenant_id, name)


@admin_router.put("/{name}/class")
def bind_class(name: str, payload: BindIn, db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)) -> dict[str, Any]:
    """Bind the product to one class (null unbinds) - it inherits the class's attributes."""
    try:
        out = lc.bind_class(db, tenant_id, name, payload.class_name)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    db.commit()
    return out

"""Product configuration API.

Everyone (forms and pages):
- GET  /api/catalog/configuration/{offering_name}         a product's design
- POST /api/catalog/configuration/{offering_name}/price   price a selection,
  with the pricelist that would apply (quote > opportunity > customer > default)

Administrators (Admin -> Product Configuration):
- GET  /api/admin/product-config                           products and their designs
- POST /api/admin/product-config/{offering_name}/plan      plain English -> proposed changes (AI or rules), nothing saved
- POST /api/admin/product-config/{offering_name}/apply     save proposed / hand-made changes
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.auth import get_current_user, require_roles
from app.crm_core import catalog_service, lifecycle_service
from app.crm_core import product_config_service as config
from app.db import get_db
from app.integration import tmf620_adapter
from app.models.auth import User, UserRole
from app.models.catalog import OfferingStatus, ProductOffering
from app.models.crm import Opportunity, Party, Quote
from app.tenancy import get_current_tenant_id

router = APIRouter(prefix="/api/catalog/configuration", tags=["product configuration"])
admin_router = APIRouter(prefix="/api/admin/product-config", tags=["admin"], dependencies=[Depends(require_roles(UserRole.admin))])


def _offering(db: Session, tenant_id: int, name: str) -> ProductOffering:
    offering = tmf620_adapter.get_active_offering_by_name(db, tenant_id, name)
    if offering is None:
        raise HTTPException(404, f"No active product named '{name}'")
    return offering


@router.get("/{offering_name}")
def product_design(offering_name: str, db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id),
                   user: User = Depends(get_current_user)) -> dict[str, Any]:
    offering = _offering(db, tenant_id, offering_name)
    return {**config.schema(db, tenant_id, offering.name), "list_price": offering.price}


class PriceIn(BaseModel):
    pricing_attributes: dict[str, Any] | str | None = None
    technical_attributes: dict[str, Any] | str | None = None
    party_id: int | None = None
    opportunity_id: int | None = None
    quote_id: int | None = None
    price_list: str | None = None
    quantity: int = Field(default=1, ge=1)
    discount_pct: float = Field(default=0, ge=0, le=100)


@router.post("/{offering_name}/price")
def price_selection(offering_name: str, payload: PriceIn, db: Session = Depends(get_db),
                    tenant_id: int = Depends(get_current_tenant_id), user: User = Depends(get_current_user)) -> dict[str, Any]:
    """What this selection would cost - errors are returned, not raised, so a form can show them as you choose."""
    offering = _offering(db, tenant_id, offering_name)
    quote = lifecycle_service.get_record(db, tenant_id, "quote", payload.quote_id) if payload.quote_id else None
    opportunity = (db.get(Opportunity, quote.opportunity_id) if isinstance(quote, Quote)
                   else lifecycle_service.get_record(db, tenant_id, "opportunity", payload.opportunity_id) if payload.opportunity_id else None)
    party = (opportunity.lead.party if isinstance(opportunity, Opportunity) and opportunity.lead is not None
             else lifecycle_service.get_record(db, tenant_id, "party", payload.party_id) if payload.party_id else None)
    try:
        price_list = config.price_list_for(db, tenant_id, party if isinstance(party, Party) else None,
                                           opportunity if isinstance(opportunity, Opportunity) else None,
                                           quote if isinstance(quote, Quote) else None, payload.price_list)
    except ValueError as exc:
        return {"ok": False, "errors": [str(exc)]}
    base, source = config.base_price(db, tenant_id, price_list, offering)
    try:
        configured = config.configure(db, tenant_id, offering.name, payload.pricing_attributes, payload.technical_attributes, base,
                                      party=party if isinstance(party, Party) else None, price_list=price_list)
    except config.ConfigurationError as exc:
        return {"ok": False, "errors": str(exc).split("; "), "base_price": base, "price_source": source}
    ceiling = offering.price_plan.discount_ceiling_pct if offering.price_plan else 20.0
    try:
        unit = catalog_service.compute_price(configured["configured"], payload.discount_pct, ceiling, offering.price_floor)
    except catalog_service.PricingError as exc:
        return {"ok": False, "errors": [str(exc)], "base_price": base, "price_source": source}
    return {
        "ok": True, "errors": [], "price_list": price_list.name if price_list else None, "price_source": source, "base_price": base,
        "lines": configured["lines"], "configured_price": configured["configured"], "discount_applied_pct": min(payload.discount_pct, ceiling),
        "unit_price": unit, "total": round(unit * payload.quantity, 2), "pricing": configured["pricing"], "technical": configured["technical"],
    }


class DraftIn(BaseModel):
    instruction: str = Field(default="", max_length=4000)
    draft: list[dict[str, Any]] = []


@router.post("/{offering_name}/suggest")
def suggest(offering_name: str, db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id),
            user: User = Depends(get_current_user)) -> dict[str, Any]:
    """A starting design for a product (AI, else telecom templates) - nothing is saved."""
    offering = _offering(db, tenant_id, offering_name)
    plan = config.suggest_design(db, tenant_id, offering.name)
    return {**plan, "preview": config.describe_ops(plan["ops"])}


@router.post("/{offering_name}/draft")
def draft(offering_name: str, payload: DraftIn, db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id),
          user: User = Depends(get_current_user)) -> dict[str, Any]:
    """Plain English -> proposed design changes (AI, else the rule parser) - nothing is saved."""
    offering = _offering(db, tenant_id, offering_name)
    if not payload.instruction.strip():
        return {"ops": [], "engine": "rules", "reply": "", "preview": []}
    plan = config.plan_design(db, tenant_id, offering.name, payload.instruction, payload.draft)
    return {**plan, "preview": config.describe_ops(plan["ops"])}


# ---------------------------------------------------------------- admin


@admin_router.get("")
def list_designs(db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)) -> list[dict[str, Any]]:
    # the same products the Product Modeller lists: drafts too (not only what is on sale)
    names = sorted({o.name for o in db.query(ProductOffering).filter(ProductOffering.tenant_id == tenant_id,
                                                                        ProductOffering.status != OfferingStatus.retired).all()})
    out = []
    for name in names:
        design = config.schema(db, tenant_id, name, workspace=True)
        out.append({"offering_name": name,
                    "pricing": sum(1 for a in design["attributes"] if a["kind"] == "pricing"),
                    "technical": sum(1 for a in design["attributes"] if a["kind"] == "technical"),
                    "rules": len(design["rules"])})
    return out


class PlanIn(BaseModel):
    instruction: str = Field(min_length=1, max_length=4000)


@admin_router.post("/{offering_name}/plan")
def plan_changes(offering_name: str, payload: PlanIn, db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)) -> dict[str, Any]:
    offering = _offering(db, tenant_id, offering_name)
    plan = config.plan_design(db, tenant_id, offering.name, payload.instruction)
    return {**plan, "preview": config.describe_ops(plan["ops"])}


class ApplyIn(BaseModel):
    ops: list[dict[str, Any]] = Field(min_length=1)


@admin_router.post("/{offering_name}/apply")
def apply_changes(offering_name: str, payload: ApplyIn, db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)) -> dict[str, Any]:
    offering = _offering(db, tenant_id, offering_name)
    try:
        changes = config.apply_design(db, tenant_id, offering.name, payload.ops)
    except (ValueError, KeyError) as exc:
        db.rollback()
        raise HTTPException(400, str(exc)) from exc
    db.commit()
    return {"changes": changes, "design": config.schema(db, tenant_id, offering.name, workspace=True)}

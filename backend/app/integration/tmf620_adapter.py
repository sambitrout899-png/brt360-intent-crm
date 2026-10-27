"""Internal adapter shaped like the TMF620 Product Catalog Management API.

HLD 6.1: vendor-proprietary APIs are wrapped behind an internal adapter that
exposes the TMF-shaped contract to the rest of the platform, so a vendor swap
doesn't ripple into Orchestration/Intent. For Sprint 1 there is no external
PDC/OSM yet, so this adapter's implementation IS the local database - but
crm_core only ever calls through this module's functions, never the ORM
directly, so pointing this at a real TMF620-compliant system later is a
contained change.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.models.catalog import OfferingStatus, PricePlan, ProductOffering


def create_offering_version(
    db: Session,
    *,
    tenant_id: int,
    name: str,
    segment: str,
    composition: str,
    list_price: float,
    price_floor: float,
    discount_ceiling_pct: float,
    parent_offering_id: int | None = None,
) -> ProductOffering:
    version = 1
    if parent_offering_id is not None:
        parent = db.get(ProductOffering, parent_offering_id)
        if parent is not None:
            version = parent.version + 1

    offering = ProductOffering(
        tenant_id=tenant_id,
        name=name,
        segment=segment,
        composition=composition,
        status=OfferingStatus.draft,
        version=version,
        price=list_price,
        price_floor=price_floor,
        parent_offering_id=parent_offering_id,
    )
    db.add(offering)
    db.flush()

    db.add(
        PricePlan(
            product_offering_id=offering.id,
            list_price=list_price,
            discount_ceiling_pct=discount_ceiling_pct,
        )
    )
    db.flush()
    return offering


def publish_offering(db: Session, offering_id: int) -> ProductOffering:
    offering = db.get(ProductOffering, offering_id)
    if offering is None:
        raise ValueError(f"Offering {offering_id} not found")
    offering.status = OfferingStatus.active
    db.flush()
    return offering


def supersede_offering(db: Session, offering_id: int) -> ProductOffering:
    offering = db.get(ProductOffering, offering_id)
    if offering is None:
        raise ValueError(f"Offering {offering_id} not found")
    offering.status = OfferingStatus.superseded
    db.flush()
    return offering


def retire_offering(db: Session, offering_id: int, *, replacement_offering_id: int, end_date) -> ProductOffering:
    offering = db.get(ProductOffering, offering_id)
    if offering is None:
        raise ValueError(f"Offering {offering_id} not found")
    offering.status = OfferingStatus.retired
    offering.end_date = end_date
    offering.replacement_offering_id = replacement_offering_id
    db.flush()
    return offering


def not_sellable(db: Session, tenant_id: int, name: str) -> str:
    """Why a product can't be sold - for a retired one, what replaced it
    (Siebel offers the replacement product)."""
    latest = (db.query(ProductOffering).filter(ProductOffering.tenant_id == tenant_id, ProductOffering.name == name)
              .order_by(ProductOffering.version.desc()).first())
    if latest is None:
        return f"No active product named '{name}'"
    if latest.status == OfferingStatus.retired:
        replacement = db.get(ProductOffering, latest.replacement_offering_id) if latest.replacement_offering_id else None
        return f"{name} is retired" + (f" - sell its replacement, {replacement.name}" if replacement is not None else "")
    return f"{name} is {latest.status.value}, not on sale - publish it first"


def active_offering_names(db: Session, tenant_id: int) -> list[str]:
    """The products on sale now - one name each, whatever versions sit behind it
    (drafts, superseded and retired versions are left out)."""
    rows = (db.query(ProductOffering.name)
            .filter(ProductOffering.tenant_id == tenant_id, ProductOffering.status == OfferingStatus.active)
            .distinct().order_by(ProductOffering.name))
    return [name for (name,) in rows]


def get_active_offering_by_name(db: Session, tenant_id: int, name: str) -> ProductOffering | None:
    return (
        db.query(ProductOffering)
        .filter(
            ProductOffering.tenant_id == tenant_id,
            ProductOffering.name == name,
            ProductOffering.status == OfferingStatus.active,
        )
        .order_by(ProductOffering.version.desc())
        .first()
    )

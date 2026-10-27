"""Platform-level tenant management.

Not tenant-scoped itself - this is what a user's JWT ties them to.
platform_admin-only: a regular tenant's admin cannot create new tenants,
only manage their own.
"""

from __future__ import annotations

import base64
import binascii
import re

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.auth import hash_password, require_roles
from app.db import get_db
from app.models.auth import User, UserRole
from app.models.tenant import Tenant, TenantVertical
from app.schemas.admin import TenantCreate, TenantOut, TenantUpdate
from app.seed import seed_reference_data
from app.licensing import validate_modules

# Logos are stored inline (a data: URL on the tenant row) - small images only.
MAX_LOGO_BYTES = 512 * 1024
_LOGO = re.compile(r"^data:image/(png|jpeg|gif|webp|svg\+xml);base64,([A-Za-z0-9+/=\s]+)$")
_COLOR = re.compile(r"^#[0-9a-fA-F]{6}$")


def _check_logo(data_url: str) -> str:
    match = _LOGO.match(data_url.strip())
    if match is None:
        raise HTTPException(400, "The logo must be a PNG, JPEG, GIF, WebP or SVG image")
    try:
        raw = base64.b64decode(match.group(2), validate=False)
    except (binascii.Error, ValueError):
        raise HTTPException(400, "The logo image could not be read")
    if len(raw) > MAX_LOGO_BYTES:
        raise HTTPException(400, f"The logo is {len(raw) // 1024} KB - the limit is {MAX_LOGO_BYTES // 1024} KB")
    return data_url.strip()

router = APIRouter(
    prefix="/api/platform", tags=["platform"], dependencies=[Depends(require_roles(UserRole.platform_admin))]
)


@router.get("/tenants", response_model=list[TenantOut])
def list_tenants(db: Session = Depends(get_db)) -> list[Tenant]:
    return db.query(Tenant).order_by(Tenant.name).all()


@router.post("/tenants", response_model=TenantOut, status_code=201)
def create_tenant(payload: TenantCreate, db: Session = Depends(get_db)) -> Tenant:
    if payload.vertical not in ("b2c", "b2b", "both"):
        raise HTTPException(400, "vertical must be one of: b2c, b2b, both")
    existing = db.query(Tenant).filter(Tenant.slug == payload.slug).first()
    if existing is not None:
        raise HTTPException(400, f"Tenant slug '{payload.slug}' already exists")
    if db.query(User).filter(User.email == payload.admin_email).first() is not None:
        raise HTTPException(400, f"Email '{payload.admin_email}' is already registered")

    tenant = Tenant(name=payload.name, slug=payload.slug, vertical=TenantVertical(payload.vertical),
                    licensed_modules=validate_modules(payload.licensed_modules))
    db.add(tenant)
    db.flush()
    # A new tenant needs its own reference data immediately - the same
    # seeding every other tenant got - so it's usable the moment it's
    # created through the UI, not an empty shell someone has to seed by
    # hand via the CLI.
    if "telecom_crm" in tenant.licensed_modules:
        seed_reference_data(db, tenant.id)
    db.add(
        User(
            tenant_id=tenant.id,
            email=payload.admin_email,
            password_hash=hash_password(payload.admin_password),
            name=payload.admin_name,
            role=UserRole.admin,
        )
    )
    db.commit()
    db.refresh(tenant)
    return tenant


@router.put("/tenants/{tenant_id}", response_model=TenantOut)
def update_tenant(tenant_id: int, payload: TenantUpdate, db: Session = Depends(get_db)) -> Tenant:
    """Edit a tenant's details and branding. The slug is its permanent
    identifier and can't change."""
    tenant = db.get(Tenant, tenant_id)
    if tenant is None:
        raise HTTPException(404, "Tenant not found")
    if payload.licensed_modules is not None:
        from app.licensing import enabled
        previously_enabled = enabled(db, tenant_id)
        tenant.licensed_modules = validate_modules(payload.licensed_modules)
        if "telecom_crm" in tenant.licensed_modules and "telecom_crm" not in previously_enabled:
            seed_reference_data(db, tenant_id)
    if payload.name is not None:
        tenant.name = payload.name.strip()
    if payload.vertical is not None:
        if payload.vertical not in ("b2c", "b2b", "both"):
            raise HTTPException(400, "vertical must be one of: b2c, b2b, both")
        tenant.vertical = TenantVertical(payload.vertical)
    if payload.display_name is not None:
        tenant.display_name = payload.display_name.strip() or None
    if payload.brand_color is not None:
        if payload.brand_color and not _COLOR.match(payload.brand_color):
            raise HTTPException(400, "brand_color must look like #1d4ed8")
        tenant.brand_color = payload.brand_color or None
    if payload.logo_data_url is not None:
        tenant.logo_data_url = _check_logo(payload.logo_data_url) if payload.logo_data_url else None
    db.commit()
    db.refresh(tenant)
    return tenant

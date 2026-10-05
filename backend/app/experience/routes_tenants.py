"""Platform-level tenant management.

Not tenant-scoped itself - this is what a user's JWT ties them to.
platform_admin-only: a regular tenant's admin cannot create new tenants,
only manage their own.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.auth import hash_password, require_roles
from app.db import get_db
from app.models.auth import User, UserRole
from app.models.tenant import Tenant, TenantVertical
from app.schemas.admin import TenantCreate, TenantOut
from app.seed import seed_reference_data

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

    tenant = Tenant(name=payload.name, slug=payload.slug, vertical=TenantVertical(payload.vertical))
    db.add(tenant)
    db.flush()
    # A new tenant needs its own reference data immediately - the same
    # seeding every other tenant got - so it's usable the moment it's
    # created through the UI, not an empty shell someone has to seed by
    # hand via the CLI.
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

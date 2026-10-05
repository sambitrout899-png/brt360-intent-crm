"""Tenant resolution (HLD multi-tenant extension).

Every request resolves to exactly one tenant, via the `X-Tenant-Slug` header.
No auth exists yet (see docs - deferred, same as Sprint 1), so this is not a
security boundary today; it IS the data-isolation boundary every query must
respect. When auth lands, tenant resolution moves to come from the verified
session/JWT instead of a client-supplied header - this function is the single
seam that change touches.

Falls back to the "default" tenant (seeded by app.seed) when no header is
sent, so the existing single-tenant deployment and the Sprint 1-4 test suite
keep working unchanged.
"""

from __future__ import annotations

from fastapi import Depends, Header, HTTPException
from sqlalchemy.orm import Session

from app.db import get_db
from app.models.tenant import Tenant

DEFAULT_TENANT_SLUG = "default"


def get_current_tenant(
    x_tenant_slug: str | None = Header(default=None),
    db: Session = Depends(get_db),
) -> Tenant:
    slug = x_tenant_slug or DEFAULT_TENANT_SLUG
    tenant = db.query(Tenant).filter(Tenant.slug == slug).first()
    if tenant is None:
        raise HTTPException(400, f"Unknown tenant '{slug}'")
    return tenant


def get_current_tenant_id(tenant: Tenant = Depends(get_current_tenant)) -> int:
    return tenant.id

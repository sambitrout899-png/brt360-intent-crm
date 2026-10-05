"""Tenant resolution.

tenant_id now comes from the authenticated user's verified JWT
(app/auth.py's get_current_user), not a client-supplied header - this
closes the gap every docstring in this module used to disclose: before
auth existed, any caller could set X-Tenant-Slug and read another
tenant's data. get_current_tenant_id is still the one dependency every
tenant-scoped route depends on, so this was the single seam that change
touched - no route file needed editing.

A platform_admin's token carries tenant_id=None (they operate across
tenants via /api/platform/*, not any single tenant's data) - calling
get_current_tenant_id as that role is a 403, not a silent wrong answer.
"""

from __future__ import annotations

from fastapi import Depends, HTTPException, status

from app.auth import get_current_user
from app.models.auth import User

# Slug of the tenant app.seed creates by default - not an auth fallback
# anymore, just the name every fresh deployment's first tenant gets.
DEFAULT_TENANT_SLUG = "default"


def get_current_tenant_id(user: User = Depends(get_current_user)) -> int:
    if user.tenant_id is None:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "This account is not bound to a tenant - use /api/platform/* instead")
    return user.tenant_id

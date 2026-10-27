"""FastAPI dependency resolving the caller's pages + data visibility
(crm_core/access_service.py). The client says which of its positions it
is working as (X-Position-Id) and how wide a view it wants (X-Data-Scope);
both are validated against what the user actually holds - an unknown
position falls back to the primary one, a too-wide scope is clamped.

require_page / require_intent guard the screen APIs the same way the UI
and the intent pipeline do: a screen's data needs one of the pages that
show it, and a direct change needs one of the intents that make it."""

from __future__ import annotations

from typing import Any, Callable

from fastapi import Depends, Header, HTTPException
from sqlalchemy.orm import Session

from app.auth import get_current_user
from app.crm_core import access_service
from app.crm_core.access_service import Access
from app.db import get_db
from app.models.auth import User
from app.tenancy import get_current_tenant_id


def get_access(
    x_position_id: int | None = Header(default=None),
    x_data_scope: str | None = Header(default=None),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Access:
    return access_service.resolve(db, user, x_position_id, x_data_scope)


_PAGE_LABELS = dict(access_service.PAGES)


def require_page(*pages: str) -> Callable[..., Access]:
    """The caller holds at least one of these pages (Admin -> Roles)."""

    def _check(access: Access = Depends(get_access)) -> Access:
        if not any(p in access.pages for p in pages):
            names = " or ".join(_PAGE_LABELS.get(p, p) for p in pages)
            raise HTTPException(403, f"This needs the {names} page - ask your administrator for access.")
        return access

    return _check


def require_intent(*codes: str) -> Callable[..., None]:
    """The caller may run at least one of these intents (Admin -> Intents):
    a screen's direct change obeys the same switch / role rules as the intent."""

    def _check(
        db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id), user: User = Depends(get_current_user)
    ) -> None:
        from app.intent_engine import intent_registry

        reasons = [intent_registry.availability(db, tenant_id, user, c) for c in codes]
        if all(reasons):
            raise HTTPException(403, reasons[0])

    return _check


def visible_parties(db: Session, access: Access) -> set[int] | None:
    """Customers the caller's position may see; None = all."""
    return access_service.visible_ids(db, access, "party")


def check_party(db: Session, access: Access, party_id: Any) -> None:
    try:
        pid = int(party_id)
    except (TypeError, ValueError):
        return
    if not access_service.can_see(db, access, "party", pid):
        raise HTTPException(404, "Customer not found")

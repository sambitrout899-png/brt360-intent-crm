"""Notifications: what's waiting for the signed-in user (shown when they log
in) and the running bar of key information on the Dashboard, My Work and
Customer 360. Administrators post broadcast messages to it."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.access import get_access
from app.auth import get_current_user, require_roles
from app.crm_core import notice_service
from app.crm_core.access_service import Access
from app.db import get_db
from app.models.auth import User, UserRole
from app.tenancy import get_current_tenant_id

router = APIRouter(prefix="/api/notices", tags=["notices"])
admin_router = APIRouter(prefix="/api/admin/broadcasts", tags=["notices"], dependencies=[Depends(require_roles(UserRole.admin))])


@router.get("")
def my_notices(
    place: str = "dashboard", party_id: int | None = None, db: Session = Depends(get_db),
    tenant_id: int = Depends(get_current_tenant_id), access: Access = Depends(get_access),
) -> dict[str, Any]:
    return notice_service.for_user(db, tenant_id, access, place, party_id)


@router.get("/popups")
def my_popups(db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id), user: User = Depends(get_current_user)) -> list[dict[str, Any]]:
    """Message boxes flows have sent this user (Flow Studio: Notify, channel "popup") that they haven't dismissed."""
    return notice_service.popups_for_user(db, tenant_id, user.id)


class SeenIn(BaseModel):
    up_to: int = Field(ge=0)


@router.post("/popups/seen", status_code=204)
def popups_seen(payload: SeenIn, db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id),
                user: User = Depends(get_current_user)) -> None:
    notice_service.mark_popups_seen(db, tenant_id, user.id, payload.up_to)
    db.commit()


class BroadcastIn(BaseModel):
    text: str = Field(min_length=1, max_length=300)
    level: str = "info"
    place: str = "all"
    ends_at: str | None = None


@admin_router.get("")
def list_broadcasts(db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)) -> list[dict[str, Any]]:
    return notice_service.broadcasts(db, tenant_id)


@admin_router.post("", status_code=201)
def add_broadcast(
    payload: BroadcastIn, db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id),
    user: User = Depends(get_current_user),
) -> dict[str, Any]:
    try:
        item = notice_service.add_broadcast(db, tenant_id, user.id, payload.text, payload.level, payload.place, payload.ends_at)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    db.commit()
    return item


@admin_router.delete("/{broadcast_id}", status_code=204)
def remove_broadcast(broadcast_id: int, db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)) -> None:
    try:
        notice_service.remove_broadcast(db, tenant_id, broadcast_id)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc
    db.commit()

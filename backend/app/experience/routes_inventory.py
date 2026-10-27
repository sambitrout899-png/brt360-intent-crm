"""Inventory API (crm_core/inventory_service.py, inventory_ai.py). Everything needs the
Inventory page (Admin -> Roles); changing items needs an admin or agent (viewers stay
read-only); the schema - item types, pools, reconciliation, the sample network - is
an admin's.

  /api/inventory/overview            health, counts, capacity, pools
  /api/inventory/types               item types (+ /draft: described in plain English)
  /api/inventory/items               search, create, read (detail), change, lifecycle,
                                     operational status, receive stock
  /api/inventory/ask                 a question in plain English -> results
  /api/inventory/relationships       link / unlink items
  /api/inventory/pools               resource pools and their utilisation
  /api/inventory/impact/{item}       what an item takes down with it
  /api/inventory/capacity            per location
  /api/inventory/orders/{order_id}   what an order holds; /release gives it back
  /api/inventory/reconcile           a discovery report against the record (dry run first)
"""

from __future__ import annotations

import json
from typing import Any, Callable

from fastapi import APIRouter, Body, Depends, HTTPException
from sqlalchemy.orm import Session

from app.access import get_access, require_page
from app.crm_core.access_service import Access
from app.auth import get_current_user, require_roles
from app.crm_core import inventory_ai, inventory_service as inv
from app.db import get_db
from app.models.auth import User, UserRole
from app.models.inventory import ITEM_KINDS, LIFECYCLE, OPER_STATUSES, RELATIONSHIPS, TRANSITIONS, InvItemType, InvPool, InvReconcileRun
from app.tenancy import get_current_tenant_id

router = APIRouter(prefix="/api/inventory", tags=["inventory"], dependencies=[Depends(require_page("inventory"))])
_write = Depends(require_roles(UserRole.admin, UserRole.agent))
_admin = Depends(require_roles(UserRole.admin))
Payload = dict[str, Any]


def _do(db: Session, fn: Callable[[], Any]) -> Any:
    try:
        result = fn()
        db.commit()
        return result
    except (ValueError, TypeError, KeyError) as exc:
        db.rollback()
        raise HTTPException(400, str(exc).strip("'\"") or "That can't be done") from exc


# ------------------------------------------------------------------ overview

@router.get("/overview")
def overview(db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)) -> Payload:
    def run() -> Payload:
        from app.crm_core import llm

        target = llm.resolve(db, tenant_id)
        return {"health": inv.health(db, tenant_id), "capacity": inv.capacity(db, tenant_id), "locations": inv.locations_tree(db, tenant_id),
                "ai": target.label if target else None, "kinds": list(ITEM_KINDS), "lifecycle": list(LIFECYCLE),
                "transitions": {k: list(v) for k, v in TRANSITIONS.items()}, "oper_statuses": list(OPER_STATUSES),
                "relationships": {k: {"forward": v[0], "reverse": v[1]} for k, v in RELATIONSHIPS.items()},
                "attribute_types": list(inv.ATTRIBUTE_TYPES)}
    return _do(db, run)


@router.post("/sample", dependencies=[_admin])
def load_sample(db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id), user: User = Depends(get_current_user)) -> Payload:
    return _do(db, lambda: inv.load_sample(db, tenant_id, user.id))


# ------------------------------------------------------------------ types

@router.get("/types")
def list_types(db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)) -> list[Payload]:
    def run() -> list[Payload]:
        inv.ensure_types(db, tenant_id)
        return [inv.type_out(db, t) for t in db.query(InvItemType).filter(InvItemType.tenant_id == tenant_id).order_by(InvItemType.kind, InvItemType.name)]
    return _do(db, run)


@router.post("/types", dependencies=[_admin], status_code=201)
def create_type(payload: Payload = Body(...), db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)) -> Payload:
    return _do(db, lambda: inv.type_out(db, inv.save_type(db, tenant_id, payload)))


@router.put("/types/{type_id}", dependencies=[_admin])
def update_type(type_id: int, payload: Payload = Body(...), db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)) -> Payload:
    return _do(db, lambda: inv.type_out(db, inv.save_type(db, tenant_id, payload, type_id)))


@router.post("/types/draft", dependencies=[_admin])
def draft_type(payload: Payload = Body(...), db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)) -> Payload:
    """An item type drafted from a plain-English description - not saved."""
    return _do(db, lambda: inventory_ai.draft_type(db, tenant_id, str(payload.get("description") or "")))


# ------------------------------------------------------------------ items

@router.post("/items/search")
def search(payload: Payload = Body(default={}), db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)) -> list[Payload]:
    return _do(db, lambda: inv.search(db, tenant_id, payload, limit=min(int(payload.get("limit") or 200), 1000)))


@router.post("/ask")
def ask(payload: Payload = Body(...), db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)) -> Payload:
    """A question in plain English - the tenant's model, else the built-in reader, turns it into a search."""
    return _do(db, lambda: inventory_ai.interpret(db, tenant_id, str(payload.get("question") or "")))


@router.post("/items", dependencies=[_write], status_code=201)
def create_item(payload: Payload = Body(...), db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id),
                user: User = Depends(get_current_user)) -> Payload:
    def run() -> Payload:
        item = inv.create_item(db, tenant_id, str(payload.get("type_code") or ""), str(payload.get("identifier") or ""),
                               name=str(payload.get("name") or ""), parent=payload.get("parent"), attributes_=payload.get("attributes") or {},
                               lifecycle=str(payload.get("lifecycle") or "available"), oper_status=str(payload.get("oper_status") or "unknown"),
                               serial=str(payload.get("serial") or ""), vendor=str(payload.get("vendor") or ""), model=str(payload.get("model") or ""),
                               notes=str(payload.get("notes") or ""), actor_id=user.id, verified=bool(payload.get("verified")))
        return inv.detail(db, tenant_id, item.id)
    return _do(db, run)


@router.post("/items/receive", dependencies=[_write])
def receive(payload: Payload = Body(...), db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id),
            user: User = Depends(get_current_user)) -> Payload:
    def run() -> Payload:
        made = inv.receive_stock(db, tenant_id, str(payload.get("type_code") or ""), int(payload.get("quantity") or 0), payload.get("location"),
                                 vendor=str(payload.get("vendor") or ""), model=str(payload.get("model") or ""),
                                 serial_prefix=str(payload.get("serial_prefix") or ""), actor_id=user.id)
        return {"received": len(made), "items": [inv.item_out(db, i, brief=True) for i in made]}
    return _do(db, run)


@router.get("/items/{item_id}")
def get_item(item_id: int, db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)) -> Payload:
    return _do(db, lambda: inv.detail(db, tenant_id, item_id))


@router.put("/items/{item_id}", dependencies=[_write])
def update_item(item_id: int, payload: Payload = Body(...), db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id),
                user: User = Depends(get_current_user)) -> Payload:
    def run() -> Payload:
        inv.update_item(db, tenant_id, item_id, payload, actor_id=user.id)
        return inv.detail(db, tenant_id, item_id)
    return _do(db, run)


@router.post("/items/{item_id}/lifecycle", dependencies=[_write])
def lifecycle(item_id: int, payload: Payload = Body(...), db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id),
              user: User = Depends(get_current_user)) -> Payload:
    def run() -> Payload:
        inv.set_lifecycle(db, tenant_id, item_id, str(payload.get("to") or ""), note=str(payload.get("note") or ""), actor_id=user.id)
        return inv.detail(db, tenant_id, item_id)
    return _do(db, run)


@router.post("/items/{item_id}/oper-status", dependencies=[_write])
def oper_status(item_id: int, payload: Payload = Body(...), db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id),
                user: User = Depends(get_current_user)) -> Payload:
    def run() -> Payload:
        inv.set_oper_status(db, tenant_id, item_id, str(payload.get("status") or ""), note=str(payload.get("note") or ""), actor_id=user.id)
        return inv.detail(db, tenant_id, item_id)
    return _do(db, run)


@router.post("/items/{item_id}/verify", dependencies=[_write])
def verify(item_id: int, db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id), user: User = Depends(get_current_user)) -> Payload:
    """Checked on site / against the network - its record is current."""
    def run() -> Payload:
        from app.timeutils import utcnow

        item = inv._owned(db, tenant_id, item_id)
        item.last_verified_at = utcnow()
        inv._history(db, item, "verified", {}, "manual", "", user.id)
        return inv.detail(db, tenant_id, item_id)
    return _do(db, run)


# ------------------------------------------------------------------ relationships

@router.post("/relationships", dependencies=[_write], status_code=201)
def relate(payload: Payload = Body(...), db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id),
           user: User = Depends(get_current_user)) -> Payload:
    def run() -> Payload:
        rel = inv.relate(db, tenant_id, payload.get("source"), payload.get("target"), str(payload.get("rel_type") or ""),
                         str(payload.get("note") or ""), actor_id=user.id)
        return {"id": rel.id}
    return _do(db, run)


@router.delete("/relationships/{rel_id}", dependencies=[_write], status_code=204)
def unrelate(rel_id: int, db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id), user: User = Depends(get_current_user)) -> None:
    _do(db, lambda: inv.unrelate(db, tenant_id, rel_id, user.id))


# ------------------------------------------------------------------ pools, impact, capacity

@router.get("/pools")
def list_pools(db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)) -> list[Payload]:
    return [inv.pool_out(db, p) for p in db.query(InvPool).filter(InvPool.tenant_id == tenant_id).order_by(InvPool.name)]


@router.post("/pools", dependencies=[_admin], status_code=201)
def create_pool(payload: Payload = Body(...), db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)) -> Payload:
    return _do(db, lambda: inv.pool_out(db, inv.save_pool(db, tenant_id, payload)))


@router.put("/pools/{pool_id}", dependencies=[_admin])
def update_pool(pool_id: int, payload: Payload = Body(...), db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)) -> Payload:
    return _do(db, lambda: inv.pool_out(db, inv.save_pool(db, tenant_id, payload, pool_id)))


@router.get("/impact/{item_id}")
def impact(item_id: int, db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)) -> Payload:
    return _do(db, lambda: inv.impact(db, tenant_id, item_id))


@router.get("/capacity")
def capacity(location: str | None = None, db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)) -> Payload:
    return _do(db, lambda: inv.capacity(db, tenant_id, location))


@router.get("/orders/{order_id}")
def for_order(order_id: int, db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)) -> list[Payload]:
    return inv.items_for_order(db, tenant_id, order_id)


@router.post("/orders/{order_id}/release", dependencies=[_write])
def release(order_id: int, payload: Payload = Body(default={}), db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id),
            user: User = Depends(get_current_user)) -> Payload:
    return _do(db, lambda: {"released": inv.release_order(db, tenant_id, order_id, actor_id=user.id, note=str(payload.get("note") or ""))})


# ------------------------------------------------------------------ Presales: a deal's network feasibility
# Presales works wherever the deal is - the record panel (My Work), the Sales Pipeline - so access follows the
# opportunity (whoever may see it sees its network check), not the Inventory page.

presales_router = APIRouter(prefix="/api/inventory/opportunities", tags=["inventory"], dependencies=[Depends(get_current_user)])


def _visible_opportunity(db: Session, access: Access, opportunity_id: int) -> None:
    from app.crm_core import access_service

    if not access_service.can_see(db, access, "opportunity", opportunity_id):
        raise HTTPException(404, "Opportunity not found")


@presales_router.get("/{opportunity_id}")
def opportunity_feasibility(opportunity_id: int, address: str = "", db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id),
                            access: Access = Depends(get_access)) -> Payload:
    """The site, the location serving it, each resource the product needs and whether it's free there, and what
    is already held for the deal. Nothing is reserved."""
    _visible_opportunity(db, access, opportunity_id)
    return _do(db, lambda: inv.opportunity_view(db, tenant_id, opportunity_id, address))


@presales_router.post("/{opportunity_id}/hold", dependencies=[_write])
def opportunity_hold(opportunity_id: int, payload: Payload = Body(default={}), db: Session = Depends(get_db),
                     tenant_id: int = Depends(get_current_tenant_id), user: User = Depends(get_current_user),
                     access: Access = Depends(get_access)) -> Payload:
    _visible_opportunity(db, access, opportunity_id)

    def run() -> Payload:
        resources = payload.get("resources")
        inv.hold_for_opportunity(db, tenant_id, opportunity_id, resources if isinstance(resources, list) and resources else None,
                                 address=str(payload.get("address") or ""), actor_id=user.id)
        return inv.opportunity_view(db, tenant_id, opportunity_id, str(payload.get("address") or ""))
    return _do(db, run)


@presales_router.post("/{opportunity_id}/release", dependencies=[_write])
def opportunity_release(opportunity_id: int, payload: Payload = Body(default={}), db: Session = Depends(get_db),
                        tenant_id: int = Depends(get_current_tenant_id), user: User = Depends(get_current_user),
                        access: Access = Depends(get_access)) -> Payload:
    _visible_opportunity(db, access, opportunity_id)

    def run() -> Payload:
        inv.release_opportunity(db, tenant_id, opportunity_id, note=str(payload.get("note") or ""), actor_id=user.id)
        return inv.opportunity_view(db, tenant_id, opportunity_id)
    return _do(db, run)


# ------------------------------------------------------------------ reconciliation

@router.post("/reconcile", dependencies=[_admin])
def reconcile(payload: Payload = Body(...), db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id),
              user: User = Depends(get_current_user)) -> Payload:
    """{"source", "full_scan_of"?, "items": [...], "apply": false} - a dry run unless apply is true."""
    return _do(db, lambda: inv.reconcile(db, tenant_id, payload, dry_run=not bool(payload.get("apply")), actor_id=user.id))


@router.get("/reconcile/runs")
def reconcile_runs(db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)) -> list[Payload]:
    return [{"id": r.id, "source": r.source_name, "dry_run": r.dry_run, "summary": json.loads(r.summary_json or "{}"), "at": r.at}
            for r in db.query(InvReconcileRun).filter(InvReconcileRun.tenant_id == tenant_id).order_by(InvReconcileRun.id.desc()).limit(30)]

"""Fulfillment - COM / SOM / TOM order orchestration - see orchestration/fulfillment_engine.py.

  /api/admin/fulfillment/...  the pattern designer: functions catalog, fulfillment
                      patterns (create from the eTOM template, edit, validate,
                      preview against an order, activate)
  /api/fulfillment/...        the order monitor: orchestration orders and their plans,
                      fallout resolution (retry / skip / complete), submitting
                      a CRM order, and the inventory system's callback
"""

from __future__ import annotations

import json
from typing import Any, Callable

from fastapi import APIRouter, Body, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session, object_session

from app.auth import require_roles
from app.db import get_db
from app.models.auth import UserRole
from app.models.fulfillment_orders import PLAN_MODES, FulfillmentOrder, FulfillmentPattern
from app.models.orders import Order
from app.orchestration import fulfillment_ai, fulfillment_engine
from app.orchestration import fulfillment_functions as fx
from app.tenancy import get_current_tenant_id

admin_router = APIRouter(prefix="/api/admin/fulfillment", tags=["admin"], dependencies=[Depends(require_roles(UserRole.admin))])
router = APIRouter(prefix="/api/fulfillment", tags=["fulfillment"])
_admin = Depends(require_roles(UserRole.admin))


class PatternIn(BaseModel):
    name: str = Field(min_length=1, max_length=150)
    description: str = ""
    products: str = ""
    segment: str = ""
    order_type: str = "new"
    priority: int = 100
    mode: str = "deterministic"
    trigger_intents: str = ""  # vanilla / custom intent codes, comma-separated - blank = Order Control approval
    graph: dict[str, Any] = Field(default_factory=lambda: {"nodes": [], "edges": []})


class GenerateIn(BaseModel):
    description: str = Field(min_length=10, max_length=4000)
    source_pattern: PatternIn | None = None


def _do(db: Session, fn: Callable[[], Any]) -> Any:
    try:
        out = fn()
        db.commit()
        return out
    except (ValueError, KeyError, TypeError) as exc:
        db.rollback()
        raise HTTPException(400, str(exc).strip("'\"") or "That can't be done") from exc


def _pattern_out(p: FulfillmentPattern) -> dict[str, Any]:
    graph = fulfillment_engine.load_graph(p)
    return {"id": p.id, "name": p.name, "description": p.description, "products": p.products, "segment": p.segment,
            "order_type": p.order_type, "priority": p.priority, "mode": p.mode, "trigger_intents": p.trigger_intents or "",
            "started_by": fulfillment_engine.triggers(p), "graph": graph, "version": p.version,
            "is_active": p.is_active, "validation_errors": fulfillment_engine.validate_graph(graph, fulfillment_engine.known_resources(object_session(p), p.tenant_id)),
            "updated_at": p.updated_at}


def _owned(db: Session, tenant_id: int, pattern_id: int) -> FulfillmentPattern:
    p = db.get(FulfillmentPattern, pattern_id)
    if p is None or p.tenant_id != tenant_id:
        raise HTTPException(404, "Fulfillment pattern not found")
    return p


def _apply(db: Session, tenant_id: int, p: FulfillmentPattern, payload: PatternIn) -> None:
    if payload.mode not in PLAN_MODES:
        raise HTTPException(400, f"mode must be one of: {', '.join(PLAN_MODES)}")
    if payload.order_type not in ("", "new", "change", "cancel", "suspend"):
        raise HTTPException(400, "order_type must be new, change, cancel, suspend - or blank for any")
    codes = list(dict.fromkeys(c.strip().upper() for c in payload.trigger_intents.split(",") if c.strip()))
    if codes:
        known = {i["code"] for i in fulfillment_ai.trigger_intents(db, tenant_id)} | {fulfillment_engine.APPROVAL_INTENT}
        unknown = [c for c in codes if c not in known]
        if unknown:
            raise HTTPException(400, f"Not an intent that can start a pattern: {', '.join(unknown)} - use an order intent "
                                     "(e.g. INT-25 Order Control approval) or one of your custom intents (FLOW-n)")
    p.name, p.description, p.products, p.segment = payload.name.strip(), payload.description, payload.products, payload.segment
    p.order_type, p.priority, p.mode = payload.order_type, payload.priority, payload.mode
    p.trigger_intents = ", ".join(codes)
    p.graph_json = json.dumps({"nodes": payload.graph.get("nodes") or [], "edges": payload.graph.get("edges") or []})


# ------------------------------------------------------------------ designer


@admin_router.get("/catalog")
def catalog(db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)) -> dict[str, Any]:
    from app.integration import inventory_adapter, tmf620_adapter
    from app.models.field_service import FsActivityTemplate

    return {
        "layers": [
            {"layer": "COM", "label": "Customer Order Management", "etom": "eTOM 1.1.1 Customer Relationship Management - Order Handling, "
             "Billing & Collections: receives the CRM order, registers customer / billing account / billing profile, routes to SOM, "
             "then starts billing, creates the installed assets and completes the order"},
            {"layer": "SOM", "label": "Service Order Management", "etom": "eTOM 1.1.2 Service Management & Operations - Service Configuration "
             "& Activation: technical actions to the inventory system; collects the logical and physical resource responses; routes to TOM"},
            {"layer": "TOM", "label": "Technical Order Management", "etom": "eTOM 1.1.3 Resource Management & Operations - Resource Provisioning, "
             "Workforce Management: Field Service work orders - installation, connectivity test, acceptance signing"},
        ],
        "functions": fx.catalog(),
        "modes": list(PLAN_MODES),
        "work_order_kinds": list(fx.WORK_ORDER_KINDS),
        "logical_resources": list(inventory_adapter.LOGICAL_RESOURCES),
        "physical_resources": list(inventory_adapter.PHYSICAL_RESOURCES),
        # the tenant's own resource types (Inventory item types) - and whether Inventory allocates them
        "inventory_resources": _inventory_resources(db, tenant_id),
        "operators": list(fulfillment_engine.OPERATORS),
        "condition_fields": ["order.segment", "order.type", "order.monthly_price", "order.contract_months", "order.address",
                             "order.products", "order.technical", "billing.billing_account_id"],
        # products on sale now, one entry each - not every draft / superseded / retired version
        "products": tmf620_adapter.active_offering_names(db, tenant_id),
        "templates": [t.name for t in db.query(FsActivityTemplate).filter(FsActivityTemplate.tenant_id == tenant_id,
                                                                         FsActivityTemplate.active.is_(True)).order_by(FsActivityTemplate.name)],
        # what can start a pattern: vanilla order intents and the tenant's custom intents
        "trigger_intents": fulfillment_ai.trigger_intents(db, tenant_id),
        "default_trigger": fulfillment_engine.APPROVAL_INTENT,
        "ai": _ai_label(db, tenant_id),
    }


def _inventory_resources(db: Session, tenant_id: int) -> list[dict[str, Any]]:
    from app.crm_core import inventory_service

    in_use = inventory_service.in_use(db, tenant_id)
    return [{"type": r, "kept_in_inventory": in_use and inventory_service.managed(db, tenant_id, r)} for r in inventory_service.resource_types(db, tenant_id)]


def _ai_label(db: Session, tenant_id: int) -> str | None:
    from app.crm_core import llm

    target = llm.resolve(db, tenant_id)
    return target.label if target is not None else None


@admin_router.post("/patterns/generate")
def generate_pattern(payload: GenerateIn, db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)) -> dict[str, Any]:
    """A fulfillment pattern drafted from a plain-English description - by the tenant's model (own or
    external, Admin -> AI), else the built-in reader. Nothing is saved: the designer opens it to review."""
    try:
        return fulfillment_ai.generate(db, tenant_id, payload.description,
                                       payload.source_pattern.model_dump() if payload.source_pattern else None)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@admin_router.get("/patterns")
def list_patterns(db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)) -> list[dict[str, Any]]:
    return [_pattern_out(p) for p in db.query(FulfillmentPattern).filter(FulfillmentPattern.tenant_id == tenant_id)
            .order_by(FulfillmentPattern.priority.desc(), FulfillmentPattern.name)]


@admin_router.post("/patterns", status_code=201)
def create_pattern(payload: PatternIn, db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)) -> dict[str, Any]:
    p = FulfillmentPattern(tenant_id=tenant_id)
    _apply(db, tenant_id, p, payload)
    db.add(p)
    db.commit()
    db.refresh(p)
    return _pattern_out(p)


@admin_router.post("/patterns/template", status_code=201)
def create_from_template(payload: dict[str, Any] = Body(default={}), db: Session = Depends(get_db),
                         tenant_id: int = Depends(get_current_tenant_id)) -> dict[str, Any]:
    """The eTOM fixed-broadband pattern, ready to adjust."""
    p = FulfillmentPattern(tenant_id=tenant_id, name=str(payload.get("name") or "Fixed broadband - eTOM standard"),
                   description="COM registers billing and routes to SOM; SOM designs the service, assigns logical and reserves physical "
                               "resources in inventory, activates it and routes to TOM; TOM raises the installation, connectivity test "
                               "and acceptance work orders in Field Service; back in COM billing starts, installed assets are created and "
                               "the order completes.",
                   products=str(payload.get("products") or ""), graph_json=json.dumps(fulfillment_engine.standard_graph()))
    db.add(p)
    db.commit()
    db.refresh(p)
    return _pattern_out(p)


@admin_router.get("/patterns/{pattern_id}")
def get_pattern(pattern_id: int, db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)) -> dict[str, Any]:
    return _pattern_out(_owned(db, tenant_id, pattern_id))


@admin_router.put("/patterns/{pattern_id}")
def update_pattern(pattern_id: int, payload: PatternIn, db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)) -> dict[str, Any]:
    p = _owned(db, tenant_id, pattern_id)
    _apply(db, tenant_id, p, payload)
    if p.is_active:
        errors = fulfillment_engine.validate_graph(fulfillment_engine.load_graph(p), fulfillment_engine.known_resources(db, tenant_id))
        if errors:
            db.rollback()
            raise HTTPException(400, f"An active pattern must stay valid: {'; '.join(errors)} - deactivate it to save a draft")
    p.version += 1  # orders already running keep the version they were planned with
    db.commit()
    db.refresh(p)
    return _pattern_out(p)


@admin_router.post("/patterns/{pattern_id}/activate")
def activate_pattern(pattern_id: int, db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)) -> dict[str, Any]:
    p = _owned(db, tenant_id, pattern_id)
    errors = fulfillment_engine.validate_graph(fulfillment_engine.load_graph(p), fulfillment_engine.known_resources(db, tenant_id))
    if errors:
        raise HTTPException(400, f"Cannot activate an invalid pattern: {'; '.join(errors)}")
    p.is_active = True
    db.commit()
    db.refresh(p)
    return _pattern_out(p)


@admin_router.post("/patterns/{pattern_id}/deactivate")
def deactivate_pattern(pattern_id: int, db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)) -> dict[str, Any]:
    p = _owned(db, tenant_id, pattern_id)
    p.is_active = False
    db.commit()
    db.refresh(p)
    return _pattern_out(p)


@admin_router.delete("/patterns/{pattern_id}", status_code=204)
def delete_pattern(pattern_id: int, db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)) -> None:
    p = _owned(db, tenant_id, pattern_id)
    if db.query(FulfillmentOrder.id).filter(FulfillmentOrder.pattern_id == p.id).first() is not None:
        raise HTTPException(400, "Orders have run on this pattern - deactivate it instead of deleting it")
    db.delete(p)
    db.commit()


@admin_router.post("/patterns/{pattern_id}/preview")
def preview_pattern(pattern_id: int, payload: dict[str, Any] = Body(default={}), db: Session = Depends(get_db),
                    tenant_id: int = Depends(get_current_tenant_id)) -> dict[str, Any]:
    """The plan the pattern makes - for a CRM order when one is given (its conditions applied). Nothing is created."""
    p = _owned(db, tenant_id, pattern_id)
    order = None
    if payload.get("order_id") not in (None, ""):
        order = db.get(Order, int(payload["order_id"]))
        if order is None or order.tenant_id != tenant_id:
            raise HTTPException(404, "Order not found")
    return fulfillment_engine.preview(db, tenant_id, p, order)


# ------------------------------------------------------------------ monitor


@router.get("/orders")
def list_orders(status: str | None = None, db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)) -> list[dict[str, Any]]:
    """Customer orders in Fulfillment (one per CRM order), newest first, with the stage each has reached."""
    q = db.query(FulfillmentOrder).filter(FulfillmentOrder.tenant_id == tenant_id, FulfillmentOrder.layer == "COM")
    rows = [fulfillment_engine.summary(db, o) for o in q.order_by(FulfillmentOrder.id.desc()).limit(200)]
    if status == "fallout":
        return [r for r in rows if r["fallout"]]
    return [r for r in rows if status in (None, "", r["status"])]


@router.get("/orders/{fulfillment_order_id}")
def get_order(fulfillment_order_id: int, db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)) -> dict[str, Any]:
    f_order = db.get(FulfillmentOrder, fulfillment_order_id)
    if f_order is None or f_order.tenant_id != tenant_id:
        raise HTTPException(404, "Orchestration order not found")
    return fulfillment_engine.view(db, fulfillment_engine.top_of(db, f_order))


@router.get("/crm-orders/{order_id}")
def for_crm_order(order_id: int, db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)) -> list[dict[str, Any]]:
    return [fulfillment_engine.view(db, o) for o in db.query(FulfillmentOrder).filter(FulfillmentOrder.tenant_id == tenant_id, FulfillmentOrder.crm_order_id == order_id,
                                                                      FulfillmentOrder.layer == "COM").order_by(FulfillmentOrder.id)]


@router.post("/orders/submit", dependencies=[_admin])
def submit(payload: dict[str, Any] = Body(...), db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)) -> dict[str, Any]:
    """Send a CRM order to Fulfillment by hand (normally Order Control approval does it)."""
    def run() -> dict[str, Any]:
        order = db.get(Order, int(payload["order_id"]))
        if order is None or order.tenant_id != tenant_id:
            raise ValueError("Order not found")
        pattern = _owned(db, tenant_id, int(payload["pattern_id"])) if payload.get("pattern_id") else None
        return fulfillment_engine.view(db, fulfillment_engine.receive(db, tenant_id, order, None, pattern))
    return _do(db, run)


@router.post("/tasks/{task_id}/resolve", dependencies=[_admin])
def resolve(task_id: int, payload: dict[str, Any] = Body(...), db: Session = Depends(get_db),
            tenant_id: int = Depends(get_current_tenant_id)) -> dict[str, Any]:
    """Fallout management: {"action": "retry" | "skip" | "complete", "note"?, "response"?}."""
    response = payload.get("response") if isinstance(payload.get("response"), dict) else None
    return _do(db, lambda: fulfillment_engine.resolve(db, tenant_id, task_id, str(payload.get("action") or ""), str(payload.get("note") or ""), response))


@router.post("/inventory/callback", dependencies=[_admin])
def inventory_callback(payload: dict[str, Any] = Body(...), db: Session = Depends(get_db),
                       tenant_id: int = Depends(get_current_tenant_id)) -> dict[str, Any]:
    """The inventory system answers an asynchronous technical action:
    {"request_id": "INVREQ-...", "status": "completed" | "failed", "resources": [{"type", "identifier", "kind"?, "status"?}], "message"?}"""
    return _do(db, lambda: fulfillment_engine.inventory_callback(db, tenant_id, str(payload.get("request_id") or ""), payload))


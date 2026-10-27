"""Inventory intents (INT-120..128) as saga steps - see intent_engine/inventory_intents.py.
The work is crm_core/inventory_service.py and inventory_ai.py; each step runs inside the
intent's saga, so a refusal rolls the whole intent back and is audited like any other."""

from __future__ import annotations

import re
from typing import Any, Callable

from sqlalchemy.orm import Session

from app.crm_core import inventory_ai, inventory_service as inv
from app.orchestration.orchestrator import SagaStep


def _one(name: str, fn: Callable[[], dict[str, Any]]) -> list[SagaStep]:
    return [SagaStep(name, fn)]


def build_int120_steps(db: Session, tenant_id: int, execution_id: int, e: dict[str, Any]):
    def step() -> dict[str, Any]:
        found = inventory_ai.interpret(db, tenant_id, str(e.get("question") or ""))
        rows = found["results"]
        return {"read_as": found["read_as"], "filter": found["filter"], "answered_by": found["model"] or "built-in reader",
                "count": len(rows), "items": rows[:50],
                "summary": f"{len(rows)} item(s)" + (f" - {found['read_as'][0].lower() + found['read_as'][1:]}" if found["read_as"] else "")}
    return _one("find_inventory", step)


def build_int121_steps(db: Session, tenant_id: int, execution_id: int, e: dict[str, Any]):
    def step() -> dict[str, Any]:
        location = e.get("location")
        if location:
            try:
                location = inv.resolve_item(db, tenant_id, location).identifier
            except ValueError:
                place = inv.serving_location(db, tenant_id, str(location))
                if place is None:
                    raise
                location = place.identifier
        cap = inv.capacity(db, tenant_id, location)
        full = [p["name"] for p in cap["pools"] if p["over_threshold"]] + [r["resource_type"] for r in cap["equipment"] if r["available"] == 0]
        return {**cap, "running_low": full,
                "summary": "; ".join(f"{r['resource_type']}: {r['available']} free of {r['total']}" for r in cap["equipment"])
                           or "No equipment recorded" + (f" at {location}" if location else "")}
    return _one("check_capacity", step)


def build_int122_steps(db: Session, tenant_id: int, execution_id: int, e: dict[str, Any]):
    def step() -> dict[str, Any]:
        t = inv.find_type(db, tenant_id, str(e.get("item_type") or ""))
        if t is None:
            raise ValueError(f"No item type '{e.get('item_type')}' - see Inventory -> Item types")
        try:
            where = inv.resolve_item(db, tenant_id, e.get("location"))
        except ValueError:
            where = inv.serving_location(db, tenant_id, str(e.get("location") or ""))
            if where is None:
                raise
        made = inv.receive_stock(db, tenant_id, t.code, int(e.get("quantity") or 0), where.id, vendor=str(e.get("vendor") or ""),
                                 model=str(e.get("model") or ""), source="intent", reference=f"intent run {execution_id}")
        return {"received": len(made), "item_type": t.name, "location": where.identifier, "identifiers": [i.identifier for i in made][:50]}
    return _one("receive_equipment", step)


def _order_ctx(db: Session, tenant_id: int, order_id: Any, execution_id: int) -> dict[str, Any]:
    from app.models.orders import Order
    from app.orchestration import fulfillment_engine

    order = db.get(Order, int(order_id))
    if order is None or order.tenant_id != tenant_id:
        raise ValueError(f"Order {order_id} not found")
    data = fulfillment_engine.order_data(db, order)["order"]
    return {"order_id": order.id, "party_id": order.party_id, "address": data["address"], "products": data["products"],
            "order_item_id": order.items[0].id if order.items else 0, "reference": f"intent run {execution_id}"}


def build_int123_steps(db: Session, tenant_id: int, execution_id: int, e: dict[str, Any]):
    def step() -> dict[str, Any]:
        resources = e.get("resources") or []
        resources = [r.strip() for r in (resources.split(",") if isinstance(resources, str) else resources) if str(r).strip()]
        if not inv.in_use(db, tenant_id):
            raise ValueError("No inventory is recorded yet - load the sample network or add locations, equipment and pools under Inventory")
        if e.get("opportunity_id") and not e.get("order_id"):
            # Presales: what the deal needs (its product's fulfillment pattern, or what is named) at the site's location
            held = inv.hold_for_opportunity(db, tenant_id, int(e["opportunity_id"]), resources or None, source="intent")
            return {"opportunity_id": held["opportunity_id"], "location": held["location"], "reserved": held["held"],
                    "summary": ", ".join(f"{r['type']} {r['identifier']}" for r in held["held"]) or "nothing more to hold"}
        known = set(inv.resource_types(db, tenant_id))
        unknown = [r for r in resources if r not in known]
        if unknown:
            raise ValueError(f"Not a resource type Inventory keeps: {', '.join(unknown)} - known: {', '.join(sorted(known))}")
        ctx = _order_ctx(db, tenant_id, e["order_id"], execution_id)
        empty = [r for r in resources if not inv.managed(db, tenant_id, r)]
        if empty:
            raise ValueError(f"Inventory holds no {', '.join(empty)} yet - receive some or add a pool first")
        kept = inv.technical_action(db, tenant_id, "reserve", resources, ctx, source="intent")
        return {"order_id": ctx["order_id"], "location": kept["location"], "reserved": kept["resources"],
                "summary": ", ".join(f"{r['type']} {r['identifier']}" for r in kept["resources"])}
    return _one("reserve_inventory", step)


def build_int124_steps(db: Session, tenant_id: int, execution_id: int, e: dict[str, Any]):
    def step() -> dict[str, Any]:
        if e.get("opportunity_id") and not e.get("order_id"):
            released = inv.release_opportunity(db, tenant_id, int(e["opportunity_id"]), source="intent", note=str(e.get("note") or ""))
            if not released:
                raise ValueError(f"Opportunity {e['opportunity_id']} holds nothing in inventory")
            return {"opportunity_id": int(e["opportunity_id"]), "released": released, "count": len(released)}
        released = inv.release_order(db, tenant_id, e["order_id"], source="intent", reference=f"intent run {execution_id}", note=str(e.get("note") or ""))
        if not released:
            raise ValueError(f"Order {e['order_id']} holds nothing in inventory")
        return {"order_id": int(e["order_id"]), "released": released, "count": len(released)}
    return _one("release_inventory", step)


def build_int125_steps(db: Session, tenant_id: int, execution_id: int, e: dict[str, Any]):
    def step() -> dict[str, Any]:
        item = inv.resolve_item(db, tenant_id, e.get("item"))
        note = str(e.get("note") or "")
        if e.get("lifecycle"):
            inv.set_lifecycle(db, tenant_id, item, str(e["lifecycle"]), source="intent", note=note, reference=f"intent run {execution_id}")
        if e.get("oper_status"):
            inv.set_oper_status(db, tenant_id, item, str(e["oper_status"]), source="intent", note=note)
        if not (e.get("lifecycle") or e.get("oper_status")):
            raise ValueError("Say what to change: a lifecycle (maintenance, available, retired ...) or an operational status (up, down, degraded)")
        hit = inv.impact(db, tenant_id, item) if item.oper_status == "down" or item.lifecycle == "maintenance" else None
        return {"item": item.identifier, "item_id": item.id, "lifecycle": item.lifecycle, "oper_status": item.oper_status,
                **({"services_affected": len(hit["services"]), "customers_affected": len(hit["customers"])} if hit else {})}
    return _one("change_item_status", step)


def build_int126_steps(db: Session, tenant_id: int, execution_id: int, e: dict[str, Any]):
    def step() -> dict[str, Any]:
        hit = inv.impact(db, tenant_id, e.get("item"))
        return {"item": hit["item"]["identifier"], "summary": hit["summary"], "affected_items": hit["affected_items"],
                "services": [s["identifier"] for s in hit["services"]], "customers": [c["name"] for c in hit["customers"]],
                "orders": hit["orders"], "protected": hit["protected"]}
    return _one("analyse_impact", step)


def build_int127_steps(db: Session, tenant_id: int, execution_id: int, e: dict[str, Any]):
    def step() -> dict[str, Any]:
        raw = str(e.get("range") or "").strip()
        data: dict[str, Any] = {"type_code": e.get("pool_type_code"), "location": e.get("location") or None, "name": e.get("pool_name") or ""}
        if "/" in raw:
            data.update(pool_type="ip", range_start=raw, block_size=int(e.get("block_size") or 0))
        else:
            m = re.match(r"^\s*(\d+)\s*(?:-|–|to)\s*(\d+)\s*$", raw)
            if not m:
                raise ValueError("Give the range as start-end (e.g. 1000-1999) or a network (e.g. 10.30.0.0/16)")
            data.update(range_start=m.group(1), range_end=m.group(2))
        pool = inv.pool_out(db, inv.save_pool(db, tenant_id, data))
        return {"pool": pool["name"], "pool_id": pool["id"], "capacity": pool["capacity"], "location": pool["location"] or "shared"}
    return _one("create_pool", step)


def build_int128_steps(db: Session, tenant_id: int, execution_id: int, e: dict[str, Any]):
    def step() -> dict[str, Any]:
        drafted = inventory_ai.draft_type(db, tenant_id, str(e.get("type_description") or ""))
        spec = drafted["type"]
        if not spec["code"]:
            raise ValueError("Couldn't tell what the item type is called - start with its name, e.g. 'A DSLAM is ...'")
        t = inv.save_type(db, tenant_id, spec)
        return {"item_type": t.name, "code": t.code, "kind": t.kind, "attributes": [a["label"] for a in inv.attributes(db, t)],
                "contained_in": [c for c in t.contained_in.split(",") if c], "drafted_by": drafted["model"] or "built-in reader",
                "next": "Review it under Inventory -> Item types"}
    return _one("design_item_type", step)


BUILDERS = {"INT-120": build_int120_steps, "INT-121": build_int121_steps, "INT-122": build_int122_steps, "INT-123": build_int123_steps,
            "INT-124": build_int124_steps, "INT-125": build_int125_steps, "INT-126": build_int126_steps, "INT-127": build_int127_steps,
            "INT-128": build_int128_steps}

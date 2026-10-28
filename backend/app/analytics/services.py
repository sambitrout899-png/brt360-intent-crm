"""What analytics counts as a live service, now that orders complete.

Since asset-based ordering, a fulfilled order ends `completed` for good and
its service lives on as an asset: suspend, cancel, terminate and every change
act on the asset through change orders, which complete too. So an order's
own status no longer says whether its service is live - the asset does.

Before analytics computes anything on order rows (engine.read), each
completed *installing* order takes its service's state instead:
- status: active / suspended while the service is, cancelled / terminated
  once it ended (by a disconnect, a port-out or a termination)
- monthly_price: what the service bills today (or billed when it ended)
- updated_at: when it ended, for churn timing
Completed *change* orders keep `completed`: they changed a service that its
installing order already counts - counting them too would double it.
Orders from before asset-based ordering keep their own status.
"""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy.orm import Session

ENDED = {"disconnect": "cancelled", "port_out": "cancelled", "terminate": "terminated"}
# the order fields whose meaning follows the service
_FOLLOWS = ("status", "monthly_price", "updated_at", "mrr", "is_live")


def _prefix(entity: str) -> str | None:
    return {"order": "", "order_item": "order."}.get(entity)


def required(entity: str, keys: list[str]) -> list[str]:
    """The keys to read as well, so `apply` can find each row's order."""
    p = _prefix(entity)
    if p is None or not any(k in (f"{p}{f}" if f not in ("mrr", "is_live") else f) for k in keys for f in _FOLLOWS):
        return keys
    return [*keys, *(k for k in (f"{p}id", f"{p}order_type", f"{p}status") if k not in keys)]


def _current(db: Session, asset):
    """The asset that carries the service today: a product change or a move replaces it."""
    from app.models.fulfillment import Asset

    seen = set()
    while asset.superseded_by_asset_id and asset.id not in seen:
        seen.add(asset.id)
        nxt = db.get(Asset, asset.superseded_by_asset_id)
        if nxt is None:
            break
        asset = nxt
    return asset


def _monthly(asset) -> float:
    data = json.loads(asset.instance_json or "{}")
    return round(float(data.get("net_unit_price") or 0) * float(data.get("quantity") or 1), 2)


def service_view(db: Session, tenant_id: int, order_ids: set[int]) -> dict[int, dict[str, Any]]:
    """For completed installing orders: {order id: status, monthly_price, ended_at} of their service."""
    from app.models.abo import AssetChange
    from app.models.fulfillment import Asset, AssetStatus
    from app.models.orders import Order, OrderItem, OrderType

    if not order_ids:
        return {}
    orders = db.query(Order).filter(Order.tenant_id == tenant_id, Order.id.in_(order_ids), Order.order_type == OrderType.new).all()
    items: dict[int, list[int]] = {}
    for item in db.query(OrderItem).filter(OrderItem.order_id.in_([o.id for o in orders] or [-1])):
        items.setdefault(item.order_id, []).append(item.id)
    out: dict[int, dict[str, Any]] = {}
    for order in orders:
        assets = db.query(Asset).filter(Asset.tenant_id == tenant_id, Asset.order_item_id.in_(items.get(order.id) or [-1])).all()
        services = [_current(db, a) for a in assets]
        if not services:
            continue  # nothing installed on record: the order's own status stands
        live = [a for a in services if a.status != AssetStatus.decommissioned]
        if live:
            status = "active" if any(a.status == AssetStatus.active for a in live) else "suspended"
            out[order.id] = {"status": status, "monthly_price": sum(_monthly(a) for a in live), "ended_at": None}
            continue
        ending = (db.query(AssetChange).filter(AssetChange.tenant_id == tenant_id, AssetChange.asset_id.in_([a.id for a in services]),
                                               AssetChange.operation.in_(list(ENDED)), AssetChange.status == "completed")
                  .order_by(AssetChange.completed_at.desc()).first())
        out[order.id] = {"status": ENDED.get(ending.operation, "cancelled") if ending else "cancelled",
                         "monthly_price": sum(_monthly(a) for a in services),
                         "ended_at": (ending.completed_at if ending else None) or order.updated_at}
    return out


def apply(db: Session, tenant_id: int, entity: str, rows: list[dict[str, Any]]) -> None:
    """Completed installing orders' rows take their service's state (in place)."""
    p = _prefix(entity)
    if p is None or not rows or f"{p}status" not in rows[0]:
        return
    ids = {r[f"{p}id"] for r in rows if r.get(f"{p}status") == "completed" and r.get(f"{p}id") is not None}
    view = service_view(db, tenant_id, ids)
    for r in rows:
        service = view.get(r.get(f"{p}id"))
        if service is None:
            continue
        r[f"{p}status"] = service["status"]
        if f"{p}monthly_price" in r:
            r[f"{p}monthly_price"] = service["monthly_price"]
        if f"{p}updated_at" in r and service["ended_at"] is not None:
            r[f"{p}updated_at"] = service["ended_at"]

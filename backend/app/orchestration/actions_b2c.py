"""INT-104 Place a B2C order and INT-105 Log an order interaction
(crm_core/b2c_order_service.py does the work)."""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.crm_core import b2c_order_service as b2c
from app.data_intelligence import event_bus
from app.orchestration.orchestrator import SagaStep


def build_int104_steps(db: Session, tenant_id: int, execution_id: int, entities: dict[str, Any]) -> list[SagaStep]:
    def step() -> dict[str, Any]:
        out = b2c.place_order(db, tenant_id, entities, execution_id)
        event_bus.publish(db, execution_id, "b2c_order.placed",
                          {k: out[k] for k in ("order_id", "party_id", "channel", "feasibility", "om_status") if k in out})
        return out

    return [SagaStep("place_b2c_order", step)]


def build_int105_steps(db: Session, tenant_id: int, execution_id: int, entities: dict[str, Any]) -> list[SagaStep]:
    def step() -> dict[str, Any]:
        out = b2c.log_interaction(db, tenant_id, entities)
        event_bus.publish(db, execution_id, "b2c_order.interaction", {k: out.get(k) for k in ("order_id", "party_id", "channel", "kind")})
        return out

    return [SagaStep("log_interaction", step)]


from app.orchestration.actions_offering_order import build_int106_steps

BUILDERS = {"INT-104": build_int104_steps, "INT-105": build_int105_steps, "INT-106": build_int106_steps}

def build_int107_steps(db, tenant_id, execution_id, entities):
    from app.crm_core import abo_service
    asset_id = int(entities["asset_id"]) if entities.get("asset_id") not in (None, "") else None  # or the order that installed it
    return [SagaStep("submit_asset_change", lambda: abo_service.submit(db, tenant_id, asset_id, entities["asset_operation"], entities))]


def build_int108_steps(db, tenant_id, execution_id, entities):
    def complete():
        from app.crm_core import abo_service
        from app.models.orders import Order, OrderStatus
        if entities.get("_actor_role") != "admin":
            raise ValueError("Only an administrator can confirm orchestration fulfillment")
        order = db.get(Order, int(entities["order_id"]))
        if not order or order.tenant_id != tenant_id:
            raise ValueError("Order not found")
        if order.status not in (OrderStatus.activating, OrderStatus.completed):
            raise ValueError("Only an order submitted for fulfillment can be completed")
        abo_service.complete(db, order)
        from app.models.abo import AssetRevision
        ids = [r.asset_id for r in db.query(AssetRevision).filter(AssetRevision.tenant_id == tenant_id, AssetRevision.order_item_id.in_([i.id for i in order.items]))]
        return {"order_id": order.id, "status": order.status.value, "asset_ids": ids}
    return [SagaStep("complete_order_and_assetize", complete)]

BUILDERS.update({"INT-107": build_int107_steps, "INT-108": build_int108_steps})


# Siebel asset-based ordering around a change order (INT-110..115) - abo_service does the work


def _change_order(db, tenant_id, entities):
    """The change order named - or the asset's change still to be fulfilled."""
    from app.crm_core import abo_service
    from app.models.orders import Order
    if entities.get("order_id") not in (None, ""):
        order = db.get(Order, int(entities["order_id"]))
        if order is None or order.tenant_id != tenant_id:
            raise ValueError("Order not found")
        return order
    change = abo_service.open_change(db, int(entities["asset_id"]))
    if change is None or change.tenant_id != tenant_id:
        raise ValueError(f"Asset {entities['asset_id']} has no change order still to be fulfilled")
    return db.get(Order, change.order_id)


def _asset_id(entities):
    return int(entities["asset_id"]) if entities.get("asset_id") not in (None, "") else None


def build_int110_steps(db, tenant_id, execution_id, entities):
    from app.crm_core import abo_service
    return [SagaStep("cancel_asset_change", lambda: abo_service.cancel(db, tenant_id, _change_order(db, tenant_id, entities),
                                                                       entities.get("reason")))]


def build_int111_steps(db, tenant_id, execution_id, entities):
    from app.crm_core import abo_service
    return [SagaStep("revise_asset_change", lambda: abo_service.revise(db, tenant_id, _change_order(db, tenant_id, entities),
                                                                       entities, entities.get("reason")))]


def build_int112_steps(db, tenant_id, execution_id, entities):
    from app.crm_core import abo_service
    return [SagaStep("bulk_asset_change", lambda: abo_service.bulk(
        db, tenant_id, entities["asset_operation"], {k: v for k, v in entities.items() if k not in ("asset_ids", "party_id", "current_offering")},
        asset_ids=entities.get("asset_ids"), party_id=entities.get("party_id"), current_offering=entities.get("current_offering")))]


def build_int113_steps(db, tenant_id, execution_id, entities):
    from app.crm_core import abo_service
    return [SagaStep("asset_history", lambda: abo_service.history(db, tenant_id, int(entities["asset_id"]), entities.get("as_of")))]


def build_int114_steps(db, tenant_id, execution_id, entities):
    from app.crm_core import abo_service
    return [SagaStep("quote_asset_change", lambda: abo_service.quote(db, tenant_id, _asset_id(entities), entities["asset_operation"], entities))]


def build_int115_steps(db, tenant_id, execution_id, entities):
    from app.crm_core import abo_service
    return [SagaStep("order_change_quote", lambda: abo_service.order_quote(db, tenant_id, entities["quote_number"]))]


BUILDERS.update({"INT-110": build_int110_steps, "INT-111": build_int111_steps, "INT-112": build_int112_steps,
                 "INT-113": build_int113_steps, "INT-114": build_int114_steps, "INT-115": build_int115_steps})

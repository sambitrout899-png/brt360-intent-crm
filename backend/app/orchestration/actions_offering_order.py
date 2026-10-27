"""Quote-free ordering with an explicit interactive handoff."""
from __future__ import annotations

import math
from typing import Any

from sqlalchemy.orm import Session

from app.crm_core import product_config_service as config
from app.integration import tmf620_adapter
from app.models.crm import Party
from app.models.orders import Order
from app.orchestration.actions_o2a import build_int07_steps
from app.orchestration.orchestrator import SagaStep


def build_int106_steps(db: Session, tenant_id: int, execution_id: int, entities: dict[str, Any]) -> list[SagaStep]:
    def execute() -> dict[str, Any]:
        mode = entities.get("order_mode") or "interactive"
        if mode not in ("silent", "interactive"):
            raise ValueError("Order mode must be silent or interactive")
        identifier = str(entities.get("party_id") or "")
        query = db.query(Party).filter(Party.tenant_id == tenant_id)
        party = query.filter(Party.id == int(identifier)).first() if identifier.isdigit() else query.filter(Party.brt_id == identifier).first()
        if party is None:
            raise ValueError("Choose a customer belonging to this workspace")
        offering = tmf620_adapter.get_active_offering_by_name(db, tenant_id, entities.get("offering_name") or "")
        if offering is None:
            raise ValueError("Choose an active offering by its exact name")
        quantity_raw = entities.get("quantity", 1)
        try:
            quantity = int(str(quantity_raw))
        except (TypeError, ValueError):
            raise ValueError("Quantity must be a positive whole number")
        if quantity < 1:
            raise ValueError("Quantity must be a positive whole number")
        values = {"party_id": party.id, "offering_name": offering.name, "quantity": quantity, "order_mode": mode}
        if entities.get("service_account_id"):
            values["service_account_id"] = entities["service_account_id"]
        if mode == "interactive" and entities.get("submit_for_provisioning") is not True:
            values.update({k: entities[k] for k in ("pricing_attributes", "technical_attributes", "requested_discount_pct") if k in entities})
            return {"party_id": party.id, "offering_name": offering.name, "status": "awaiting_configuration",
                    "configuration_request": {"intent": "INT-106", "entities": values},
                    "message": "Configure the order line, then explicitly submit for provisioning. No order has been created."}
        discount = 0.0 if mode == "silent" else float(entities.get("requested_discount_pct") or 0)
        if not math.isfinite(discount) or not 0 <= discount <= 100:
            raise ValueError("Order line discount must be between 0 and 100 percent")
        price_list = config.price_list_for(db, tenant_id, party)
        base, _ = config.base_price(db, tenant_id, price_list, offering, entities.get("quantity") or 1)
        configured = config.configure(db, tenant_id, offering.name,
                                      {} if mode == "silent" else entities.get("pricing_attributes"),
                                      {} if mode == "silent" else entities.get("technical_attributes"),
                                      base, require_technical=True, party=party, price_list=price_list)
        values.update(pricing_attributes=configured["pricing"], technical_attributes=configured["technical"],
                      requested_discount_pct=discount)
        values.update({k: entities[k] for k in ("_actor_role", "_actor_user_id") if k in entities})
        out: dict[str, Any] = {}
        for step in build_int07_steps(db, tenant_id, execution_id, values):
            out.update(step.action() or {})
        order = db.get(Order, out["order_id"])
        item = order.items[0]
        item.unit_price = configured["configured"]
        item.discount_pct = discount
        item.net_unit_price = round(order.monthly_price / quantity, 2)
        db.flush()
        out.update(order_item_id=item.id, product_offering_id=offering.id, offering_name=offering.name,
                   quantity=quantity, unit_price=item.unit_price, discount_pct=discount, net_unit_price=item.net_unit_price)
        from app.models.fulfillment import Asset
        asset = db.query(Asset).filter_by(tenant_id=tenant_id, order_item_id=item.id).first()
        if asset:
            out["asset_id"] = asset.id
        return out

    return [SagaStep("order_offering", execute)]

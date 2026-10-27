"""Configurator intents.

INT-47  Configure with AI           - a customer's need in plain words -> the engine's valid, completed
                                      configuration (lowest cost within a budget) saved on the opportunity
INT-48  Release a product model     - tests, QA and regression gate, then an effective-dated version
INT-49  Check what a customer can buy - product offering qualification (eligibility, what they own)
INT-50  Change a live service's configuration - asset-based ordering: the change is checked against
                                      the product's rules and applied as a delta (add / update / delete).
                                      On a completed order it changes the installed asset through a change
                                      order; the order that installed it stays as it is
"""

from __future__ import annotations

import datetime as dt
import json
from typing import Any

from sqlalchemy.orm import Session

from app.crm_core import configurator as cfg
from app.crm_core import model_lifecycle as lc
from app.crm_core import product_config_service as pc
from app.crm_core import product_model_service as pm
from app.data_intelligence import event_bus
from app.models.catalog import ProductOffering
from app.models.crm import Opportunity, Party
from app.models.orders import Order, OrderStatus
from app.orchestration.orchestrator import SagaStep


def build_int47_steps(db: Session, tenant_id: int, execution_id: int, entities: dict[str, Any]) -> list[SagaStep]:
    from app.orchestration import actions_sales

    state: dict[str, Any] = {}

    def propose() -> dict[str, Any]:
        opportunity = db.get(Opportunity, entities.get("opportunity_id") or 0)
        if opportunity is None or opportunity.tenant_id != tenant_id:
            raise ValueError("Pick the opportunity to configure")
        from app.crm_core import process_service

        if process_service.is_presales_only(db, tenant_id, actions_sales.actor(entities)):
            raise ValueError("Presales sees the pricing attributes but doesn't change them - the Account Manager configures the product")
        name = entities.get("offering_name") or opportunity.offering_name or (opportunity.lead.interested_offering_name if opportunity.lead else None)
        offering = pm.offering_of(db, tenant_id, name or "")
        if offering is None:
            raise ValueError("Say which product to configure")
        party = opportunity.lead.party if opportunity.lead is not None else None
        price_list = pc.price_list_for(db, tenant_id, party, opportunity)
        base, _ = pc.base_price(db, tenant_id, price_list, offering)
        design = pc.schema(db, tenant_id, offering.name)
        need = str(entities.get("need") or "")
        if entities.get("max_monthly"):
            need += f" budget {entities['max_monthly']}"
        current = json.loads(opportunity.config.pricing_attributes_json or "{}") if opportunity.config and \
            (opportunity.offering_name or "").lower() == offering.name.lower() else {}
        result = cfg.ai_configure(db, tenant_id, design, base, need, current)
        if not result["valid"]:
            raise ValueError(result["conflict"]["message"] if result["conflict"] else "; ".join(result["problems"]) or "No valid configuration")
        state.update(offering=offering.name, instance=result["instance"])
        return {"proposal": result["proposal"]["picks"], "configuration": result["instance"], "monthly_price": result["price"]["monthly"],
                "one_time_price": result["price"]["one_time"], "dropped": result["dropped"], "engine": result["proposal"]["engine"],
                "missing": result["missing"]}

    steps = [SagaStep("ai_propose_and_solve", propose)]

    def save() -> dict[str, Any]:
        inner = actions_sales.build_int37_steps(db, tenant_id, execution_id, {
            "opportunity_id": entities.get("opportunity_id"), "offering_name": state["offering"],
            "pricing_attributes": state["instance"], "_actor_user_id": entities.get("_actor_user_id")})
        out: dict[str, Any] = {}
        for step in inner:
            out.update(step.action() or {})
        return out

    return steps + [SagaStep("save_configuration", save)]


def build_int48_steps(db: Session, tenant_id: int, execution_id: int, entities: dict[str, Any]) -> list[SagaStep]:
    def step() -> dict[str, Any]:
        offering = pm.offering_of(db, tenant_id, str(entities.get("offering_name") or ""))
        if offering is None:
            raise ValueError("Pick the product whose model to release")
        valid_from = None
        if entities.get("valid_from"):
            try:
                valid_from = dt.datetime.fromisoformat(str(entities["valid_from"])[:19])
            except ValueError:
                raise ValueError("valid from must be a date, e.g. 2026-11-01")
        row = lc.release(db, tenant_id, offering.name, valid_from, str(entities.get("notes") or ""))
        report = json.loads(row.report_json or "{}")
        event_bus.publish(db, execution_id, "product_model.released", {"offering_name": offering.name, "version": row.version})
        return {"offering_name": offering.name, "model_version": row.version, "valid_from": row.valid_from.isoformat(),
                "tests_passed": f"{report.get('tests_passed', 0)}/{report.get('tests', 0)}", "warnings": report.get("warnings", [])}

    return [SagaStep("test_and_release_model", step)]


def build_int49_steps(db: Session, tenant_id: int, execution_id: int, entities: dict[str, Any]) -> list[SagaStep]:
    def step() -> dict[str, Any]:
        party = db.get(Party, entities.get("party_id") or 0)
        if party is None or party.tenant_id != tenant_id:
            raise ValueError("Pick the customer")
        names = [entities["offering_name"]] if entities.get("offering_name") else \
            [p["name"] for p in pm.products(db, tenant_id) if p["status"] == "active"]
        eligible, why = pm.eligible(db, tenant_id, names, party)
        compat = {n: errs for n in eligible if (errs := pm.compatibility_errors(db, tenant_id, n, party, []))}
        return {"party_id": party.id, "eligible": [n for n in eligible if n not in compat], "not_eligible": why,
                "needs_first": {n: "; ".join(e) for n, e in compat.items()}, "owns": pm.owned_products(db, tenant_id, party)}

    return [SagaStep("qualify_products", step)]


def build_int50_steps(db: Session, tenant_id: int, execution_id: int, entities: dict[str, Any]) -> list[SagaStep]:
    from app.crm_core import abo_service
    from app.integration import inventory_adapter, order_adapter
    from app.models.fulfillment import Asset
    from app.models.product_config import OrderItemConfig

    state: dict[str, Any] = {}

    def check_installed(order: Order) -> dict[str, Any]:
        """A completed order is read-only: the service it installed lives on as
        an asset, and the change is a change order from that asset."""
        if not order.items:
            raise ValueError(f"Order {order.id} has no product to reconfigure")
        offering = db.get(ProductOffering, order.items[0].product_offering_id)
        changes = pc.selection(db, tenant_id, offering.name, entities.get("pricing_attributes"))
        asset = abo_service.asset_of_order(db, tenant_id, order.id)
        # today's released model decides what the service may become (raises on a broken rule) ...
        installed = (json.loads(asset.instance_json or "{}").get("configuration") or {}).get("pricing") or {}
        pc.configure(db, tenant_id, offering.name, {**installed, **changes}, {}, 0.0)
        # ... and the change order from the asset is worked out (and checked) without placing it
        check = abo_service.preview(db, tenant_id, asset.id, "modify", {"pricing_attributes": changes})
        delta = check.get("delta") or {}
        if not delta.get("attributes") and all(p["action"] == "-" for p in delta.get("components", [])):
            raise ValueError("Nothing changes - say what to change, e.g. Bandwidth: 4 Mbps, CPE: Yes")
        state.update(order=order, asset_id=asset.id, changes=changes, before_technical=provisioning_view(asset))
        return {"order_id": order.id, "asset_id": asset.id, "delta": delta, "monthly_change": check.get("monthly_change")}

    def provisioning_view(asset: Asset) -> dict[str, Any]:
        """What the network sees of a service: its technical settings plus the
        options that affect provisioning (as the order-based change sends them)."""
        data = json.loads(asset.instance_json or "{}")
        configuration = data.get("configuration") or {}
        design = data.get("definition") or pc.schema(db, tenant_id, data.get("offering_name"))
        view = dict(configuration.get("technical") or {})
        for name, value in (configuration.get("pricing") or {}).items():
            attr = pc._find(design.get("attributes", []), name)
            comp = next((c for c in design.get("components", []) if pc._key(c["name"]) == pc._key(name)), None)
            if (attr is not None and attr.get("affects_provisioning")) or (comp is not None and comp.get("affects_provisioning")):
                view[name] = value
        return view

    def apply_installed() -> dict[str, Any]:
        order = state["order"]
        change = abo_service.live_change(db, tenant_id, order, "modify", {"pricing_attributes": state["changes"]})
        asset = db.get(Asset, change["asset_id"])
        technical = provisioning_view(asset)
        provisioning = [line for line in cfg.delta(state["before_technical"], technical) if line["action"] != "-"]
        inventory_adapter.sync_asset_to_inventory(asset.asset_type, asset.identifier, technical)
        price_change = change["monthly_change"]
        if price_change:
            account = order_adapter.get_or_create_billing_account(db, tenant_id, order.party_id)
            order_adapter.apply_billing_adjustment(db, account.id, change["order_id"], price_change, "Configuration change proration")
        event_bus.publish(db, execution_id, "order.reconfigured", {"order_id": order.id, "asset_id": asset.id, "change_order_id": change["order_id"],
                                                                   "delta": change.get("delta"), "provisioning_delta": provisioning})
        return {"order_id": order.id, "asset_id": asset.id, "change_order_id": change["order_id"], "change_order_status": change["status"],
                "new_monthly_price": change["new_monthly"], "price_change": price_change, "status": OrderStatus.changed.value,
                "provisioning_delta": provisioning}

    def load_and_check() -> dict[str, Any]:
        order = db.get(Order, entities.get("order_id") or 0)
        if order is None or order.tenant_id != tenant_id:
            raise ValueError("Pick the live service (order) to change")
        if abo_service.installed(order):
            return check_installed(order)
        if order.status not in (OrderStatus.active, OrderStatus.changed):
            raise ValueError(f"Order {order.id} is {order.status.value} - only a live service can be reconfigured")
        if not order.items:
            raise ValueError(f"Order {order.id} has no product to reconfigure")
        item = order.items[0]
        offering = db.get(ProductOffering, item.product_offering_id)
        row = db.query(OrderItemConfig).filter(OrderItemConfig.order_item_id == item.id).first()
        before_pricing = pc.loads(row.pricing_attributes_json) if row else {}
        before_technical = pc.loads(row.technical_attributes_json) if row else {}
        changes = pc.selection(db, tenant_id, offering.name, entities.get("pricing_attributes"))
        after = {**before_pricing, **changes}
        for k, v in (entities.get("pricing_attributes") or {}).items() if isinstance(entities.get("pricing_attributes"), dict) else []:
            if v in (None, ""):
                after = {a: b for a, b in after.items() if a.lower() != str(k).lower()}
        party = db.get(Party, order.party_id) if order.party_id else None
        price_list = pc.price_list_for(db, tenant_id, party)
        base, _ = pc.base_price(db, tenant_id, price_list, offering)
        configured = pc.configure(db, tenant_id, offering.name, after, {}, base, price_list=price_list)  # today's released model decides
        lines = cfg.delta(before_pricing, configured["pricing"])
        if not any(line["action"] != "-" for line in lines):
            raise ValueError("Nothing changes - say what to change, e.g. Bandwidth: 4 Mbps, CPE: Yes")
        state.update(order=order, item=item, offering=offering, row=row, configured=configured, before_technical=before_technical, lines=lines)
        return {"order_id": order.id, "delta": lines, "model_version": configured["model_version"]}

    def apply() -> dict[str, Any]:
        if "asset_id" in state:
            return apply_installed()
        order, item, configured = state["order"], state["item"], state["configured"]
        new_monthly = round(configured["configured"] * (item.quantity or 1), 2)
        delta_amount = round(new_monthly - (order.monthly_price or 0), 2)
        design = pc.schema(db, tenant_id, state["offering"].name)
        technical = dict(state["before_technical"])
        for name, value in configured["pricing"].items():
            attr = pc._find(design["attributes"], name)
            comp = next((c for c in design.get("components", []) if pc._key(c["name"]) == pc._key(name)), None)
            if (attr is not None and attr.get("affects_provisioning")) or (comp is not None and comp["affects_provisioning"]):
                technical[name] = value
        row = state["row"]
        if row is None:
            row = OrderItemConfig(order_item_id=item.id)
            db.add(row)
        row.pricing_attributes_json = json.dumps(configured["pricing"])
        row.technical_attributes_json = json.dumps(technical)
        order.monthly_price = new_monthly
        order.status = OrderStatus.changed
        db.flush()
        provisioning = [line for line in cfg.delta(state["before_technical"], technical) if line["action"] != "-"]
        for asset in db.query(Asset).filter(Asset.order_item_id == item.id):
            asset.attributes = json.dumps(technical)
            inventory_adapter.sync_asset_to_inventory(asset.asset_type, asset.identifier, technical)
        if delta_amount:
            account = order_adapter.get_or_create_billing_account(db, tenant_id, order.party_id)
            order_adapter.apply_billing_adjustment(db, account.id, order.id, delta_amount, "Configuration change proration")
        event_bus.publish(db, execution_id, "order.reconfigured", {"order_id": order.id, "delta": state["lines"], "provisioning_delta": provisioning})
        return {"order_id": order.id, "new_monthly_price": new_monthly, "price_change": delta_amount, "status": order.status.value,
                "provisioning_delta": provisioning}

    return [SagaStep("check_change_against_model", load_and_check), SagaStep("apply_delta", apply)]

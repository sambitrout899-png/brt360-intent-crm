"""Product instances (models/product_model.py ProductInstance) - the catalog
definition frozen into an opportunity, quote or order.

  catalog definition (class -> product)          never touched by selling
        │ snapshot when the product is picked
        ▼
  opportunity instance ──copy──> quote instance ──copy──> revision / order instance
        configured in context: picks, quantities, price, rules

Everything that configures, prices or checks a product in a record's
context asks design_for() for the instance's frozen definition. Records
made before instances existed get one taken from the catalog the first
time they are configured.
"""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy.orm import Session

from app.crm_core import product_config_service as pc
from app.models.product_model import ProductInstance
from app.timeutils import utcnow

CONTEXTS = ("opportunity", "quote", "order")


def live_definition(db: Session, tenant_id: int, offering_name: str) -> tuple[dict[str, Any], int | None]:
    """What the catalog says the product is right now (the released model
    valid today, else the workspace) - with its class inheritance resolved."""
    design = pc.schema(db, tenant_id, offering_name)
    return design, design.get("version")


def get(db: Session, tenant_id: int, context_type: str, context_id: int | None, offering_name: str | None = None) -> ProductInstance | None:
    if not context_id:
        return None
    q = db.query(ProductInstance).filter(ProductInstance.tenant_id == tenant_id, ProductInstance.context_type == context_type,
                                         ProductInstance.context_id == context_id)
    if offering_name:
        q = q.filter(ProductInstance.offering_name.ilike(offering_name))
    return q.order_by(ProductInstance.id.desc()).first()


def _offering_id(db: Session, tenant_id: int, name: str) -> int | None:
    from app.crm_core import product_model_service as pm

    o = pm.offering_of(db, tenant_id, name)
    return o.id if o else None


def take(db: Session, tenant_id: int, context_type: str, context_id: int, offering_name: str, source: ProductInstance | None = None,
         item_id: int | None = None) -> ProductInstance:
    """A new instance: a copy of `source` (same product), else the live catalog definition."""
    if context_type not in CONTEXTS:
        raise ValueError(f"Instances live on {', '.join(CONTEXTS)}")
    if source is not None and pc._key(source.offering_name) == pc._key(offering_name):
        definition, version, configuration = source.definition_json, source.model_version, source.configuration_json
    else:
        design, version = live_definition(db, tenant_id, offering_name)
        definition, configuration, source = json.dumps(design, default=str), "{}", None
    inst = ProductInstance(tenant_id=tenant_id, context_type=context_type, context_id=context_id, item_id=item_id, offering_name=offering_name,
                           offering_id=_offering_id(db, tenant_id, offering_name), model_version=version, definition_json=definition,
                           configuration_json=configuration, source_instance_id=source.id if source else None, taken_at=utcnow())
    db.add(inst)
    db.flush()
    return inst


def ensure(db: Session, tenant_id: int, context_type: str, context_id: int, offering_name: str, source: ProductInstance | None = None,
           item_id: int | None = None) -> ProductInstance:
    """This record's instance of the product - taken now if it has none."""
    found = get(db, tenant_id, context_type, context_id, offering_name)
    if found is not None:
        if item_id and not found.item_id:
            found.item_id = item_id
        return found
    return take(db, tenant_id, context_type, context_id, offering_name, source, item_id)


def design(inst: ProductInstance | None) -> dict[str, Any] | None:
    if inst is None:
        return None
    d = pc.loads(inst.definition_json)
    d.setdefault("offering_name", inst.offering_name)
    return d


def design_for(db: Session, tenant_id: int, context_type: str, context_id: int | None, offering_name: str) -> dict[str, Any] | None:
    """The frozen definition this record works on (None when it has no instance yet)."""
    return design(get(db, tenant_id, context_type, context_id, offering_name))


def record(inst: ProductInstance, configured: dict[str, Any], quantity: int | None = None) -> None:
    """What was configured on the instance - picks and price."""
    conf = pc.loads(inst.configuration_json)
    conf.update({"pricing": configured.get("pricing", {}), "technical": configured.get("technical", conf.get("technical", {}))})
    if quantity is not None:
        conf["quantity"] = quantity
    inst.configuration_json = json.dumps(conf, default=str)
    inst.price_json = json.dumps({k: configured.get(k) for k in ("base", "configured", "one_time", "lines", "messages")}, default=str)
    inst.updated_at = utcnow()


def set_technical(inst: ProductInstance, technical: dict[str, Any]) -> None:
    conf = pc.loads(inst.configuration_json)
    conf["technical"] = {**conf.get("technical", {}), **technical}
    inst.configuration_json = json.dumps(conf, default=str)


def stale(db: Session, tenant_id: int, inst: ProductInstance) -> bool:
    """Has the catalog definition moved on since this copy was taken?"""
    from app.crm_core import product_hub

    live, _ = live_definition(db, tenant_id, inst.offering_name)
    keys = ("attributes", "components", "rules", "constraints")
    frozen = pc.loads(inst.definition_json)
    return json.dumps(product_hub._normal({k: frozen.get(k) for k in keys}), sort_keys=True, default=str) != \
        json.dumps(product_hub._normal({k: live.get(k) for k in keys}), sort_keys=True, default=str) \
        or product_hub._normal(frozen.get("price_rules") or []) != product_hub._normal(live.get("price_rules") or [])


def refresh(db: Session, tenant_id: int, inst: ProductInstance) -> ProductInstance:
    """Take the current catalog definition into this instance (an explicit
    upgrade); the picks made so far are kept and re-checked by the caller."""
    live, version = live_definition(db, tenant_id, inst.offering_name)
    inst.definition_json = json.dumps(live, default=str)
    inst.model_version = version
    inst.taken_at = utcnow()
    db.flush()
    return inst


def describe(db: Session, tenant_id: int, inst: ProductInstance | None) -> dict[str, Any] | None:
    if inst is None:
        return None
    d = pc.loads(inst.definition_json)
    return {
        "id": inst.id, "brt_id": inst.brt_id, "context_type": inst.context_type, "context_id": inst.context_id, "offering_name": inst.offering_name,
        "model_version": inst.model_version, "taken_at": inst.taken_at, "source_instance_id": inst.source_instance_id,
        "configuration": pc.loads(inst.configuration_json), "price": pc.loads(inst.price_json),
        "attributes": len(d.get("attributes", [])), "components": len(d.get("components", [])),
        "inherited_from": sorted({a["inherited_from"] for a in d.get("attributes", []) if a.get("inherited_from")}),
        "stale": stale(db, tenant_id, inst),
    }

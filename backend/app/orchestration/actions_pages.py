"""INT-103 Customize a page (crm_core/page_service.py does the work).

The instruction goes through the same planner the Page Designer's AI box
uses - the tenant's AI model when one is set up, else the built-in reader -
and the planned changes are applied to this tenant's page only.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.crm_core import page_service
from app.data_intelligence import event_bus
from app.orchestration.orchestrator import SagaStep


def build_int103_steps(db: Session, tenant_id: int, execution_id: int, entities: dict[str, Any]) -> list[SagaStep]:
    def run() -> dict[str, Any]:
        found = page_service.find_page(db, tenant_id, str(entities.get("page") or ""))
        key, copied = found["page_key"], None
        if entities.get("copy_as"):
            copied = page_service.copy_page(db, tenant_id, key, str(entities["copy_as"]).strip())
            key = copied["page_key"]
        changes: list[str] = []
        engine = None
        instruction = str(entities.get("instruction") or "").strip()
        if instruction:
            plan = page_service.plan_page(db, tenant_id, found["entity_type"], found["segment"], instruction, key)
            if not plan["ops"]:
                raise ValueError("I couldn't tell what to change on the page. Try e.g. \"add a button 'Credit check' that runs INT-75\", "
                                 "\"make GSTIN mandatory\", \"when saved run the flow Welcome kit\", \"give it to the Sales role\" or \"activate\"")
            page_service.apply_page_ops(db, tenant_id, found["entity_type"], found["segment"], plan["ops"], key)
            changes, engine = plan["preview"], plan["engine"]
        result = page_service.page_by_key(db, tenant_id, key)
        out = {"page_key": key, "page": result["name"], "entity_type": result["entity_type"], "segment": result["segment"],
               "copied_from": found["name"] if copied else None, "changes": changes, "engine": engine, "is_active": result["is_active"],
               "role_ids": result["role_ids"], "events": result["events"]}
        event_bus.publish(db, execution_id, "page.customized", {"page_key": key, "copied": bool(copied), "changes": len(changes)})
        return out

    return [SagaStep("customize_page", run)]


BUILDERS = {"INT-103": build_int103_steps}

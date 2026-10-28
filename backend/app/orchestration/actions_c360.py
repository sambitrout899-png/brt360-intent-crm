"""INT-33 - Configure Customer 360.

The admin says what the 360 page should look like or which systems it
should read ("add network and billing panes, connect our billing system at
https://bss.example.com, compact Vi-red look"). The Configure 360 agent
turns that into checked layout operations; this saga saves the result as a
new layout version (so it can be undone) and registers any systems it
connected. Admins only - it changes the page for the whole tenant.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.c360 import agent, service
from app.crm_core import llm
from app.models.c360 import C360Layout
from app.models.tenant import Tenant
from app.orchestration.orchestrator import SagaStep


class Configure360Error(ValueError):
    pass


def build_int33_steps(db: Session, tenant_id: int, execution_id: int, entities: dict[str, Any]) -> list[SagaStep]:
    instruction = str(entities.get("instruction") or "").strip()
    state: dict[str, Any] = {}

    def understand() -> dict[str, Any]:
        if entities.get("_actor_role") != "admin":
            raise PermissionError("Only a tenant administrator can configure Customer 360")
        layout, version = service.current(db, tenant_id)
        tenant = db.get(Tenant, tenant_id)
        result = agent.plan(instruction, layout, service.specs(db, tenant_id), llm.resolve(db, tenant_id), tenant.brand_color if tenant else None)
        if result["undo"]:
            previous = service.previous(db, tenant_id)
            if previous is None:
                raise Configure360Error("There is no earlier Customer 360 version to go back to")
            result["layout"] = previous
        if not result["changes"]:
            raise Configure360Error(
                "I couldn't work out a change from that - try e.g. 'add a billing pane with revenue trend' or 'connect our network system'"
                + (f" ({'; '.join(result['notes'])})" if result["notes"] else "")
            )
        state.update(result=result, from_version=version)
        return {"changes": result["changes"], "notes": result["notes"], "engine": result["engine"]}

    def connect() -> dict[str, Any]:
        created = service.create_from_drafts(db, tenant_id, state["result"]["connectors"])
        return {"connectors_added": [c.name for c in created]}

    def save() -> dict[str, Any]:
        result = state["result"]
        version = service.save(db, tenant_id, result["layout"], "; ".join(result["changes"]), f"intent #{execution_id}")
        layout_id = db.query(C360Layout.id).filter(C360Layout.tenant_id == tenant_id, C360Layout.version == version).scalar()
        return {
            "c360_version": version,
            "c360_layout_id": layout_id,
            "previous_version": state["from_version"],
            "panes": [p["title"] for p in result["layout"]["panes"]],
            "summary": "; ".join(result["changes"]),
        }

    return [
        SagaStep("understand_request", understand),
        SagaStep("connect_systems", connect),
        SagaStep("save_customer_360_layout", save),
    ]


# The intents this module carries out (app/capabilities.py).
BUILDERS = {
    "INT-33": build_int33_steps,
}

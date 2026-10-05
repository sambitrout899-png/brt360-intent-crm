"""INT-15 - Add a prospect.

The actual entry point of the customer journey, ahead of any Lead: a
Party can now exist as a bare prospect (party_type "prospect") with no
source/region/propensity data at all - those only make sense once
something is actually being sold to them, which is what "Qualify this
lead" (INT-04) still does, now optionally against an existing prospect
Party instead of always creating a brand new one.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.data_intelligence import event_bus
from app.integration import party_adapter
from app.orchestration.orchestrator import SagaStep


def build_int15_steps(db: Session, tenant_id: int, execution_id: int, entities: dict[str, Any]) -> list[SagaStep]:
    name = entities["name"]
    email = entities.get("email")
    phone = entities.get("phone")

    def step1() -> dict[str, Any]:
        party = party_adapter.get_or_create_party(db, tenant_id, name, party_type="prospect")
        if email:
            party.email = email
        if phone:
            party.phone = phone
        db.flush()
        event_bus.publish(db, execution_id, "prospect.added", {"party_id": party.id, "name": party.name})
        return {"party_id": party.id, "name": party.name, "party_type": party.party_type}

    return [SagaStep("add_prospect", step1)]

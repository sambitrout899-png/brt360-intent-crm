"""INT-13 - Verify customer identity (Phase 3: B2C process pack).

Gated to b2c/both tenants - see app/intent_engine/reasoning.py's
vertical-gating check, fed by context_assembly.py.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.crm_core import identity_service
from app.data_intelligence import event_bus
from app.models.crm import Party
from app.orchestration.orchestrator import SagaStep


def build_int13_steps(db: Session, tenant_id: int, execution_id: int, entities: dict[str, Any]) -> list[SagaStep]:
    party_id = entities["party_id"]
    id_type = entities.get("id_type") or "national_id"
    id_number = entities["id_number"]
    # a commercial registration is a business's ID - the business registry (WATHQ) checks it
    id_type = str(id_type).strip().lower().replace(" ", "_")
    provider_name = entities.get("provider") or ("WATHQ" if id_type == "commercial_registration" else "Yakeen")
    state: dict[str, Any] = {}

    def step1() -> dict[str, Any]:
        party = db.get(Party, party_id)
        if party is None:
            raise ValueError(f"Party {party_id} not found")
        state["party"] = party
        return {"party_id": party.id}

    def step2() -> dict[str, Any]:
        verification = identity_service.submit_verification(db, tenant_id, party_id, provider_name, id_type, id_number)
        state["verification"] = verification
        event_bus.publish(
            db,
            execution_id,
            "identity.verification_submitted",
            {"verification_id": verification.id, "party_id": party_id, "status": verification.status.value},
        )
        return {
            "verification_id": verification.id,
            "party_id": party_id,
            "status": verification.status.value,
            "provider": provider_name,
            "detail": verification.detail,
        }

    return [
        SagaStep("resolve_party", step1),
        SagaStep("submit_identity_verification", step2),
    ]

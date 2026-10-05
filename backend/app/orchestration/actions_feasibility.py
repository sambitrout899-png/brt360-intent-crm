"""INT-18 - Run feasibility assessment (B2B: at the Opportunity level,
before quoting - see app/crm_core/feasibility_service.py).
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.crm_core import feasibility_service
from app.data_intelligence import event_bus
from app.models.crm import Opportunity
from app.orchestration.orchestrator import SagaStep


def build_int18_steps(db: Session, tenant_id: int, execution_id: int, entities: dict[str, Any]) -> list[SagaStep]:
    opportunity_id = entities["opportunity_id"]
    locator = entities.get("address") or entities.get("site") or ""

    def step1() -> dict[str, Any]:
        opportunity = db.get(Opportunity, opportunity_id)
        if opportunity is None:
            raise ValueError(f"Opportunity {opportunity_id} not found")
        assessment = feasibility_service.run_assessment(db, tenant_id, opportunity_id, locator)
        event_bus.publish(
            db,
            execution_id,
            "feasibility.assessed",
            {"opportunity_id": opportunity_id, "assessment_id": assessment.id, "outcome": assessment.outcome.value},
        )
        return {
            "opportunity_id": opportunity_id,
            "assessment_id": assessment.id,
            "outcome": assessment.outcome.value,
            "results": [
                {"check_type_name": r.check_type_name, "outcome": r.outcome.value, "detail": r.detail}
                for r in assessment.results
            ],
        }

    return [SagaStep("run_feasibility_assessment", step1)]

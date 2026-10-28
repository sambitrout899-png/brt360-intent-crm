"""INT-18 - Run feasibility assessment (B2B: at the Opportunity level,
before quoting - see app/crm_core/feasibility_service.py).
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.crm_core import feasibility_service
from app.data_intelligence import event_bus
from app.models.crm import Opportunity, OpportunityStage
from app.orchestration.orchestrator import SagaStep


def build_int18_steps(db: Session, tenant_id: int, execution_id: int, entities: dict[str, Any]) -> list[SagaStep]:
    opportunity_id = entities["opportunity_id"]
    locator = entities.get("address") or entities.get("site") or ""

    def step1() -> dict[str, Any]:
        opportunity = db.get(Opportunity, opportunity_id)
        if opportunity is None:
            raise ValueError(f"Opportunity {opportunity_id} not found")
        on_presales_path = opportunity.stage == OpportunityStage.in_feasibility
        config = opportunity.config
        assessment = feasibility_service.run_assessment(
            db, tenant_id, opportunity_id, locator or (config.site_address if config is not None else None) or ""
        )
        event_bus.publish(
            db,
            execution_id,
            "feasibility.assessed",
            {"opportunity_id": opportunity_id, "assessment_id": assessment.id, "outcome": assessment.outcome.value},
        )
        result = {
            "opportunity_id": opportunity_id,
            "assessment_id": assessment.id,
            "outcome": assessment.outcome.value,
            "results": [
                {"check_type_name": r.check_type_name, "outcome": r.outcome.value, "detail": r.detail}
                for r in assessment.results
            ],
        }
        # Sent to Presales (INT-38): Presales' conclusion moves the
        # opportunity on - feasible back to the Account Manager to quote,
        # not feasible back to reconfigure.
        if on_presales_path:
            from app.orchestration import actions_sales

            result.update(actions_sales.presales_outcome(db, tenant_id, opportunity, assessment.outcome.value, entities))
        return result

    return [SagaStep("run_feasibility_assessment", step1)]


def build_int34_steps(db, tenant_id, execution_id, entities):
    from app.crm_core.inventory_sync_service import sync
    def run():
        result = sync(db, tenant_id)
        event_bus.publish(db, execution_id, "inventory.check_types_synced", result)
        return result
    return [SagaStep("sync_inventory_check_types", run)]


# The intents this module carries out (app/capabilities.py).
BUILDERS = {
    "INT-18": build_int18_steps,
    "INT-34": build_int34_steps,
}

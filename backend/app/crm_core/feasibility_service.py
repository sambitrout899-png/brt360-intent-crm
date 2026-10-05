"""Configurable, multi-resource feasibility assessment - see
app/models/feasibility.py. Runs every active FeasibilityCheckType for a
tenant against one Opportunity, via inventory_adapter/address_adapter.

Each check is deterministic (see those adapters) except the one case
that stands in for "couldn't decide synchronously": a locator containing
"PENDING" - that check comes back pending rather than passed/failed, and
if ANY check is pending, the whole assessment starts a
"feasibility_assessment" SLA clock (the BRD's "5-day -> Jeopardy" pattern)
rather than resolving immediately. The registered breach handler just
escalates - Jeopardy means "flag for a human," not auto-resolve.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.crm_core import sla_service
from app.integration import address_adapter, inventory_adapter
from app.models.feasibility import FeasibilityAssessment, FeasibilityCheckResult, FeasibilityCheckType, FeasibilityOutcome
from app.models.sla import SlaClock
from app.timeutils import utcnow


def run_assessment(db: Session, tenant_id: int, opportunity_id: int, locator: str) -> FeasibilityAssessment:
    """locator stands in for whatever a real check would key off - an
    installation address for B2C FTTH, a site code for a B2B circuit.
    """
    assessment = FeasibilityAssessment(tenant_id=tenant_id, opportunity_id=opportunity_id, outcome=FeasibilityOutcome.pending)
    db.add(assessment)
    db.flush()

    check_types = (
        db.query(FeasibilityCheckType)
        .filter(FeasibilityCheckType.tenant_id == tenant_id, FeasibilityCheckType.is_active.is_(True))
        .all()
    )

    address_ok, address_detail = address_adapter.validate_address(locator)
    any_failed = not address_ok
    any_pending = False

    if not check_types:
        # No checks configured - nothing to assess against; treat as an
        # address-validation-only pass/fail so this intent still does
        # something meaningful for a tenant that hasn't configured
        # resource-level checks yet.
        db.add(
            FeasibilityCheckResult(
                assessment_id=assessment.id,
                check_type_name="Address Validation",
                resource_type="address",
                outcome=FeasibilityOutcome.passed if address_ok else FeasibilityOutcome.failed,
                detail=address_detail,
            )
        )
    else:
        for check_type in check_types:
            if not address_ok:
                outcome, detail = FeasibilityOutcome.failed, address_detail
            elif "PENDING" in locator.upper():
                outcome, detail = FeasibilityOutcome.pending, f"{check_type.name} could not be decided synchronously."
                any_pending = True
            else:
                available, detail = inventory_adapter.check_resource_availability(check_type.resource_type, locator)
                outcome = FeasibilityOutcome.passed if available else FeasibilityOutcome.failed
                any_failed = any_failed or not available
            db.add(
                FeasibilityCheckResult(
                    assessment_id=assessment.id,
                    check_type_name=check_type.name,
                    resource_type=check_type.resource_type,
                    outcome=outcome,
                    detail=detail,
                )
            )

    db.flush()

    if any_pending:
        assessment.outcome = FeasibilityOutcome.pending
        sla_service.start_clock(db, tenant_id, "feasibility_assessment", "feasibility_assessment", assessment.id)
    elif any_failed:
        assessment.outcome = FeasibilityOutcome.failed
        assessment.resolved_at = utcnow()
    else:
        assessment.outcome = FeasibilityOutcome.passed
        assessment.resolved_at = utcnow()

    db.flush()
    return assessment


def _handle_feasibility_breach(db: Session, clock: SlaClock, correlation_id: int) -> dict[str, Any]:
    assessment = db.get(FeasibilityAssessment, clock.entity_id)
    if assessment is None or assessment.outcome != FeasibilityOutcome.pending:
        return {"outcome": "skipped", "detail": "Assessment is no longer pending - nothing to do."}
    # Jeopardy: flag for a human, don't guess pass/fail on their behalf.
    return {
        "outcome": "escalated",
        "detail": f"Feasibility assessment {assessment.id} is in Jeopardy - still pending after the SLA window lapsed.",
    }


sla_service.register_breach_handler("feasibility_assessment", _handle_feasibility_breach)

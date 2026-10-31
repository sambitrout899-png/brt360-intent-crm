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

import logging
from typing import Any

from sqlalchemy.orm import Session

from app.crm_core import sla_service
from app.integration import address_adapter, inventory_adapter
from app.models.feasibility import FeasibilityAssessment, FeasibilityCheckResult, FeasibilityCheckType, FeasibilityOutcome
from app.models.sla import SlaClock
from app.timeutils import utcnow

logger = logging.getLogger(__name__)

def run_assessment(db: Session, tenant_id: int, opportunity_id: int | None, locator: str, order_id: int | None = None) -> FeasibilityAssessment:
    """locator stands in for whatever a real check would key off - an
    installation address for B2C FTTH, a site code for a B2B circuit.
    B2B runs it on the opportunity (Presales); a B2C order runs it on the
    order itself, from the Order Module (order_id, no opportunity).
    """
    assessment = FeasibilityAssessment(tenant_id=tenant_id, opportunity_id=opportunity_id, order_id=order_id,
                                       outcome=FeasibilityOutcome.pending)
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

    if opportunity_id is not None and address_ok and "PENDING" not in locator.upper() and _network_recorded(db, tenant_id):
        # The tenant records its network in BRT 360 Inventory: check the real thing - the location serving
        # the site, and each resource the product needs there (what a reservation would give).
        from app.crm_core import inventory_service

        try:
            with db.begin_nested():
                view = inventory_service.opportunity_view(db, tenant_id, opportunity_id, locator)
        except Exception as exc:
            # Inventory couldn't answer: undecided, not "not feasible" - Presales decides (and may override the inventory check)
            if not isinstance(exc, ValueError):
                logger.exception("Inventory check failed for opportunity %s", opportunity_id)
            view = {"location": None, "checks": [], "holds": []}
            any_pending = True
            db.add(FeasibilityCheckResult(assessment_id=assessment.id, check_type_name="Network Inventory", resource_type="inventory",
                                          outcome=FeasibilityOutcome.pending,
                                          detail=f"Inventory couldn't be checked: {exc if isinstance(exc, ValueError) else 'it did not answer'}"
                                                 " - Presales decides, and can override the inventory check"))
        else:
            db.add(FeasibilityCheckResult(assessment_id=assessment.id, check_type_name="Serving location", resource_type="location",
                                          outcome=FeasibilityOutcome.passed if view["location"] else FeasibilityOutcome.failed,
                                          detail=f"{view['location']} ({view['location_name']}) serves the site" if view["location"]
                                          else f"No location in Inventory serves '{locator}'"))
            any_failed = view["location"] is None
        held = {h["type_code"] for h in view["holds"]} | {h.get("resource_type") for h in view["holds"]}
        for c in view["checks"]:
            ok = c["available"] is not False or c["resource_type"] in held
            any_failed = any_failed or not ok
            db.add(FeasibilityCheckResult(assessment_id=assessment.id, check_type_name=f"Network Inventory · {c['resource_type'].replace('_', ' ')}",
                                          resource_type=c["resource_type"][:50],
                                          outcome=FeasibilityOutcome.passed if ok else FeasibilityOutcome.failed,
                                          detail=("already held for this deal" if c["resource_type"] in held else c["detail"])[:2000]))
    elif not check_types:
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


OVERRIDE = "inventory_override"  # the check result Presales' manual override of the inventory check leaves


def latest(db: Session, tenant_id: int, opportunity_id: int) -> FeasibilityAssessment | None:
    return (db.query(FeasibilityAssessment).filter(FeasibilityAssessment.tenant_id == tenant_id,
                                                   FeasibilityAssessment.opportunity_id == opportunity_id)
            .order_by(FeasibilityAssessment.id.desc()).first())


def record_override(db: Session, tenant_id: int, opportunity_id: int, reason: str) -> None:
    """Presales passed feasibility over the inventory check: the network resources weren't held, so what
    provisioning takes from Inventory is assigned at fulfilment (see inventory_overridden)."""
    assessment = latest(db, tenant_id, opportunity_id)
    if assessment is not None:
        db.add(FeasibilityCheckResult(assessment_id=assessment.id, check_type_name="Inventory check overridden by Presales",
                                      resource_type=OVERRIDE, outcome=FeasibilityOutcome.passed, detail=reason[:2000]))
        db.flush()


def inventory_overridden(db: Session, tenant_id: int, opportunity_id: int | None) -> bool:
    """Whether the opportunity's last feasibility check passed on Presales' inventory override."""
    assessment = latest(db, tenant_id, opportunity_id) if opportunity_id else None
    if assessment is not None and any(r.resource_type == OVERRIDE for r in assessment.results):
        return True
    # overrides made before they were recorded as a check result left only the feasibility note
    from app.models.crm import Opportunity

    opp = db.get(Opportunity, opportunity_id) if opportunity_id else None
    return bool(opp is not None and opp.config is not None and "Inventory check overridden by Presales" in (opp.config.feasibility_notes or ""))


def order_overridden(db: Session, tenant_id: int, order) -> bool:
    """Whether the deal an order came from passed feasibility on Presales' inventory override."""
    from app.models.crm import Quote

    quote = db.get(Quote, order.quote_id) if order.quote_id else None
    return quote is not None and inventory_overridden(db, tenant_id, quote.opportunity_id)


def _network_recorded(db: Session, tenant_id: int) -> bool:
    from app.crm_core import inventory_service

    return inventory_service.in_use(db, tenant_id)


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

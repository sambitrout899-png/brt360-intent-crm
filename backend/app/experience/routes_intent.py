"""Intent Command Bar API (HLD Experience layer).

POST /api/intents is the single entry point: free text in, full execution
trace out. This function is intentionally the one place that calls every
other layer in order, so the HLD's chain (Capture -> Context -> Reasoning ->
Orchestrate -> Verify) is visible as a straight line of code, not scattered
across the codebase.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.auth import require_roles
from app.data_intelligence import audit
from app.db import get_db
from app.intent_engine import classifier, context_assembly, reasoning, verification
from app.models.auth import UserRole
from app.orchestration import (
    actions,
    actions_feasibility,
    actions_fulfillment,
    actions_identity,
    actions_l2q,
    actions_o2a,
    actions_prospect,
    actions_site,
    actions_t2r,
)
from app.orchestration.orchestrator import run_saga
from app.schemas.intent import IntentAssistRequest, IntentAssistResponse, IntentRequest, IntentResponse, PlanStep
from app.tenancy import get_current_tenant_id

# viewer is read-only - blocked from submitting intents (the only way
# this platform mutates anything), not just from /api/admin/*.
router = APIRouter(
    prefix="/api/intents", tags=["intents"], dependencies=[Depends(require_roles(UserRole.admin, UserRole.agent))]
)

_STEP_BUILDERS = {
    "INT-01": actions.build_int01_steps,
    "INT-02": actions.build_int02_steps,
    "INT-03": actions.build_int03_steps,
    "INT-04": actions_l2q.build_int04_steps,
    "INT-05": actions_l2q.build_int05_steps,
    "INT-06": actions_l2q.build_int06_steps,
    "INT-07": actions_o2a.build_int07_steps,
    "INT-08": actions_o2a.build_int08_steps,
    "INT-09": actions_o2a.build_int09_steps,
    "INT-10": actions_t2r.build_int10_steps,
    "INT-11": actions_t2r.build_int11_steps,
    "INT-12": actions_t2r.build_int12_steps,
    "INT-13": actions_identity.build_int13_steps,
    "INT-14": actions_site.build_int14_steps,
    "INT-15": actions_prospect.build_int15_steps,
    "INT-16": actions_l2q.build_int16_steps,
    "INT-17": actions_l2q.build_int17_steps,
    "INT-18": actions_feasibility.build_int18_steps,
    "INT-19": actions_l2q.build_int19_steps,
    "INT-20": actions_l2q.build_int20_steps,
    "INT-22": actions_fulfillment.build_int22_steps,
    "INT-23": actions_fulfillment.build_int23_steps,
    "INT-24": actions_fulfillment.build_int24_steps,
    "INT-25": actions_fulfillment.build_int25_steps,
    "INT-26": actions_fulfillment.build_int26_steps,
    "INT-27": actions_fulfillment.build_int27_steps,
    "INT-28": actions_fulfillment.build_int28_steps,
    "INT-21": actions_o2a.build_int21_steps,
}


@router.post("", response_model=IntentResponse)
def submit_intent(
    request: IntentRequest,
    db: Session = Depends(get_db),
    tenant_id: int = Depends(get_current_tenant_id),
) -> IntentResponse:
    log = audit.start_execution(db, tenant_id, request.text, channel=request.channel)

    # 1. Intent Capture (classification)
    classification = classifier.classify(request.text, db, tenant_id)

    # 2. Context Assembly
    context = (
        context_assembly.assemble_context(db, tenant_id, classification.intent_type, classification.entities)
        if classification.intent_type
        else {}
    )
    audit.record_context(log, context)

    # 3. Reasoning & Planning
    reasoning_result = reasoning.plan(
        classification.intent_type,
        classification.entities,
        context,
        classification.confidence,
        classification.rationale,
    )
    audit.record_classification(
        log, classification.intent_type or "UNKNOWN", classification.entities, reasoning_result.confidence, reasoning_result.rationale
    )

    plan_steps: list[PlanStep] = []
    result: dict | None = None
    events: list[str] = []

    if not reasoning_result.auto_execute:
        audit.complete(log, "needs_review")
        db.commit()
        return IntentResponse(
            intent_execution_id=log.id,
            intent_type=classification.intent_type,
            entities=classification.entities,
            confidence=reasoning_result.confidence,
            rationale=reasoning_result.rationale,
            status="needs_review",
            plan=[],
            result=None,
            events=[],
        )

    # 4. Action Orchestrator
    step_builder = _STEP_BUILDERS[classification.intent_type]
    saga_steps = step_builder(db, tenant_id, log.id, classification.entities)
    saga_result = run_saga(db, saga_steps)
    plan_steps = [PlanStep(**s) for s in saga_result.steps]
    audit.record_plan(log, saga_result.steps)

    if not saga_result.ok:
        audit.complete(log, "failed")
        db.commit()
        return IntentResponse(
            intent_execution_id=log.id,
            intent_type=classification.intent_type,
            entities=classification.entities,
            confidence=reasoning_result.confidence,
            rationale=reasoning_result.rationale + f" Execution failed: {saga_result.error}",
            status="failed",
            plan=plan_steps,
            result=None,
            events=[],
        )

    # 5. Verification
    verification_result = verification.verify(db, classification.intent_type, saga_result.result)
    status = "executed" if verification_result.ok else "verification_failed"
    audit.complete(log, status)

    events = [e.event_type for e in log.events]
    db.commit()

    return IntentResponse(
        intent_execution_id=log.id,
        intent_type=classification.intent_type,
        entities=classification.entities,
        confidence=reasoning_result.confidence,
        rationale=reasoning_result.rationale + f" Verification: {verification_result.detail}",
        status=status,
        plan=plan_steps,
        result=saga_result.result,
        events=events,
    )


@router.post("/assist", response_model=IntentAssistResponse)
def assist_intent(
    request: IntentAssistRequest,
    db: Session = Depends(get_db),
    tenant_id: int = Depends(get_current_tenant_id),
) -> IntentAssistResponse:
    """The Expression Builder's live intellisense - runs the exact same
    classifier the real submission uses (same tenant-resolved AI key,
    same prompt, same casual-English tolerance) against whatever the
    user has typed so far, and reports what it currently understands:
    which intent, which required fields are already filled, which are
    still missing. Never executes anything, never writes an audit log
    entry - purely advisory, called repeatedly (debounced) while typing.
    """
    classification = classifier.classify(request.text, db, tenant_id)
    required = reasoning.required_fields_for(classification.intent_type) if classification.intent_type else []
    missing = [
        f for f in required
        if classification.entities.get(f) is None or classification.entities.get(f) == "" or classification.entities.get(f) == []
    ]
    return IntentAssistResponse(
        intent_type=classification.intent_type,
        intent_label=classifier.INTENT_LABELS.get(classification.intent_type) if classification.intent_type else None,
        confidence=classification.confidence,
        rationale=classification.rationale,
        entities=classification.entities,
        required_fields=required,
        missing_fields=missing,
    )

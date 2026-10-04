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

from app.data_intelligence import audit
from app.db import get_db
from app.intent_engine import classifier, context_assembly, reasoning, verification
from app.orchestration import actions, actions_l2q, actions_o2a, actions_t2r
from app.orchestration.orchestrator import run_saga
from app.schemas.intent import IntentRequest, IntentResponse, PlanStep

router = APIRouter(prefix="/api/intents", tags=["intents"])

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
}


@router.post("", response_model=IntentResponse)
def submit_intent(request: IntentRequest, db: Session = Depends(get_db)) -> IntentResponse:
    log = audit.start_execution(db, request.text)

    # 1. Intent Capture (classification)
    classification = classifier.classify(request.text)

    # 2. Context Assembly
    context = (
        context_assembly.assemble_context(db, classification.intent_type, classification.entities)
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
    saga_steps = step_builder(db, log.id, classification.entities)
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

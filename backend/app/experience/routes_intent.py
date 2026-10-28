"""Intent Command Bar API (HLD Experience layer).

POST /api/intents is the single entry point: free text in, full execution
trace out. This function is intentionally the one place that calls every
other layer in order, so the HLD's chain (Capture -> Context -> Reasoning ->
Orchestrate -> Verify) is visible as a straight line of code, not scattered
across the codebase.
"""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.access import get_access
from app.auth import get_current_user, require_roles
from app.crm_core import access_service, lifecycle_service, record_service
from app.crm_core.access_service import Access
from app.data_intelligence import audit
from app.db import get_db
from app.intent_engine import (
    classifier,
    context_assembly,
    copilot,
    intent_registry,
    reasoning,
    understanding,
    verification,
)
from app.models.auth import User, UserRole
from app.models.intent_log import IntentExecutionLog
from app import capabilities
from app.orchestration import actions_lifecycle, conflicts, flow_engine, rehearsal
from app.orchestration import fulfillment_engine as _fulfillment_engine
from app.orchestration.orchestrator import SagaStep, run_saga
from app.schemas.intent import (
    IntentAssistRequest,
    IntentAssistResponse,
    IntentRequest,
    IntentResponse,
    IntentSuggestion,
    PlanStep,
)
from app.schemas.intent_catalog import IntentPrepareIn
from app.tenancy import get_current_tenant_id

# viewer is read-only - blocked from submitting intents (the only way
# this platform mutates anything), not just from /api/admin/*.
router = APIRouter(
    prefix="/api/intents", tags=["intents"], dependencies=[Depends(require_roles(UserRole.admin, UserRole.agent))]
)


MAX_STATE_FLOW_CASCADE = 3


def _normalize_entities(intent_type: str | None, entities: dict) -> dict:
    """INT-29's updates may arrive as a "fields" object (LLM) or as loose
    keys - collapse both into "fields" so the required-field check and
    the step builder see one shape. List fields typed into a form as text
    ("Fiber500 + VOIP") become lists."""
    if intent_type == "INT-29" and not isinstance(entities.get("fields"), dict):
        entities = {**entities, "fields": record_service.split_update_fields(entities)}
    return understanding.normalize_list_fields(dict(entities))


def _suggestions(db: Session, tenant_id: int, codes: list[str], user: User | None = None) -> list[IntentSuggestion]:
    out = []
    for code in codes:
        if user is not None and intent_registry.availability(db, tenant_id, user, code):
            continue
        required, label = intent_registry.requirements(db, tenant_id, code)
        if label:
            out.append(IntentSuggestion(intent_type=code, label=label, required_fields=required))
    return out


_RECORD_KEYS = {"party_id": "party", "lead_id": "lead", "opportunity_id": "opportunity", "quote_id": "quote", "order_id": "order"}


def _hidden_record(db: Session, access: Access, entities: dict) -> str | None:
    """The first record an intent names that the caller may not see."""
    if access.unrestricted:
        return None
    refs = [(entity_type, entities.get(key)) for key, entity_type in _RECORD_KEYS.items()]
    if entities.get("target_service_account_id"):
        from app.crm_core.abo_service import account
        _, target_owner = account(db, access.tenant_id, int(entities["target_service_account_id"]))
        refs.append(("party", target_owner))
    if entities.get("asset_id"):
        from app.models.fulfillment import Asset
        from app.crm_core.abo_service import account
        asset = db.get(Asset, int(entities["asset_id"]))
        if asset and asset.tenant_id == access.tenant_id:
            _, owner = account(db, access.tenant_id, asset.service_account_id)
            refs.append(("party", owner))
    refs.append((lifecycle_service.normalize_entity_type(entities.get("entity_type")), entities.get("entity_id")))
    for entity_type, raw_id in refs:
        try:
            entity_id = int(raw_id)
        except (TypeError, ValueError):
            continue
        if entity_type and lifecycle_service.get_record(db, access.tenant_id, entity_type, entity_id) is not None:
            if not access_service.can_see(db, access, entity_type, entity_id):
                return f"{entity_type.capitalize()} #{entity_id}"
    return None


def _position_label(access: Access) -> str:
    return access.active_position.name if access.active_position else "none"


def _context_dict(request) -> dict | None:
    return request.context.model_dump(exclude_none=True) if request.context else None


def _run_error_flows(db: Session, tenant_id: int, execution_id: int, intent_type: str | None, entities: dict, error: dict) -> list[dict]:
    """The flows tagged "on error" of this intent - run after its failure was
    undone, each on its own savepoint, with the failure as {{error.*}}. A
    handler that fails itself is reported and undone; it never hides the
    original error."""
    if not intent_type:
        return []
    from app.orchestration import flow_errors

    public = {k: v for k, v in entities.items() if not str(k).startswith("_")}
    runs: list[dict] = []
    for flow in flow_engine.flows_for_intent(db, tenant_id, intent_type, "error"):
        ctx = {"entities": public, "result": {}, "error": error,
               "trigger": {"intent": intent_type, "input": dict(public), "output": {}, "error": error}}
        if public.get("entity_type") and public.get("entity_id"):
            ctx.update(entity_type=public["entity_type"], entity_id=public["entity_id"])
        savepoint = db.begin_nested()
        try:
            run = flow_engine.run_flow(db, tenant_id, execution_id, flow, ctx)
            savepoint.commit()
            runs.append({k: run.get(k) for k in ("flow_id", "flow_name", "outcome", "entity_type", "entity_id", "trace")})
        except Exception as exc:  # noqa: BLE001 - a broken handler is reported, never raised over the original failure
            if savepoint.is_active:
                savepoint.rollback()
            err = flow_errors.classify(exc, flow)
            runs.append({"flow_id": flow.id, "flow_name": flow.name, "outcome": "failed", "error": err.as_dict(),
                         "trace": (err.run or {}).get("trace", [])})
    return runs


def _compose_steps(
    db: Session,
    tenant_id: int,
    execution_id: int,
    intent_type: str,
    entities: dict,
    core_steps: list[SagaStep],
) -> tuple[list[SagaStep], dict]:
    """Wraps an intent's own steps with (Phase 7):
    - "before" flows tagged on this intent (can reject it),
    - "after" flows tagged on this intent (see the intent's results),
    - the fulfillment hook: an active fulfillment pattern this intent starts
      (vanilla or custom - its trigger intents) takes the order it acted on,
    - the lifecycle hook: state history + assignment rules for every
      record the intent touched, then any state_enter flows, cascading
      (bounded) when a flow itself moves a record into another state.
    Everything stays inside one saga, so it all commits or rolls back
    together. Returns the steps and the live dict the steps fill in."""
    public_entities = {k: v for k, v in entities.items() if not k.startswith("_")}
    collected: dict = {"flows_run": []}
    intent_output: dict = {}

    def wrap(step: SagaStep, core: bool = False, after: bool = False) -> SagaStep:
        def action() -> dict:
            if after and intent_output.get("configuration_request"):
                return {}
            detail = step.action() or {}
            if core:
                intent_output.update({k: v for k, v in detail.items() if k != "flows_run"})
            for key, value in detail.items():
                if key in ("flows_run", "flows_skipped", "flows_failed"):
                    collected.setdefault(key, []).extend(value)
                else:
                    collected[key] = value
            return detail

        return SagaStep(step.name, action)

    def ctx() -> dict:
        return {"entities": public_entities, "result": {k: v for k, v in collected.items() if k != "flows_run"},
                "trigger": {"intent": intent_type, "input": dict(public_entities), "output": dict(intent_output)}}

    def lifecycle_hook() -> dict:
        source = f"intent:{intent_type}"
        entries = lifecycle_service.sync_records(db, tenant_id, lifecycle_service.records_in_result(collected), source)
        all_entries = list(entries)
        for _ in range(MAX_STATE_FLOW_CASCADE):
            touched: list[tuple[str, int]] = []
            for entry in entries:
                for flow in flow_engine.flows_for_state(db, tenant_id, entry["entity_type"], entry["to_state"]):
                    run = flow_engine.run_flow_guarded(
                        db, tenant_id, execution_id, flow,
                        {**ctx(), "entity_type": entry["entity_type"], "entity_id": entry["entity_id"]},
                    )
                    if run.get("failed"):  # its policy said carry on - reported, not raised
                        collected.setdefault("flows_failed", []).append(run)
                        continue
                    if run.get("skipped") and run.get("reason"):
                        collected.setdefault("flows_skipped", []).append(run)
                        continue
                    collected["flows_run"].append(run)
                    touched.append((entry["entity_type"], entry["entity_id"]))
                    # records a "Run an action" step moved (the lead's opportunity) - their own state flows run next
                    touched.extend(tuple(t) for t in run.get("touched", []) if tuple(t) not in touched)
            if not touched:
                break
            entries = lifecycle_service.sync_records(db, tenant_id, touched, "flow")
            all_entries.extend(entries)
        return {"state_entries": all_entries} if all_entries else {}

    def fulfillment_hook() -> dict:
        if intent_output.get("configuration_request"):
            return {}
        return _fulfillment_engine.on_intent(db, tenant_id, execution_id, intent_type, public_entities, collected)

    before = [flow_engine.flow_step(db, tenant_id, execution_id, f, ctx) for f in flow_engine.flows_for_intent(db, tenant_id, intent_type, "before")]
    after = [flow_engine.flow_step(db, tenant_id, execution_id, f, ctx) for f in flow_engine.flows_for_intent(db, tenant_id, intent_type, "after")]
    steps = [wrap(s) for s in before] + [wrap(s, core=True) for s in core_steps] + [wrap(s, after=True) for s in after]
    if _fulfillment_engine.started_by_intents(db, tenant_id, intent_type):  # only shown in the plan when a pattern listens for it
        steps.append(wrap(SagaStep("fulfillment_trigger", fulfillment_hook)))
    steps.append(wrap(SagaStep("lifecycle_sync", lifecycle_hook)))
    return steps, collected


@router.post("", response_model=IntentResponse)
def submit_intent(
    request: IntentRequest,
    db: Session = Depends(get_db),
    tenant_id: int = Depends(get_current_tenant_id),
    user: User = Depends(get_current_user),
    access: Access = Depends(get_access),
) -> IntentResponse:
    return _handle(request, db, tenant_id, user, access)


@router.post("/rehearse", response_model=IntentResponse)
def rehearse_intent(
    request: IntentRequest,
    db: Session = Depends(get_db),
    tenant_id: int = Depends(get_current_tenant_id),
    user: User = Depends(get_current_user),
    access: Access = Depends(get_access),
) -> IntentResponse:
    """See the future before you commit: the intent runs exactly as for
    real - same checks, approvals, flows, cascades and notifications - then
    everything is rolled back and its consequences come back as `impact`
    (see orchestration/rehearsal.py). Nothing persists, not even the audit
    log entry."""
    with rehearsal.capture(db) as rows:
        response = _handle(request, db, tenant_id, user, access, rehearse=rows)
    db.rollback()
    response.intent_execution_id = 0
    response.rehearsal = True
    try:  # what this run touches that pulls against something else - for an admin to settle
        ran = [f.get("flow_id") for f in (response.result or {}).get("flows_run", []) if isinstance(f, dict)]
        response.conflicts = conflicts.relevant(db, tenant_id, [response.intent_type or ""], ran)
    except Exception:  # noqa: BLE001 - a scan that can't run must not spoil the rehearsal
        response.conflicts = []
    return response


def _settle(db: Session, rehearse: list | None, hold: bool = False) -> None:
    """A real run commits; a rehearsal leaves no trace; a held run (one step
    of an agent's plan) leaves the transaction open for the agent to settle."""
    if hold:
        db.flush()
    elif rehearse is None:
        db.commit()
    else:
        db.rollback()


def _handle(
    request: IntentRequest,
    db: Session,
    tenant_id: int,
    user: User,
    access: Access,
    rehearse: list | None = None,
    hold: bool = False,
) -> IntentResponse:
    log = audit.start_execution(db, tenant_id, request.text, channel=request.channel)
    log.user_id = user.id

    # 1. Intent Capture (classification) - skipped for a structured intent
    # from an in-page form, which already states the intent precisely.
    if request.structured is not None:
        known = capabilities.has_steps(request.structured.intent_type) or flow_engine.flow_from_intent_code(
            db, tenant_id, request.structured.intent_type
        )
        entities = {k: v for k, v in request.structured.entities.items() if not str(k).startswith("_")}
        classification = classifier.ClassificationResult(
            request.structured.intent_type if known else None,
            copilot.typed_entities(db, tenant_id, request.structured.intent_type, entities) if known else entities,
            1.0 if known else 0.0,
            "Structured intent submitted from an in-page form." if known else "Unknown structured intent type.",
        )
    else:
        classification = classifier.classify(request.text, db, tenant_id, _context_dict(request))
    classification.entities = _normalize_entities(classification.intent_type, classification.entities)
    if request.structured is None:
        filled = understanding.apply_context(
            classification.intent_type, classification.entities, _context_dict(request),
            intent_registry.requirements(db, tenant_id, classification.intent_type)[0],
        )
        if filled:
            classification.rationale += f" Filled {', '.join(filled)} from the record you are viewing."
    linked = copilot.fill_from_records(db, tenant_id, classification.intent_type, classification.entities)
    if linked:
        classification.rationale += f" Took {', '.join(linked)} from the chosen record."

    # The tenant's intent catalog: switched-off intents, and intents
    # restricted to roles the user doesn't hold, never run.
    blocked = intent_registry.availability(db, tenant_id, user, classification.intent_type, classification.entities)
    if blocked:
        audit.record_classification(log, classification.intent_type or "UNKNOWN", classification.entities, 0.0, blocked)
        audit.complete(log, "failed")
        _settle(db, rehearse, hold)
        return IntentResponse(
            intent_execution_id=log.id,
            intent_type=classification.intent_type,
            entities=classification.entities,
            confidence=0.0,
            rationale=blocked,
            status="failed",
            plan=[],
            suggestions=_suggestions(db, tenant_id, classification.alternatives, user),
            intent_label=intent_registry.requirements(db, tenant_id, classification.intent_type)[1],
        )

    # Data visibility: an intent may only act on records the caller's
    # position can see, and records it creates are stamped with that
    # position (lifecycle_service.sync_records reads current_actor).
    hidden = _hidden_record(db, access, classification.entities)
    if hidden:
        audit.complete(log, "failed")
        _settle(db, rehearse, hold)
        return IntentResponse(
            intent_execution_id=log.id,
            intent_type=classification.intent_type,
            entities=classification.entities,
            confidence=classification.confidence,
            rationale=f"{hidden} is outside the data your position ({_position_label(access)}) can see - nothing was changed.",
            status="failed",
            plan=[],
        )
    access_service.current_actor.set(access_service.actor_for(access))

    custom_flow = flow_engine.flow_from_intent_code(db, tenant_id, classification.intent_type)
    # The actor's role travels with the entities for the steps that need
    # it (INT-31 role-restricted transitions) - never logged or returned.
    # The actor's id decides approvals bound to a position.
    build_entities = {**classification.entities, "_actor_role": user.role.value, "_actor_user_id": user.id}

    # 2. Context Assembly
    context = (
        context_assembly.assemble_context(db, tenant_id, classification.intent_type, build_entities)
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
        required_fields=flow_engine.required_fields(custom_flow) if custom_flow else None,
    )
    audit.record_classification(
        log, classification.intent_type or "UNKNOWN", classification.entities, reasoning_result.confidence, reasoning_result.rationale
    )

    plan_steps: list[PlanStep] = []
    result: dict | None = None
    events: list[str] = []

    if not reasoning_result.auto_execute:
        audit.complete(log, "needs_review")
        _settle(db, rehearse, hold)
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
            missing_fields=[f for f in reasoning_result.missing_fields if f != "intent_type"],
            suggestions=_suggestions(db, tenant_id, classification.alternatives, user),
            intent_label=intent_registry.requirements(db, tenant_id, classification.intent_type)[1],
            intent_icon=_icon(db, tenant_id, classification.intent_type),
        )

    # 4. Action Orchestrator
    if custom_flow is not None:
        core_steps = actions_lifecycle.build_custom_flow_steps(db, tenant_id, log.id, build_entities, custom_flow)
    else:
        core_steps = capabilities.step_builder(classification.intent_type)(db, tenant_id, log.id, build_entities)
    saga_steps, collected = _compose_steps(db, tenant_id, log.id, classification.intent_type, build_entities, core_steps)
    saga_result = run_saga(db, saga_steps)
    # The lifecycle hook runs after every intent, but only belongs in the
    # visible plan when it actually recorded a state entry.
    saga_result.steps = [s for s in saga_result.steps if not (s["action"] == "lifecycle_sync" and not s["detail"])]
    if saga_result.ok and saga_result.result is not None:
        saga_result.result["flows_run"] = collected["flows_run"]
        if collected.get("flows_skipped"):
            saga_result.result["flows_skipped"] = collected["flows_skipped"]
        if collected.get("flows_failed"):
            saga_result.result["flows_failed"] = collected["flows_failed"]
        if not collected["flows_run"]:
            saga_result.result.pop("flows_run")
    plan_steps = [PlanStep(**s) for s in saga_result.steps]
    audit.record_plan(log, saga_result.steps)

    if not saga_result.ok:
        # Everything the intent did is undone; its "on error" flows run now, on what is left.
        error = dict(saga_result.error_detail or {"message": saga_result.error})
        handlers = _run_error_flows(db, tenant_id, log.id, classification.intent_type, classification.entities, error)
        if handlers:
            error["flows_run"] = handlers
        audit.complete(log, "failed")
        events = [e.event_type for e in log.events]
        _settle(db, rehearse, hold)
        handled = [h["flow_name"] for h in handlers if h.get("outcome") != "failed"]
        return IntentResponse(
            intent_execution_id=log.id,
            intent_type=classification.intent_type,
            entities=classification.entities,
            confidence=reasoning_result.confidence,
            rationale=reasoning_result.rationale + f" Execution failed: {saga_result.error}",
            status="failed",
            plan=plan_steps,
            result=None,
            events=events,
            intent_label=intent_registry.requirements(db, tenant_id, classification.intent_type)[1],
            intent_icon=_icon(db, tenant_id, classification.intent_type),
            summary=f"Nothing was changed: {saga_result.error}" + (f" (handled by: {', '.join(handled)})" if handled else ""),
            error=error,
        )

    # 5. Verification
    verification_result = verification.verify(db, classification.intent_type, saga_result.result)
    status = "executed" if verification_result.ok else "verification_failed"
    audit.complete(log, status)

    events = [e.event_type for e in log.events]
    impact = None
    if rehearse is None:
        _settle(db, None, hold)
    else:
        # read everything the run would leave behind, then take it all back
        db.flush()
        impact = rehearsal.summarize(db, rehearse, collected)
        status = "rehearsed" if verification_result.ok else status
    view = copilot.outcome(db, tenant_id, user, access, classification.intent_type, classification.entities, saga_result.result)
    failed_flows = (saga_result.result or {}).get("flows_failed") or []
    if failed_flows:  # the intent went through; these automations were undone under their "continue" policy
        notes = "; ".join(f"{f.get('flow_name')}: {(f.get('error') or {}).get('message', 'failed')}" for f in failed_flows)
        view["summary"] = f"{view.get('summary') or ''} Not done (automation failed and was undone): {notes}".strip()
    if rehearse is not None:
        db.rollback()
        view["summary"] = impact["headline"]
        view["next_actions"] = []
        view["records"] = []  # what they'd point at was never kept
    return IntentResponse(
        impact=impact,
        intent_execution_id=log.id,
        intent_type=classification.intent_type,
        entities=classification.entities,
        confidence=reasoning_result.confidence,
        rationale=reasoning_result.rationale + f" Verification: {verification_result.detail}",
        status=status,
        plan=plan_steps,
        result=saga_result.result,
        events=events,
        intent_label=intent_registry.requirements(db, tenant_id, classification.intent_type)[1],
        intent_icon=_icon(db, tenant_id, classification.intent_type),
        **view,
    )


def _icon(db: Session, tenant_id: int, code: str | None) -> str | None:
    found = intent_registry.entry(db, tenant_id, code)
    return found["icon"] if found else None


@router.post("/assist", response_model=IntentAssistResponse)
def assist_intent(
    request: IntentAssistRequest,
    db: Session = Depends(get_db),
    tenant_id: int = Depends(get_current_tenant_id),
    user: User = Depends(get_current_user),
) -> IntentAssistResponse:
    """The Expression Builder's live intellisense - runs the exact same
    classifier the real submission uses (same tenant-resolved AI key,
    same prompt, same casual-English tolerance) against whatever the
    user has typed so far, and reports what it currently understands:
    which intent, which required fields are already filled, which are
    still missing. Never executes anything, never writes an audit log
    entry - purely advisory, called repeatedly (debounced) while typing.
    """
    context = _context_dict(request)
    classification = classifier.classify(request.text, db, tenant_id, context, live=True)
    classification.entities = _normalize_entities(classification.intent_type, classification.entities)
    blocked = intent_registry.availability(db, tenant_id, user, classification.intent_type)
    if blocked:
        alternatives = [c for c in classification.alternatives if not intent_registry.availability(db, tenant_id, user, c)]
        return IntentAssistResponse(
            intent_type=None, intent_label=None, confidence=0.0, rationale=blocked, entities={},
            required_fields=[], missing_fields=[], suggestions=_suggestions(db, tenant_id, alternatives, user),
        )
    required, label = intent_registry.requirements(db, tenant_id, classification.intent_type)
    understanding.apply_context(classification.intent_type, classification.entities, context, required)
    copilot.fill_from_records(db, tenant_id, classification.intent_type, classification.entities)
    missing = [
        f for f in required
        if classification.entities.get(f) in (None, "", [], {})
    ]
    return IntentAssistResponse(
        intent_type=classification.intent_type,
        intent_label=label,
        confidence=classification.confidence,
        rationale=classification.rationale,
        entities=classification.entities,
        required_fields=required,
        missing_fields=missing,
        suggestions=_suggestions(db, tenant_id, classification.alternatives, user),
    )


# ---------------------------------------------------------------- command bar


@router.get("/catalog")
def intent_catalog(
    db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id), user: User = Depends(get_current_user)
) -> dict:
    """The intents this user may run, grouped for the command bar."""
    from app.orchestration.function_map import PROCESSES

    intents = intent_registry.intents_for_user(db, tenant_id, user)
    used = {c for c in (i["category"] for i in intents)}
    mine = {i["code"] for i in intents}
    # The business processes, each with only the steps this user's roles allow, in process order.
    processes = [{"key": p["key"], "label": p["label"], "steps": [c for c in dict.fromkeys(p["steps"]) if c in mine]} for p in PROCESSES]
    return {
        "intents": intents,
        "categories": [{"key": k, "label": v} for k, v in intent_registry.CATEGORIES if k in used],
        "processes": [p for p in processes if p["steps"]],
    }


@router.get("/recent")
def recent_intents(
    db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id), user: User = Depends(get_current_user)
) -> list[dict]:
    """The user's last runs, newest first, one per intent - for 'Run again'."""
    logs = (
        db.query(IntentExecutionLog)
        .filter(IntentExecutionLog.tenant_id == tenant_id, IntentExecutionLog.user_id == user.id, IntentExecutionLog.intent_type != "")
        .order_by(IntentExecutionLog.id.desc())
        .limit(60)
        .all()
    )
    seen: set[str] = set()
    out: list[dict] = []
    for log in logs:
        if log.intent_type in seen or log.intent_type == "UNKNOWN" or intent_registry.availability(db, tenant_id, user, log.intent_type):
            continue
        found = intent_registry.entry(db, tenant_id, log.intent_type)
        if found is None:
            continue
        seen.add(log.intent_type)
        try:
            entities = json.loads(log.entities_json or "{}")
        except json.JSONDecodeError:
            entities = {}
        out.append({"code": log.intent_type, "label": found["label"], "icon": found["icon"], "status": log.status,
                    "text": log.raw_text, "entities": entities, "at": log.created_at})
        if len(out) >= 6:
            break
    return out


@router.post("/prepare")
def prepare_intent(
    payload: IntentPrepareIn,
    db: Session = Depends(get_db),
    tenant_id: int = Depends(get_current_tenant_id),
    user: User = Depends(get_current_user),
    access: Access = Depends(get_access),
) -> dict:
    """Pre-fill, options, suggestions and pre-checks for one intent's form
    (see copilot.prepare) - never executes anything."""
    context = payload.context.model_dump(exclude_none=True) if payload.context else None
    blocked = intent_registry.availability(db, tenant_id, user, payload.intent_type, payload.entities)
    if blocked:
        raise HTTPException(403, blocked)
    prepared = copilot.prepare(db, tenant_id, user, access, payload.intent_type, payload.entities, payload.text, context)
    if prepared is None:
        raise HTTPException(404, "Unknown intent")
    return prepared


@router.get("/lookup")
def lookup_records(
    record_type: str,
    q: str = "",
    intent_type: str | None = None,
    field: str | None = None,
    db: Session = Depends(get_db),
    tenant_id: int = Depends(get_current_tenant_id),
    access: Access = Depends(get_access),
) -> list[dict]:
    return copilot.lookup(db, tenant_id, access, record_type, q, intent_type, field, limit=10)

"""The "llm_planned" orchestration flavor (Phase 5) - see
app/models/orchestration_policy.py and app/orchestration/fulfillment_ops.py.

Hard safety boundary: the LLM is given a goal description, the already-
known entities, and a short, explicit list of operation names it is
allowed to use (a subset of fulfillment_ops.CATALOG, chosen by the
caller for this specific decision point). It returns an ordered JSON
plan of {"operation", "params"} steps. Every operation name is validated
against that allow-list before anything runs - an unrecognized name
raises immediately rather than being skipped or guessed at, so a
hallucinated or out-of-scope operation always surfaces as a visible
failure, never a silent wrong action. The LLM never sees or produces
code; it only ever selects and parameterizes functions a human already
wrote and tested. If no OPENAI_API_KEY is configured, or the LLM's
response doesn't parse into a usable plan, this raises - the caller
(actions_fulfillment.py) catches that and falls back to its fixed
deterministic step list, exactly like every other "opt-in, safe
fallback" primitive in this codebase.
"""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy.orm import Session

from app.config import get_settings
from app.orchestration import fulfillment_ops
from app.orchestration.orchestrator import SagaStep


class PlanningUnavailable(RuntimeError):
    """Raised whenever the LLM-planned flavor cannot produce a usable
    plan - no API key, a malformed response, or a disallowed operation.
    Callers should catch this and fall back to the deterministic plan.
    """


def _ask_llm_for_plan(
    api_key: str, model: str, goal_description: str, entities: dict[str, Any], allowed_operations: list[str]
) -> list[dict[str, Any]]:
    from openai import OpenAI

    client = OpenAI(api_key=api_key)
    system_prompt = (
        "You are an orchestration planner for a telecom Intent CRM. Given a goal and the "
        "already-known entities/results, produce a strict JSON plan for what should happen "
        "next. Respond with exactly this shape: "
        '{"plan": [{"operation": "<name>", "params": {...}}, ...]}. '
        f"You MUST only use these operation names, exactly as spelled: {', '.join(allowed_operations)}. "
        "Never invent an operation name, and never include a step whose operation is not in "
        "that list. Only include params each operation actually needs. An empty plan "
        '({"plan": []}) is correct when nothing further should happen.'
    )
    user_prompt = f"Goal: {goal_description}\nKnown entities and results so far: {json.dumps(entities, default=str)}"
    response = client.chat.completions.create(
        model=model,
        response_format={"type": "json_object"},
        messages=[{"role": "system", "content": system_prompt}, {"role": "user", "content": user_prompt}],
        temperature=0,
    )
    raw = response.choices[0].message.content or "{}"
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise PlanningUnavailable("LLM response was not valid JSON") from exc
    plan = parsed.get("plan")
    if not isinstance(plan, list):
        raise PlanningUnavailable("LLM response had no 'plan' list")
    return plan


def plan_steps(
    db: Session,
    tenant_id: int,
    goal_description: str,
    entities: dict[str, Any],
    allowed_operations: list[str],
) -> list[SagaStep]:
    """Returns a list[SagaStep] exactly like a deterministic
    build_intXX_steps function would - the caller passes this straight
    into orchestrator.run_saga, so rollback-on-failure behaves
    identically regardless of which flavor produced the plan.
    """
    settings = get_settings()
    if not settings.openai_api_key:
        raise PlanningUnavailable("No OPENAI_API_KEY configured - llm_planned mode is unavailable")

    raw_plan = _ask_llm_for_plan(settings.openai_api_key, settings.openai_model, goal_description, entities, allowed_operations)

    steps: list[SagaStep] = []
    for raw_step in raw_plan:
        if not isinstance(raw_step, dict):
            raise PlanningUnavailable(f"LLM plan step was not an object: {raw_step!r}")
        operation = raw_step.get("operation")
        params = raw_step.get("params") or {}
        if operation not in allowed_operations or operation not in fulfillment_ops.CATALOG:
            raise PlanningUnavailable(
                f"LLM proposed operation '{operation}', which is outside the allowed catalog {allowed_operations}"
            )
        if not isinstance(params, dict):
            raise PlanningUnavailable(f"LLM plan step '{operation}' had non-object params: {params!r}")

        fn = fulfillment_ops.CATALOG[operation]

        def action(fn=fn, params=params) -> dict[str, Any]:
            return fn(db, tenant_id, **params)

        steps.append(SagaStep(f"llm_planned:{operation}", action))

    return steps

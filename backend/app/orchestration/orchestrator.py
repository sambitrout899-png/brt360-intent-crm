"""Action Orchestrator (HLD Layer 4) - executes an approved plan as a saga.

Sprint 1 scope note: every side effect a saga step can currently have is
local (this database). A failed step therefore compensates by rolling back
the whole transaction - the simplest *correct* form of compensation when
nothing external has been touched yet. The per-step hook (SagaStep.name,
independently retryable/identifiable) is already in place so that once a
later sprint's orchestration spans real external provisioning systems
(HLD INT-07 territory), per-step compensation handlers slot in here without
changing the step functions' call signature.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from sqlalchemy.orm import Session


@dataclass
class SagaStep:
    name: str
    action: Callable[[], dict[str, Any]]


@dataclass
class SagaResult:
    ok: bool
    steps: list[dict[str, Any]]
    result: dict[str, Any] | None
    error: str | None = None


def run_saga(db: Session, steps: list[SagaStep]) -> SagaResult:
    executed: list[dict[str, Any]] = []
    try:
        # Accumulate across all steps (later steps override same-named keys)
        # rather than keeping only the last step's return value - a step late
        # in the sequence (e.g. a notification/retention step) may not repeat
        # every identifier an earlier step established, and Verification needs
        # all of them.
        accumulated: dict[str, Any] = {}
        for i, step in enumerate(steps, start=1):
            detail = step.action() or {}
            accumulated.update(detail)
            executed.append({"step": i, "action": step.name, "status": "ok", "detail": detail})
        return SagaResult(ok=True, steps=executed, result=accumulated)
    except Exception as exc:  # noqa: BLE001 - deliberately broad: any step failure aborts the saga
        executed.append({"step": len(executed) + 1, "action": "error", "status": "failed", "detail": {"error": str(exc)}})
        db.rollback()
        return SagaResult(ok=False, steps=executed, result=None, error=str(exc))

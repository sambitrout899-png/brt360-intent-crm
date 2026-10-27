"""Tenant-local memory of action chains the agent carried out successfully -
the sibling of process_memory (configuration recipes), in the same audit store.

Only a chain that ran to the end ("done") is remembered, with the steps as
they were run (the plan, not the records it created - ids made by the run are
never stored as inputs). Recall is for an exact repeat of the request: its
proven plan is reused instead of planning again (no model calls), then
re-annotated against today's catalog - so an action since switched off or
forbidden is refused when it runs, as always. This is retrieval memory, not
model training.
"""
from __future__ import annotations

import json
from typing import Any

from app.models.intent_log import IntentExecutionLog

KIND = "CHAIN-RECIPE"
LIMIT = 50


def _norm(text: str) -> str:
    return " ".join((text or "").lower().split())


def recipes(db, tenant_id: int) -> list[dict[str, Any]]:
    rows = (db.query(IntentExecutionLog)
            .filter(IntentExecutionLog.tenant_id == tenant_id, IntentExecutionLog.intent_type == KIND, IntentExecutionLog.status == "executed")
            .order_by(IntentExecutionLog.id.desc()).limit(LIMIT).all())
    out = []
    for row in rows:
        try:
            value = json.loads(row.context_json)
        except (ValueError, TypeError):
            continue
        if isinstance(value, dict) and value.get("version") == 1 and isinstance(value.get("steps"), list):
            out.append({**value, "id": row.id})
    return out


def recall(db, tenant_id: int, goal: str) -> dict[str, Any] | None:
    wanted = _norm(goal)
    return next((r for r in recipes(db, tenant_id) if _norm(r.get("goal", "")) == wanted), None) if wanted else None


def remember(db, tenant_id: int, user_id: int | None, goal: str, steps: list[dict[str, Any]], notes: list[str] | None = None) -> int | None:
    """Called after a chain was committed. The steps as planned and confirmed by the user."""
    from app.data_intelligence import audit

    if not _norm(goal) or not steps:
        return None
    clean = [{"intent_type": s.get("intent_type"), "text": s.get("text") or "",
              "entities": {k: v for k, v in (s.get("entities") or {}).items() if not str(k).startswith("_")}} for s in steps]
    log = audit.start_execution(db, tenant_id, goal[:4000], channel="chain_memory")
    log.intent_type = KIND
    log.user_id = user_id
    audit.record_context(log, {"version": 1, "goal": goal, "steps": clean, "notes": notes or []})
    audit.complete(log, "executed")
    db.commit()
    return log.id

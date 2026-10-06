"""Resolves which orchestration flavor governs one intent execution for
one tenant - see app/models/orchestration_policy.py. A tenant can
configure a policy for a specific intent type (e.g. "INT-25") or for
"ALL"; a specific-intent-type row always wins over an "ALL" row. No
matching active row, or llm_planned chosen without an OPENAI_API_KEY
configured, both resolve to deterministic - this is always a safe,
silent fallback, never a hard error.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.config import get_settings
from app.models.orchestration_policy import OrchestrationMode, OrchestrationPolicy


def resolve_mode(db: Session, tenant_id: int, intent_type: str) -> OrchestrationMode:
    policy = (
        db.query(OrchestrationPolicy)
        .filter(
            OrchestrationPolicy.tenant_id == tenant_id,
            OrchestrationPolicy.intent_type == intent_type,
            OrchestrationPolicy.is_active.is_(True),
        )
        .first()
    )
    if policy is None:
        policy = (
            db.query(OrchestrationPolicy)
            .filter(
                OrchestrationPolicy.tenant_id == tenant_id,
                OrchestrationPolicy.intent_type == "ALL",
                OrchestrationPolicy.is_active.is_(True),
            )
            .first()
        )
    if policy is None:
        return OrchestrationMode.deterministic
    if policy.mode == OrchestrationMode.llm_planned and not get_settings().openai_api_key:
        return OrchestrationMode.deterministic
    return policy.mode

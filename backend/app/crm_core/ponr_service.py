"""Configurable Point of No Return (PONR) - see app/models/ponr.py.

DEFAULT_PONR_STATUSES is what applies when a tenant hasn't configured a
policy: a quote is past PONR once approved (an approved quote should be
cancelled and requoted, not silently edited); an order is past PONR
once it has actually started provisioning (anything beyond
created/activating - the whole point of "activating" existing as its own
status, see actions_o2a.py's multi-site path).

Precedence: an active PonrPolicy, then a configured state model's
locked states (lifecycle_service), then DEFAULT_PONR_STATUSES.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.models.ponr import PonrPolicy

DEFAULT_PONR_STATUSES: dict[str, set[str]] = {
    "quote": {"approved"},
    "order": {"active", "changed", "suspended", "pending_cancellation", "cancelled", "terminated"},
}


def has_reached_ponr(db: Session, tenant_id: int, entity_type: str, current_status: str) -> bool:
    policy = (
        db.query(PonrPolicy)
        .filter(PonrPolicy.tenant_id == tenant_id, PonrPolicy.entity_type == entity_type, PonrPolicy.is_active.is_(True))
        .first()
    )
    if policy is not None:
        return current_status in {s.strip() for s in policy.ponr_statuses.split(",") if s.strip()}

    # Phase 7: a tenant-configured state model's lock point (editable=False)
    # is the PONR when no explicit PONR policy overrides it.
    from app.crm_core import lifecycle_service

    model = lifecycle_service.resolve_state_model(db, tenant_id, entity_type) if entity_type in lifecycle_service.ENTITY_MODELS else None
    if model is not None and model.configured and current_status in model.states:
        return not model.states[current_status].editable
    return current_status in DEFAULT_PONR_STATUSES.get(entity_type, set())

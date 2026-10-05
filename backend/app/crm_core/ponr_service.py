"""Configurable Point of No Return (PONR) - see app/models/ponr.py.

DEFAULT_PONR_STATUSES is what applies when a tenant hasn't configured a
policy: a quote is past PONR once approved (an approved quote should be
cancelled and requoted, not silently edited); an order is past PONR
once it has actually started provisioning (anything beyond
created/activating - the whole point of "activating" existing as its own
status, see actions_o2a.py's multi-site path).
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
    ponr_statuses = (
        {s.strip() for s in policy.ponr_statuses.split(",") if s.strip()}
        if policy is not None
        else DEFAULT_PONR_STATUSES.get(entity_type, set())
    )
    return current_status in ponr_statuses

"""Configurable multi-tier approval chains (Phase 1 primitive #2).

Replaces the old flat, hardcoded DISCOUNT_MATRIX constant (HLD 5.2 Master &
Reference Data, Appendix B - flagged there as illustrative and needing a
real authority matrix per deployment). A chain is now tenant-configured
reference data: ApprovalChainTemplate rows, each covering a discount-pct
band and optionally a segment, with an ordered list of tiers
(ApprovalChainTier). Per the B2C/B2B BRD research, this needs to vary
*structurally* per tenant/segment - e.g. a large Wholesale deal might need
Regional Sales Head -> VP Sales -> CFO in sequence, while a small B2C
discount needs a single rubber-stamp tier - so template resolution
considers both the discount band and the quote's product segment.

DEFAULT_SEED_TEMPLATES is the out-of-the-box chain shape every newly
seeded tenant gets (equivalent to the old DISCOUNT_MATRIX, with one real
multi-tier band added at the top so the capability is actually exercised).
Admins edit/replace this per tenant via /api/admin/approval-chains.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.models.approval import ApprovalChainInstance, ApprovalChainTemplate, ApprovalStepInstance
from app.models.crm import ApprovalStatus, Quote
from app.timeutils import utcnow

# (name, segment, min_discount_pct, max_discount_pct, [approver tiers in order])
DEFAULT_SEED_TEMPLATES: list[tuple[str, str | None, float, float, list[str]]] = [
    ("Auto-Approved", None, 0.0, 10.0, ["Auto-Approved"]),
    ("Regional Sign-off", None, 10.0, 20.0, ["Regional Sales Head"]),
    ("VP Sign-off", None, 20.0, 30.0, ["VP Sales"]),
    ("Executive Escalation", None, 30.0, 100.0, ["VP Sales", "CFO"]),
]


def resolve_template(db: Session, tenant_id: int, discount_pct: float, segment: str | None) -> ApprovalChainTemplate | None:
    """Pick the chain template covering this discount band. A template
    scoped to the quote's exact segment wins over a generic (segment=None)
    one when both cover the discount; ties broken by the narrowest band.
    """
    candidates = (
        db.query(ApprovalChainTemplate)
        .filter(
            ApprovalChainTemplate.tenant_id == tenant_id,
            ApprovalChainTemplate.is_active.is_(True),
            ApprovalChainTemplate.min_discount_pct <= discount_pct,
            ApprovalChainTemplate.max_discount_pct >= discount_pct,
        )
        .all()
    )
    if not candidates:
        return None
    segment_matches = [t for t in candidates if t.segment == segment]
    pool = segment_matches or [t for t in candidates if t.segment is None]
    if not pool:
        return None
    return min(pool, key=lambda t: t.max_discount_pct - t.min_discount_pct)


def get_rep_authority_ceiling_pct(db: Session, tenant_id: int, segment: str | None) -> float:
    """The discount pct above which a rep cannot submit a quote without a
    written justification - the ceiling of whichever template is a single,
    auto-approved tier covering zero discount. Falls back to 0 (always
    require justification) if no such template is configured.
    """
    template = resolve_template(db, tenant_id, 0.0, segment)
    if template and len(template.tiers) == 1:
        return template.max_discount_pct
    return 0.0


def instantiate_chain(db: Session, quote: Quote, template: ApprovalChainTemplate) -> ApprovalChainInstance:
    instance = ApprovalChainInstance(
        quote_id=quote.id, template_id=template.id, status=ApprovalStatus.pending, current_sequence=1
    )
    db.add(instance)
    db.flush()
    for tier in template.tiers:
        db.add(
            ApprovalStepInstance(
                chain_instance_id=instance.id,
                sequence=tier.sequence,
                approver_tier=tier.approver_tier,
                status=ApprovalStatus.pending,
            )
        )
    db.flush()
    return instance


def record_step_decision(db: Session, instance: ApprovalChainInstance, decision: str, reasoning: str) -> dict:
    """Decide whichever tier is currently pending. Advances the chain to
    the next tier on approval, or terminates it immediately on rejection -
    a reject at any tier rejects the whole chain, it does not fall through
    to the next one.
    """
    step = next((s for s in instance.steps if s.sequence == instance.current_sequence), None)
    if step is None:
        raise ValueError("This approval chain has no pending step left to decide")

    step.status = ApprovalStatus.approved if decision == "approve" else (
        ApprovalStatus.rejected if decision == "reject" else ApprovalStatus.conditional
    )
    step.reasoning = reasoning
    step.decided_at = utcnow()

    is_last_tier = instance.current_sequence >= len(instance.steps)
    if decision == "reject":
        instance.status = ApprovalStatus.rejected
    elif is_last_tier:
        instance.status = ApprovalStatus.approved
    else:
        instance.current_sequence += 1
        instance.status = ApprovalStatus.pending

    db.flush()
    next_tier = None
    if instance.status == ApprovalStatus.pending:
        next_tier = instance.steps[instance.current_sequence - 1].approver_tier
    return {
        "chain_status": instance.status.value,
        "decided_tier": step.approver_tier,
        "next_tier": next_tier,
    }

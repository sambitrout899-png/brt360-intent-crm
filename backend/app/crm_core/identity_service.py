"""KYC / identity verification (Phase 3: B2C process pack).

submit_verification is the one call INT-13 makes. _stub_check is a
deterministic stand-in for a real provider call - same
testable-without-a-live-system convention as order_service's network/SIM/
IP stand-ins: "FAIL" in the id_number rejects outright, "PENDING"
simulates a provider that can't decide synchronously (the case the 2h
kyc_verification SLA clock exists for), anything else verifies
immediately.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.crm_core import sla_service
from app.models.identity import IdentityVerification, VerificationStatus
from app.models.sla import SlaClock
from app.timeutils import utcnow


def _stub_check(id_number: str) -> str:
    upper = id_number.upper()
    if "FAIL" in upper:
        return "rejected"
    if "PENDING" in upper:
        return "pending"
    return "verified"


def submit_verification(
    db: Session, tenant_id: int, party_id: int, provider_name: str, id_type: str, id_number: str
) -> IdentityVerification:
    outcome = _stub_check(id_number)
    detail = {
        "verified": "",
        "rejected": f"{provider_name} rejected this identity document.",
        "pending": f"{provider_name} could not decide synchronously - awaiting the KYC auto-approval window.",
    }[outcome]

    verification = IdentityVerification(
        tenant_id=tenant_id,
        party_id=party_id,
        provider_name=provider_name,
        id_type=id_type,
        id_last4=id_number[-4:],
        status=VerificationStatus(outcome),
        detail=detail,
    )
    if outcome != "pending":
        verification.resolved_at = utcnow()
    db.add(verification)
    db.flush()

    if outcome == "pending":
        sla_service.start_clock(db, tenant_id, "kyc_verification", "identity_verification", verification.id)

    return verification


def _handle_kyc_breach(db: Session, clock: SlaClock, correlation_id: int) -> dict[str, Any]:
    verification = db.get(IdentityVerification, clock.entity_id)
    if verification is None or verification.status != VerificationStatus.pending:
        return {"outcome": "skipped", "detail": "Verification is no longer pending - nothing to do."}
    verification.status = VerificationStatus.verified
    verification.resolved_at = utcnow()
    verification.detail = "Auto-approved - the KYC SLA grace period lapsed with no manual decision."
    db.flush()
    return {"outcome": "verified", "detail": f"Identity verification {verification.id} auto-approved after SLA breach."}


sla_service.register_breach_handler("kyc_verification", _handle_kyc_breach)

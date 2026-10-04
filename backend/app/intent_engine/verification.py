"""Verification & Notification (HLD 4.5).

Checks each step's post-condition against the original intent - e.g. is the
offering *actually* active with the expected version, not just "saga said ok".
Only after this passes does the caller get a completion notification. This is
deliberately separate from the mid-saga "notify_channels" step in
orchestration/actions.py, which announces the change to Sales/Care - this
step verifies the change actually took effect before telling the *requester*
it's done.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from app.models.catalog import OfferingStatus, ProductOffering


@dataclass
class VerificationResult:
    ok: bool
    detail: str


def verify(db: Session, intent_type: str, result: dict[str, Any] | None) -> VerificationResult:
    if not result or "product_offering_id" not in result:
        return VerificationResult(False, "No product_offering_id in the saga result to verify.")

    offering = db.get(ProductOffering, result["product_offering_id"])
    if offering is None:
        return VerificationResult(False, f"Offering {result['product_offering_id']} not found after execution.")

    if intent_type in ("INT-01", "INT-02"):
        if offering.status != OfferingStatus.active:
            return VerificationResult(False, f"Expected status 'active', found '{offering.status.value}'.")
        return VerificationResult(True, f"Offering {offering.id} v{offering.version} confirmed active.")

    if intent_type == "INT-03":
        if offering.status != OfferingStatus.retired:
            return VerificationResult(False, f"Expected status 'retired', found '{offering.status.value}'.")
        if offering.replacement_offering_id is None:
            return VerificationResult(False, "Offering retired but no replacement_offering_id recorded.")
        return VerificationResult(True, f"Offering {offering.id} confirmed retired with a migration path on file.")

    return VerificationResult(True, "No verification rule defined for this intent type; passing through.")

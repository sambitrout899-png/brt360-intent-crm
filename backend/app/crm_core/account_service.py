"""3-tier account hierarchy (Phase 1 primitive #5).

get_or_create_service_account is the one call INT-07 (order activation)
makes before creating an Order - idempotent on (billing_account_id, name)
so re-ordering under the same name groups into the same service account,
while a different name (e.g. a different site or bundle) gets its own.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.models.accounts import ServiceAccount


def standing(db: Session, tenant_id: int, party_id: int | None) -> tuple[list[str], list[str]]:
    """The account's standing for selling to it, as Siebel checks it: a
    blocked account (fraud, central block) or a blocked billing profile
    stops new sales and changes - errors; a billing profile in collections
    is a warning. Disconnects and suspensions are never stopped by this."""
    from app.models.comms import CmAccountDetail, CmBillingProfile

    errors: list[str] = []
    warnings: list[str] = []
    if not party_id:
        return errors, warnings
    detail = db.query(CmAccountDetail).filter(CmAccountDetail.tenant_id == tenant_id, CmAccountDetail.party_id == party_id).first()
    if detail is not None and not detail.good_standing:
        errors.append(f"The account is blocked ({detail.block_reason or 'not in good standing'}) - clear the block first")
    for profile in db.query(CmBillingProfile).filter(CmBillingProfile.tenant_id == tenant_id, CmBillingProfile.party_id == party_id):
        if profile.status == "red":
            errors.append(f"Billing profile {profile.name} is blocked{f' ({profile.blocked_reason})' if profile.blocked_reason else ''}")
        elif profile.status == "yellow":
            warnings.append(f"Billing profile {profile.name} is in collections")
    return errors, warnings


def check_standing(db: Session, tenant_id: int, party_id: int | None) -> None:
    errors, _ = standing(db, tenant_id, party_id)
    if errors:
        raise ValueError("; ".join(errors))


def get_or_create_service_account(db: Session, tenant_id: int, billing_account_id: int, name: str) -> ServiceAccount:
    existing = (
        db.query(ServiceAccount)
        .filter(ServiceAccount.tenant_id == tenant_id, ServiceAccount.billing_account_id == billing_account_id, ServiceAccount.name == name)
        .first()
    )
    if existing:
        return existing
    account = ServiceAccount(tenant_id=tenant_id, billing_account_id=billing_account_id, name=name, status="active")
    db.add(account)
    db.flush()
    return account

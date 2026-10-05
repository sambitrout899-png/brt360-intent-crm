"""3-tier account hierarchy (Phase 1 primitive #5).

get_or_create_service_account is the one call INT-07 (order activation)
makes before creating an Order - idempotent on (billing_account_id, name)
so re-ordering under the same name groups into the same service account,
while a different name (e.g. a different site or bundle) gets its own.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.models.accounts import ServiceAccount


def get_or_create_service_account(db: Session, tenant_id: int, billing_account_id: int, name: str) -> ServiceAccount:
    existing = (
        db.query(ServiceAccount)
        .filter(ServiceAccount.billing_account_id == billing_account_id, ServiceAccount.name == name)
        .first()
    )
    if existing:
        return existing
    account = ServiceAccount(tenant_id=tenant_id, billing_account_id=billing_account_id, name=name, status="active")
    db.add(account)
    db.flush()
    return account

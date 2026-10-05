"""3-tier account hierarchy (Phase 1 primitive #5): Party -> BillingAccount
-> ServiceAccount -> Order/Asset. Per the B2C/B2B BRD research, a single
BillingAccount can cover several operationally distinct services - e.g. a
B2B customer's HQ line and a branch office's line are billed together but
provisioned and managed separately. ServiceAccount is that missing middle
tier; every Order now belongs to exactly one (see app/models/orders.py).
"""

from __future__ import annotations

import datetime as dt

from sqlalchemy import ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.timeutils import utcnow


class ServiceAccount(Base):
    __tablename__ = "service_accounts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"))
    billing_account_id: Mapped[int] = mapped_column(ForeignKey("billing_accounts.id"))
    name: Mapped[str] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(20), default="active")  # active/suspended/closed
    created_at: Mapped[dt.datetime] = mapped_column(default=utcnow)

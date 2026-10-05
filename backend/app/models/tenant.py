from __future__ import annotations

import datetime as dt
import enum

from sqlalchemy import Enum, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.timeutils import utcnow


class TenantVertical(str, enum.Enum):
    b2c = "b2c"
    b2b = "b2b"
    both = "both"


class Tenant(Base):
    """A telecom operator using this platform. Every root-level business
    record carries a tenant_id (directly, or via its parent FK for
    child/leaf tables - see models/__init__.py for which is which).

    `vertical` is a display/default hint only - it does NOT gate which
    intents or UI a tenant can use (both B2C and B2B flows work for any
    tenant); it just picks sensible defaults when an admin sets up a new
    tenant's approval chains/SLA policies/product attributes.
    """

    __tablename__ = "tenants"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    slug: Mapped[str] = mapped_column(String(100), unique=True)
    vertical: Mapped[TenantVertical] = mapped_column(Enum(TenantVertical), default=TenantVertical.both)
    created_at: Mapped[dt.datetime] = mapped_column(default=utcnow)

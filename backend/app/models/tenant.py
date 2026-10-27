from __future__ import annotations

import datetime as dt
import enum

from sqlalchemy import Enum, String, Text, JSON
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

    `vertical` is a default hint for most of the platform (approval
    chains/SLA policies/product attributes work for any tenant regardless
    of vertical) - but Phase 3's process-pack intents are actually gated
    by it: INT-13 (identity verification) requires b2c/both, INT-14
    (multi-site provisioning) requires b2b/both. See
    app/intent_engine/context_assembly.py's "vertical_blocked" checks.
    """

    __tablename__ = "tenants"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    slug: Mapped[str] = mapped_column(String(100), unique=True)
    vertical: Mapped[TenantVertical] = mapped_column(Enum(TenantVertical), default=TenantVertical.both)
    # Branding shown to every user of the tenant (app header, tab title):
    # the company name as it should appear (defaults to `name`), its logo
    # as a data: URL (small images only - see routes_tenants), and an
    # accent colour.
    display_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    logo_data_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    brand_color: Mapped[str | None] = mapped_column(String(20), nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(default=utcnow)
    # NULL preserves pre-licensing installations; [] explicitly disables all modules.
    licensed_modules: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)

"""KYC / identity verification (Phase 3: B2C process pack).

Per the B2C BRD docs, onboarding sits behind pluggable identity-
verification providers (Yakeen, WATHQ, SIMAH, Abshir) - modeled here as
tenant-configurable reference data (IdentityVerificationProvider), not
hardcoded, same as every Phase 1/2 primitive. This was flagged in the BRD
research as a capability this platform had zero abstraction for.

IdentityVerification records only a masked id_number (last 4 digits) -
never the raw value - plus whether the configured provider's check
passed, failed, or couldn't decide synchronously, in which case an SLA
clock (clock_type "kyc_verification") can auto-approve it after a
configured grace period, per the BRD's "2h -> auto-approve" KYC pattern.
INT-13 is gated to b2c/both tenants - see app/intent_engine/reasoning.py.
"""

from __future__ import annotations

import datetime as dt
import enum

from sqlalchemy import Boolean, Enum, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.timeutils import utcnow


class VerificationStatus(str, enum.Enum):
    pending = "pending"
    verified = "verified"
    rejected = "rejected"


class IdentityVerificationProvider(Base):
    __tablename__ = "identity_verification_providers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"))
    name: Mapped[str] = mapped_column(String(100))  # e.g. "Yakeen", "WATHQ", "SIMAH", "Abshir"
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[dt.datetime] = mapped_column(default=utcnow)


class IdentityVerification(Base):
    __tablename__ = "identity_verifications"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"))
    party_id: Mapped[int] = mapped_column(ForeignKey("parties.id"))
    provider_name: Mapped[str] = mapped_column(String(100))
    id_type: Mapped[str] = mapped_column(String(50))  # e.g. "national_id", "passport"
    id_last4: Mapped[str] = mapped_column(String(4))
    status: Mapped[VerificationStatus] = mapped_column(Enum(VerificationStatus), default=VerificationStatus.pending)
    detail: Mapped[str] = mapped_column(String(300), default="")
    created_at: Mapped[dt.datetime] = mapped_column(default=utcnow)
    resolved_at: Mapped[dt.datetime | None] = mapped_column(nullable=True)

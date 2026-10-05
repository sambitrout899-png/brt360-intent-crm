from __future__ import annotations

import datetime as dt
import enum

from sqlalchemy import DateTime, Enum, Float, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.timeutils import utcnow


class OfferingStatus(str, enum.Enum):
    draft = "draft"
    active = "active"
    superseded = "superseded"
    retired = "retired"


class ProductSpecification(Base):
    """Atomic, sellable product line (connectivity, VOIP, mobile, add-on, ...).

    Bundles (ProductOffering) are composed of these. Seeded as reference data -
    HLD 4.1 preconditions assume atomic products already exist in the catalog.
    """

    __tablename__ = "product_specifications"
    __table_args__ = (UniqueConstraint("tenant_id", "name", name="uq_product_specifications_tenant_name"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"))
    name: Mapped[str] = mapped_column(String(200))
    product_line: Mapped[str] = mapped_column(String(100))  # e.g. "connectivity", "voip", "mobile", "addon"
    is_atomic: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)


class ProductOffering(Base):
    """A sellable bundle/offer. Never mutated in place once active - INT-02/03
    always create a new version or flip status, per the HLD's "fully auditable,
    zero service interruption" non-functional note.
    """

    __tablename__ = "product_offerings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"))
    name: Mapped[str] = mapped_column(String(200))
    segment: Mapped[str] = mapped_column(String(100))  # e.g. "B2C", "B2B-SMB"
    status: Mapped[OfferingStatus] = mapped_column(Enum(OfferingStatus), default=OfferingStatus.draft)
    version: Mapped[int] = mapped_column(Integer, default=1)
    price: Mapped[float] = mapped_column(Float)
    price_floor: Mapped[float] = mapped_column(Float, default=0.0)
    effective_date: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)
    end_date: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)

    # Self-referencing version chain: v2's parent is v1, etc.
    parent_offering_id: Mapped[int | None] = mapped_column(ForeignKey("product_offerings.id"), nullable=True)

    # Set on retirement (INT-03) - the bundle existing subscribers should migrate to.
    replacement_offering_id: Mapped[int | None] = mapped_column(ForeignKey("product_offerings.id"), nullable=True)

    composition: Mapped[str] = mapped_column(String(500))  # comma-separated ProductSpecification names, MVP-simple

    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    price_plan: Mapped["PricePlan"] = relationship(back_populates="offering", uselist=False, cascade="all, delete-orphan")
    eligibility_rules: Mapped[list["BundleEligibilityRule"]] = relationship(
        back_populates="offering", cascade="all, delete-orphan"
    )


class PricePlan(Base):
    """PDC-style pricing reference for one ProductOffering version."""

    __tablename__ = "price_plans"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    product_offering_id: Mapped[int] = mapped_column(ForeignKey("product_offerings.id"))
    list_price: Mapped[float] = mapped_column(Float)
    discount_ceiling_pct: Mapped[float] = mapped_column(Float, default=20.0)

    offering: Mapped[ProductOffering] = relationship(back_populates="price_plan")


class BundleEligibilityRule(Base):
    """Segment/channel/region eligibility for one ProductOffering version."""

    __tablename__ = "bundle_eligibility_rules"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    product_offering_id: Mapped[int] = mapped_column(ForeignKey("product_offerings.id"))
    segment: Mapped[str] = mapped_column(String(100))
    channel: Mapped[str] = mapped_column(String(100))  # "B2B" / "B2C"
    region: Mapped[str] = mapped_column(String(100), default="ALL")

    offering: Mapped[ProductOffering] = relationship(back_populates="eligibility_rules")


class BundleCompatibilityRule(Base):
    """Which product lines may combine into one bundle - reference data used by
    INT-01/02/05's composition validation step.
    """

    __tablename__ = "bundle_compatibility_rules"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"))
    primary_product_line: Mapped[str] = mapped_column(String(100))
    addon_product_line: Mapped[str] = mapped_column(String(100))
    max_addons: Mapped[int] = mapped_column(Integer, default=5)

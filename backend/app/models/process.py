"""Reference data for the vanilla sales processes.

- PriceList / PriceListItem: what a B2B account pays (STO step 2 assigns
  one when a prospect becomes an active account). A list either prices a
  product explicitly (an item) or adjusts every list price by a percentage.
- ProcessRole: who does each step of the B2B Sales-to-Order process - the
  Account Manager, Presales, Sales Ops head, CPMO and the installation team
  (BSD) - as one of the tenant's positions. Steps that hand a record over
  (send to Presales, attach the PO, CPMO approval, WFM1) use this mapping.
"""

from __future__ import annotations

import datetime as dt

from sqlalchemy import Boolean, Date, Float, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.timeutils import utcnow


class PriceList(Base):
    __tablename__ = "price_lists"
    __table_args__ = (UniqueConstraint("tenant_id", "name", name="uq_price_lists_tenant_name"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"))
    name: Mapped[str] = mapped_column(String(150))
    currency: Mapped[str] = mapped_column(String(10), default="INR")
    # Applied to a product's list price when the list has no item for it:
    # -5 = 5% below list.
    adjustment_pct: Mapped[float] = mapped_column(Float, default=0.0)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    # in effect between these dates (Siebel's effective start / end) - open when empty
    valid_from: Mapped[dt.date | None] = mapped_column(Date, nullable=True)
    valid_to: Mapped[dt.date | None] = mapped_column(Date, nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(default=utcnow)

    def in_effect(self, on: dt.date | None = None) -> bool:
        on = on or utcnow().date()
        return self.is_active and (self.valid_from is None or self.valid_from <= on) and (self.valid_to is None or on <= self.valid_to)

    items: Mapped[list["PriceListItem"]] = relationship(back_populates="price_list", cascade="all, delete-orphan")


class PriceListItem(Base):
    __tablename__ = "price_list_items"
    __table_args__ = (UniqueConstraint("price_list_id", "offering_name", "min_quantity", name="uq_price_list_items_offering_tier"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    price_list_id: Mapped[int] = mapped_column(ForeignKey("price_lists.id"))
    offering_name: Mapped[str] = mapped_column(String(200))
    price: Mapped[float] = mapped_column(Float)
    # volume pricing: this price from this many units up (1 = the plain price)
    min_quantity: Mapped[int] = mapped_column(Integer, default=1, server_default="1")

    price_list: Mapped[PriceList] = relationship(back_populates="items")


PROCESS_ROLES: dict[str, str] = {
    "account_manager": "Account Manager",
    "presales": "Presales",
    "sales_ops": "Sales Ops Head",
    "cpmo": "CPMO",
    "bsd": "Installation team (BSD)",
}


class ProcessRole(Base):
    __tablename__ = "process_roles"
    __table_args__ = (UniqueConstraint("tenant_id", "role_key", name="uq_process_roles_tenant_role"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"))
    role_key: Mapped[str] = mapped_column(String(30))
    position_id: Mapped[int | None] = mapped_column(ForeignKey("positions.id"), nullable=True)

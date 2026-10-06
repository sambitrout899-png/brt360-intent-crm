from __future__ import annotations

import datetime as dt
import enum

from sqlalchemy import Enum, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.timeutils import utcnow


class OrderType(str, enum.Enum):
    new = "new"
    change = "change"
    cancel = "cancel"
    suspend = "suspend"


class OrderStatus(str, enum.Enum):
    created = "created"
    activating = "activating"
    active = "active"
    changed = "changed"
    suspended = "suspended"
    # Retention window open after a cancel request - the order is still
    # live and billable; it only actually cancels if the window lapses
    # with no retention offer accepted (see actions_o2a.py INT-09).
    pending_cancellation = "pending_cancellation"
    cancelled = "cancelled"
    terminated = "terminated"


class ServiceOrderStatus(str, enum.Enum):
    pending = "pending"
    provisioned = "provisioned"
    active = "active"
    released = "released"


class ResourceStatus(str, enum.Enum):
    reserved = "reserved"
    active = "active"
    released = "released"


class Order(Base):
    """HLD 5.1 Order/OrderItem - the approved commercial commitment, decomposed
    into execution tasks across network, billing and field service.
    """

    __tablename__ = "orders"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"))
    quote_id: Mapped[int | None] = mapped_column(ForeignKey("quotes.id"), nullable=True)
    party_id: Mapped[int] = mapped_column(ForeignKey("parties.id"))
    # 3-tier account hierarchy (Phase 1 primitive #5): every Order belongs
    # to a ServiceAccount, which belongs to a BillingAccount - party_id is
    # kept too since every existing query already filters by it directly.
    service_account_id: Mapped[int] = mapped_column(ForeignKey("service_accounts.id"))
    order_type: Mapped[OrderType] = mapped_column(Enum(OrderType), default=OrderType.new)
    status: Mapped[OrderStatus] = mapped_column(Enum(OrderStatus), default=OrderStatus.created)
    contract_months: Mapped[int] = mapped_column(Integer, default=12)
    monthly_price: Mapped[float] = mapped_column(Float, default=0.0)
    created_at: Mapped[dt.datetime] = mapped_column(default=utcnow)
    updated_at: Mapped[dt.datetime] = mapped_column(default=utcnow, onupdate=utcnow)

    items: Mapped[list["OrderItem"]] = relationship(back_populates="order", cascade="all, delete-orphan")
    service_orders: Mapped[list["ServiceOrder"]] = relationship(back_populates="order", cascade="all, delete-orphan")
    # Order Control Unit approval (Phase 5, INT-25) - polymorphic, same
    # pattern as Lead.chain_instance/Quote.chain_instance; opt-in, so an
    # Order whose tenant has no "order" approval chain template configured
    # simply has no instance here and INT-25 auto-approves immediately.
    chain_instance: Mapped["ApprovalChainInstance"] = relationship(
        viewonly=True,
        uselist=False,
        primaryjoin=(
            "and_(foreign(ApprovalChainInstance.entity_id) == Order.id, "
            "ApprovalChainInstance.entity_type == 'order')"
        ),
    )


class OrderItem(Base):
    __tablename__ = "order_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id"))
    product_offering_id: Mapped[int] = mapped_column(ForeignKey("product_offerings.id"))
    quantity: Mapped[int] = mapped_column(Integer, default=1)

    order: Mapped[Order] = relationship(back_populates="items")


class ServiceOrder(Base):
    """Network/service-level execution unit - HLD 5.1. Carries the
    provisioning deltas (network path, SIM, IP) as Resource rows.

    Usually exactly one per Order, but a B2B multi-site order (Phase 3:
    IPLC's A-End/Z-End pattern) has one ServiceOrder per site, each with
    its own independent status - site_label distinguishes them
    ("Primary" for the default single-site case). See actions_o2a.py
    (INT-07 creates them) and actions_site.py (INT-14 provisions one
    independently).
    """

    __tablename__ = "service_orders"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id"))
    site_label: Mapped[str] = mapped_column(String(100), default="Primary")
    status: Mapped[ServiceOrderStatus] = mapped_column(Enum(ServiceOrderStatus), default=ServiceOrderStatus.pending)
    created_at: Mapped[dt.datetime] = mapped_column(default=utcnow)
    updated_at: Mapped[dt.datetime] = mapped_column(default=utcnow, onupdate=utcnow)

    order: Mapped[Order] = relationship(back_populates="service_orders")
    resources: Mapped[list["Resource"]] = relationship(back_populates="service_order", cascade="all, delete-orphan")


class Resource(Base):
    """Network/inventory resource reservation - HLD 5.1 ServiceOrder/Resource."""

    __tablename__ = "resources"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    service_order_id: Mapped[int] = mapped_column(ForeignKey("service_orders.id"))
    resource_type: Mapped[str] = mapped_column(String(50))  # network_path/sim_profile/ip_block
    identifier: Mapped[str] = mapped_column(String(200))
    status: Mapped[ResourceStatus] = mapped_column(Enum(ResourceStatus), default=ResourceStatus.reserved)
    created_at: Mapped[dt.datetime] = mapped_column(default=utcnow)

    service_order: Mapped[ServiceOrder] = relationship(back_populates="resources")


class BillingAccount(Base):
    __tablename__ = "billing_accounts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"))
    party_id: Mapped[int] = mapped_column(ForeignKey("parties.id"), unique=True)
    status: Mapped[str] = mapped_column(String(20), default="active")  # active/suspended/closed
    rate_plan: Mapped[str] = mapped_column(String(200), default="")
    balance: Mapped[float] = mapped_column(Float, default=0.0)
    created_at: Mapped[dt.datetime] = mapped_column(default=utcnow)
    updated_at: Mapped[dt.datetime] = mapped_column(default=utcnow, onupdate=utcnow)

    adjustments: Mapped[list["BillingAdjustment"]] = relationship(back_populates="billing_account")


class BillingAdjustment(Base):
    __tablename__ = "billing_adjustments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    billing_account_id: Mapped[int] = mapped_column(ForeignKey("billing_accounts.id"))
    order_id: Mapped[int | None] = mapped_column(ForeignKey("orders.id"), nullable=True)
    amount: Mapped[float] = mapped_column(Float)  # positive = charge, negative = credit
    reason: Mapped[str] = mapped_column(Text)
    created_at: Mapped[dt.datetime] = mapped_column(default=utcnow)

    billing_account: Mapped[BillingAccount] = relationship(back_populates="adjustments")

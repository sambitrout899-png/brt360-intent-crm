"""Immutable asset revision history and separately fulfilled change requests."""
from __future__ import annotations
import datetime as dt
from sqlalchemy import Date, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, Index, text
from sqlalchemy.orm import Mapped, mapped_column
from app.db import Base
from app.timeutils import utcnow
from sqlalchemy import event, inspect
from sqlalchemy.orm import Session


@event.listens_for(Session, "before_flush")
def protect_completed_orders(session, *_):
    from app.models.orders import Order, OrderItem, OrderStatus
    from app.models.product_model import ProductInstance
    from app.models.product_config import OrderItemConfig
    for obj in list(session.dirty) + list(session.deleted) + list(session.new):
        order = obj if isinstance(obj, Order) else obj.order if isinstance(obj, OrderItem) else None
        if isinstance(obj, ProductInstance) and obj.context_type == "order":
            order = session.get(Order, obj.context_id)
        if isinstance(obj, OrderItemConfig):
            item = session.get(OrderItem, obj.order_item_id)
            order = item.order if item else None
        if order is None:
            continue
        history = inspect(order).attrs.status.history
        previous = history.deleted[0] if history.deleted else order.status
        if previous != OrderStatus.completed:
            continue
        changed = [a.key for a in inspect(obj).mapper.column_attrs if not a.key.startswith("sys_") and a.key != "updated_at" and inspect(obj).attrs[a.key].history.has_changes()]
        if changed or obj in session.deleted or obj in session.new:
            raise ValueError("Completed orders and product instances are read-only. Create a change order from the installed asset.")


class AssetChange(Base):
    __tablename__ = "asset_changes"
    __table_args__ = (Index("uq_asset_pending_change", "asset_id", unique=True, sqlite_where=text("status = 'submitted'"), postgresql_where=text("status = 'submitted'")),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"))
    asset_id: Mapped[int] = mapped_column(ForeignKey("assets.id"))
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id"), unique=True)
    operation: Mapped[str] = mapped_column(String(30))
    base_version: Mapped[int] = mapped_column(Integer)
    requested_json: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(30), default="submitted")
    orchestration_ref: Mapped[str] = mapped_column(String(100))
    created_at: Mapped[dt.datetime] = mapped_column(default=utcnow)
    # Siebel open orders: a future-dated change waits ("queued") behind the open
    # one it was built on, and becomes the open ("submitted") one when that is fulfilled
    due_date: Mapped[dt.date | None] = mapped_column(Date, nullable=True)
    after_change_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    revision_of_change_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    values_json: Mapped[str | None] = mapped_column(Text, nullable=True)  # what was asked for - a revision starts from it
    reason: Mapped[str | None] = mapped_column(String(300), nullable=True)
    completed_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)


class AssetQuote(Base):
    """A priced change of an installed service, quoted before it is ordered
    (Siebel: Modify on an asset makes a quote; the quote becomes the change order)."""
    __tablename__ = "asset_quotes"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"))
    asset_id: Mapped[int] = mapped_column(ForeignKey("assets.id"))
    quote_number: Mapped[str] = mapped_column(String(30), default="")
    operation: Mapped[str] = mapped_column(String(30))
    base_version: Mapped[int] = mapped_column(Integer)
    values_json: Mapped[str] = mapped_column(Text, default="{}")
    preview_json: Mapped[str] = mapped_column(Text, default="{}")
    status: Mapped[str] = mapped_column(String(20), default="quoted")  # quoted | ordered | expired | cancelled
    valid_until: Mapped[dt.date] = mapped_column(Date)
    order_id: Mapped[int | None] = mapped_column(ForeignKey("orders.id"), nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(default=utcnow)


class AssetRevision(Base):
    __tablename__ = "asset_revisions"
    __table_args__ = (UniqueConstraint("asset_id", "version"), UniqueConstraint("order_item_id"))
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"))
    asset_id: Mapped[int] = mapped_column(ForeignKey("assets.id"))
    order_item_id: Mapped[int] = mapped_column(ForeignKey("order_items.id"))
    version: Mapped[int] = mapped_column(Integer)
    snapshot_json: Mapped[str] = mapped_column(Text)
    created_at: Mapped[dt.datetime] = mapped_column(default=utcnow)

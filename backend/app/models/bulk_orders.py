"""Bulk orders (orchestration/bulk_orders.py): many orders in one request - each row runs the same
single-transaction process an agent would run by hand, intent by intent through the intent pipeline
(checks, approvals, Flow Studio flows, audit), and is followed until its fulfilment completes, its
services are installed as assets and billing has started.

  BulkOrder     the request: which process, the values every row shares, who submitted it (rows run
                as that person, with their position and data scope), whether to approve orders and
                send them to fulfilment along the way, and progress counts
  BulkOrderRow  one order to make: its values, the stage it has reached, the customer and orders it
                made, the assets and billing it ended with, every intent it ran, and why it stopped
"""

from __future__ import annotations

import datetime as dt

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.timeutils import utcnow

PROCESSES = ("b2c_new_connect", "b2b_quote_order", "mobile_lines", "asset_change")
BULK_STATUSES = ("draft", "running", "completed", "completed_with_errors", "cancelled")
# a row's stages, in the order a row moves through them
ROW_STAGES = ("invalid", "queued", "ordering", "awaiting_discount_approval", "awaiting_order_approval", "ready_for_approval",
              "ready_for_fulfillment", "in_fulfillment", "fallout", "completed", "failed", "cancelled")
FINAL_STAGES = ("invalid", "completed", "failed", "cancelled")


class BulkOrder(Base):
    __tablename__ = "bulk_orders"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"), index=True)
    number: Mapped[str] = mapped_column(String(30), default="")
    name: Mapped[str] = mapped_column(String(200), default="")
    process: Mapped[str] = mapped_column(String(30))
    status: Mapped[str] = mapped_column(String(25), default="draft")
    defaults: Mapped[dict | None] = mapped_column(JSON, nullable=True)  # values every row gets unless it says otherwise
    auto_approve: Mapped[bool] = mapped_column(Boolean, default=True)  # approve each booked order (Order Control, INT-25)
    send_to_fulfillment: Mapped[bool] = mapped_column(Boolean, default=True)  # hand orders no pattern took to Fulfillment (INT-117)
    submitted_by: Mapped[int | None] = mapped_column(Integer, nullable=True)
    position_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    data_scope: Mapped[str | None] = mapped_column(String(30), nullable=True)
    source: Mapped[str] = mapped_column(String(20), default="api")  # csv / api / intent
    counts: Mapped[dict | None] = mapped_column(JSON, nullable=True)  # rows per stage
    created_at: Mapped[dt.datetime] = mapped_column(default=utcnow)
    started_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)
    finished_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)


class BulkOrderRow(Base):
    __tablename__ = "bulk_order_rows"
    __table_args__ = (UniqueConstraint("bulk_order_id", "row_no", name="uq_bulk_order_row"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"), index=True)
    bulk_order_id: Mapped[int] = mapped_column(ForeignKey("bulk_orders.id"), index=True)
    row_no: Mapped[int] = mapped_column(Integer)
    data: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    stage: Mapped[str] = mapped_column(String(30), default="queued", index=True)
    party_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    order_ids: Mapped[list | None] = mapped_column(JSON, nullable=True)
    asset_ids: Mapped[list | None] = mapped_column(JSON, nullable=True)
    billing: Mapped[dict | None] = mapped_column(JSON, nullable=True)  # what billing initiation answered, per order
    runs: Mapped[list | None] = mapped_column(JSON, nullable=True)  # [{"intent", "execution_id", "status", "at", "note"}]
    message: Mapped[str] = mapped_column(Text, default="")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    updated_at: Mapped[dt.datetime] = mapped_column(default=utcnow, onupdate=utcnow)

"""Phase 5: the real B2B order-fulfillment lifecycle, additive alongside
the existing immediate-activation path (INT-07) - a quote that has gone
through a signed Proposal and an Order Control Unit approval decomposes
into a sequence of WorkOrders (see app/models/assurance.py's WorkOrder,
extended with `kind`/`technical_parameters`/`order_item_id` here) rather
than activating in one call. Closing the final work order creates an
Asset - the CRM's own lightweight "CMDB" copy of what the primary
inventory/CMDB system (ServiceNow-style Configuration Item, TMF639
Resource) holds, kept in sync via inventory_adapter.sync_asset_to_inventory.
"""

from __future__ import annotations

import datetime as dt
import enum

from sqlalchemy import DateTime, Enum, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.timeutils import utcnow


class ProposalStatus(str, enum.Enum):
    sent = "sent"
    customer_approved = "customer_approved"
    customer_rejected = "customer_rejected"


class Proposal(Base):
    """A proposal document generated from an approved Quote and sent to
    the customer - the customer's decision normally arrives over email,
    outside this system, so "Record customer approval of the proposal"
    (INT-23) is a human recording what the customer said, same convention
    as identity_service's human-mediated external steps.
    """

    __tablename__ = "proposals"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"))
    quote_id: Mapped[int] = mapped_column(ForeignKey("quotes.id"))
    status: Mapped[ProposalStatus] = mapped_column(Enum(ProposalStatus), default=ProposalStatus.sent)
    document_ref: Mapped[str] = mapped_column(String(200))
    sent_at: Mapped[dt.datetime] = mapped_column(default=utcnow)
    decided_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)


class AssetStatus(str, enum.Enum):
    active = "active"
    suspended = "suspended"
    decommissioned = "decommissioned"


class Asset(Base):
    """The CRM-local copy of a provisioned service/CPE/circuit under a
    ServiceAccount - created once fulfillment actually completes (the
    third work order closes), which is also the moment a prospect Party
    becomes a paying customer. inventory_system_ref is this asset's id
    in the real external inventory/CMDB system (inventory_adapter);
    this row is the CRM's own queryable copy, same relationship ServiceNow's
    CRM apps have to its own CMDB.
    """

    __tablename__ = "assets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"))
    service_account_id: Mapped[int] = mapped_column(ForeignKey("service_accounts.id"))
    order_item_id: Mapped[int | None] = mapped_column(ForeignKey("order_items.id"), nullable=True)
    asset_type: Mapped[str] = mapped_column(String(100))  # e.g. "connection", "CPE", "circuit"
    identifier: Mapped[str] = mapped_column(String(200))
    status: Mapped[AssetStatus] = mapped_column(Enum(AssetStatus), default=AssetStatus.active)
    inventory_system_ref: Mapped[str | None] = mapped_column(String(200), nullable=True)
    attributes: Mapped[str] = mapped_column(Text, default="")
    instance_json: Mapped[str] = mapped_column(Text, default="{}")
    version: Mapped[int] = mapped_column(Integer, default=1)
    superseded_by_asset_id: Mapped[int | None] = mapped_column(ForeignKey("assets.id"), nullable=True, index=True)
    revisions: Mapped[list["AssetRevision"]] = relationship(viewonly=True, order_by="AssetRevision.version")
    changes: Mapped[list["AssetChange"]] = relationship(viewonly=True, order_by="AssetChange.id")

    @property
    def order_history(self):
        import json
        history = [{"version": r.version, "order_id": json.loads(r.snapshot_json).get("order_id"),
                    "order_item_id": r.order_item_id, "status": "completed"} for r in self.revisions]
        history.extend({"order_id": c.order_id, "operation": c.operation, "status": c.status,
                        "due_date": c.due_date.isoformat() if c.due_date else None} for c in self.changes)
        return history
    created_at: Mapped[dt.datetime] = mapped_column(default=utcnow)

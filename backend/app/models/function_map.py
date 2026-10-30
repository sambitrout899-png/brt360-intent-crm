"""Versions of a tenant's Functional Map (orchestration/function_map.py).

BRT's original map is code and never stored. A tenant never edits the map
it runs; it makes versions - from the original, from the map in use or from
another version - works on them as drafts, and activates one: activating
re-orchestrates the engines to that version. Exactly one version is active
at a time (none = BRT's original); the one it replaces is kept as retired."""

from __future__ import annotations

import datetime as dt

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.timeutils import utcnow

VERSION_STATUSES = ("draft", "active", "retired")


class FunctionMapVersion(Base):
    __tablename__ = "function_map_versions"
    __table_args__ = (Index("ix_function_map_versions_tenant", "tenant_id", "number"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"))
    number: Mapped[int] = mapped_column(Integer)  # 1, 2, 3 ... per tenant
    # revisions of one version share its number: v3 (0), v3.1, v3.2 ... - Revise makes the next
    revision: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    parent_id: Mapped[int | None] = mapped_column(Integer, nullable=True)  # the revision it was revised from
    name: Mapped[str] = mapped_column(String(150))
    notes: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(20), default="draft")
    based_on: Mapped[str] = mapped_column(String(60), default="original")  # "original", "active map" or "version 3"
    # {"nodes": [{code, label, lane, active, x, dy}], "edges": [{source, target, kind, why, when}],
    #  "base": [[source, target], ...] - BRT's original hand-offs the picture was last reconciled with}
    picture_json: Mapped[str] = mapped_column(Text, default="{}")
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)
    activated_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)
    activated_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)

"""Configurable Customer 360 (INT-33 "Configure 360").

C360Layout - one row per saved version of a tenant's Customer 360 page
(theme, panes, widgets as JSON - see app/c360/catalog.py). The highest
version is live; older ones are the undo history.

DataConnector - an external system the 360 page pulls from: a billing
system, a service desk, network OSS, inventory. It holds how to reach it
(base URL + auth) and, per dataset it serves, the endpoint path and how
its own field names map onto ours (endpoints_json). kind "demo" is a
simulated system for trying the page out before real systems are wired.
The secret is write-only through the API - it is never returned.
"""

from __future__ import annotations

import datetime as dt

from sqlalchemy import Boolean, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.timeutils import utcnow


class C360Layout(Base):
    __tablename__ = "c360_layouts"
    __table_args__ = (UniqueConstraint("tenant_id", "version", name="uq_c360_layouts_tenant_version"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    config_json: Mapped[str] = mapped_column(Text)
    summary: Mapped[str] = mapped_column(Text, default="")
    changed_by: Mapped[str] = mapped_column(String(200), default="")
    created_at: Mapped[dt.datetime] = mapped_column(default=utcnow)


class DataConnector(Base):
    __tablename__ = "data_connectors"
    __table_args__ = (UniqueConstraint("tenant_id", "key", name="uq_data_connectors_tenant_key"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"), index=True)
    key: Mapped[str] = mapped_column(String(60))
    name: Mapped[str] = mapped_column(String(150))
    domain: Mapped[str] = mapped_column(String(30))  # billing / service / network / inventory / sales
    kind: Mapped[str] = mapped_column(String(20), default="rest")  # rest / demo
    base_url: Mapped[str] = mapped_column(String(500), default="")
    auth_type: Mapped[str] = mapped_column(String(20), default="none")  # none / bearer / api_key / basic
    auth_header: Mapped[str] = mapped_column(String(100), default="")  # header name for api_key
    secret: Mapped[str] = mapped_column(Text, default="")
    # {dataset: {"path": "/customers/{party_id}/invoices", "records_path": "data.items", "field_map": {ours: theirs}}}
    endpoints_json: Mapped[str] = mapped_column(Text, default="{}")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    last_test_at: Mapped[dt.datetime | None] = mapped_column(nullable=True)
    last_test_ok: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    last_test_message: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[dt.datetime] = mapped_column(default=utcnow)
    updated_at: Mapped[dt.datetime] = mapped_column(default=utcnow, onupdate=utcnow)

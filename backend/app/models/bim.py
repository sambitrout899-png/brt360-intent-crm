"""BIM - BRT Integration Manager: the only way data is loaded into BRT 360
from outside, in batches (CSV / JSON files) or record by record over the
API (EAI). Every incoming record must carry a BRT_ID that BRT 360 issued
(app/brt_id.py) - see crm_core/bim_service.py.

- BimInterface: a named, reusable load definition - which entity it loads,
  how source columns map to BRT 360 fields, and whether a known BRT_ID
  updates the record (upsert) or is refused (insert only).
- BimJob: one load - a batch file or an API call - with its counts and
  the rows it refused and why.
"""

from __future__ import annotations

import datetime as dt

from sqlalchemy import Boolean, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.timeutils import utcnow


class BimInterface(Base):
    __tablename__ = "bim_interfaces"
    __table_args__ = (UniqueConstraint("tenant_id", "code", name="uq_bim_interfaces_code"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"))
    code: Mapped[str] = mapped_column(String(60))  # what the EAI endpoint is addressed by
    name: Mapped[str] = mapped_column(String(150))
    entity: Mapped[str] = mapped_column(String(40))  # a BIM entity key, e.g. "prospect", "lead", "product"
    mode: Mapped[str] = mapped_column(String(10), default="upsert")  # upsert | insert
    # {"<source column>": "<BRT 360 field>"}; columns not mapped are loaded under their own name
    mapping_json: Mapped[str] = mapped_column(Text, default="{}")
    description: Mapped[str] = mapped_column(Text, default="")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[dt.datetime] = mapped_column(default=utcnow)


class BimJob(Base):
    __tablename__ = "bim_jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"))
    interface_id: Mapped[int | None] = mapped_column(ForeignKey("bim_interfaces.id"), nullable=True)
    entity: Mapped[str] = mapped_column(String(40))
    channel: Mapped[str] = mapped_column(String(10), default="batch")  # batch | api
    file_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    # running | completed | completed_with_errors | failed - 30: "completed_with_errors" is 21
    # characters, which SQLite stored in a String(20) but Postgres refuses
    status: Mapped[str] = mapped_column(String(30), default="running")
    dry_run: Mapped[bool] = mapped_column(Boolean, default=False)
    total: Mapped[int] = mapped_column(Integer, default=0)
    inserted: Mapped[int] = mapped_column(Integer, default=0)
    updated: Mapped[int] = mapped_column(Integer, default=0)
    failed: Mapped[int] = mapped_column(Integer, default=0)
    errors_json: Mapped[str] = mapped_column(Text, default="[]")  # [{"row": n, "brt_id": str, "errors": [str]}]
    started_at: Mapped[dt.datetime] = mapped_column(default=utcnow)
    finished_at: Mapped[dt.datetime | None] = mapped_column(nullable=True)

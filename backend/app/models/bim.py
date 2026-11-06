"""BIM - BRT Integration Manager: the only way data is loaded into BRT 360
from outside, in batches (CSV / JSON files) or record by record over the
API (EAI). Every incoming record carries a BRT_ID that BRT 360 issued
(app/brt_id.py) - see crm_core/bim_service.py.

- BimInterface: a named, reusable load definition - which entity it loads,
  how source columns map to BRT 360 fields, and whether a known BRT_ID
  updates the record (upsert) or is refused (insert only). With a key field
  the source system uses its own ids and BRT 360 issues the BRT_IDs.
- BimJob: one load - a batch file or an API call - with its counts and
  the rows it refused and why.
- BimExternalRef: which BRT_ID a source system's own id became.
"""

from __future__ import annotations

import datetime as dt

from sqlalchemy import Boolean, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.secret_box import Encrypted
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
    # {"<source column>": "<BRT 360 field>"} or a list of rules with transforms (hub/mapping.py);
    # columns not mapped are loaded under their own name, unless ignore_unmapped
    mapping_json: Mapped[str] = mapped_column(Text, default="{}")
    description: Mapped[str] = mapped_column(Text, default="")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[dt.datetime] = mapped_column(default=utcnow)
    ignore_unmapped: Mapped[bool] = mapped_column(Boolean, default=False)  # a source sends more than we want: leave the rest out
    source_system: Mapped[str] = mapped_column(String(60), default="")  # whose ids key_field holds; blank: the interface's code
    key_field: Mapped[str] = mapped_column(String(200), default="")  # the source's own id: BRT 360 issues and remembers the BRT_ID
    ack_operation_id: Mapped[int | None] = mapped_column(ForeignKey("hub_operations.id", use_alter=True, name="fk_bim_interfaces_ack_op"), nullable=True)  # told how each load went
    # scheduled pull: BRT 360 fetches the records itself
    pull_operation_id: Mapped[int | None] = mapped_column(ForeignKey("hub_operations.id", use_alter=True, name="fk_bim_interfaces_pull_op"), nullable=True)
    pull_every_minutes: Mapped[int] = mapped_column(Integer, default=0)
    pull_records_path: Mapped[str] = mapped_column(String(200), default="")  # where the records are in the answer
    pull_last_at: Mapped[dt.datetime | None] = mapped_column(nullable=True)
    pull_cursor: Mapped[str] = mapped_column(String(100), default="")  # {{cursor}}: when the last good pull started


class BimJob(Base):
    __tablename__ = "bim_jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"))
    interface_id: Mapped[int | None] = mapped_column(ForeignKey("bim_interfaces.id"), nullable=True)
    entity: Mapped[str] = mapped_column(String(40))
    channel: Mapped[str] = mapped_column(String(10), default="batch")  # batch | api | pull
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
    failed_rows_json: Mapped[str] = mapped_column(Encrypted, default="")  # the rows it refused, as sent - to fix and reprocess
    parent_job_id: Mapped[int | None] = mapped_column(Integer, nullable=True)  # the load whose failed rows this one reprocessed
    message_id: Mapped[int | None] = mapped_column(Integer, nullable=True)  # the integration message that started it


class BimExternalRef(Base):
    __tablename__ = "bim_external_refs"
    __table_args__ = (UniqueConstraint("tenant_id", "system", "entity", "external_id", name="uq_bim_external_refs"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"), index=True)
    system: Mapped[str] = mapped_column(String(60))
    entity: Mapped[str] = mapped_column(String(40))
    external_id: Mapped[str] = mapped_column(String(200))
    record_brt_id: Mapped[str] = mapped_column(String(20), index=True)
    created_at: Mapped[dt.datetime] = mapped_column(default=utcnow)

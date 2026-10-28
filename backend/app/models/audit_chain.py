"""The tamper-evident audit chain (app/crm_core/audit_chain.py).

One chain per tenant. Each entry fingerprints one finished record of what
happened - an intent run (intent_execution_log) or a training audit entry -
and links to the entry before it: hash = SHA-256(previous hash + this
entry's fields). Changing a recorded run afterwards breaks its fingerprint;
changing, removing or inserting an entry breaks every hash after it. On
Postgres the table refuses UPDATE and DELETE (migration e2b4c6d8f0a1).
"""

from __future__ import annotations

import datetime as dt

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.timeutils import utcnow


class AuditChainEntry(Base):
    __tablename__ = "audit_chain"
    __table_args__ = (
        Index("ix_audit_chain_tenant_seq", "tenant_id", "seq"),
        UniqueConstraint("source", "source_id", name="uq_audit_chain_source"),  # each record chained once
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"))
    seq: Mapped[int] = mapped_column(Integer)  # 1, 2, 3 ... within the tenant's chain
    at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)
    source: Mapped[str] = mapped_column(String(40))  # intent_execution_log | training_audit
    source_id: Mapped[int] = mapped_column(Integer)
    record_sha256: Mapped[str] = mapped_column(String(64))  # the record's fingerprint when it was chained
    prev_hash: Mapped[str] = mapped_column(String(64))
    hash: Mapped[str] = mapped_column(String(64))

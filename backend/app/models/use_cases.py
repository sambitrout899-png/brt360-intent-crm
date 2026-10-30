"""Use case sets (orchestration/use_cases.py): an administrator's functional
use cases - uploaded on the BRT 360 use case template (Word or RTF), read
into structured use cases, mapped against the Functional Map (what BRT
already does, what is a delta on it, what is a new requirement), reviewed
and edited on screen, committed into a map version, and tested.

    draft      read and mapped; being reviewed
    committed  put into a draft Functional Map version (built when it goes live)
    live       committed and the version activated
    covered    committed with nothing to change - BRT's process already does it all
"""

from __future__ import annotations

import datetime as dt

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.timeutils import utcnow

SET_STATUSES = ("draft", "committed", "live", "covered")


class UseCaseSet(Base):
    __tablename__ = "use_case_sets"
    __table_args__ = (Index("ix_use_case_sets_tenant", "tenant_id", "id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"))
    name: Mapped[str] = mapped_column(String(150))
    filename: Mapped[str] = mapped_column(String(200), default="")
    status: Mapped[str] = mapped_column(String(20), default="draft")
    read_by: Mapped[str] = mapped_column(String(40), default="template")  # template | ai | text
    # [{id, title, business_function, actor, trigger, preconditions, main_flow: [{text, intent, pinned}], ..., mapping}]
    use_cases_json: Mapped[str] = mapped_column(Text, default="[]")
    # the design the last commit verified and implemented (process_architect.verify's shape)
    analysis_json: Mapped[str] = mapped_column(Text, default="{}")
    version_id: Mapped[int | None] = mapped_column(Integer, nullable=True)  # the Functional Map version it was committed into
    # {generated_at, manual: [...], automation: "<pytest module>"}
    tests_json: Mapped[str] = mapped_column(Text, default="{}")
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)
    committed_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)

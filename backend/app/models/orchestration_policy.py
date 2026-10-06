"""Configurable orchestration mode (Phase 5): per-tenant, per-intent-type
choice between the "deterministic" flavor (a fixed Python step sequence -
every intent in this codebase before this model existed) and the
"llm_planned" flavor (an LLM chooses and parameterizes which steps run,
from a fixed catalog of pre-vetted operations - see
app/orchestration/llm_planner.py). The LLM is NEVER given authority to
execute arbitrary code or skip the catalog - it only sequences/
parameterizes functions a human already wrote and tested, same safety
boundary as every other adapter in this codebase. Unconfigured (no row,
or OPENAI_API_KEY unset) always falls back to deterministic - this is
purely additive, same opt-in convention as every other Phase 2-4
primitive.
"""

from __future__ import annotations

import datetime as dt
import enum

from sqlalchemy import Boolean, Enum, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.timeutils import utcnow


class OrchestrationMode(str, enum.Enum):
    deterministic = "deterministic"
    llm_planned = "llm_planned"


class OrchestrationPolicy(Base):
    __tablename__ = "orchestration_policies"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"))
    # The intent type this policy governs, e.g. "INT-25" - or "ALL" to
    # cover every intent type that has a registered operations catalog
    # (see llm_planner.CATALOGS) and no more specific row.
    intent_type: Mapped[str] = mapped_column(String(20))
    mode: Mapped[OrchestrationMode] = mapped_column(Enum(OrchestrationMode), default=OrchestrationMode.deterministic)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[dt.datetime] = mapped_column(default=utcnow)

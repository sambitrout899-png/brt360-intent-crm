"""Phase 6: tenant-configurable AI key/model for the intent classifier's
LLM path (app/intent_engine/classifier.py) - the same "admin-configurable
over hardcoded" convention as every other engine primitive in this
codebase (ApprovalChainTemplate, SlaPolicy, OrchestrationPolicy, ...).
One row per tenant; unconfigured (no row, or is_active False, or
api_key blank) falls back to the global OPENAI_API_KEY in app.config,
which itself may be unset - in which case the classifier falls back
further, to the deterministic rule-based parser. Never a hard failure.
"""

from __future__ import annotations

import datetime as dt

from sqlalchemy import Boolean, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.timeutils import utcnow


class AiSettings(Base):
    __tablename__ = "ai_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"), unique=True)
    api_key: Mapped[str | None] = mapped_column(String(300), nullable=True)
    model: Mapped[str] = mapped_column(String(100), default="gpt-4o-mini")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[dt.datetime] = mapped_column(default=utcnow)
    updated_at: Mapped[dt.datetime] = mapped_column(default=utcnow, onupdate=utcnow)

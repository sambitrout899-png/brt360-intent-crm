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

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String
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
    # Which model powers this tenant's AI: external (api_key/model above),
    # local (the platform's own model on this server) or rules (none).
    # None = the platform default (app.config.default_llm_provider).
    provider: Mapped[str | None] = mapped_column(String(20), nullable=True)
    local_model: Mapped[str | None] = mapped_column(String(100), nullable=True)
    # Use the local model for as-you-type suggestions too (slow on CPU).
    local_live_assist: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[dt.datetime] = mapped_column(default=utcnow)
    updated_at: Mapped[dt.datetime] = mapped_column(default=utcnow, onupdate=utcnow)


class AiCall(Base):
    """One model call through the AI gateway (app/services/ai_gateway.py) -
    the prompt audit, and what a tenant's monthly token budget is counted
    from. The prompt itself is never stored: its SHA-256 shows which calls
    sent the same text, its length how much, `redactions` how many personal
    details were masked before it left the server."""

    __tablename__ = "ai_calls"
    __table_args__ = (Index("ix_ai_calls_tenant_at", "tenant_id", "at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int | None] = mapped_column(ForeignKey("tenants.id"), nullable=True)  # None: a platform call
    at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)
    provider: Mapped[str] = mapped_column(String(20))
    model: Mapped[str] = mapped_column(String(100), default="")
    # ok | limited | over_budget | no_key | timeout | unreachable | http_NNN
    outcome: Mapped[str] = mapped_column(String(20))
    prompt_tokens: Mapped[int] = mapped_column(Integer, default=0)
    completion_tokens: Mapped[int] = mapped_column(Integer, default=0)
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)
    prompt_sha256: Mapped[str] = mapped_column(String(64), default="")
    prompt_chars: Mapped[int] = mapped_column(Integer, default=0)
    redactions: Mapped[int] = mapped_column(Integer, default=0)

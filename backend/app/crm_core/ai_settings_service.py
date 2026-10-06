"""Resolves which OpenAI API key/model governs the intent classifier's
LLM path for one tenant - see app/models/ai_settings.py. A tenant's own
configured key always wins when present and active; otherwise the
global app.config key (the one this whole platform shipped with) is
used; otherwise there is no key at all, and the classifier itself falls
back to the deterministic rule-based parser. This resolution is always
safe - it never raises, it only ever returns a key or None.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.config import get_settings
from app.models.ai_settings import AiSettings


def resolve_api_key_and_model(db: Session, tenant_id: int) -> tuple[str | None, str]:
    settings = get_settings()
    row = (
        db.query(AiSettings)
        .filter(AiSettings.tenant_id == tenant_id, AiSettings.is_active.is_(True))
        .first()
    )
    if row is not None and row.api_key:
        return row.api_key, row.model or settings.openai_model
    return settings.openai_api_key, settings.openai_model


def get_or_create(db: Session, tenant_id: int) -> AiSettings:
    row = db.query(AiSettings).filter(AiSettings.tenant_id == tenant_id).first()
    if row is None:
        row = AiSettings(tenant_id=tenant_id)
        db.add(row)
        db.flush()
    return row


def mask_key(api_key: str | None) -> str | None:
    if not api_key:
        return None
    if len(api_key) <= 8:
        return "*" * len(api_key)
    return f"{api_key[:4]}{'*' * 8}{api_key[-4:]}"

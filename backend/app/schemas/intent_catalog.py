from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from app.schemas.intent import IntentContext


class IntentFieldIn(BaseModel):
    key: str = ""
    label: str
    type: str = "text"
    required: bool = False
    help: str = ""
    placeholder: str = ""
    values: list[str] = []
    source: str = ""
    record_type: str = ""


class IntentCreateIn(BaseModel):
    label: str = Field(min_length=2, max_length=150)
    description: str = ""
    # What should happen when it runs - drafted into the intent's flow by AI.
    behaviour: str = ""
    category: str = "custom"
    icon: str = "✨"
    entity_type: str | None = None
    phrases: list[str] = []
    fields: list[IntentFieldIn] = []
    role_ids: list[int] = []
    is_active: bool = True


class IntentUpdateIn(BaseModel):
    label: str | None = None
    description: str | None = None
    category: str | None = None
    icon: str | None = None
    is_active: bool | None = None
    role_ids: list[int] | None = None
    phrases: list[str] | None = None
    defaults: dict[str, Any] | None = None
    fields: list[IntentFieldIn] | None = None  # custom intents only


class IntentPrepareIn(BaseModel):
    intent_type: str
    entities: dict[str, Any] = {}
    text: str = ""
    context: IntentContext | None = None

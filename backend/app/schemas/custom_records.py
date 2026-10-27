from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class RecordTypeIn(BaseModel):
    label: str = Field(min_length=1, max_length=100)
    plural_label: str | None = Field(default=None, max_length=100)
    key: str | None = Field(default=None, max_length=28)
    description: str = ""
    icon: str | None = Field(default=None, max_length=10)
    number_prefix: str | None = Field(default=None, max_length=10)
    parent_entity_type: str | None = None
    parent_required: bool = False


class RecordTypeUpdate(BaseModel):
    """Only the fields sent are changed."""

    label: str | None = Field(default=None, min_length=1, max_length=100)
    plural_label: str | None = Field(default=None, max_length=100)
    description: str | None = None
    icon: str | None = Field(default=None, max_length=10)
    number_prefix: str | None = Field(default=None, max_length=10)
    parent_entity_type: str | None = None
    parent_required: bool | None = None
    is_active: bool | None = None


class CustomRecordIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    parent_id: int | None = None
    fields: dict[str, Any] = Field(default_factory=dict)


class CustomRecordUpdate(BaseModel):
    """Only the fields sent are changed; parent_id: null unlinks the record."""

    name: str | None = Field(default=None, min_length=1, max_length=200)
    parent_id: int | None = None
    fields: dict[str, Any] = Field(default_factory=dict)

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
    name_label: str | None = Field(default=None, max_length=100)
    list_fields: list[str] | str | None = None


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
    name_label: str | None = Field(default=None, max_length=100)
    list_fields: list[str] | str | None = None


class CustomRecordIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    # the record it hangs off - its id, number or name
    parent_id: int | str | None = None
    fields: dict[str, Any] = Field(default_factory=dict)
    page_key: str | None = None


class CustomRecordUpdate(BaseModel):
    """Only the fields sent are changed; parent_id: null unlinks the record."""

    name: str | None = Field(default=None, min_length=1, max_length=200)
    parent_id: int | str | None = None
    fields: dict[str, Any] = Field(default_factory=dict)
    page_key: str | None = None


class RelationshipIn(BaseModel):
    from_entity_type: str
    to_entity_type: str
    label: str = Field(default="", max_length=100)
    reverse_label: str = Field(default="", max_length=100)
    key: str | None = Field(default=None, max_length=60)
    description: str = ""


class RelationshipUpdate(BaseModel):
    label: str | None = Field(default=None, max_length=100)
    reverse_label: str | None = Field(default=None, max_length=100)
    description: str | None = None


class DesignIn(BaseModel):
    text: str = Field(default="", max_length=8000)
    # a plan the admin reviewed and edited (record_designer.check form) - built as it is
    plan: dict[str, Any] | None = None


class TransitionIn(BaseModel):
    state: str = Field(min_length=1, max_length=100)  # a state's code or label, or a move's name
    reason: str = ""


class AssignIn(BaseModel):
    to_user: str | None = None
    to_position: str | None = None
    to_department: str | None = None
    reason: str = ""


class LinkIn(BaseModel):
    relationship: str
    related: str | int  # the other record's id, number or name
    unlink: bool = False
    note: str = ""

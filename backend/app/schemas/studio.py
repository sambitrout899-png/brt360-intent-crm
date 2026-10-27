"""Schemas for Phase 7: state models, positions, assignment rules, custom
fields, AI Flow Studio, and the generic record lifecycle view."""

from __future__ import annotations

import datetime as dt
from typing import Any

from pydantic import BaseModel, Field

CODE_PATTERN = r"^[a-z][a-z0-9_]*$"


class StateIn(BaseModel):
    code: str = Field(min_length=1, max_length=50, pattern=CODE_PATTERN)
    label: str = Field(default="", max_length=100)
    editable: bool = True
    is_terminal: bool = False


class TransitionIn(BaseModel):
    from_state: str = Field(min_length=1, max_length=50)
    to_state: str = Field(min_length=1, max_length=50)
    action: str = Field(min_length=1, max_length=50, pattern=CODE_PATTERN)
    intent_type: str | None = None
    allowed_roles: str = ""


class StateModelIn(BaseModel):
    name: str = Field(min_length=1, max_length=150)
    states: list[StateIn] = Field(min_length=1)
    transitions: list[TransitionIn] = []


class StateOut(BaseModel):
    code: str
    label: str
    editable: bool
    is_terminal: bool
    built_in: bool


class TransitionOut(BaseModel):
    from_state: str
    to_state: str
    action: str
    intent_type: str | None
    allowed_roles: str


class StateModelOut(BaseModel):
    entity_type: str
    name: str
    configured: bool
    built_in_states: list[str]
    states: list[StateOut]
    transitions: list[TransitionOut]


class PositionIn(BaseModel):
    name: str = Field(min_length=1, max_length=150)
    parent_position_id: int | None = None
    department_id: int | None = None
    data_scope: str = "own"
    user_ids: list[int] = []


class PositionUpdate(BaseModel):
    name: str = Field(min_length=1, max_length=150)
    parent_position_id: int | None = None
    department_id: int | None = None
    data_scope: str = "own"


class PositionMembersIn(BaseModel):
    user_ids: list[int]


class PositionOut(BaseModel):
    id: int
    name: str
    parent_position_id: int | None
    department_id: int | None = None
    data_scope: str = "own"
    is_active: bool
    members: list[dict[str, Any]]


class AssignmentRuleIn(BaseModel):
    name: str = Field(min_length=1, max_length=150)
    entity_type: str
    on_state: str | None = None
    strategy: str = "position"  # position | round_robin | user | department
    position_id: int | None = None
    user_id: int | None = None
    department_id: int | None = None
    priority: int = 100


class AssignmentRuleOut(BaseModel):
    id: int
    name: str
    entity_type: str
    on_state: str | None
    strategy: str
    position_id: int | None
    user_id: int | None
    department_id: int | None = None
    priority: int
    is_active: bool

    model_config = {"from_attributes": True}


class CustomFieldIn(BaseModel):
    entity_type: str
    key: str = Field(min_length=1, max_length=60, pattern=CODE_PATTERN)
    label: str = Field(min_length=1, max_length=150)
    data_type: str = "text"
    allowed_values: str | None = None
    required: bool = False
    default_value: str | None = None


class CustomFieldOut(BaseModel):
    id: int
    entity_type: str
    key: str
    label: str
    data_type: str
    allowed_values: str | None
    required: bool
    default_value: str | None
    is_active: bool

    model_config = {"from_attributes": True}


class FlowIn(BaseModel):
    name: str = Field(min_length=1, max_length=150)
    description: str = ""
    entity_type: str | None = None
    trigger_type: str = "intent"
    trigger_intent_type: str | None = None
    timing: str = "after"
    trigger_state: str | None = None
    trigger_phrases: str | None = None
    required_fields: str | None = None
    graph: dict[str, Any] = Field(default_factory=lambda: {"nodes": [], "edges": []})
    # an error the flow doesn't catch: "fail" (fail the intent too) | "continue" (undo only this flow)
    on_error: str = "fail"


class FlowOut(BaseModel):
    id: int
    name: str
    description: str
    entity_type: str | None
    trigger_type: str
    trigger_intent_type: str | None
    timing: str
    trigger_state: str | None
    trigger_phrases: str | None
    required_fields: str | None
    graph: dict[str, Any]
    on_error: str = "fail"
    version: int
    is_active: bool
    intent_code: str | None
    validation_errors: list[str]
    # Field/value references the Studio could not place (see flow_schema).
    warnings: list[str] = []
    # Fixes applied automatically by the save that returned this.
    corrections: list[dict[str, Any]] = []
    updated_at: dt.datetime


class FlowGenerateIn(BaseModel):
    prompt: str = Field(min_length=3, max_length=4000)
    entity_type: str | None = None
    trigger_type: str = "intent"
    trigger_intent_type: str | None = None
    required_fields: str | None = None


class FlowReviewIn(BaseModel):
    entity_type: str | None = None
    trigger_type: str = "intent"
    trigger_intent_type: str | None = None
    required_fields: str | None = None
    graph: dict[str, Any]


class FlowReviewOut(BaseModel):
    graph: dict[str, Any]
    corrections: list[dict[str, Any]]
    issues: list[dict[str, Any]]


class FlowGenerateOut(BaseModel):
    graph: dict[str, Any]
    source: str  # "llm" | "heuristic"
    notes: str
    # When the description said when the flow runs ("When the order gets approved ...").
    trigger: dict[str, Any] | None = None
    validation_errors: list[str]
    corrections: list[dict[str, Any]] = []
    issues: list[dict[str, Any]] = []


class FlowSimulateIn(BaseModel):
    entity_id: int | None = None
    entities: dict[str, Any] = {}
    result: dict[str, Any] = {}


class FlowSimulateOut(BaseModel):
    ok: bool
    error: str | None
    # the run up to where it stopped - its trace shows the failing step
    run: dict[str, Any] | None
    # the failure, classified: code, category, message, node (see flow_errors)
    error_detail: dict[str, Any] | None = None


class RecordHistoryOut(BaseModel):
    kind: str  # "state" | "assignment"
    summary: str
    source: str
    created_at: dt.datetime


class RecordLifecycleOut(BaseModel):
    entity_type: str
    entity_id: int
    title: str
    brt_id: str | None = None
    sys_created_at: dt.datetime | None = None
    sys_created_by: dict | None = None
    state: str
    state_label: str
    built_in_status: str
    editable: bool
    is_terminal: bool
    state_model_name: str
    available_actions: list[dict[str, Any]]
    # The record's buttons (crm_core/record_actions.py) - the same everywhere it is shown.
    actions: list[dict[str, Any]] = []
    # The approval it waits for, if any, and whether the viewer may decide it (approval_service.waiting_on).
    approval: dict[str, Any] | None = None
    owner_user: dict[str, Any] | None
    owner_position: dict[str, Any] | None
    owner_department: dict[str, Any] | None = None
    core_fields: dict[str, Any]
    updatable_core_fields: list[str]
    core_field_labels: dict[str, str] = {}
    core_field_options: dict[str, list[str]] = {}
    custom_fields: list[dict[str, Any]]
    history: list[RecordHistoryOut]
    revisions: list[dict[str, Any]] = []


class AssigneesOut(BaseModel):
    users: list[dict[str, Any]]
    positions: list[dict[str, Any]]
    departments: list[dict[str, Any]] = []


class MyWorkItemOut(BaseModel):
    entity_type: str
    entity_id: int
    title: str
    state: str
    via_position: str | None
    updated_at: dt.datetime
    # a work order: the record to open for it (its order)
    open_entity_type: str | None = None
    open_entity_id: int | None = None

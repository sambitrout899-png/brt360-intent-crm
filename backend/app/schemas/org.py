from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class DepartmentIn(BaseModel):
    name: str = Field(min_length=1, max_length=150)
    parent_department_id: int | None = None


class DepartmentOut(BaseModel):
    id: int
    name: str
    parent_department_id: int | None
    is_active: bool

    model_config = {"from_attributes": True}


class AppRoleIn(BaseModel):
    name: str = Field(min_length=1, max_length=150)
    description: str = ""
    pages: list[str] = []


class AppRoleOut(BaseModel):
    id: int
    name: str
    description: str
    pages: list[str]
    is_active: bool
    user_count: int


class UserAccessIn(BaseModel):
    role_ids: list[int] = []
    position_ids: list[int] = []
    primary_position_id: int | None = None


class UserAccessOut(BaseModel):
    id: int
    name: str
    email: str
    role: str  # built-in permission level (admin/agent/viewer)
    is_active: bool
    role_ids: list[int]
    position_ids: list[int]
    primary_position_id: int | None
    pages: list[str]


class AccessCatalogOut(BaseModel):
    pages: list[dict[str, str]]
    data_scopes: list[dict[str, str]]


class MyAccessOut(BaseModel):
    """What the signed-in user gets, for the position/scope in effect."""

    user: dict[str, Any]
    pages: list[str]
    roles: list[dict[str, Any]]
    positions: list[dict[str, Any]]
    active_position_id: int | None
    max_scope: str
    scope: str
    available_scopes: list[dict[str, str]]

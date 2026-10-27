"""Org structure + access model: Organization (the tenant) -> Departments ->
Positions -> Users, and Roles -> pages.

Two independent axes decide what a user gets:
- Roles (AppRole, many per user) decide which pages/views they see - the
  union of every role's pages.
- Positions (lifecycle.Position, many per user, one primary) decide which
  data records those pages return. Each position has a department and a
  maximum data scope (see crm_core/access_service.py: own < team <
  department < department_tree < org); the user works "as" one position
  at a time - the primary one by default.
"""

from __future__ import annotations

import datetime as dt

from sqlalchemy import Boolean, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.timeutils import utcnow


class Department(Base):
    __tablename__ = "departments"
    __table_args__ = (UniqueConstraint("tenant_id", "name", name="uq_departments_tenant_name"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"))
    name: Mapped[str] = mapped_column(String(150))
    parent_department_id: Mapped[int | None] = mapped_column(ForeignKey("departments.id"), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[dt.datetime] = mapped_column(default=utcnow)


class AppRole(Base):
    """A tenant-defined role: a named bundle of pages ("Sales Executive" ->
    dashboard, mywork, customer, pipeline). Distinct from User.role, the
    coarse built-in admin/agent/viewer permission level."""

    __tablename__ = "app_roles"
    __table_args__ = (UniqueConstraint("tenant_id", "name", name="uq_app_roles_tenant_name"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"))
    name: Mapped[str] = mapped_column(String(150))
    description: Mapped[str] = mapped_column(Text, default="")
    pages: Mapped[str] = mapped_column(Text, default="")  # comma-separated page keys
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[dt.datetime] = mapped_column(default=utcnow)


class UserAppRole(Base):
    __tablename__ = "user_app_roles"
    __table_args__ = (UniqueConstraint("user_id", "role_id", name="uq_user_app_roles"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    role_id: Mapped[int] = mapped_column(ForeignKey("app_roles.id"))

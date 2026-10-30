"""Change control for a tenant's configuration (orchestration/change_control.py).

Several administrators can develop at once without overwriting each other:
each works in a workspace; an object they change (a Flow Studio flow, a draft
version of the Functional Map ...) is locked to that workspace until its work
is released or discarded; every save is a numbered revision of the object;
and only the tenant's root administrator deploys a release - a bundle of
submitted workspaces - to the live configuration, and can roll it back."""

from __future__ import annotations

import datetime as dt

from sqlalchemy import JSON, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.timeutils import utcnow

WORKSPACE_STATUSES = ("open", "submitted", "released", "discarded")
RELEASE_STATUSES = ("draft", "deployed", "rolled_back")


class Workspace(Base):
    __tablename__ = "change_workspaces"
    __table_args__ = (Index("ix_change_workspaces_tenant", "tenant_id", "status"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"))
    name: Mapped[str] = mapped_column(String(150))
    description: Mapped[str] = mapped_column(Text, default="")
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    status: Mapped[str] = mapped_column(String(20), default="open")
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)
    submitted_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)
    release_id: Mapped[int | None] = mapped_column(Integer, nullable=True)


class ObjectLock(Base):
    """One object checked out to one workspace - nobody else changes it meanwhile."""

    __tablename__ = "change_locks"
    __table_args__ = (UniqueConstraint("tenant_id", "object_type", "object_key", name="uq_change_locks_object"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"))
    object_type: Mapped[str] = mapped_column(String(40))  # flow | map_version | ...
    object_key: Mapped[str] = mapped_column(String(80))  # the live object's id, or new-<n> for one made in the workspace
    workspace_id: Mapped[int | None] = mapped_column(ForeignKey("change_workspaces.id"), nullable=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    locked_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)


class ObjectRevision(Base):
    """Every save of an object in a workspace: what it is now (snapshot) and,
    on its first revision, what it was live (base - for the release's rollback)."""

    __tablename__ = "change_revisions"
    __table_args__ = (Index("ix_change_revisions_object", "tenant_id", "object_type", "object_key"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"))
    object_type: Mapped[str] = mapped_column(String(40))
    object_key: Mapped[str] = mapped_column(String(80))
    number: Mapped[int] = mapped_column(Integer)  # the object's revision 1, 2, 3 ...
    workspace_id: Mapped[int] = mapped_column(ForeignKey("change_workspaces.id"))
    label: Mapped[str] = mapped_column(String(200), default="")
    snapshot_json: Mapped[str] = mapped_column(Text)  # {"deleted": true} = deleted in the workspace
    base_json: Mapped[str | None] = mapped_column(Text, nullable=True)  # null = made in the workspace
    status: Mapped[str] = mapped_column(String(20), default="draft")  # draft | released | discarded
    author_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)


class Release(Base):
    __tablename__ = "change_releases"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"))
    number: Mapped[int] = mapped_column(Integer)
    name: Mapped[str] = mapped_column(String(150))
    status: Mapped[str] = mapped_column(String(20), default="draft")
    workspace_ids: Mapped[list[int]] = mapped_column(JSON, default=list)
    map_version_id: Mapped[int | None] = mapped_column(Integer, nullable=True)  # a Functional Map version it activates
    created_by_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)
    deployed_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    deployed_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)
    # what deploying changed, to undo it: [{"type", "key", "before": snapshot | null, "after_key"}], and the map version it replaced
    undo_json: Mapped[str] = mapped_column(Text, default="{}")
    rolled_back_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)

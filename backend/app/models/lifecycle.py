"""Phase 7 - entity lifecycle: configurable state models, record ownership
(assignment/transfer to users or positions), and the history of both.

Everything here is polymorphic (entity_type + entity_id, same pattern as
ApprovalChainInstance/SlaClock) so one engine covers party (customer/
prospect), lead, opportunity, quote and order without a column on each.

State model semantics (see crm_core/lifecycle_service.py):
- Each entity's built-in status (Quote.status, Lead.status, ...) stays the
  source of truth for the core intents that already drive it.
- A tenant's StateModel describes those statuses - which ones are still
  editable (save/modify allowed) and which are locked (only revise/cancel
  style transitions remain) - and may add custom states. A custom state is
  a *sub-state* anchored to the built-in status it was entered from
  (EntityRecordMeta.sub_state/sub_state_anchor): once a core intent moves
  the built-in status on, the sub-state is superseded automatically.
- No StateModel configured for an entity type falls back to the code
  defaults in lifecycle_service.DEFAULT_STATE_MODELS, same "opt-in, safe
  fallback" philosophy as PONR/approval chains.
"""

from __future__ import annotations

import datetime as dt

from sqlalchemy import Boolean, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.timeutils import utcnow


class StateModel(Base):
    __tablename__ = "state_models"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"))
    entity_type: Mapped[str] = mapped_column(String(30))
    name: Mapped[str] = mapped_column(String(150))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[dt.datetime] = mapped_column(default=utcnow)
    updated_at: Mapped[dt.datetime] = mapped_column(default=utcnow, onupdate=utcnow)

    states: Mapped[list["StateModelState"]] = relationship(
        back_populates="state_model", cascade="all, delete-orphan", order_by="StateModelState.sequence"
    )
    transitions: Mapped[list["StateModelTransition"]] = relationship(
        back_populates="state_model", cascade="all, delete-orphan", order_by="StateModelTransition.id"
    )


class StateModelState(Base):
    __tablename__ = "state_model_states"
    __table_args__ = (UniqueConstraint("state_model_id", "code", name="uq_state_model_states_code"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    state_model_id: Mapped[int] = mapped_column(ForeignKey("state_models.id"))
    code: Mapped[str] = mapped_column(String(50))
    label: Mapped[str] = mapped_column(String(100))
    sequence: Mapped[int] = mapped_column(Integer, default=0)
    # Save/modify (INT-29) allowed while in this state. False = locked:
    # past the point where the record can be edited in place.
    editable: Mapped[bool] = mapped_column(Boolean, default=True)
    is_terminal: Mapped[bool] = mapped_column(Boolean, default=False)
    # True for states that are values of the entity's built-in status enum
    # (cannot be removed); False for admin-defined custom sub-states.
    built_in: Mapped[bool] = mapped_column(Boolean, default=False)

    state_model: Mapped[StateModel] = relationship(back_populates="states")


class StateModelTransition(Base):
    __tablename__ = "state_model_transitions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    state_model_id: Mapped[int] = mapped_column(ForeignKey("state_models.id"))
    from_state: Mapped[str] = mapped_column(String(50))  # "*" = from any non-terminal state
    to_state: Mapped[str] = mapped_column(String(50))
    action: Mapped[str] = mapped_column(String(50))  # e.g. "advance", "revise", "cancel", "close_won"
    # Set when a core intent owns this transition (e.g. INT-06 approves a
    # quote) - the generic transition intent (INT-31) refuses it and names
    # the intent to use instead, so business steps are never bypassed.
    intent_type: Mapped[str | None] = mapped_column(String(20), nullable=True)
    # Comma-separated roles allowed to perform it; empty = admin + agent.
    # Text: a list of position names outgrows any fixed length (live data: 152 chars).
    allowed_roles: Mapped[str] = mapped_column(Text, default="")

    state_model: Mapped[StateModel] = relationship(back_populates="transitions")


class Position(Base):
    """An org position (e.g. "Sales Director - North") records can be
    assigned to, independent of whichever users currently hold it."""

    __tablename__ = "positions"
    __table_args__ = (UniqueConstraint("tenant_id", "name", name="uq_positions_tenant_name"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"))
    name: Mapped[str] = mapped_column(String(150))
    # Reporting line - "my team" is this position plus everything below it.
    parent_position_id: Mapped[int | None] = mapped_column(ForeignKey("positions.id"), nullable=True)
    department_id: Mapped[int | None] = mapped_column(ForeignKey("departments.id"), nullable=True)
    # The widest data the holder may see (access_service.DATA_SCOPES).
    data_scope: Mapped[str] = mapped_column(String(30), default="own")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[dt.datetime] = mapped_column(default=utcnow)

    members: Mapped[list["PositionMember"]] = relationship(back_populates="position", cascade="all, delete-orphan")


class PositionMember(Base):
    __tablename__ = "position_members"
    __table_args__ = (UniqueConstraint("position_id", "user_id", name="uq_position_members"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    position_id: Mapped[int] = mapped_column(ForeignKey("positions.id"))
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    # The position the user works as by default (one per user).
    is_primary: Mapped[bool] = mapped_column(Boolean, default=False)

    position: Mapped[Position] = relationship(back_populates="members")


class AssignmentRule(Base):
    """Pre-configured assignment the workflow follows: when a record of
    entity_type enters on_state (None = when first seen/created), assign
    it to a fixed user, or to a position (optionally picking one of the
    position's members round-robin)."""

    __tablename__ = "assignment_rules"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"))
    name: Mapped[str] = mapped_column(String(150))
    entity_type: Mapped[str] = mapped_column(String(30))
    on_state: Mapped[str | None] = mapped_column(String(50), nullable=True)
    position_id: Mapped[int | None] = mapped_column(ForeignKey("positions.id"), nullable=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    department_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    strategy: Mapped[str] = mapped_column(String(20), default="position")  # position | round_robin | user | department
    priority: Mapped[int] = mapped_column(Integer, default=100)  # lower wins
    last_assigned_user_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[dt.datetime] = mapped_column(default=utcnow)


class EntityRecordMeta(Base):
    """Per-record lifecycle bookkeeping: owner, custom sub-state, and the
    last state the lifecycle hook saw (so it can detect state entry)."""

    __tablename__ = "entity_record_meta"
    __table_args__ = (UniqueConstraint("tenant_id", "entity_type", "entity_id", name="uq_entity_record_meta"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"))
    entity_type: Mapped[str] = mapped_column(String(30))
    entity_id: Mapped[int] = mapped_column(Integer)
    owner_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    owner_position_id: Mapped[int | None] = mapped_column(ForeignKey("positions.id"), nullable=True)
    # Assigned to a whole department (its members see it and find it in My Work).
    owner_department_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    sub_state: Mapped[str | None] = mapped_column(String(50), nullable=True)
    sub_state_anchor: Mapped[str | None] = mapped_column(String(50), nullable=True)
    last_seen_state: Mapped[str | None] = mapped_column(String(50), nullable=True)
    # Who created the record, as which position, in which department - the
    # basis of data visibility (access_service). Stamped when the record is
    # first seen; never changed by reassignment.
    created_by_user_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_by_position_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    department_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(default=utcnow)
    updated_at: Mapped[dt.datetime] = mapped_column(default=utcnow, onupdate=utcnow)


class EntityAssignmentHistory(Base):
    __tablename__ = "entity_assignment_history"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"))
    entity_type: Mapped[str] = mapped_column(String(30))
    entity_id: Mapped[int] = mapped_column(Integer)
    from_user_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    from_position_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    to_user_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    to_position_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    from_department_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    to_department_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    reason: Mapped[str] = mapped_column(Text, default="")
    source: Mapped[str] = mapped_column(String(50), default="manual")  # manual | rule:<id> | flow:<id>
    created_at: Mapped[dt.datetime] = mapped_column(default=utcnow)


class EntityStateHistory(Base):
    __tablename__ = "entity_state_history"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"))
    entity_type: Mapped[str] = mapped_column(String(30))
    entity_id: Mapped[int] = mapped_column(Integer)
    from_state: Mapped[str | None] = mapped_column(String(50), nullable=True)
    to_state: Mapped[str] = mapped_column(String(50))
    source: Mapped[str] = mapped_column(String(50), default="intent")  # intent:<INT-xx> | transition | flow:<id>
    created_at: Mapped[dt.datetime] = mapped_column(default=utcnow)

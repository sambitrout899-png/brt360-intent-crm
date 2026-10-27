"""org structure, roles -> pages, position-driven data visibility

- departments (tree per tenant); positions gain department_id + data_scope
- position_members.is_primary (the position a user works as by default)
- app_roles (pages per role) + user_app_roles
- entity_record_meta.created_by_user_id / created_by_position_id /
  department_id - who created a record, as which position, where

Existing positions get data_scope 'org' so nobody loses sight of data on
upgrade; new positions default to 'own'. Records that existed before this
migration have no creator stamp and are visible at org scope only.

Revision ID: c4d6e8f0a2b4
Revises: b3e5f7a9c1d2
Create Date: 2026-10-07 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c4d6e8f0a2b4'
down_revision: Union[str, Sequence[str], None] = 'b3e5f7a9c1d2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "departments",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=150), nullable=False),
        sa.Column("parent_department_id", sa.Integer(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),
        sa.ForeignKeyConstraint(["parent_department_id"], ["departments.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "name", name="uq_departments_tenant_name"),
    )
    op.create_table(
        "app_roles",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=150), nullable=False),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("pages", sa.Text(), nullable=False, server_default=""),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "name", name="uq_app_roles_tenant_name"),
    )
    op.create_table(
        "user_app_roles",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("role_id", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["role_id"], ["app_roles.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "role_id", name="uq_user_app_roles"),
    )
    with op.batch_alter_table("positions") as batch_op:
        batch_op.add_column(sa.Column("department_id", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("data_scope", sa.String(length=30), nullable=False, server_default="org"))
        batch_op.create_foreign_key("fk_positions_department_id", "departments", ["department_id"], ["id"])
    with op.batch_alter_table("position_members") as batch_op:
        batch_op.add_column(sa.Column("is_primary", sa.Boolean(), nullable=False, server_default=sa.false()))
    with op.batch_alter_table("entity_record_meta") as batch_op:
        batch_op.add_column(sa.Column("created_by_user_id", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("created_by_position_id", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("department_id", sa.Integer(), nullable=True))

    # Each user already in positions gets one primary (their first).
    bind = op.get_bind()
    rows = bind.execute(sa.text("SELECT id, user_id FROM position_members ORDER BY id")).fetchall()
    seen: set[int] = set()
    for member_id, user_id in rows:
        if user_id not in seen:
            seen.add(user_id)
            bind.execute(sa.text("UPDATE position_members SET is_primary = :t WHERE id = :id"), {"t": True, "id": member_id})


def downgrade() -> None:
    with op.batch_alter_table("entity_record_meta") as batch_op:
        batch_op.drop_column("department_id")
        batch_op.drop_column("created_by_position_id")
        batch_op.drop_column("created_by_user_id")
    with op.batch_alter_table("position_members") as batch_op:
        batch_op.drop_column("is_primary")
    with op.batch_alter_table("positions") as batch_op:
        batch_op.drop_constraint("fk_positions_department_id", type_="foreignkey")
        batch_op.drop_column("data_scope")
        batch_op.drop_column("department_id")
    op.drop_table("user_app_roles")
    op.drop_table("app_roles")
    op.drop_table("departments")

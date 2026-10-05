"""sla clocks and policies

Brand new tables for the SLA clock + escalation engine (Phase 1 primitive
#3) - no existing data to migrate, unlike the tenant/approval-chain
migrations before this one.

Revision ID: 6c05cbcc2d0e
Revises: 118e58982bce
Create Date: 2026-10-05 11:33:09.281673

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '6c05cbcc2d0e'
down_revision: Union[str, Sequence[str], None] = '118e58982bce'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

sla_clock_status_enum = sa.Enum("running", "completed", "breached", "cancelled", name="slaclockstatus")


def upgrade() -> None:
    op.create_table(
        "sla_policies",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("clock_type", sa.String(length=100), nullable=False),
        sa.Column("duration_hours", sa.Float(), nullable=False),
        sa.Column("on_breach_action", sa.String(length=50), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "sla_clocks",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("clock_type", sa.String(length=100), nullable=False),
        sa.Column("entity_type", sa.String(length=50), nullable=False),
        sa.Column("entity_id", sa.Integer(), nullable=False),
        sa.Column("on_breach_action", sa.String(length=50), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=False),
        sa.Column("deadline", sa.DateTime(), nullable=False),
        sa.Column("status", sla_clock_status_enum, nullable=False),
        sa.Column("resolved_at", sa.DateTime(), nullable=True),
        sa.Column("detail", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    op.drop_table("sla_clocks")
    op.drop_table("sla_policies")

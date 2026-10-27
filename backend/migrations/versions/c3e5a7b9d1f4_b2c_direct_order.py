"""B2C direct order: a B2C prospect goes straight to an order (no lead,
opportunity or quote) and the Order Module runs its feasibility checks.

Additive: feasibility assessments can belong to an order (order_id) as well
as an opportunity (opportunity_id becomes nullable - every existing row keeps
its opportunity); order details record the channel the order came in on and
the installation address; a new table keeps each order's omnichannel
interactions.

Revision ID: c3e5a7b9d1f4
Revises: b2d4f6a8c0e3
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "c3e5a7b9d1f4"
down_revision: Union[str, Sequence[str], None] = "b2d4f6a8c0e3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("feasibility_assessments") as b:
        b.alter_column("opportunity_id", existing_type=sa.Integer(), nullable=True)
        b.add_column(sa.Column("order_id", sa.Integer(), nullable=True))
    op.create_index("ix_feasibility_assessments_order_id", "feasibility_assessments", ["order_id"])
    with op.batch_alter_table("om_order_details") as b:
        b.add_column(sa.Column("channel", sa.String(20), nullable=True))
        b.add_column(sa.Column("channel_reference", sa.String(100), nullable=True))
        b.add_column(sa.Column("installation_address", sa.String(400), nullable=True))
    op.create_table(
        "om_channel_interactions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.Integer(), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("party_id", sa.Integer(), nullable=True),
        sa.Column("order_id", sa.Integer(), nullable=True),
        sa.Column("channel", sa.String(20), nullable=False),
        sa.Column("kind", sa.String(30), nullable=False),
        sa.Column("reference", sa.String(100), nullable=False),
        sa.Column("note", sa.String(1000), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("at", sa.DateTime(), nullable=False),
        sa.Column("brt_id", sa.String(20), nullable=False),
        sa.Column("sys_created_at", sa.DateTime(), nullable=True),
        sa.Column("sys_created_by", sa.Integer(), nullable=True),
        sa.Column("sys_created_position_id", sa.Integer(), nullable=True),
        sa.Column("sys_department_id", sa.Integer(), nullable=True),
        sa.Column("sys_updated_at", sa.DateTime(), nullable=True),
        sa.Column("sys_updated_by", sa.Integer(), nullable=True),
        sa.UniqueConstraint("brt_id"),
    )
    for column in ("tenant_id", "party_id", "order_id"):
        op.create_index(f"ix_om_channel_interactions_{column}", "om_channel_interactions", [column])


def downgrade() -> None:
    for column in ("order_id", "party_id", "tenant_id"):
        op.drop_index(f"ix_om_channel_interactions_{column}", "om_channel_interactions")
    op.drop_table("om_channel_interactions")
    with op.batch_alter_table("om_order_details") as b:
        for column in ("installation_address", "channel_reference", "channel"):
            b.drop_column(column)
    op.drop_index("ix_feasibility_assessments_order_id", "feasibility_assessments")
    with op.batch_alter_table("feasibility_assessments") as b:
        b.drop_column("order_id")
        b.alter_column("opportunity_id", existing_type=sa.Integer(), nullable=False)

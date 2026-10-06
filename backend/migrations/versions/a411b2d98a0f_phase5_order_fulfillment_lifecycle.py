"""phase 5: order-fulfillment lifecycle (proposal, order control unit,
work-order chain, assets, orchestration mode)

Additive, no backfill-sensitive renames: three brand-new tables
(proposals, assets, orchestration_policies) and three new columns on
the existing work_orders table (kind, technical_parameters,
order_item_id). Every existing work_orders row is a pre-existing
field-dispatch ticket work order, so kind backfills to the literal
'field_dispatch' - exactly its implicit meaning before this column
existed - and technical_parameters backfills to ''.

Revision ID: a411b2d98a0f
Revises: 539d4e4f563a
Create Date: 2026-10-06 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a411b2d98a0f'
down_revision: Union[str, Sequence[str], None] = '539d4e4f563a'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

proposalstatus_enum = sa.Enum("sent", "customer_approved", "customer_rejected", name="proposalstatus")
assetstatus_enum = sa.Enum("active", "decommissioned", name="assetstatus")
orchestrationmode_enum = sa.Enum("deterministic", "llm_planned", name="orchestrationmode")


def upgrade() -> None:
    bind = op.get_bind()

    # --- work_orders: kind / technical_parameters / order_item_id -----
    op.add_column(
        "work_orders",
        sa.Column("kind", sa.String(length=30), nullable=False, server_default="field_dispatch"),
    )
    op.add_column(
        "work_orders",
        sa.Column("technical_parameters", sa.Text(), nullable=False, server_default=""),
    )
    op.add_column("work_orders", sa.Column("order_item_id", sa.Integer(), nullable=True))
    if bind.dialect.name == "postgresql":
        op.create_foreign_key(
            "work_orders_order_item_id_fkey", "work_orders", "order_items", ["order_item_id"], ["id"]
        )
    else:
        with op.batch_alter_table("work_orders") as batch_op:
            batch_op.create_foreign_key("work_orders_order_item_id_fkey", "order_items", ["order_item_id"], ["id"])

    # --- proposals -------------------------------------------------------
    proposalstatus_enum.create(bind, checkfirst=True)
    op.create_table(
        "proposals",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("quote_id", sa.Integer(), nullable=False),
        sa.Column("status", sa.Enum("sent", "customer_approved", "customer_rejected", name="proposalstatus", create_type=False), nullable=False),
        sa.Column("document_ref", sa.String(length=200), nullable=False),
        sa.Column("sent_at", sa.DateTime(), nullable=False),
        sa.Column("decided_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),
        sa.ForeignKeyConstraint(["quote_id"], ["quotes.id"]),
        sa.PrimaryKeyConstraint("id"),
    )

    # --- assets ----------------------------------------------------------
    assetstatus_enum.create(bind, checkfirst=True)
    op.create_table(
        "assets",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("service_account_id", sa.Integer(), nullable=False),
        sa.Column("order_item_id", sa.Integer(), nullable=True),
        sa.Column("asset_type", sa.String(length=100), nullable=False),
        sa.Column("identifier", sa.String(length=200), nullable=False),
        sa.Column("status", sa.Enum("active", "decommissioned", name="assetstatus", create_type=False), nullable=False),
        sa.Column("inventory_system_ref", sa.String(length=200), nullable=True),
        sa.Column("attributes", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),
        sa.ForeignKeyConstraint(["service_account_id"], ["service_accounts.id"]),
        sa.ForeignKeyConstraint(["order_item_id"], ["order_items.id"]),
        sa.PrimaryKeyConstraint("id"),
    )

    # --- orchestration_policies -------------------------------------------
    orchestrationmode_enum.create(bind, checkfirst=True)
    op.create_table(
        "orchestration_policies",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("intent_type", sa.String(length=20), nullable=False),
        sa.Column("mode", sa.Enum("deterministic", "llm_planned", name="orchestrationmode", create_type=False), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    bind = op.get_bind()

    op.drop_table("orchestration_policies")
    op.drop_table("assets")
    op.drop_table("proposals")

    if bind.dialect.name == "postgresql":
        op.drop_constraint("work_orders_order_item_id_fkey", "work_orders", type_="foreignkey")
        op.drop_column("work_orders", "order_item_id")
        op.drop_column("work_orders", "technical_parameters")
        op.drop_column("work_orders", "kind")
        orchestrationmode_enum.drop(bind, checkfirst=True)
        assetstatus_enum.drop(bind, checkfirst=True)
        proposalstatus_enum.drop(bind, checkfirst=True)
    else:
        with op.batch_alter_table("work_orders") as batch_op:
            batch_op.drop_constraint("work_orders_order_item_id_fkey", type_="foreignkey")
            batch_op.drop_column("order_item_id")
            batch_op.drop_column("technical_parameters")
            batch_op.drop_column("kind")

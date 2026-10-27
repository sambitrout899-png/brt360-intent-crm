"""Product instances - the catalog definition frozen when a product is picked
on an opportunity, quote or order (additive).

Revision ID: a3c5e7f9b1d2
Revises: f2c4e6a8b0d1
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "a3c5e7f9b1d2"
down_revision: Union[str, Sequence[str], None] = "f2c4e6a8b0d1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "product_instances",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.Integer(), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("context_type", sa.String(20), nullable=False),
        sa.Column("context_id", sa.Integer(), nullable=False),
        sa.Column("item_id", sa.Integer(), nullable=True),
        sa.Column("offering_name", sa.String(200), nullable=False),
        sa.Column("offering_id", sa.Integer(), sa.ForeignKey("product_offerings.id"), nullable=True),
        sa.Column("model_version", sa.Integer(), nullable=True),
        sa.Column("definition_json", sa.Text(), nullable=False),
        sa.Column("configuration_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("price_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("source_instance_id", sa.Integer(), sa.ForeignKey("product_instances.id"), nullable=True),
        sa.Column("taken_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("brt_id", sa.String(20), nullable=False),
        sa.Column("sys_created_at", sa.DateTime(), nullable=True),
        sa.Column("sys_created_by", sa.Integer(), nullable=True),
        sa.Column("sys_created_position_id", sa.Integer(), nullable=True),
        sa.Column("sys_department_id", sa.Integer(), nullable=True),
        sa.Column("sys_updated_at", sa.DateTime(), nullable=True),
        sa.Column("sys_updated_by", sa.Integer(), nullable=True),
        sa.UniqueConstraint("brt_id", name="uq_product_instances_brt_id"),
    )
    op.create_index("ix_product_instances_context_id", "product_instances", ["context_id"])


def downgrade() -> None:
    op.drop_index("ix_product_instances_context_id", table_name="product_instances")
    op.drop_table("product_instances")

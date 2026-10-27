"""Product hierarchies and Siebel-style product rules - additive.

- product_components: child products of a product (any depth), with
  cardinality and their own charge (monthly / one-time).
- product_rules: eligibility, compatibility and constraint rules.
- offering_attributes gains component_id, affects_price,
  affects_provisioning, min_value, max_value, reference_type, description
  (all nullable - existing attributes behave exactly as before).

Revision ID: e7a9c1b3d5f8
Revises: c6e8f0a2b4d7
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "e7a9c1b3d5f8"
down_revision: Union[str, Sequence[str], None] = "c6e8f0a2b4d7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _system(table: str) -> list:
    return [
        sa.Column("brt_id", sa.String(20), nullable=False),
        sa.Column("sys_created_at", sa.DateTime(), nullable=True),
        sa.Column("sys_created_by", sa.Integer(), nullable=True),
        sa.Column("sys_created_position_id", sa.Integer(), nullable=True),
        sa.Column("sys_department_id", sa.Integer(), nullable=True),
        sa.Column("sys_updated_at", sa.DateTime(), nullable=True),
        sa.Column("sys_updated_by", sa.Integer(), nullable=True),
        sa.UniqueConstraint("brt_id", name=f"uq_{table}_brt_id"),
    ]


def upgrade() -> None:
    op.create_table(
        "product_components",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.Integer(), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("offering_name", sa.String(200), nullable=False),
        sa.Column("parent_id", sa.Integer(), sa.ForeignKey("product_components.id"), nullable=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("min_qty", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_qty", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("default_qty", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("charge_type", sa.String(20), nullable=True),
        sa.Column("price", sa.Float(), nullable=False, server_default="0"),
        sa.Column("affects_provisioning", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("sequence", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        *_system("product_components"),
    )
    op.create_index("ix_product_components_offering_name", "product_components", ["offering_name"])
    op.create_table(
        "product_rules",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.Integer(), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("offering_name", sa.String(200), nullable=False),
        sa.Column("rule_type", sa.String(20), nullable=False),
        sa.Column("source_text", sa.Text(), nullable=False, server_default=""),
        sa.Column("definition_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("message", sa.String(300), nullable=False, server_default=""),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        *_system("product_rules"),
    )
    op.create_index("ix_product_rules_offering_name", "product_rules", ["offering_name"])
    with op.batch_alter_table("offering_attributes") as batch_op:
        batch_op.add_column(sa.Column("component_id", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("affects_price", sa.Boolean(), nullable=True))
        batch_op.add_column(sa.Column("affects_provisioning", sa.Boolean(), nullable=True))
        batch_op.add_column(sa.Column("min_value", sa.Float(), nullable=True))
        batch_op.add_column(sa.Column("max_value", sa.Float(), nullable=True))
        batch_op.add_column(sa.Column("reference_type", sa.String(60), nullable=True))
        batch_op.add_column(sa.Column("description", sa.Text(), nullable=True))
        batch_op.add_column(sa.Column("unit", sa.String(30), nullable=True))
        batch_op.create_foreign_key("fk_offering_attributes_component", "product_components", ["component_id"], ["id"])


def downgrade() -> None:
    with op.batch_alter_table("offering_attributes") as batch_op:
        batch_op.drop_constraint("fk_offering_attributes_component", type_="foreignkey")
        for name in ("unit", "description", "reference_type", "max_value", "min_value", "affects_provisioning", "affects_price", "component_id"):
            batch_op.drop_column(name)
    op.drop_index("ix_product_rules_offering_name", table_name="product_rules")
    op.drop_table("product_rules")
    op.drop_index("ix_product_components_offering_name", table_name="product_components")
    op.drop_table("product_components")

"""Product configuration - purely additive.

New tables: offering_attributes (a product's pricing/technical attributes),
attribute_price_impacts, offering_attribute_rules (design time);
quote_terms (a quote's own pricelist), quote_item_configs, order_item_configs
(what was chosen, carried quote -> order). opportunity_configs gains the
opportunity's own pricelist and the technical attributes captured while
selling. Products without a design keep pricing exactly as before.

Revision ID: f3b5d7e9a1c4
Revises: e2a4c6e8f0b3
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "f3b5d7e9a1c4"
down_revision: Union[str, Sequence[str], None] = "e2a4c6e8f0b3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "offering_attributes",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.Integer(), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("offering_name", sa.String(200), nullable=False),
        sa.Column("attribute_definition_id", sa.Integer(), sa.ForeignKey("product_attribute_definitions.id"), nullable=False),
        sa.Column("kind", sa.String(20), nullable=False, server_default="pricing"),
        sa.Column("allowed_values", sa.Text(), nullable=True),
        sa.Column("required", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("default_value", sa.String(200), nullable=True),
        sa.Column("sequence", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("visible_when_json", sa.Text(), nullable=True),
        sa.Column("help", sa.String(300), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("tenant_id", "offering_name", "attribute_definition_id", name="uq_offering_attributes"),
    )
    op.create_table(
        "attribute_price_impacts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("offering_attribute_id", sa.Integer(), sa.ForeignKey("offering_attributes.id"), nullable=False),
        sa.Column("value", sa.String(200), nullable=False),
        sa.Column("impact_type", sa.String(20), nullable=False, server_default="flat"),
        sa.Column("amount", sa.Float(), nullable=False, server_default="0"),
        sa.UniqueConstraint("offering_attribute_id", "value", name="uq_attribute_price_impacts"),
    )
    op.create_table(
        "offering_attribute_rules",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.Integer(), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("offering_name", sa.String(200), nullable=False),
        sa.Column("if_attribute", sa.String(100), nullable=False),
        sa.Column("operator", sa.String(10), nullable=False, server_default="eq"),
        sa.Column("if_value", sa.String(200), nullable=True),
        sa.Column("then_attribute", sa.String(100), nullable=False),
        sa.Column("effect", sa.String(20), nullable=False, server_default="required"),
        sa.Column("then_value", sa.String(200), nullable=True),
        sa.Column("message", sa.String(300), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
    )
    op.create_table(
        "quote_terms",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("quote_id", sa.Integer(), sa.ForeignKey("quotes.id"), nullable=False, unique=True),
        sa.Column("price_list_id", sa.Integer(), sa.ForeignKey("price_lists.id"), nullable=True),
    )
    op.create_table(
        "quote_item_configs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("quote_item_id", sa.Integer(), sa.ForeignKey("quote_items.id"), nullable=False, unique=True),
        sa.Column("pricing_attributes_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("technical_attributes_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("price_breakdown_json", sa.Text(), nullable=False, server_default="{}"),
    )
    op.create_table(
        "order_item_configs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("order_item_id", sa.Integer(), sa.ForeignKey("order_items.id"), nullable=False, unique=True),
        sa.Column("pricing_attributes_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("technical_attributes_json", sa.Text(), nullable=False, server_default="{}"),
    )
    with op.batch_alter_table("opportunity_configs") as batch_op:
        batch_op.add_column(sa.Column("price_list_id", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("technical_attributes_json", sa.Text(), nullable=True))
        batch_op.create_foreign_key("fk_opportunity_configs_price_list_id", "price_lists", ["price_list_id"], ["id"])


def downgrade() -> None:
    with op.batch_alter_table("opportunity_configs") as batch_op:
        batch_op.drop_constraint("fk_opportunity_configs_price_list_id", type_="foreignkey")
        batch_op.drop_column("technical_attributes_json")
        batch_op.drop_column("price_list_id")
    for table in ("order_item_configs", "quote_item_configs", "quote_terms", "offering_attribute_rules",
                  "attribute_price_impacts", "offering_attributes"):
        op.drop_table(table)

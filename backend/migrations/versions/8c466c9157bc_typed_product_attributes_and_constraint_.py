"""typed product attributes and constraint rules

Brand new tables for Phase 1 primitive #4 - no existing data to migrate.

Revision ID: 8c466c9157bc
Revises: 6c05cbcc2d0e
Create Date: 2026-10-05 11:48:48.018279

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '8c466c9157bc'
down_revision: Union[str, Sequence[str], None] = '6c05cbcc2d0e'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "product_attribute_definitions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("data_type", sa.String(length=20), nullable=False),
        sa.Column("unit", sa.String(length=30), nullable=True),
        sa.Column("allowed_values", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "product_attribute_values",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("product_specification_id", sa.Integer(), nullable=False),
        sa.Column("attribute_definition_id", sa.Integer(), nullable=False),
        sa.Column("value", sa.String(length=200), nullable=False),
        sa.ForeignKeyConstraint(["product_specification_id"], ["product_specifications.id"]),
        sa.ForeignKeyConstraint(["attribute_definition_id"], ["product_attribute_definitions.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "attribute_constraint_rules",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("if_product_spec_id", sa.Integer(), nullable=False),
        sa.Column("if_attribute_id", sa.Integer(), nullable=False),
        sa.Column("operator", sa.String(length=10), nullable=False),
        sa.Column("threshold", sa.String(length=200), nullable=False),
        sa.Column("rule_type", sa.String(length=20), nullable=False),
        sa.Column("then_product_spec_id", sa.Integer(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),
        sa.ForeignKeyConstraint(["if_product_spec_id"], ["product_specifications.id"]),
        sa.ForeignKeyConstraint(["if_attribute_id"], ["product_attribute_definitions.id"]),
        sa.ForeignKeyConstraint(["then_product_spec_id"], ["product_specifications.id"]),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    op.drop_table("attribute_constraint_rules")
    op.drop_table("product_attribute_values")
    op.drop_table("product_attribute_definitions")

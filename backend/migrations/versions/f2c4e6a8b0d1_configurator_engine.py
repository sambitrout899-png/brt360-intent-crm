"""Configurator: relationship slots, product classes, released model
versions and model tests - additive.

Revision ID: f2c4e6a8b0d1
Revises: e7a9c1b3d5f8
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "f2c4e6a8b0d1"
down_revision: Union[str, Sequence[str], None] = "e7a9c1b3d5f8"
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
        "product_slot_domains",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("component_id", sa.Integer(), sa.ForeignKey("product_components.id"), nullable=False, unique=True),
        sa.Column("domain_type", sa.String(10), nullable=False, server_default="products"),
        sa.Column("products", sa.Text(), nullable=False, server_default=""),
        sa.Column("class_name", sa.String(100), nullable=True),
        sa.Column("default_product", sa.String(200), nullable=True),
        *_system("product_slot_domains"),
    )
    op.create_table(
        "product_classes",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.Integer(), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("parent_name", sa.String(100), nullable=True),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("members", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        *_system("product_classes"),
    )
    op.create_table(
        "product_model_versions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.Integer(), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("offering_name", sa.String(200), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(12), nullable=False, server_default="released"),
        sa.Column("valid_from", sa.DateTime(), nullable=False),
        sa.Column("snapshot_json", sa.Text(), nullable=False),
        sa.Column("notes", sa.Text(), nullable=False, server_default=""),
        sa.Column("report_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        *_system("product_model_versions"),
    )
    op.create_index("ix_product_model_versions_offering_name", "product_model_versions", ["offering_name"])
    op.create_table(
        "product_model_tests",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.Integer(), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("offering_name", sa.String(200), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("picks_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("expect", sa.String(10), nullable=False, server_default="valid"),
        sa.Column("expect_price", sa.Float(), nullable=True),
        sa.Column("expect_domains_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        *_system("product_model_tests"),
    )
    op.create_index("ix_product_model_tests_offering_name", "product_model_tests", ["offering_name"])


def downgrade() -> None:
    op.drop_index("ix_product_model_tests_offering_name", table_name="product_model_tests")
    op.drop_table("product_model_tests")
    op.drop_index("ix_product_model_versions_offering_name", table_name="product_model_versions")
    op.drop_table("product_model_versions")
    op.drop_table("product_classes")
    op.drop_table("product_slot_domains")

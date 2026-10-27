"""Page Designer, BIM and the New Connect lead products - additive.

- page_layouts, validation_rules (Page Designer), lead_products (a lead's
  interested products), bim_interfaces, bim_jobs (BRT Integration Manager)
  - each with its BRT_ID and system fields like every table.
- custom_field_definitions gains is_system / segment / section / position /
  help_text, so the standard B2B/B2C fields (seeded on deploy, only where a
  tenant has no field of that key) sit beside the tenant's own.

Revision ID: c6e8f0a2b4d7
Revises: a4c6e8f0b2d5
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "c6e8f0a2b4d7"
down_revision: Union[str, Sequence[str], None] = "a4c6e8f0b2d5"
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
        "page_layouts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.Integer(), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("page_key", sa.String(80), nullable=False),
        sa.Column("entity_type", sa.String(30), nullable=False),
        sa.Column("segment", sa.String(10), nullable=True),
        sa.Column("name", sa.String(150), nullable=False),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("layout_json", sa.Text(), nullable=False),
        sa.Column("is_default", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("tenant_id", "page_key", name="uq_page_layouts_key"),
        *_system("page_layouts"),
    )
    op.create_table(
        "validation_rules",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.Integer(), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("entity_type", sa.String(30), nullable=False),
        sa.Column("segment", sa.String(10), nullable=True),
        sa.Column("name", sa.String(150), nullable=False),
        sa.Column("source_text", sa.Text(), nullable=False, server_default=""),
        sa.Column("kind", sa.String(10), nullable=False, server_default="simple"),
        sa.Column("compiled_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("flow_id", sa.Integer(), sa.ForeignKey("flow_definitions.id"), nullable=True),
        sa.Column("message", sa.String(300), nullable=False, server_default=""),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        *_system("validation_rules"),
    )
    op.create_table(
        "lead_products",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("lead_id", sa.Integer(), sa.ForeignKey("leads.id"), nullable=False),
        sa.Column("offering_name", sa.String(200), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("notes", sa.String(300), nullable=True),
        sa.Column("position", sa.Integer(), nullable=False, server_default="0"),
        *_system("lead_products"),
    )
    op.create_index("ix_lead_products_lead_id", "lead_products", ["lead_id"])
    op.create_table(
        "bim_interfaces",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.Integer(), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("code", sa.String(60), nullable=False),
        sa.Column("name", sa.String(150), nullable=False),
        sa.Column("entity", sa.String(40), nullable=False),
        sa.Column("mode", sa.String(10), nullable=False, server_default="upsert"),
        sa.Column("mapping_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("tenant_id", "code", name="uq_bim_interfaces_code"),
        *_system("bim_interfaces"),
    )
    op.create_table(
        "bim_jobs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.Integer(), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("interface_id", sa.Integer(), sa.ForeignKey("bim_interfaces.id"), nullable=True),
        sa.Column("entity", sa.String(40), nullable=False),
        sa.Column("channel", sa.String(10), nullable=False, server_default="batch"),
        sa.Column("file_name", sa.String(200), nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="running"),
        sa.Column("dry_run", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("total", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("inserted", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("updated", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("failed", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("errors_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("started_at", sa.DateTime(), nullable=False),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        *_system("bim_jobs"),
    )
    with op.batch_alter_table("custom_field_definitions") as batch_op:
        batch_op.add_column(sa.Column("is_system", sa.Boolean(), nullable=False, server_default=sa.false()))
        batch_op.add_column(sa.Column("segment", sa.String(10), nullable=True))
        batch_op.add_column(sa.Column("section", sa.String(60), nullable=True))
        batch_op.add_column(sa.Column("position", sa.Integer(), nullable=False, server_default="0"))
        batch_op.add_column(sa.Column("help_text", sa.String(300), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("custom_field_definitions") as batch_op:
        for name in ("help_text", "position", "section", "segment", "is_system"):
            batch_op.drop_column(name)
    op.drop_table("bim_jobs")
    op.drop_table("bim_interfaces")
    op.drop_index("ix_lead_products_lead_id", table_name="lead_products")
    op.drop_table("lead_products")
    op.drop_table("validation_rules")
    op.drop_table("page_layouts")

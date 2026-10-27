"""configurable customer 360: layout versions and data connectors

Revision ID: a8b0c2d4e6f9
Revises: f7a9b1c3d5e8
Create Date: 2026-10-08 18:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a8b0c2d4e6f9'
down_revision: Union[str, Sequence[str], None] = 'f7a9b1c3d5e8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "c360_layouts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.Integer(), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("config_json", sa.Text(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False, server_default=""),
        sa.Column("changed_by", sa.String(length=200), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("tenant_id", "version", name="uq_c360_layouts_tenant_version"),
    )
    op.create_index("ix_c360_layouts_tenant_id", "c360_layouts", ["tenant_id"])
    op.create_table(
        "data_connectors",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.Integer(), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("key", sa.String(length=60), nullable=False),
        sa.Column("name", sa.String(length=150), nullable=False),
        sa.Column("domain", sa.String(length=30), nullable=False),
        sa.Column("kind", sa.String(length=20), nullable=False, server_default="rest"),
        sa.Column("base_url", sa.String(length=500), nullable=False, server_default=""),
        sa.Column("auth_type", sa.String(length=20), nullable=False, server_default="none"),
        sa.Column("auth_header", sa.String(length=100), nullable=False, server_default=""),
        sa.Column("secret", sa.Text(), nullable=False, server_default=""),
        sa.Column("endpoints_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("last_test_at", sa.DateTime(), nullable=True),
        sa.Column("last_test_ok", sa.Boolean(), nullable=True),
        sa.Column("last_test_message", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("tenant_id", "key", name="uq_data_connectors_tenant_key"),
    )
    op.create_index("ix_data_connectors_tenant_id", "data_connectors", ["tenant_id"])


def downgrade() -> None:
    op.drop_index("ix_data_connectors_tenant_id", table_name="data_connectors")
    op.drop_table("data_connectors")
    op.drop_index("ix_c360_layouts_tenant_id", table_name="c360_layouts")
    op.drop_table("c360_layouts")

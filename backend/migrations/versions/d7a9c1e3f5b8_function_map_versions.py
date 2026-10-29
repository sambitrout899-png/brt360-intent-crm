"""function_map_versions: a tenant's versions of the Functional Map - drafts,
the active one, the retired ones (app/models/function_map.py)."""
from alembic import op
import sqlalchemy as sa

revision = "d7a9c1e3f5b8"
down_revision = "c6f8b0d2e4a7"
branch_labels = depends_on = None


def upgrade():
    op.create_table(
        "function_map_versions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.Integer(), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("number", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(150), nullable=False),
        sa.Column("notes", sa.Text(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("based_on", sa.String(60), nullable=False),
        sa.Column("picture_json", sa.Text(), nullable=False),
        sa.Column("created_by_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("activated_at", sa.DateTime(), nullable=True),
        sa.Column("activated_by_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("brt_id", sa.String(20), nullable=False),
        sa.Column("sys_created_at", sa.DateTime(), nullable=True),
        sa.Column("sys_created_by", sa.Integer(), nullable=True),
        sa.Column("sys_created_position_id", sa.Integer(), nullable=True),
        sa.Column("sys_department_id", sa.Integer(), nullable=True),
        sa.Column("sys_updated_at", sa.DateTime(), nullable=True),
        sa.Column("sys_updated_by", sa.Integer(), nullable=True),
        sa.UniqueConstraint("brt_id", name="uq_function_map_versions_brt_id"),
    )
    op.create_index("ix_function_map_versions_tenant", "function_map_versions", ["tenant_id", "number"])


def downgrade():
    op.drop_index("ix_function_map_versions_tenant", table_name="function_map_versions")
    op.drop_table("function_map_versions")

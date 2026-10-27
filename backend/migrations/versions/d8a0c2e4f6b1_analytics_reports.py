"""Analytics Studio: saved reports and dashboards (analytics_reports) - a JSON spec each,
read against the live data every time."""
from alembic import op
import sqlalchemy as sa

revision = "d8a0c2e4f6b1"
down_revision = "c6e8a0b2d4f7"
branch_labels = depends_on = None


def upgrade():
    op.create_table(
        "analytics_reports",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.Integer(), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("owner_user_id", sa.Integer(), nullable=True),
        sa.Column("kind", sa.String(20), nullable=False, server_default="report"),
        sa.Column("name", sa.String(150), nullable=False),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("spec_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("shared", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        # the system columns every table carries (app/brt_id.py)
        sa.Column("brt_id", sa.String(20), nullable=False),
        sa.Column("sys_created_at", sa.DateTime(), nullable=True),
        sa.Column("sys_created_by", sa.Integer(), nullable=True),
        sa.Column("sys_created_position_id", sa.Integer(), nullable=True),
        sa.Column("sys_department_id", sa.Integer(), nullable=True),
        sa.Column("sys_updated_at", sa.DateTime(), nullable=True),
        sa.Column("sys_updated_by", sa.Integer(), nullable=True),
        sa.UniqueConstraint("brt_id", name="uq_analytics_reports_brt_id"),
    )
    op.create_index("ix_analytics_reports_tenant_id", "analytics_reports", ["tenant_id"])


def downgrade():
    op.drop_index("ix_analytics_reports_tenant_id", table_name="analytics_reports")
    op.drop_table("analytics_reports")

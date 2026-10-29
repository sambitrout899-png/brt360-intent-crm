"""notification_deliveries: what became of each recorded notification
(app/crm_core/notification_delivery.py)."""
from alembic import op
import sqlalchemy as sa

revision = "c6f8b0d2e4a7"
down_revision = "b5e7a9c1d3f6"
branch_labels = depends_on = None


def upgrade():
    op.create_table(
        "notification_deliveries",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.Integer(), sa.ForeignKey("tenants.id"), nullable=True),
        sa.Column("event_id", sa.Integer(), nullable=False),
        sa.Column("at", sa.DateTime(), nullable=False),
        sa.Column("channel", sa.String(30), nullable=False),
        sa.Column("audience", sa.String(300), nullable=False),
        sa.Column("recipients", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("detail", sa.Text(), nullable=False),
        sa.Column("brt_id", sa.String(20), nullable=False),
        sa.Column("sys_created_at", sa.DateTime(), nullable=True),
        sa.Column("sys_created_by", sa.Integer(), nullable=True),
        sa.Column("sys_created_position_id", sa.Integer(), nullable=True),
        sa.Column("sys_department_id", sa.Integer(), nullable=True),
        sa.Column("sys_updated_at", sa.DateTime(), nullable=True),
        sa.Column("sys_updated_by", sa.Integer(), nullable=True),
        sa.UniqueConstraint("brt_id", name="uq_notification_deliveries_brt_id"),
        sa.UniqueConstraint("event_id", name="uq_notification_deliveries_event_id"),
    )
    op.create_index("ix_notification_deliveries_tenant_at", "notification_deliveries", ["tenant_id", "at"])


def downgrade():
    op.drop_index("ix_notification_deliveries_tenant_at", table_name="notification_deliveries")
    op.drop_table("notification_deliveries")

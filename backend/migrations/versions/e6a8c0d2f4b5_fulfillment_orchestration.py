"""Fulfillment (COM / SOM / TOM order orchestration): fulfillment patterns the
tenant admin designs, and the layered orders and tasks they run as."""
from alembic import op
import sqlalchemy as sa

revision = "e6a8c0d2f4b5"
down_revision = "d5f7b9c1e3a4"
branch_labels = depends_on = None


def _system_columns() -> list[sa.Column]:
    return [
        sa.Column("brt_id", sa.String(20), nullable=False),
        sa.Column("sys_created_at", sa.DateTime(), nullable=True),
        sa.Column("sys_created_by", sa.Integer(), nullable=True),
        sa.Column("sys_created_position_id", sa.Integer(), nullable=True),
        sa.Column("sys_department_id", sa.Integer(), nullable=True),
        sa.Column("sys_updated_at", sa.DateTime(), nullable=True),
        sa.Column("sys_updated_by", sa.Integer(), nullable=True),
    ]


def upgrade():
    op.create_table(
        "fulfillment_patterns",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.Integer(), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("name", sa.String(150), nullable=False),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("products", sa.Text(), nullable=False, server_default=""),
        sa.Column("segment", sa.String(20), nullable=False, server_default=""),
        sa.Column("order_type", sa.String(20), nullable=False, server_default="new"),
        sa.Column("priority", sa.Integer(), nullable=False, server_default="100"),
        sa.Column("mode", sa.String(20), nullable=False, server_default="deterministic"),
        sa.Column("graph_json", sa.Text(), nullable=False, server_default='{"nodes": [], "edges": []}'),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        *_system_columns(),
        sa.UniqueConstraint("brt_id", name="uq_fulfillment_patterns_brt_id"),
    )
    op.create_table(
        "fulfillment_orders",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.Integer(), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("layer", sa.String(3), nullable=False),
        sa.Column("number", sa.String(30), nullable=False, server_default=""),
        sa.Column("crm_order_id", sa.Integer(), sa.ForeignKey("orders.id"), nullable=False),
        sa.Column("parent_id", sa.Integer(), sa.ForeignKey("fulfillment_orders.id"), nullable=True),
        sa.Column("parent_task_id", sa.Integer(), nullable=True),
        sa.Column("pattern_id", sa.Integer(), sa.ForeignKey("fulfillment_patterns.id"), nullable=True),
        sa.Column("pattern_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("planned_by", sa.String(20), nullable=False, server_default="deterministic"),
        sa.Column("plan_note", sa.Text(), nullable=False, server_default=""),
        sa.Column("status", sa.String(20), nullable=False, server_default="in_progress"),
        sa.Column("data_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        *_system_columns(),
        sa.UniqueConstraint("brt_id", name="uq_fulfillment_orders_brt_id"),
    )
    op.create_index("ix_fulfillment_orders_crm_order_id", "fulfillment_orders", ["crm_order_id"])
    op.create_table(
        "fulfillment_tasks",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.Integer(), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("fulfillment_order_id", sa.Integer(), sa.ForeignKey("fulfillment_orders.id"), nullable=False),
        sa.Column("node_id", sa.String(50), nullable=False),
        sa.Column("function", sa.String(50), nullable=False),
        sa.Column("label", sa.String(150), nullable=False, server_default=""),
        sa.Column("sequence", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("depends_on_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("config_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("request_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("response_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("error_json", sa.Text(), nullable=False, server_default=""),
        sa.Column("work_order_id", sa.Integer(), nullable=True),
        sa.Column("external_ref", sa.String(100), nullable=False, server_default=""),
        sa.Column("child_order_id", sa.Integer(), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("note", sa.Text(), nullable=False, server_default=""),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        *_system_columns(),
        sa.UniqueConstraint("brt_id", name="uq_fulfillment_tasks_brt_id"),
    )
    op.create_index("ix_fulfillment_tasks_fulfillment_order_id", "fulfillment_tasks", ["fulfillment_order_id"])
    op.create_index("ix_fulfillment_tasks_work_order_id", "fulfillment_tasks", ["work_order_id"])
    op.create_index("ix_fulfillment_tasks_external_ref", "fulfillment_tasks", ["external_ref"])


def downgrade():
    op.drop_index("ix_fulfillment_tasks_external_ref", table_name="fulfillment_tasks")
    op.drop_index("ix_fulfillment_tasks_work_order_id", table_name="fulfillment_tasks")
    op.drop_index("ix_fulfillment_tasks_fulfillment_order_id", table_name="fulfillment_tasks")
    op.drop_table("fulfillment_tasks")
    op.drop_index("ix_fulfillment_orders_crm_order_id", table_name="fulfillment_orders")
    op.drop_table("fulfillment_orders")
    op.drop_table("fulfillment_patterns")

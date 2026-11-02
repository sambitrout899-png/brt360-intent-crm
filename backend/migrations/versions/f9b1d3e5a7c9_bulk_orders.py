"""Bulk orders (app/models/bulk_orders.py): a request of many orders, each row run through the single-order
process and followed to fulfilment completion, assets and billing initiation."""
from alembic import op
import sqlalchemy as sa

revision = "f9b1d3e5a7c9"
down_revision = "e8a0c2d4f6b8"
branch_labels = depends_on = None


def _create(name, *columns):
    """Every table also carries the BRT_ID and the system audit columns (app/brt_id.py)."""
    op.create_table(
        name, *columns,
        sa.Column("brt_id", sa.String(20), nullable=False),
        sa.Column("sys_created_at", sa.DateTime(), nullable=True),
        sa.Column("sys_created_by", sa.Integer(), nullable=True),
        sa.Column("sys_created_position_id", sa.Integer(), nullable=True),
        sa.Column("sys_department_id", sa.Integer(), nullable=True),
        sa.Column("sys_updated_at", sa.DateTime(), nullable=True),
        sa.Column("sys_updated_by", sa.Integer(), nullable=True),
        sa.UniqueConstraint("brt_id", name=f"uq_{name}_brt_id"),
    )


def upgrade():
    _create(
        "bulk_orders",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.Integer(), sa.ForeignKey("tenants.id"), nullable=False, index=True),
        sa.Column("number", sa.String(30), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("process", sa.String(30), nullable=False),
        sa.Column("status", sa.String(25), nullable=False),
        sa.Column("defaults", sa.JSON(), nullable=True),
        sa.Column("auto_approve", sa.Boolean(), nullable=False),
        sa.Column("send_to_fulfillment", sa.Boolean(), nullable=False),
        sa.Column("submitted_by", sa.Integer(), nullable=True),
        sa.Column("position_id", sa.Integer(), nullable=True),
        sa.Column("data_scope", sa.String(30), nullable=True),
        sa.Column("source", sa.String(20), nullable=False),
        sa.Column("counts", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
    )
    _create(
        "bulk_order_rows",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.Integer(), sa.ForeignKey("tenants.id"), nullable=False, index=True),
        sa.Column("bulk_order_id", sa.Integer(), sa.ForeignKey("bulk_orders.id"), nullable=False, index=True),
        sa.Column("row_no", sa.Integer(), nullable=False),
        sa.Column("data", sa.JSON(), nullable=True),
        sa.Column("stage", sa.String(30), nullable=False, index=True),
        sa.Column("party_id", sa.Integer(), nullable=True),
        sa.Column("order_ids", sa.JSON(), nullable=True),
        sa.Column("asset_ids", sa.JSON(), nullable=True),
        sa.Column("billing", sa.JSON(), nullable=True),
        sa.Column("runs", sa.JSON(), nullable=True),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("bulk_order_id", "row_no", name="uq_bulk_order_row"),
    )


def downgrade():
    op.drop_table("bulk_order_rows")
    op.drop_table("bulk_orders")

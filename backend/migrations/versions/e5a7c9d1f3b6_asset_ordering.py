"""Asset instances, revision history, and asset-based change orders."""
from alembic import op
import sqlalchemy as sa
revision = "e5a7c9d1f3b6"
down_revision = "d4f6a8b0c2e5"
branch_labels = depends_on = None

def upgrade():
    if op.get_bind().dialect.name == "postgresql":
        with op.get_context().autocommit_block():
            op.execute("ALTER TYPE orderstatus ADD VALUE IF NOT EXISTS 'completed'")
            op.execute("ALTER TYPE assetstatus ADD VALUE IF NOT EXISTS 'suspended'")
    op.add_column("order_items", sa.Column("status", sa.String(30), nullable=False, server_default="created"))
    op.add_column("assets", sa.Column("instance_json", sa.Text(), nullable=False, server_default="{}"))
    op.add_column("assets", sa.Column("version", sa.Integer(), nullable=False, server_default="1"))
    op.create_table("asset_changes", sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.Integer(), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("asset_id", sa.Integer(), sa.ForeignKey("assets.id"), nullable=False),
        sa.Column("order_id", sa.Integer(), sa.ForeignKey("orders.id"), nullable=False, unique=True),
        sa.Column("operation", sa.String(30), nullable=False), sa.Column("base_version", sa.Integer(), nullable=False),
        sa.Column("requested_json", sa.Text(), nullable=False), sa.Column("status", sa.String(30), nullable=False),
        sa.Column("orchestration_ref", sa.String(100), nullable=False), sa.Column("created_at", sa.DateTime(), nullable=False))
    op.create_table("asset_revisions", sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.Integer(), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("asset_id", sa.Integer(), sa.ForeignKey("assets.id"), nullable=False),
        sa.Column("order_item_id", sa.Integer(), sa.ForeignKey("order_items.id"), nullable=False, unique=True),
        sa.Column("version", sa.Integer(), nullable=False), sa.Column("snapshot_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False), sa.UniqueConstraint("asset_id", "version"))
    for table in ("asset_changes", "asset_revisions"):
        op.add_column(table, sa.Column("brt_id", sa.String(20), nullable=False))
        for name in ("sys_created_at", "sys_updated_at"):
            op.add_column(table, sa.Column(name, sa.DateTime(), nullable=True))
        for name in ("sys_created_by", "sys_created_position_id", "sys_department_id", "sys_updated_by"):
            op.add_column(table, sa.Column(name, sa.Integer(), nullable=True))
        op.create_index(f"ix_{table}_brt_id", table, ["brt_id"], unique=True)
    op.create_index("uq_asset_pending_change", "asset_changes", ["asset_id"], unique=True,
                    sqlite_where=sa.text("status = 'submitted'"), postgresql_where=sa.text("status = 'submitted'"))

def downgrade():
    op.drop_table("asset_revisions")
    op.drop_table("asset_changes")
    with op.batch_alter_table("assets") as b:
        b.drop_column("instance_json")
        b.drop_column("version")
    with op.batch_alter_table("order_items") as b:
        b.drop_column("status")

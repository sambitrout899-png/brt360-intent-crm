"""Siebel asset-based ordering: future-dated (queued) changes, revisions and
cancellations of change orders, and asset change quotes."""
from alembic import op
import sqlalchemy as sa

revision = "b3d5f7a9c1e2"
down_revision = "a2b4c6d8e0f1"
branch_labels = depends_on = None


def upgrade():
    # plain ADD COLUMNs - no table rebuild, so the partial "one open change per asset" index stays as it is
    op.add_column("asset_changes", sa.Column("due_date", sa.Date(), nullable=True))
    op.add_column("asset_changes", sa.Column("after_change_id", sa.Integer(), nullable=True))
    op.add_column("asset_changes", sa.Column("revision_of_change_id", sa.Integer(), nullable=True))
    op.add_column("asset_changes", sa.Column("values_json", sa.Text(), nullable=True))
    op.add_column("asset_changes", sa.Column("reason", sa.String(300), nullable=True))
    op.add_column("asset_changes", sa.Column("completed_at", sa.DateTime(), nullable=True))
    op.create_table(
        "asset_quotes",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.Integer(), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("asset_id", sa.Integer(), sa.ForeignKey("assets.id"), nullable=False),
        sa.Column("quote_number", sa.String(30), nullable=False, server_default=""),
        sa.Column("operation", sa.String(30), nullable=False),
        sa.Column("base_version", sa.Integer(), nullable=False),
        sa.Column("values_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("preview_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("status", sa.String(20), nullable=False, server_default="quoted"),
        sa.Column("valid_until", sa.Date(), nullable=False),
        sa.Column("order_id", sa.Integer(), sa.ForeignKey("orders.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("brt_id", sa.String(20), nullable=False),
        sa.Column("sys_created_at", sa.DateTime(), nullable=True),
        sa.Column("sys_created_by", sa.Integer(), nullable=True),
        sa.Column("sys_created_position_id", sa.Integer(), nullable=True),
        sa.Column("sys_department_id", sa.Integer(), nullable=True),
        sa.Column("sys_updated_at", sa.DateTime(), nullable=True),
        sa.Column("sys_updated_by", sa.Integer(), nullable=True),
        sa.UniqueConstraint("brt_id", name="uq_asset_quotes_brt_id"),
    )


def downgrade():
    op.drop_table("asset_quotes")
    with op.batch_alter_table("asset_changes") as batch:
        for name in ("completed_at", "reason", "values_json", "revision_of_change_id", "after_change_id", "due_date"):
            batch.drop_column(name)
    # the rebuild may not carry the partial index's WHERE - put it back exactly
    op.execute("DROP INDEX IF EXISTS uq_asset_pending_change")
    op.create_index("uq_asset_pending_change", "asset_changes", ["asset_id"], unique=True,
                    sqlite_where=sa.text("status = 'submitted'"), postgresql_where=sa.text("status = 'submitted'"))

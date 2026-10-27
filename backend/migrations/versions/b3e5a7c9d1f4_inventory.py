"""Inventory: item types, items (locations, equipment, logical resources, services),
relationships, resource pools, item history and reconciliation runs - the network
record Fulfillment's SOM steps allocate from (models/inventory.py)."""
from alembic import op
import sqlalchemy as sa

revision = "b3e5a7c9d1f4"
down_revision = "a1c3e5f7b9d2"
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


def _tenant() -> sa.Column:
    return sa.Column("tenant_id", sa.Integer(), sa.ForeignKey("tenants.id"), nullable=False)


def upgrade():
    op.create_table(
        "inv_item_types",
        sa.Column("id", sa.Integer(), primary_key=True), _tenant(),
        sa.Column("code", sa.String(60), nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("parent_code", sa.String(60), nullable=False, server_default=""),
        sa.Column("attributes_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("contained_in", sa.Text(), nullable=False, server_default=""),
        sa.Column("naming", sa.String(150), nullable=False, server_default=""),
        sa.Column("resource_type", sa.String(60), nullable=False, server_default=""),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("builtin", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        *_system_columns(),
        sa.UniqueConstraint("brt_id", name="uq_inv_item_types_brt_id"),
    )
    op.create_index("ix_inv_item_types_tenant_id", "inv_item_types", ["tenant_id"])
    op.create_table(
        "inv_items",
        sa.Column("id", sa.Integer(), primary_key=True), _tenant(),
        sa.Column("type_id", sa.Integer(), sa.ForeignKey("inv_item_types.id"), nullable=False),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("name", sa.String(200), nullable=False, server_default=""),
        sa.Column("identifier", sa.String(200), nullable=False),
        sa.Column("lifecycle", sa.String(20), nullable=False, server_default="available"),
        sa.Column("oper_status", sa.String(20), nullable=False, server_default="unknown"),
        sa.Column("parent_id", sa.Integer(), sa.ForeignKey("inv_items.id"), nullable=True),
        sa.Column("location_id", sa.Integer(), nullable=True),
        sa.Column("pool_id", sa.Integer(), nullable=True),
        sa.Column("serial", sa.String(120), nullable=False, server_default=""),
        sa.Column("vendor", sa.String(120), nullable=False, server_default=""),
        sa.Column("model", sa.String(120), nullable=False, server_default=""),
        sa.Column("attributes_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("sources_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("order_id", sa.Integer(), nullable=True),
        sa.Column("party_id", sa.Integer(), nullable=True),
        sa.Column("service_item_id", sa.Integer(), nullable=True),
        sa.Column("reference", sa.String(120), nullable=False, server_default=""),
        sa.Column("reserved_until", sa.DateTime(), nullable=True),
        sa.Column("source", sa.String(20), nullable=False, server_default="manual"),
        sa.Column("last_verified_at", sa.DateTime(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        *_system_columns(),
        sa.UniqueConstraint("brt_id", name="uq_inv_items_brt_id"),
    )
    for column in ("tenant_id", "type_id", "kind", "identifier", "lifecycle", "parent_id", "location_id", "pool_id", "serial", "order_id",
                   "party_id", "service_item_id"):
        op.create_index(f"ix_inv_items_{column}", "inv_items", [column])
    op.create_table(
        "inv_relationships",
        sa.Column("id", sa.Integer(), primary_key=True), _tenant(),
        sa.Column("source_id", sa.Integer(), sa.ForeignKey("inv_items.id"), nullable=False),
        sa.Column("target_id", sa.Integer(), sa.ForeignKey("inv_items.id"), nullable=False),
        sa.Column("rel_type", sa.String(30), nullable=False),
        sa.Column("note", sa.String(300), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        *_system_columns(),
        sa.UniqueConstraint("brt_id", name="uq_inv_relationships_brt_id"),
    )
    for column in ("tenant_id", "source_id", "target_id"):
        op.create_index(f"ix_inv_relationships_{column}", "inv_relationships", [column])
    op.create_table(
        "inv_pools",
        sa.Column("id", sa.Integer(), primary_key=True), _tenant(),
        sa.Column("name", sa.String(150), nullable=False),
        sa.Column("type_id", sa.Integer(), sa.ForeignKey("inv_item_types.id"), nullable=False),
        sa.Column("pool_type", sa.String(20), nullable=False, server_default="range"),
        sa.Column("location_id", sa.Integer(), nullable=True),
        sa.Column("range_start", sa.String(80), nullable=False, server_default=""),
        sa.Column("range_end", sa.String(80), nullable=False, server_default=""),
        sa.Column("block_size", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("format", sa.String(80), nullable=False, server_default="{n}"),
        sa.Column("threshold_pct", sa.Integer(), nullable=False, server_default="80"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        *_system_columns(),
        sa.UniqueConstraint("brt_id", name="uq_inv_pools_brt_id"),
    )
    op.create_index("ix_inv_pools_tenant_id", "inv_pools", ["tenant_id"])
    op.create_table(
        "inv_history",
        sa.Column("id", sa.Integer(), primary_key=True), _tenant(),
        sa.Column("item_id", sa.Integer(), nullable=False),
        sa.Column("action", sa.String(30), nullable=False),
        sa.Column("changes_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("source", sa.String(20), nullable=False, server_default="manual"),
        sa.Column("reference", sa.String(120), nullable=False, server_default=""),
        sa.Column("actor_id", sa.Integer(), nullable=True),
        sa.Column("at", sa.DateTime(), nullable=False),
        *_system_columns(),
        sa.UniqueConstraint("brt_id", name="uq_inv_history_brt_id"),
    )
    for column in ("tenant_id", "item_id", "at"):
        op.create_index(f"ix_inv_history_{column}", "inv_history", [column])
    op.create_table(
        "inv_reconcile_runs",
        sa.Column("id", sa.Integer(), primary_key=True), _tenant(),
        sa.Column("source_name", sa.String(120), nullable=False, server_default=""),
        sa.Column("dry_run", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("summary_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("details_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("at", sa.DateTime(), nullable=False),
        *_system_columns(),
        sa.UniqueConstraint("brt_id", name="uq_inv_reconcile_runs_brt_id"),
    )
    op.create_index("ix_inv_reconcile_runs_tenant_id", "inv_reconcile_runs", ["tenant_id"])


def downgrade():
    op.drop_index("ix_inv_reconcile_runs_tenant_id", table_name="inv_reconcile_runs")
    op.drop_table("inv_reconcile_runs")
    for column in ("tenant_id", "item_id", "at"):
        op.drop_index(f"ix_inv_history_{column}", table_name="inv_history")
    op.drop_table("inv_history")
    op.drop_index("ix_inv_pools_tenant_id", table_name="inv_pools")
    op.drop_table("inv_pools")
    for column in ("tenant_id", "source_id", "target_id"):
        op.drop_index(f"ix_inv_relationships_{column}", table_name="inv_relationships")
    op.drop_table("inv_relationships")
    for column in ("tenant_id", "type_id", "kind", "identifier", "lifecycle", "parent_id", "location_id", "pool_id", "serial", "order_id",
                   "party_id", "service_item_id"):
        op.drop_index(f"ix_inv_items_{column}", table_name="inv_items")
    op.drop_table("inv_items")
    op.drop_index("ix_inv_item_types_tenant_id", table_name="inv_item_types")
    op.drop_table("inv_item_types")

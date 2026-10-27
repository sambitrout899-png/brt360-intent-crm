"""Fulfillment naming: a database that ran the first build of e6a8c0d2f4b5
has its tables under the module's earlier working name - rename them, their
indexes, the task -> order column and the milestone codes to Fulfillment.
A database that ran the current e6a8c0d2f4b5 already has them: nothing to do."""
from alembic import op
import sqlalchemy as sa

revision = "f8c0e2a4b6d9"
down_revision = "e6a8c0d2f4b5"
branch_labels = depends_on = None

_OLD = "osm"  # the earlier working name, only ever read here
_TABLES = ("patterns", "orders", "tasks")


def upgrade():
    bind = op.get_bind()
    tables = set(sa.inspect(bind).get_table_names())
    if f"{_OLD}_patterns" not in tables or "fulfillment_patterns" in tables:
        return
    indexes = {name for t in (f"{_OLD}_orders", f"{_OLD}_tasks") for name in (i["name"] for i in sa.inspect(bind).get_indexes(t))}
    for name in (f"ix_{_OLD}_orders_crm_order_id", f"ix_{_OLD}_tasks_{_OLD}_order_id", f"ix_{_OLD}_tasks_work_order_id", f"ix_{_OLD}_tasks_external_ref"):
        if name in indexes:
            op.drop_index(name, table_name=f"{_OLD}_orders" if "_orders_" in name else f"{_OLD}_tasks")
    for table in _TABLES:
        op.rename_table(f"{_OLD}_{table}", f"fulfillment_{table}")
    with op.batch_alter_table("fulfillment_tasks") as batch:
        batch.alter_column(f"{_OLD}_order_id", new_column_name="fulfillment_order_id", existing_type=sa.Integer(), existing_nullable=False)
    op.create_index("ix_fulfillment_orders_crm_order_id", "fulfillment_orders", ["crm_order_id"])
    op.create_index("ix_fulfillment_tasks_fulfillment_order_id", "fulfillment_tasks", ["fulfillment_order_id"])
    op.create_index("ix_fulfillment_tasks_work_order_id", "fulfillment_tasks", ["work_order_id"])
    op.create_index("ix_fulfillment_tasks_external_ref", "fulfillment_tasks", ["external_ref"])
    # the orders' timelines: osm_com_opened -> fulfillment_com_opened, osm_fallout -> fulfillment_fallout ...
    op.execute(sa.text("UPDATE order_milestones SET code = 'fulfillment_' || substr(code, :start) WHERE code LIKE :prefix")
               .bindparams(start=len(_OLD) + 2, prefix=f"{_OLD}\\_%" if bind.dialect.name == "postgresql" else f"{_OLD}_%"))


def downgrade():
    # Nothing to undo: e6a8c0d2f4b5 itself creates the fulfillment_* tables.
    pass

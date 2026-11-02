"""Mobile lines on orders and assets (comm_mobile.on_asset_installed): the order line and asset a line belongs
to, who charges its plan, and the plan's profile as sold; plan / pack names as long as catalog product names.

d7f9b1c3e5a7 already creates these on a fresh database; databases that ran its first version (staging) get them
here - each change is applied only where it is missing."""
from alembic import op
import sqlalchemy as sa

revision = "e8a0c2d4f6b8"
down_revision = "d7f9b1c3e5a7"
branch_labels = depends_on = None

_COLUMNS = (
    ("mb_lines", sa.Column("order_item_id", sa.Integer(), nullable=True)),
    ("mb_lines", sa.Column("asset_id", sa.Integer(), nullable=True)),
    ("mb_lines", sa.Column("plan_charge", sa.String(10), nullable=False, server_default="ledger")),
    ("mb_lines", sa.Column("plan_profile", sa.JSON(), nullable=True)),
)
_INDEXES = (("ix_mb_lines_order_item_id", "mb_lines", "order_item_id"), ("ix_mb_lines_asset_id", "mb_lines", "asset_id"))
_WIDER = (("mb_lines", "plan", 60), ("mb_packs", "code", 60), ("mb_packs", "name", 150))


def upgrade():
    inspector = sa.inspect(op.get_bind())
    for table, column in _COLUMNS:
        if column.name not in {c["name"] for c in inspector.get_columns(table)}:
            with op.batch_alter_table(table) as batch:
                batch.add_column(column)
    for name, table, column in _INDEXES:
        if name not in {i["name"] for i in inspector.get_indexes(table)}:
            op.create_index(name, table, [column])
    for table, column, old in _WIDER:
        current = next(c for c in inspector.get_columns(table) if c["name"] == column)
        if getattr(current["type"], "length", None) == old:
            with op.batch_alter_table(table) as batch:
                batch.alter_column(column, type_=sa.String(200), existing_type=sa.String(old), existing_nullable=False)


def downgrade():
    pass  # the columns belong to d7f9b1c3e5a7 on a fresh database - removed with its tables

"""Persist offering-order line prices and discounts."""
from alembic import op
import sqlalchemy as sa

revision = "d4f6a8b0c2e5"
down_revision = "c3e5a7b9d1f4"
branch_labels = None
depends_on = None


def upgrade():
    for name in ("unit_price", "discount_pct", "net_unit_price"):
        op.add_column("order_items", sa.Column(name, sa.Float(), nullable=True))


def downgrade():
    with op.batch_alter_table("order_items") as batch:
        for name in ("net_unit_price", "discount_pct", "unit_price"):
            batch.drop_column(name)

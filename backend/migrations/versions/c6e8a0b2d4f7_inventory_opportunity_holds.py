"""Inventory held for an opportunity: inv_items.opportunity_id - Presales' feasibility check reserves
the resources a deal needs at the location serving its site; the hold moves to the order created from
it, and Fulfillment's SOM steps use exactly those items."""
from alembic import op
import sqlalchemy as sa

revision = "c6e8a0b2d4f7"
down_revision = "b3e5a7c9d1f4"
branch_labels = depends_on = None


def upgrade():
    with op.batch_alter_table("inv_items") as batch:
        batch.add_column(sa.Column("opportunity_id", sa.Integer(), nullable=True))
    op.create_index("ix_inv_items_opportunity_id", "inv_items", ["opportunity_id"])


def downgrade():
    op.drop_index("ix_inv_items_opportunity_id", table_name="inv_items")
    with op.batch_alter_table("inv_items") as batch:
        batch.drop_column("opportunity_id")

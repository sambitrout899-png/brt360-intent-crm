"""Siebel pricing: a pricelist in effect between dates, and volume (quantity tier) prices on its items."""
from alembic import op
import sqlalchemy as sa

revision = "a2b4c6d8e0f1"
down_revision = "f1c3e5a7b9d2"
branch_labels = depends_on = None


def upgrade():
    with op.batch_alter_table("price_lists") as batch:
        batch.add_column(sa.Column("valid_from", sa.Date(), nullable=True))
        batch.add_column(sa.Column("valid_to", sa.Date(), nullable=True))
    with op.batch_alter_table("price_list_items") as batch:
        batch.add_column(sa.Column("min_quantity", sa.Integer(), nullable=False, server_default="1"))
        # one price per product AND tier - a product can have several volume tiers
        batch.drop_constraint("uq_price_list_items_offering", type_="unique")
        batch.create_unique_constraint("uq_price_list_items_offering_tier", ["price_list_id", "offering_name", "min_quantity"])


def downgrade():
    with op.batch_alter_table("price_list_items") as batch:
        batch.drop_constraint("uq_price_list_items_offering_tier", type_="unique")
        batch.create_unique_constraint("uq_price_list_items_offering", ["price_list_id", "offering_name"])
        batch.drop_column("min_quantity")
    with op.batch_alter_table("price_lists") as batch:
        batch.drop_column("valid_to")
        batch.drop_column("valid_from")

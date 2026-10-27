"""Fulfillment patterns started by intents: fulfillment_patterns.trigger_intents -
the vanilla or custom intents (INT-n / FLOW-n) whose completion starts the pattern
on their order. Blank (every existing pattern) = Order Control approval, as before."""
from alembic import op
import sqlalchemy as sa

revision = "a1c3e5f7b9d2"
down_revision = "f8c0e2a4b6d9"
branch_labels = depends_on = None


def upgrade():
    with op.batch_alter_table("fulfillment_patterns") as batch:
        batch.add_column(sa.Column("trigger_intents", sa.Text(), nullable=False, server_default=""))


def downgrade():
    with op.batch_alter_table("fulfillment_patterns") as batch:
        batch.drop_column("trigger_intents")

"""offering_attributes.capture_stage: when an attribute's value is captured -
while selling ("sales", the default when null) or by the installation engineer
during service provisioning ("installation" - network-level attributes,
prefilled from Inventory and never asked for on the quote or at order approval)."""
from alembic import op
import sqlalchemy as sa

revision = "b4d6f8a0c2e3"
down_revision = "a2c4e6f8b0d1"
branch_labels = depends_on = None


def upgrade():
    with op.batch_alter_table("offering_attributes") as batch:
        batch.add_column(sa.Column("capture_stage", sa.String(20), nullable=True))


def downgrade():
    with op.batch_alter_table("offering_attributes") as batch:
        batch.drop_column("capture_stage")

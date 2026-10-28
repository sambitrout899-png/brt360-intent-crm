"""state_model_transitions.allowed_roles: String(100) -> Text.

The column holds a comma-separated list of position names, which outgrows
100 characters (live data: 152). SQLite never enforced the length, so
nothing broke there; Postgres does, and the move to it would fail."""
from alembic import op
import sqlalchemy as sa

revision = "f4a6c8e0b2d1"
down_revision = "e2b4d6f8a0c3"
branch_labels = depends_on = None


def upgrade():
    with op.batch_alter_table("state_model_transitions") as batch_op:
        batch_op.alter_column("allowed_roles", existing_type=sa.String(length=100), type_=sa.Text(), existing_nullable=False)


def downgrade():
    # lists longer than 100 characters would not fit back - Postgres refuses the downgrade then
    with op.batch_alter_table("state_model_transitions") as batch_op:
        batch_op.alter_column("allowed_roles", existing_type=sa.Text(), type_=sa.String(length=100), existing_nullable=False)

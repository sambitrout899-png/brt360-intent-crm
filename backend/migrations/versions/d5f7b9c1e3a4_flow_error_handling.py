"""Flow error handling: a flow's policy for errors it doesn't catch
(fail | continue). The "error" timing and the catch / throw steps need no
schema - they live in the existing timing column and graph_json."""
from alembic import op
import sqlalchemy as sa

revision = "d5f7b9c1e3a4"
down_revision = "c4e6a8b0d2f3"
branch_labels = depends_on = None


def upgrade():
    # every existing flow keeps today's behaviour: an error fails it, and the intent with it
    op.add_column("flow_definitions", sa.Column("on_error", sa.String(10), nullable=False, server_default="fail"))


def downgrade():
    # "error"-timed flows can't exist without this release - switch them off rather than let them run as "after" flows
    op.execute(sa.text("UPDATE flow_definitions SET is_active = :off WHERE timing = 'error'").bindparams(off=False))
    with op.batch_alter_table("flow_definitions") as batch:
        batch.drop_column("on_error")

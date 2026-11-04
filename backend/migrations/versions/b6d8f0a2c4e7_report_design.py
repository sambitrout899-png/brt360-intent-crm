"""Report design: how the tenant's PDF reports look - logo, colours, header, footer, watermark
(app/report_design.py, Admin -> Report Design)."""
from alembic import op
import sqlalchemy as sa

revision = "b6d8f0a2c4e7"
down_revision = "d3f5a7b9c1e4"
branch_labels = depends_on = None


def upgrade():
    with op.batch_alter_table("tenants") as batch:
        batch.add_column(sa.Column("report_design", sa.JSON(), nullable=True))


def downgrade():
    with op.batch_alter_table("tenants") as batch:
        batch.drop_column("report_design")

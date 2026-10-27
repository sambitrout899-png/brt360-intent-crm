"""Quote price override: a negotiated total replacing a quote's computed
price, kept as one row per negotiation round (INT-116)."""
from alembic import op
import sqlalchemy as sa

revision = "c4e6a8b0d2f3"
down_revision = "b3d5f7a9c1e2"
branch_labels = depends_on = None


def upgrade():
    op.create_table(
        "quote_price_overrides",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.Integer(), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("quote_id", sa.Integer(), sa.ForeignKey("quotes.id"), nullable=False),
        sa.Column("list_price", sa.Float(), nullable=False),
        sa.Column("computed_price", sa.Float(), nullable=False),
        sa.Column("override_price", sa.Float(), nullable=False),
        sa.Column("effective_discount_pct", sa.Float(), nullable=False, server_default="0"),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("ended_reason", sa.String(20), nullable=True),
        sa.Column("created_by_user_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("ended_at", sa.DateTime(), nullable=True),
        sa.Column("brt_id", sa.String(20), nullable=False),
        sa.Column("sys_created_at", sa.DateTime(), nullable=True),
        sa.Column("sys_created_by", sa.Integer(), nullable=True),
        sa.Column("sys_created_position_id", sa.Integer(), nullable=True),
        sa.Column("sys_department_id", sa.Integer(), nullable=True),
        sa.Column("sys_updated_at", sa.DateTime(), nullable=True),
        sa.Column("sys_updated_by", sa.Integer(), nullable=True),
        sa.UniqueConstraint("brt_id", name="uq_quote_price_overrides_brt_id"),
    )
    op.create_index("ix_quote_price_overrides_quote_id", "quote_price_overrides", ["quote_id"])


def downgrade():
    op.drop_index("ix_quote_price_overrides_quote_id", table_name="quote_price_overrides")
    op.drop_table("quote_price_overrides")

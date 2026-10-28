"""ai_calls: the AI gateway's record of every model call (prompt audit and
monthly token budgets - app/models/ai_settings.py, AiCall). The migrate
service puts the tenant isolation policy on it afterwards (app/rls.py)."""
from alembic import op
import sqlalchemy as sa

revision = "d1f3a5c7e9b2"
down_revision = "c8e0a2b4d6f9"
branch_labels = depends_on = None


def upgrade():
    op.create_table(
        "ai_calls",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.Integer(), sa.ForeignKey("tenants.id"), nullable=True),
        sa.Column("at", sa.DateTime(), nullable=False),
        sa.Column("provider", sa.String(20), nullable=False),
        sa.Column("model", sa.String(100), nullable=False),
        sa.Column("outcome", sa.String(20), nullable=False),
        sa.Column("prompt_tokens", sa.Integer(), nullable=False),
        sa.Column("completion_tokens", sa.Integer(), nullable=False),
        sa.Column("duration_ms", sa.Integer(), nullable=False),
        sa.Column("prompt_sha256", sa.String(64), nullable=False),
        sa.Column("prompt_chars", sa.Integer(), nullable=False),
        sa.Column("redactions", sa.Integer(), nullable=False),
        sa.Column("brt_id", sa.String(20), nullable=False),
        sa.Column("sys_created_at", sa.DateTime(), nullable=True),
        sa.Column("sys_created_by", sa.Integer(), nullable=True),
        sa.Column("sys_created_position_id", sa.Integer(), nullable=True),
        sa.Column("sys_department_id", sa.Integer(), nullable=True),
        sa.Column("sys_updated_at", sa.DateTime(), nullable=True),
        sa.Column("sys_updated_by", sa.Integer(), nullable=True),
        sa.UniqueConstraint("brt_id", name="uq_ai_calls_brt_id"),
    )
    op.create_index("ix_ai_calls_tenant_at", "ai_calls", ["tenant_id", "at"])


def downgrade():
    op.drop_index("ix_ai_calls_tenant_at", table_name="ai_calls")
    op.drop_table("ai_calls")

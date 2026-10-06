"""phase 6: tenant-configurable AI settings

Net-new table, no backfill needed: every tenant simply has no row until
an admin visits /api/admin/ai-settings, at which point
ai_settings_service.get_or_create() creates one on first read with
api_key=NULL (falls back to the global OPENAI_API_KEY, exactly as
before this table existed).

Revision ID: 2d78011f8fbc
Revises: a411b2d98a0f
Create Date: 2026-10-06 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '2d78011f8fbc'
down_revision: Union[str, Sequence[str], None] = 'a411b2d98a0f'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "ai_settings",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("api_key", sa.String(length=300), nullable=True),
        sa.Column("model", sa.String(length=100), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", name="uq_ai_settings_tenant"),
    )


def downgrade() -> None:
    op.drop_table("ai_settings")

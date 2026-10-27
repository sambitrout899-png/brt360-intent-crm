"""intent catalog: per-tenant intent settings + who ran each intent

- intent_settings: label/description/category/icon overrides, on/off,
  roles allowed, extra phrases, field defaults; field schema for custom
  intents (FLOW-<id>)
- intent_execution_log.user_id - recent intents / remembered values

Revision ID: d5e7f9a1b3c6
Revises: c4d6e8f0a2b4
Create Date: 2026-10-07 18:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd5e7f9a1b3c6'
down_revision: Union[str, Sequence[str], None] = 'c4d6e8f0a2b4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "intent_settings",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("code", sa.String(length=20), nullable=False),
        sa.Column("label", sa.String(length=150), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("category", sa.String(length=50), nullable=True),
        sa.Column("icon", sa.String(length=16), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("role_ids", sa.Text(), nullable=False, server_default=""),
        sa.Column("phrases", sa.Text(), nullable=False, server_default=""),
        sa.Column("defaults_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("fields_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "code", name="uq_intent_settings_tenant_code"),
    )
    with op.batch_alter_table("intent_execution_log") as batch_op:
        batch_op.add_column(sa.Column("user_id", sa.Integer(), nullable=True))
        batch_op.create_foreign_key("fk_intent_execution_log_user_id", "users", ["user_id"], ["id"])


def downgrade() -> None:
    with op.batch_alter_table("intent_execution_log") as batch_op:
        batch_op.drop_constraint("fk_intent_execution_log_user_id", type_="foreignkey")
        batch_op.drop_column("user_id")
    op.drop_table("intent_settings")

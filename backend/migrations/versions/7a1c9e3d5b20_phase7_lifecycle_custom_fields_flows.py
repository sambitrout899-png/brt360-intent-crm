"""phase 7: entity lifecycle (state models, positions, assignment), custom fields, AI Flow Studio

Net-new tables, plus two quote columns for the revision chain (INT-30)
and a 'superseded' QuoteStatus value. No backfill: existing quotes get
revision_number 1 via server_default, and every record simply has no
entity_record_meta row until the lifecycle hook first sees it.

Same enum-change tradeoff as 12200dd5f70a: Postgres can't drop an enum
value, so that half of downgrade is a no-op there.

Revision ID: 7a1c9e3d5b20
Revises: 2d78011f8fbc
Create Date: 2026-10-06 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '7a1c9e3d5b20'
down_revision: Union[str, Sequence[str], None] = '2d78011f8fbc'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

OLD_QUOTE_STATUS_VALUES = ("draft", "pending_approval", "approved", "rejected", "cancelled")
NEW_QUOTE_STATUS_VALUES = ("draft", "pending_approval", "approved", "rejected", "cancelled", "superseded")


def upgrade() -> None:
    op.create_table(
        "state_models",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("entity_type", sa.String(length=30), nullable=False),
        sa.Column("name", sa.String(length=150), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "state_model_states",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("state_model_id", sa.Integer(), nullable=False),
        sa.Column("code", sa.String(length=50), nullable=False),
        sa.Column("label", sa.String(length=100), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("editable", sa.Boolean(), nullable=False),
        sa.Column("is_terminal", sa.Boolean(), nullable=False),
        sa.Column("built_in", sa.Boolean(), nullable=False),
        sa.ForeignKeyConstraint(["state_model_id"], ["state_models.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("state_model_id", "code", name="uq_state_model_states_code"),
    )
    op.create_table(
        "state_model_transitions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("state_model_id", sa.Integer(), nullable=False),
        sa.Column("from_state", sa.String(length=50), nullable=False),
        sa.Column("to_state", sa.String(length=50), nullable=False),
        sa.Column("action", sa.String(length=50), nullable=False),
        sa.Column("intent_type", sa.String(length=20), nullable=True),
        sa.Column("allowed_roles", sa.String(length=100), nullable=False),
        sa.ForeignKeyConstraint(["state_model_id"], ["state_models.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "positions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=150), nullable=False),
        sa.Column("parent_position_id", sa.Integer(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),
        sa.ForeignKeyConstraint(["parent_position_id"], ["positions.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "name", name="uq_positions_tenant_name"),
    )
    op.create_table(
        "position_members",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("position_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["position_id"], ["positions.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("position_id", "user_id", name="uq_position_members"),
    )
    op.create_table(
        "assignment_rules",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=150), nullable=False),
        sa.Column("entity_type", sa.String(length=30), nullable=False),
        sa.Column("on_state", sa.String(length=50), nullable=True),
        sa.Column("position_id", sa.Integer(), nullable=True),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("strategy", sa.String(length=20), nullable=False),
        sa.Column("priority", sa.Integer(), nullable=False),
        sa.Column("last_assigned_user_id", sa.Integer(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),
        sa.ForeignKeyConstraint(["position_id"], ["positions.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "entity_record_meta",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("entity_type", sa.String(length=30), nullable=False),
        sa.Column("entity_id", sa.Integer(), nullable=False),
        sa.Column("owner_user_id", sa.Integer(), nullable=True),
        sa.Column("owner_position_id", sa.Integer(), nullable=True),
        sa.Column("sub_state", sa.String(length=50), nullable=True),
        sa.Column("sub_state_anchor", sa.String(length=50), nullable=True),
        sa.Column("last_seen_state", sa.String(length=50), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),
        sa.ForeignKeyConstraint(["owner_user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["owner_position_id"], ["positions.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "entity_type", "entity_id", name="uq_entity_record_meta"),
    )
    op.create_table(
        "entity_assignment_history",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("entity_type", sa.String(length=30), nullable=False),
        sa.Column("entity_id", sa.Integer(), nullable=False),
        sa.Column("from_user_id", sa.Integer(), nullable=True),
        sa.Column("from_position_id", sa.Integer(), nullable=True),
        sa.Column("to_user_id", sa.Integer(), nullable=True),
        sa.Column("to_position_id", sa.Integer(), nullable=True),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("source", sa.String(length=50), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "entity_state_history",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("entity_type", sa.String(length=30), nullable=False),
        sa.Column("entity_id", sa.Integer(), nullable=False),
        sa.Column("from_state", sa.String(length=50), nullable=True),
        sa.Column("to_state", sa.String(length=50), nullable=False),
        sa.Column("source", sa.String(length=50), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "custom_field_definitions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("entity_type", sa.String(length=30), nullable=False),
        sa.Column("key", sa.String(length=60), nullable=False),
        sa.Column("label", sa.String(length=150), nullable=False),
        sa.Column("data_type", sa.String(length=20), nullable=False),
        sa.Column("allowed_values", sa.Text(), nullable=True),
        sa.Column("required", sa.Boolean(), nullable=False),
        sa.Column("default_value", sa.String(length=300), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "entity_type", "key", name="uq_custom_field_definitions_key"),
    )
    op.create_table(
        "custom_field_values",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("field_id", sa.Integer(), nullable=False),
        sa.Column("entity_type", sa.String(length=30), nullable=False),
        sa.Column("entity_id", sa.Integer(), nullable=False),
        sa.Column("value", sa.Text(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),
        sa.ForeignKeyConstraint(["field_id"], ["custom_field_definitions.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("field_id", "entity_id", name="uq_custom_field_values"),
    )
    op.create_table(
        "flow_definitions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=150), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("entity_type", sa.String(length=30), nullable=True),
        sa.Column("trigger_type", sa.String(length=20), nullable=False),
        sa.Column("trigger_intent_type", sa.String(length=20), nullable=True),
        sa.Column("timing", sa.String(length=10), nullable=False),
        sa.Column("trigger_state", sa.String(length=50), nullable=True),
        sa.Column("trigger_phrases", sa.Text(), nullable=True),
        sa.Column("required_fields", sa.String(length=300), nullable=True),
        sa.Column("graph_json", sa.Text(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),
        sa.PrimaryKeyConstraint("id"),
    )

    # --- quotes: revision chain + 'superseded' status ---------------------
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        with op.get_context().autocommit_block():
            op.execute("ALTER TYPE quotestatus ADD VALUE IF NOT EXISTS 'superseded'")
    with op.batch_alter_table("quotes") as batch_op:
        batch_op.add_column(sa.Column("revision_number", sa.Integer(), nullable=False, server_default="1"))
        batch_op.add_column(sa.Column("revision_of_quote_id", sa.Integer(), nullable=True))
        batch_op.create_foreign_key("fk_quotes_revision_of_quote_id", "quotes", ["revision_of_quote_id"], ["id"])
        if bind.dialect.name != "postgresql":
            batch_op.alter_column(
                "status",
                existing_type=sa.Enum(*OLD_QUOTE_STATUS_VALUES, name="quotestatus"),
                type_=sa.Enum(*NEW_QUOTE_STATUS_VALUES, name="quotestatus"),
            )


def downgrade() -> None:
    bind = op.get_bind()
    with op.batch_alter_table("quotes") as batch_op:
        batch_op.drop_constraint("fk_quotes_revision_of_quote_id", type_="foreignkey")
        batch_op.drop_column("revision_of_quote_id")
        batch_op.drop_column("revision_number")
        if bind.dialect.name != "postgresql":
            batch_op.alter_column(
                "status",
                existing_type=sa.Enum(*NEW_QUOTE_STATUS_VALUES, name="quotestatus"),
                type_=sa.Enum(*OLD_QUOTE_STATUS_VALUES, name="quotestatus"),
            )

    op.drop_table("flow_definitions")
    op.drop_table("custom_field_values")
    op.drop_table("custom_field_definitions")
    op.drop_table("entity_state_history")
    op.drop_table("entity_assignment_history")
    op.drop_table("entity_record_meta")
    op.drop_table("assignment_rules")
    op.drop_table("position_members")
    op.drop_table("positions")
    op.drop_table("state_model_transitions")
    op.drop_table("state_model_states")
    op.drop_table("state_models")

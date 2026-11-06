"""Integration Hub: clients, published services, connections, operations, subscriptions, the message log;
BIM interfaces with source keys, scheduled pulls and acknowledgements; refused rows kept to reprocess

Revision ID: 7e3a9c1f5b2d
Revises: a7c9e2f4b6d8
Create Date: 2026-11-06 18:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '7e3a9c1f5b2d'
down_revision: Union[str, Sequence[str], None] = 'a7c9e2f4b6d8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _table(name: str, *columns) -> None:
    """Every table also carries the BRT_ID and the system audit columns (app/brt_id.py)."""
    op.create_table(
        name, *columns,
        sa.Column("brt_id", sa.String(20), nullable=False),
        sa.Column("sys_created_at", sa.DateTime(), nullable=True),
        sa.Column("sys_created_by", sa.Integer(), nullable=True),
        sa.Column("sys_created_position_id", sa.Integer(), nullable=True),
        sa.Column("sys_department_id", sa.Integer(), nullable=True),
        sa.Column("sys_updated_at", sa.DateTime(), nullable=True),
        sa.Column("sys_updated_by", sa.Integer(), nullable=True),
        sa.UniqueConstraint("brt_id", name=f"uq_{name}_brt_id"),
    )


def upgrade() -> None:
    _table(
        "hub_clients",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.Integer(), sa.ForeignKey("tenants.id"), nullable=False, index=True),
        sa.Column("code", sa.String(60), nullable=False),
        sa.Column("name", sa.String(150), nullable=False),
        sa.Column("key_id", sa.String(24), nullable=False, unique=True),
        sa.Column("key_hash", sa.String(64), nullable=False),
        sa.Column("previous_key_hash", sa.String(64), nullable=True),
        sa.Column("previous_key_until", sa.DateTime(), nullable=True),
        sa.Column("run_as_user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("allowed_services_json", sa.Text(), nullable=False),
        sa.Column("ip_allowlist", sa.Text(), nullable=False),
        sa.Column("rate_per_minute", sa.Integer(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("last_used_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("tenant_id", "code", name="uq_hub_clients_code"),
    )
    _table(
        "hub_connections",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.Integer(), sa.ForeignKey("tenants.id"), nullable=False, index=True),
        sa.Column("code", sa.String(60), nullable=False),
        sa.Column("name", sa.String(150), nullable=False),
        sa.Column("base_url", sa.String(500), nullable=False),
        sa.Column("auth_type", sa.String(30), nullable=False),
        sa.Column("auth_header", sa.String(100), nullable=False),
        sa.Column("username", sa.String(200), nullable=False),
        sa.Column("secret", sa.Text(), nullable=False),
        sa.Column("token_url", sa.String(500), nullable=False),
        sa.Column("oauth_scope", sa.String(300), nullable=False),
        sa.Column("headers_json", sa.Text(), nullable=False),
        sa.Column("timeout_seconds", sa.Integer(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("failure_count", sa.Integer(), nullable=False),
        sa.Column("circuit_open_until", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("tenant_id", "code", name="uq_hub_connections_code"),
    )
    _table(
        "hub_operations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.Integer(), sa.ForeignKey("tenants.id"), nullable=False, index=True),
        sa.Column("connection_id", sa.Integer(), sa.ForeignKey("hub_connections.id"), nullable=False, index=True),
        sa.Column("code", sa.String(60), nullable=False),
        sa.Column("name", sa.String(150), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("protocol", sa.String(10), nullable=False),
        sa.Column("method", sa.String(10), nullable=False),
        sa.Column("path", sa.String(500), nullable=False),
        sa.Column("headers_json", sa.Text(), nullable=False),
        sa.Column("body_template", sa.Text(), nullable=False),
        sa.Column("soap_action", sa.String(300), nullable=False),
        sa.Column("soap_version", sa.String(5), nullable=False),
        sa.Column("pattern", sa.String(20), nullable=False),
        sa.Column("mode", sa.String(10), nullable=False),
        sa.Column("reply_via", sa.String(10), nullable=False),
        sa.Column("max_attempts", sa.Integer(), nullable=False),
        sa.Column("backoff_seconds", sa.Integer(), nullable=False),
        sa.Column("reply_timeout_minutes", sa.Integer(), nullable=False),
        sa.Column("reply_mapping_json", sa.Text(), nullable=False),
        sa.Column("reply_interface_id", sa.Integer(), sa.ForeignKey("bim_interfaces.id"), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("tenant_id", "code", name="uq_hub_operations_code"),
    )
    _table(
        "hub_services",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.Integer(), sa.ForeignKey("tenants.id"), nullable=False, index=True),
        sa.Column("code", sa.String(60), nullable=False),
        sa.Column("name", sa.String(150), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("target_type", sa.String(20), nullable=False),
        sa.Column("target", sa.String(60), nullable=False),
        sa.Column("mode", sa.String(10), nullable=False),
        sa.Column("fields_json", sa.Text(), nullable=False),
        sa.Column("input_mapping_json", sa.Text(), nullable=False),
        sa.Column("output_mapping_json", sa.Text(), nullable=False),
        sa.Column("callback_operation_id", sa.Integer(), sa.ForeignKey("hub_operations.id"), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("tenant_id", "code", name="uq_hub_services_code"),
    )
    _table(
        "hub_subscriptions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.Integer(), sa.ForeignKey("tenants.id"), nullable=False, index=True),
        sa.Column("name", sa.String(150), nullable=False),
        sa.Column("event_pattern", sa.String(150), nullable=False),
        sa.Column("operation_id", sa.Integer(), sa.ForeignKey("hub_operations.id"), nullable=False),
        sa.Column("start_event_id", sa.Integer(), nullable=False),
        sa.Column("last_event_id", sa.Integer(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    _table(
        "hub_messages",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.Integer(), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("correlation_id", sa.String(64), nullable=False, index=True),
        sa.Column("direction", sa.String(5), nullable=False),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("protocol", sa.String(10), nullable=False),
        sa.Column("pattern", sa.String(20), nullable=False),
        sa.Column("mode", sa.String(10), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("max_attempts", sa.Integer(), nullable=False),
        sa.Column("next_attempt_at", sa.DateTime(), nullable=True),
        sa.Column("locked_until", sa.DateTime(), nullable=True),
        sa.Column("client_id", sa.Integer(), sa.ForeignKey("hub_clients.id"), nullable=True),
        sa.Column("service_code", sa.String(60), nullable=True),
        sa.Column("idempotency_key", sa.String(200), nullable=True),
        sa.Column("connection_id", sa.Integer(), sa.ForeignKey("hub_connections.id"), nullable=True),
        sa.Column("operation_id", sa.Integer(), sa.ForeignKey("hub_operations.id"), nullable=True),
        sa.Column("subscription_id", sa.Integer(), sa.ForeignKey("hub_subscriptions.id"), nullable=True),
        sa.Column("event_id", sa.Integer(), nullable=True),
        sa.Column("reply_token_hash", sa.String(64), nullable=True, unique=True),
        sa.Column("reply_deadline", sa.DateTime(), nullable=True),
        sa.Column("flow_run_id", sa.Integer(), nullable=True),
        sa.Column("source", sa.String(100), nullable=False),
        sa.Column("execution_id", sa.Integer(), nullable=True),
        sa.Column("http_method", sa.String(10), nullable=False),
        sa.Column("url", sa.String(1000), nullable=False),
        sa.Column("request_headers", sa.Text(), nullable=False),
        sa.Column("payload", sa.Text(), nullable=False),
        sa.Column("request_body", sa.Text(), nullable=False),
        sa.Column("http_status", sa.Integer(), nullable=True),
        sa.Column("response_body", sa.Text(), nullable=False),
        sa.Column("reply_json", sa.Text(), nullable=False),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("error", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint("client_id", "service_code", "idempotency_key", name="uq_hub_messages_idempotency"),
        sa.UniqueConstraint("subscription_id", "event_id", name="uq_hub_messages_event"),
    )
    op.create_index("ix_hub_messages_due", "hub_messages", ["status", "next_attempt_at"])
    op.create_index("ix_hub_messages_tenant", "hub_messages", ["tenant_id", "id"])
    _table(
        "bim_external_refs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.Integer(), sa.ForeignKey("tenants.id"), nullable=False, index=True),
        sa.Column("system", sa.String(60), nullable=False),
        sa.Column("entity", sa.String(40), nullable=False),
        sa.Column("external_id", sa.String(200), nullable=False),
        sa.Column("record_brt_id", sa.String(20), nullable=False, index=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("tenant_id", "system", "entity", "external_id", name="uq_bim_external_refs"),
    )
    with op.batch_alter_table("bim_interfaces") as batch:
        batch.add_column(sa.Column("ignore_unmapped", sa.Boolean(), nullable=False, server_default=sa.false()))
        batch.add_column(sa.Column("source_system", sa.String(60), nullable=False, server_default=""))
        batch.add_column(sa.Column("key_field", sa.String(200), nullable=False, server_default=""))
        batch.add_column(sa.Column("ack_operation_id", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("pull_operation_id", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("pull_every_minutes", sa.Integer(), nullable=False, server_default="0"))
        batch.add_column(sa.Column("pull_records_path", sa.String(200), nullable=False, server_default=""))
        batch.add_column(sa.Column("pull_last_at", sa.DateTime(), nullable=True))
        batch.add_column(sa.Column("pull_cursor", sa.String(100), nullable=False, server_default=""))
        batch.create_foreign_key("fk_bim_interfaces_ack_op", "hub_operations", ["ack_operation_id"], ["id"])
        batch.create_foreign_key("fk_bim_interfaces_pull_op", "hub_operations", ["pull_operation_id"], ["id"])
    with op.batch_alter_table("bim_jobs") as batch:
        batch.add_column(sa.Column("failed_rows_json", sa.Text(), nullable=False, server_default=""))
        batch.add_column(sa.Column("parent_job_id", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("message_id", sa.Integer(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("bim_jobs") as batch:
        batch.drop_column("message_id")
        batch.drop_column("parent_job_id")
        batch.drop_column("failed_rows_json")
    with op.batch_alter_table("bim_interfaces") as batch:
        batch.drop_constraint("fk_bim_interfaces_pull_op", type_="foreignkey")
        batch.drop_constraint("fk_bim_interfaces_ack_op", type_="foreignkey")
        for column in ("pull_cursor", "pull_last_at", "pull_records_path", "pull_every_minutes", "pull_operation_id", "ack_operation_id",
                       "key_field", "source_system", "ignore_unmapped"):
            batch.drop_column(column)
    op.drop_table("bim_external_refs")
    op.drop_index("ix_hub_messages_tenant", table_name="hub_messages")
    op.drop_index("ix_hub_messages_due", table_name="hub_messages")
    op.drop_table("hub_messages")
    op.drop_table("hub_subscriptions")
    op.drop_table("hub_services")
    op.drop_table("hub_operations")
    op.drop_table("hub_connections")
    op.drop_table("hub_clients")

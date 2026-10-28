"""audit_chain: the tamper-evident audit chain (app/crm_core/audit_chain.py).

On Postgres a trigger refuses UPDATE and DELETE on it - the app can only
append. (A database owner can still drop the trigger; the chain's hashes
are what show a change was made.)"""
from alembic import op
import sqlalchemy as sa

revision = "e2b4c6d8f0a1"
down_revision = "d1f3a5c7e9b2"
branch_labels = depends_on = None


def upgrade():
    op.create_table(
        "audit_chain",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.Integer(), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("at", sa.DateTime(), nullable=False),
        sa.Column("source", sa.String(40), nullable=False),
        sa.Column("source_id", sa.Integer(), nullable=False),
        sa.Column("record_sha256", sa.String(64), nullable=False),
        sa.Column("prev_hash", sa.String(64), nullable=False),
        sa.Column("hash", sa.String(64), nullable=False),
        sa.Column("brt_id", sa.String(20), nullable=False),
        sa.Column("sys_created_at", sa.DateTime(), nullable=True),
        sa.Column("sys_created_by", sa.Integer(), nullable=True),
        sa.Column("sys_created_position_id", sa.Integer(), nullable=True),
        sa.Column("sys_department_id", sa.Integer(), nullable=True),
        sa.Column("sys_updated_at", sa.DateTime(), nullable=True),
        sa.Column("sys_updated_by", sa.Integer(), nullable=True),
        sa.UniqueConstraint("brt_id", name="uq_audit_chain_brt_id"),
        sa.UniqueConstraint("source", "source_id", name="uq_audit_chain_source"),
    )
    op.create_index("ix_audit_chain_tenant_seq", "audit_chain", ["tenant_id", "seq"])
    if op.get_bind().dialect.name == "postgresql":
        op.execute("""
            CREATE FUNCTION audit_chain_append_only() RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN
                RAISE EXCEPTION 'audit_chain is append-only';
            END $$""")
        op.execute("CREATE TRIGGER audit_chain_append_only BEFORE UPDATE OR DELETE ON audit_chain "
                   "FOR EACH ROW EXECUTE FUNCTION audit_chain_append_only()")


def downgrade():
    if op.get_bind().dialect.name == "postgresql":
        op.execute("DROP TRIGGER IF EXISTS audit_chain_append_only ON audit_chain")
        op.execute("DROP FUNCTION IF EXISTS audit_chain_append_only()")
    op.drop_index("ix_audit_chain_tenant_seq", table_name="audit_chain")
    op.drop_table("audit_chain")

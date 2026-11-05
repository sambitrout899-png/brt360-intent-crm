"""Security testing (app/models/security_tests.py): VAPT assessments and their findings with evidence."""
from alembic import op
import sqlalchemy as sa

revision = "c9e1a3b5d7f2"
down_revision = "b6d8f0a2c4e7"
branch_labels = depends_on = None


def _create(name, *columns):
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


def upgrade():
    _create(
        "security_test_runs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.Integer(), sa.ForeignKey("tenants.id"), nullable=False, index=True),
        sa.Column("number", sa.String(30), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("started_by", sa.Integer(), nullable=True),
        sa.Column("scope", sa.JSON(), nullable=True),
        sa.Column("total", sa.Integer(), nullable=False),
        sa.Column("done", sa.Integer(), nullable=False),
        sa.Column("passed", sa.Integer(), nullable=False),
        sa.Column("failed", sa.Integer(), nullable=False),
        sa.Column("errors", sa.Integer(), nullable=False),
        sa.Column("skipped", sa.Integer(), nullable=False),
        sa.Column("score", sa.Float(), nullable=True),
        sa.Column("grade", sa.String(5), nullable=True),
        sa.Column("summary", sa.JSON(), nullable=True),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
    )
    _create(
        "security_test_results",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.Integer(), sa.ForeignKey("tenants.id"), nullable=False, index=True),
        sa.Column("run_id", sa.Integer(), sa.ForeignKey("security_test_runs.id"), nullable=False, index=True),
        sa.Column("code", sa.String(20), nullable=False),
        sa.Column("category", sa.String(60), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("severity", sa.String(10), nullable=False),
        sa.Column("cvss", sa.Float(), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("duration_ms", sa.Integer(), nullable=False),
        sa.Column("evidence", sa.JSON(), nullable=True),
        sa.Column("tester_note", sa.Text(), nullable=False),
        sa.Column("recorded_by", sa.Integer(), nullable=True),
        sa.Column("recorded_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    # AI provider keys encrypted at rest (app/secret_box.py) - the ciphertext needs more room than the key
    with op.batch_alter_table("ai_settings") as batch:
        batch.alter_column("api_key", type_=sa.Text(), existing_type=sa.String(300), existing_nullable=True)
    from app.secret_box import is_sealed, seal

    conn = op.get_bind()
    for row_id, key in conn.execute(sa.text("SELECT id, api_key FROM ai_settings WHERE api_key IS NOT NULL AND api_key <> ''")).all():
        if not is_sealed(key):
            conn.execute(sa.text("UPDATE ai_settings SET api_key = :k WHERE id = :i"), {"k": seal(key), "i": row_id})


def downgrade():
    from app.secret_box import is_sealed, open_

    conn = op.get_bind()
    for row_id, key in conn.execute(sa.text("SELECT id, api_key FROM ai_settings WHERE api_key IS NOT NULL")).all():
        if is_sealed(key):
            conn.execute(sa.text("UPDATE ai_settings SET api_key = :k WHERE id = :i"), {"k": open_(key), "i": row_id})
    with op.batch_alter_table("ai_settings") as batch:
        batch.alter_column("api_key", type_=sa.String(300), existing_type=sa.Text(), existing_nullable=True)
    op.drop_table("security_test_results")
    op.drop_table("security_test_runs")

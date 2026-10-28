"""Sign-in security: account lockout and two-step sign-in (TOTP) on users,
and the tenant setting that requires it (app/crm_core/login_security.py)."""
from alembic import op
import sqlalchemy as sa

revision = "f3c5e7a9b1d4"
down_revision = "e2b4c6d8f0a1"
branch_labels = depends_on = None


def upgrade():
    with op.batch_alter_table("users") as batch:
        batch.add_column(sa.Column("failed_logins", sa.Integer(), nullable=False, server_default="0"))
        batch.add_column(sa.Column("locked_until", sa.DateTime(), nullable=True))
        batch.add_column(sa.Column("mfa_enabled", sa.Boolean(), nullable=False, server_default=sa.false()))
        batch.add_column(sa.Column("mfa_secret_enc", sa.String(300), nullable=True))
        batch.add_column(sa.Column("mfa_last_step", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("mfa_recovery_hashes", sa.Text(), nullable=True))
    with op.batch_alter_table("tenants") as batch:
        batch.add_column(sa.Column("mfa_required", sa.Boolean(), nullable=False, server_default=sa.false()))


def downgrade():
    with op.batch_alter_table("tenants") as batch:
        batch.drop_column("mfa_required")
    with op.batch_alter_table("users") as batch:
        for column in ("mfa_recovery_hashes", "mfa_last_step", "mfa_secret_enc", "mfa_enabled", "locked_until", "failed_logins"):
            batch.drop_column(column)

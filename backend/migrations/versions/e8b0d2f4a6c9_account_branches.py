"""Separate billing/service ownership and preserve reconciled placeholder assets."""
from alembic import op
import sqlalchemy as sa

revision = "e8b0d2f4a6c9"
down_revision = "e5a7c9d1f3b6"
branch_labels = depends_on = None


def upgrade():
    for table, name, target in (("service_accounts", "party_id", "parties.id"),
                                ("cm_billing_profiles", "billing_account_id", "billing_accounts.id"),
                                ("assets", "superseded_by_asset_id", "assets.id")):
        with op.batch_alter_table(table) as batch:
            batch.add_column(sa.Column(name, sa.Integer(), nullable=True))
            batch.create_foreign_key(f"fk_{table}_{name}", target.split(".")[0], [name], [target.split(".")[1]])
            batch.create_index(f"ix_{table}_{name}", [name])
    op.execute("UPDATE service_accounts SET party_id = (SELECT party_id FROM billing_accounts WHERE billing_accounts.id = service_accounts.billing_account_id AND billing_accounts.tenant_id = service_accounts.tenant_id)")
    op.execute("UPDATE cm_billing_profiles SET billing_account_id = (SELECT id FROM billing_accounts WHERE billing_accounts.party_id = cm_billing_profiles.party_id AND billing_accounts.tenant_id = cm_billing_profiles.tenant_id)")


def downgrade():
    for table, name in (("assets", "superseded_by_asset_id"), ("cm_billing_profiles", "billing_account_id"), ("service_accounts", "party_id")):
        with op.batch_alter_table(table) as batch:
            batch.drop_index(f"ix_{table}_{name}")
            batch.drop_constraint(f"fk_{table}_{name}", type_="foreignkey")
            batch.drop_column(name)

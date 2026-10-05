"""identity verification kyc

Phase 3 (B2C process pack): KYC/identity verification. Brand new tables,
no backfill needed for existing rows - but seeds the BRD-named providers
(Yakeen, WATHQ, SIMAH, Abshir) for every existing tenant inline, same as
118e58982bce did for approval chain templates, so this feature is usable
immediately without a separate seed run.

Revision ID: 93a570a0f595
Revises: 12200dd5f70a
Create Date: 2026-10-05 12:39:51.711465

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '93a570a0f595'
down_revision: Union[str, Sequence[str], None] = '12200dd5f70a'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

DEFAULT_IDENTITY_PROVIDERS = ["Yakeen", "WATHQ", "SIMAH", "Abshir"]


def upgrade() -> None:
    bind = op.get_bind()

    op.create_table(
        "identity_verification_providers",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "identity_verifications",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=False),
        sa.Column("party_id", sa.Integer(), nullable=False),
        sa.Column("provider_name", sa.String(length=100), nullable=False),
        sa.Column("id_type", sa.String(length=50), nullable=False),
        sa.Column("id_last4", sa.String(length=4), nullable=False),
        sa.Column("status", sa.Enum("pending", "verified", "rejected", name="verificationstatus"), nullable=False),
        sa.Column("detail", sa.String(length=300), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("resolved_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"]),
        sa.ForeignKeyConstraint(["party_id"], ["parties.id"]),
        sa.PrimaryKeyConstraint("id"),
    )

    providers_table = sa.table(
        "identity_verification_providers",
        sa.column("id", sa.Integer()),
        sa.column("tenant_id", sa.Integer()),
        sa.column("name", sa.String()),
        sa.column("is_active", sa.Boolean()),
        sa.column("created_at", sa.DateTime()),
    )

    tenant_ids = [row[0] for row in bind.execute(sa.text("SELECT id FROM tenants")).fetchall()]
    for tenant_id in tenant_ids:
        for name in DEFAULT_IDENTITY_PROVIDERS:
            bind.execute(
                providers_table.insert().values(tenant_id=tenant_id, name=name, is_active=True, created_at=sa.func.now())
            )


def downgrade() -> None:
    op.drop_table("identity_verifications")
    op.drop_table("identity_verification_providers")

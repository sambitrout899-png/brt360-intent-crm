"""fix stale global unique constraints from tenant migration

af04ebec9bad (the original tenant_id migration) added tenant_id to
product_specifications and territory_rules and the model stopped
declaring unique=True on their name/region columns - but never actually
dropped the old global UNIQUE constraint the pre-multi-tenant schema had
on those columns. Invisible locally because SQLite test databases are
always created fresh from the current model (which never had the
constraint), so this only surfaced the first time a second tenant was
ever created against the real production database and its seed data
collided with the "default" tenant's identical product/territory names.

Replaces the old global uniqueness with the per-tenant-scoped version
that was actually intended: UNIQUE(tenant_id, name) and
UNIQUE(tenant_id, region).

Revision ID: 137090a72994
Revises: 2f289a462815
Create Date: 2026-10-05 15:21:57.045062

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '137090a72994'
down_revision: Union[str, Sequence[str], None] = '2f289a462815'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# (table, column) pairs that used to have a bare UniqueConstraint(column)
# in the pre-multi-tenant schema.
STALE_CONSTRAINTS = [
    ("product_specifications", "name"),
    ("territory_rules", "region"),
]


def upgrade() -> None:
    bind = op.get_bind()

    if bind.dialect.name == "postgresql":
        # Found by name, not assumed - Alembic/Postgres's default naming
        # convention isn't guaranteed, and guessing wrong here (again)
        # isn't worth the risk.
        for table, column in STALE_CONSTRAINTS:
            constraint_name = bind.execute(
                sa.text(
                    "SELECT con.conname FROM pg_constraint con "
                    "JOIN pg_class rel ON rel.oid = con.conrelid "
                    "JOIN pg_attribute att ON att.attrelid = rel.oid AND att.attnum = ANY(con.conkey) "
                    "WHERE rel.relname = :table AND con.contype = 'u' AND att.attname = :column"
                ),
                {"table": table, "column": column},
            ).scalar_one_or_none()
            if constraint_name:
                op.drop_constraint(constraint_name, table, type_="unique")

    with op.batch_alter_table("product_specifications") as batch_op:
        batch_op.create_unique_constraint("uq_product_specifications_tenant_name", ["tenant_id", "name"])
    with op.batch_alter_table("territory_rules") as batch_op:
        batch_op.create_unique_constraint("uq_territory_rules_tenant_region", ["tenant_id", "region"])


def downgrade() -> None:
    with op.batch_alter_table("territory_rules") as batch_op:
        batch_op.drop_constraint("uq_territory_rules_tenant_region", type_="unique")
    with op.batch_alter_table("product_specifications") as batch_op:
        batch_op.drop_constraint("uq_product_specifications_tenant_name", type_="unique")

    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.create_unique_constraint("product_specifications_name_key", "product_specifications", ["name"])
        op.create_unique_constraint("territory_rules_region_key", "territory_rules", ["region"])

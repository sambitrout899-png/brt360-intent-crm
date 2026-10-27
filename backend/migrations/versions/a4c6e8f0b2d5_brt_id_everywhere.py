"""BRT_ID and system fields on every table - existing data kept.

1. brt_id_counter - the register of minted BRT_ID numbers (app/brt_id.py).
2. Every table gains brt_id and the system fields sys_created_at,
   sys_created_by, sys_created_position_id, sys_department_id,
   sys_updated_at, sys_updated_by.
3. Every existing row is given its BRT_ID (minted with the server's
   BRT_ID secret - set BRT_ID_SECRET before running this); created-at comes
   from the row's own created_at where it has one, and created-by /
   position / department from the record ownership data where kept.
4. brt_id becomes required and unique: from here on no record exists
   without one.

Revision ID: a4c6e8f0b2d5
Revises: f3b5d7e9a1c4
"""

import datetime as dt
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "a4c6e8f0b2d5"
down_revision: Union[str, Sequence[str], None] = "f3b5d7e9a1c4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

SKIP = {"alembic_version", "brt_id_counter"}
SYSTEM_COLUMNS = [
    ("sys_created_at", sa.DateTime()),
    ("sys_created_by", sa.Integer()),
    ("sys_created_position_id", sa.Integer()),
    ("sys_department_id", sa.Integer()),
    ("sys_updated_at", sa.DateTime()),
    ("sys_updated_by", sa.Integer()),
]
# record types whose ownership (who created it, as which position, in which department) is kept in entity_record_meta
OWNED = {"parties": "party", "leads": "lead", "opportunities": "opportunity", "quotes": "quote", "orders": "order"}


def _tables(bind) -> list[str]:
    return [t for t in sa.inspect(bind).get_table_names() if t not in SKIP]


def upgrade() -> None:
    from app import brt_id

    bind = op.get_bind()
    tables = _tables(bind)
    op.create_table("brt_id_counter", sa.Column("id", sa.Integer(), primary_key=True), sa.Column("value", sa.Integer(), nullable=False))
    op.execute("INSERT INTO brt_id_counter (id, value) VALUES (1, 0)")

    for table in tables:
        op.add_column(table, sa.Column("brt_id", sa.String(20), nullable=True))
        for name, type_ in SYSTEM_COLUMNS:
            op.add_column(table, sa.Column(name, type_, nullable=True))

    now = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)
    inspector = sa.inspect(bind)
    for table in tables:
        pk = inspector.get_pk_constraint(table)["constrained_columns"]
        columns = {c["name"] for c in inspector.get_columns(table)}
        order = ", ".join(pk) if pk else "rowid"
        keys = pk or ["rowid"]
        rows = bind.execute(sa.text(f"SELECT {', '.join(keys)}{', created_at' if 'created_at' in columns else ''} FROM {table} ORDER BY {order}")).fetchall()
        if not rows:
            continue
        numbers = brt_id.reserve(bind, len(rows))
        owners: dict[int, tuple] = {}
        if table in OWNED:
            owners = {
                r[0]: (r[1], r[2], r[3])
                for r in bind.execute(sa.text(
                    "SELECT entity_id, created_by_user_id, created_by_position_id, department_id FROM entity_record_meta WHERE entity_type = :t"
                ), {"t": OWNED[table]}).fetchall()
            }
        for row, number in zip(rows, numbers):
            key_values = dict(zip(keys, row[: len(keys)]))
            created = row[len(keys)] if "created_at" in columns else None
            owner = owners.get(key_values.get("id"), (None, None, None))
            where = " AND ".join(f"{k} = :k_{k}" for k in keys)
            bind.execute(
                sa.text(f"UPDATE {table} SET brt_id = :brt_id, sys_created_at = :created, sys_created_by = :by, "
                        f"sys_created_position_id = :pos, sys_department_id = :dep WHERE {where}"),
                {"brt_id": brt_id.code_for(number, table), "created": created or now, "by": owner[0], "pos": owner[1], "dep": owner[2],
                 **{f"k_{k}": v for k, v in key_values.items()}},
            )

    for table in tables:
        with op.batch_alter_table(table) as batch_op:
            batch_op.alter_column("brt_id", existing_type=sa.String(20), nullable=False)
            batch_op.create_unique_constraint(f"uq_{table}_brt_id", ["brt_id"])


def downgrade() -> None:
    bind = op.get_bind()
    for table in _tables(bind):
        with op.batch_alter_table(table) as batch_op:
            batch_op.drop_constraint(f"uq_{table}_brt_id", type_="unique")
            batch_op.drop_column("brt_id")
            for name, _ in SYSTEM_COLUMNS:
                batch_op.drop_column(name)
    op.drop_table("brt_id_counter")

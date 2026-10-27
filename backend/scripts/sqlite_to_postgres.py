"""Copy BRT 360's SQLite database into Postgres - the one-time move.

Run with the app stopped (nothing may write to SQLite meanwhile), after
`alembic upgrade head` has built the schema on Postgres:

    python scripts/sqlite_to_postgres.py sqlite:////data/brt360.db --yes

The target is DATABASE_URL, read like the app reads it. What it does:
1. both databases must be at the same Alembic revision (same schema)
2. every table on the target is emptied - the seed rows the migrations
   wrote go; the copy brings the real ones (hence --yes)
3. every table is copied in batches, foreign key checks off for the copy
   (tables can then go in any order); booleans SQLite kept as 0/1 become
   true/false
4. every id sequence moves past the copied ids, so new rows don't collide
5. row counts are compared table by table - any difference fails the run

Nothing is written to the SQLite file. Re-running starts over (step 2).
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # the backend dir, for `app`

from sqlalchemy import Boolean, Integer, MetaData, create_engine, func, select, text  # noqa: E402

from app.config import get_settings  # noqa: E402

BATCH = 1000
SKIP = {"alembic_version"}


def _revision(engine) -> str | None:
    with engine.connect() as conn:
        return conn.execute(text("SELECT version_num FROM alembic_version")).scalar()


def _count(engine, table) -> int:
    with engine.connect() as conn:
        return conn.execute(select(func.count()).select_from(table)).scalar()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("sqlite_url", help="the source, e.g. sqlite:////data/brt360.db")
    parser.add_argument("--yes", action="store_true", help="empty the Postgres tables before the copy")
    args = parser.parse_args()

    target_url = get_settings().database_url
    if not target_url.startswith("postgresql"):
        print(f"DATABASE_URL must point at Postgres, not {target_url.split(':')[0]}")
        return 2
    if not args.yes:
        print("This empties every table in the Postgres database first. Re-run with --yes to go ahead.")
        return 2

    src = create_engine(args.sqlite_url)
    dst = create_engine(target_url)

    src_rev, dst_rev = _revision(src), _revision(dst)
    if src_rev != dst_rev:
        print(f"Schema mismatch: SQLite is at {src_rev}, Postgres at {dst_rev} - run `alembic upgrade head` on both first")
        return 1

    src_meta, dst_meta = MetaData(), MetaData()
    src_meta.reflect(src)
    dst_meta.reflect(dst)
    lost = sorted(set(src_meta.tables) - set(dst_meta.tables) - SKIP)
    if lost:
        print(f"Tables in SQLite but not in Postgres (their data would be lost): {', '.join(lost)}")
        return 1
    tables = [t for t in dst_meta.sorted_tables if t.name not in SKIP]
    for table in tables:
        if table.name in src_meta.tables:
            extra = sorted(set(src_meta.tables[table.name].columns.keys()) - set(table.columns.keys()))
            if extra:
                print(f"{table.name}: columns in SQLite but not in Postgres: {', '.join(extra)}")
                return 1

    started = time.monotonic()
    print(f"Copying {len(tables)} tables at revision {src_rev}")
    with dst.begin() as conn:
        conn.execute(text("SET session_replication_role = replica"))  # foreign key checks off for this session
        conn.execute(text("TRUNCATE " + ", ".join(f'"{t.name}"' for t in tables) + " RESTART IDENTITY CASCADE"))
        for table in tables:
            source = src_meta.tables.get(table.name)
            if source is None:
                continue
            columns = [c.name for c in table.columns if c.name in source.columns]
            booleans = {c.name for c in table.columns if isinstance(c.type, Boolean)}
            copied = 0
            with src.connect() as sconn:
                rows = sconn.execution_options(stream_results=True).execute(select(*[source.c[c] for c in columns]))
                while batch := rows.fetchmany(BATCH):
                    values = []
                    for row in batch:
                        record = dict(zip(columns, row))
                        for name in booleans & record.keys():
                            if record[name] is not None and not isinstance(record[name], bool):
                                record[name] = bool(int(record[name]))
                        values.append(record)
                    conn.execute(table.insert(), values)
                    copied += len(values)
            if copied:
                print(f"  {table.name}: {copied}")
        conn.execute(text("SET session_replication_role = DEFAULT"))
        # new ids continue after the copied ones
        for table in tables:
            for column in table.primary_key.columns:
                if not isinstance(column.type, Integer):
                    continue
                sequence = conn.execute(text("SELECT pg_get_serial_sequence(:t, :c)"), {"t": f'"{table.name}"', "c": column.name}).scalar()
                if sequence:
                    conn.execute(text(f'SELECT setval(:s, COALESCE((SELECT MAX("{column.name}") FROM "{table.name}"), 0) + 1, false)'),
                                 {"s": sequence})

    mismatches = []
    for table in tables:
        source = src_meta.tables.get(table.name)
        want = _count(src, source) if source is not None else 0
        got = _count(dst, table)
        if want != got:
            mismatches.append(f"{table.name}: SQLite {want}, Postgres {got}")
    if mismatches:
        print("ROW COUNTS DIFFER:\n  " + "\n  ".join(mismatches))
        return 1
    print(f"Done in {time.monotonic() - started:.0f}s - every table's row count matches.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

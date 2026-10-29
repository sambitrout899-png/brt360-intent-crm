"""Where one request's time goes - on the server, against its own database.

Runs a path in-process (no network, no other callers) N times as the load
test's user (scripts/load_test.py setup: the "loadtest" tenant, AI on rules)
and prints, per request: time, SQL statements and the time spent waiting on
them, the slowest statements, and the app code that took the most time.

    cd /opt/brt360-staging/deploy/compose
    docker compose run --rm --no-deps api python scripts/profile_request.py create --yes-this-is-staging

Paths: create (INT-15), update (INT-29), read (360 view), classify, bim (a load of --rows prospects).
"""

from __future__ import annotations

import argparse
import collections
import cProfile
import io
import pstats
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # the backend dir, for `app`


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("path", choices=["create", "update", "read", "classify", "bim"])
    parser.add_argument("-n", type=int, default=30, help="requests to profile [30]")
    parser.add_argument("--rows", type=int, default=500, help="bim: rows in the load [500]")
    parser.add_argument("--yes-this-is-staging", action="store_true", help="required: this writes test data")
    args = parser.parse_args()
    if not args.yes_this_is_staging:
        sys.exit("Run it on staging only, and say so: --yes-this-is-staging")

    from fastapi.testclient import TestClient
    from sqlalchemy import event

    from app.db import engine
    from app.main import app
    from scripts.load_test import setup

    _, token = setup()
    client = TestClient(app, headers={"Authorization": f"Bearer {token}"})
    stamp = time.strftime("%H%M%S")

    def create(i: int) -> int | None:
        body = client.post("/api/intents", json={"text": "p", "structured": {"intent_type": "INT-15", "entities": {"name": f"PR {stamp} {i}"}}}).json()
        return (body.get("result") or {}).get("party_id")

    party = create(0)
    if not party:
        sys.exit("Couldn't create a prospect to work on")

    def bim(i: int) -> None:
        ids = client.post("/api/bim/brt-ids", json={"entity": "prospect", "count": args.rows}).json()["brt_ids"]
        csv = "\n".join(["brt_id,name,email,segment,tax_id,city"] + [f"{b},PR {stamp} bim {i}-{k},pr{k}@example.invalid,B2B,,Pune" for k, b in enumerate(ids)])
        # a dry run answers in the request (no background job) and keeps nothing - the per-row work is the same
        client.post("/api/bim/load", json={"entity": "prospect", "content": csv, "dry_run": True})

    calls = {
        "create": create,
        "update": lambda i: client.post("/api/intents", json={"text": "p", "structured": {"intent_type": "INT-29", "entities": {
            "entity_type": "party", "entity_id": party, "fields": {"name": f"PR {stamp} renamed {i}"}}}}),
        "read": lambda i: client.get(f"/api/parties/{party}/360"),
        "classify": lambda i: client.post("/api/intents/assist", json={"text": "create a lead for Acme Telecom"}),
        "bim": bim,
    }
    call = calls[args.path]
    n = 1 if args.path == "bim" else args.n
    for i in range(2):
        call(i)  # warm up

    statements: list[tuple[str, float]] = []
    started_at: dict[int, float] = {}

    @event.listens_for(engine, "before_cursor_execute")
    def _before(conn, cursor, statement, parameters, context, executemany):
        started_at[id(cursor)] = time.perf_counter()

    @event.listens_for(engine, "after_cursor_execute")
    def _after(conn, cursor, statement, parameters, context, executemany):
        statements.append((re.sub(r"\s+", " ", statement)[:120], (time.perf_counter() - started_at.pop(id(cursor), time.perf_counter())) * 1000))

    profiler = cProfile.Profile()
    t0 = time.perf_counter()
    profiler.enable()
    for i in range(n):
        call(i + 10)
    profiler.disable()
    total_ms = (time.perf_counter() - t0) * 1000
    per = n if args.path != "bim" else args.rows
    unit = "request" if args.path != "bim" else "row"
    db_ms = sum(ms for _, ms in statements)

    print(f"\n{args.path}: {total_ms / per:.1f} ms per {unit} (profiler on - real time is lower), "
          f"{len(statements) / per:.1f} SQL statements per {unit}, {db_ms / per:.1f} ms of them waiting on the database")
    by_stmt: dict[str, list[float]] = collections.defaultdict(list)
    for stmt, ms in statements:
        by_stmt[stmt].append(ms)
    print(f"\nStatements that took the most time in all (per {unit}: count, ms):")
    for stmt, times in sorted(by_stmt.items(), key=lambda kv: -sum(kv[1]))[:12]:
        print(f"  {len(times) / per:6.2f} x {sum(times) / per:7.2f} ms  {stmt}")
    out = io.StringIO()
    pstats.Stats(profiler, stream=out).sort_stats("cumulative").print_stats(r"[\\/]app[\\/](?!.*site-packages)", 30)
    print("\nApp code by cumulative time (all calls):")
    print("\n".join(line for line in out.getvalue().splitlines() if re.search(r"[\\/]app[\\/]", line) or "ncalls" in line))


if __name__ == "__main__":
    main()

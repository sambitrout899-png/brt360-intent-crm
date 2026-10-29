"""Load test against the plan's targets (architecture plan, "Performance targets").

Run it on the server, against STAGING, from inside its api container - so it
measures the server, not your internet connection:

    cd /opt/brt360-staging/deploy/compose
    docker compose run --rm --no-deps api python scripts/load_test.py --yes-this-is-staging

Everything it creates lives in its own tenant ("loadtest", AI set to built-in
rules, so no model call is made or paid for): prospects named "LT <run> <n>",
one flow, and the BIM rows. It signs in as its own admin user by minting a
token directly - no password exists or is needed. Other tenants' data is not
touched. Results print as a table (--json FILE also saves them).

Scenarios (each runs --duration seconds with --concurrency parallel callers):
  create   an intent that creates a prospect (INT-15)       p95 < 200 ms
  read     a prospect's 360 view                             p95 < 200 ms
  update   an intent that renames a prospect (INT-29)        p95 < 200 ms
  classify the command bar understanding text, rules only    p95 < 300 ms
  flow     Flow Studio simulation of a 10-step flow, per step p95 < 100 ms
  bim      one BIM load of --bim-rows prospects              < 120 s for 10,000
"""

from __future__ import annotations

import argparse
import asyncio
import json
import random
import secrets
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # the backend dir, for `app`

import httpx  # noqa: E402

TARGETS_MS = {"create": 200, "read": 200, "update": 200, "classify": 300, "flow": 100}
BIM_TARGET_S = 120
PHRASES = ["create a lead for Acme Telecom", "show the open quotes", "raise a ticket for a broken router",
           "add a 10% discount to quote 12", "new prospect Globex in Pune", "move the order to activation"]


def setup() -> tuple[int, str]:
    """The loadtest tenant (AI on rules) and an admin token for it."""
    from app.auth import create_access_token
    from app.db import SessionLocal
    from app.models.ai_settings import AiSettings
    from app.models.auth import UserRole
    from app.seed import get_or_create_tenant, get_or_create_user, seed_reference_data

    with SessionLocal() as db:
        tenant = get_or_create_tenant(db, "loadtest", "Load test")
        seed_reference_data(db, tenant.id)
        settings = db.query(AiSettings).filter(AiSettings.tenant_id == tenant.id).first() or AiSettings(tenant_id=tenant.id)
        settings.provider = "rules"
        db.add(settings)
        user = get_or_create_user(db, tenant_id=tenant.id, email="loadtest@brt360.invalid", password=secrets.token_urlsafe(24),
                                  name="Load test", role=UserRole.admin)
        db.commit()
        return tenant.id, create_access_token(user)


class Stats:
    def __init__(self) -> None:
        self.ms: list[float] = []
        self.errors: list[str] = []

    def add(self, started: float, ok: bool, detail: str = "") -> None:
        self.ms.append((time.perf_counter() - started) * 1000)
        if not ok:
            self.errors.append(detail[:200])

    def pct(self, p: float) -> float:
        if not self.ms:
            return float("nan")
        ordered = sorted(self.ms)
        return ordered[min(len(ordered) - 1, int(round(p / 100 * (len(ordered) - 1))))]


async def run(name: str, call, duration: float, concurrency: int) -> Stats:
    stats = Stats()
    stop = time.perf_counter() + duration

    async def caller(i: int) -> None:
        n = 0
        while time.perf_counter() < stop:
            n += 1
            await call(stats, i, n)

    print(f"  {name}: {concurrency} callers for {duration:g} s ...", flush=True)
    await asyncio.gather(*(caller(i) for i in range(concurrency)))
    return stats


def _party_id(body: dict) -> int | None:
    for key in ("party_id", "prospect_id"):
        value = (body.get("result") or {}).get(key)
        if value:
            return int(value)
    for record in body.get("records") or []:
        if record.get("entity_type") == "party" and record.get("entity_id"):
            return int(record["entity_id"])
    return None


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--base", default="http://web", help="where the site answers, from inside the container [http://web]")
    parser.add_argument("--duration", type=float, default=30, help="seconds per scenario [30]")
    parser.add_argument("--concurrency", type=int, default=8, help="parallel callers [8]")
    parser.add_argument("--bim-rows", type=int, default=10000, help="rows in the BIM load (0 = skip) [10000]")
    parser.add_argument("--yes-this-is-staging", action="store_true", help="required: this writes test data")
    parser.add_argument("--json", default="", help="also save the results to this file")
    args = parser.parse_args()
    if not args.yes_this_is_staging:
        sys.exit("Run it on staging only, and say so: --yes-this-is-staging")

    _, token = setup()
    run_id = time.strftime("%m%d%H%M")
    headers = {"Authorization": f"Bearer {token}"}
    results: dict[str, Stats] = {}
    parties: list[int] = []

    async with httpx.AsyncClient(base_url=args.base, headers=headers, timeout=180) as client:
        health = await client.get("/api/health")
        if health.status_code != 200:
            sys.exit(f"{args.base}/api/health answered {health.status_code} - is --base right?")

        async def create(stats: Stats, i: int, n: int) -> None:
            started = time.perf_counter()
            resp = await client.post("/api/intents", json={"text": "load test", "structured": {
                "intent_type": "INT-15", "entities": {"name": f"LT {run_id} {i}-{n}"}}})
            body = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {}
            ok = resp.status_code == 200 and body.get("status") == "executed"
            stats.add(started, ok, f"{resp.status_code} {body.get('status')} {str(body)[:120]}")
            if ok and (pid := _party_id(body)):
                parties.append(pid)

        async def read(stats: Stats, i: int, n: int) -> None:
            started = time.perf_counter()
            resp = await client.get(f"/api/parties/{random.choice(parties)}/360")
            stats.add(started, resp.status_code == 200, f"{resp.status_code} {resp.text[:120]}")

        async def update(stats: Stats, i: int, n: int) -> None:
            started = time.perf_counter()
            resp = await client.post("/api/intents", json={"text": "load test", "structured": {"intent_type": "INT-29", "entities": {
                "entity_type": "party", "entity_id": random.choice(parties), "fields": {"name": f"LT {run_id} renamed {i}-{n}"}}}})
            ok = resp.status_code == 200 and resp.json().get("status") == "executed"
            stats.add(started, ok, f"{resp.status_code} {resp.text[:120]}")

        async def classify(stats: Stats, i: int, n: int) -> None:
            started = time.perf_counter()
            resp = await client.post("/api/intents/assist", json={"text": random.choice(PHRASES)})
            stats.add(started, resp.status_code == 200, f"{resp.status_code} {resp.text[:120]}")

        print("Scenarios:")
        results["create"] = await run("create", create, args.duration, args.concurrency)
        if not parties:
            sys.exit("No prospect was created - see the errors: " + "; ".join(results["create"].errors[:3]))
        results["read"] = await run("read", read, args.duration, args.concurrency)
        results["update"] = await run("update", update, args.duration, args.concurrency)
        results["classify"] = await run("classify", classify, args.duration, args.concurrency)

        # flow steps: a 10-step flow (conditions and value mapping - no writes), simulated on a prospect
        steps = 10
        nodes = [{"id": "s", "type": "start", "label": "Start", "config": {}}]
        for k in range(steps):
            nodes.append({"id": f"c{k}", "type": "condition", "label": f"Check {k}", "config": {"field": "record.name", "operator": "not_empty"}}
                         if k % 2 == 0 else {"id": f"c{k}", "type": "map", "label": f"Map {k}", "config": {"values": {"n": k, "who": "{{record.name}}"}}})
        nodes.append({"id": "end", "type": "end", "label": "End", "config": {}})
        edges = [{"id": f"e{k}", "source": nodes[k]["id"], "target": nodes[k + 1]["id"], "label": "true" if nodes[k]["type"] == "condition" else ""}
                 for k in range(len(nodes) - 1)]
        made = await client.post("/api/admin/flows", json={"name": f"Load test {run_id}", "entity_type": "party", "trigger_type": "intent",
                                                           "trigger_intent_type": "INT-15", "timing": "after",
                                                           "graph": {"nodes": nodes, "edges": edges}})
        if made.status_code not in (200, 201):
            sys.exit(f"Couldn't create the test flow: {made.status_code} {made.text[:300]}")
        flow = made.json()  # never activated: simulation runs it, nothing else does

        async def flow_step(stats: Stats, i: int, n: int) -> None:
            started = time.perf_counter()
            resp = await client.post(f"/api/admin/flows/{flow['id']}/simulate", json={"entity_id": random.choice(parties)})
            ok = resp.status_code == 200 and resp.json().get("ok")
            stats.ms.append((time.perf_counter() - started) * 1000 / steps)  # per step
            if not ok:
                stats.errors.append(f"{resp.status_code} {resp.text[:200]}")

        results["flow"] = await run("flow (per step)", flow_step, args.duration, args.concurrency)
        await client.delete(f"/api/admin/flows/{flow['id']}")

        bim_seconds = None
        bim_note = "skipped"
        if args.bim_rows:
            print(f"  bim: one load of {args.bim_rows} prospects ...", flush=True)
            ids: list[str] = []
            while len(ids) < args.bim_rows:
                got = await client.post("/api/bim/brt-ids", json={"entity": "prospect", "count": min(10000, args.bim_rows - len(ids))})
                ids += got.json()["brt_ids"]
            csv = "\n".join(["brt_id,name,email,segment,tax_id,city"] +
                            [f"{b},LT {run_id} bim {k},lt{k}@example.invalid,B2B,,Pune" for k, b in enumerate(ids)])
            started = time.perf_counter()
            job = await client.post("/api/bim/load", json={"entity": "prospect", "content": csv, "file_name": f"loadtest-{run_id}.csv"})
            body = job.json() if job.status_code == 200 else {}
            # a large load runs in the background: follow the job until it's done
            while body.get("status") in ("queued", "running") and time.perf_counter() - started < 600:
                await asyncio.sleep(1)
                body = (await client.get(f"/api/bim/jobs/{body['id']}")).json()
            bim_seconds = time.perf_counter() - started
            bim_note = (f"{body.get('inserted', 0)} inserted, {body.get('failed', '?')} failed ({body.get('status')})" if body
                        else f"HTTP {job.status_code}: {job.text[:150]}")

    # ---------------------------------------------------------------- report
    print(f"\n{'scenario':<16}{'calls':>7}{'errors':>8}{'per s':>8}{'p50 ms':>9}{'p95 ms':>9}{'p99 ms':>9}{'target':>9}  result")
    report = {}
    passed = True
    for name, stats in results.items():
        p95 = stats.pct(95)
        ok = p95 < TARGETS_MS[name] and not stats.errors
        passed &= ok
        rate = len(stats.ms) / args.duration
        report[name] = {"calls": len(stats.ms), "errors": len(stats.errors), "per_second": round(rate, 1), "p50_ms": round(stats.pct(50), 1),
                        "p95_ms": round(p95, 1), "p99_ms": round(stats.pct(99), 1), "target_p95_ms": TARGETS_MS[name], "pass": ok,
                        "first_errors": stats.errors[:3]}
        print(f"{name:<16}{len(stats.ms):>7}{len(stats.errors):>8}{rate:>8.1f}{stats.pct(50):>9.1f}{p95:>9.1f}{stats.pct(99):>9.1f}"
              f"{'<' + str(TARGETS_MS[name]):>9}  {'PASS' if ok else 'FAIL'}")
    if bim_seconds is not None:
        target = BIM_TARGET_S * args.bim_rows / 10000
        ok = bim_seconds < target and "0 failed" in bim_note
        passed &= ok
        report["bim"] = {"rows": args.bim_rows, "seconds": round(bim_seconds, 1), "target_seconds": target, "detail": bim_note, "pass": ok}
        print(f"{'bim':<16}{args.bim_rows:>7} rows in {bim_seconds:.1f} s (target < {target:g} s) - {bim_note}  {'PASS' if ok else 'FAIL'}")
    for name, stats in results.items():
        if stats.errors:
            print(f"  {name} errors, first: {stats.errors[0]}")
    if args.json:
        Path(args.json).write_text(json.dumps({"run": run_id, "concurrency": args.concurrency, "duration": args.duration,
                                               "results": report}, indent=2))
    print(f"\n{'All targets met' if passed else 'Some targets missed'} "
          f"(median of all calls: {statistics.median([m for s in results.values() for m in s.ms]):.0f} ms)")
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))

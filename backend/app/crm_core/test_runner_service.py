"""Runs the backend's real pytest suite as a subprocess, triggered from
the Admin UI. Tests run against their own isolated in-memory SQLite
fixture (tests/conftest.py) - this never touches the tenant's real data,
so it's safe to run against a live deployment, not just locally.

tests/test_admin_test_runner.py (the one file that calls this endpoint)
is excluded from the subprocess's own collection via --ignore. Without
that, the spawned pytest would collect and run that test too, which
would spawn ANOTHER subprocess pytest run, which would do the same
again - unbounded recursive process spawning, not just a slow test.

No concurrency guard beyond a simple in-process lock: two overlapping
runs would interleave confusingly and waste CPU for no benefit, so a
second trigger while one is in flight is rejected outright rather than
queued.
"""

from __future__ import annotations

import re
import subprocess
import sys
import threading
import time
from pathlib import Path

from sqlalchemy.orm import Session

from app.models.testing import TestRun, TestRunStatus
from app.timeutils import utcnow

_BACKEND_DIR = Path(__file__).resolve().parents[2]
_lock = threading.Lock()


def is_running() -> bool:
    return _lock.locked()


def _parse_summary(output: str) -> dict[str, int]:
    passed = failed = errors = 0
    m = re.search(r"(\d+) passed", output)
    if m:
        passed = int(m.group(1))
    m = re.search(r"(\d+) failed", output)
    if m:
        failed = int(m.group(1))
    m = re.search(r"(\d+) error", output)
    if m:
        errors = int(m.group(1))
    return {"total": passed + failed + errors, "passed": passed, "failed": failed, "errors": errors}


def run_suite(db: Session, tenant_id: int) -> TestRun:
    if not _lock.acquire(blocking=False):
        raise RuntimeError("A test run is already in progress - wait for it to finish.")
    try:
        run = TestRun(tenant_id=tenant_id, status=TestRunStatus.running)
        db.add(run)
        db.commit()
        db.refresh(run)

        start = time.monotonic()
        try:
            proc = subprocess.run(
                [sys.executable, "-m", "pytest", "-q", "--tb=short", "--ignore=tests/test_admin_test_runner.py"],
                cwd=_BACKEND_DIR,
                capture_output=True,
                text=True,
                # the suite has grown past 5 minutes on a busy server - give it room
                timeout=1200,
            )
            output = proc.stdout + ("\n" + proc.stderr if proc.stderr else "")
            duration = time.monotonic() - start
            counts = _parse_summary(output)
            status = TestRunStatus.passed if proc.returncode == 0 else TestRunStatus.failed
        except subprocess.TimeoutExpired as e:
            output = (e.stdout or "") + "\n[TIMED OUT after 1200s]"
            duration = time.monotonic() - start
            counts = {"total": 0, "passed": 0, "failed": 0, "errors": 1}
            status = TestRunStatus.error

        run.status = status
        run.total = counts["total"]
        run.passed = counts["passed"]
        run.failed = counts["failed"]
        run.errors = counts["errors"]
        run.duration_seconds = round(duration, 2)
        run.output = output[-20000:]  # cap stored output size
        run.completed_at = utcnow()
        db.commit()
        db.refresh(run)
        return run
    finally:
        _lock.release()

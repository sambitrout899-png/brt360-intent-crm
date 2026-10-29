"""Background jobs: inventory sync, inventory reservation expiry, training
reminders, Flow Studio waits and the audit chain, every minute.

Exactly one process may run them. Under gunicorn every worker runs the app's
lifespan, so with N workers each job used to run N times a minute. Now
SCHEDULER_MODE decides:
- "embedded" (default): the API worker that takes the lock file first runs
  them; the other workers skip. If that worker dies, gunicorn's replacement
  takes the lock on its startup.
- "off": the API never runs them - a separate process does
  (`python -m app.scheduler`), as the container setup will.
"""

from __future__ import annotations

import asyncio
import logging
import os
import tempfile
import time

from app.config import get_settings
from app.db import SessionLocal

log = logging.getLogger(__name__)

INTERVAL_SECONDS = 60


def _inventory_sync() -> None:
    from app.crm_core.inventory_sync_service import run_due

    run_due(SessionLocal)


def _reservation_expiry() -> None:
    # inventory reservations no open order needs any more go back
    from app.crm_core import inventory_service

    inventory_service.run_due(SessionLocal)


def _training() -> None:
    from app.crm_core.training_service import run_due_training

    run_due_training(SessionLocal)


def _flow_runs() -> None:
    # Flow Studio waits whose time has come, and approval requests past their expiry
    from app.orchestration import flow_runs

    flow_runs.run_due(SessionLocal)


def _audit_chain() -> None:
    # finished intent runs and training audit entries into the tamper-evident chain
    from app.crm_core import audit_chain

    with SessionLocal() as db:
        audit_chain.extend(db)


JOBS = (
    ("Inventory scheduler", _inventory_sync),
    ("Inventory reservation expiry", _reservation_expiry),
    ("Training scheduler", _training),
    ("Flow waits", _flow_runs),
    ("Audit chain", _audit_chain),
)

# Jobs that belong to a split-out service (app/services/): with that service in
# SPLIT_SERVICES its own worker runs them (`python -m app.scheduler --service training`,
# from its own image), so a release of the service updates its jobs too.
SERVICE_OF = {"Training scheduler": "training", "Flow waits": "flow"}


def jobs_for(service: str | None = None) -> list[tuple[str, object]]:
    """service's own jobs; without one, every job no split service has taken."""
    if service:
        return [(name, job) for name, job in JOBS if SERVICE_OF.get(name) == service]
    split = set(get_settings().split_services_list)
    return [(name, job) for name, job in JOBS if SERVICE_OF.get(name) not in split]


def run_jobs_once(service: str | None = None) -> None:
    """Each job on its own: one failing never stops the others."""
    for name, job in jobs_for(service):
        try:
            job()
        except Exception:
            log.exception("%s failed", name)


async def run_forever() -> None:
    while True:
        await asyncio.to_thread(run_jobs_once)
        await asyncio.sleep(INTERVAL_SECONDS)


def claim():
    """The scheduler lock, or None when another process already holds it.
    Held for the life of the process - the OS drops it when the process ends."""
    try:
        import fcntl
    except ImportError:  # Windows dev machine: a single process, nothing to share
        return True
    path = get_settings().scheduler_lock_file or os.path.join(tempfile.gettempdir(), "brt360-scheduler.lock")
    handle = open(path, "a")
    try:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        handle.close()
        return None
    return handle


def release(lock) -> None:
    if lock is not None and lock is not True:
        lock.close()


if __name__ == "__main__":
    # The dedicated scheduler process (SCHEDULER_MODE=off on the API), or with
    # --service NAME a split service's own (its jobs only).
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--service", default=None)
    service = parser.parse_args().service
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    from app.services.split_app import install_hooks

    install_hooks()  # as the API has them: jobs that activate an order make the prospect an account
    if claim() is None:
        raise SystemExit("Another process already runs the scheduler - not starting a second one")
    log.info("Scheduler running every %s s: %s", INTERVAL_SECONDS, ", ".join(name for name, _ in jobs_for(service)) or "no jobs")
    while True:
        run_jobs_once(service)
        time.sleep(INTERVAL_SECONDS)

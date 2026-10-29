"""The whole platform in one app - development and the tests.

It is the core service with every other service in it (app/services/split_app.py,
create("core")): SPLIT_SERVICES is empty here, so nothing is left out. In
production each service runs in its own container and the api container
serves core alone, from the same builder. Here, with SCHEDULER_MODE
"embedded", one worker also runs the background jobs.

Schema is Alembic-driven (migrations/) - run `alembic upgrade head` before
starting the app, in both dev and production. Nothing here creates tables.
"""

import asyncio
from contextlib import asynccontextmanager, suppress

from app import scheduler
from app.config import get_settings
from app.services import split_app


async def inventory_worker():
    # the background jobs, every minute (a test swaps this out to keep them off its database)
    await scheduler.run_forever()


@asynccontextmanager
async def lifespan(app):
    # Only one API worker runs the background jobs (app/scheduler.py).
    lock = scheduler.claim() if get_settings().scheduler_mode == "embedded" else None
    task = asyncio.create_task(inventory_worker()) if lock is not None else None
    try:
        yield
    finally:
        if task is not None:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
        scheduler.release(lock)


app = split_app.create(split_app.CORE, lifespan=lifespan)

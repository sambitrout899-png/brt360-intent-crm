import asyncio
from contextlib import asynccontextmanager, suppress
from app import scheduler
from app.experience import routes_inventory_sync

async def inventory_worker():
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

from app import service
from app.config import get_settings
from app.experience import (
    routes_admin,
    routes_auth,
    routes_c360,
    routes_comms,
    routes_crm,
    routes_field_service,
    routes_notices,
    routes_order_mgmt,
    routes_org,
    routes_fulfillment,
    routes_inventory,
    routes_tenants,
)

# Schema is Alembic-driven (migrations/) - run `alembic upgrade head` before
# starting the app, in both dev and production. Nothing here creates tables.

app = service.new_app("BRT 360 — Intent CRM", lifespan=lifespan)

from app.services.split_app import install_hooks  # noqa: E402

install_hooks()  # the session hooks every process that writes records needs (split services and worker too)

service.include(
    app,
    routes_auth.router,
    routes_crm.router,
    routes_admin.router,
    routes_tenants.router,
    routes_org.router,
    routes_org.access_router,
    routes_c360.router,
    routes_c360.admin_router,
    routes_field_service.router,
    routes_order_mgmt.router,
    routes_order_mgmt.admin_router,
    routes_fulfillment.router,
    routes_fulfillment.admin_router,
    routes_inventory.router,
    routes_inventory.presales_router,
    routes_comms.router,
    routes_comms.admin_router,
    routes_notices.router,
    routes_notices.admin_router,
)

from app.experience import routes_analytics  # noqa: E402

service.include(app, routes_analytics.router)
service.finish(app)  # /api/health, /metrics
service.include(app, routes_inventory_sync.router)

from app.experience import routes_modules  # noqa: E402

service.include(app, routes_modules.router)

# Services that run in their own container when SPLIT_SERVICES names them
# (app/services/<name>.py); otherwise this app serves their routes too.
import importlib  # noqa: E402

from app.services.split_app import SERVICES as _SPLITTABLE  # noqa: E402

for _name in _SPLITTABLE:
    if not service.split(_name):
        service.include(app, *importlib.import_module(f"app.services.{_name}").routers())

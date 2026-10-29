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
    routes_agent,
    routes_assistant,
    routes_auth,
    routes_builder,
    routes_c360,
    routes_comms,
    routes_conflicts,
    routes_crm,
    routes_field_service,
    routes_function_map,
    routes_intent,
    routes_intent_admin,
    routes_notices,
    routes_order_mgmt,
    routes_org,
    routes_fulfillment,
    routes_inventory,
    routes_process,
    routes_studio,
    routes_tenants,
)

# Schema is Alembic-driven (migrations/) - run `alembic upgrade head` before
# starting the app, in both dev and production. Nothing here creates tables.

app = service.new_app("BRT 360 — Intent CRM", lifespan=lifespan)

from app.crm_core import process_service as _process_service  # noqa: E402

_process_service.install_account_conversion()

service.include(
    app,
    routes_auth.router,
    routes_intent.router,
    routes_intent_admin.router,
    routes_crm.router,
    routes_admin.router,
    routes_tenants.router,
    routes_studio.router,
    routes_studio.approvals_router,
    routes_org.router,
    routes_org.access_router,
    routes_c360.router,
    routes_c360.admin_router,
    routes_process.admin_router,
    routes_process.router,
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
    routes_agent.router,
    routes_builder.router,
    routes_assistant.router,
    routes_notices.admin_router,
    routes_function_map.router,
    routes_conflicts.router,
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

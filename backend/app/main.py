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
    routes_bim,
    routes_builder,
    routes_c360,
    routes_catalog,
    routes_comms,
    routes_configurator,
    routes_conflicts,
    routes_crm,
    routes_custom_records,
    routes_field_service,
    routes_function_map,
    routes_intent,
    routes_intent_admin,
    routes_notices,
    routes_order_mgmt,
    routes_org,
    routes_fulfillment,
    routes_inventory,
    routes_pages,
    routes_process,
    routes_product_config,
    routes_product_model,
    routes_records,
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
    routes_catalog.router,
    routes_crm.router,
    routes_admin.router,
    routes_tenants.router,
    routes_records.router,
    routes_studio.router,
    routes_custom_records.admin_router,
    routes_custom_records.router,
    routes_org.router,
    routes_org.access_router,
    routes_c360.router,
    routes_c360.admin_router,
    routes_process.admin_router,
    routes_process.router,
    routes_product_config.router,
    routes_product_config.admin_router,
    routes_configurator.router,
    routes_configurator.tmf_router,
    routes_configurator.admin_router,
    routes_configurator.classes_router,
    routes_product_model.router,
    routes_product_model.admin_router,
    routes_pages.router,
    routes_pages.admin_router,
    routes_bim.router,
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

# Training runs in its own container when SPLIT_SERVICES names it (app/services/training.py).
if not service.split("training"):
    from app.services import training as _training

    service.include(app, *_training.routers())

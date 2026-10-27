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

from fastapi import FastAPI
from app.licensing import include_module_router
from fastapi.middleware.cors import CORSMiddleware

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

app = FastAPI(title="BRT 360 — Intent CRM", version="0.1.0-sprint1", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=get_settings().cors_allowed_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-Example-Count"],
)

from app.crm_core import process_service as _process_service  # noqa: E402

_process_service.install_account_conversion()


@app.middleware("http")
async def _record_who_acts(request, call_next):
    """Records saved during this request are stamped with who saved them
    (app/brt_id.py) - read from the verified token; no token, no stamp.
    The verified claims are kept on request.state, so get_current_user
    doesn't decode the token a second time."""
    from app import brt_id
    from app.auth import decode_token

    token = request.headers.get("authorization", "")
    payload = decode_token(token[7:]) if token.lower().startswith("bearer ") else None
    request.state.token_payload = payload
    try:
        user_id = int(payload["sub"]) if payload else None
    except (KeyError, TypeError, ValueError):
        user_id = None
    marker = brt_id.current_user.set({"user_id": user_id} if user_id else None)
    try:
        return await call_next(request)
    finally:
        brt_id.current_user.reset(marker)


include_module_router(app, routes_auth.router)
include_module_router(app, routes_intent.router)
include_module_router(app, routes_intent_admin.router)
include_module_router(app, routes_catalog.router)
include_module_router(app, routes_crm.router)
include_module_router(app, routes_admin.router)
include_module_router(app, routes_tenants.router)
include_module_router(app, routes_records.router)
include_module_router(app, routes_studio.router)
include_module_router(app, routes_custom_records.admin_router)
include_module_router(app, routes_custom_records.router)
include_module_router(app, routes_org.router)
include_module_router(app, routes_org.access_router)
include_module_router(app, routes_c360.router)
include_module_router(app, routes_c360.admin_router)
include_module_router(app, routes_process.admin_router)
include_module_router(app, routes_process.router)
include_module_router(app, routes_product_config.router)
include_module_router(app, routes_product_config.admin_router)
include_module_router(app, routes_configurator.router)
include_module_router(app, routes_configurator.tmf_router)
include_module_router(app, routes_configurator.admin_router)
include_module_router(app, routes_configurator.classes_router)
include_module_router(app, routes_product_model.router)
include_module_router(app, routes_product_model.admin_router)
include_module_router(app, routes_pages.router)
include_module_router(app, routes_pages.admin_router)
include_module_router(app, routes_bim.router)
include_module_router(app, routes_field_service.router)
include_module_router(app, routes_order_mgmt.router)
include_module_router(app, routes_order_mgmt.admin_router)
include_module_router(app, routes_fulfillment.router)
include_module_router(app, routes_fulfillment.admin_router)
include_module_router(app, routes_inventory.router)
include_module_router(app, routes_inventory.presales_router)
include_module_router(app, routes_comms.router)
include_module_router(app, routes_comms.admin_router)
include_module_router(app, routes_notices.router)
include_module_router(app, routes_agent.router)
include_module_router(app, routes_builder.router)
include_module_router(app, routes_assistant.router)
include_module_router(app, routes_notices.admin_router)
include_module_router(app, routes_function_map.router)
include_module_router(app, routes_conflicts.router)

from app.experience import routes_analytics  # noqa: E402
include_module_router(app, routes_analytics.router)


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


from app import observability  # noqa: E402

observability.install(app)  # /metrics, and request counts + latency per route

include_module_router(app, routes_inventory_sync.router)

from app.experience import routes_training, routes_modules, routes_training_brd
include_module_router(app, routes_training.router)
include_module_router(app, routes_training.public_router)
include_module_router(app, routes_modules.router)
include_module_router(app, routes_training_brd.router)
include_module_router(app, routes_training_brd.public)


from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import get_settings
from app.experience import routes_admin, routes_auth, routes_catalog, routes_crm, routes_intent, routes_tenants

# Schema is Alembic-driven (migrations/) - run `alembic upgrade head` before
# starting the app, in both dev and production. Nothing here creates tables.

app = FastAPI(title="BRT 360 — Intent CRM", version="0.1.0-sprint1")

app.add_middleware(
    CORSMiddleware,
    allow_origins=get_settings().cors_allowed_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(routes_auth.router)
app.include_router(routes_intent.router)
app.include_router(routes_catalog.router)
app.include_router(routes_crm.router)
app.include_router(routes_admin.router)
app.include_router(routes_tenants.router)


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}

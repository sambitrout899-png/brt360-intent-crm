from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.db import Base, engine
from app.experience import routes_catalog, routes_intent

# Sprint 1 dev convenience: ensure tables exist on startup. Production swaps
# this for Alembic migrations (migrations/ is already scaffolded) once the
# schema needs controlled, reviewable changes across environments.
Base.metadata.create_all(bind=engine)

app = FastAPI(title="BRT 360 — Intent CRM", version="0.1.0-sprint1")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(routes_intent.router)
app.include_router(routes_catalog.router)


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}

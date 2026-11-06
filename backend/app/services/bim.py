"""The BIM service: BRT Integration Manager - bulk loads and the BRT_ID register (/api/bim),
the Integration Hub's administration (/api/integration) and the gateway other systems call
(/api/gateway) - in its own container so neither a large load nor a slow partner ever slows
the CRM down. Queued work runs in the worker container (app/scheduler.py)."""

from __future__ import annotations

from fastapi import APIRouter

PREFIXES = ("/api/bim/", "/api/integration/", "/api/gateway/")


def routers() -> list[APIRouter]:
    from app.experience import routes_bim, routes_gateway, routes_hub

    return [routes_bim.router, routes_hub.router, routes_gateway.router]

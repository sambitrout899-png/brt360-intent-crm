"""Building a BRT 360 service: the one FastAPI app (app/main.py) and the
services split out of it (app/services/) start the same way.

    app = service.new_app("BRT 360 - Training")
    service.include(app, routes_training.router, ...)
    service.finish(app)

new_app adds CORS and the "who is acting" stamp; finish adds /api/health
and the request metrics at /metrics. Every service shares the code, the
database (row-level security scopes each request to its tenant) and token
verification, so a split service needs no login of its own.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import get_settings
from app.licensing import include_module_router


def new_app(title: str, **kwargs: Any) -> FastAPI:
    app = FastAPI(title=title, version="0.1.0-sprint1", **kwargs)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=get_settings().cors_allowed_origins_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["X-Example-Count"],
    )

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
            user_id = int(payload["sub"]) if payload and not payload.get("purpose") else None
        except (KeyError, TypeError, ValueError):
            user_id = None
        marker = brt_id.current_user.set({"user_id": user_id} if user_id else None)
        try:
            return await call_next(request)
        finally:
            brt_id.current_user.reset(marker)

    return app


def include(app: FastAPI, *routers: APIRouter) -> None:
    """Each route behind its licence check (app/licensing.py)."""
    for router in routers:
        include_module_router(app, router)


def finish(app: FastAPI) -> None:
    from app import observability

    @app.get("/api/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    observability.install(app)  # /metrics, and request counts + latency per route


def split(name: str) -> bool:
    """True when service `name` runs in its own container (SPLIT_SERVICES),
    so the main API leaves its routes to it."""
    return name in get_settings().split_services_list

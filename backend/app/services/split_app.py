"""A split service's container app: BRT_SERVICE names which (app/services/<name>.py).

    BRT_SERVICE=bim gunicorn app.services.split_app:app -k uvicorn.workers.UvicornWorker

With that service in SPLIT_SERVICES the main API leaves its routes out, and
the web container sends its PREFIXES here (frontend/nginx.conf).
"""

from __future__ import annotations

import importlib
import os

from fastapi import FastAPI

from app import service

# name -> title; each has app/services/<name>.py with PREFIXES and routers()
SERVICES = {"training": "Training", "bim": "BIM", "product": "Products", "metadata": "Metadata"}


def create(name: str) -> FastAPI:
    if name not in SERVICES:
        raise SystemExit(f"BRT_SERVICE must be one of {', '.join(SERVICES)} (got {name!r})")
    app = service.new_app(f"BRT 360 — {SERVICES[name]}")
    service.include(app, *importlib.import_module(f"app.services.{name}").routers())
    service.finish(app)
    return app


def __getattr__(attr: str) -> FastAPI:
    # gunicorn asks for `app`: built for the service the container runs
    if attr == "app":
        globals()["app"] = built = create(os.environ.get("BRT_SERVICE", ""))
        return built
    raise AttributeError(attr)

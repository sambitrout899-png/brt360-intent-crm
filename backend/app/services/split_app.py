"""Every service's container app: BRT_SERVICE names which (app/services/<name>.py).

    BRT_SERVICE=bim gunicorn app.services.split_app:app -k uvicorn.workers.UvicornWorker

"core" (app/services/core.py) is what isn't split out, plus every service
SPLIT_SERVICES doesn't name - with none named it is the whole platform, which
is what app/main.py serves for development and the tests. Each other service
answers its PREFIXES; the web container sends them there (frontend/nginx.conf).
"""

from __future__ import annotations

import importlib
import os

from fastapi import FastAPI

from app import service

# name -> title; each has app/services/<name>.py with PREFIXES and routers()
SERVICES = {"training": "Training", "bim": "BIM", "product": "Products", "metadata": "Metadata", "intent": "Intents",
            "flow": "Flow Studio", "notification": "Notifications"}
CORE = "core"  # app/services/core.py: routers() only - it answers every /api path the others don't claim


def install_hooks() -> None:
    """Session hooks every process that writes records needs, as app/main.py
    installs them: a prospect becomes an account when an order of theirs is
    activated, whichever service (or worker job) activated it."""
    from app.crm_core import process_service

    process_service.install_account_conversion()


def create(name: str, **app_kwargs) -> FastAPI:
    if name != CORE and name not in SERVICES:
        raise SystemExit(f"BRT_SERVICE must be {CORE} or one of {', '.join(SERVICES)} (got {name!r})")
    install_hooks()
    app = service.new_app(f"BRT 360 — {SERVICES.get(name, 'Core')}", **app_kwargs)
    if name == CORE:
        # what isn't split out, and every service that doesn't run in its own container
        modules = [CORE, *(s for s in SERVICES if not service.split(s))]
    else:
        modules = [name]
    for module in modules:
        service.include(app, *importlib.import_module(f"app.services.{module}").routers())
    service.finish(app)
    return app


def __getattr__(attr: str) -> FastAPI:
    # gunicorn asks for `app`: built for the service the container runs
    if attr == "app":
        globals()["app"] = built = create(os.environ.get("BRT_SERVICE", ""))
        return built
    raise AttributeError(attr)

"""The training service: the training workspace (/api/training), the
original assessment links (/api/assessments) and the public participant
pages (/api/p).

With SPLIT_SERVICES=training the main API leaves these routes out, the
training container serves them (app/services/training_app.py) and the web
container sends their paths there (frontend/nginx.conf). Training's
background jobs (reminders, retention) stay in the worker container.
"""

from __future__ import annotations

from fastapi import APIRouter

# The URL prefixes this service answers - what the web container routes to it.
PREFIXES = ("/api/training", "/api/assessments", "/api/p/")


def routers() -> list[APIRouter]:
    from app.experience import routes_training, routes_training_brd

    return [routes_training.router, routes_training.public_router, routes_training_brd.router, routes_training_brd.public]

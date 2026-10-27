"""Prometheus metrics at /metrics: request counts and latency per route.

Routes are recorded by their template (/api/orders/{order_id}), never the
raw path, so the number of series stays small. Under gunicorn every worker
records into PROMETHEUS_MULTIPROC_DIR and /metrics adds them up
(gunicorn.conf.py prepares that folder). /metrics is for Prometheus on the
private network: neither nginx (host or web container) passes it on.
"""

from __future__ import annotations

import os
import time

from fastapi import FastAPI, Response
from prometheus_client import CONTENT_TYPE_LATEST, CollectorRegistry, Counter, Histogram, generate_latest, multiprocess

REQUESTS = Counter("brt360_http_requests_total", "HTTP requests handled", ["method", "route", "status"])
LATENCY = Histogram("brt360_http_request_duration_seconds", "Time to answer an HTTP request", ["method", "route"],
                    buckets=(0.025, 0.05, 0.1, 0.2, 0.3, 0.5, 1, 2, 3, 5, 10, 30))


def install(app: FastAPI) -> None:
    @app.middleware("http")
    async def _measure(request, call_next):
        start = time.perf_counter()
        status = 500  # an exception escaping the app is answered with a 500
        try:
            response = await call_next(request)
            status = response.status_code
            return response
        finally:
            route = getattr(request.scope.get("route"), "path", None) or "unmatched"
            if route != "/metrics":
                REQUESTS.labels(request.method, route, str(status)).inc()
                LATENCY.labels(request.method, route).observe(time.perf_counter() - start)

    @app.get("/metrics", include_in_schema=False)
    def metrics() -> Response:
        if os.environ.get("PROMETHEUS_MULTIPROC_DIR"):
            registry = CollectorRegistry()
            multiprocess.MultiProcessCollector(registry)
            return Response(generate_latest(registry), media_type=CONTENT_TYPE_LATEST)
        return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)

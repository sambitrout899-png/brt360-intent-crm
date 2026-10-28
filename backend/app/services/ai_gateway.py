"""The AI gateway: the one way out to a language model.

The app's model calls (app/crm_core/llm.py, chat_json) come here when
AI_GATEWAY_URL is set. The gateway

- holds the platform's model key - the app containers have none;
- uses a tenant's own key instead when the tenant set one (Admin -> AI
  Settings), read from the ai_settings table this module owns;
- sends "local" calls to the model on this server (LOCAL_LLM_BASE_URL);
- limits each tenant to AI_GATEWAY_TENANT_RPM calls a minute;
- retries the external API once or twice when it is busy, within the
  caller's time;
- counts calls, time and tokens per tenant (/metrics).

It speaks the OpenAI chat-completions API, so the caller is the ordinary
OpenAI client. Only the platform's own services may call it: the bearer
token must be AI_GATEWAY_TOKEN, and with no token configured it refuses
everything. Its answers are the model's own; its own refusals use the
OpenAI error shape, and the app turns any of them into "use the rules".

    gunicorn app.services.ai_gateway:app -k uvicorn.workers.UvicornWorker
"""

from __future__ import annotations

import hmac
import json
import time

import httpx
from fastapi import Request
from fastapi.responses import JSONResponse, Response
from prometheus_client import Counter, Histogram

from app import ratelimit, rls, service
from app.config import get_settings
from app.db import SessionLocal

CALLS = Counter("brt360_ai_calls_total", "Model calls through the AI gateway", ["tenant", "provider", "outcome"])
SECONDS = Histogram("brt360_ai_call_duration_seconds", "Time a model took to answer", ["provider"],
                    buckets=(0.5, 1, 2, 3, 5, 10, 20, 30, 60, 90))
TOKENS = Counter("brt360_ai_tokens_total", "Tokens used through the AI gateway", ["tenant", "provider", "kind"])

RETRY_STATUSES = {429, 500, 502, 503}
MAX_TIMEOUT = 120.0

app = service.new_app("BRT 360 — AI gateway")


def _error(status: int, message: str) -> JSONResponse:
    return JSONResponse({"error": {"message": message, "type": "gateway_error"}}, status_code=status)


def _authorized(request: Request) -> bool:
    expected = get_settings().ai_gateway_token
    given = request.headers.get("authorization", "")
    return bool(expected) and hmac.compare_digest(given, f"Bearer {expected}")


def upstream(provider: str, tenant_id: int | None) -> tuple[str, str] | None:
    """(base url, key) for this call, or None when there is no key for it."""
    s = get_settings()
    if provider == "local":
        return s.local_llm_base_url, "local"
    if tenant_id is not None:
        from app.models.ai_settings import AiSettings

        with SessionLocal() as db:
            rls.scope(db, tenant_id)
            row = db.query(AiSettings).filter(AiSettings.tenant_id == tenant_id).first()
            if row is not None and row.is_active and row.api_key:
                return s.openai_base_url, row.api_key
    return (s.openai_base_url, s.openai_api_key) if s.openai_api_key else None


def _count_tokens(tenant: str, provider: str, body: bytes) -> None:
    try:
        usage = json.loads(body).get("usage") or {}
    except (ValueError, AttributeError):
        return
    for kind in ("prompt_tokens", "completion_tokens"):
        if isinstance(usage.get(kind), int):
            TOKENS.labels(tenant, provider, kind.removesuffix("_tokens")).inc(usage[kind])


@app.post("/v1/chat/completions")
def chat_completions(request: Request, body: dict) -> Response:
    if not _authorized(request):
        return _error(401, "Not a platform service")
    provider = request.headers.get("x-brt-provider", "")
    if provider not in ("external", "local"):
        return _error(400, "X-BRT-Provider must be external or local")
    raw_tenant = request.headers.get("x-brt-tenant", "")
    tenant_id = int(raw_tenant) if raw_tenant.isdigit() else None
    tenant = str(tenant_id) if tenant_id is not None else "platform"
    try:
        timeout = min(max(float(request.headers.get("x-brt-timeout", "30")), 1.0), MAX_TIMEOUT)
    except ValueError:
        timeout = 30.0

    if tenant_id is not None and not ratelimit.allow(f"ai:{tenant_id}", get_settings().ai_gateway_tenant_rpm):
        CALLS.labels(tenant, provider, "limited").inc()
        return _error(429, "This workspace has used its AI calls for this minute")
    target = upstream(provider, tenant_id)
    if target is None:
        CALLS.labels(tenant, provider, "no_key").inc()
        return _error(503, "No model key is configured for external AI")
    base_url, key = target

    deadline = time.monotonic() + timeout
    attempts = 3 if provider == "external" else 1
    started = time.monotonic()
    for attempt in range(attempts):
        left = deadline - time.monotonic()
        try:
            resp = httpx.post(f"{base_url.rstrip('/')}/chat/completions", json=body, timeout=left,
                              headers={"Authorization": f"Bearer {key}"})
        except httpx.TimeoutException:
            CALLS.labels(tenant, provider, "timeout").inc()
            return _error(504, f"The model did not answer within {timeout:g}s")
        except httpx.HTTPError as exc:
            CALLS.labels(tenant, provider, "unreachable").inc()
            return _error(502, f"Could not reach the model ({type(exc).__name__})")
        wait = 0.5 * (attempt + 1)
        if resp.status_code in RETRY_STATUSES and attempt + 1 < attempts and deadline - time.monotonic() > wait + 1:
            time.sleep(wait)
            continue
        break
    SECONDS.labels(provider).observe(time.monotonic() - started)
    CALLS.labels(tenant, provider, "ok" if resp.status_code < 400 else f"http_{resp.status_code}").inc()
    if resp.status_code < 400:
        _count_tokens(tenant, provider, resp.content)
    return Response(resp.content, status_code=resp.status_code, media_type="application/json")


@app.get("/v1/status")
def status(request: Request) -> Response:
    """For the app's AI Settings page: is there a platform key behind the gateway?"""
    if not _authorized(request):
        return _error(401, "Not a platform service")
    return JSONResponse({"platform_key": bool(get_settings().openai_api_key)})


service.finish(app)

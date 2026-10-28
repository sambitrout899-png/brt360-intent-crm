"""The AI gateway: the one way out to a language model.

The app's model calls (app/crm_core/llm.py, chat_json) come here when
AI_GATEWAY_URL is set. The gateway

- holds the platform's model key - the app containers have none;
- uses a tenant's own key instead when the tenant set one (Admin -> AI
  Settings), read from the ai_settings table this module owns;
- sends "local" calls to the model on this server (LOCAL_LLM_BASE_URL);
- masks emails, phone numbers, card numbers and IBANs before a prompt goes
  to an external model, and puts them back in the answer (ai_redact.py);
- limits each tenant to AI_GATEWAY_TENANT_RPM calls a minute and, when set,
  AI_GATEWAY_TENANT_MONTHLY_TOKENS tokens a month;
- retries the external API once or twice when it is busy, within the
  caller's time;
- records every call in ai_calls (the prompt audit: who, which model, the
  outcome, tokens, time, the prompt's hash and size - never its text) and
  counts them per tenant at /metrics.

It speaks the OpenAI chat-completions API, so the caller is the ordinary
OpenAI client. Only the platform's own services may call it: the bearer
token must be AI_GATEWAY_TOKEN, and with no token configured it refuses
everything. Its answers are the model's own; its own refusals use the
OpenAI error shape, and the app turns any of them into "use the rules".

    gunicorn app.services.ai_gateway:app -k uvicorn.workers.UvicornWorker
"""

from __future__ import annotations

import datetime as dt
import hashlib
import hmac
import json
import logging
import time
from dataclasses import dataclass

import httpx
from fastapi import Request
from fastapi.responses import JSONResponse, Response
from prometheus_client import Counter, Histogram
from sqlalchemy import func

from app import ratelimit, rls, service
from app.config import get_settings
from app.db import SessionLocal
from app.models.ai_settings import AiCall, AiSettings
from app.services.ai_redact import Redactor
from app.timeutils import utcnow

log = logging.getLogger(__name__)

CALLS = Counter("brt360_ai_calls_total", "Model calls through the AI gateway", ["tenant", "provider", "outcome"])
SECONDS = Histogram("brt360_ai_call_duration_seconds", "Time a model took to answer", ["provider"],
                    buckets=(0.5, 1, 2, 3, 5, 10, 20, 30, 60, 90))
TOKENS = Counter("brt360_ai_tokens_total", "Tokens used through the AI gateway", ["tenant", "provider", "kind"])
REDACTED = Counter("brt360_ai_redactions_total", "Personal details masked before leaving the server", ["tenant"])

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
        with SessionLocal() as db:
            rls.scope(db, tenant_id)
            row = db.query(AiSettings).filter(AiSettings.tenant_id == tenant_id).first()
            if row is not None and row.is_active and row.api_key:
                return s.openai_base_url, row.api_key
    return (s.openai_base_url, s.openai_api_key) if s.openai_api_key else None


def tokens_this_month(tenant_id: int) -> int:
    start = utcnow().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    with SessionLocal() as db:
        rls.scope(db, tenant_id)
        used = db.query(func.coalesce(func.sum(AiCall.prompt_tokens + AiCall.completion_tokens), 0)).filter(
            AiCall.tenant_id == tenant_id, AiCall.at >= start).scalar()
    return int(used or 0)


@dataclass
class _Call:
    """What ai_calls and /metrics record about one call."""

    tenant_id: int | None
    provider: str
    model: str
    prompt_sha256: str
    prompt_chars: int
    started: float
    redactions: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0

    @property
    def tenant(self) -> str:
        return str(self.tenant_id) if self.tenant_id is not None else "platform"

    def done(self, outcome: str) -> None:
        CALLS.labels(self.tenant, self.provider, outcome).inc()
        try:
            with SessionLocal() as db:
                rls.scope(db, self.tenant_id)
                db.add(AiCall(tenant_id=self.tenant_id, at=utcnow(), provider=self.provider, model=self.model[:100],
                              outcome=outcome[:20], prompt_tokens=self.prompt_tokens, completion_tokens=self.completion_tokens,
                              duration_ms=round((time.monotonic() - self.started) * 1000), prompt_sha256=self.prompt_sha256,
                              prompt_chars=self.prompt_chars, redactions=self.redactions))
                db.commit()
        except Exception:  # noqa: BLE001 - the call itself went through; a lost audit row is logged
            log.exception("AI gateway: could not record the call in ai_calls")


def _prompt(body: dict) -> str:
    return "\n".join(m["content"] for m in body.get("messages") or [] if isinstance(m, dict) and isinstance(m.get("content"), str))


@app.post("/v1/chat/completions")
def chat_completions(request: Request, body: dict) -> Response:
    if not _authorized(request):
        return _error(401, "Not a platform service")
    s = get_settings()
    provider = request.headers.get("x-brt-provider", "")
    if provider not in ("external", "local"):
        return _error(400, "X-BRT-Provider must be external or local")
    raw_tenant = request.headers.get("x-brt-tenant", "")
    tenant_id = int(raw_tenant) if raw_tenant.isdigit() else None
    try:
        timeout = min(max(float(request.headers.get("x-brt-timeout", "30")), 1.0), MAX_TIMEOUT)
    except ValueError:
        timeout = 30.0
    prompt = _prompt(body)
    call = _Call(tenant_id, provider, str(body.get("model") or ""), hashlib.sha256(prompt.encode()).hexdigest(),
                 len(prompt), time.monotonic())

    if tenant_id is not None and not ratelimit.allow(f"ai:{tenant_id}", s.ai_gateway_tenant_rpm):
        call.done("limited")
        return _error(429, "This workspace has used its AI calls for this minute")
    if tenant_id is not None and s.ai_gateway_tenant_monthly_tokens > 0 \
            and tokens_this_month(tenant_id) >= s.ai_gateway_tenant_monthly_tokens:
        call.done("over_budget")
        return _error(429, "This workspace has used its AI budget for this month")
    target = upstream(provider, tenant_id)
    if target is None:
        call.done("no_key")
        return _error(503, "No model key is configured for external AI")
    base_url, key = target

    redactor = Redactor()
    if provider == "external" and s.ai_gateway_redact:
        body = redactor.mask(body)
        call.redactions = redactor.count
        if redactor.count:
            REDACTED.labels(call.tenant).inc(redactor.count)

    deadline = time.monotonic() + timeout
    attempts = 3 if provider == "external" else 1
    for attempt in range(attempts):
        left = deadline - time.monotonic()
        try:
            resp = httpx.post(f"{base_url.rstrip('/')}/chat/completions", json=body, timeout=left,
                              headers={"Authorization": f"Bearer {key}"})
        except httpx.TimeoutException:
            call.done("timeout")
            return _error(504, f"The model did not answer within {timeout:g}s")
        except httpx.HTTPError as exc:
            call.done("unreachable")
            return _error(502, f"Could not reach the model ({type(exc).__name__})")
        wait = 0.5 * (attempt + 1)
        if resp.status_code in RETRY_STATUSES and attempt + 1 < attempts and deadline - time.monotonic() > wait + 1:
            time.sleep(wait)
            continue
        break
    SECONDS.labels(provider).observe(time.monotonic() - call.started)
    if resp.status_code >= 400:
        call.done(f"http_{resp.status_code}")
        return Response(resp.content, status_code=resp.status_code, media_type="application/json")
    try:
        answer = resp.json()
    except ValueError:
        call.done("bad_answer")
        return _error(502, "The model's answer was not JSON")
    usage = answer.get("usage") or {}
    call.prompt_tokens = usage.get("prompt_tokens") if isinstance(usage.get("prompt_tokens"), int) else 0
    call.completion_tokens = usage.get("completion_tokens") if isinstance(usage.get("completion_tokens"), int) else 0
    TOKENS.labels(call.tenant, provider, "prompt").inc(call.prompt_tokens)
    TOKENS.labels(call.tenant, provider, "completion").inc(call.completion_tokens)
    call.done("ok")
    return JSONResponse(redactor.unmask(answer), status_code=resp.status_code)


@app.get("/v1/status")
def status(request: Request) -> Response:
    """For the app's AI Settings page: is there a platform key behind the gateway?"""
    if not _authorized(request):
        return _error(401, "Not a platform service")
    return JSONResponse({"platform_key": bool(get_settings().openai_api_key)})


@app.get("/v1/usage")
def usage(request: Request, tenant: int, month: str | None = None) -> Response:
    """A tenant's calls and tokens for a month (YYYY-MM, default this one) - from ai_calls."""
    if not _authorized(request):
        return _error(401, "Not a platform service")
    try:
        start = dt.datetime.strptime(month, "%Y-%m") if month else utcnow().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    except ValueError:
        return _error(400, "month must be YYYY-MM")
    end = (start + dt.timedelta(days=32)).replace(day=1)
    with SessionLocal() as db:
        rls.scope(db, tenant)
        rows = db.query(AiCall.outcome, func.count(), func.coalesce(func.sum(AiCall.prompt_tokens + AiCall.completion_tokens), 0),
                        func.coalesce(func.sum(AiCall.redactions), 0)).filter(
            AiCall.tenant_id == tenant, AiCall.at >= start, AiCall.at < end).group_by(AiCall.outcome).all()
    budget = get_settings().ai_gateway_tenant_monthly_tokens
    tokens = sum(int(r[2]) for r in rows)
    return JSONResponse({"tenant": tenant, "month": start.strftime("%Y-%m"), "tokens": tokens, "budget": budget or None,
                         "calls": {r[0]: int(r[1]) for r in rows}, "redactions": sum(int(r[3]) for r in rows)})


service.finish(app)

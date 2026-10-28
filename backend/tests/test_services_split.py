"""Phase 2: services in their own containers - the training service
(app/services/training.py) and the AI gateway (app/services/ai_gateway.py)."""

import json
import pathlib

import httpx
import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.crm_core import llm
from app.main import app as main_app
from app.models.ai_settings import AiSettings
from app.services import ai_gateway, training, training_app


def _paths(app) -> set[str]:
    out = set()

    def walk(routes):
        for r in routes:
            if hasattr(r, "original_router"):
                walk(r.original_router.routes)
            elif hasattr(r, "path"):
                out.add(r.path)

    walk(app.routes)
    return out


# ---------------------------------------------------------------- training


def test_training_service_owns_exactly_its_prefixes():
    """The web container routes these prefixes to the training container, so
    every route under them must be training's, and training's must all be
    under them - or a request lands where its route isn't."""
    under = {p for p in _paths(main_app) if p.startswith(training.PREFIXES)}
    own = {p for p in _paths(training_app.app) if p not in ("/api/health", "/metrics")
           and not p.startswith(("/docs", "/openapi", "/redoc"))}
    assert under == own and len(own) > 50


def test_training_service_answers_on_its_own(client):
    with TestClient(training_app.app) as svc:
        assert svc.get("/api/health").json() == {"status": "ok"}
        assert svc.get("/api/training/catalog").status_code == 401  # its own token check, same tokens
        assert svc.get("/api/crm/leads").status_code == 404  # nothing but training here


def test_web_container_sends_training_prefixes_to_training():
    conf = (pathlib.Path(__file__).resolve().parents[2] / "frontend" / "nginx.conf").read_text(encoding="utf-8")
    for prefix in training.PREFIXES:
        location = prefix if prefix.endswith("/") else prefix + "/"
        assert f"location {location} {{\n        proxy_pass $training;" in conf, prefix


# ---------------------------------------------------------------- AI gateway


@pytest.fixture()
def gateway(client, monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "ai_gateway_token", "svc-token")
    monkeypatch.setattr(settings, "openai_api_key", "platform-key")
    monkeypatch.setattr(settings, "ai_gateway_tenant_rpm", 0)
    monkeypatch.setattr(ai_gateway, "SessionLocal", client.TestSessionLocal)
    calls = []

    def fake_post(url, json=None, timeout=None, headers=None):
        calls.append({"url": url, "body": json, "key": headers["Authorization"], "timeout": timeout})
        answer = {"choices": [{"message": {"content": '{"ok": true}'}, "finish_reason": "stop"}],
                  "usage": {"prompt_tokens": 7, "completion_tokens": 3}}
        return httpx.Response(calls[-1].get("status", 200), json=answer)

    monkeypatch.setattr(ai_gateway.httpx, "post", fake_post)
    with TestClient(ai_gateway.app) as svc:
        svc.calls = calls
        yield svc


def _ask(svc, provider="external", tenant="", token="svc-token", timeout="30"):
    return svc.post("/v1/chat/completions", json={"model": "m", "messages": [{"role": "user", "content": "hi"}]},
                    headers={"Authorization": f"Bearer {token}", "X-BRT-Provider": provider, "X-BRT-Tenant": tenant,
                             "X-BRT-Timeout": timeout})


def test_gateway_only_answers_platform_services(gateway, monkeypatch):
    assert _ask(gateway, token="wrong").status_code == 401
    monkeypatch.setattr(get_settings(), "ai_gateway_token", "")
    assert _ask(gateway, token="").status_code == 401  # no token configured: refuses everything
    assert gateway.calls == []


def test_gateway_uses_platform_key_or_tenant_key_and_counts(gateway, client):
    resp = _ask(gateway)
    assert resp.status_code == 200 and resp.json()["choices"][0]["message"]["content"] == '{"ok": true}'
    assert gateway.calls[-1]["key"] == "Bearer platform-key"
    assert gateway.calls[-1]["url"] == "https://api.openai.com/v1/chat/completions"
    assert gateway.calls[-1]["body"]["model"] == "m"

    from app.models.tenant import Tenant

    db = client.TestSessionLocal()
    tenant_id = db.query(Tenant.id).first()[0]
    db.add(AiSettings(tenant_id=tenant_id, api_key="tenant-own-key", model="gpt-x", is_active=True))
    db.commit()
    db.close()
    assert _ask(gateway, tenant=str(tenant_id)).status_code == 200
    assert gateway.calls[-1]["key"] == "Bearer tenant-own-key"

    metrics = gateway.get("/metrics").text
    assert f'brt360_ai_calls_total{{outcome="ok",provider="external",tenant="{tenant_id}"}}' in metrics
    assert f'brt360_ai_tokens_total{{kind="prompt",provider="external",tenant="{tenant_id}"}}' in metrics


def test_gateway_sends_local_calls_to_this_servers_model(gateway):
    assert _ask(gateway, provider="local").status_code == 200
    assert gateway.calls[-1]["url"].startswith(get_settings().local_llm_base_url.rstrip("/"))
    assert gateway.calls[-1]["key"] == "Bearer local"
    assert _ask(gateway, provider="openai").status_code == 400


def test_gateway_without_a_key_says_so(gateway, monkeypatch):
    monkeypatch.setattr(get_settings(), "openai_api_key", None)
    resp = _ask(gateway)
    assert resp.status_code == 503 and "No model key" in resp.json()["error"]["message"]
    assert gateway.get("/v1/status", headers={"Authorization": "Bearer svc-token"}).json() == {"platform_key": False}


def test_gateway_limits_each_tenant(gateway, monkeypatch):
    from app import ratelimit

    monkeypatch.setattr(get_settings(), "ai_gateway_tenant_rpm", 2)
    ratelimit._local.clear()
    assert [_ask(gateway, tenant="42").status_code for _ in range(3)] == [200, 200, 429]
    assert _ask(gateway, tenant="43").status_code == 200  # another tenant has its own allowance
    assert _ask(gateway).status_code == 200  # platform calls (no tenant) are not limited


def test_gateway_retries_a_busy_api_then_passes_its_answer_on(gateway, monkeypatch):
    statuses = iter([429, 200])

    def busy_then_ok(url, json=None, timeout=None, headers=None):
        gateway.calls.append({"url": url})
        return httpx.Response(next(statuses), json={"choices": []})

    monkeypatch.setattr(ai_gateway.httpx, "post", busy_then_ok)
    monkeypatch.setattr(ai_gateway.time, "sleep", lambda s: None)
    assert _ask(gateway).status_code == 200 and len(gateway.calls) == 2


def test_app_calls_the_model_through_the_gateway(monkeypatch):
    """With AI_GATEWAY_URL set the app needs no key: it sends the gateway its
    token, the tenant and the provider, and the gateway's refusal is just
    another reason to use the rules."""
    settings = get_settings()
    monkeypatch.setattr(settings, "ai_gateway_url", "http://ai-gateway:8000")
    monkeypatch.setattr(settings, "ai_gateway_token", "svc-token")
    monkeypatch.setattr(settings, "openai_api_key", None)
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(url=str(request.url), headers=dict(request.headers), body=json.loads(request.content))
        if request.url.path.endswith("/v1/status"):
            return httpx.Response(200, json={"platform_key": True})
        return httpx.Response(200, json={"id": "x", "object": "chat.completion", "created": 0, "model": "m",
                                         "choices": [{"index": 0, "finish_reason": "stop",
                                                      "message": {"role": "assistant", "content": '{"intent": "INT-01"}'}}]})

    from openai import OpenAI

    real_client = llm._client

    def via_mock(target):
        client = real_client(target)
        return OpenAI(api_key=client.api_key, base_url=client.base_url, timeout=client.timeout, max_retries=client.max_retries,
                      default_headers=client._custom_headers, http_client=httpx.Client(transport=httpx.MockTransport(handler)))

    monkeypatch.setattr(llm, "_client", via_mock)
    target = llm.LlmTarget("external", "gpt-4o-mini", "", None, 30.0, tenant_id=7)
    assert llm.chat_json(target, "system", "user") == {"intent": "INT-01"}
    assert seen["url"] == "http://ai-gateway:8000/v1/chat/completions"
    assert seen["headers"]["authorization"] == "Bearer svc-token"
    assert seen["headers"]["x-brt-tenant"] == "7" and seen["headers"]["x-brt-provider"] == "external"
    assert seen["body"]["model"] == "gpt-4o-mini"


def test_app_sees_the_gateways_platform_key(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "openai_api_key", None)
    monkeypatch.setattr(settings, "ai_gateway_url", "")
    monkeypatch.setattr(settings, "default_llm_provider", "external")
    assert llm.platform_key_available() is False and llm.resolve(None, None) is None

    monkeypatch.setattr(settings, "ai_gateway_url", "http://ai-gateway:8000")
    monkeypatch.setattr(llm, "_gateway_status", {})
    monkeypatch.setattr(httpx, "get", lambda url, timeout=None, headers=None: httpx.Response(200, json={"platform_key": True}))
    assert llm.platform_key_available() is True
    target = llm.resolve(None, None)
    assert target is not None and target.provider == "external"

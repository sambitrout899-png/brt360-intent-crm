"""Which language model a tenant's AI features use - and the one way to call it.

Each tenant admin chooses (Admin -> AI Settings):

- "external": a hosted API (OpenAI) with the tenant's key, else the
  platform's OPENAI_API_KEY.
- "local": the platform's own model running on this server (Ollama or any
  OpenAI-compatible server - LOCAL_LLM_BASE_URL, default
  http://127.0.0.1:11434/v1). No data leaves the server, no per-call fees.
  Where the local server lives is a platform setting, not a tenant one, so
  a tenant can't point the backend at arbitrary hosts.
- "rules": no model at all - every feature uses its built-in rule engine.

Every AI feature (intent understanding, Configure 360 agent, field
mapping, Flow Studio drafting, flow AI decisions) calls chat_json(); any
failure (unreachable, slow, bad JSON) raises LlmUnavailable and the caller
falls back to its rules, so the app never depends on a model being up.
"""

from __future__ import annotations

import json
import re
import time
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, replace
from typing import Any

from sqlalchemy.orm import Session

from app.config import get_settings

PROVIDERS = ("external", "local", "rules")


class LlmUnavailable(RuntimeError):
    pass


@dataclass(frozen=True)
class LlmTarget:
    provider: str  # external | local
    model: str
    api_key: str
    base_url: str | None
    timeout: float

    @property
    def label(self) -> str:
        return f"{'own model' if self.provider == 'local' else 'external API'} ({self.model})"


def local_target(model: str | None = None) -> LlmTarget:
    s = get_settings()
    return LlmTarget("local", model or s.local_llm_model, "local", s.local_llm_base_url, s.local_llm_timeout_seconds)


def resolve(db: Session | None, tenant_id: int | None, live: bool = False) -> LlmTarget | None:
    """The model to use, or None = rules only. live=True is the
    as-you-type path: a CPU-hosted local model is too slow for it unless
    the tenant opted in."""
    s = get_settings()
    row = None
    if db is not None and tenant_id is not None:
        from app.models.ai_settings import AiSettings

        row = db.query(AiSettings).filter(AiSettings.tenant_id == tenant_id).first()
    provider = (row.provider if row is not None and row.provider in PROVIDERS else None) or s.default_llm_provider
    if provider == "rules":
        return None
    if provider == "local":
        if live and not (row is not None and row.local_live_assist):
            return None
        return local_target(row.local_model if row is not None and row.local_model else None)
    if row is not None and row.is_active and row.api_key:
        return LlmTarget("external", row.model or s.openai_model, row.api_key, None, 30.0)
    if s.openai_api_key:
        return LlmTarget("external", s.openai_model, s.openai_api_key, None, 30.0)
    return None


def _client(target: LlmTarget):
    from openai import OpenAI

    return OpenAI(api_key=target.api_key, base_url=target.base_url, timeout=target.timeout, max_retries=0 if target.provider == "local" else 2)


def parse_json(raw: str) -> dict[str, Any]:
    """Small local models sometimes wrap JSON in ``` fences or add a
    sentence around it - take the first complete JSON object."""
    raw = (raw or "").strip()
    raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw)
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, dict):
            return parsed
    except json.JSONDecodeError:
        pass
    start = raw.find("{")
    while start != -1:
        depth = 0
        for i in range(start, len(raw)):
            if raw[i] == "{":
                depth += 1
            elif raw[i] == "}":
                depth -= 1
                if depth == 0:
                    try:
                        parsed = json.loads(raw[start : i + 1])
                        if isinstance(parsed, dict):
                            return parsed
                    except json.JSONDecodeError:
                        break
        start = raw.find("{", start + 1)
    raise LlmUnavailable("The model did not answer with JSON")


# ---------------------------------------------------------------- per-request time budget
#
# A request that may call the model many times (the agent's planner classifies
# every step; the Process Builder drafts flows and pages) must still answer
# before the web server gives up on it (nginx proxy_read_timeout). Inside
# `with llm.budget(seconds):` every call gets at most the time left, and once
# it is used up the calls fail fast - each caller then uses its rules, as it
# does whenever the model is unavailable.

_deadline: ContextVar[float | None] = ContextVar("llm_deadline", default=None)
MIN_CALL_SECONDS = 3.0


@contextmanager
def budget(seconds: float) -> Iterator[None]:
    token = _deadline.set(time.monotonic() + seconds)
    try:
        yield
    finally:
        _deadline.reset(token)


def _within_budget(target: LlmTarget) -> LlmTarget:
    deadline = _deadline.get()
    if deadline is None:
        return target
    left = deadline - time.monotonic()
    if left < MIN_CALL_SECONDS:
        raise LlmUnavailable(f"{target.label} skipped - this request's AI time is used up")
    return replace(target, timeout=min(target.timeout, left)) if left < target.timeout else target


def chat_json(target: LlmTarget, system: str, user: str, temperature: float = 0.0,
              max_tokens: int | None = None) -> dict[str, Any]:
    target = _within_budget(target)
    try:
        response = _client(target).chat.completions.create(
            model=target.model,
            response_format={"type": "json_object"},
            temperature=temperature,
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
            **({"max_tokens": max_tokens} if max_tokens is not None else {}),
        )
    except Exception as exc:  # noqa: BLE001 - every transport/model error means "use the rules"
        kind = type(exc).__name__
        status = getattr(exc, "status_code", None)
        reason = (f"timed out after {target.timeout:g}s" if "Timeout" in kind else
                  "could not connect to the configured model server" if "Connection" in kind else
                  "model or API endpoint was not found; check the installed model name and base URL" if status == 404 else
                  "model server rejected authentication" if status in (401, 403) else
                  f"model server returned HTTP {status}" if isinstance(status, int) else kind)
        raise LlmUnavailable(f"{target.label} unavailable: {reason}") from exc
    if not response.choices:
        raise LlmUnavailable(f"{target.label} returned no answer")
    choice = response.choices[0]
    if getattr(choice, "finish_reason", None) == "length":
        raise LlmUnavailable(f"{target.label} reached its output token limit before completing the JSON; increase the output limit")
    try:
        return parse_json(choice.message.content or "")
    except LlmUnavailable as exc:
        raise LlmUnavailable(f"{target.label} returned invalid or incomplete JSON") from exc


# ---------------------------------------------------------------- local server status


def _root(base_url: str) -> str:
    return re.sub(r"/v1/?$", "", base_url.rstrip("/"))


def local_status() -> dict[str, Any]:
    """Is the on-server model running, and which models does it have?"""
    import httpx

    s = get_settings()
    out: dict[str, Any] = {"base_url": s.local_llm_base_url, "default_model": s.local_llm_model, "reachable": False, "models": [], "error": None}
    started = time.perf_counter()
    try:
        resp = httpx.get(f"{_root(s.local_llm_base_url)}/api/tags", timeout=3.0)  # Ollama
        if resp.status_code == 200:
            out["models"] = [
                {"name": m.get("name"), "size_gb": round((m.get("size") or 0) / 1e9, 1),
                 "parameters": (m.get("details") or {}).get("parameter_size"), "quantization": (m.get("details") or {}).get("quantization_level")}
                for m in resp.json().get("models", [])
            ]
            out["reachable"] = True
        else:
            resp = httpx.get(f"{s.local_llm_base_url.rstrip('/')}/models", timeout=3.0)  # any OpenAI-compatible server
            resp.raise_for_status()
            out["models"] = [{"name": m.get("id")} for m in resp.json().get("data", [])]
            out["reachable"] = True
    except Exception as exc:  # noqa: BLE001
        out["error"] = f"Not running at {s.local_llm_base_url} ({type(exc).__name__}) - install it with: bash deploy/local_llm.sh install"
    out["latency_ms"] = round((time.perf_counter() - started) * 1000)
    return out

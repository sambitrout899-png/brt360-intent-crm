"""The intent service: the command bar's pipeline (/api/intents), the agent
(/api/agent), the assistant (/api/assistant), the intent catalog's
settings (/api/admin/intents) and flow conflict rules (/api/admin/conflicts).

Running an intent still reaches every module in-process - the same code and
database in every container - through the capability registry
(app/capabilities.py)."""

from __future__ import annotations

from fastapi import APIRouter

PREFIXES = ("/api/intents", "/api/agent/", "/api/assistant/", "/api/admin/intents", "/api/admin/conflicts")


def routers() -> list[APIRouter]:
    from app.experience import routes_agent, routes_assistant, routes_conflicts, routes_intent, routes_intent_admin

    return [routes_intent.router, routes_intent_admin.router, routes_agent.router, routes_assistant.router, routes_conflicts.router]

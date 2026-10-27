"""The app must start. The suite's TestClient never runs the lifespan, so a
startup failure (the RecursionError of nesting one lifespan per included
route) would otherwise only show up on the server."""
import asyncio

import app.main as main


def test_app_lifespan_is_not_nested_per_route():
    assert main.app.router.lifespan_context is main.lifespan


def test_app_starts_and_answers_health(monkeypatch):
    from fastapi.testclient import TestClient

    async def idle():  # keep the background scheduler away from the real database
        await asyncio.sleep(3600)

    monkeypatch.setattr(main, "inventory_worker", idle)
    with TestClient(main.app) as client:
        assert client.get("/api/health").json() == {"status": "ok"}

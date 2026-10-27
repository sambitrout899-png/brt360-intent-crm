"""The own model's training data teaches it to think first (style "reasoned"):
rationale before the answer, look-alikes ruled out, vague requests with
alternatives, out-of-scope ones as UNKNOWN, multi-step plans - and every
built-in action is covered. The original examples stay available (style "plain")."""

import json

from app.intent_engine.intent_registry import BUILTIN


def _rows(client, **params):
    resp = client.get("/api/admin/ai-settings/training-data", params={"per_template": 2, **params})
    assert resp.status_code == 200, resp.text
    return [json.loads(line) for line in resp.text.splitlines()]


def test_reasoned_training_data(client):
    rows = _rows(client)
    answers = [r["messages"][2]["content"] for r in rows]
    parsed = [json.loads(a) for a in answers]
    intents = [p for p in parsed if "intent_type" in p]
    assert all(a.startswith('{"rationale"') for a, p in zip(answers, parsed) if "intent_type" in p)  # thinks first
    assert set(BUILTIN) <= {p["intent_type"] for p in intents}  # every action has examples
    assert any(p["intent_type"] == "UNKNOWN" for p in intents)
    assert any(p["alternatives"] and p["confidence"] < 0.75 for p in intents)  # vague -> best guess + alternatives
    assert any("Not Send a proposal (INT-22)" in p["rationale"] for p in intents if p["intent_type"] == "INT-109")
    plans = [p for p in parsed if "steps" in p]
    assert plans and all(len(p["steps"]) >= 2 for p in plans)
    assert any([s["intent_type"] for s in p["steps"]] == ["INT-06", "INT-109", "INT-22"] for p in plans)
    planner = next(r for r in rows if "steps" in json.loads(r["messages"][2]["content"]))
    assert planner["messages"][0]["content"].startswith("You are the planning agent")  # the agent's own prompt


def test_plain_training_data_is_unchanged(client):
    parsed = [json.loads(r["messages"][2]["content"]) for r in _rows(client, style="plain")]
    assert all("steps" not in p and p.get("intent_type") != "UNKNOWN" for p in parsed)
    assert client.get("/api/admin/ai-settings/training-data", params={"style": "fancy"}).status_code == 400

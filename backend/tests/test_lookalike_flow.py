"""A tenant flow whose description shares a word with a plain request ("create a new B2B prospect" vs
"NGO Prospect to Account Conversion", built from "In order to make this happen, create a new flow ...
whenever the prospect of type NGO is creating an opportunity") is never what the request means - even
when the model picks it. And "in order to" never makes an action ask for an order."""

from app.crm_core import llm
from app.intent_engine import classifier
from app.orchestration import scratch

REQUEST = ("In order to make this happen, create a new flow to run behind whenever the prospect of type NGO is creating an opportunity "
           "for the very first time")


def _lookalike(client) -> str:
    db = client.TestSessionLocal()
    try:
        spec = scratch.draft(db, 1, REQUEST)
        assert spec["entity_type"] != "order", spec  # "in order to" is no order
        assert not spec["label"].lower().startswith("in order"), spec["label"]
        spec["label"] = "NGO Prospect to Account Conversion"
        return scratch.build(db, 1, None, spec)["code"]
    finally:
        db.close()


def test_a_model_drifting_to_a_lookalike_flow_is_overruled(client, monkeypatch):
    code = _lookalike(client)
    monkeypatch.setattr(llm, "resolve", lambda *a, **k: llm.local_target("test"))
    monkeypatch.setattr(llm, "chat_json", lambda target, system, user, **k: (
        {"steps": [{"text": "create a new B2B prospect", "intent_type": code, "entities": {}, "why": "custom flow"}]}
        if "planning agent" in system else {"intent_type": code, "entities": {}, "confidence": 0.9, "rationale": "custom flow"}))

    out = client.post("/api/assistant/understand", json={"text": "create a new B2B prospect"}).json()
    assert out["route"] == "action", out
    assert out["action"]["intent_type"] == "INT-15", out

    live = client.post("/api/intents/assist", json={"text": "create a new B2B prospect"}).json()
    assert live["intent_type"] == "INT-15", live

    # the flow is still found when it is named
    db = client.TestSessionLocal()
    try:
        named = classifier.classify("run the NGO prospect to account conversion", db, 1, None)
    finally:
        db.close()
    assert named.intent_type == code, named

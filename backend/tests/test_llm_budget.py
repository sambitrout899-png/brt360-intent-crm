"""A request that may call the model many times answers in time: inside
llm.budget every call gets at most the time left, and once it is used up the
calls are skipped and the rules answer - a slow on-server model can no longer
push the agent or the Process Builder past nginx's timeout (504)."""

import time

import pytest

from app.crm_core import llm


class _Slow:
    """Stands in for the OpenAI client - records the timeout it was given."""

    def __init__(self, calls):
        self.calls = calls
        self.chat = self
        self.completions = self

    def create(self, **kwargs):
        self.calls.append(kwargs)
        raise TimeoutError("model too slow")


def test_a_used_up_budget_skips_the_model_entirely(monkeypatch):
    calls: list = []
    monkeypatch.setattr(llm, "_client", lambda target: _Slow(calls))
    with llm.budget(0):
        with pytest.raises(llm.LlmUnavailable, match="AI time is used up"):
            llm.chat_json(llm.local_target("test"), "system", "user")
    assert calls == []


def test_a_call_gets_only_the_time_left(monkeypatch):
    seen: list = []
    monkeypatch.setattr(llm, "_client", lambda target: (seen.append(target.timeout), _Slow([]))[1])
    started = time.monotonic()
    with llm.budget(10):
        with pytest.raises(llm.LlmUnavailable):
            llm.chat_json(llm.local_target("test"), "system", "user")  # a 90 s local timeout, capped
    assert seen and seen[0] <= 10 and time.monotonic() - started < 5
    # outside a budget nothing changes
    with pytest.raises(llm.LlmUnavailable):
        llm.chat_json(llm.local_target("test"), "system", "user")
    assert seen[-1] == llm.local_target("test").timeout


def test_the_agent_still_plans_when_the_ai_time_is_gone(client, monkeypatch):
    from app.experience import routes_agent
    from test_agent import SALES_CHAIN

    calls: list = []
    monkeypatch.setattr(llm, "resolve", lambda *a, **k: llm.local_target("test"))
    monkeypatch.setattr(llm, "_client", lambda target: _Slow(calls))
    monkeypatch.setattr(routes_agent, "AI_SECONDS", 0.0)
    plan = client.post("/api/agent/plan", json={"goal": SALES_CHAIN}).json()
    assert calls == []  # no model call was even attempted
    assert [s["intent_type"] for s in plan["steps"]] == ["INT-15", "INT-46", "INT-17", "INT-37", "INT-05"]

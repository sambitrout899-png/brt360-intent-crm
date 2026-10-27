"""Own-model support: tenant chooses external / local / rules, every AI call
goes through app.crm_core.llm, a failing model falls back to the rules,
and the training-data export for fine-tuning."""

import json
from types import SimpleNamespace

import pytest

from app.crm_core import llm


class FakeClient:
    """Stands in for the OpenAI client - records calls, returns canned text."""

    def __init__(self, answer, calls):
        self._answer, self._calls = answer, calls
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self._calls.append(kwargs)
        content = self._answer(kwargs) if callable(self._answer) else self._answer
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))])


@pytest.fixture()
def fake_model(monkeypatch):
    calls, targets = [], []

    def install(answer):
        def make(target):
            targets.append(target)
            return FakeClient(answer, calls)

        monkeypatch.setattr(llm, "_client", make)
        return calls, targets

    return install


def test_default_is_rules_when_no_key_anywhere(client):
    body = client.get("/api/admin/ai-settings").json()
    assert body["provider"] == "external" and body["effective"] == "rules" and body["platform_key_available"] is False


def test_tenant_switches_to_own_model_and_intents_use_it(client, fake_model):
    calls, targets = fake_model('```json\n{"intent_type": "INT-15", "entities": {"name": "Globex Local"}, "confidence": 0.93, '
                                '"rationale": "adds a prospect"}\n```')
    saved = client.put("/api/admin/ai-settings", json={"model": "gpt-4o-mini", "provider": "local", "local_model": "brt360-telecom"}).json()
    assert saved["effective"] == "local" and "own model" in saved["effective_label"] and saved["local_live_assist"] is False

    ran = client.post("/api/intents", json={"text": "please add globex as a new prospect"}).json()
    assert ran["status"] == "executed" and ran["intent_type"] == "INT-15", ran["rationale"]
    assert "own model (brt360-telecom)" in ran["rationale"]
    assert targets[-1].base_url == "http://127.0.0.1:11434/v1" and targets[-1].provider == "local"
    assert calls[-1]["model"] == "brt360-telecom" and calls[-1]["response_format"] == {"type": "json_object"}

    # as-you-type suggestions stay on the fast rules unless the tenant opts in
    n = len(calls)
    client.post("/api/intents/assist", json={"text": "Add a prospect. name: Initech"})
    assert len(calls) == n
    client.put("/api/admin/ai-settings", json={"model": "gpt-4o-mini", "local_live_assist": True})
    client.post("/api/intents/assist", json={"text": "Add a prospect. name: Initech"})
    assert len(calls) == n + 1


def test_unreachable_model_falls_back_to_rules(client, fake_model):
    def down(_):
        raise ConnectionError("connection refused")

    fake_model(down)
    client.put("/api/admin/ai-settings", json={"model": "gpt-4o-mini", "provider": "local"})
    ran = client.post("/api/intents", json={"text": "Add a prospect. name: Fallback Co"}).json()
    assert ran["status"] == "executed" and ran["intent_type"] == "INT-15"
    assert "built-in rules" in ran["rationale"]


def test_rules_provider_never_calls_a_model(client, fake_model, monkeypatch):
    calls, _ = fake_model('{"intent_type": "INT-15", "entities": {}}')
    with_key = llm.get_settings().model_copy(update={"openai_api_key": "sk-platform-key"})
    monkeypatch.setattr(llm, "get_settings", lambda: with_key)
    assert llm.resolve(None, None).provider == "external"  # a platform key exists...
    client.put("/api/admin/ai-settings", json={"model": "gpt-4o-mini", "provider": "rules"})
    assert client.get("/api/admin/ai-settings").json()["effective"] == "rules"  # ...but this tenant chose rules
    client.post("/api/intents", json={"text": "Add a prospect. name: Rules Co"})
    assert calls == []


def test_admin_test_button_reports_answer_and_speed(client, fake_model):
    fake_model('{"intent_type": "INT-04", "entities": {"company_name": "Acme Telecom", "source": "referral"}, "confidence": 0.9}')
    result = client.post("/api/admin/ai-settings/test", json={"provider": "local"}).json()
    assert result["ok"] and result["provider"] == "local" and result["intent_type"] == "INT-04"
    assert result["entities"]["company_name"] == "Acme Telecom" and result["latency_ms"] >= 0

    no_key = client.post("/api/admin/ai-settings/test", json={"provider": "external"}).json()
    assert not no_key["ok"] and "No API key" in no_key["error"]
    rules = client.post("/api/admin/ai-settings/test", json={"provider": "rules", "text": "Add a prospect. name: R1"}).json()
    assert rules["ok"] and rules["intent_type"] == "INT-15"
    assert client.put("/api/admin/ai-settings", json={"model": "x", "provider": "skynet"}).status_code == 400


def test_local_status_when_not_installed(client, monkeypatch):
    import httpx

    def refuse(*a, **k):
        raise httpx.ConnectError("refused")

    monkeypatch.setattr(httpx, "get", refuse)
    status = client.get("/api/admin/ai-settings/local-status").json()
    assert status["reachable"] is False and "local_llm.sh install" in status["error"]


def test_configure_360_agent_runs_on_own_model(client, fake_model):
    fake_model('{"ops": [{"op": "set_theme", "preset": "daylight"}], "reply": "Switched to light."}')
    client.put("/api/admin/c360/layout", json={"layout": client.get("/api/c360/layout").json()["layout"]})
    client.put("/api/admin/ai-settings", json={"model": "gpt-4o-mini", "provider": "local"})
    plan = client.post("/api/admin/c360/agent", json={"instruction": "make it light"}).json()
    assert plan["engine"] == "ai" and plan["layout"]["theme"]["preset"] == "daylight" and plan["reply"] == "Switched to light."
    assert client.get("/api/admin/c360").json()["ai_label"].startswith("own model")


def test_training_data_export(client):
    client.post("/api/intents", json={"text": "Add a prospect. name: Real Usage Co, email: hi@real.co"})
    resp = client.get("/api/admin/ai-settings/training-data?per_template=2")
    assert resp.status_code == 200 and "attachment" in resp.headers["content-disposition"]
    rows = [json.loads(line) for line in resp.text.splitlines()]
    assert len(rows) == int(resp.headers["x-example-count"]) > 80
    for row in rows:
        roles = [m["role"] for m in row["messages"]]
        assert roles == ["system", "user", "assistant"]
        json.loads(row["messages"][2]["content"])  # every answer is valid JSON
    answers = [json.loads(r["messages"][2]["content"]) for r in rows]
    intents = {a.get("intent_type") for a in answers}
    assert {f"INT-{i:02d}" for i in range(1, 34)} <= intents
    assert any(a.get("entities", {}).get("name") == "Real Usage Co" for a in answers)  # real history first
    assert any("ops" in a for a in answers)  # Configure 360 agent examples


@pytest.mark.parametrize("raw", ['{"a": 1}', '```json\n{"a": 1}\n```', 'Sure! Here it is: {"a": 1} hope that helps'])
def test_parse_json_tolerates_chatty_small_models(raw):
    assert llm.parse_json(raw) == {"a": 1}


def test_parse_json_rejects_non_json():
    with pytest.raises(llm.LlmUnavailable):
        llm.parse_json("I cannot help with that")

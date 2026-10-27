"""Flow Studio -> Describe the flow, said the short way: "For any B2B Prospect if
the Company type is PSU, convert it as Account" - the scope lead-in ("For any B2B
prospect") is not a step, and "convert it as Account" is the Convert intent."""

import pytest

from app.orchestration import flow_ai

SAID = [
    "For any B2B Prospect if the Company type is PSU, convert it as Account",
    "For any B2B Prospect, if the Company type is PSU, convert it to an Account",
    "For all B2B prospects if company type is PSU then convert it into account",
]


@pytest.mark.parametrize("prompt", SAID)
def test_flow_studio_reads_the_short_rule(client, prompt):
    draft = client.post("/api/admin/flows/ai-generate", json={"prompt": prompt, "trigger_type": "intent", "entity_type": "party"}).json()
    assert "Could not interpret" not in (draft.get("notes") or ""), draft.get("notes")
    kinds = {n["type"]: n for n in draft["graph"]["nodes"]}
    assert kinds["condition"]["config"]["value"] == "PSU" and "company_type" in kinds["condition"]["config"]["field"], kinds
    assert kinds["action"]["config"]["intent"] == "INT-35", kinds
    assert not draft["validation_errors"], draft["validation_errors"]


def test_a_model_that_times_out_says_so(client, monkeypatch):
    """A slow on-server model falls back to the rules - and the note says why, not "no AI key configured"."""
    from app.crm_core import llm

    def slow(*_a, **_k):
        raise llm.LlmUnavailable("own model (brt360-telecom) unavailable: timed out after 90s")

    monkeypatch.setattr(llm, "resolve", lambda *a, **k: llm.local_target())
    monkeypatch.setattr(llm, "chat_json", slow)
    draft = client.post("/api/admin/flows/ai-generate", json={"prompt": SAID[0], "trigger_type": "intent", "entity_type": "party"}).json()
    assert "timed out after 90s" in draft["notes"] and "no AI key" not in draft["notes"], draft["notes"]
    assert {n["type"] for n in draft["graph"]["nodes"]} >= {"condition", "action"}


def test_the_scope_lead_in_is_dropped():
    assert flow_ai._LEAD_IN.sub("", "For any B2B Prospect if the Company type is PSU, convert it").startswith("if the Company")

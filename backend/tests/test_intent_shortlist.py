"""The model is shown only the intents a request could mean - every one of the
catalog's intents reachable, prompts that fit a small local model's context,
and one request never asks the model the same thing twice."""

from app.crm_core import llm
from app.intent_engine import classifier, shortlist
from app.orchestration import agent, flow_ai, flow_records


def test_the_prompt_lists_the_intents_the_request_could_mean():
    prompt = classifier.system_prompt(None, "reserve a SIM from inventory for order 33")
    assert "INT-123" in prompt and "Reserve inventory" in prompt  # beyond the 34 the original prompt describes
    assert prompt.startswith("You are the NLU classifier") and "strict JSON" in prompt
    assert len(prompt) < len(classifier.system_prompt()) * 0.8
    # misspelt, and in everyday words
    assert shortlist.pick("the customer is complaning their internet is down", shortlist.builtin_entries())[0] == "INT-10"
    assert "INT-84" in shortlist.pick("show me the invoices for customer 12", shortlist.builtin_entries())


def test_training_examples_always_list_their_answer():
    prompt = classifier.system_prompt(None, "something vague", must_include=("INT-91",))
    assert "INT-91" in prompt


def test_the_planner_prompt_fits_a_small_model():
    catalog = {e["code"]: e for e in shortlist.builtin_entries()}
    goal = "Add prospect Globex Ltd, then generate a lead for Fiber 500, then approve the lead"
    prompt = agent.plan_prompt(catalog, goal, None)
    assert len(prompt) // 4 < 3000
    assert all(f"\n{code} |" in prompt for code in ("INT-15", "INT-46", "INT-17"))
    assert "\nINT-128 |" in agent.plan_prompt(catalog, goal, None, ("INT-128",))


def test_the_flow_prompt_sends_only_the_tables_it_is_about():
    tables = flow_ai._tables_for_prompt(flow_records.catalog(), "when an order is approved add an order item", "order")
    names = [t["name"] for t in tables]
    assert "orders" in names and "order_items" in names and len(names) <= flow_ai.PROMPT_TABLES


def test_planning_a_goal_asks_the_model_once_per_request(client, monkeypatch):
    calls = []

    def fake_chat_json(target, system, user, temperature=0.0, max_tokens=None):
        calls.append(system[:40])
        raise llm.LlmUnavailable("model is down")

    monkeypatch.setattr(llm, "resolve", lambda db, tenant_id, live=False: llm.LlmTarget("local", "m", "local", "http://x", 5.0))
    monkeypatch.setattr(llm, "chat_json", fake_chat_json)
    out = client.post("/api/assistant/understand", json={"text": "show me the invoices for customer 12"}).json()
    assert out["route"] in ("unknown", "process", "answer", "action", "chain")
    planner_calls = [c for c in calls if c.startswith("You are the planning agent")]
    assert len(planner_calls) <= 1, calls
    # no model call per part of a chain - the rules read each part
    calls.clear()
    client.post("/api/assistant/understand", json={"text": "Add prospect Globex Ltd, then generate a lead for Fiber 500, then approve the lead"})
    assert not [c for c in calls if c.startswith("You are the NLU classifier")], calls

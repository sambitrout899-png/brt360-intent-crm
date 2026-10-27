"""A source-preserving edit must actually wait for installation before activation."""
import copy
from types import SimpleNamespace

import pytest

from app.crm_core import llm
from app.orchestration import fulfillment_ai, fulfillment_engine
from app.orchestration import fulfillment_functions as fx
from tests.test_fulfillment_orchestration import (
    approve_order, close_next_field_job, standard_pattern, tasks_of,
)
from tests.test_phase5_fulfillment import _generated_order

REQUEST = ("Prepare a new pattern where everything should be same as the standard eTOM fixed broadband pattern, "
           "but just make sure the service activation towards the network happens only after the 1st TOM work order")


def test_exact_request_survives_model_failure(client, monkeypatch):
    monkeypatch.setattr(llm, "resolve", lambda *a: SimpleNamespace(label="own model (test)"))
    seen = []

    def unavailable(target, system, user, **kwargs):
        seen.append((system, kwargs))
        raise llm.LlmUnavailable("own model (test) unavailable: could not connect to the configured model server")

    monkeypatch.setattr(llm, "chat_json", unavailable)
    response = client.post("/api/admin/fulfillment/patterns/generate", json={"description": REQUEST})
    assert response.status_code == 200, response.text
    draft = response.json()
    assert draft["validation_errors"] == []
    assert "could not connect" in draft["notes"]
    assert "installation" in seen[0][0] and seen[0][1]["max_tokens"] == 4096
    graph = draft["pattern"]["graph"]
    original = fulfillment_engine.standard_graph()
    assert {n["id"]: n["config"] for n in graph["nodes"]} == {n["id"]: n["config"] for n in original["nodes"]}
    edges = {(e["source"], e["target"]) for e in graph["edges"]}
    assert {("s3", "s5"), ("t1", "s4"), ("s4", "t2")} <= edges
    assert ("s4", "s5") not in edges


@pytest.mark.parametrize("async_activation", [False, True])
def test_install_then_activate_then_test_and_accept(client, async_activation):
    product = "Delayed Activation Fiber"
    graph = fulfillment_ai._activation_after_work(fulfillment_engine.standard_graph(), REQUEST)
    next(n for n in graph["nodes"] if n["id"] == "s4")["config"]["response"] = "async" if async_activation else "sync"
    standard_pattern(client, products=product, graph=graph)
    order_id = _generated_order(client, "Delayed Activation Co", product)
    approve_order(client, order_id)
    assert tasks_of(client, "SOM", order_id)[1]["s4"] == "pending"
    assert tasks_of(client, "TOM", order_id)[1] == {"t1": "waiting", "t2": "pending", "t3": "pending"}
    close_next_field_job(client, order_id)
    if async_activation:
        assert tasks_of(client, "SOM", order_id)[1]["s4"] == "waiting"
        assert tasks_of(client, "TOM", order_id)[1]["t2"] == "pending"
        view = client.get(f"/api/fulfillment/crm-orders/{order_id}").json()[0]
        activation = next(t for t in view["children"][0]["tasks"] if t["node_id"] == "s4")
        response = client.post("/api/fulfillment/inventory/callback", json={"request_id": activation["external_ref"], "status": "completed", "resources": []})
        assert response.status_code == 200, response.text
    assert tasks_of(client, "SOM", order_id)[1]["s4"] == "completed"
    assert tasks_of(client, "TOM", order_id)[1] == {"t1": "completed", "t2": "waiting", "t3": "pending"}
    close_next_field_job(client, order_id)
    close_next_field_job(client, order_id)
    for layer in ("COM", "SOM", "TOM"):
        assert tasks_of(client, layer, order_id)[0] == "completed"


def test_route_deadlock_is_rejected():
    graph = fulfillment_engine.standard_graph()
    # Standard route waits for activation, which now waits for a job that
    # cannot open until that route starts.
    graph["edges"].append({"source": "t1", "target": "s4"})
    assert any("deadlock" in e for e in fulfillment_engine.validate_graph(graph))


def test_activation_fallout_blocks_later_work_until_retry(client, monkeypatch):
    product = "Activation Retry Fiber"
    graph = fulfillment_ai._activation_after_work(fulfillment_engine.standard_graph(), REQUEST)
    standard_pattern(client, products=product, graph=graph)
    order_id = _generated_order(client, "Activation Retry Co", product)
    other_id = _generated_order(client, "Other Activation Co", product)
    approve_order(client, order_id)
    approve_order(client, other_id)
    real = fx.IMPLEMENTATIONS["activate_service"]

    def fail(rt):
        raise ValueError("Network activation refused")

    monkeypatch.setitem(fx.IMPLEMENTATIONS, "activate_service", fail)
    close_next_field_job(client, order_id)
    assert tasks_of(client, "SOM", order_id)[1]["s4"] == "failed"
    assert tasks_of(client, "TOM", order_id)[1]["t2"] == "pending"
    assert tasks_of(client, "SOM", other_id)[1]["s4"] == "pending"
    monkeypatch.setitem(fx.IMPLEMENTATIONS, "activate_service", real)
    view = client.get(f"/api/fulfillment/crm-orders/{order_id}").json()[0]
    activation = next(t for t in view["children"][0]["tasks"] if t["node_id"] == "s4")
    response = client.post(f"/api/fulfillment/tasks/{activation['id']}/resolve", json={"action": "retry"})
    assert response.status_code == 200, response.text
    assert tasks_of(client, "TOM", order_id)[1]["t2"] == "waiting"
    assert tasks_of(client, "TOM", other_id)[1]["t1"] == "waiting"


def test_edit_preserves_source_settings_and_unknown_edit_is_not_applied(client, monkeypatch):
    monkeypatch.setattr(llm, "resolve", lambda *a: None)
    source = {"name": "Custom broadband", "products": "Custom Fiber", "priority": 777, "segment": "B2B",
              "trigger_intents": "INT-25", "graph": fulfillment_engine.standard_graph()}
    source["graph"]["nodes"][0]["label"] = "Keep this custom label"
    source["graph"]["nodes"][0]["mandatory"] = True
    saved = copy.deepcopy(source)
    draft = client.post("/api/admin/fulfillment/patterns/generate", json={"description": REQUEST, "source_pattern": source}).json()
    assert draft["validation_errors"] == []
    for key in ("products", "priority", "segment", "trigger_intents"):
        assert draft["pattern"][key] == source[key]
    assert draft["pattern"]["graph"]["nodes"][0]["label"] == "Keep this custom label"
    assert draft["pattern"]["graph"]["nodes"][0]["mandatory"] is True
    assert source == saved
    unsupported = client.post("/api/admin/fulfillment/patterns/generate", json={
        "description": "Keep the same pattern but change something unspecified", "source_pattern": source}).json()
    assert unsupported["validation_errors"]
    assert unsupported["pattern"]["graph"] == source["graph"]


def test_model_truncation_is_explicit(monkeypatch):
    response = SimpleNamespace(choices=[SimpleNamespace(finish_reason="length", message=SimpleNamespace(content='{"nodes":['))])
    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=lambda **kw: response)))
    monkeypatch.setattr(llm, "_client", lambda target: client)
    with pytest.raises(llm.LlmUnavailable, match="output token limit"):
        llm.chat_json(llm.local_target(), "system", "user", max_tokens=4096)

from unittest.mock import Mock

import pytest

from app.crm_core import llm, orchestration_mode_service
from app.models.orchestration_policy import OrchestrationMode
from app.orchestration import actions_fulfillment, fulfillment_ops, llm_planner
from app.orchestration.orchestrator import SagaStep


@pytest.mark.parametrize("params", [
    {"order_id": 31, "order_item_id": 7},
    {},
    {"order_id": 31, "tenant_id": 2},
    {"order_id": 31, "db": None},
    [],
    None,
])
def test_invalid_parameters_fall_back_before_any_operation_runs(monkeypatch, params):
    monkeypatch.setattr(llm, "resolve", lambda *args: object())
    monkeypatch.setattr(orchestration_mode_service, "resolve_mode", lambda *args: OrchestrationMode.llm_planned)
    calls = []

    def submit(db, tenant_id, order_id):
        calls.append(order_id)
        return {"order_id": order_id}

    monkeypatch.setitem(fulfillment_ops.CATALOG, "submit_order_to_orchestration", submit)
    monkeypatch.setattr(llm_planner, "_ask_llm_for_plan", lambda *args: [
        {"operation": "submit_order_to_orchestration", "params": {"order_id": 31}},
        {"operation": "submit_order_to_orchestration", "params": params},
    ])
    fallback = Mock(return_value={"order_id": 31, "work_order_id": 9})
    result = actions_fulfillment._run_consequence(
        None, 1, "INT-25", "Approve order", {"order_id": 31},
        ["submit_order_to_orchestration"], [SagaStep("fixed", fallback)],
    )
    assert result == {"order_id": 31, "work_order_id": 9}
    fallback.assert_called_once_with()
    assert calls == []


def test_valid_plan_executes_with_operation_specific_parameters(monkeypatch):
    monkeypatch.setattr(llm, "resolve", lambda *args: object())
    calls = []

    def submit(db, tenant_id, order_id):
        calls.append((tenant_id, order_id))
        return {"order_id": order_id}

    monkeypatch.setitem(fulfillment_ops.CATALOG, "submit_order_to_orchestration", submit)
    monkeypatch.setattr(llm_planner, "_ask_llm_for_plan", lambda *args: [
        {"operation": "submit_order_to_orchestration", "params": {"order_id": 31}},
    ])
    steps = llm_planner.plan_steps(None, 1, "Approve order", {}, ["submit_order_to_orchestration"])
    assert calls == []
    assert steps[0].action() == {"order_id": 31}
    assert calls == [(1, 31)]

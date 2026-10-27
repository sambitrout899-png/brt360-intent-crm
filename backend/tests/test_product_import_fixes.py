"""Product Modeller import, as product managers fill the sheet:
- price impacts separated by commas ("100 Mbps:1499 each, 200 Mbps:2499 each") - a comma inside a number stays;
- "X must not be Y" rules keep Y as the value (not "be Y");
- a bulk import reads rules with the built-in reader first, so a sheet of rules doesn't spend a model call per row
  (with a CPU-hosted model that timed the import out behind nginx)."""

from test_sales_to_order import _launch

from app.crm_core import llm
from app.crm_core import product_model_service as pm


def test_comma_separated_price_impacts():
    assert pm.parse_impacts("100 Mbps:1499 each, 200 Mbps:2,499 each, 1 Gbps:7499 each") == [
        {"value": "100 Mbps", "type": "per_unit", "amount": 1499.0},
        {"value": "200 Mbps", "type": "per_unit", "amount": 2499.0},
        {"value": "1 Gbps", "type": "per_unit", "amount": 7499.0}]
    assert pm.parse_impacts("Silver 99.5%:0 each, Gold 99.9%:5000 each")[1] == {"value": "Gold 99.9%", "type": "per_unit", "amount": 5000.0}
    assert pm.parse_impacts("Fiber: +500; DSL: -10%") == [{"value": "Fiber", "type": "flat", "amount": 500.0},
                                                         {"value": "DSL", "type": "percent", "amount": -10.0}]


def test_must_not_be_keeps_the_value():
    d = pm._rules_compile("constraint", "If Access Medium is Microwave then Bandwidth must not be 1 Gbps", ["Access Medium", "Bandwidth"], [])
    assert d["then"] == [{"field": "Bandwidth", "op": "ne", "value": "1 Gbps"}]
    assert pm.explain("constraint", d, "ILL") == "If Access Medium is Microwave: Bandwidth must not be 1 Gbps"
    d = pm._rules_compile("constraint", "If Access Medium is DSL then Bandwidth must be 2 Mbps", ["Access Medium", "Bandwidth"], [])
    assert d["then"] == [{"field": "Bandwidth", "op": "eq", "value": "2 Mbps"}]


def test_imports_read_rules_without_the_model_when_they_can(client, monkeypatch):
    _launch(client, name="Rule Fiber")
    calls = []
    monkeypatch.setattr(llm, "resolve", lambda *a, **k: llm.local_target())

    def model(*a, **k):
        calls.append(1)
        raise llm.LlmUnavailable("not in tests")

    monkeypatch.setattr(llm, "chat_json", model)
    db = client.TestSessionLocal()
    try:
        out = pm.compile_rule(db, 1, "Rule Fiber", "eligibility", "only for B2B customers", prefer_rules=True)
        assert out["engine"] == "rules" and calls == []  # the import path: no model call
        pm.compile_rule(db, 1, "Rule Fiber", "eligibility", "only for B2B customers")
        assert calls == [1]  # one rule typed in the Rules tab still asks the model first
    finally:
        db.close()

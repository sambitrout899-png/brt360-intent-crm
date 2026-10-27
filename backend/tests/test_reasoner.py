"""The Intent Command Bar reasons about an automation: "I need to create a process which will create an
order automatically from the prospect for a prospect with Company Type for a B2B scenario for any product".

- it is understood as an automation (not a field, not one action to run now) - outcome: an order;
- what is missing is asked (which Company Type), the business's choices too (Presales? auto-approve?);
- the road is the existing intents; the step that needs the customer (their yes) is a wait, not skipped:
  flow 1 runs up to the proposal, flow 2 creates the order when the customer accepts;
- built on consent; a matching prospect really reaches an order, any other does not;
- asked again, the live flows are recommended instead of built twice."""

from test_sales_to_order import _launch, run

from app.models.crm import Lead
from app.models.orders import Order

ASKED = ("I need to create a process which will create an order automatically from the prospect for a prospect with Company Type "
         "for a B2B scenario for any product")
STEPS = [{"kind": "goal", "text": ASKED}]


def _design(client, answers):
    return client.post("/api/process-builder/design", json={"request": ASKED, "steps": STEPS, "answers": answers}).json()


def test_an_order_automation_is_reasoned_asked_built_and_runs(client):
    _launch(client, name="Fiber 500 Bundle", price=500)
    out = client.post("/api/assistant/understand", json={"text": ASKED}).json()
    assert out["route"] == "process" and out["goal"]["outcome"] == "order", out
    assert out["goal"]["key"] == "company_type" and out["goal"]["value"] is None and out["goal"]["product"] == "*"
    asked = {q["id"]: q for q in out["blueprint"]["questions"]}
    assert set(asked) == {"c1.value"} and "Government" in [o["value"] for o in asked["c1.value"]["options"]], asked

    blueprint = _design(client, {"c1.value": "Government"})
    asked = {q["id"]: q for q in blueprint["questions"]}
    assert set(asked) == {"c1.presales", "c1.approval"} and asked["c1.presales"]["default"] == "skip", asked

    answers = {"c1.value": "Government", "c1.presales": "skip", "c1.approval": "auto"}
    blueprint = _design(client, answers)
    assert blueprint["ready"], blueprint
    assert [c["kind"] for c in blueprint["components"]] == ["flow", "flow"], blueprint["components"]
    assert any("customer's yes" in r for r in blueprint["reasoning"]) and any("By hand today" in r for r in blueprint["reasoning"])

    built = client.post("/api/process-builder/build", json={"request": ASKED, "steps": STEPS, "answers": answers, "mode": "execute"}).json()
    assert built["status"] == "built", built
    assert all(c["ok"] for c in built["checks"]), built["checks"]

    gov = run(client, "INT-15", name="Ministry of Telecom", segment="B2B", details={"company_type": "Government"}, products=["Fiber 500 Bundle"])
    assert gov["status"] == "executed", gov
    ran = [t["intent"] for f in gov["result"].get("flows_run", []) for t in f["trace"] if t["type"] == "action" and not t.get("skipped")]
    assert ran == ["INT-46", "INT-17", "INT-05", "INT-06", "INT-22"], "\n".join(
        str({k: t.get(k) for k in ("intent", "status", "skipped", "input", "output") if t.get(k) is not None})[:300]
        for f in gov["result"]["flows_run"] for t in f["trace"])
    proposal = next(t for f in gov["result"]["flows_run"] for t in f["trace"] if t.get("intent") == "INT-22")["output"]["proposal_id"]
    accepted = run(client, "INT-23", proposal_id=proposal, decision="approve")
    assert accepted["status"] == "executed", accepted
    db = client.TestSessionLocal()
    try:
        assert db.query(Order).filter(Order.party_id == gov["result"]["party_id"]).count() == 1, accepted["result"].get("flows_run")
    finally:
        db.close()

    other = run(client, "INT-15", name="Private Co", segment="B2B", details={"company_type": "Private Limited"}, products=["Fiber 500 Bundle"])
    db = client.TestSessionLocal()
    try:
        assert db.query(Lead).filter(Lead.party_id == other["result"]["party_id"]).count() == 0
    finally:
        db.close()

    again = _design(client, answers)
    assert not again["components"] and len(again["recommendations"]) == 2, again


def _ran(result):
    return [t["intent"] for f in (result or {}).get("flows_run", []) for t in f["trace"] if t["type"] == "action" and t.get("status") == "ok"]


def test_presales_and_the_approver_are_waited_for_then_the_road_carries_on(client):
    _launch(client, name="Fiber 500 Bundle", price=500)
    answers = {"c1.value": "Government", "c1.presales": "keep", "c1.approval": "wait"}
    blueprint = _design(client, answers)
    assert blueprint["ready"] and len(blueprint["components"]) == 4, blueprint  # prospect / feasible / approved / accepted
    built = client.post("/api/process-builder/build", json={"request": ASKED, "steps": STEPS, "answers": answers, "mode": "execute"}).json()
    assert built["status"] == "built", built

    gov = run(client, "INT-15", name="Ministry of Telecom", segment="B2B", products=["Fiber 500 Bundle"],
              details={"company_type": "Government", "address_line1": "Sanchar Bhawan, 20 Ashoka Road", "city": "New Delhi"})
    assert _ran(gov["result"]) == ["INT-46", "INT-17", "INT-37", "INT-38"], "\n".join(
        str({k: t.get(k) for k in ("intent", "status", "skipped", "input", "output") if t.get(k) is not None})[:400]
        for f in gov["result"]["flows_run"] for t in f["trace"] if t.get("intent") in ("INT-37", "INT-38"))
    opp = next(t for f in gov["result"]["flows_run"] for t in f["trace"] if t.get("intent") == "INT-17")["output"]["opportunity_id"]

    feasible = run(client, "INT-18", opportunity_id=opp, decision="feasible")  # Presales, by hand
    assert feasible["status"] == "executed" and _ran(feasible["result"]) == ["INT-05"], feasible
    quote = next(t for f in feasible["result"]["flows_run"] for t in f["trace"] if t.get("intent") == "INT-05")["output"]["quote_id"]

    approved = run(client, "INT-06", quote_id=quote, decision="approve")  # the approver, by hand
    assert _ran(approved["result"]) == ["INT-22"], approved["result"].get("flows_run")
    proposal = next(t for f in approved["result"]["flows_run"] for t in f["trace"] if t.get("intent") == "INT-22")["output"]["proposal_id"]
    accepted = run(client, "INT-23", proposal_id=proposal, decision="approve")
    assert _ran(accepted["result"]) == ["INT-24"], accepted["result"].get("flows_run")


def test_a_question_or_a_one_off_action_is_not_an_automation(client):
    from app.orchestration import reasoner

    db = client.TestSessionLocal()
    try:
        assert reasoner.understand(db, 1, "How do I create an order for a prospect?") is None
        assert reasoner.understand(db, 1, "Create an order for quote 12") is None
        goal = reasoner.understand(db, 1, "Automatically create a quote for B2B prospects whose company type is PSU, product \"Fiber 500 Bundle\"")
        assert goal and goal.outcome == "quote" and goal.field_key == "company_type" and goal.value == "PSU" and goal.product == "Fiber 500 Bundle"
        # "Government" alone is an Industry and a Company type - asked, not guessed
        goal = reasoner.understand(db, 1, "Automatically create a quote for every Government prospect")
        assert goal and goal.field_key is None and {c[0] for c in goal.choices} >= {"company_type", "industry"}, goal
    finally:
        db.close()

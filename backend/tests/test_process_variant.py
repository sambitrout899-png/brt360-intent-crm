"""A variant of a vanilla process said in plain English - "for a B2B prospect
of Company Type Government, create a quote directly ... skip Generate lead,
Approve a lead, Configure a product" - is read as a variant, not as "your
vanilla process already does this": the condition's field, an automation that
runs the skipped steps by itself for those records only, and an honest note
where the request conflicts with the vanilla rules."""

from test_sales_to_order import _launch, run

from app.models.crm import Lead, Opportunity

ASKED = ('build a process that for a B2B prospect of Company Type as "Government" - create directly a quote with product as "Fiber 500 Bundle" '
         "and send it to presales for feasibility check.The vanilla steps to create a lead->opportunity should be skipped and directly a create "
         "quote intent should open up. I want to skip certain vanilla steps like Generate lead, Approve a Lead, Configure a product")


def test_a_variant_is_read_designed_and_built_on_consent(client):
    _launch(client, name="Fiber 500 Bundle", price=500)
    out = client.post("/api/assistant/understand", json={"text": ASKED}).json()
    assert out["route"] == "process"
    fit = out["fit"]
    v = fit["variant"]
    assert v["condition"]["field"] == "Company Type" and v["condition"]["value"] == "Government" and v["condition"]["segment"] == "B2B"
    assert {"INT-46", "INT-17", "INT-37"} <= set(v["skipped"])
    assert v["automated"] == ["INT-46", "INT-17"] and v["target"] == "INT-05" and v["product"] == "Fiber 500 Bundle"
    assert v["conflicts"] and v["conflicts"][0]["what"] == "Presales after the quote"
    assert fit["gaps"] >= 1 and "Nothing to build" not in out["why"] and "Company Type = Government" in out["why"]
    assert all(i["kind"] == "variant" for i in fit["items"])
    assert v["condition"]["exists"] and v["condition"]["key"] == "company_type"  # the B2B prospect page has Company type already
    kinds = [c["kind"] for c in out["blueprint"]["components"]]
    assert kinds == ["flow"], out["blueprint"]
    flow = out["blueprint"]["components"][0]
    assert flow["lines"][0] == "After “Add a prospect”, only when Company Type is Government"

    built = client.post("/api/process-builder/build", json={"request": ASKED, "steps": fit["build_steps"], "answers": {}, "graphs": {}, "mode": "execute"})
    assert built.status_code == 200, built.text

    # used: a Government prospect lands on an opportunity for Fiber 500 Bundle - no lead steps for the user
    gov = run(client, "INT-15", name="Ministry of Telecom", segment="B2B", details={"company_type": "Government"})
    assert gov["status"] == "executed", gov
    ran = [t["intent"] for f in gov["result"].get("flows_run", []) for t in f["trace"] if t["type"] == "action" and not t.get("skipped")]
    assert ran == ["INT-46", "INT-17"], gov["result"].get("flows_run")
    assert "INT-05" in [n["code"] for n in gov["next_actions"]]
    db = client.TestSessionLocal()
    try:
        lead = db.query(Lead).filter(Lead.party_id == gov["result"]["party_id"]).one()
        opp = db.query(Opportunity).filter(Opportunity.lead_id == lead.id).one()
        assert lead.status.value == "qualified" and opp.offering_name == "Fiber 500 Bundle"
    finally:
        db.close()
    quote = run(client, "INT-05", opportunity_id=opp.id)  # the product is already on it
    assert quote["status"] == "executed", quote

    # anyone else follows the vanilla road
    other = run(client, "INT-15", name="Private Co", segment="B2B", details={"company_type": "Private Limited"})
    db = client.TestSessionLocal()
    try:
        assert db.query(Lead).filter(Lead.party_id == other["result"]["party_id"]).count() == 0
    finally:
        db.close()

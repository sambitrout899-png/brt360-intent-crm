"""Process fit: a business described in plain English is read against the
vanilla processes - what is already a vanilla step, what information has to be
captured (custom fields on the right page), which rules / automations are
missing, and which steps no action does yet. Only the gaps go to the builders,
and only on consent."""

from test_sales_to_order import _user

from app.models.custom_fields import CustomFieldDefinition

B2B = ("We sell fibre to enterprise customers. The account manager qualifies the lead and configures the product. "
       "Presales checks feasibility for every opportunity. We need to capture the customer's GST number and PAN. "
       "Every opportunity must have a site contact phone and a site survey date. "
       "Quotes with discount above 15% need approval from the CFO. "
       "The customer signs an NDA before we share the proposal. Then the customer sends a purchase order and Sales Ops matches it.")


def _ask(client, text, headers=None):
    resp = client.post("/api/assistant/understand", json={"text": text}, headers=headers or {})
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_a_described_process_is_read_against_vanilla_and_only_gaps_go_to_the_builders(client):
    out = _ask(client, B2B)
    assert out["route"] == "process"
    fit = out["fit"]
    assert fit["process"]["key"] == "b2b_sto" and fit["process"]["segment"] == "B2B"
    kinds = {i["text"]: (i["kind"], i["status"]) for i in fit["items"]}
    assert kinds["We sell fibre to enterprise customers"] == ("context", "covered")
    assert kinds["Presales checks feasibility for every opportunity"] == ("step", "covered")
    assert kinds["We need to capture the customer's GST number and PAN"] == ("data", "covered")  # the B2B page has both already
    assert kinds["Every opportunity must have a site contact phone and a site survey date"] == ("data", "gap")
    assert kinds["Quotes with discount above 15% need approval from the CFO"] == ("config", "gap")
    assert kinds["The customer signs an NDA before we share the proposal"] == ("new_step", "gap")
    two = next(i for i in fit["items"] if i["text"].startswith("The account manager"))
    assert two["codes"] == ["INT-04", "INT-37"]  # two vanilla steps in one sentence
    po = next(i for i in fit["items"] if "purchase order" in i["text"])
    assert po["codes"] == ["INT-39", "INT-40"]
    fields = next(i for i in fit["items"] if i["text"].startswith("Every opportunity"))["fields"]
    assert [(f["label"], f["data_type"], f["mandatory"]) for f in fields] == [("Site contact phone", "phone", True), ("Site survey date", "date", True)]
    # the gaps, in the builders' own words: one Page Designer change for the opportunity page, the approval rule
    assert [s["text"] for s in fit["build_steps"]] == [
        "On the Opportunity page add a mandatory field Site contact phone as phone; add a mandatory field Site survey date as date",
        "Quotes with discount above 15% need approval from the CFO"]
    assert fit["new_steps"] == ["The customer signs an NDA before we share the proposal"]
    assert "B2B Sales to Order" in out["why"] and "1 new step" in out["why"]
    page = next(c for c in out["blueprint"]["components"] if c["kind"] == "page")
    assert page["lines"] == ["Add new field Site contact phone (phone) - mandatory", "Add new field Site survey date (date) - mandatory"]


def test_the_gaps_are_built_on_consent(client):
    fit = _ask(client, "Our sales process: every enterprise opportunity must have a competitor name and an expected close date.")["fit"]
    assert fit["process"]["key"] in ("b2b_sto", "l2q") and fit["gaps"] == 1
    fields = {f["label"]: f for f in fit["items"][-1]["fields"]}
    assert not fields["Competitor name"]["exists"] and fields["Expected close date"]["data_type"] == "date"
    payload = {"request": "Opportunity details", "steps": fit["build_steps"], "answers": {}, "graphs": {}}

    def opportunity_fields():
        db = client.TestSessionLocal()
        try:
            return {c.key: c.data_type for c in db.query(CustomFieldDefinition).filter(CustomFieldDefinition.entity_type == "opportunity")}
        finally:
            db.close()

    assert client.post("/api/process-builder/build", json={**payload, "mode": "rehearse"}).status_code == 200
    assert "competitor_name" not in opportunity_fields()  # the rehearsal keeps nothing
    built = client.post("/api/process-builder/build", json={**payload, "mode": "execute"})
    assert built.status_code == 200, built.text
    made = opportunity_fields()
    assert made.get("competitor_name") == "text" and made.get("expected_close_date") == "date"
    again = _ask(client, "Our sales process: every enterprise opportunity must have a competitor name and an expected close date.")["fit"]
    assert again["gaps"] == 0  # built - now it is part of the set-up


def test_a_process_vanilla_already_runs_needs_nothing(client):
    out = _ask(client, "Our consumer process: we verify the customer's identity, then place a B2C order and complete the installation.")
    fit = out["fit"]
    assert fit["process"]["key"] == "b2c" and fit["gaps"] == 0 and fit["build_steps"] == []
    assert "Nothing to build" in out["why"] and "blueprint" not in out


def test_commands_journeys_and_vague_requests_route_as_before(client):
    assert "fit" not in _ask(client, "Add prospect Fit Co")
    assert _ask(client, "Make everything better for the customers")["route"] == "unknown"
    journey = _ask(client, "Build a B2C process - customer -> identity verification -> raise order -> installation -> start billing")
    assert journey["route"] == "process" and "journey" in journey["why"]  # still built as a journey ...
    assert journey["fit"]["journey"] and journey["fit"]["process"]["key"] == "b2c"  # ... with its vanilla fit shown


def test_new_custom_fields_said_plainly_are_page_fields_not_a_product_design(client):
    asked = "Add two new custom fields to Prospect - Education and Marital Status"
    out = _ask(client, asked)
    assert out["route"] == "process" and out["fit"]["data_only"]
    assert "2 new custom fields (Education, Marital Status) on the B2B prospect page and B2C prospect page" in out["why"]
    assert [c["kind"] for c in out["blueprint"]["components"]] == ["page", "page"]  # a prospect has a B2B and a B2C page - both get them
    one = _ask(client, "add Education and Marital Status as new fields on B2C prospects")["fit"]
    assert [s["text"] for s in one["build_steps"]] == ["On the B2C prospect page add a field Education; add a field Marital Status"]
    built = client.post("/api/process-builder/build", json={"request": asked, "steps": out["fit"]["build_steps"], "answers": {}, "graphs": {},
                                                             "mode": "execute"})
    assert built.status_code == 200, built.text
    db = client.TestSessionLocal()
    try:
        keys = {c.key for c in db.query(CustomFieldDefinition).filter(CustomFieldDefinition.entity_type == "party")}
    finally:
        db.close()
    assert {"education", "marital_status"} <= keys
    for segment in ("B2B", "B2C"):
        form = client.get("/api/pages/party/form", params={"segment": segment}).json()
        assert {"education", "marital_status"} <= {f["key"] for s in form["sections"] for f in s["fields"]}
    assert "nothing to add" in _ask(client, "add custom fields to the prospect: Education, Marital Status")["why"]  # (the exact repeat is replayed from memory)


def test_non_administrators_see_the_fit_but_cannot_build(client):
    rep = _user(client, "fit-rep")
    out = _ask(client, B2B, headers={"X-Test-User-Id": str(rep)})
    assert out["route"] == "process" and out["needs_admin"] and out["fit"]["gaps"] >= 1 and "blueprint" not in out

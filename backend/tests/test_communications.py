"""Communications (Siebel Communications Guide) on top of the CRM: account
hierarchy, contacts, profiles, addresses and premises, friends & family lists,
assets, opportunities, billing (bill run, payments, adjustments,
arrangements, duplicates), collections, fraud, trouble ticket parent/child,
fallout, solutions and surveys, agreements and provider work orders - all
through the intents themselves."""

import datetime as dt

from app.models.comms import CmInvoice
from test_sales_to_order import _launch, _opportunity, _user, run, say

CM = "/api/comms"


def _live_customer(client, name, email=None):
    _launch(client)
    party = run(client, "INT-41", name=name, email=email or f"{name.split()[0].lower()}@example.com")["result"]["party_id"]
    order = run(client, "INT-07", party_id=party, offering_name="Fiber 900")
    assert order["status"] == "executed", order
    return party, order["result"]["order_id"]


def _usage(client, party, events):
    r = client.post(f"{CM}/usage", json={"party_id": party, "events": events})
    assert r.status_code == 200, r.text


def _backdate(client, invoice_id, days):
    db = client.TestSessionLocal()
    try:
        inv = db.get(CmInvoice, invoice_id)
        inv.issue_date = dt.date.today() - dt.timedelta(days=days + 14)
        inv.due_date = dt.date.today() - dt.timedelta(days=days)
        db.commit()
    finally:
        db.close()


def test_account_hierarchy_contacts_profiles_addresses_and_premises(client):
    holding, _ = _live_customer(client, "Acme Holdings")

    child = say(client, "Add Acme York under Acme Holdings as a service account")
    assert child["intent_type"] == "INT-72" and child["status"] == "executed", child
    york = child["result"]["party_id"]
    assert child["result"]["account"]["account_class"] == "service" and child["result"]["account"]["parent_name"] == "Acme Holdings"
    billing = run(client, "INT-72", account_name="Acme North Billing", parent_id=holding, account_class="billing")["result"]["party_id"]
    loop = run(client, "INT-72", party_id=holding, parent_id=york)
    assert loop["status"] == "failed" and "loop" in loop["rationale"]
    # a merger: the whole tree moves with its top
    parent2 = run(client, "INT-41", name="Globex Group")["result"]["party_id"]
    moved = say(client, f"move customer {holding} under customer {parent2}")
    assert moved["intent_type"] == "INT-72" and moved["status"] == "executed", moved
    tree = moved["result"]["hierarchy"]
    assert tree["party_id"] == parent2 and {c["party_id"] for c in tree["children"][0]["children"]} == {york, billing}
    assert client.get(f"{CM}/explorer?account_class=billing").json()[0]["party_id"] == billing

    contact = say(client, "Add contact Sara Ali, sara@acme.com, to Acme Holdings as the billing contact")
    assert contact["intent_type"] == "INT-73" and contact["status"] == "executed", contact
    assert contact["result"]["contact"]["name"] == "Sara Ali" and contact["result"]["contact"]["accounts"][0]["role"] == "billing"
    again = run(client, "INT-73", party_id=york, contact_name="Sara Ali", email="sara@acme.com")
    assert again["result"]["existing_contact"] and len(again["result"]["contact"]["accounts"]) == 2

    leak = run(client, "INT-74", party_id=holding, payment_method="credit_card", card_last4="4111111111111111")
    assert leak["status"] == "failed" and "last 4" in leak["rationale"]
    prof = say(client, "Bills for Acme Holdings by email to ap@acme.com, monthly, by card ending 4242")
    assert prof["intent_type"] == "INT-74" and prof["status"] == "executed", prof
    assert prof["result"]["profile"]["card"].endswith("4242") and prof["result"]["profile"]["bill_media"] == "email"
    mixed = run(client, "INT-74", party_id=holding, profile_name="Quarterly", frequency="quarterly", new=True)
    assert mixed["status"] == "failed" and "frequency" in mixed["rationale"]

    exempt = say(client, "Acme Holdings is tax exempt - certificate TX-778")
    assert exempt["intent_type"] == "INT-75" and exempt["result"]["profile"]["tax_exempt"] is True, exempt
    fin = say(client, f"update the financial profile of customer {holding} and run a credit check")
    assert fin["status"] == "executed" and fin["result"]["profile"]["rating"] in ("A", "B", "C")
    loyalty = run(client, "INT-75", party_id=holding, profile_kind="loyalty")
    assert loyalty["result"]["profile"]["monthly_revenue"] > 0 and "churn_risk" in loyalty["result"]["profile"]

    addr = say(client, "Validate the address 12 King Fahd Road, Apt 4, Riyadh 12271 for Acme Holdings and make it a premises")
    assert addr["intent_type"] == "INT-77" and addr["result"]["validated"], addr
    assert addr["result"]["standard_form"] == "12 KING FAHD RD APT 4 RIYADH 12271" and addr["result"]["premise_id"]
    bad = run(client, "INT-77", address="Riyadh")
    assert bad["result"]["validated"] is False and bad["result"]["issues"]
    hook = say(client, "Register a hookup at 12 King Fahd Road, Apt 4, Riyadh 12271: service point JACK-1 for Fiber 900")
    assert hook["intent_type"] == "INT-78" and hook["result"]["premise_id"] == addr["result"]["premise_id"], hook
    assert hook["result"]["premise"]["service_points"][0]["identifier"] == "JACK-1"

    ff = say(client, "Add 0551234567 and 0559876543 to Acme Holdings's friends and family list")
    assert ff["intent_type"] == "INT-79" and ff["result"]["list"]["active_items"] == 2, ff
    full = run(client, "INT-79", party_id=holding, add_numbers="0551110001, 0551110002, 0551110003, 0551110004")
    assert full["status"] == "failed" and "at most 5" in full["rationale"]
    gone = run(client, "INT-79", party_id=holding, remove_numbers="0551234567")
    assert gone["result"]["list"]["active_items"] == 1

    view = client.get(f"{CM}/accounts/{holding}").json()
    assert view["profiles"]["exemption"]["tax_exempt"] and view["contacts"][0]["email"] == "sara@acme.com"
    assert view["premises"][0]["service_points"] == 1 and view["rating_lists"][0]["max_items"] == 5


def test_bill_run_payments_adjustments_arrangements_and_duplicates(client):
    assert client.post("/api/admin/order-management/setup/sample").status_code == 200  # tax rules
    party, _ = _live_customer(client, "Billing Person", "bp@example.com")
    client.post("/api/admin/comms/allowances", json={"party_id": party, "name": "Minutes", "event_type": "voice", "granted": 100,
                                                     "valid_from": "2020-01-01"})
    now = dt.datetime.utcnow().replace(microsecond=0)
    _usage(client, party, [{"event_type": "voice", "quantity": 150, "at": now.isoformat(), "destination": "0551234567"},
                           {"event_type": "voice", "quantity": 10, "at": now.isoformat(), "destination": "+49691234", "international": True}])

    bill = say(client, f"Run the bill for customer {party}")
    assert bill["intent_type"] == "INT-84" and bill["status"] == "executed", bill
    inv = bill["result"]["invoice"]
    kinds = {line["kind"] for line in inv["lines"]}
    assert {"recurring", "usage", "tax"} <= kinds and inv["invoice_number"].startswith("INV-")
    usage_line = next(line for line in inv["lines"] if line["description"].startswith("Voice usage"))
    assert usage_line["amount"] == 25.0  # 50 minutes above the 100 included, at 0.5
    intl = next(line for line in inv["lines"] if "international" in line["description"])
    assert intl["amount"] == 20.0  # 10 min x 0.5 x 4
    again = run(client, "INT-84", party_id=party)
    assert again["status"] == "failed" and "billed up to" in again["rationale"]

    explained = say(client, f"Explain bill {inv['invoice_number']}")
    assert explained["intent_type"] == "INT-90" and explained["result"]["top_destinations"], explained

    total = inv["total"]
    leak = run(client, "INT-85", invoice=inv["invoice_number"], payment_method="credit_card", card_last4="4111 1111 1111 1111")
    assert leak["status"] == "failed"
    assert total > 50
    paid = say(client, f"Take a payment of 50 for bill {inv['invoice_number']} by card ending 4242")
    assert paid["intent_type"] == "INT-85" and paid["status"] == "executed", paid
    assert paid["result"]["payment"]["applied"][0]["amount"] == 50 and paid["result"]["confirmation"].startswith("PMT-")
    assert client.get(f"{CM}/invoices/{inv['invoice_number']}").json()["invoice"]["status"] == "partially_paid"

    small = say(client, f"Credit 20 on the international line of {inv['invoice_number']} - customer did not make these calls")
    assert small["intent_type"] == "INT-86" and small["result"]["adjustment"]["status"] == "posted", small
    agent = _user(client, "billing.agent@example.com")
    big = run(client, "INT-86", user_id=agent, invoice=inv["invoice_number"], amount=max(30.0, round(total - 70, 2)), reason="goodwill")
    assert big["result"]["adjustment"]["status"] == "pending_approval", big
    adj = big["result"]["adjustment"]["request_number"]
    refused = run(client, "INT-86", user_id=agent, adjustment=adj, decision="approve")
    assert refused["status"] == "failed" and "supervisor" in refused["rationale"]
    approved = say(client, f"approve adjustment {adj}")
    assert approved["intent_type"] == "INT-86" and approved["result"]["adjustment"]["status"] == "posted", approved
    decided = say(client, f"customer rejected adjustment {adj}")
    assert decided["intent_type"] == "INT-88" and decided["result"]["adjustment"]["customer_decision"] == "rejected"
    assert client.get(f"{CM}/invoices/{inv['invoice_number']}").json()["invoice"]["status"] == "paid"

    dup = say(client, f"Send a duplicate of {inv['invoice_number']} by email")
    assert dup["intent_type"] == "INT-89" and dup["result"]["sent_to"] == "bp@example.com", dup

    # a second bill to arrange
    _usage(client, party, [{"event_type": "voice", "quantity": 400, "at": now.isoformat(), "destination": "0550000001"}])
    inv2 = run(client, "INT-84", party_id=party, period_end=(dt.date.today() + dt.timedelta(days=40)).isoformat())["result"]["invoice"]
    plan = say(client, f"Payment arrangement for customer {party}: 9 instalments")
    assert plan["intent_type"] == "INT-87" and plan["result"]["arrangement"]["status"] == "countered", plan
    assert plan["result"]["arrangement"]["installments"] == 6
    number = plan["result"]["arrangement"]["number"]
    accepted = say(client, f"The customer accepted arrangement {number}")
    assert accepted["intent_type"] == "INT-88" and accepted["result"]["arrangement"]["status"] == "active", accepted
    pay = run(client, "INT-85", invoice=inv2["invoice_number"], amount=plan["result"]["arrangement"]["installment_amount"], payment_method="cash")
    assert pay["result"]["arrangement_installments_paid"] == 1
    portal = client.get(f"{CM}/accounts/{party}/billing").json()
    assert portal["arrangements"][0]["schedule"][0]["paid"] > 0 and portal["unbilled"]["period_start"]


def test_collections_ladder_late_fee_and_exit(client):
    party, _ = _live_customer(client, "Late Payer", "late@example.com")
    inv = run(client, "INT-84", party_id=party)["result"]["invoice"]
    _backdate(client, inv["id"], 20)
    run_ = say(client, "Run collections")
    assert run_["intent_type"] == "INT-91" and run_["status"] == "executed", run_
    assert run_["result"]["opened"] and run_["result"]["late_fees"][0]["fee"] >= 25
    step = next(x for x in run_["result"]["escalated"] if x["party_id"] == party)
    assert step["action"] == "courtesy_dunning_letter"  # 20 days overdue
    alert = run_["result"]["opened"][0]
    assert client.get(f"{CM}/accounts/{party}/billing").json()["billing_profiles"][0]["status"] == "yellow"

    early = run(client, "INT-92", credit_alert=alert, status="closed")
    assert early["status"] == "failed" and "overdue" in early["rationale"]
    note = say(client, f"Credit alert {alert}: courtesy phone call done, customer will pay Friday")
    assert note["intent_type"] == "INT-92" and note["result"]["alert"]["action_type"] == "courtesy_phone_call", note

    paid = run(client, "INT-85", party_id=party, payment_method="cash")
    assert paid["status"] == "executed" and alert in paid["result"]["collections_closed"], paid
    portal = client.get(f"{CM}/accounts/{party}/billing").json()
    assert portal["billing_profiles"][0]["status"] == "green" and portal["credit_alerts"][0]["sub_status"] == "exited_collections"


def test_fraud_thresholds_simultaneous_calls_and_follow_up(client):
    party, order = _live_customer(client, "Fraud Target", "ft@example.com")
    day = (dt.datetime.utcnow() - dt.timedelta(days=1)).replace(hour=10, minute=0, second=0, microsecond=0)
    _usage(client, party, [
        {"event_type": "voice", "quantity": 30, "at": day.isoformat(), "service": "0550001111", "origin_location": "Riyadh", "destination": "x"},
        {"event_type": "voice", "quantity": 5, "at": (day + dt.timedelta(minutes=5)).isoformat(), "service": "0550001111", "origin_location": "Jeddah"},
        *[{"event_type": "voice", "quantity": 60, "at": (day + dt.timedelta(hours=1, minutes=i)).isoformat(), "service": "0550001111",
           "international": True, "destination": "+4400000"} for i in range(4)],
    ])
    scan = say(client, "Run a fraud check")
    assert scan["intent_type"] == "INT-93" and scan["status"] == "executed", scan
    events = {a["fraud_event"]: a for a in scan["result"]["raised"]}
    assert events["simultaneous_calls"]["level"] == "certain" and "international" not in events  # 480 is under 500

    lower = say(client, f"Raise the international fraud threshold for customer {party} to 400")
    assert lower["intent_type"] == "INT-76" and lower["result"]["profile"]["international_threshold"] == 400, lower
    rescan = run(client, "INT-93", party_id=party, scan=True, day=day.date().isoformat())
    intl = next(a for a in rescan["result"]["raised"] if a["fraud_event"] == "international")
    cleared = run(client, "INT-76", party_id=party, international_threshold=1000)
    assert intl["alert_number"] in cleared["result"]["alerts_cleared"]

    alert = events["simultaneous_calls"]["alert_number"]
    for _ in range(2):
        assert not say(client, f"{alert} no answer")["result"].get("disconnect")
    third = say(client, f"{alert} no answer")
    assert third["intent_type"] == "INT-94" and third["result"]["disconnect"] and third["result"]["services"][0]["order_id"] == order

    stolen = say(client, f"report a stolen phone for customer {party}")
    assert stolen["intent_type"] == "INT-93" and stolen["result"]["alert"]["fraud_type"] == "stolen_handset", stolen
    confirm = run(client, "INT-94", fraud_alert=stolen["result"]["alert"]["alert_number"], fraud_outcome="confirm")
    assert confirm["result"]["alert"]["status"] == "confirmed"
    assert client.get(f"{CM}/accounts/{party}").json()["account"]["good_standing"] is False


def test_tickets_parent_child_resolve_survey_fallout_and_solutions(client):
    assert client.post("/api/admin/comms/setup/sample").status_code == 200  # solutions and providers
    party, order = _live_customer(client, "Ticket Person")
    ids = []
    for _ in range(3):
        body = run(client, "INT-10", company_name="Ticket Person", description="no dial tone at the office, exchange RYD-04 area")
        assert body["status"] == "executed", body
        ids.append(body["result"]["ticket_id"])
    parent, kids = ids[0], ids[1:]
    linked = say(client, f"Link tickets {kids[0]} and {kids[1]} to parent ticket {parent}")
    assert linked["intent_type"] == "INT-95" and set(linked["result"]["linked"]) == set(kids), linked

    found = say(client, f"Find a solution for ticket {parent}")
    assert found["intent_type"] == "INT-99" and found["result"]["solutions"][0]["title"] == "No dial tone on a fixed line", found
    resolved = say(client, f"Resolve ticket {parent} - cable repaired and tested")
    assert resolved["intent_type"] == "INT-96" and resolved["result"]["status"] == "resolved", resolved
    assert set(resolved["result"]["children"]) == set(kids) and not resolved["result"]["children_closed"]
    child = client.get(f"{CM}/tickets/{kids[0]}").json()
    assert child["sub_status"] == "pending_verification" and child["parent_ticket_id"] == parent
    closed_again = run(client, "INT-96", ticket_id=parent, resolution="x")
    assert closed_again["status"] == "failed"
    reopened = say(client, f"reopen ticket {parent}")
    assert reopened["result"]["status"] == "open"

    survey = say(client, f"Satisfaction survey for ticket {kids[0]}: overall 2, timeliness 1, would recommend 3")
    assert survey["intent_type"] == "INT-97" and survey["result"]["follow_up"], survey
    analysis = client.get(f"{CM}/tickets/analysis").json()
    assert analysis["total"] >= 3 and analysis["satisfaction"] is not None

    fallout = say(client, f"Order {order} failed in provisioning - error PORT_UNAVAILABLE")
    assert fallout["intent_type"] == "INT-98" and fallout["result"]["global_error_code"] == "RES-PORT", fallout
    fixed = run(client, "INT-96", ticket_id=fallout["result"]["ticket_id"], resolution="Port reserved and resubmitted")
    assert fixed["status"] == "executed"
    view = client.get(f"{CM}/tickets/{fallout['result']['ticket_id']}").json()
    assert view["origin"] == "fallout" and view["fallout"]["recovery"].startswith("Reserve another network port")


def test_assets_opportunities_agreements_and_providers(client):
    assert client.post("/api/admin/comms/setup/sample").status_code == 200
    assert client.post("/api/field-service/setup/sample", json={"timezone": "UTC"}).status_code == 200
    party, order = _live_customer(client, "Asset Person")
    asset = client.post("/api/field-service/assets", json={"party_id": party, "product_name": "Router CPE", "serial_number": "R-1"})
    assert asset.status_code == 200, asset.text
    asset_id = asset.json()["id"] if "id" in asset.json() else asset.json()["asset_id"]
    installed = say(client, f"install SFP module 1G on asset {asset_id}")
    assert installed["intent_type"] == "INT-80" and installed["result"]["asset"]["components"][0]["product"] == "SFP module 1G", installed
    replaced = say(client, f"Replace the SFP module 1G on asset {asset_id}")
    assert replaced["status"] == "executed" and len(replaced["result"]["asset"]["components"]) == 2
    cr = say(client, f"Change request on asset {asset_id}: customer wants a static IP on the backup line")
    assert cr["intent_type"] == "INT-81" and cr["result"]["change_request"]["number"].startswith("CR-"), cr

    opp = _opportunity(client, "Opportunity Co")
    site = say(client, f"Add customer {party} as a related site on opportunity {opp}")
    assert site["intent_type"] == "INT-82" and site["result"]["links"][0]["kind"] == "related_site", site
    not_partner = run(client, "INT-82", opportunity_id=opp, related_party_id=party, link_kind="partner")
    assert not_partner["status"] == "failed" and "partner" in not_partner["rationale"]
    prof = say(client, f"Profile opportunity {opp}: budget yes, authority yes, need yes, timeline no")
    assert prof["intent_type"] == "INT-83" and prof["result"]["score"] == 70 and prof["result"]["target_market"], prof

    # agreement from a quote (OM), then revise / sub-agreement / document
    opp2 = _opportunity(client, "Agreement Co")
    q = run(client, "INT-05", opportunity_id=opp2, offering_name="Fiber 900")["result"]["quote_id"]
    agr = run(client, "INT-65", quote_id=q, term_months=12, response_hours=8)["result"]["agreement_number"]
    rev = say(client, f"Revise agreement {agr}: add term Early exit: 3 months' fees; term 24 months; response 4 hours")
    assert rev["intent_type"] == "INT-100" and rev["result"]["revision"] == 2 and rev["result"]["name"].endswith("(.2)"), rev
    assert rev["result"]["agreement"]["entitlement"]["response_hours"] == 4
    sub = run(client, "INT-100", agreement_id=agr, sub_agreement="Branch SLA")
    assert sub["result"]["parent_agreement_id"] == rev["result"]["agreement_id"]
    doc = say(client, f"Generate the agreement document for {agr}")
    assert doc["intent_type"] == "INT-101" and "Early exit: 3 months' fees" in doc["result"]["document"], doc

    alloc = say(client, f"Allocate order {order} to providers")
    assert alloc["intent_type"] == "INT-102" and alloc["status"] == "executed", alloc
    wo = alloc["result"]["work_orders"][0]
    assert wo["provider"] == "Metro Fiber ISP" and wo["number"].startswith("PWO-")
    twice = run(client, "INT-102", order_id=order)
    assert twice["status"] == "failed" and "already" in twice["rationale"]
    failed = client.put(f"{CM}/provider-work-orders/{wo['id']}", json={"status": "failed", "notes": "No capacity at the exchange"}).json()
    assert failed["status"] == "failed" and failed["fallout"]["ticket_id"]


def test_communications_catalog_and_sample(client):
    catalog = client.get("/api/intents/catalog").json()
    codes = {i["code"] for i in catalog["intents"]}
    assert {f"INT-{n}" for n in range(72, 103)} <= codes
    _live_customer(client, "Sample Person")
    out = client.post("/api/admin/comms/setup/sample").json()
    assert out["usage_events"] > 0 and any(c.startswith("bill INV-") for c in out["created"])
    assert out["collections"]["opened"] and out["fraud_alerts"]
    overview = client.get(f"{CM}/overview").json()
    assert overview["credit_alerts_open"] >= 1 and overview["fraud_alerts_open"] >= 1
    settings = client.put("/api/admin/comms/settings", json={"grace_days": 3}).json()
    assert settings["grace_days"] == 3 and settings["collections_ladder"]
    assert client.put("/api/admin/comms/settings", json={"nonsense": 1}).status_code == 400

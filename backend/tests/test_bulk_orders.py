"""Bulk ordering (orchestration/bulk_orders.py, INT-149 / INT-150): many orders in one request, each row
run through the same single-transaction process - customer, order, approval, fulfilment (COM / SOM /
TOM with field work), installed assets, billing start - one row's failure never stops the others."""

from tests.test_fulfillment_orchestration import close_next_field_job, standard_pattern
from tests.test_b2c_direct_order import _setup

from app.models.intent_log import IntentExecutionLog as IntentExecution
from app.models.mobile import MbLine


def run(client, code, **entities):
    return client.post("/api/intents", json={"text": "", "structured": {"intent_type": code, "entities": entities}}).json()


def create(client, **body):
    out = client.post("/api/bulk-orders", json=body)
    assert out.status_code == 200, out.text
    return out.json()


def process(client, bulk_id, limit=25):
    out = client.post(f"/api/bulk-orders/{bulk_id}/process", params={"limit": limit})
    assert out.status_code == 200, out.text
    return out.json()


def rows(client, bulk_id):
    return {r["row_no"]: r for r in client.get(f"/api/bulk-orders/{bulk_id}").json()["row_items"]}


CSV = """customer_name,email,products,installation_address
Asha Rao,asha@example.com,Home Fiber 100,4 Park Street Kolkata
Ben Thomas,ben@example.com,Home Fiber 100,9 MG Road Bengaluru
Chen Li,chen@example.com,,1 Lake View Pune
Dev Patel,dev@example.com,Nonexistent Plan,2 Hill Road Mumbai
"""


def test_b2c_new_connects_run_the_single_order_process_to_fulfilment_assets_and_billing(client):
    _setup(client)
    standard_pattern(client, products="Home Fiber 100")
    templates = {p["process"]: p for p in client.get("/api/bulk-orders/processes").json()}
    assert templates["b2c_new_connect"]["intents"] == ["INT-41", "INT-104", "INT-25", "INT-117"]
    assert "products" in templates["b2c_new_connect"]["csv_header"]

    bulk = create(client, process="b2c_new_connect", name="Campus connects", csv=CSV, defaults={"order_channel": "store"})
    assert bulk["status"] == "running" and bulk["number"].startswith("BULK-") and bulk["rows"] == 4
    r = rows(client, bulk["id"])
    assert r[3]["stage"] == "invalid" and "products" in r[3]["message"]  # caught before anything ran

    step = process(client, bulk["id"])
    r = rows(client, bulk["id"])
    # rows 1 and 2: customer added, order placed and booked, approved by Order Control -> the pattern runs, TOM waits for the engineer
    for n in (1, 2):
        assert r[n]["stage"] == "in_fulfillment", r[n]
        assert [x["intent"] for x in r[n]["runs"]] == ["INT-41", "INT-104", "INT-25"] and all(x["status"] == "executed" for x in r[n]["runs"])
        assert r[n]["party_id"] and len(r[n]["order_ids"]) == 1 and "work order" in r[n]["message"].lower()
    # row 4: the B2C order refused the product - the row stops with the reason, the others went ahead
    assert r[4]["stage"] == "failed" and r[4]["message"].startswith("Place a B2C order: Add at least one service from the catalog")
    assert step["counts"] == {"in_fulfillment": 2, "invalid": 1, "failed": 1}

    # every step was an intent of its own in the audit trail, run as the submitter
    db = client.TestSessionLocal()
    try:
        audited = {e.intent_type for e in db.query(IntentExecution).filter(IntentExecution.raw_text.like(f"{bulk['number']} row %"))}
        assert {"INT-41", "INT-104", "INT-25"} <= audited
    finally:
        db.close()

    # the engineers do the field work (installation, connectivity test, acceptance) - the rows follow
    for n in (1, 2):
        for _ in range(3):
            close_next_field_job(client, r[n]["order_ids"][0])
    done = process(client, bulk["id"])
    r = rows(client, bulk["id"])
    for n in (1, 2):
        assert r[n]["stage"] == "completed", r[n]
        assert r[n]["asset_ids"], r[n]  # the service is installed
        billing = r[n]["billing"][str(r[n]["order_ids"][0])]
        assert billing.get("billing_start_ref") or billing.get("billing_account_status") == "active"
    assert done["status"] == "completed_with_errors" and done["completed"] == 2 and done["failed"] == 2

    csv = client.get(f"/api/bulk-orders/{bulk['id']}/results.csv").text
    assert csv.splitlines()[0].endswith("stage,party_id,order_ids,asset_ids,billing,message") and csv.count("completed") == 2

    # the failed row, once fixed at the source, can go again
    assert client.post(f"/api/bulk-orders/{bulk['id']}/retry").json()["requeued"] == 1


def test_orders_wait_for_their_approver_when_the_request_does_not_approve(client):
    _setup(client)
    standard_pattern(client, products="Home Fiber 100")
    bulk = create(client, process="b2c_new_connect", auto_approve=False, rows=[
        {"customer_name": "Uma Iyer", "products": "Home Fiber 100", "installation_address": "3 Beach Road Chennai"}])
    process(client, bulk["id"])
    row = rows(client, bulk["id"])[1]
    assert row["stage"] == "ready_for_approval" and [x["intent"] for x in row["runs"]] == ["INT-41", "INT-104"]
    assert run(client, "INT-25", order_id=row["order_ids"][0], decision="approve")["status"] == "executed"  # Order Control decides
    process(client, bulk["id"])
    assert rows(client, bulk["id"])[1]["stage"] == "in_fulfillment"


def test_mobile_lines_and_asset_changes_in_bulk(client):
    assert client.post("/api/mobile/sample-catalog").status_code == 200
    party = run(client, "INT-41", name="Fleet Owner")["result"]["party_id"]
    bulk = create(client, process="mobile_lines", rows=[
        {"party_id": party, "offering_name": "Postpaid 399", "quantity": 2, "sim_type": "esim", "user_name": "Ann, Bob"},
        {"customer_name": "New Mobile Customer", "offering_name": "Prepaid Smart 28", "initial_recharge": 299, "sim_type": "esim"},
        {"party_id": party, "offering_name": "Data 10 GB Booster"},
    ])
    out = process(client, bulk["id"])
    r = rows(client, bulk["id"])
    assert r[1]["stage"] == "completed" and len(r[1]["order_ids"]) == 2 and len(r[1]["asset_ids"]) == 2
    assert r[2]["stage"] == "completed" and [x["intent"] for x in r[2]["runs"]] == ["INT-41", "INT-136"]
    assert r[3]["stage"] == "failed" and "not a mobile plan" in r[3]["message"]
    assert out["status"] == "completed_with_errors"

    # the installed lines change in bulk: each a change order on its asset, fulfilled, the line follows
    change = create(client, process="asset_change", rows=[{"asset_id": a, "asset_operation": "suspend"} for a in r[1]["asset_ids"]])
    process(client, change["id"])
    c = rows(client, change["id"])
    assert all(x["stage"] == "completed" for x in c.values()), c
    assert [x["intent"] for x in c[1]["runs"]] == ["INT-107", "INT-108"]
    db = client.TestSessionLocal()
    try:
        assert {l.status for l in db.query(MbLine).filter(MbLine.asset_id.in_(r[1]["asset_ids"]))} == {"suspended"}
    finally:
        db.close()


def test_start_and_follow_a_bulk_order_in_plain_english(client):
    assert client.post("/api/mobile/sample-catalog").status_code == 200
    started = client.post("/api/intents", json={"text": "Start a bulk order of mobile lines\ncustomer_name,offering_name,sim_type\n"
                                                        "Kiran Bulk,Prepaid Pay-as-you-go,esim\nLeela Bulk,Prepaid Pay-as-you-go,esim"}).json()
    assert started["intent_type"] == "INT-149" and started["status"] == "executed", started
    number = started["result"]["number"]
    assert started["result"]["rows"] == 2 and started["result"]["status"] == "running"
    process(client, started["result"]["bulk_order_id"])
    status = client.post("/api/intents", json={"text": f"Status of {number}"}).json()
    assert status["intent_type"] == "INT-150" and status["result"]["completed"] == 2, status
    assert any(b["number"] == number for b in client.get("/api/bulk-orders").json())

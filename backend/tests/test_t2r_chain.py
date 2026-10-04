def test_full_t2r_chain_complaint_diagnose_dispatch_complete(client):
    ticket_resp = client.post(
        "/api/intents",
        json={
            "text": "Log this complaint. description: Fiber internet is completely down since this morning, "
            "company_name: Acme Telecom, sla_class: premium"
        },
    )
    ticket_body = ticket_resp.json()
    assert ticket_body["intent_type"] == "INT-10"
    assert ticket_body["status"] == "executed"
    ticket_id = ticket_body["result"]["ticket_id"]
    assert ticket_body["result"]["priority"] == "P1"  # premium + critical (keyword "down")

    diagnose_resp = client.post("/api/intents", json={"text": f"Diagnose the root cause. ticket_id: {ticket_id}"})
    diagnose_body = diagnose_resp.json()
    assert diagnose_body["intent_type"] == "INT-11"
    assert diagnose_body["status"] == "executed"
    assert diagnose_body["result"]["field_dispatch_required"] is True  # severity critical -> field dispatch
    assert diagnose_body["result"]["escalated_to_human"] is True  # confidence 0.6 < 0.7 threshold

    dispatch_resp = client.post(
        "/api/intents",
        json={"text": f"Dispatch a technician. ticket_id: {ticket_id}, required_skill: fiber_install, region: North"},
    )
    dispatch_body = dispatch_resp.json()
    assert dispatch_body["intent_type"] == "INT-12"
    assert dispatch_body["status"] == "executed"
    assert dispatch_body["result"]["status"] == "en_route"
    work_order_id = dispatch_body["result"]["work_order_id"]

    complete_resp = client.post(
        "/api/intents",
        json={"text": f"Complete the work order. work_order_id: {work_order_id}"},
    )
    complete_body = complete_resp.json()
    assert complete_body["intent_type"] == "INT-12"
    assert complete_body["status"] == "executed"
    assert complete_body["result"]["status"] == "complete"


def test_dispatch_with_no_matching_technician_fails(client):
    ticket_id = client.post(
        "/api/intents",
        json={"text": "Log this complaint. description: billing question about my invoice"},
    ).json()["result"]["ticket_id"]

    resp = client.post(
        "/api/intents",
        json={"text": f"Dispatch a technician. ticket_id: {ticket_id}, required_skill: satellite_repair"},
    )
    assert resp.json()["status"] == "failed"


def test_diagnose_unknown_ticket_needs_review(client):
    resp = client.post("/api/intents", json={"text": "Diagnose the root cause. ticket_id: 99999"})
    assert resp.json()["status"] == "needs_review"

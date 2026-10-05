import time


def _active_order(client, offering_name="Fiber 500 Bundle", price=1000, company="Acme Telecom"):
    client.post(
        "/api/intents",
        json={
            "text": f"Launch a new bundle. name: {offering_name}, composition: Fiber500, "
            f"segment: B2B-SMB, channel: B2B, price: {price}, discount: 0"
        },
    )
    opp_id = client.post(
        "/api/intents",
        json={"text": f"Qualify this lead. company_name: {company}, source: referral, region: North"},
    ).json()["result"]["opportunity_id"]
    quote_id = client.post(
        "/api/intents",
        json={"text": f"Configure and quote this bundle. opportunity_id: {opp_id}, offering_name: {offering_name}"},
    ).json()["result"]["quote_id"]
    client.post("/api/intents", json={"text": f"Approve this quote. quote_id: {quote_id}"})
    return client.post(
        "/api/intents", json={"text": f"Activate this order end-to-end. quote_id: {quote_id}"}
    ).json()["result"]["order_id"]


def _add_policy(client, clock_type, duration_hours, on_breach_action="auto_cancel"):
    resp = client.post(
        "/api/admin/sla-policies",
        json={"clock_type": clock_type, "duration_hours": duration_hours, "on_breach_action": on_breach_action},
    )
    assert resp.status_code == 201


def test_cancel_without_policy_is_still_immediate(client):
    order_id = _active_order(client, company="RetentionNoPolicyCo")
    resp = client.post("/api/intents", json={"text": f"Cancel this service. order_id: {order_id}"})
    body = resp.json()
    assert body["result"]["status"] == "cancelled"
    assert "early_termination_fee" in body["result"]


def test_cancel_with_policy_opens_retention_window(client):
    _add_policy(client, "retention_window", 24.0)
    order_id = _active_order(client, company="RetentionWindowCo")

    resp = client.post("/api/intents", json={"text": f"Cancel this service. order_id: {order_id}"})
    body = resp.json()
    assert body["result"]["status"] == "pending_cancellation"
    assert "retention_window_deadline" in body["result"]

    order = client.get(f"/api/orders/{order_id}").json()
    assert order["status"] == "pending_cancellation"
    # Not deprovisioned yet - resources still active.
    assert order["service_orders"][0]["status"] == "active"


def test_retain_decision_restores_order_to_active(client):
    _add_policy(client, "retention_window", 24.0)
    order_id = _active_order(client, company="RetainMeCo")
    client.post("/api/intents", json={"text": f"Cancel this service. order_id: {order_id}"})

    resp = client.post(
        "/api/intents", json={"text": f"Cancel this service. order_id: {order_id}, decision: retain"}
    )
    body = resp.json()
    assert body["result"]["status"] == "active"
    assert body["result"]["retained"] is True

    order = client.get(f"/api/orders/{order_id}").json()
    assert order["status"] == "active"

    # The clock was completed, not left running - a sweep should find nothing due.
    sweep = client.post("/api/admin/sla/process-due").json()
    assert sweep["checked"] == 0


def test_retention_window_lapse_cancels_order(client):
    _add_policy(client, "retention_window", 0.0001)
    order_id = _active_order(client, company="LapsedRetentionCo")
    client.post("/api/intents", json={"text": f"Cancel this service. order_id: {order_id}"})

    time.sleep(0.5)
    sweep = client.post("/api/admin/sla/process-due").json()
    assert sweep["checked"] == 1
    assert sweep["breached"][0]["outcome"] == "cancelled"

    order = client.get(f"/api/orders/{order_id}").json()
    assert order["status"] == "cancelled"
    assert order["service_orders"][0]["status"] == "released"

    audit = client.get("/api/audit").json()
    assert audit[0]["intent_type"] == "SYS-SLA"


def test_suspend_starts_grace_clock_and_escalates_to_terminated(client):
    _add_policy(client, "suspension_grace", 0.0001)
    order_id = _active_order(client, company="SuspendGraceCo")

    resp = client.post("/api/intents", json={"text": f"Suspend this service. order_id: {order_id}"})
    body = resp.json()
    assert body["result"]["status"] == "suspended"
    assert "suspension_grace_deadline" in body["result"]

    time.sleep(0.5)
    sweep = client.post("/api/admin/sla/process-due").json()
    assert sweep["checked"] == 1
    assert sweep["breached"][0]["outcome"] == "terminated"

    order = client.get(f"/api/orders/{order_id}").json()
    assert order["status"] == "terminated"


def test_repeated_suspend_does_not_start_duplicate_clocks(client):
    _add_policy(client, "suspension_grace", 24.0)
    order_id = _active_order(client, company="DoubleSuspendCo")
    client.post("/api/intents", json={"text": f"Suspend this service. order_id: {order_id}"})
    client.post("/api/intents", json={"text": f"Suspend this service. order_id: {order_id}"})

    clocks = client.get("/api/admin/sla-clocks?status=running").json()
    matching = [c for c in clocks if c["clock_type"] == "suspension_grace" and c["entity_id"] == order_id]
    assert len(matching) == 1

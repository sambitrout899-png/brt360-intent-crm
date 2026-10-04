def _launch(client, name, price=999, composition="Fiber500"):
    return client.post(
        "/api/intents",
        json={
            "text": f"Launch a new bundle. name: {name}, composition: {composition}, "
            f"segment: B2C, channel: B2C, price: {price}, discount: 0"
        },
    )


def test_retire_without_replacement_is_rejected(client):
    _launch(client, "Fiber 100 Bundle")

    resp = client.post(
        "/api/intents",
        json={"text": "Retire this bundle. offering_name: Fiber 100 Bundle"},
    )
    body = resp.json()
    # No migration path -> reasoning routes to human review before orchestration
    # ever runs (HLD hard rule: "no retirement is permitted without a migration path").
    assert body["status"] == "needs_review"

    catalog = client.get("/api/catalog").json()
    assert next(o for o in catalog if o["name"] == "Fiber 100 Bundle")["status"] == "active"


def test_retire_with_replacement_succeeds_and_retains_history(client):
    _launch(client, "Fiber 100 Bundle")
    _launch(client, "Fiber 500 Bundle", price=999)

    resp = client.post(
        "/api/intents",
        json={
            "text": "Retire this bundle. offering_name: Fiber 100 Bundle, "
            "replacement_offering_name: Fiber 500 Bundle"
        },
    )
    body = resp.json()
    assert body["intent_type"] == "INT-03"
    assert body["status"] == "executed"
    assert body["result"]["status"] == "retired"
    assert "offering.retired" in body["events"]

    catalog = {o["name"]: o for o in client.get("/api/catalog").json()}
    retired = catalog["Fiber 100 Bundle"]
    assert retired["status"] == "retired"
    assert retired["replacement_offering_id"] == catalog["Fiber 500 Bundle"]["id"]
    assert retired["end_date"] is not None

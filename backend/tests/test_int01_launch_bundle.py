def test_launch_bundle_executes_and_publishes(client):
    resp = client.post(
        "/api/intents",
        json={
            "text": "Launch a new bundle. name: Fiber 500 Bundle, composition: Fiber500+VOIP, "
            "segment: B2C, channel: B2C, price: 999, discount: 10"
        },
    )
    assert resp.status_code == 200
    body = resp.json()

    assert body["intent_type"] == "INT-01"
    assert body["status"] == "executed"
    assert body["entities"]["composition"] == ["Fiber500", "VOIP"]
    assert body["result"]["status"] == "active"
    assert body["result"]["version"] == 1
    assert "offering.published" in body["events"]
    assert len(body["plan"]) == 5
    assert all(step["status"] == "ok" for step in body["plan"])

    catalog = client.get("/api/catalog").json()
    assert any(o["name"] == "Fiber 500 Bundle" and o["status"] == "active" for o in catalog)

    audit = client.get("/api/audit").json()
    assert audit[0]["intent_type"] == "INT-01"
    assert audit[0]["status"] == "executed"


def test_launch_bundle_rejects_incompatible_composition(client):
    resp = client.post(
        "/api/intents",
        json={
            "text": "Launch a new bundle. name: Bad Bundle, composition: MobileLine+CloudBackup, "
            "segment: B2C, channel: B2C, price: 349, discount: 0"
        },
    )
    body = resp.json()
    assert body["status"] == "failed"
    assert "not compatible" in body["rationale"]

    catalog = client.get("/api/catalog").json()
    assert not any(o["name"] == "Bad Bundle" for o in catalog)


def test_launch_bundle_low_confidence_needs_review(client):
    resp = client.post("/api/intents", json={"text": "launch something vague"})
    body = resp.json()
    assert body["status"] == "needs_review"
    assert body["plan"] == []
    assert body["result"] is None

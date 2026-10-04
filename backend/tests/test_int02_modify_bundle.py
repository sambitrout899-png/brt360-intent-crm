def _launch(client, name="Fiber 500 Bundle", price=999):
    return client.post(
        "/api/intents",
        json={
            "text": f"Launch a new bundle. name: {name}, composition: Fiber500, "
            f"segment: B2C, channel: B2C, price: {price}, discount: 0"
        },
    )


def test_modify_creates_new_version_and_supersedes_old(client):
    _launch(client)

    resp = client.post(
        "/api/intents",
        json={"text": "Modify this bundle. offering_name: Fiber 500 Bundle, new_price: 849"},
    )
    body = resp.json()
    assert body["intent_type"] == "INT-02"
    assert body["status"] == "executed"
    assert body["result"]["version"] == 2
    assert "offering.updated" in body["events"]

    catalog = {o["id"]: o for o in client.get("/api/catalog").json()}
    versions = [o for o in catalog.values() if o["name"] == "Fiber 500 Bundle"]
    assert len(versions) == 2
    v1 = next(o for o in versions if o["version"] == 1)
    v2 = next(o for o in versions if o["version"] == 2)
    assert v1["status"] == "superseded"
    assert v2["status"] == "active"
    assert v2["price"] == 849
    assert v2["parent_offering_id"] == v1["id"]


def test_modify_unknown_bundle_needs_review(client):
    resp = client.post(
        "/api/intents",
        json={"text": "Modify this bundle. offering_name: Does Not Exist, new_price: 100"},
    )
    body = resp.json()
    assert body["status"] == "needs_review"

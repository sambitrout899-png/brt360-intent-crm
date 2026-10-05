def _spec_id(client, name):
    specs = client.get("/api/admin/product-specifications").json()
    return next(s["id"] for s in specs if s["name"] == name)


def _launch_bundle(client, name, composition, price=1000):
    return client.post(
        "/api/intents",
        json={
            "text": f"Launch a new bundle. name: {name}, composition: {composition}, "
            f"segment: B2B, channel: B2B, price: {price}, discount: 0"
        },
    )


def test_create_attribute_definition_and_set_value(client):
    defn_resp = client.post(
        "/api/admin/attribute-definitions", json={"name": "Download Speed", "data_type": "number", "unit": "Mbps"}
    )
    assert defn_resp.status_code == 201
    defn_id = defn_resp.json()["id"]

    fiber1000_id = _spec_id(client, "Fiber1000")
    set_resp = client.put(
        "/api/admin/attribute-values",
        json={"product_specification_id": fiber1000_id, "attribute_definition_id": defn_id, "value": "1000"},
    )
    assert set_resp.status_code == 200
    assert set_resp.json()["attribute_name"] == "Download Speed"

    values = client.get(f"/api/admin/attribute-values?product_specification_id={fiber1000_id}").json()
    assert values[0]["value"] == "1000"


def test_requires_constraint_blocks_bundle_missing_required_addon(client):
    defn_id = client.post("/api/admin/attribute-definitions", json={"name": "Download Speed", "data_type": "number"}).json()["id"]
    fiber1000_id = _spec_id(client, "Fiber1000")
    staticip_id = _spec_id(client, "StaticIP")

    client.put(
        "/api/admin/attribute-values",
        json={"product_specification_id": fiber1000_id, "attribute_definition_id": defn_id, "value": "1000"},
    )
    rule_resp = client.post(
        "/api/admin/attribute-constraint-rules",
        json={
            "if_product_spec_id": fiber1000_id,
            "if_attribute_id": defn_id,
            "operator": "gte",
            "threshold": "500",
            "rule_type": "requires",
            "then_product_spec_id": staticip_id,
        },
    )
    assert rule_resp.status_code == 201
    assert rule_resp.json()["if_product_name"] == "Fiber1000"
    assert rule_resp.json()["then_product_name"] == "StaticIP"

    missing_resp = _launch_bundle(client, "Fiber Gig No Static", "Fiber1000+VOIP")
    body = missing_resp.json()
    assert body["status"] == "failed"
    assert "requires" in body["rationale"]
    assert "StaticIP" in body["rationale"]

    ok_resp = _launch_bundle(client, "Fiber Gig With Static", "Fiber1000+VOIP+StaticIP")
    assert ok_resp.json()["status"] == "executed"


def test_excludes_constraint_blocks_bundle_with_excluded_addon(client):
    defn_id = client.post("/api/admin/attribute-definitions", json={"name": "Contention Ratio", "data_type": "string"}).json()["id"]
    fiber1000_id = _spec_id(client, "Fiber1000")
    cloudbackup_id = _spec_id(client, "CloudBackup")

    client.put(
        "/api/admin/attribute-values",
        json={"product_specification_id": fiber1000_id, "attribute_definition_id": defn_id, "value": "1:1"},
    )
    client.post(
        "/api/admin/attribute-constraint-rules",
        json={
            "if_product_spec_id": fiber1000_id,
            "if_attribute_id": defn_id,
            "operator": "eq",
            "threshold": "1:1",
            "rule_type": "excludes",
            "then_product_spec_id": cloudbackup_id,
        },
    )

    blocked_resp = _launch_bundle(client, "Fiber Dedicated Plus Backup", "Fiber1000+CloudBackup")
    body = blocked_resp.json()
    assert body["status"] == "failed"
    assert "excludes" in body["rationale"]

    ok_resp = _launch_bundle(client, "Fiber Dedicated Alone", "Fiber1000+VOIP")
    assert ok_resp.json()["status"] == "executed"


def test_deactivated_rule_no_longer_applies(client):
    defn_id = client.post("/api/admin/attribute-definitions", json={"name": "Download Speed", "data_type": "number"}).json()["id"]
    fiber1000_id = _spec_id(client, "Fiber1000")
    staticip_id = _spec_id(client, "StaticIP")
    client.put(
        "/api/admin/attribute-values",
        json={"product_specification_id": fiber1000_id, "attribute_definition_id": defn_id, "value": "1000"},
    )
    rule_id = client.post(
        "/api/admin/attribute-constraint-rules",
        json={
            "if_product_spec_id": fiber1000_id,
            "if_attribute_id": defn_id,
            "operator": "gte",
            "threshold": "500",
            "rule_type": "requires",
            "then_product_spec_id": staticip_id,
        },
    ).json()["id"]

    assert _launch_bundle(client, "Fiber Gig A", "Fiber1000+VOIP").json()["status"] == "failed"

    deactivate_resp = client.patch(f"/api/admin/attribute-constraint-rules/{rule_id}/deactivate")
    assert deactivate_resp.status_code == 204

    assert _launch_bundle(client, "Fiber Gig B", "Fiber1000+VOIP").json()["status"] == "executed"

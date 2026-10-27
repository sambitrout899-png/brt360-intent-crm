"""Admin -> Intents: bulk role assignment, and the user's actions grouped by
business process according to their roles."""

from test_sales_to_order import _user


def _as(user_id):
    return {"X-Test-User-Id": str(user_id)}


def _role(client, name):
    resp = client.post("/api/admin/roles", json={"name": name, "pages": ["customer", "intent"]})
    assert resp.status_code in (200, 201), resp.text
    return resp.json()["id"]


def _roles_of(client, code):
    return next(i for i in client.get("/api/admin/intents").json()["intents"] if i["code"] == code)["role_ids"]


def test_bulk_roles_add_remove_set_and_everyone(client):
    sales, presales = _role(client, "Bulk Sales"), _role(client, "Bulk Presales")
    codes = ["INT-15", "INT-46", "INT-17"]

    out = client.post("/api/admin/intents/bulk-roles", json={"codes": codes, "role_ids": [sales], "mode": "add"}).json()
    assert out["changed"] == codes and not out["skipped"]
    out = client.post("/api/admin/intents/bulk-roles", json={"codes": ["INT-17"], "role_ids": [presales], "mode": "add"}).json()
    assert out["changed"] == ["INT-17"] and sorted(_roles_of(client, "INT-17")) == sorted([sales, presales])

    # removing the only role would open the intent to everyone - skipped and reported
    out = client.post("/api/admin/intents/bulk-roles", json={"codes": codes, "role_ids": [sales], "mode": "remove"}).json()
    assert out["changed"] == ["INT-17"] and {s["code"] for s in out["skipped"]} == {"INT-15", "INT-46"}
    assert _roles_of(client, "INT-17") == [presales] and _roles_of(client, "INT-15") == [sales]

    client.post("/api/admin/intents/bulk-roles", json={"codes": codes, "role_ids": [presales], "mode": "set"})
    assert all(_roles_of(client, c) == [presales] for c in codes)
    client.post("/api/admin/intents/bulk-roles", json={"codes": codes, "mode": "everyone"})
    assert all(_roles_of(client, c) == [] for c in codes)

    assert client.post("/api/admin/intents/bulk-roles", json={"codes": codes, "role_ids": [], "mode": "add"}).status_code == 400
    assert client.post("/api/admin/intents/bulk-roles", json={"codes": codes, "role_ids": [999999], "mode": "add"}).status_code == 400


def test_catalog_groups_a_users_actions_by_process(client):
    rep = _role(client, "Process Rep")
    user = _user(client, "process.rep@test.local")
    assert client.put(f"/api/admin/users/{user}/access", json={"role_ids": [rep], "position_ids": []}).status_code == 200
    everything = [i["code"] for i in client.get("/api/admin/intents").json()["intents"]]
    # the rep may only add prospects and generate leads
    client.post("/api/admin/intents/bulk-roles", json={"codes": everything, "role_ids": [rep], "mode": "set"})
    others = _role(client, "Process Others")
    client.post("/api/admin/intents/bulk-roles", json={"codes": [c for c in everything if c not in ("INT-15", "INT-46")], "role_ids": [others], "mode": "set"})

    catalog = client.get("/api/intents/catalog", headers=_as(user)).json()
    assert {i["code"] for i in catalog["intents"]} == {"INT-15", "INT-46"}
    by_key = {p["key"]: p["steps"] for p in catalog["processes"]}
    assert by_key["b2b_sto"] == ["INT-15", "INT-46"]  # process order, only what the role allows
    assert all(set(steps) <= {"INT-15", "INT-46"} for steps in by_key.values())
    assert "fulfilment" not in by_key  # a process with nothing for this role is left out

    admin_view = client.get("/api/admin/intents").json()
    assert any(p["key"] == "b2b_sto" and "INT-17" in p["steps"] for p in admin_view["processes"])

"""One product, the same everywhere: catalog <-> model <-> attribute library <-> pricelists <-> intents."""

import base64

from test_configurator import _setup
from test_product_hierarchy import BBI, _run

from app.crm_core import product_model_service as model


def _hub(client, name):
    r = client.get(f"/api/catalog/products/{name}")
    assert r.status_code == 200, r.text
    return r.json()


def test_product_picture_follows_every_change(client):
    client.post("/api/intents", json={"text": "Launch a new bundle. name: Plain Fiber, composition: Fiber500, segment: B2B-SMB, channel: B2B, price: 900, discount: 0"})
    plain = _hub(client, "Plain Fiber")
    assert plain["model"]["status"] == "not_modelled" and plain["launch_eligibility"][0]["segment"] == "B2B-SMB"
    assert {p["price_list"] for p in plain["prices"]} >= {None, "Standard", "Enterprise", "Strategic"}

    _setup(client)
    assert _hub(client, BBI)["model"]["status"] == "workspace"
    _run(client, "INT-48", offering_name=BBI)
    assert _hub(client, BBI)["model"]["status"] == "released"
    client.post(f"/api/admin/product-model/{BBI}/rules", json={"rule_type": "constraint", "text": "if Access Medium is Fiber then CPE is required"})
    hub = _hub(client, BBI)
    assert hub["model"]["status"] == "released_changed" and hub["model"]["rules"]["constraint"] == 4 and hub["tree"]["children"]
    assert any(p["name"] == BBI for p in client.get("/api/catalog/products").json())
    assert any(d["offering_name"] == BBI for d in client.get("/api/admin/product-config").json())  # drafts listed there too

    # the launch's segment becomes an enforced eligibility rule
    rule = client.post("/api/admin/product-model/Plain Fiber/rules/from-launch").json()
    assert rule["rule_type"] == "eligibility" and rule["definition"]["when"][0] == {"field": "segment", "op": "in", "value": ["B2B"]}
    consumer = _run(client, "INT-15", name="Home Person", segment="B2C")["result"]["party_id"]
    assert "Plain Fiber" in client.get("/api/catalog/eligibility", params={"party_id": consumer, "names": "Plain Fiber"}).json()["not_eligible"]


def test_attribute_library_knows_where_each_attribute_is_used(client):
    _setup(client)
    usage = {u["name"]: u for u in client.get("/api/admin/attribute-definitions/usage").json()}
    bandwidth = usage["Bandwidth"]
    assert any(p["offering_name"] == BBI and p["unit"] == "Kbps" for p in bandwidth["products"])
    refused = client.delete(f"/api/admin/attribute-definitions/{bandwidth['id']}")
    assert refused.status_code == 409 and BBI in refused.text

    access = usage["Access Medium"]
    tidy = client.post(f"/api/admin/attribute-definitions/{access['id']}/tidy").json()
    assert tidy["removed"] == access["unused_values"]
    renamed = client.patch(f"/api/admin/attribute-definitions/{access['id']}", json={"name": "Access Technology"})
    assert renamed.status_code == 200
    names = {a["name"] for a in client.get(f"/api/admin/product-model/{BBI}").json()["attributes"]}
    assert "Access Technology" in names and "Access Medium" not in names  # every product follows the library


def test_slots_are_priced_from_the_pricelist_and_round_trip_through_excel(client):
    _setup(client)
    client.post("/api/intents", json={"text": "Launch a new bundle. name: Router Pro, composition: Fiber500, segment: B2B, channel: B2B, price: 450, discount: 0"})
    client.post("/api/admin/product-classes", json={"name": "Routers", "members": ["Router Pro"]})
    client.post(f"/api/admin/product-model/{BBI}/components", json={"name": "Router"})
    client.put(f"/api/admin/product-model/{BBI}/components/Router/slot", json={"domain_type": "class", "class_name": "Routers"})
    _run(client, "INT-43", price_list="Enterprise", offering_name="Router Pro", price=300)
    standard = client.post(f"/api/configurator/{BBI}/session", json={"picks": {"Router": "Router Pro"}}).json()
    enterprise = client.post(f"/api/configurator/{BBI}/session", json={"picks": {"Router": "Router Pro"}, "price_list": "Enterprise"}).json()
    assert {x["value"]: x.get("price") for x in next(v for v in standard["variables"] if v["name"] == "Router")["values"]}["Router Pro"] == 450
    assert {x["value"]: x.get("price") for x in next(v for v in enterprise["variables"] if v["name"] == "Router")["values"]}["Router Pro"] == 300

    xlsx = client.get("/api/admin/product-model/export/file", params={"names": BBI, "format": "xlsx"}).content
    sheets = dict(model.read_xlsx(xlsx))
    assert sheets["Classes"][1][:1] == ["Routers"] and "Router Pro" in sheets["Classes"][1][3]
    router_row = next(r for r in sheets["Products"] if r[0] == "Router")
    assert router_row[model.COLUMNS.index("Slot Class")] == "Routers"
    copy = xlsx  # re-import into another product name: the slot comes along
    parsed = model.read_sheets(copy, "x.xlsx")
    for r in parsed["products"]:
        for k in ("element", "parent"):
            if r.get(k) == BBI:
                r[k] = "BBI Copy"
    db = client.TestSessionLocal()
    result = model.apply_import(db, 1, model.plan_import(db, 1, parsed["products"], [], parsed["classes"]))
    db.commit()
    assert result["errors"] == [], result["errors"]
    router = next(c for c in model.tree(db, 1, "BBI Copy")["children"] if c["name"] == "Router")
    assert router["slot"]["class"] == "Routers"
    db.close()
    imported = client.post("/api/admin/product-model/import/file", json={"file_name": "bbi.xlsx", "content_base64": base64.b64encode(xlsx).decode(), "dry_run": True}).json()
    assert any(line.startswith("Class Routers") for line in imported["preview"])


def test_a_draft_product_can_be_designed_and_priced_through_the_intents(client):
    draft = client.post("/api/admin/product-model", json={"name": "Draft Office Wifi", "segment": "B2B", "price": 700}).json()
    assert draft["status"] == "draft"
    designed = _run(client, "INT-42", offering_name="Draft Office Wifi", instruction="Access points: 2, 4, 8; technical: SSID name")
    assert designed["status"] == "executed", designed
    priced = _run(client, "INT-43", price_list="Enterprise", offering_name="Draft Office Wifi", price=650)
    assert priced["status"] == "executed", priced
    hub = _hub(client, "Draft Office Wifi")
    assert hub["model"]["attributes"] >= 2 and any(p["price_list"] == "Enterprise" and p["price"] == 650 for p in hub["prices"])
    # still not sellable until published
    party_id = _run(client, "INT-41", name="Someone")["result"]["party_id"]
    assert _run(client, "INT-07", party_id=party_id, offering_name="Draft Office Wifi")["status"] != "executed"

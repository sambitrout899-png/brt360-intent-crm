"""Product hierarchies (root -> child products -> attributes), spreadsheet
import / export, and eligibility / compatibility / constraint rules."""

import base64

from app.crm_core import product_model_service as model
from app.models.orders import Order
from app.models.product_config import OrderItemConfig

# The sheet as product managers keep it (Description holds each attribute's domain).
SHEET = """Product Element,Type,Parent,Description,Mandatory/Optional(M/O),Pricing Impact,Provisioning Impact,Price,Charge Type,Price Impacts,Segment
Business Broadband Internet,Root Product,,Main parent product,M,,,0,,,B2B
Contention Ratio,Product Attribute,Business Broadband Internet,1;10,M,Y,Y,,,,
Access Medium,Product Attribute,Business Broadband Internet,"WiMax,Microwave,Fiber,MWP2M,RCY,Zain-DIA,STC-DIA-MEL,Dawiyat-LMA,Mobily-DIA,GPON,Bitstream,DSL",M,Y,Y,,,Fiber: +500; DSL: -10%,
Bandwidth(KBps),Product Attribute,Business Broadband Internet,512 Kbps to 4Mbps,M,Y,Y,,,*: 0.5 each,
Use Existing Media,Product Attribute,Business Broadband Internet,,M,,Y,,,,
IP Address,Product Attribute,Business Broadband Internet,IP Address,,,Y,,,,
POP Location,Product Attribute,Business Broadband Internet,Place,M,,Y,,,,
Access Switch,Product Attribute,Business Broadband Internet,Logical Device,M,,Y,,,,
Access Port,Product Attribute,Business Broadband Internet,Device Interface,M,,Y,,,,
CPE,Child Product,Business Broadband Internet,,O,,Y,,,,
CPE Type,Product Attribute,CPE,"Customer,Provider",O,Y,Y,,,Provider: +200,
BBI-MRC,Child Product,Business Broadband Internet,This would capture the monthly recurring charges,M,,,1500,,,
BBI-NRC,Child Product,Business Broadband Internet,This would capture the one time setup charges,O,,,3000,,,
"""


def _import(client, content: str | bytes, name="bbi.csv", dry_run=False):
    data = content.encode() if isinstance(content, str) else content
    return client.post("/api/admin/product-model/import/file",
                       json={"file_name": name, "content_base64": base64.b64encode(data).decode(), "dry_run": dry_run}).json()


def _run(client, code, **entities):
    return client.post("/api/intents", json={"text": "", "structured": {"intent_type": code, "entities": entities}}).json()


def _opportunity(client, segment="B2B", name="Gulf Logistics"):
    party_id = _run(client, "INT-15", name=name, segment=segment)["result"]["party_id"]
    lead = _run(client, "INT-46", party_id=party_id, products=["Business Broadband Internet"])
    assert lead["status"] == "executed", lead
    opp = _run(client, "INT-17", lead_id=lead["result"]["lead_id"], decision="approve")["result"]["opportunity_id"]
    return party_id, opp


BBI = "Business Broadband Internet"
GOOD = "Contention Ratio: 1:10, Access Medium: Fiber, Bandwidth: 2 Mbps, CPE: Yes, CPE Type: Provider, BBI-NRC: Yes"


def test_sheet_imports_as_a_hierarchy(client):
    dry = _import(client, SHEET, dry_run=True)
    assert dry["errors"] == [] and any("CPE › CPE Type" in line or "› CPE: attribute CPE Type" in line for line in dry["preview"])
    assert client.get("/api/admin/product-model").json() == []  or all(p["name"] != BBI for p in client.get("/api/admin/product-model").json())
    done = _import(client, SHEET)
    assert done["errors"] == [] and done["created"] == [BBI]

    tree = client.get(f"/api/admin/product-model/{BBI}").json()
    attrs = {a["name"]: a for a in tree["attributes"]}
    assert attrs["Contention Ratio"]["values"] == ["1:1", "1:10"] and attrs["Contention Ratio"]["affects_price"] and attrs["Contention Ratio"]["affects_provisioning"]
    assert (attrs["Bandwidth"]["min"], attrs["Bandwidth"]["max"], attrs["Bandwidth"]["unit"]) == (512, 4096, "Kbps")
    assert attrs["POP Location"]["reference_type"] == "Place" and not attrs["POP Location"]["affects_price"]
    assert attrs["Access Medium"]["required"] and len(attrs["Access Medium"]["values"]) == 12
    assert attrs["Use Existing Media"]["values"] == ["Yes", "No"]
    children = {c["name"]: c for c in tree["children"]}
    assert children["CPE"]["mandatory"] is False and children["CPE"]["attributes"][0]["name"] == "CPE Type"
    assert (children["BBI-MRC"]["charge_type"], children["BBI-MRC"]["price"], children["BBI-MRC"]["mandatory"]) == ("recurring", 1500, True)
    assert children["BBI-NRC"]["charge_type"] == "one_time"

    again = _import(client, SHEET)  # importing twice changes nothing new
    assert again["created"] == [] and again["errors"] == []


def test_configuring_prices_children_checks_ranges_and_carries_provisioning_to_the_order(client):
    _import(client, SHEET)
    assert client.post(f"/api/admin/product-model/{BBI}/publish").status_code == 200
    price = client.post(f"/api/catalog/configuration/{BBI}/price", json={"pricing_attributes": GOOD}).json()
    assert price["ok"], price
    # 0 base + 1500 MRC + 500 Fiber + 200 Provider CPE + 2048 Kbps x 0.5; the 3000 NRC is one-time
    assert price["configured_price"] == 1500 + 500 + 200 + 1024
    assert any("(one-time)" in line["label"] and line["amount"] == 3000 for line in price["lines"])
    too_fast = client.post(f"/api/catalog/configuration/{BBI}/price", json={"pricing_attributes": GOOD.replace("2 Mbps", "8 Mbps")}).json()
    assert not too_fast["ok"] and "between 512 and 4096 Kbps" in too_fast["errors"][0]
    no_mrc = client.post(f"/api/catalog/configuration/{BBI}/price", json={"pricing_attributes": GOOD + ", BBI-MRC: No"}).json()
    assert not no_mrc["ok"] and "BBI-MRC is mandatory" in str(no_mrc["errors"])

    party_id, opp = _opportunity(client)
    quote = _run(client, "INT-05", opportunity_id=opp, offering_name=BBI, pricing_attributes=GOOD)
    assert quote["status"] == "executed", quote
    assert quote["result"]["total_price"] == 3224
    _run(client, "INT-06", quote_id=quote["result"]["quote_id"], decision="approve")
    order = client.post("/api/intents", json={"text": f"Activate this order end-to-end. quote_id: {quote['result']['quote_id']}"}).json()
    db = client.TestSessionLocal()
    row = db.query(Order).filter(Order.quote_id == quote["result"]["quote_id"]).first()
    assert row is not None, order
    technical = model.config.loads(db.query(OrderItemConfig).filter(OrderItemConfig.order_item_id == row.items[0].id).one().technical_attributes_json)
    # price-and-provisioning attributes and the provisioned child product reach the order's technical side
    assert technical["Access Medium"] == "Fiber" and technical["Bandwidth"] == "2 Mbps" and technical["CPE"] == "1"
    db.close()


def test_eligibility_compatibility_and_constraint_rules(client):
    _import(client, SHEET)
    client.post(f"/api/admin/product-model/{BBI}/publish")
    constraint = client.post(f"/api/admin/product-model/{BBI}/rules", json={"rule_type": "constraint", "text": "if Access Medium is DSL then Bandwidth must be at most 2048"})
    assert constraint.status_code == 200, constraint.text
    assert constraint.json()["explanation"] == "If Access Medium is DSL: Bandwidth must be at most 2048"
    requires = client.post(f"/api/admin/product-model/{BBI}/rules", json={"rule_type": "constraint", "text": "CPE requires CPE Type"})
    assert requires.status_code == 200, requires.text

    dsl = client.post(f"/api/catalog/configuration/{BBI}/price", json={"pricing_attributes": GOOD.replace("Fiber", "DSL").replace("2 Mbps", "4 Mbps")}).json()
    assert not dsl["ok"] and "Bandwidth must be at most 2048" in str(dsl["errors"])
    no_type = client.post(f"/api/catalog/configuration/{BBI}/price", json={"pricing_attributes": GOOD.replace(", CPE Type: Provider", "")}).json()
    assert not no_type["ok"] and "CPE Type is required" in str(no_type["errors"])

    elig = client.post(f"/api/admin/product-model/{BBI}/rules", json={"rule_type": "eligibility", "text": "only for B2B customers"})
    assert elig.json()["explanation"] == f"{BBI} is only for customers whose Segment is B2B"
    consumer = _run(client, "INT-15", name="Ravi Kumar", segment="B2C")["result"]["party_id"]
    # Vanilla B2C goes straight to an order (no lead); this checks lead interest ignoring eligibility,
    # so it uses the tenant switch that brings the B2C lead path back.
    assert client.put("/api/admin/order-management/b2c-journey", json={"direct_order": False}).status_code == 200
    interest = _run(client, "INT-46", party_id=consumer, products=[BBI])
    assert interest["status"] == "executed", interest
    business, _ = _opportunity(client, name="Oman Freight")
    assert client.get("/api/catalog/eligibility", params={"party_id": business, "names": BBI}).json()["eligible"] == [BBI]
    assert BBI in client.get("/api/catalog/eligibility", params={"party_id": consumer, "names": BBI}).json()["not_eligible"]

    client.post("/api/intents", json={"text": "Launch a new bundle. name: Static IP Pack, composition: Fiber500, segment: B2B, channel: B2B, price: 300, discount: 0"})
    comp = client.post(f"/api/admin/product-model/{BBI}/rules", json={"rule_type": "compatibility", "text": "requires Static IP Pack already installed"})
    assert comp.json()["definition"] == {"effect": "requires", "other": "Static IP Pack", "scope": "account"}
    _, opp = _opportunity(client, name="Muscat Ports")
    blocked = _run(client, "INT-05", opportunity_id=opp, offering_name=BBI, pricing_attributes=GOOD)
    assert blocked["status"] == "failed" and "needs Static IP Pack" in str(blocked)


def test_export_round_trips_through_excel(client):
    _import(client, SHEET)
    client.post(f"/api/admin/product-model/{BBI}/rules", json={"rule_type": "eligibility", "text": "only for B2B customers"})
    xlsx = client.get("/api/admin/product-model/export/file", params={"names": BBI, "format": "xlsx"})
    assert xlsx.status_code == 200 and xlsx.content[:2] == b"PK"
    sheets = dict(model.read_xlsx(xlsx.content))
    assert sheets["Products"][0][:7] == model.COLUMNS[:7]
    assert ["CPE Type", "Product Attribute", "CPE"] == sheets["Products"][[r[0] for r in sheets["Products"]].index("CPE Type")][:3]
    assert sheets["Rules"][1][:3] == [BBI, "eligibility", "only for B2B customers"]
    # into a fresh product name: same structure, same rules
    renamed = xlsx.content
    rows, rules = model.read_rows(renamed, "bbi.xlsx")
    for r in rows:
        for k in ("element", "parent"):
            if r.get(k) == BBI:
                r[k] = "BBI Copy"
    for r in rules:
        r["product"] = "BBI Copy"
    db = client.TestSessionLocal()
    plan = model.plan_import(db, 1, rows, rules)
    result = model.apply_import(db, 1, plan)
    db.commit()
    assert result["errors"] == [], result["errors"]
    copy = model.tree(db, 1, "BBI Copy")
    original = model.tree(db, 1, BBI)
    assert [a["name"] for a in copy["attributes"]] == [a["name"] for a in original["attributes"]]
    assert {c["name"] for c in copy["children"]} == {c["name"] for c in original["children"]}
    assert copy["rules"][0]["explanation"] == "BBI Copy is only for customers whose Segment is B2B"
    db.close()
    csv_text = client.get("/api/admin/product-model/export/file", params={"names": BBI, "format": "csv"}).text
    assert csv_text.startswith("Product Element,Type,Parent,Description")
    template = client.get("/api/admin/product-model/export/file", params={"template": True})
    assert template.status_code == 200 and dict(model.read_xlsx(template.content))["Products"][1][0] == BBI

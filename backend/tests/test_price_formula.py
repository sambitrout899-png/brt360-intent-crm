"""Price formulas - the product modeller's price impact expression builder
(crm_core/price_formula.py): formulas and plain English checked against the
product, an impact preview, and the formula priced as a breakdown line."""

import base64

import pytest

from app.crm_core import price_formula as pf

SHEET = """Product Element,Type,Parent,Description,Mandatory/Optional(M/O),Pricing Impact,Provisioning Impact,Price,Charge Type,Price Impacts,Segment
Business Broadband Internet,Root Product,,Main parent product,M,,,0,,,B2B
Contention Ratio,Product Attribute,Business Broadband Internet,1;10,M,Y,Y,,,,
Access Medium,Product Attribute,Business Broadband Internet,"WiMax,Microwave,Fiber,GPON,DSL",M,Y,Y,,,Fiber: +500; DSL: -10%,
Bandwidth(KBps),Product Attribute,Business Broadband Internet,512 Kbps to 4Mbps,M,Y,Y,,,*: 0.5 each,
POP Location,Product Attribute,Business Broadband Internet,Place,M,,Y,,,,
CPE,Child Product,Business Broadband Internet,,O,,Y,,,,
CPE Type,Product Attribute,CPE,"Customer,Provider",O,Y,Y,,,Provider: +200,
BBI-MRC,Child Product,Business Broadband Internet,This would capture the monthly recurring charges,M,,,1500,,,
"""
BBI = "Business Broadband Internet"
URL = f"/api/admin/product-model/{BBI}"


def _setup(client):
    data = base64.b64encode(SHEET.encode()).decode()
    done = client.post("/api/admin/product-model/import/file", json={"file_name": "bbi.csv", "content_base64": data, "dry_run": False}).json()
    assert done["errors"] == [], done
    assert client.post(f"{URL}/publish").status_code == 200


def _check(client, text, **extra):
    return client.post(f"{URL}/price-formulas/check", json={"text": text, "use_ai": False, **extra})


def _price(client, picks):
    out = client.post(f"/api/catalog/configuration/{BBI}/price", json={"pricing_attributes": picks}).json()
    assert out["ok"], out
    return out


def test_the_language_reads_and_works_out_amounts():
    node = pf.parse('if([Access Medium] = "fiber" and Bandwidth >= 2048, -10% * subtotal, max(0, Bandwidth - 1024) * 0.5)')
    assert pf.references(node) == ["Access Medium", "Bandwidth"]
    fiber = {"accessmedium": "Fiber", "bandwidth": 4096.0}
    assert pf.evaluate(node, fiber, 0, 3000) == pytest.approx(-300)
    assert pf.evaluate(node, {"accessmedium": "DSL", "bandwidth": 2048.0}, 0, 3000) == pytest.approx(512)
    assert pf.evaluate(pf.parse("[CPE] * 250 / 0"), {"cpe": 1.0}, 0, 0) == 0  # never fails at selling time
    assert pf.evaluate(pf.parse('Bandwidth in ("1", "2") or not between(Bandwidth, 10, 20)'), {"bandwidth": None}, 0, 0) is True
    assert pf.evaluate(pf.parse("round(10 / 3, 2) + ceil(1.2) + floor(1.8) + abs(-1) + min(3, 4) + max(3, 4)"), {}, 0, 0) == pytest.approx(3.33 + 2 + 1 + 1 + 3 + 4)
    assert pf.say(node) == ("if Access Medium is fiber and Bandwidth is at least 2,048 then minus 10% of the price so far, "
                            "otherwise (Bandwidth − 1,024, never below 0) × 0.5")
    for bad, said in [("if(Bandwidth > 1, 2)x", "Unexpected"), ("max(", "ends too early"), ("sqrt(4)", "not a function"),
                      ("if(1)", "takes 2 to 3"), ("1 +", "ends too early"), ("[ ]", "Empty"), ("5 $ 4", "don't understand")]:
        with pytest.raises(pf.FormulaError, match=said):
            pf.parse(bad)


def test_a_formula_is_checked_against_the_product_with_an_impact_preview(client):
    _setup(client)
    ok = _check(client, 'if([Access Medium] = "Fiber" and Bandwidth > 2048, -10% * subtotal, 0)', picks={"Access Medium": "Fiber", "Bandwidth": "4 Mbps"}).json()
    assert ok["engine"] == "formula" and ok["uses"] == ["Access Medium", "Bandwidth"] and ok["charge"] == "recurring"
    assert ok["label"] == "Access Medium & Bandwidth pricing" and "Access Medium is Fiber and Bandwidth is more than 2,048" in ok["explanation"]
    # 4 Mbps = 4096 Kbps: 1500 MRC + 500 Fiber + 4096 x 0.5 = 4048 so far -> 10% off
    assert ok["amount"] == -404.8
    rows = {(r["picks"]["Access Medium"], r["picks"]["Bandwidth"]): r["amount"] for r in ok["preview"]}
    assert rows[("DSL", "4,096 Kbps")] == 0 and rows[("Fiber", "2,048 Kbps")] == 0 and rows[("Fiber", "2,049 Kbps")] < 0

    for text, said in [("Bandwith * 2", "did you mean [Bandwidth]"), ("[POP Location] = \"x\"", "technical attribute"),
                       ('if([Access Medium] = "Fibre", 1, 0)', "'Fibre' is not a value of Access Medium"),
                       ('if(Bandwidth = "2 Mbps", 1, 0)', 'number in Kbps - write 2048 instead of "2 Mbps"'),
                       ('[Access Medium] = "Fiber"', "not an amount"), ("if([CPE] > 0, 100", "Expected ')'")]:
        bad = _check(client, text)
        assert bad.status_code == 400 and said in bad.json()["detail"], (text, bad.json())


@pytest.mark.parametrize("text, formula, charge", [
    ("add 300 if Access Medium is Fiber and CPE is chosen", 'if([Access Medium] = "Fiber" and CPE > 0, 300, 0)', "recurring"),
    ("2 per Kbps above 2048 a month", "max(0, Bandwidth - 2048) * 2", "recurring"),
    ("500 extra when Bandwidth is more than 2 Mbps", "if(Bandwidth > 2048, 500, 0)", "recurring"),
    ("10% off the total when Access Medium is DSL", 'if([Access Medium] = "DSL", -10% * subtotal, 0)', "recurring"),
    ("one-time 1,000 installation fee for Provider", 'if([CPE Type] = "Provider", 1000, 0)', "one_time"),
    ("if Bandwidth is at most 1024 then 0; if Bandwidth is at most 2048 then 400; otherwise 900",
     "if(Bandwidth <= 1024, 0, if(Bandwidth <= 2048, 400, 900))", "recurring"),
    ("Access Medium is one of Fiber, GPON: +250", 'if([Access Medium] in ("Fiber", "GPON"), 250, 0)', "recurring"),
])
def test_plain_english_becomes_a_formula(client, text, formula, charge):
    _setup(client)
    out = _check(client, text).json()
    assert (out.get("expression"), out.get("charge"), out.get("engine")) == (formula, charge, "rules"), out


def test_a_saved_formula_prices_quotes_and_is_kept_with_the_model(client):
    _setup(client)
    picks = "Contention Ratio: 1:10, Access Medium: Fiber, Bandwidth: 4 Mbps"
    before = _price(client, picks)["configured_price"]
    assert before == 1500 + 500 + 2048

    saved = client.post(f"{URL}/price-formulas", json={"expression": 'if([Access Medium] = "Fiber" and Bandwidth > 2048, -10% * subtotal, 0)',
                                                        "label": "Fiber high-speed discount", "described_as": "10% off fast fiber"}).json()
    assert saved["rule_type"] == "price" and saved["definition"]["described_as"] == "10% off fast fiber"
    assert saved["explanation"].startswith("Fiber high-speed discount (a month): if Access Medium is Fiber")
    once = client.post(f"{URL}/price-formulas", json={"expression": "[CPE] * 750", "label": "CPE installation", "charge": "one_time"}).json()

    after = _price(client, picks)
    assert after["configured_price"] == round(before * 0.9, 2)
    assert {"label": "Fiber high-speed discount", "amount": -404.8} in [{k: v for k, v in line.items() if k in ("label", "amount")} for line in after["lines"]]
    with_cpe = _price(client, picks + ", CPE: Yes, CPE Type: Customer")
    assert with_cpe["configured_price"] == round(before * 0.9, 2)  # the one-time line is not monthly
    assert any(line["label"] == "CPE installation (one-time)" and line["amount"] == 750 and line.get("one_time") for line in with_cpe["lines"])

    # editing keeps one row; switching it off takes it out of the price
    edited = client.post(f"{URL}/price-formulas", json={"rule_id": saved["id"], "expression": 'if([Access Medium] = "Fiber", -100, 0)', "label": "Fiber discount"}).json()
    assert edited["id"] == saved["id"]
    assert _price(client, picks)["configured_price"] == before - 100
    client.patch(f"/api/admin/product-model/rules/{saved['id']}/active", params={"active": False})
    assert _price(client, picks)["configured_price"] == before

    # the spreadsheet carries it as a rule whose text is the formula
    tree = client.get(URL).json()
    assert {r["id"] for r in tree["rules"] if r["rule_type"] == "price"} == {saved["id"], once["id"]}
    assert next(r for r in tree["rules"] if r["id"] == once["id"])["source_text"] == "one-time: [CPE] * 750"
    # a rules-sheet row of type "price" imports through the same checks
    sheet = "Product,Rule Type,Rule,Message,Active\n" + f'{BBI},price,"if([Access Medium] = ""DSL"", -50, 0)",DSL saver,Y\n'
    done = client.post("/api/admin/product-model/import/file",
                       json={"file_name": "rules.csv", "content_base64": base64.b64encode(sheet.encode()).decode(), "dry_run": False}).json()
    assert done["errors"] == [], done
    assert _price(client, "Contention Ratio: 1:10, Access Medium: DSL, Bandwidth: 1 Mbps")["configured_price"] == 1500 + 512 - 50  # DSL's -10% is of the 0 base


def test_the_vocabulary_lists_what_a_formula_can_use(client):
    _setup(client)
    v = client.get(f"{URL}/price-formulas/vocabulary").json()
    assert {a["name"] for a in v["attributes"]} == {"Contention Ratio", "Access Medium", "Bandwidth", "CPE Type"}
    assert "POP Location" in v["technical"] and {c["name"] for c in v["components"]} == {"CPE", "BBI-MRC"}
    assert "if" in v["functions"] and set(v["variables"]) == {"base", "subtotal"} and v["examples"]

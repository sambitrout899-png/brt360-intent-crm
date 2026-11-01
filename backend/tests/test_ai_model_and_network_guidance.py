"""AI model guidance (Product Modeller) and network attributes at installation.

- Guidance: the AI engine proposes a product type's pricing and service attributes; existing network-level ones are
  moved to installation; it never adds a network-level attribute to the selling model; nothing is saved until the
  administrator applies what they kept; without an AI engine it says so.
- Installation: an attribute "set at installation" is never asked for at order approval; the installation work order
  can't close until it is set; the AI's defaults may only name real inventory resources; the engineer's overrides
  are kept and recorded on the job sheet, and the values reach the asset.
"""

import json
from types import SimpleNamespace

from app.crm_core import llm
from test_product_configuration import _designed
from test_sales_to_order import _opportunity, run

GUIDANCE = {
    "product_type": "Business fibre broadband",
    "summary": "Bandwidth, term and SLA drive the price; access medium and CPE are service choices.",
    "attributes": [
        {"action": "keep", "name": "Bandwidth", "reason": "core pricing choice"},
        {"action": "update", "name": "Contract term", "kind": "pricing", "data_type": "enum",
         "values": ["12 months", "24 months", "36 months"], "required": True, "default": "12 months",
         "price_impacts": [{"value": "24 months", "type": "percent", "amount": -5}, {"value": "36 months", "type": "percent", "amount": -12},
                           {"value": "60 months", "type": "percent", "amount": -20}], "reason": "longer terms earn a bigger discount"},
        {"action": "add", "name": "Access Medium", "kind": "technical", "data_type": "enum", "values": ["Fiber", "GPON"],
         "required": True, "default": "Fiber", "reason": "decides how it is delivered"},
        {"action": "add", "name": "PON Port", "kind": "technical", "reason": "network port"},
        {"action": "move_to_installation", "name": "VLAN ID", "reason": "network-level"},
        {"action": "move_to_installation", "name": "Bandwidth", "reason": "duplicate - ignored, Bandwidth was answered already"},
        {"action": "remove", "name": "Static IPs", "reason": "not sold with this product"},
        {"action": "remove", "name": "Ghost", "reason": "not an attribute of the product"},
    ],
}


def _ai(monkeypatch, answer, calls=None):
    monkeypatch.setattr(llm, "resolve", lambda *a, **k: SimpleNamespace(label="own model (test)"))
    monkeypatch.setattr(llm, "chat_json", lambda target, system, user, **k: (calls.append(user) if calls is not None else None) or answer)


def test_guidance_proposes_and_applies_only_what_was_kept(client, monkeypatch):
    _designed(client)
    monkeypatch.setattr(llm, "resolve", lambda *a, **k: None)
    none = client.post("/api/admin/product-model/Fiber 900/guidance")
    assert none.status_code == 400 and "AI engine" in none.json()["detail"]

    calls: list[str] = []
    _ai(monkeypatch, GUIDANCE, calls)
    plan = client.post("/api/admin/product-model/Fiber 900/guidance").json()
    sent = json.loads(calls[0].split("\n\n")[0])
    assert sent["product"] == "Fiber 900" and "VLAN ID" in [a["name"] for a in sent["current_design"]]
    assert plan["product_type"] == "Business fibre broadband" and plan["engine"] == "ai"
    by_name = {p["name"]: p for p in plan["proposals"]}
    assert "PON Port" not in by_name and any("PON Port" in n and "network-level" in n for n in plan["notes"])
    assert "Ghost" not in by_name
    assert by_name["Bandwidth"]["action"] == "keep" and not by_name["Bandwidth"]["selected"]
    assert by_name["VLAN ID"]["action"] == "move_to_installation" and by_name["VLAN ID"]["group"] == "network"
    assert by_name["Access Medium"]["group"] == "service" and by_name["Contract term"]["group"] == "pricing"
    # an impact on a value the attribute doesn't offer is dropped
    assert [i["value"] for i in by_name["Contract term"]["op"]["price_impacts"]] == ["24 months", "36 months"]
    # attributes the AI said nothing about are kept
    assert by_name["CPE type"]["action"] == "keep"
    assert plan["live"]["status"] in ("active", "draft") and plan["live"]["note"]
    # nothing saved yet
    design = client.get("/api/catalog/configuration/Fiber 900").json()
    assert "Access Medium" not in [a["name"] for a in design["attributes"]]

    kept = [by_name[n]["op"] for n in ("Contract term", "Access Medium", "VLAN ID")]
    applied = client.post("/api/admin/product-model/Fiber 900/guidance/apply", json={"ops": kept}).json()
    assert applied["errors"] == [] and "VLAN ID is now set at installation" in applied["applied"]
    attrs = {a["name"]: a for a in client.get("/api/admin/product-model/Fiber 900").json()["attributes"]}
    assert attrs["VLAN ID"]["capture_stage"] == "installation" and attrs["VLAN ID"]["required"]
    assert attrs["Access Medium"]["capture_stage"] == "sales" and attrs["Access Medium"]["kind"] == "technical"
    assert {"value": "36 months", "type": "percent", "amount": -12.0} in attrs["Contract term"]["price_impacts"]
    assert "Static IPs" in attrs  # its removal wasn't kept

    # a pricing attribute can't wait for installation; a rule is not an attribute change
    bad = client.post("/api/admin/product-model/Fiber 900/guidance/apply", json={"ops": [
        {"op": "upsert_attribute", "name": "Bandwidth", "capture_stage": "installation"}, {"op": "add_rule", "name": "x"}]}).json()
    assert len(bad["errors"]) == 2 and "pricing attribute" in bad["errors"][0]


def _order_with_vlan_at_installation(client):
    _designed(client)
    client.post("/api/admin/product-model/Fiber 900/guidance/apply",
                json={"ops": [{"op": "upsert_attribute", "name": "VLAN ID", "capture_stage": "installation"}]})
    opp_id = _opportunity(client, "NetCo")
    run(client, "INT-37", opportunity_id=opp_id, offering_name="Fiber 900", pricing_attributes={"Bandwidth": "500 Mbps"})
    run(client, "INT-38", opportunity_id=opp_id, address="7 Ring Road", technical_attributes="CPE type Huawei")
    run(client, "INT-18", opportunity_id=opp_id, decision="feasible")
    quote_id = run(client, "INT-05", opportunity_id=opp_id, offering_name="Fiber 900")["result"]["quote_id"]
    run(client, "INT-06", quote_id=quote_id, decision="approve")
    run(client, "INT-39", quote_id=quote_id, po_number="PO-9", po_amount=1800)
    return run(client, "INT-40", quote_id=quote_id)["result"]["order_id"]


def test_network_attributes_are_set_at_installation_not_at_order_approval(client, monkeypatch):
    order_id = _order_with_vlan_at_installation(client)
    assert client.get(f"/api/orders/{order_id}").json()["configuration"][0]["missing"] == []
    approved = run(client, "INT-25", order_id=order_id, decision="approve")
    assert approved["status"] == "executed", approved["rationale"]
    wo = approved["result"]["work_order_id"]

    # the installation can't close without the network attribute the product models for it
    blocked = run(client, "INT-26", work_order_id=wo)
    assert blocked["status"] == "failed" and "VLAN ID is required" in blocked["rationale"]

    # without an AI engine the modelled attributes are listed, with no defaults
    monkeypatch.setattr(llm, "resolve", lambda *a, **k: None)
    plain = client.get(f"/api/field-service/work-orders/{wo}/network-attributes").json()
    assert plain["engine"] is None and [a["name"] for a in plain["products"][0]["attributes"]] == ["VLAN ID"]

    calls: list[str] = []
    _ai(monkeypatch, {"summary": "Fibre over the serving OLT", "products": [{"product": "Fiber 900", "attributes": [
        {"name": "VLAN ID", "value": "301", "source": "default", "required": True, "reason": "next customer VLAN"},
        {"name": "OLT Port", "value": "OLT-9/1/4", "source": "inventory", "resource_type": "port", "reason": "free PON port"},
        {"name": "ONT Serial", "value": "", "source": "engineer", "reason": "read from the ONT on site"},
    ]}]}, calls)
    proposal = client.get(f"/api/field-service/work-orders/{wo}/network-attributes").json()
    sent = json.loads(calls[0].split("\n\n")[0])
    assert sent["products"][0]["quote_configuration"]["CPE type"] == "Huawei"
    assert sent["products"][0]["modelled_installation_attributes"][0]["name"] == "VLAN ID"
    attrs = {a["name"]: a for a in proposal["products"][0]["attributes"]}
    assert attrs["VLAN ID"]["value"] == "301" and attrs["VLAN ID"]["modelled"]
    # the model may not invent an inventory identifier: nothing is held or free here, so it's left to the engineer
    assert attrs["OLT Port"]["value"] == "" and any("OLT-9/1/4" in n for n in proposal["notes"])
    assert not attrs["ONT Serial"]["modelled"]

    item_id = proposal["products"][0]["order_item_id"]
    wrong = client.post(f"/api/field-service/work-orders/{wo}/network-attributes",
                        json={"products": [{"order_item_id": item_id, "values": {"VLAN ID": "abc"}}]})
    assert wrong.status_code == 400 and "number" in wrong.json()["detail"]
    saved = client.post(f"/api/field-service/work-orders/{wo}/network-attributes", json={"products": [{
        "order_item_id": item_id, "values": {"VLAN ID": "305", "OLT Port": "", "ONT Serial": "HWTC1234"},
        "defaults": {"VLAN ID": "301", "OLT Port": "", "ONT Serial": ""}}]}).json()
    assert saved["missing"] == [] and saved["overridden"] == ["Fiber 900 VLAN ID: 301 → 305"]
    assert "Network attributes - Fiber 900: VLAN ID: 305; ONT Serial: HWTC1234" in saved["job_sheet"]
    assert "overridden by" in saved["job_sheet"]

    # a second visit shows what was saved, not the AI's default
    again = {a["name"]: a for a in client.get(f"/api/field-service/work-orders/{wo}/network-attributes").json()["products"][0]["attributes"]}
    assert again["VLAN ID"]["value"] == "305" and again["VLAN ID"]["default"] == "301" and again["ONT Serial"]["value"] == "HWTC1234"

    installed = run(client, "INT-26", work_order_id=wo)
    assert installed["status"] == "executed", installed["rationale"]
    assert installed["result"]["provisioning_attributes"]["VLAN ID"] == "305"

"""Siebel asset-based ordering, complete: every change is a delta order (the
service's line and its components with action codes), a later-dated change
queues behind an open one and is fulfilled after it, an in-flight change
can be cancelled or revised, a change can be quoted first and ordered from
the quote, one change can go to many services at once (bulk request), and
the asset's history shows its revisions, what it was on a date and what it
will be once its open orders are fulfilled."""

import datetime as dt
import json

from test_offering_order_intent import invoke
from test_post_order_scenarios import _asset, _confirm, _installed, _product, say

from app.models.fulfillment import Asset
from app.models.order_mgmt import OmLine
from app.models.orders import Order


def _lines(client, order_id):
    with client.TestSessionLocal() as db:
        return [(r.action_code, r.product_name, r.kind, r.note, r.status) for r in db.query(OmLine).filter_by(
            context_type="order", context_id=order_id).order_by(OmLine.line_number)]


def _with_component(client, asset_id, qty="2"):
    with client.TestSessionLocal() as db:
        asset = db.get(Asset, asset_id)
        data = json.loads(asset.instance_json)
        data["definition"].setdefault("components", []).append(
            {"id": 9001, "name": "Static IP", "parent_id": None, "parent": None, "path": ["Static IP"], "description": "",
             "min_qty": 0, "max_qty": 5, "default_qty": 0, "mandatory": False, "charge_type": "recurring", "price": 0,
             "affects_provisioning": False, "sequence": 1, "slot": None})
        data["configuration"]["pricing"]["Static IP"] = qty
        asset.instance_json = json.dumps(data)
        db.commit()


def test_every_change_is_a_delta_order(client):
    asset_id, _, _ = _installed(client)
    _with_component(client, asset_id)
    up = invoke(client, "INT-107", asset_id=asset_id, asset_operation="modify",
                pricing_attributes={"Bandwidth": "500 Mbps", "Static IP": "3"})
    assert up["status"] == "executed", up
    delta = up["result"]["delta"]
    assert delta["action"] == "Update" and {"name": "Bandwidth", "old": "100 Mbps", "new": "500 Mbps"} in delta["attributes"], delta
    assert delta["components"] == [{"name": "Static IP", "action": "Update", "old_quantity": 2, "new_quantity": 3}]
    lines = _lines(client, up["result"]["order_id"])
    assert lines[0][0] == "Update" and "Bandwidth: 100 Mbps -> 500 Mbps" in lines[0][3]
    assert lines[1][:3] == ("Update", "Static IP", "component")
    _confirm(client, up)
    assert {s for *_, s in _lines(client, up["result"]["order_id"])} == {"complete"}  # Auto-Asset closes the lines

    suspend = invoke(client, "INT-107", asset_id=asset_id, asset_operation="suspend")
    assert [(a, kind) for a, _, kind, *_ in _lines(client, suspend["result"]["order_id"])] == [("Suspend", "service"), ("Suspend", "component")]
    _confirm(client, suspend)
    ended = invoke(client, "INT-107", asset_id=asset_id, asset_operation="terminate")
    assert [a for a, *_ in _lines(client, ended["result"]["order_id"])] == ["Delete", "Delete"]
    view = client.get(f"/api/order-management/orders/{ended['result']['order_id']}")
    assert view.status_code == 200 and view.json()["totals"]["recurring"] == 0, view.text  # its charge stops


def test_a_later_dated_change_queues_behind_the_open_one(client):
    asset_id, _, _ = _installed(client)
    first = invoke(client, "INT-107", asset_id=asset_id, asset_operation="modify", pricing_attributes={"Bandwidth": "500 Mbps"})
    assert first["status"] == "executed", first
    undated = invoke(client, "INT-107", asset_id=asset_id, asset_operation="modify", quantity=2)
    assert undated["status"] == "failed" and "due date" in undated["rationale"], undated

    later = (dt.date.today() + dt.timedelta(days=10)).isoformat()
    second = invoke(client, "INT-107", asset_id=asset_id, asset_operation="modify", quantity=2, due_date=later)
    assert second["status"] == "executed", second
    result = second["result"]
    assert result["change_status"] == "queued" and result["queued_behind_order_id"] == first["result"]["order_id"]
    assert result["due_date"] == later and result["current_monthly"] == 700 and result["new_monthly"] == 1400  # built on the 500 Mbps one

    history = invoke(client, "INT-113", asset_id=asset_id)["result"]
    assert history["current"]["monthly"] == 500 and history["requested"]["monthly"] == 1400  # Siebel's requested asset
    assert [c["status"] for c in history["changes"]] == ["submitted", "queued"]

    early = invoke(client, "INT-108", order_id=second["result"]["order_id"])
    assert early["status"] == "failed" and "queued behind order" in early["rationale"], early
    _confirm(client, first)
    _confirm(client, second)
    status, data, _ = _asset(client, asset_id)
    assert data["quantity"] == 2 and data["configuration"]["pricing"]["Bandwidth"] == "500 Mbps"

    history = invoke(client, "INT-113", asset_id=asset_id, as_of=dt.date.today().isoformat())["result"]
    assert [r["version"] for r in history["revisions"]] == [1, 2, 3] and "requested" not in history
    assert history["as_of"]["version"] == 3
    assert invoke(client, "INT-113", asset_id=asset_id, as_of="2020-01-01")["result"]["as_of"]["note"].startswith("The service wasn't installed")


def test_cancel_an_in_flight_change_leaves_the_service_as_it_is(client):
    asset_id, _, _ = _installed(client)
    change = invoke(client, "INT-107", asset_id=asset_id, asset_operation="modify", pricing_attributes={"Bandwidth": "500 Mbps"})
    said = say(client, f"Cancel the pending change on asset {asset_id}")
    assert said["intent_type"] == "INT-110" and said["status"] == "executed", said
    assert said["result"]["order_id"] == change["result"]["order_id"] and said["result"]["asset_version"] == 1
    with client.TestSessionLocal() as db:
        assert db.get(Order, change["result"]["order_id"]).status.value == "cancelled"
    assert {s for *_, s in _lines(client, change["result"]["order_id"])} == {"cancelled"}
    assert _asset(client, asset_id)[1]["configuration"]["pricing"]["Bandwidth"] == "100 Mbps"
    assert invoke(client, "INT-108", order_id=change["result"]["order_id"])["status"] == "failed"

    # Cancel a sales order (INT-61) on an asset change order releases the service too
    again = invoke(client, "INT-107", asset_id=asset_id, asset_operation="suspend")
    cancelled = invoke(client, "INT-61", order_id=again["result"]["order_id"], reason="Customer called back")
    assert cancelled["status"] == "executed" and cancelled["result"]["status"] == "cancelled", cancelled
    assert invoke(client, "INT-107", asset_id=asset_id, asset_operation="suspend")["status"] == "executed"


def test_revise_an_in_flight_change(client):
    asset_id, _, _ = _installed(client)
    change = invoke(client, "INT-107", asset_id=asset_id, asset_operation="modify", pricing_attributes={"Bandwidth": "500 Mbps"})
    revised = say(client, f"Revise change order {change['result']['order_id']}: quantity 2")
    assert revised["intent_type"] == "INT-111" and revised["status"] == "executed", revised
    result = revised["result"]
    assert result["revision"] == 2 and result["revision_of_order_id"] == change["result"]["order_id"]
    assert result["new_monthly"] == 1400  # still 500 Mbps - now two of them
    with client.TestSessionLocal() as db:
        assert db.get(Order, change["result"]["order_id"]).status.value == "cancelled"
    _confirm(client, revised)
    data = _asset(client, asset_id)[1]
    assert data["quantity"] == 2 and data["configuration"]["pricing"]["Bandwidth"] == "500 Mbps"
    history = invoke(client, "INT-113", asset_id=asset_id)["result"]
    assert [(c["status"], c["revision_of_order_id"]) for c in history["changes"]] == [
        ("revised", None), ("completed", change["result"]["order_id"])]


def test_quote_a_change_then_order_it(client):
    asset_id, _, _ = _installed(client)
    quoted = say(client, f"Quote an upgrade of asset {asset_id} to 500 Mbps")
    assert quoted["intent_type"] == "INT-114" and quoted["status"] == "executed", quoted
    q = quoted["result"]
    assert q["quote_number"].startswith("AQ-") and q["new_monthly"] == 700 and q["order_lines"][0]["action"] == "Update"
    with client.TestSessionLocal() as db:  # nothing ordered yet
        assert db.query(Order).filter(Order.order_type == "change").count() == 0
    ordered = say(client, f"Order change quote {q['quote_number']}")
    assert ordered["intent_type"] == "INT-115" and ordered["status"] == "executed", ordered
    assert ordered["result"]["new_monthly"] == 700 and ordered["result"]["quote_number"] == q["quote_number"]
    assert invoke(client, "INT-115", quote_number=q["quote_number"])["status"] == "failed"  # ordered already

    stale = invoke(client, "INT-114", asset_id=asset_id, asset_operation="suspend", due_date=(dt.date.today() + dt.timedelta(days=5)).isoformat())
    assert stale["status"] == "executed", stale
    _confirm(client, ordered)  # the service changes before the quote is ordered
    late = invoke(client, "INT-115", quote_number=stale["result"]["quote_number"])
    assert late["status"] == "failed" and "quote the change again" in late["rationale"], late


def test_bulk_request(client):
    asset_id, _, party = _installed(client)
    second = invoke(client, "INT-106", party_id=party, offering_name="Home Fiber", order_mode="silent")
    assert second["status"] == "executed", second
    with client.TestSessionLocal() as db:
        other = db.query(Asset).filter_by(order_item_id=db.get(Order, second["result"]["order_id"]).items[0].id).one().id
    invoke(client, "INT-107", asset_id=other, asset_operation="suspend")  # this one already has an open change
    bulk = say(client, f"Upgrade all Home Fiber services of customer {party} to 500 Mbps")
    assert bulk["intent_type"] == "INT-112" and bulk["status"] == "executed", bulk
    result = bulk["result"]
    assert result["requested"] == 2 and result["submitted"] == 1 and result["failed"] == 1, result
    assert {r["asset_id"]: r["status"] for r in result["results"]} == {asset_id: "submitted", other: "failed"}
    by_ids = invoke(client, "INT-112", asset_ids=f"{asset_id}, {other}", asset_operation="resume")
    assert by_ids["status"] == "failed" and "No service was changed" in by_ids["rationale"]


def test_the_new_steps_are_understood_and_offered(client):
    for text, code in (("Cancel change order 12", "INT-110"), ("Revise change order 12 to 500 Mbps", "INT-111"),
                       ("Suspend assets 4, 5 and 6", "INT-112"), ("History of asset 4", "INT-113"),
                       ("What did asset 4 look like on 2026-01-31", "INT-113"), ("Quote relocating asset 4 to 5 MG Road", "INT-114"),
                       ("Order change quote AQ-3", "INT-115"), ("Upgrade asset 4 to 500 Mbps", "INT-107")):
        got = client.post("/api/intents/rehearse", json={"text": text})  # understood, nothing kept
        assert got.status_code == 200 and got.json()["intent_type"] == code, (text, got.json())
    for text in ("Revise order 7: add 2 x Router CPE", "Cancel order 7", "Cancel asset 4"):  # sales-order wording stays as it was
        assert client.post("/api/intents/rehearse", json={"text": text}).json()["intent_type"] not in ("INT-110", "INT-111"), text
    asset_id, order_id, _ = _installed(client)
    buttons = client.get(f"/api/records/order/{order_id}").json()["actions"]
    assert {"INT-113", "INT-114"} <= {b["intent"] for b in buttons}
    change = invoke(client, "INT-107", asset_id=asset_id, asset_operation="suspend")["result"]["order_id"]
    assert {"INT-110", "INT-111", "INT-108"} <= {b["intent"] for b in client.get(f"/api/records/order/{change}").json()["actions"]}

"""Order Management (Siebel OM) on top of the quote -> order chain: quote
lines, discounts, promotions, verification, forecast, agreements; order
entry (ship-to, availability, tax, payment, credit, submit with approval),
hold, cancel, supplemental and follow-on orders, shipping and delivery
(assets + billing), returns, activity plans, resume and tracking - all
through the intents themselves."""

from app.models.field_service import FsInventoryLevel, FsInventoryLocation
from app.models.assurance import WorkOrder
from test_sales_to_order import _launch, _opportunity, _user, run, say

OM = "/api/order-management"


def _setup(client):
    assert client.post("/api/field-service/setup/sample", json={"timezone": "UTC"}).status_code == 200
    assert client.post(f"{OM.replace('/api/', '/api/admin/')}/setup/sample").status_code == 200


def _quote(client, company):
    opp = _opportunity(client, company)
    body = run(client, "INT-05", opportunity_id=opp, offering_name="Fiber 900")
    assert body["status"] == "executed", body
    return opp, body["result"]["quote_id"]


def _order_from(client, quote_id):
    assert run(client, "INT-06", quote_id=quote_id, decision="approve")["result"]["status"] == "approved"
    proposal = run(client, "INT-22", quote_id=quote_id)["result"]["proposal_id"]
    assert run(client, "INT-23", proposal_id=proposal, decision="approve")["status"] == "executed"
    body = run(client, "INT-24", quote_id=quote_id)
    assert body["status"] == "executed", body
    return body["result"]["order_id"]


def _stock(client, product, location="Central Warehouse"):
    db = client.TestSessionLocal()
    try:
        loc = db.query(FsInventoryLocation).filter(FsInventoryLocation.name == location).one()
        lv = db.query(FsInventoryLevel).filter(FsInventoryLevel.location_id == loc.id, FsInventoryLevel.product_name == product,
                                               FsInventoryLevel.status == "good").one()
        return lv.on_hand, lv.reserved
    finally:
        db.close()


def test_quote_lines_discounts_promotion_verify_forecast_and_agreement(client):
    _setup(client)
    _launch(client)
    opp, q = _quote(client, "Quote Lines Co")

    added = say(client, f"Add 2 x Router CPE to quote {q}")
    assert added["intent_type"] == "INT-51" and added["status"] == "executed", added
    cpe = added["result"]["added"][0]
    assert cpe["kind"] == "equipment" and cpe["price_type"] == "one_time" and cpe["list_price"] == 150 and cpe["quantity"] == 2
    assert any(r["product"] == "SFP module 1G" for r in added["result"]["recommended"])  # up-sell for the router

    unknown = run(client, "INT-51", quote_id=q, product="Site survey")
    assert unknown["status"] == "failed" and "write-in" in unknown["rationale"]
    fee = run(client, "INT-51", quote_id=q, product="Site survey", unit_price=500, price_type="one_time")
    assert fee["result"]["added"][0]["kind"] == "write_in"

    no_reason = run(client, "INT-52", quote_id=q, line="Router CPE", discount_pct=15)
    assert no_reason["status"] == "failed" and "reason" in no_reason["rationale"]
    disc = say(client, f"Discount the Router CPE line on quote {q} by 15% - multi-site deal")
    assert disc["status"] == "executed" and disc["result"]["line"]["net_price"] == 127.5

    promo = say(client, f"Apply promotion ROUTER10 to quote {q}")
    assert promo["status"] == "executed" and promo["result"]["commitment_months"] == 12
    assert promo["result"]["lines"][0]["discount_pct"] == 15  # the better discount stays

    verified = say(client, f"Verify quote {q}")
    assert verified["result"]["ok"] and any("write-in" in i["text"] for i in verified["result"]["issues"])

    forecast = say(client, f"Update the opportunity from quote {q}")
    assert forecast["status"] == "executed"
    revenue = client.get(f"{OM}/opportunities/{opp}/revenue").json()
    assert {r["product"] for r in revenue} == {"Fiber 900", "Router CPE", "Site survey"}
    assert next(r for r in revenue if r["product"] == "Router CPE")["amount"] == 255.0

    view = client.get(f"{OM}/quotes/{q}").json()
    assert view["quote_number"].startswith("Q-") and view["totals"]["one_time"] == 755.0 and not view["expired"]

    agreement = say(client, f"Create an agreement from quote {q} for 24 months with 4 hour response")
    assert agreement["intent_type"] == "INT-65" and agreement["status"] == "executed", agreement
    assert agreement["result"]["agreement_number"].startswith("AG-") and agreement["result"]["entitlement"]
    ents = client.get("/api/field-service/entitlements").json()
    assert any(e["response_hours"] == 4 and e["name"].startswith("AG-") for e in ents)


def test_order_entry_submit_ship_deliver_return_and_follow_on(client):
    _setup(client)
    _launch(client)
    _, q = _quote(client, "Order Entry Co")
    run(client, "INT-51", quote_id=q, product="Router CPE", quantity=2)
    order_id = _order_from(client, q)

    view = client.get(f"{OM}/orders/{order_id}").json()
    assert view["om_status"] == "pending" and view["order_number"].startswith("SO-")
    assert [line["product_name"] for line in view["lines"]] == ["Fiber 900", "Router CPE"]  # the quote's lines came along

    early = run(client, "INT-59", order_id=order_id)
    assert early["status"] == "failed" and "ship-to" in early["rationale"]

    ship_to = say(client, f"Ship to 12 King Fahd Road, Riyadh for order {order_id} by Standard")
    assert ship_to["intent_type"] == "INT-55" and ship_to["result"]["freight"] == 70.0  # 50 + 2 x 10
    atp = say(client, f"Check availability for order {order_id}")
    assert next(a for a in atp["result"]["availability"] if a["product_name"] == "Router CPE")["atp_status"] == "available"
    tax = say(client, f"Calculate tax for order {order_id}")
    assert tax["result"]["one_time_tax"] == 45.0 and tax["result"]["monthly_tax"] > 0  # 15% of 300

    leak = run(client, "INT-57", order_id=order_id, payment_method="credit_card", card_last4="4111 1111 1111 1111")
    assert leak["status"] == "failed" and "full card number" in leak["rationale"]
    paid = say(client, f"Add a payment by card ending 4242 for order {order_id}")
    assert paid["result"]["payment"]["status"] == "authorized" and paid["result"]["payment"]["amount"] == 415.0  # 300 + 70 + 45

    assert say(client, f"Put order {order_id} on hold - waiting for site access")["result"]["hold"] is True
    held = run(client, "INT-59", order_id=order_id)
    assert held["status"] == "failed" and "hold" in held["rationale"]
    assert say(client, f"release order {order_id} from hold")["result"]["hold"] is False

    before, _ = _stock(client, "Router CPE")
    submitted = say(client, f"Submit order {order_id}")
    assert submitted["intent_type"] == "INT-59" and submitted["result"]["om_status"] == "awaiting_shipping", submitted
    assert "confirmation sent" in submitted["result"]["acknowledgement"]
    assert _stock(client, "Router CPE")[1] == 2  # reserved for the order
    lines = client.get(f"{OM}/orders/{order_id}").json()["lines"]
    assert [line["quantity"] for line in lines if line["product_name"] == "Router CPE"] == [1, 1]  # exploded for asset tracking

    supp = say(client, f"Supplemental order for order {order_id}: add 1 x SFP module 1G")
    assert supp["status"] == "executed" and supp["result"]["revision"] == 2 and supp["result"]["changes"][0]["action"] == "Add"

    shipped = say(client, f"Ship order {order_id} from Central Warehouse")
    assert shipped["intent_type"] == "INT-64" and shipped["result"]["om_status"] == "shipped" and shipped["result"]["tracking_number"]
    assert _stock(client, "Router CPE") == (before - 2, 0)

    too_late = run(client, "INT-61", order_id=order_id, reason="changed mind")
    assert too_late["status"] == "failed" and "return it" in too_late["rationale"]
    no_supp = run(client, "INT-62", order_id=order_id, changes="add 1 x Router CPE")
    assert no_supp["status"] == "failed" and "follow-on" in no_supp["rationale"]

    delivered = say(client, f"order {order_id} delivered")
    assert delivered["result"]["om_status"] == "delivered" and len(delivered["result"]["assets"]) == 3
    assert delivered["result"]["billed"] > 0
    view = client.get(f"{OM}/orders/{order_id}").json()
    assert view["payments"][0]["status"] == "captured"
    assets = client.get("/api/field-service/assets?q=Router").json()
    assert any(a["warranty"] for a in assets)  # equipment warranties came with the new assets

    returned = say(client, f"Return 1 Router CPE on order {order_id} - dead on arrival")
    assert returned["status"] == "executed" and returned["result"]["repairs"][0].startswith("RPR-") and returned["result"]["credit"] == 150.0

    tracked = say(client, f"Track order {order_id}")
    assert tracked["result"]["shipments"] and tracked["result"]["om_status"] == "delivered"

    follow = say(client, f"Follow-on order for order {order_id}: add 1 x Router CPE from 2030-01-15")
    assert follow["status"] == "executed", follow
    new_view = client.get(f"{OM}/orders/{follow['result']['order_id']}").json()
    assert new_view["requested_date"] == "2030-01-15" and new_view["source_order_id"] == order_id
    assert {line["action_code"] for line in new_view["lines"]} == {"-", "Add"}

    # delivered, and then field service completes the order: its service is in place - fulfilled
    from app.models.orders import Order, OrderStatus
    with client.TestSessionLocal() as db:
        db.get(Order, order_id).status = OrderStatus.completed
        db.commit()
    assert client.get(f"{OM}/orders/{order_id}").json()["om_status"] == "fulfilled"


def test_approval_cancel_credit_activity_plan_and_resume(client):
    _setup(client)
    _launch(client)
    _, q = _quote(client, "Approval Co")
    order_id = _order_from(client, q)
    run(client, "INT-51", order_id=order_id, product="Router CPE")
    run(client, "INT-52", order_id=order_id, line="Router CPE", discount_pct=25, discount_justification="launch customer")
    run(client, "INT-55", order_id=order_id, address="1 Main St", shipping_method="Express")
    run(client, "INT-56", order_id=order_id)
    run(client, "INT-57", order_id=order_id, payment_method="credit_card", card_last4="4242")

    agent = _user(client, "om-agent@default.local")
    waiting = run(client, "INT-59", user_id=agent, order_id=order_id)
    assert waiting["result"]["om_status"] == "awaiting_approval" and waiting["result"]["approval_needed"]
    refused = run(client, "INT-59", user_id=agent, order_id=order_id, decision="approve")
    assert refused["status"] == "failed" and "manager" in refused["rationale"]
    approved = say(client, f"approve the submitted order {order_id}")
    assert approved["result"]["om_status"] == "awaiting_shipping" and approved["result"]["approved"]

    review = say(client, f"Credit check for order {order_id}")
    assert review["result"]["result"] == "review"  # no credit line on file
    party_id = client.get(f"{OM}/orders/{order_id}").json()["party_id"]
    assert client.put(f"/api/admin/order-management/credit/profiles/{party_id}", json={"credit_limit": 100000}).status_code == 200
    assert run(client, "INT-58", order_id=order_id)["result"]["result"] == "approved"

    plan = say(client, f"Add the Fiber installation activity plan to order {order_id}")
    assert plan["status"] == "executed" and plan["result"]["steps"] == 5
    db = client.TestSessionLocal()
    assert db.get(WorkOrder, plan["result"]["work_order_id"]).order_id == order_id
    db.close()

    cancelled = say(client, f"Cancel sales order {order_id} - customer changed their mind")
    assert cancelled["intent_type"] == "INT-61" and cancelled["result"]["om_status"] == "cancelled" and cancelled["result"]["payments_voided"] == 1
    assert _stock(client, "Router CPE")[1] == 0  # reservation released
    assert client.get(f"/api/orders/{order_id}").json()["status"] == "cancelled"

    # resume: a live consumer service, suspended, then resumed
    party = run(client, "INT-41", name="Resume Person", email="r@example.com")["result"]["party_id"]
    live = run(client, "INT-07", party_id=party, offering_name="Fiber 900")
    live_order = live["result"]["order_id"]
    assert run(client, "INT-09", order_id=live_order, action="suspend")["result"]["status"] == "suspended"
    resumed = say(client, f"Resume order {live_order}")
    assert resumed["intent_type"] == "INT-70" and resumed["result"]["status"] == "active"


def test_order_management_setup_lists(client):
    created = client.post("/api/admin/order-management/tax-rules", json={"jurisdiction": "Riyadh", "name": "City levy", "rate_pct": 2,
                                                                         "applies_to": "recurring"})
    assert created.status_code == 200 and created.json()["rate_pct"] == 2
    bad = client.post("/api/admin/order-management/tax-rules", json={"name": "Odd", "applies_to": "weekends"})
    assert bad.status_code == 400
    fav = client.post("/api/admin/order-management/favorites", json={"name": "Starter", "items": [{"product": "Router CPE", "quantity": 1}]}).json()
    assert fav["items"] == [{"product": "Router CPE", "quantity": 1.0}]
    assert client.get("/api/order-management/catalog").json()["favorites"][0]["name"] == "Starter"
    catalog = client.get("/api/intents/catalog").json()
    codes = {i["code"] for i in catalog["intents"]}
    assert {f"INT-{n}" for n in range(51, 72)} <= codes

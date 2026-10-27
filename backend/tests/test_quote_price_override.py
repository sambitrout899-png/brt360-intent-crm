"""Quote price override (INT-116): a negotiated total replaces a draft
quote's computed price, routes approval on its effective discount, flows to
the order, and ends when the quote is re-priced."""


def run(client, code, **entities):
    return client.post("/api/intents", json={"text": "", "structured": {"intent_type": code, "entities": entities}}).json()


def _draft_quote(client, offering="Fiber Negotiate", price=1000, quantity=1, company="NegotiateCo"):
    assert run(client, "INT-01", name=offering, composition="Fiber500", segment="B2B-SMB", channel="B2B",
               list_price=price, requested_discount_pct=0)["status"] == "executed"
    opp_id = run(client, "INT-04", company_name=company, source="referral", region="North")["result"]["opportunity_id"]
    quoted = run(client, "INT-05", opportunity_id=opp_id, offering_name=offering, quantity=quantity, requested_discount_pct=0)
    assert quoted["status"] == "executed", quoted
    return quoted["result"]["quote_id"]


def test_override_sets_total_and_keeps_the_system_price(client):
    quote_id = _draft_quote(client, quantity=2)
    body = run(client, "INT-116", quote_id=quote_id, override_price=1700, reason="competing offer at 1,750")
    assert body["intent_type"] == "INT-116" and body["status"] == "executed", body
    result = body["result"]
    assert result["total_price"] == 1700 and result["computed_price"] == 2000 and result["list_price"] == 2000
    assert result["effective_discount_pct"] == 15.0 and result["adjustment"] == -300
    assert "quote.price_overridden" in body["events"]

    quote = client.get(f"/api/quotes/{quote_id}").json()
    assert quote["total_price"] == 1700
    assert quote["price_override"]["active"]["reason"] == "competing offer at 1,750"

    # a counter-offer replaces it, the earlier round stays in the history
    assert run(client, "INT-116", quote_id=quote_id, override_price=1800, reason="met in the middle")["result"]["computed_price"] == 2000
    history = client.get(f"/api/quotes/{quote_id}").json()["price_override"]["history"]
    assert [(h["override_price"], h["active"], h["ended_reason"]) for h in history] == [(1700, False, "replaced"), (1800, True, None)]

    # back to the system price
    removed = run(client, "INT-116", quote_id=quote_id, remove_override=True)
    assert removed["status"] == "executed" and removed["result"]["total_price"] == 2000
    assert client.get(f"/api/quotes/{quote_id}").json()["price_override"]["active"] is None


def test_override_routes_approval_on_its_effective_discount(client):
    quote_id = _draft_quote(client, offering="Fiber Negotiate VP", company="VpCo")
    run(client, "INT-116", quote_id=quote_id, override_price=750, reason="multi-year enterprise deal")
    submitted = run(client, "INT-06", quote_id=quote_id, decision="approve")
    assert submitted["status"] == "executed", submitted
    chain = client.get(f"/api/quotes/{quote_id}").json()["approval_chain"]
    assert chain["template_name"] == "VP Sign-off"  # 25% off list - not the auto-approved 0% band


def test_override_needs_a_reason_and_respects_the_floor(client):
    quote_id = _draft_quote(client, offering="Fiber Negotiate Floor", company="FloorCo")
    no_reason = run(client, "INT-116", quote_id=quote_id, override_price=900)
    assert no_reason["status"] != "executed" and "reason" in str(no_reason).lower()
    assert client.get(f"/api/quotes/{quote_id}").json()["total_price"] == 1000

    from app.models.catalog import ProductOffering

    db = client.TestSessionLocal()
    try:
        offering = db.query(ProductOffering).filter(ProductOffering.name == "Fiber Negotiate Floor").one()
        offering.price_floor = 800
        db.commit()
    finally:
        db.close()
    below = run(client, "INT-116", quote_id=quote_id, override_price=700, reason="too good to be true")
    assert below["status"] != "executed"
    assert client.get(f"/api/quotes/{quote_id}").json()["total_price"] == 1000


def test_only_a_draft_quote_can_be_overridden(client):
    quote_id = _draft_quote(client, offering="Fiber Negotiate Locked", company="LockedCo")
    assert run(client, "INT-06", quote_id=quote_id, decision="approve")["result"]["status"] == "approved"
    refused = run(client, "INT-116", quote_id=quote_id, override_price=900, reason="late ask")
    assert refused["status"] != "executed"
    assert client.get(f"/api/quotes/{quote_id}").json()["total_price"] == 1000


def test_repricing_ends_the_override(client):
    quote_id = _draft_quote(client, offering="Fiber Negotiate Reprice", company="RepriceCo")
    run(client, "INT-116", quote_id=quote_id, override_price=900, reason="loyalty")
    revised = run(client, "INT-19", quote_id=quote_id, requested_discount_pct=5)
    assert revised["status"] == "executed" and revised["result"]["price_override_ended"] is True
    quote = client.get(f"/api/quotes/{quote_id}").json()
    assert quote["total_price"] != 900
    assert quote["price_override"]["active"] is None and quote["price_override"]["history"][0]["ended_reason"] == "repriced"


def test_negotiated_price_becomes_the_order_price(client):
    quote_id = _draft_quote(client, offering="Fiber Negotiate Order", company="OrderCo")
    run(client, "INT-116", quote_id=quote_id, override_price=940, reason="price match")
    assert run(client, "INT-06", quote_id=quote_id, decision="approve")["result"]["status"] == "approved"
    activated = run(client, "INT-07", quote_id=quote_id)
    assert activated["status"] == "executed", activated
    assert client.get(f"/api/orders/{activated['result']['order_id']}").json()["monthly_price"] == 940


def test_override_is_understood_in_plain_english(client):
    for text, expected in (
        ("Override the price of quote 12 to 4,500 - customer has a competing offer",
         {"quote_id": 12, "override_price": 4500.0, "reason": "customer has a competing offer"}),
        ("negotiated price for quote 7 is 3200 because they sign for 36 months",
         {"quote_id": 7, "override_price": 3200.0, "reason": "they sign for 36 months"}),
        ("Remove the price override on quote 12", {"quote_id": 12, "remove_override": True}),
    ):
        said = client.post("/api/intents/rehearse", json={"text": text}).json()
        assert said["intent_type"] == "INT-116", (text, said)
        for key, value in expected.items():
            assert said["entities"].get(key) == value, (text, key, said["entities"])
    # a quote revision is still a revision
    assert client.post("/api/intents/rehearse", json={"text": "Revise quote 12 to 10% discount"}).json()["intent_type"] == "INT-19"

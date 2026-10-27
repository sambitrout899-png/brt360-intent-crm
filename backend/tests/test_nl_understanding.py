"""Natural-language understanding on the no-AI-key path: everyday
phrasings, "did you mean" suggestions instead of dead ends, values written
naturally ("quote 12", "10%", "₹999"), and the viewed record standing in
for "this"/"it"."""

import pytest

from app.intent_engine import classifier, understanding


def _classify(text, context=None):
    return classifier._classify_with_rules(text, [], context)


@pytest.mark.parametrize(
    "text, expected",
    [
        ("Add a new product", "INT-01"),
        ("create a new plan called Fiber Max with Fiber500 + VOIP for ₹999", "INT-01"),
        ("change the price of the Fiber 500 Bundle", "INT-02"),
        ("discontinue the old DSL package", "INT-03"),
        ("add a new prospect", "INT-15"),
        ("approve quote 12", "INT-06"),
        ("reject lead 4 because budget is gone", "INT-17"),
        ("approve order 9", "INT-25"),
        ("the customer is complaining their internet is not working", "INT-10"),
        ("troubleshoot ticket 3", "INT-11"),
        ("send a technician for ticket 3", "INT-12"),
        ("suspend the service on order 5", "INT-09"),
        ("update quote 12 to a 10% discount", "INT-19"),
        ("make a new revision of quote 12", "INT-30"),
        ("cancel quote 8", "INT-20"),
        ("mark deal 4 as won", "INT-31"),
        ("transfer quote 12 to the Sales Director", "INT-32"),
        ("generate a proposal for quote 12", "INT-22"),
    ],
)
def test_everyday_phrasings(text, expected):
    result = _classify(text)
    assert result.intent_type == expected, (text, result)


def test_unclear_text_offers_suggestions_not_a_dead_end():
    result = _classify("do something with the quote")
    assert result.intent_type is None
    assert result.alternatives  # quote-related intents offered
    assert all(code.startswith("INT-") for code in result.alternatives)


def test_values_written_naturally_are_extracted():
    loose = understanding.extract_loose_entities("create a plan called Fiber Max with Fiber500 + VOIP at ₹999, 10% off, quote #12")
    assert loose["name"] == "Fiber Max"
    assert loose["composition"] == ["Fiber500", "VOIP"]
    assert loose["price"] == 999.0
    assert loose["discount"] == 10.0
    assert loose["quote_id"] == 12

    entities = understanding.entities_for("INT-01", loose, "")
    assert entities["list_price"] == 999.0 and entities["requested_discount_pct"] == 10.0


def test_work_order_is_not_mistaken_for_an_order():
    loose = understanding.extract_loose_entities("close work order 7")
    assert loose == {"work_order_id": 7}


def test_viewed_record_resolves_this():
    on_quote = {"entity_type": "quote", "entity_id": 12}
    result = _classify("approve this", on_quote)
    assert result.intent_type == "INT-06"
    assert result.entities["quote_id"] == 12
    assert "record you are viewing" in result.rationale

    on_order = {"entity_type": "order", "entity_id": 5}
    assert _classify("approve this", on_order).intent_type == "INT-25"

    # a stated id always beats the context
    assert _classify("approve quote 7", on_quote).entities["quote_id"] == 7


def test_ranked_match_needs_confirmation_and_suggestions_reach_the_api(client):
    body = client.post("/api/intents", json={"text": "Add a new product"}).json()
    assert body["intent_type"] == "INT-01"
    assert body["status"] == "needs_review"  # never auto-run from a fuzzy match
    assert set(body["missing_fields"]) >= {"name", "composition", "list_price"}

    unclear = client.post("/api/intents", json={"text": "do something with the quote"}).json()
    assert unclear["intent_type"] is None and unclear["missing_fields"] == []
    assert unclear["suggestions"] and all(s["label"] and "required_fields" in s for s in unclear["suggestions"])

    assist = client.post("/api/intents/assist", json={"text": "do something with the quote"}).json()
    assert assist["suggestions"]


def test_add_a_new_product_conversation(client):
    """Exactly what the Intent Drawer does: free text -> the engine names the
    action and what it still needs -> the user fills those in -> it runs."""
    first = client.post("/api/intents", json={"text": "Add a new product"}).json()
    assert first["intent_type"] == "INT-01" and first["intent_label"] == "Launch a new product"

    answers = {"name": "Fiber Max", "composition": ["Fiber500", "VOIP"], "list_price": 999}
    second = client.post(
        "/api/intents",
        json={"text": "Add a new product", "structured": {"intent_type": first["intent_type"], "entities": {**first["entities"], **answers}}},
    ).json()
    assert second["status"] == "executed", second["rationale"]
    assert any(o["name"] == "Fiber Max" for o in client.get("/api/catalog").json())


def test_approve_this_while_viewing_a_quote(client):
    client.post("/api/intents", json={"text": "Launch a new bundle. name: Fiber 950, composition: Fiber500, segment: B2B-SMB, channel: B2B, price: 1000, discount: 0"})
    opp_id = client.post("/api/intents", json={"text": "Qualify this lead. company_name: CtxCo, source: referral, region: North"}).json()["result"]["opportunity_id"]
    quote_id = client.post(
        "/api/intents", json={"text": f"Configure and quote this bundle. opportunity_id: {opp_id}, offering_name: Fiber 950, quantity: 1"}
    ).json()["result"]["quote_id"]

    body = client.post("/api/intents", json={"text": "approve this", "context": {"entity_type": "quote", "entity_id": quote_id}}).json()
    assert body["intent_type"] == "INT-06"
    assert body["entities"]["quote_id"] == quote_id
    assert body["status"] == "executed", body["rationale"]


def test_context_fills_generic_record_intents_through_the_api(client):
    party_id = client.post("/api/intents", json={"text": "Add this prospect. name: ContextCo"}).json()["result"]["party_id"]
    body = client.post(
        "/api/intents",
        json={"text": "deactivate this", "context": {"entity_type": "party", "entity_id": party_id, "page": "customer"}},
    ).json()
    assert body["intent_type"] == "INT-31"
    assert body["entities"]["entity_id"] == party_id

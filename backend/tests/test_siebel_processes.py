"""Order, quote, promotion, pricing, product and account rules as Siebel
applies them: a blocked account takes no new sales or changes (it can still
be disconnected); an expired quote isn't ordered; breaking a promotion's
commitment costs its penalty; a pricelist prices only while it is in effect,
with volume tiers; a retired product names its replacement."""

import datetime as dt
import json

from test_offering_order_intent import invoke
from test_post_order_scenarios import _asset, _installed, _product

from app.models.comms import CmAccountDetail
from app.models.fulfillment import Asset
from app.models.order_mgmt import OmLine, OmPromotion


def _block(client, party_id, reason="Fraud confirmed"):
    with client.TestSessionLocal() as db:
        detail = db.query(CmAccountDetail).filter_by(party_id=party_id).first()
        if detail is None:
            db.add(CmAccountDetail(tenant_id=1, party_id=party_id, good_standing=False, block_reason=reason))
        else:
            detail.good_standing, detail.block_reason = False, reason
        db.commit()


def test_a_blocked_account_takes_no_new_sale_or_change_but_can_be_disconnected(client):
    asset_id, _, party = _installed(client)
    _block(client, party)
    sale = invoke(client, "INT-106", party_id=party, offering_name="Home Fiber", order_mode="silent")
    assert sale["status"] == "failed" and "account is blocked (Fraud confirmed)" in sale["rationale"], sale
    change = invoke(client, "INT-107", asset_id=asset_id, asset_operation="modify", pricing_attributes={"Bandwidth": "500 Mbps"})
    assert change["status"] == "failed" and "blocked" in change["rationale"]
    assert invoke(client, "INT-107", asset_id=asset_id, asset_operation="suspend")["status"] == "executed"


def test_an_expired_quote_is_not_ordered(client):
    import pytest

    from app.integration import order_adapter
    from app.models.crm import Lead, Opportunity, Quote
    from app.models.order_mgmt import OmQuoteDetail
    from app.models.orders import OrderType

    _, _, party = _installed(client)
    with client.TestSessionLocal() as db:
        # a real opportunity behind the quote (Postgres enforces the link; SQLite never did)
        lead = Lead(tenant_id=1, party_id=party, source="test", owner_name="Test", propensity_score=0.5)
        db.add(lead)
        db.flush()
        opportunity = Opportunity(tenant_id=1, lead_id=lead.id, name="Old deal")
        db.add(opportunity)
        db.flush()
        quote = Quote(tenant_id=1, opportunity_id=opportunity.id, total_price=500)
        db.add(quote)
        db.flush()
        db.add(OmQuoteDetail(tenant_id=1, quote_id=quote.id, quote_number="Q-OLD", effective_from=dt.date(2020, 1, 1),
                             effective_through=dt.date(2020, 1, 31)))
        db.flush()
        with pytest.raises(ValueError, match="Quote Q-OLD expired on 2020-01-31"):
            order_adapter.create_order(db, tenant_id=1, party_id=party, service_account_id=1, quote_id=quote.id,
                                       order_type=OrderType.new, monthly_price=1)
        db.rollback()


def test_breaking_a_promotion_commitment_costs_its_penalty(client):
    asset_id, _, _ = _installed(client)
    with client.TestSessionLocal() as db:
        db.add(OmPromotion(tenant_id=1, code="LOYAL12", name="Loyalty 12", discount_pct=10, commitment_months=12,
                           early_termination_fee=1500, active=True))
        db.commit()
    applied = invoke(client, "INT-107", asset_id=asset_id, asset_operation="promotion_change", promotion="LOYAL12", fulfil_now=True)
    assert applied["status"] == "executed", applied
    promo = _asset(client, asset_id)[1]["promotion"]
    assert promo["commitment_months"] == 12 and promo["start_date"] and promo["commitment_end"] > promo["start_date"]

    waived = invoke(client, "INT-107", asset_id=asset_id, asset_operation="promotion_change", promotion="none", waive_penalty=True)
    assert waived["result"]["penalty_waived"] and waived["result"]["promotion_penalty"] == 0, waived
    with client.TestSessionLocal() as db:  # the waived change is dropped so the asset can take the next one
        from app.models.abo import AssetChange
        db.query(AssetChange).filter_by(asset_id=asset_id, status="submitted").update({"status": "cancelled"})
        db.commit()

    ended = invoke(client, "INT-107", asset_id=asset_id, asset_operation="terminate")
    assert ended["status"] == "executed" and ended["result"]["promotion_penalty"] == 1500, ended
    with client.TestSessionLocal() as db:
        charge = db.query(OmLine).filter_by(context_type="order", context_id=ended["result"]["order_id"],
                                             product_name="Early termination charge").one()
        assert charge.price_type == "one_time" and charge.net_price == 1500 and "Loyalty 12" in charge.note


def test_pricelist_dates_and_volume_tiers(client):
    _product(client, "Biz SIM", 300, bandwidth=False)
    party = invoke(client, "INT-41", name="Volume buyer")["result"]["party_id"]
    made = client.post("/api/admin/price-lists", json={"name": "Volume", "is_default": True, "items": [
        {"offering_name": "Biz SIM", "price": 280}, {"offering_name": "Biz SIM", "price": 250, "min_quantity": 10},
        {"offering_name": "Biz SIM", "price": 220, "min_quantity": 50}]})
    assert made.status_code == 201, made.text
    assert [i["min_quantity"] for i in made.json()["items"]] == [1, 10, 50] and made.json()["in_effect"]

    def ordered(quantity):
        body = invoke(client, "INT-106", party_id=party, offering_name="Biz SIM", order_mode="silent", quantity=quantity)
        assert body["status"] == "executed", body
        with client.TestSessionLocal() as db:
            from app.models.orders import Order
            return db.get(Order, body["result"]["order_id"]).items[0].unit_price

    assert ordered(2) == 280 and ordered(10) == 250 and ordered(60) == 220
    duplicate = client.put(f"/api/admin/price-lists/{made.json()['id']}", json={"name": "Volume", "is_default": True, "items": [
        {"offering_name": "Biz SIM", "price": 280}, {"offering_name": "Biz SIM", "price": 270}]})
    assert duplicate.status_code == 400

    # out of effect: the account falls back to the catalog price
    # the server's date (UTC) decides - a local date is a day ahead of it after midnight in India
    from app.timeutils import utcnow
    yesterday = (utcnow().date() - dt.timedelta(days=1)).isoformat()
    expired = client.put(f"/api/admin/price-lists/{made.json()['id']}", json={"name": "Volume", "is_default": True, "valid_to": yesterday,
                                                                               "items": made.json()["items"]})
    assert expired.status_code == 200 and expired.json()["in_effect"] is False
    assert ordered(10) == 300


def test_a_retired_product_names_its_replacement(client):
    from app.integration import tmf620_adapter
    from app.models.catalog import ProductOffering

    party = invoke(client, "INT-41", name="Retired buyer")["result"]["party_id"]
    _product(client, "Old Fiber", 400, bandwidth=False)
    _product(client, "New Fiber", 450, bandwidth=False)
    with client.TestSessionLocal() as db:
        old = db.query(ProductOffering).filter_by(name="Old Fiber").one()
        new = db.query(ProductOffering).filter_by(name="New Fiber").one()
        tmf620_adapter.retire_offering(db, old.id, replacement_offering_id=new.id, end_date=dt.datetime(2020, 1, 1))
        db.commit()
    body = invoke(client, "INT-07", party_id=party, offering_name="Old Fiber")
    assert body["status"] == "failed" and "Old Fiber is retired - sell its replacement, New Fiber" in body["rationale"], body

"""The whole journey, end to end, on BRT 360's own network inventory:

prospect -> lead -> opportunity -> configured -> sent to Presales -> Presales checks the network for the
site and reserves what the deal needs -> feasibility passed -> quote (discount) -> proposal -> PO ->
match all -> order (the deal's reservation moves to it) -> Order Control approval -> the fulfillment
pattern: COM (billing registration) -> SOM (uses exactly the items Presales reserved) -> TOM work orders,
closed one after another -> network activated (inventory finalised) -> billing started -> service and
installed assets.

Plus the hold's rules: a site no location serves, or a resource that is used up, is not feasible; not
feasible releases the hold; a lost deal's hold expires; Presales' screen reads the same rules."""

import datetime as dt

from tests.test_fulfillment_orchestration import close_next_field_job, tasks_of
from tests.test_inventory import item, sample
from tests.test_sales_to_order import _launch, _opportunity, run

from app.crm_core import inventory_service as inv
from app.models.assurance import WorkOrder
from app.models.crm import Opportunity, OpportunityStage
from app.models.fulfillment import Asset
from app.models.inventory import InvItem
from app.models.orders import Order
from app.orchestration import fulfillment_engine

SITE = "Unit 4, 80 Feet Road, Koramangala, Bangalore 560034"
PRODUCT = "E2E Fibre 1G"


def _pattern_activating_after_the_field(client, product=PRODUCT):
    """The eTOM template, with the network activated only once TOM's work orders are closed."""
    graph = fulfillment_engine.standard_graph()
    graph["edges"] = [e for e in graph["edges"] if e["target"] not in ("s4", "s5")]
    graph["edges"] += [{"id": "e-s5", "source": "s3", "target": "s5"}, {"id": "e-s4", "source": "s5", "target": "s4"}]
    pattern = client.post("/api/admin/fulfillment/patterns", json={"name": "Fibre - activate after install", "products": product,
                                                                   "order_type": "new", "graph": graph}).json()
    assert pattern["validation_errors"] == [], pattern
    assert client.post(f"/api/admin/fulfillment/patterns/{pattern['id']}/activate").status_code == 200


def _deal_with_presales(client, company, site=SITE, product=PRODUCT):
    run(client, "INT-15", name=company, email=f"it@{company.split()[0].lower()}.com")  # the prospect
    opp_id = _opportunity(client, company)  # lead qualified -> opportunity (the prospect is reused)
    assert run(client, "INT-37", opportunity_id=opp_id, offering_name=product)["result"]["stage"] == "configured"
    sent = run(client, "INT-38", opportunity_id=opp_id, address=site, technical_parameters="1 x GPON")
    assert sent["result"]["stage"] == "in_feasibility", sent
    return opp_id


def _held(client, **where):
    db = client.TestSessionLocal()
    try:
        q = db.query(InvItem)
        for k, v in where.items():
            q = q.filter(getattr(InvItem, k) == v)
        return {i.identifier: (i.lifecycle, i.order_id, i.opportunity_id) for i in q}
    finally:
        db.close()


def test_prospect_to_live_service_on_the_recorded_network(client):
    sample(client)
    _launch(client, PRODUCT, price=3000)
    _pattern_activating_after_the_field(client)
    opp_id = _deal_with_presales(client, "Orbit Telecom")

    # ---- Presales: the network for the site, then reserve what the deal needs
    view = client.get(f"/api/inventory/opportunities/{opp_id}").json()
    assert view["in_use"] and view["location"] == "BLR-EX-01" and view["feasible"], view
    assert view["resources_from"] == "the 'Fibre - activate after install' fulfillment pattern"
    assert {c["resource_type"] for c in view["checks"]} == {"ip_block", "vlan", "port", "ont"}
    assert all(c["available"] for c in view["checks"]) and view["holds"] == []
    held = client.post(f"/api/inventory/opportunities/{opp_id}/hold", json={}).json()
    reserved = {h["identifier"]: h["type_code"] for h in held["holds"]}
    assert sorted(reserved.values()) == ["ip_block", "ont", "port", "vlan"] and all(h["lifecycle"] == "reserved" for h in held["holds"])
    port = next(i for i, t in reserved.items() if t == "port")
    assert port.startswith("BLR-EX-01-OLT-01/")  # at the exchange serving the site

    # ---- Presales passes feasibility: the checks ran on the real network, the reservation is kept (not doubled)
    passed = run(client, "INT-18", opportunity_id=opp_id, decision="feasible", notes="GPON port and ONT reserved")
    assert passed["status"] == "executed" and passed["result"]["stage"] == "feasible", passed
    assert sorted(passed["result"]["inventory_held"]) == sorted(f"{t} {i}" for i, t in reserved.items())
    checks = {r["check_type_name"]: r for r in passed["result"]["results"]}
    assert checks["Serving location"]["outcome"] == "passed" and checks["Network Inventory · port"]["detail"] == "already held for this deal"
    assert set(_held(client, opportunity_id=opp_id)) == set(reserved)

    # ---- quote with a discount, proposal, PO, match all -> the order, which takes over the reservation
    quote = run(client, "INT-05", opportunity_id=opp_id, offering_name=PRODUCT, requested_discount_pct=5)
    assert quote["status"] == "executed", quote
    quote_id = quote["result"]["quote_id"]
    assert run(client, "INT-06", quote_id=quote_id, decision="approve")["result"]["status"] == "approved"
    proposal = run(client, "INT-22", quote_id=quote_id)
    assert proposal["status"] == "executed", proposal
    total = client.get(f"/api/quotes/{quote_id}").json()["total_price"]
    run(client, "INT-39", quote_id=quote_id, po_number="PO-ORBIT-1", po_amount=total)
    matched = run(client, "INT-40", quote_id=quote_id)
    assert matched["status"] == "executed", matched["rationale"]
    order_id = matched["result"]["order_id"]
    assert {k: v[:2] for k, v in _held(client, opportunity_id=opp_id).items()} == {i: ("reserved", order_id) for i in reserved}

    # ---- Order Control approves -> the fulfillment pattern: COM -> SOM (Presales' items, nothing new) -> TOM
    approved = run(client, "INT-25", order_id=order_id, decision="approve")
    assert approved["status"] == "executed" and approved["result"]["fulfillment_order"].startswith("COM-"), approved
    som = client.get(f"/api/fulfillment/crm-orders/{order_id}").json()[0]["children"][0]
    given = {r["identifier"] for t in som["tasks"] if t["function"] in ("assign_logical", "reserve_physical") for r in t["response"]["resources"]}
    assert given == set(reserved)  # exactly what Presales reserved
    assert tasks_of(client, "SOM", order_id)[1]["s4"] == "pending"  # activation waits for the field
    assert _held(client, order_id=order_id)[port][0] == "reserved"

    # ---- the work orders, strictly one after another
    db = client.TestSessionLocal()
    try:
        assert db.query(WorkOrder).filter(WorkOrder.order_id == order_id, WorkOrder.kind == "tom", WorkOrder.status != "complete").count() == 1
    finally:
        db.close()
    for expected in ("Installation", "Connectivity test", "Customer acceptance signing"):
        subject, sheet = close_next_field_job(client, order_id)
        assert subject.startswith(expected) and port in sheet  # the job carries the reserved port

    # ---- network activated (inventory finalised) -> billing -> service and assets; the order is complete
    for layer in ("TOM", "SOM", "COM"):
        assert tasks_of(client, layer, order_id)[0] == "completed", layer
    final = _held(client, order_id=order_id)
    assert all(final[i][0] == "in_use" for i in reserved)
    service = next(i for i, v in final.items() if i.startswith("SVC-"))
    assert final[service][0] == "in_use"
    db = client.TestSessionLocal()
    try:
        order = db.get(Order, order_id)
        assert order.status.value == "completed"
        codes = [m.code for m in order.milestones]
        for code in ("order_approved", "fulfillment_com_opened", "fulfillment_som_opened", "fulfillment_tom_opened", "wo_installation_raised",
                     "wo_acceptance_closed", "network_activated", "billing_started", "assets_created", "live"):
            assert code in codes, (code, codes)
        assert codes.index("wo_acceptance_closed") < codes.index("network_activated") < codes.index("billing_started")
        assert db.query(Asset).filter(Asset.service_account_id == order.service_account_id).count() == 1
        party_id = order.party_id
    finally:
        db.close()
    network = client.get(f"/api/c360/parties/{party_id}/network").json()
    assert network["services"][0]["service"] == service and network["services"][0]["crm_assets"]


def test_not_feasible_on_the_network(client):
    sample(client)
    _launch(client, PRODUCT, price=3000)
    _pattern_activating_after_the_field(client)

    # Whitefield is in Bangalore, but no exchange serves it: the region alone gives no exchange's ports
    nowhere = _deal_with_presales(client, "Faraway Co", site="Plot 9, Whitefield, Bangalore 560066")
    view = client.get(f"/api/inventory/opportunities/{nowhere}").json()
    assert view["location"] == "BLR" and not view["feasible"], view
    port_check = next(c for c in view["checks"] if c["resource_type"] == "port")
    assert port_check["available"] is False and port_check["detail"] == "none free at BLR"
    refused = client.post(f"/api/inventory/opportunities/{nowhere}/hold", json={})
    assert refused.status_code == 400 and "no free" in refused.json()["detail"] and "BLR" in refused.json()["detail"]  # (the exchange VLANs / ports)
    assert _held(client, opportunity_id=nowhere) == {}  # all or nothing - no partial hold is left behind
    abroad = _deal_with_presales(client, "Abroad Co", site="1 Marine Drive, Mumbai 400020")
    assert client.get(f"/api/inventory/opportunities/{abroad}").json()["location"] is None  # nothing serves Mumbai at all
    checked = run(client, "INT-18", opportunity_id=nowhere)  # the checks decide
    assert checked["result"]["outcome"] == "failed" and checked["result"]["stage"] == "feasibility_failed", checked
    forced = _deal_with_presales(client, "Faraway Two", site="Plot 9, Whitefield, Bangalore 560066")
    blocked = run(client, "INT-18", opportunity_id=forced, decision="feasible")
    assert blocked["status"] == "failed" and "Can't pass feasibility" in blocked["rationale"]  # feasible means buildable

    # every ONT taken -> the deal at a covered site is not feasible either, and says why
    db = client.TestSessionLocal()
    try:
        for ont in db.query(InvItem).filter(InvItem.identifier.like("ONT-BLR-%")):
            ont.lifecycle = "maintenance"
        db.commit()
    finally:
        db.close()
    short = _deal_with_presales(client, "Short Co")
    ont_check = next(c for c in client.get(f"/api/inventory/opportunities/{short}").json()["checks"] if c["resource_type"] == "ont")
    assert ont_check["available"] is False and "none free" in ont_check["detail"]
    assert run(client, "INT-18", opportunity_id=short)["result"]["outcome"] == "failed"


def test_holds_go_back_when_the_deal_does(client):
    sample(client)
    _launch(client, PRODUCT, price=3000)
    _pattern_activating_after_the_field(client)

    # reserved, then Presales finds it isn't feasible after all: the hold is released
    opp_id = _deal_with_presales(client, "Undecided Co")
    reserved = run(client, "INT-123", opportunity_id=opp_id)  # the intent does what the Reserve button does
    assert reserved["status"] == "executed" and reserved["result"]["location"] == "BLR-EX-01", reserved
    port = next(r["identifier"] for r in reserved["result"]["reserved"] if r["type"] == "port")
    failed = run(client, "INT-18", opportunity_id=opp_id, decision="not_feasible", notes="customer wants a different site")
    assert port in failed["result"]["inventory_released"]
    assert _held(client, identifier=port)[port] == ("available", None, None)

    # a lost deal's hold expires; an open deal's hold is renewed
    lost = _deal_with_presales(client, "Lost Co")
    kept = _deal_with_presales(client, "Open Co")
    for o in (lost, kept):
        assert run(client, "INT-123", opportunity_id=o)["status"] == "executed"
    db = client.TestSessionLocal()
    try:
        db.get(Opportunity, lost).stage = OpportunityStage.lost
        db.commit()
        later = dt.datetime.now(dt.UTC).replace(tzinfo=None) + dt.timedelta(days=inv.RESERVATION_DAYS + 1)
        assert inv.expire_reservations(db, later) == 4  # the lost deal's port, ONT, VLAN and IP block
        db.commit()
    finally:
        db.close()
    assert _held(client, opportunity_id=lost) == {} and len(_held(client, opportunity_id=kept)) == 4

    # Presales can also release by hand (intent or button)
    released = run(client, "INT-124", opportunity_id=kept, note="customer paused")
    assert released["status"] == "executed" and released["result"]["count"] == 4
    said = client.post("/api/intents", json={"text": "Reserve inventory for opportunity 7"}).json()
    assert said["intent_type"] == "INT-123" and said["entities"].get("opportunity_id") == 7

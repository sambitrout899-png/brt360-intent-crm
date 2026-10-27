"""Field Service module: service request verification (entitlement ->
committed date, warranty), activities from templates, engineer ranking and
appointment booking, the engineer's job (time, parts, swap, completion),
charges after cover, invoicing, inventory rules and replenishment,
preventive maintenance triggers, repairs, and older work orders."""

import datetime as dt

from app.crm_core import fs_pm, fs_service
from app.crm_core.fs_calendar import Calendar
from app.models.accounts import ServiceAccount
from app.models.assurance import Technician, TechnicianAssignment, TroubleTicket, WorkOrder
from app.models.crm import Party
from app.models.field_service import FsPmAction, FsServiceRegion
from app.models.orders import BillingAccount
from app.tenancy import DEFAULT_TENANT_SLUG
from app.models.tenant import Tenant
from app.timeutils import utcnow

FS = "/api/field-service"


def _tenant_id(session) -> int:
    return session.query(Tenant).filter(Tenant.slug == DEFAULT_TENANT_SLUG).one().id


def _customer(client, name="Acme Field Co") -> int:
    s = client.TestSessionLocal()
    tid = _tenant_id(s)
    party = Party(tenant_id=tid, name=name)
    s.add(party)
    s.flush()
    ba = BillingAccount(tenant_id=tid, party_id=party.id, status="active")
    s.add(ba)
    s.flush()
    s.add(ServiceAccount(tenant_id=tid, billing_account_id=ba.id, name=f"{name} HQ"))
    s.commit()
    pid = party.id
    s.close()
    return pid


def _setup(client):
    r = client.post(f"{FS}/setup/sample", json={"timezone": "UTC"})
    assert r.status_code == 200, r.text
    return r.json()


def _template(client, name):
    return next(t for t in client.get(f"{FS}/templates").json() if t["name"] == name)


def test_calendar_counts_response_time_inside_service_hours():
    cal = Calendar(8 * 60, 17 * 60, {0, 1, 2, 3, 4})
    friday = dt.datetime(2026, 10, 9, 16, 30)  # a Friday
    assert cal.add_hours(friday, 4) == dt.datetime(2026, 10, 12, 11, 30)  # half an hour Friday, 3.5h Monday
    assert Calendar(always=True).add_hours(friday, 4) == dt.datetime(2026, 10, 9, 20, 30)
    # in a region at UTC+05:30 the hours are local: 16:30 local Friday is 11:00 UTC
    region = FsServiceRegion(name="IN", timezone="UTC+05:30", day_start="08:00", day_end="17:00", work_days="0,1,2,3,4")
    india = Calendar.of(region)
    assert india.add_hours(dt.datetime(2026, 10, 9, 11, 0), 4) == dt.datetime(2026, 10, 12, 6, 0)  # Monday 11:30 local
    # a holiday is skipped
    region.holidays_json = '["2026-10-12"]'
    assert Calendar.of(region).add_hours(dt.datetime(2026, 10, 9, 11, 0), 4) == dt.datetime(2026, 10, 13, 6, 0)


def test_verify_picks_the_best_entitlement_and_finds_the_warranty(client):
    _setup(client)
    pid = _customer(client)
    asset = client.post(f"{FS}/assets", json={"party_id": pid, "product_name": "Router CPE", "serial_number": "RC-1001"}).json()
    assert asset["warranty"]["name"].startswith("Standard equipment warranty"), asset
    silver = client.post(f"{FS}/entitlements", json={"party_id": pid, "name": "Silver care", "response_hours": 8, "coverage": "business"}).json()
    gold = client.post(f"{FS}/entitlements", json={"party_id": pid, "name": "Gold care", "response_hours": 4, "coverage": "24x7",
                                                   "labor_discount_pct": 50}).json()
    other = client.post(f"{FS}/entitlements", json={"party_id": pid, "name": "Other kit only", "response_hours": 1, "product": "Satellite dish"}).json()
    assert silver["current"] and gold["current"] and other["current"]

    sr = client.post(f"{FS}/requests", json={"description": "Internet is down at the office", "party_id": pid, "asset_id": asset["id"]}).json()
    assert sr["verified"] and sr["entitlement_name"] == "Gold care"  # best response time among those that cover the asset
    assert {c["name"] for c in sr["candidates"]} == {"Gold care", "Silver care"}
    opened = dt.datetime.fromisoformat(sr["opened_at"])
    assert dt.datetime.fromisoformat(sr["committed_at"]) == opened + dt.timedelta(hours=4)  # 24x7 cover
    assert sr["warranty_name"].startswith("Standard equipment warranty") and sr["sla"] in ("on_track", "at_risk")

    # the agent may pick the other entitlement: business hours, 8h
    v = client.post(f"{FS}/requests/{sr['id']}/verify", json={"entitlement_id": silver["id"]}).json()
    assert v["entitlement"]["name"] == "Silver care"
    assert client.post(f"{FS}/requests/{sr['id']}/verify", json={"entitlement_id": other["id"]}).status_code == 400


def test_book_execute_and_complete_a_warranty_swap(client):
    _setup(client)
    pid = _customer(client)
    asset = client.post(f"{FS}/assets", json={"party_id": pid, "product_name": "Router CPE", "serial_number": "RC-2001"}).json()
    sr = client.post(f"{FS}/requests", json={"description": "Router keeps rebooting", "party_id": pid, "asset_id": asset["id"]}).json()
    act = client.post(f"{FS}/requests/{sr['id']}/activities", json={"template_id": _template(client, "CPE swap")["id"]}).json()
    assert act["status"] == "unscheduled" and act["skills"] == ["network"] and act["steps_total"] == 4
    assert act["due_at"] == sr["committed_at"]  # activities inherit the committed date

    ranked = client.get(f"{FS}/activities/{act['id']}/candidates").json()
    by_name = {r["name"]: r for r in ranked}
    assert by_name["general"]["qualified"] is False and by_name["general"]["missing_skills"] == ["network"]
    assert ranked[0]["qualified"] and ranked[0]["available"] and ranked[0]["breakdown"]["parts"] == 10.0  # CPE in the trunk

    options = client.get(f"{FS}/activities/{act['id']}/slots").json()
    assert options and all(o["name"] in ("Dev Prakash", "Simran Kaur") for o in options)
    first = options[0]
    booked = client.post(f"{FS}/activities/{act['id']}/book", json={"technician_id": first["technician_id"], "start": first["start"]}).json()
    assert booked["status"] == "scheduled" and booked["technician_name"] == first["name"]

    # a second job for the same engineer at the same time is refused unless forced
    act2 = client.post(f"{FS}/requests/{sr['id']}/activities", json={"subject": "Check the cabling", "skills": []}).json()
    clash = client.post(f"{FS}/activities/{act2['id']}/book", json={"technician_id": first["technician_id"], "start": first["start"]})
    assert clash.status_code == 400 and "busy" in clash.json()["detail"]
    client.post(f"{FS}/activities/{act2['id']}/status", json={"status": "cancelled"})

    # the engineer's day: en route -> on site -> parts, time -> swap -> complete
    assert client.post(f"{FS}/activities/{act['id']}/status", json={"status": "on_site"}).json()["status"] == "on_site"
    trunk_before = client.get(f"{FS}/activities/{act['id']}").json()["trunk"]
    cpe_before = next(l["on_hand"] for l in trunk_before["levels"] if l["product_name"] == "Router CPE" and l["status"] == "good")
    swapped = client.post(f"{FS}/activities/{act['id']}/swap", json={"new_serial": "RC-2002"}).json()
    assert swapped["asset_id"] != asset["id"]
    assert {p["direction"] for p in swapped["parts"]} == {"install", "remove"}
    client.post(f"{FS}/activities/{act['id']}/time", json={"kind": "work", "minutes": 60})
    trunk = swapped["trunk"]
    assert next(l["on_hand"] for l in trunk["levels"] if l["product_name"] == "Router CPE" and l["status"] == "good") == cpe_before - 1
    assert next(l["on_hand"] for l in trunk["levels"] if l["product_name"] == "Router CPE" and l["status"] == "defective") == 1

    new_asset = client.get(f"{FS}/assets/{swapped['asset_id']}").json()
    assert new_asset["serial_number"] == "RC-2002" and new_asset["warranty"] is not None  # the transferable warranty moved with the swap
    assert client.get(f"{FS}/assets/{asset['id']}").json()["status"] == "decommissioned"

    done = client.post(f"{FS}/activities/{act['id']}/complete", json={"resolution_code": "replaced", "signature": "J. Customer"}).json()
    assert done["status"] == "complete" and done["signature"] == "J. Customer"
    lines = done["charges"]["lines"]
    assert {l["kind"] for l in lines} >= {"labor", "part"}
    assert all(l["discount_pct"] == 100 and l["discount_reason"].startswith("Warranty") for l in lines if l["kind"] in ("labor", "part", "travel"))
    assert done["charges"]["net"] == 0

    detail = client.get(f"{FS}/requests/{sr['id']}").json()
    assert detail["status"] == "resolved"  # every activity is done or cancelled
    inv = client.post(f"{FS}/requests/{sr['id']}/invoice").json()
    assert inv["invoiced"] == 0 and inv["waived"] == len(lines)
    assert client.post(f"{FS}/requests/{sr['id']}/close", json={"resolution_code": "fixed"}).json()["status"] == "closed"


def test_entitlement_discounts_and_invoice_to_billing(client):
    _setup(client)
    pid = _customer(client, "Beta Builders")
    asset = client.post(f"{FS}/assets", json={"party_id": pid, "product_name": "SFP module 1G", "serial_number": "SFP-9"}).json()
    assert asset["warranty"] is None  # the starter warranty covers CPE and ONT only
    client.post(f"{FS}/entitlements", json={"party_id": pid, "name": "Business care", "response_hours": 8, "labor_discount_pct": 50})
    sr = client.post(f"{FS}/requests", json={"description": "Link is slow", "party_id": pid, "asset_id": asset["id"]}).json()
    act = client.post(f"{FS}/requests/{sr['id']}/activities", json={"subject": "Replace the SFP", "skills": ["network"]}).json()
    opt = client.get(f"{FS}/activities/{act['id']}/slots").json()[0]
    client.post(f"{FS}/activities/{act['id']}/book", json={"technician_id": opt["technician_id"], "start": opt["start"]})
    client.post(f"{FS}/activities/{act['id']}/status", json={"status": "on_site"})
    # the engineer's trunk holds no SFP: take it from the warehouse
    wh = next(l for l in client.get(f"{FS}/inventory/locations").json() if l["kind"] == "warehouse")
    r = client.post(f"{FS}/activities/{act['id']}/parts", json={"product_name": "SFP module 1G"})
    assert r.status_code == 400 and "available" in r.json()["detail"]
    client.post(f"{FS}/activities/{act['id']}/parts", json={"product_name": "SFP module 1G", "location_id": wh["id"]})
    client.post(f"{FS}/activities/{act['id']}/time", json={"kind": "work", "minutes": 60})
    client.post(f"{FS}/activities/{act['id']}/expenses", json={"kind": "parking", "amount": 10})
    done = client.post(f"{FS}/activities/{act['id']}/complete", json={}).json()
    labor = next(l for l in done["charges"]["lines"] if l["kind"] == "labor")
    part = next(l for l in done["charges"]["lines"] if l["kind"] == "part")
    assert labor["gross"] >= 45 and labor["discount_pct"] == 50 and labor["discount_reason"] == "Entitlement: Business care"
    assert part["gross"] == 65 and part["net"] == 65
    total = done["charges"]["net"]

    s = client.TestSessionLocal()
    before = s.query(BillingAccount).filter(BillingAccount.party_id == pid).one().balance
    s.close()
    inv = client.post(f"{FS}/requests/{sr['id']}/invoice").json()
    assert inv["invoiced"] == total and inv["billing_adjustment_id"]
    s = client.TestSessionLocal()
    assert s.query(BillingAccount).filter(BillingAccount.party_id == pid).one().balance == before + total
    s.close()
    assert client.post(f"{FS}/requests/{sr['id']}/invoice").status_code == 400  # nothing left pending
    ent = client.get(f"{FS}/entitlements?party_id={pid}").json()[0]
    assert ent["visits_used"] == 1


def test_inventory_moves_counts_and_replenishment(client):
    _setup(client)
    locs = client.get(f"{FS}/inventory/locations").json()
    wh = next(l for l in locs if l["kind"] == "warehouse")
    trunk = next(l for l in locs if l["kind"] == "trunk" and l["technician_name"] == "Dev Prakash")
    assert client.post(f"{FS}/inventory/transactions", json={"kind": "consume", "product_name": "Router CPE", "quantity": 5,
                                                             "source_id": trunk["id"]}).status_code == 400  # only 2 in the trunk
    assert client.post(f"{FS}/inventory/transactions", json={"kind": "consume", "product_name": "Router CPE", "quantity": 2,
                                                             "source_id": trunk["id"]}).status_code == 200
    proposals = client.post(f"{FS}/inventory/replenish", json={"location_id": trunk["id"]}).json()
    cpe = next(p for p in proposals if p["product_name"] == "Router CPE")
    assert cpe["source_id"] == wh["id"] and cpe["quantity"] == 3 and not cpe["done"]  # back up to max 3
    client.post(f"{FS}/inventory/replenish", json={"location_id": trunk["id"], "apply": True})
    trunk_now = next(l for l in client.get(f"{FS}/inventory/locations").json() if l["id"] == trunk["id"])
    assert next(l["on_hand"] for l in trunk_now["levels"] if l["product_name"] == "Router CPE") == 3
    # a cycle count corrects the shelf and leaves an adjustment behind
    assert client.post(f"{FS}/inventory/count", json={"location_id": wh["id"], "product_name": "Fiber patch cord", "counted": 150}).json()["adjusted"]
    browser = client.get(f"{FS}/inventory/parts?q=patch").json()
    assert browser[0]["product_name"] == "Fiber patch cord" and any(l["location_name"] == "Central Warehouse" and l["available"] == 150
                                                                  for l in browser[0]["locations"])
    kinds = {t["kind"] for t in client.get(f"{FS}/inventory/transactions").json()}
    assert {"consume", "replenish", "adjust", "transfer"} <= kinds


def test_pm_interval_triggers_follow_the_documented_example(client):
    triggers = [{"type": "interval", "every_days": 1, "valid_start": 0, "valid_end": 2},
                {"type": "interval", "every_days": 3, "valid_start": 2, "valid_end": 7},
                {"type": "interval", "every_days": 5, "valid_start": 7, "valid_end": None}]
    june8 = dt.date(2026, 6, 8)
    first = fs_pm.interval_dates(june8, triggers, None, None, dt.date(2026, 6, 30))
    assert [d.day for d in first] == [9, 11, 14, 18, 23, 28]
    after_done = fs_pm.interval_dates(june8, triggers, dt.date(2026, 6, 26), None, dt.date(2026, 7, 15))
    assert [d.day for d in after_done] == [1, 6, 11]
    after_scheduled = fs_pm.interval_dates(june8, triggers, None, dt.date(2026, 7, 11), dt.date(2026, 7, 30))  # the end day counts
    assert [d.day for d in after_scheduled] == [16, 21, 26]
    assert fs_pm.usage_change(9900, 200, 0, 9999) == 300  # a counter that wrapped


def test_pm_engine_creates_service_requests_and_respects_its_rules(client):
    _setup(client)
    pid = _customer(client, "Gamma Print")
    asset = client.post(f"{FS}/assets", json={"party_id": pid, "product_name": "Copier X1", "serial_number": "CX-1",
                                              "installed_on": "2026-01-01"}).json()
    client.post(f"{FS}/assets/{asset['id']}/measurements", json={"name": "Copies", "kind": "counter", "max_value": 99999})
    temp = client.post(f"{FS}/assets/{asset['id']}/measurements", json={"name": "Temperature", "kind": "gauge", "upper_limit": 45}).json()
    copies_id = next(m["id"] for m in temp["measurements"] if m["name"] == "Copies")
    temp_id = next(m["id"] for m in temp["measurements"] if m["name"] == "Temperature")
    pm_tpl = _template(client, "Preventive maintenance visit")
    plan = client.post(f"{FS}/pm/plans", json={"name": "Copier service", "products": ["Copier X1"], "template_id": pm_tpl["id"],
                                               "triggers": [{"type": "usage", "measurement": "Copies", "every": 3000},
                                                            {"type": "threshold", "measurement": "Temperature", "upper": 45}]}).json()
    assert plan["asset_count"] == 1

    client.post(f"{FS}/measurements/{copies_id}/readings", json={"value": 1000})
    assert client.post(f"{FS}/pm/run", json={}).json()["generated"] == []  # 1000 copies: not due
    client.post(f"{FS}/measurements/{copies_id}/readings", json={"value": 4200})
    out = client.post(f"{FS}/pm/run", json={}).json()["generated"]
    assert len(out) == 1 and out[0]["trigger"] == "usage"
    sr = client.get(f"{FS}/requests/{out[0]['ticket_id']}").json()
    assert sr["source"] == "pm" and sr["activities"][0]["category"] == "preventive_maintenance" and sr["activities"][0]["steps_total"] == 4

    # an outstanding action blocks further usage/threshold triggers - and no trigger fires twice a day
    client.post(f"{FS}/measurements/{temp_id}/readings", json={"value": 60})
    assert client.post(f"{FS}/pm/run", json={}).json()["generated"] == []

    # once that action is done, the hot reading (taken after it) triggers
    s = client.TestSessionLocal()
    action = s.get(FsPmAction, out[0]["action_id"])
    action.status, action.completed_on, action.due_on = "done", dt.date.today() - dt.timedelta(days=2), dt.date.today() - dt.timedelta(days=2)
    action.created_at = action.created_at - dt.timedelta(days=2)
    s.commit()
    s.close()
    again = client.post(f"{FS}/pm/run", json={}).json()["generated"]
    assert [g["trigger"] for g in again] == ["threshold"]


def test_repair_loop_moves_stock(client):
    _setup(client)
    locs = client.get(f"{FS}/inventory/locations").json()
    rc = next(l for l in locs if l["kind"] == "repair_center")
    r = client.post(f"{FS}/repairs", json={"product_name": "ONT fiber modem", "serial_number": "ONT-77", "location_id": rc["id"],
                                           "symptoms": "No optical signal"}).json()
    assert r["repair_number"].startswith("RPR-") and r["status"] == "received" and "ship_to_vendor" in r["actions"]
    assert client.post(f"{FS}/repairs/{r['id']}/action", json={"action": "return_to_customer"}).status_code == 400
    r = client.post(f"{FS}/repairs/{r['id']}/action", json={"action": "ship_to_vendor", "vendor": "OptiFix Ltd"}).json()
    assert r["status"] == "at_vendor" and r["vendor"] == "OptiFix Ltd"
    r = client.post(f"{FS}/repairs/{r['id']}/action", json={"action": "receive_from_vendor"}).json()
    assert r["status"] == "repaired"
    r = client.post(f"{FS}/repairs/{r['id']}/action", json={"action": "return_to_stock"}).json()
    assert r["status"] == "in_stock" and r["closed_at"]
    levels = next(l for l in client.get(f"{FS}/inventory/locations").json() if l["id"] == rc["id"])["levels"]
    assert {(l["status"], l["on_hand"]) for l in levels if l["product_name"] == "ONT fiber modem"} == {("good", 1), ("defective", 0)}
    assert client.get(f"{FS}/repairs").json() == []  # nothing open


def test_older_work_orders_show_up_and_fulfillment_ones_close_from_the_order(client):
    s = client.TestSessionLocal()
    tid = _tenant_id(s)
    ticket = TroubleTicket(tenant_id=tid, category="connectivity_high", severity="high", priority="P2", description="Fiber cut")
    s.add(ticket)
    s.flush()
    wo = WorkOrder(tenant_id=tid, ticket_id=ticket.id, scheduled_window="next business day", status="en_route")
    s.add(wo)
    s.flush()
    tech = s.query(Technician).filter(Technician.tenant_id == tid, Technician.name == "Dev Prakash").one()
    s.add(TechnicianAssignment(work_order_id=wo.id, technician_id=tech.id))
    install = WorkOrder(tenant_id=tid, kind="installation", status="scheduled", scheduled_window="")
    s.add(install)
    s.commit()
    wo_id, install_id = wo.id, install.id
    s.close()

    acts = client.get(f"{FS}/activities").json()
    legacy = next(a for a in acts if a["work_order_id"] == wo_id)
    assert legacy["technician_name"] == "Dev Prakash" and legacy["status"] == "en_route" and legacy["subject"] == "Fiber cut"
    fulfil = next(a for a in acts if a["work_order_id"] == install_id)
    assert fulfil["close_intent"].startswith("Close Work Order 1 (Installation)")
    refused = client.post(f"{FS}/activities/{fulfil['id']}/complete", json={})
    assert refused.status_code == 400 and "order" in refused.json()["detail"]
    board = client.get(f"{FS}/board").json()
    assert any(u["work_order_id"] == install_id for u in board["unscheduled"]) and board["rows"]
    # the legacy job completes in the field service flow like any other
    done = client.post(f"{FS}/activities/{legacy['id']}/complete", json={"resolution_code": "spliced"}).json()
    assert done["status"] == "complete"
    s = client.TestSessionLocal()
    assert s.get(TroubleTicket, s.get(WorkOrder, wo_id).ticket_id).status == "resolved"
    s.close()


def test_auto_schedule_books_urgent_work_first_and_skips_absent_engineers(client):
    _setup(client)
    pid = _customer(client, "Delta Retail")
    sr = client.post(f"{FS}/requests", json={"description": "Office internet outage", "party_id": pid}).json()
    low = client.post(f"{FS}/requests/{sr['id']}/activities", json={"subject": "Tidy the rack", "priority": 4, "skills": []}).json()
    urgent = client.post(f"{FS}/requests/{sr['id']}/activities", json={"subject": "Restore service", "priority": 1, "skills": ["network"]}).json()
    techs = {e["name"]: e for e in client.get(f"{FS}/engineers").json()}
    start = utcnow() - dt.timedelta(days=1)
    client.post(f"{FS}/engineers/{techs['Simran Kaur']['technician_id']}/absences",
                json={"starts_at": start.isoformat(), "ends_at": (start + dt.timedelta(days=40)).isoformat(), "reason": "Leave"})
    result = client.post(f"{FS}/auto-schedule", json={"days": 10}).json()
    order = [b["activity_id"] for b in result["booked"]]
    assert order.index(urgent["id"]) < order.index(low["id"])
    assert all(b["technician_name"] != "Simran Kaur" for b in result["booked"])
    got = client.get(f"{FS}/activities/{urgent['id']}").json()
    assert got["technician_name"] == "Dev Prakash" and got["status"] == "scheduled"
    # unassign puts it back on the unscheduled list
    back = client.post(f"{FS}/activities/{urgent['id']}/unassign").json()
    assert back["status"] == "unscheduled" and back["technician_name"] is None

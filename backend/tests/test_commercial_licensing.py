"""Commercial licensing: the platform admin's catalog and prices, an organization's subscription
(edition, lines, price, status), seats and per-user services, invoices, requests and the lifecycle
that closes modules when a contract lapses. Tenants without a subscription behave as before."""

import datetime as dt

from app.crm_core import subscription_service as subs
from app.models.auth import User
from app.models.tenant import Tenant
from tests.test_tenant_management import _as_platform_admin

P = "/api/platform/licensing"


def _tenant_id(client):
    with client.TestSessionLocal() as db:
        return db.query(Tenant).filter(Tenant.slug == "default").one().id


def _admin_id(client):
    with client.TestSessionLocal() as db:
        return db.query(User).filter(User.email == "test-admin@default.local").one().id


def _subscribe(client, pa, tid, **extra):
    body = {"edition_code": "starter", "currency": "INR", "billing_cycle": "annual", **extra}
    resp = client.put(f"{P}/tenants/{tid}", json=body, headers=pa)
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_catalog_is_the_platform_admins_and_prices_are_editable(client):
    pa = _as_platform_admin(client)
    assert client.get(f"{P}/catalog").status_code == 403  # a tenant admin can't see or change the catalog
    cat = client.get(f"{P}/catalog", headers=pa).json()
    codes = {p["code"] for p in cat["products"]}
    assert {"core_platform", "core_user", "telecom_crm", "training_management", "flow_studio", "ai_credits"} <= codes
    assert {e["code"] for e in cat["editions"]} >= {"starter", "professional", "enterprise"}
    assert cat["settings"]["default_currency"] == "INR"

    changed = client.put(f"{P}/products/telecom_crm", json={"prices": {"INR": 2800, "USD": 32}, "annual_discount_pct": 15}, headers=pa)
    assert changed.status_code == 200 and changed.json()["prices"] == {"INR": 2800.0, "USD": 32.0}
    assert client.put(f"{P}/products/telecom_crm", json={"prices": {"EUR": 30}}, headers=pa).status_code == 400  # not a platform currency
    assert client.put(f"{P}/settings", json={"currencies": ["INR", "USD", "EUR"]}, headers=pa).status_code == 200
    assert client.put(f"{P}/products/telecom_crm", json={"prices": {"INR": 2800, "EUR": 30}}, headers=pa).status_code == 200
    assert client.put(f"{P}/products/telecom_crm", json={"modules": ["nope"]}, headers=pa).status_code == 400

    new = client.post(f"{P}/products", json={"code": "wfm", "name": "Workforce Management", "kind": "service", "unit": "user",
                                             "modules": [], "prices": {"INR": 900}, "requires": ["core_user"]}, headers=pa)
    assert new.status_code == 200, new.text
    bad_edition = client.post(f"{P}/editions", json={"code": "broken", "name": "Broken", "items": [{"product": "telecom_crm", "quantity": 5}]}, headers=pa)
    assert bad_edition.status_code == 400 and "Core platform" in bad_edition.json()["detail"]


def test_subscription_prices_grants_and_seats(client):
    pa, tid = _as_platform_admin(client), _tenant_id(client)
    sub = _subscribe(client, pa, tid)
    assert sub["status"] == "trial" and sub["edition_code"] == "starter" and sub["modules"] == ["telecom_crm"]
    price = sub["pricing"]
    # annual: 10% off list on the catalog products that have it; AI credits have none
    assert price["mrr"] == 25000 * 0.9 + 10 * 1200 * 0.9 + 10 * 2500 * 0.9 + 2000
    assert price["arr"] == round(price["mrr"] * 12, 2) and price["tax_pct"] == 18.0
    assert price["total_monthly"] == round(price["mrr"] * 1.18, 2)

    # a negotiated price and a contract discount
    sub = client.put(f"{P}/tenants/{tid}/lines", json={"product_code": "telecom_crm", "quantity": 10, "unit_price": 2000}, headers=pa).json()
    tele = next(line for line in sub["pricing"]["lines"] if line["product"] == "telecom_crm")
    assert tele["overridden"] and tele["monthly"] == 20000.0  # an override isn't discounted again for the annual term
    sub = client.put(f"{P}/tenants/{tid}", json={"discount_pct": 10}, headers=pa).json()
    assert sub["pricing"]["discount_monthly"] == round(sub["pricing"]["subtotal_monthly"] * 0.1, 2)

    # activating fills seats for the existing users
    active = client.post(f"{P}/tenants/{tid}/status", json={"status": "active"}, headers=pa).json()
    assert active["status"] == "active" and active["term_end"]
    core = next(s for s in active["seats"] if s["product"] == "core_user")
    assert core["assigned"] >= 1 and core["purchased"] == 10

    # the organization's administrator sees it - without internal notes - and its users and licences
    mine = client.get("/api/licensing").json()
    assert mine["subscription"]["status"] == "active" and "notes" not in mine["subscription"]
    me = next(u for u in mine["users"] if u["email"] == "test-admin@default.local")
    assert set(me["licenses"]) == {"core_user", "telecom_crm"}
    assert any(lim["key"] == "users" for lim in mine["limits"])


def test_per_user_service_needs_a_seat_and_new_users_need_a_core_licence(client):
    pa, tid, admin = _as_platform_admin(client), _tenant_id(client), _admin_id(client)
    _subscribe(client, pa, tid)
    client.post(f"{P}/tenants/{tid}/status", json={"status": "active"}, headers=pa)
    assert client.get("/api/admin/product-model").status_code == 200

    # without a Telecom CRM seat this user can't use Telecom CRM - the organization still has it
    assert client.delete(f"/api/licensing/seats/{admin}/telecom_crm").status_code == 200
    blocked = client.get("/api/admin/product-model")
    assert blocked.status_code == 403 and "licence" in blocked.json()["detail"]
    modules = {m["key"]: m for m in client.get("/api/modules").json()}
    assert modules["telecom_crm"]["organization"] and not modules["telecom_crm"]["enabled"]
    assert client.post("/api/licensing/seats", json={"user_id": admin, "product_code": "telecom_crm"}).status_code == 200
    assert client.get("/api/admin/product-model").status_code == 200

    # lowering seats below what is assigned is refused; at the limit a new user is refused
    with client.TestSessionLocal() as db:
        used = db.query(User).filter(User.tenant_id == tid, User.is_active.is_(True)).count()
    low = client.put(f"{P}/tenants/{tid}/lines", json={"product_code": "core_user", "quantity": max(used - 1, 0)}, headers=pa)
    assert low.status_code == 400 and "assigned" in low.json()["detail"]
    client.put(f"{P}/tenants/{tid}/lines", json={"product_code": "telecom_crm", "quantity": used}, headers=pa)
    assert client.put(f"{P}/tenants/{tid}/lines", json={"product_code": "core_user", "quantity": used}, headers=pa).status_code == 200
    refused = client.post("/api/admin/users", json={"email": "extra@default.local", "password": "password123", "name": "Extra", "role": "agent"})
    assert refused.status_code == 400 and "Core user licences are in use" in refused.json()["detail"]
    client.put(f"{P}/tenants/{tid}/lines", json={"product_code": "core_user", "quantity": used + 1}, headers=pa)
    ok = client.post("/api/admin/users", json={"email": "extra@default.local", "password": "password123", "name": "Extra", "role": "agent"})
    assert ok.status_code == 201, ok.text
    seats = {s["product"]: s for s in client.get("/api/licensing").json()["subscription"]["seats"]}
    assert seats["core_user"]["free"] == 0
    # deactivating releases the seats
    client.patch(f"/api/admin/users/{ok.json()['id']}/deactivate")
    seats = {s["product"]: s for s in client.get("/api/licensing").json()["subscription"]["seats"]}
    assert seats["core_user"]["free"] == 1


def test_suspension_closes_modules_and_reactivation_opens_them(client):
    pa, tid = _as_platform_admin(client), _tenant_id(client)
    _subscribe(client, pa, tid)
    client.post(f"{P}/tenants/{tid}/status", json={"status": "active"}, headers=pa)
    suspended = client.post(f"{P}/tenants/{tid}/status", json={"status": "suspended", "reason": "unpaid"}, headers=pa).json()
    assert suspended["modules"] == [] and not suspended["granting"]
    assert client.get("/api/admin/product-model").status_code == 403
    assert client.post(f"{P}/tenants/{tid}/status", json={"status": "expired"}, headers=pa).status_code == 400  # not an allowed move
    client.post(f"{P}/tenants/{tid}/status", json={"status": "active"}, headers=pa)
    assert client.get("/api/admin/product-model").status_code == 200


def test_invoices_requests_and_history(client):
    pa, tid = _as_platform_admin(client), _tenant_id(client)
    _subscribe(client, pa, tid, billing_cycle="quarterly")
    client.post(f"{P}/tenants/{tid}/status", json={"status": "active"}, headers=pa)
    inv = client.post(f"{P}/tenants/{tid}/invoices", headers=pa).json()
    assert inv["number"].startswith("BRT-INV-") and inv["status"] == "issued" and inv["lines"][0]["months"] == 3
    assert inv["total"] == round(inv["subtotal"] - inv["discount"] + inv["tax"], 2)
    second = client.post(f"{P}/tenants/{tid}/invoices", headers=pa).json()
    assert dt.date.fromisoformat(second["period_start"]) == dt.date.fromisoformat(inv["period_end"]) + dt.timedelta(days=1)
    paid = client.post(f"{P}/invoices/{inv['id']}/status", json={"status": "paid", "payment_ref": "NEFT-1"}, headers=pa).json()
    assert paid["status"] == "paid" and paid["payment_ref"] == "NEFT-1"

    # the organization asks for more seats; approving adds them to the contract
    mine = client.post("/api/licensing/requests", json={"kind": "seats", "product_code": "telecom_crm", "quantity": 5, "message": "new team"}).json()
    req = mine["requests"][0]
    assert req["status"] == "open"
    client.post(f"{P}/requests/{req['id']}", json={"approve": True, "response": "done"}, headers=pa)
    seats = {s["product"]: s for s in client.get("/api/licensing").json()["subscription"]["seats"]}
    assert seats["telecom_crm"]["purchased"] == 15
    history = client.get(f"{P}/tenants/{tid}", headers=pa).json()["events"]
    assert any(e["action"] == "request" for e in history) and any(e["action"] == "invoice" for e in history)
    overview = client.get(f"{P}/overview", headers=pa).json()
    assert overview["mrr"]["INR"] > 0 and overview["by_status"]["active"] == 1


def test_lifecycle_lapses_renews_and_suspends_for_overdue_invoices(client):
    pa, tid = _as_platform_admin(client), _tenant_id(client)
    _subscribe(client, pa, tid)
    client.post(f"{P}/tenants/{tid}/status", json={"status": "active"}, headers=pa)
    client.put(f"{P}/tenants/{tid}", json={"auto_renew": False, "grace_days": 10}, headers=pa)
    with client.TestSessionLocal() as db:
        sub = subs.subscription(db, tid)
        end = sub.term_end
        assert subs.advance(db, sub, on=end + dt.timedelta(days=1)) == "past_due"
        assert db.get(Tenant, tid).licensed_modules == ["telecom_crm"]  # still working in the grace period
        assert subs.advance(db, sub, on=end + dt.timedelta(days=11)) == "expired"
        assert db.get(Tenant, tid).licensed_modules == []
        db.commit()
    # renewal brings it back
    renewed = client.post(f"{P}/tenants/{tid}/renew", headers=pa).json()
    assert renewed["status"] == "active" and renewed["modules"] == ["telecom_crm"]

    # an overdue invoice: past due, then suspended after the grace period, active again once paid
    client.put(f"{P}/tenants/{tid}", json={"auto_renew": True}, headers=pa)
    inv = client.post(f"{P}/tenants/{tid}/invoices", headers=pa).json()
    with client.TestSessionLocal() as db:
        sub = subs.subscription(db, tid)
        due = dt.date.fromisoformat(inv["due_on"])
        assert subs.advance(db, sub, on=due + dt.timedelta(days=1)) == "past_due"
        assert subs.advance(db, sub, on=due + dt.timedelta(days=11)) == "suspended"
        db.commit()
    client.post(f"{P}/invoices/{inv['id']}/status", json={"status": "paid"}, headers=pa)
    assert client.get("/api/licensing").json()["subscription"]["status"] == "active"


def test_a_tenant_without_a_subscription_is_unchanged(client):
    mine = client.get("/api/licensing").json()
    assert mine["subscription"] is None and "telecom_crm" in mine["modules"]
    assert client.get("/api/admin/product-model").status_code == 200
    ok = client.post("/api/admin/users", json={"email": "free@default.local", "password": "password123", "name": "Free", "role": "agent"})
    assert ok.status_code == 201

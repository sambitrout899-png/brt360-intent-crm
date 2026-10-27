def _approved_quote(client, offering_name="IPLC Circuit", price=5000, company="Multinational Corp"):
    client.post(
        "/api/intents",
        json={
            "text": f"Launch a new bundle. name: {offering_name}, composition: Fiber1000, "
            f"segment: B2B-Wholesale, channel: B2B, price: {price}, discount: 0"
        },
    )
    opp_id = client.post(
        "/api/intents",
        json={"text": f"Qualify this lead. company_name: {company}, source: referral, region: North"},
    ).json()["result"]["opportunity_id"]
    quote_id = client.post(
        "/api/intents",
        json={"text": f"Configure and quote this bundle. opportunity_id: {opp_id}, offering_name: {offering_name}"},
    ).json()["result"]["quote_id"]
    client.post("/api/intents", json={"text": f"Approve this quote. quote_id: {quote_id}"})
    return quote_id


def test_multi_site_activation_leaves_order_activating(client):
    quote_id = _approved_quote(client, company="MultiSiteCo1")
    resp = client.post(
        "/api/intents", json={"text": f"Activate this order end-to-end. quote_id: {quote_id}, sites: A-End+Z-End"}
    )
    body = resp.json()
    assert body["status"] == "executed"
    assert body["result"]["status"] == "activating"
    assert body["result"]["sites"] == ["A-End", "Z-End"]
    order_id = body["result"]["order_id"]

    order = client.get(f"/api/orders/{order_id}").json()
    assert order["status"] == "activating"
    assert len(order["service_orders"]) == 2
    assert all(so["status"] == "pending" for so in order["service_orders"])


def test_provisioning_each_site_eventually_activates_order(client):
    quote_id = _approved_quote(client, company="MultiSiteCo2")
    order_id = client.post(
        "/api/intents", json={"text": f"Activate this order end-to-end. quote_id: {quote_id}, sites: A-End+Z-End"}
    ).json()["result"]["order_id"]

    first = client.post(
        "/api/intents", json={"text": f"Provision this site. order_id: {order_id}, site_label: A-End"}
    ).json()
    assert first["status"] == "executed"
    assert first["result"]["all_sites_provisioned"] is False
    assert first["result"]["order_status"] == "activating"

    order_mid = client.get(f"/api/orders/{order_id}").json()
    assert order_mid["status"] == "activating"
    assert {so["status"] for so in order_mid["service_orders"]} == {"active", "pending"}

    second = client.post(
        "/api/intents", json={"text": f"Provision this site. order_id: {order_id}, site_label: Z-End"}
    ).json()
    assert second["result"]["all_sites_provisioned"] is True
    assert second["result"]["order_status"] == "completed"

    order_final = client.get(f"/api/orders/{order_id}").json()
    assert order_final["status"] == "completed"
    assert all(so["status"] == "active" for so in order_final["service_orders"])


def test_provision_unknown_site_label_needs_review(client):
    quote_id = _approved_quote(client, company="MultiSiteCo3")
    order_id = client.post(
        "/api/intents", json={"text": f"Activate this order end-to-end. quote_id: {quote_id}, sites: A-End+Z-End"}
    ).json()["result"]["order_id"]

    resp = client.post(
        "/api/intents", json={"text": f"Provision this site. order_id: {order_id}, site_label: Q-End"}
    )
    assert resp.json()["status"] == "needs_review"


def test_provision_already_active_site_fails(client):
    quote_id = _approved_quote(client, company="MultiSiteCo4")
    order_id = client.post(
        "/api/intents", json={"text": f"Activate this order end-to-end. quote_id: {quote_id}, sites: A-End+Z-End"}
    ).json()["result"]["order_id"]

    client.post("/api/intents", json={"text": f"Provision this site. order_id: {order_id}, site_label: A-End"})
    resp = client.post("/api/intents", json={"text": f"Provision this site. order_id: {order_id}, site_label: A-End"})
    assert resp.json()["status"] == "failed"


def test_single_site_default_still_activates_immediately(client):
    quote_id = _approved_quote(client, company="SingleSiteCo")
    resp = client.post("/api/intents", json={"text": f"Activate this order end-to-end. quote_id: {quote_id}"})
    body = resp.json()
    assert body["result"]["status"] == "completed"
    order = client.get(f"/api/orders/{body['result']['order_id']}").json()
    assert len(order["service_orders"]) == 1
    assert order["service_orders"][0]["status"] == "active"


def test_int14_blocked_for_b2c_only_tenant(client):
    db = client.TestSessionLocal()
    try:
        from app.auth import hash_password
        from app.models.auth import User, UserRole
        from app.models.crm import Party
        from app.models.orders import Order, ServiceOrder
        from app.models.accounts import ServiceAccount
        from app.models.orders import BillingAccount
        from app.models.tenant import Tenant, TenantVertical

        b2c_tenant = Tenant(slug="b2c-only", name="B2C Only Co", vertical=TenantVertical.b2c)
        db.add(b2c_tenant)
        db.flush()
        party = Party(tenant_id=b2c_tenant.id, name="B2COnlyParty", party_type="organization")
        db.add(party)
        db.flush()
        billing_account = BillingAccount(tenant_id=b2c_tenant.id, party_id=party.id, status="active")
        db.add(billing_account)
        db.flush()
        service_account = ServiceAccount(tenant_id=b2c_tenant.id, billing_account_id=billing_account.id, name="SA")
        db.add(service_account)
        db.flush()
        order = Order(
            tenant_id=b2c_tenant.id,
            party_id=party.id,
            service_account_id=service_account.id,
            monthly_price=100,
        )
        db.add(order)
        db.flush()
        so = ServiceOrder(order_id=order.id, site_label="A-End")
        db.add(so)
        user = User(
            tenant_id=b2c_tenant.id,
            email="admin@b2c-only.test",
            password_hash=hash_password("password123"),
            name="B2C Admin",
            role=UserRole.admin,
        )
        db.add(user)
        db.commit()
        order_id = order.id
        user_id = user.id
    finally:
        db.close()

    resp = client.post(
        "/api/intents",
        json={"text": f"Provision this site. order_id: {order_id}, site_label: A-End"},
        headers={"X-Test-User-Id": str(user_id)},
    )
    body = resp.json()
    assert body["status"] == "needs_review"
    assert "B2C only" in body["rationale"]

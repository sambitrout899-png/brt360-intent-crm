from tests.conftest import DEFAULT_TEST_PLATFORM_ADMIN_EMAIL


def _platform_admin_id(client):
    db = client.TestSessionLocal()
    try:
        from app.models.auth import User

        return db.query(User).filter(User.email == DEFAULT_TEST_PLATFORM_ADMIN_EMAIL).one().id
    finally:
        db.close()


def _as_platform_admin(client):
    return {"X-Test-User-Id": str(_platform_admin_id(client))}


def test_regular_admin_cannot_list_tenants(client):
    resp = client.get("/api/platform/tenants")
    assert resp.status_code == 403


def test_list_tenants_includes_default(client):
    tenants = client.get("/api/platform/tenants", headers=_as_platform_admin(client)).json()
    slugs = {t["slug"] for t in tenants}
    assert "default" in slugs


def test_create_tenant_seeds_reference_data_and_first_admin(client):
    resp = client.post(
        "/api/platform/tenants",
        json={
            "name": "Acme Telco",
            "slug": "acme-telco",
            "vertical": "b2b",
            "admin_email": "admin@acme-telco.test",
            "admin_password": "correct-horse-battery",
        },
        headers=_as_platform_admin(client),
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["slug"] == "acme-telco"
    assert body["vertical"] == "b2b"

    db = client.TestSessionLocal()
    try:
        from app.models.auth import User
        from app.models.approval import ApprovalChainTemplate
        from app.models.catalog import ProductSpecification
        from app.models.identity import IdentityVerificationProvider

        new_admin = db.query(User).filter(User.email == "admin@acme-telco.test").one()
        assert new_admin.tenant_id == body["id"]
        assert new_admin.role.value == "admin"

        assert db.query(ProductSpecification).filter(ProductSpecification.tenant_id == body["id"]).count() > 0
        assert db.query(ApprovalChainTemplate).filter(ApprovalChainTemplate.tenant_id == body["id"]).count() > 0
        assert db.query(IdentityVerificationProvider).filter(IdentityVerificationProvider.tenant_id == body["id"]).count() > 0
    finally:
        db.close()


def test_create_tenant_rejects_duplicate_slug(client):
    headers = _as_platform_admin(client)
    payload = {"name": "First", "slug": "dupe-slug", "vertical": "both", "admin_email": "a@dupe.test", "admin_password": "password123"}
    client.post("/api/platform/tenants", json=payload, headers=headers)
    payload["admin_email"] = "b@dupe.test"
    resp = client.post("/api/platform/tenants", json=payload, headers=headers)
    assert resp.status_code == 400


def test_create_tenant_rejects_invalid_vertical(client):
    resp = client.post(
        "/api/platform/tenants",
        json={
            "name": "Bad Vertical",
            "slug": "bad-vertical",
            "vertical": "nonsense",
            "admin_email": "a@bad-vertical.test",
            "admin_password": "password123",
        },
        headers=_as_platform_admin(client),
    )
    assert resp.status_code in (400, 422)


def test_new_tenant_user_is_fully_isolated_from_default(client):
    client.post(
        "/api/platform/tenants",
        json={
            "name": "Isolated Co",
            "slug": "isolated-co",
            "vertical": "both",
            "admin_email": "admin@isolated-co.test",
            "admin_password": "password123",
        },
        headers=_as_platform_admin(client),
    )

    client.post(
        "/api/intents",
        json={"text": "Qualify this lead. company_name: DefaultTenantParty, source: referral, region: North"},
    )

    db = client.TestSessionLocal()
    try:
        from app.models.auth import User

        isolated_admin_id = db.query(User).filter(User.email == "admin@isolated-co.test").one().id
    finally:
        db.close()

    isolated_parties = client.get("/api/parties", headers={"X-Test-User-Id": str(isolated_admin_id)}).json()
    assert all(p["name"] != "DefaultTenantParty" for p in isolated_parties)


def test_uppercase_slug_is_normalized_before_duplicate_check(client):
    payload = {'name': 'CWMA Training', 'slug': ' CWMA-TRAINING ',
               'admin_email': 'cwma@example.test', 'admin_password': 'password123',
               'licensed_modules': ['training_management']}
    headers = _as_platform_admin(client)
    response = client.post('/api/platform/tenants', json=payload, headers=headers)
    assert response.status_code == 201, response.text
    assert response.json()['slug'] == 'cwma-training'
    payload.update(slug='CWMA-TRAINING', admin_email='second@example.test')
    assert client.post('/api/platform/tenants', json=payload, headers=headers).status_code == 400
    payload['slug'] = 'cwma training'
    assert client.post('/api/platform/tenants', json=payload, headers=headers).status_code == 422

def test_list_tenants_includes_default(client):
    tenants = client.get("/api/platform/tenants").json()
    slugs = {t["slug"] for t in tenants}
    assert "default" in slugs


def test_create_tenant_seeds_reference_data_immediately(client):
    resp = client.post("/api/platform/tenants", json={"name": "Acme Telco", "slug": "acme-telco", "vertical": "b2b"})
    assert resp.status_code == 201
    body = resp.json()
    assert body["slug"] == "acme-telco"
    assert body["vertical"] == "b2b"

    # Seeded reference data (product specs, approval chains, identity
    # providers) should already exist for this brand-new tenant, visible
    # by switching the active tenant header.
    specs = client.get("/api/admin/product-specifications", headers={"X-Tenant-Slug": "acme-telco"}).json()
    assert len(specs) > 0
    chains = client.get("/api/admin/approval-chains", headers={"X-Tenant-Slug": "acme-telco"}).json()
    assert len(chains) > 0
    providers = client.get("/api/admin/identity-providers", headers={"X-Tenant-Slug": "acme-telco"}).json()
    assert len(providers) > 0


def test_create_tenant_rejects_duplicate_slug(client):
    client.post("/api/platform/tenants", json={"name": "First", "slug": "dupe-slug", "vertical": "both"})
    resp = client.post("/api/platform/tenants", json={"name": "Second", "slug": "dupe-slug", "vertical": "both"})
    assert resp.status_code == 400


def test_create_tenant_rejects_invalid_vertical(client):
    resp = client.post("/api/platform/tenants", json={"name": "Bad Vertical", "slug": "bad-vertical", "vertical": "nonsense"})
    assert resp.status_code in (400, 422)


def test_new_tenant_is_fully_isolated_from_default(client):
    client.post("/api/platform/tenants", json={"name": "Isolated Co", "slug": "isolated-co", "vertical": "both"})

    client.post(
        "/api/intents",
        json={"text": "Qualify this lead. company_name: DefaultTenantParty, source: referral, region: North"},
    )
    isolated_parties = client.get("/api/parties", headers={"X-Tenant-Slug": "isolated-co"}).json()
    assert all(p["name"] != "DefaultTenantParty" for p in isolated_parties)

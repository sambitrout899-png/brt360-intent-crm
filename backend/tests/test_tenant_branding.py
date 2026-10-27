"""Platform admin edits a tenant and its branding; the tenant's users see it."""

import base64

from tests.test_tenant_management import _as_platform_admin

PNG = "data:image/png;base64," + base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"\x00" * 64).decode()


def _default_tenant_id(client):
    tenants = client.get("/api/platform/tenants", headers=_as_platform_admin(client)).json()
    return next(t["id"] for t in tenants if t["slug"] == "default")


def test_platform_admin_edits_tenant_and_branding_reaches_its_users(client):
    tenant_id = _default_tenant_id(client)
    before = client.get("/api/auth/branding").json()
    assert before["display_name"] == before["name"] and before["logo_data_url"] is None

    resp = client.put(
        f"/api/platform/tenants/{tenant_id}",
        json={"name": "Salam Telecom", "display_name": "Salam", "vertical": "b2b", "brand_color": "#0ea5e9", "logo_data_url": PNG},
        headers=_as_platform_admin(client),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["name"] == "Salam Telecom" and body["vertical"] == "b2b" and body["slug"] == "default"

    seen = client.get("/api/auth/branding").json()  # as the tenant's own admin
    assert seen == {"name": "Salam Telecom", "display_name": "Salam", "logo_data_url": PNG, "brand_color": "#0ea5e9"}

    # removing the logo and the display name falls back to the plain name
    client.put(f"/api/platform/tenants/{tenant_id}", json={"logo_data_url": "", "display_name": ""}, headers=_as_platform_admin(client))
    seen = client.get("/api/auth/branding").json()
    assert seen["logo_data_url"] is None and seen["display_name"] == "Salam Telecom"


def test_branding_input_is_validated(client):
    tenant_id = _default_tenant_id(client)
    headers = _as_platform_admin(client)
    bad_type = client.put(f"/api/platform/tenants/{tenant_id}", json={"logo_data_url": "data:text/html;base64,PHNjcmlwdD4="}, headers=headers)
    assert bad_type.status_code == 400 and "PNG" in bad_type.text
    too_big = "data:image/png;base64," + base64.b64encode(b"\x00" * (600 * 1024)).decode()
    assert client.put(f"/api/platform/tenants/{tenant_id}", json={"logo_data_url": too_big}, headers=headers).status_code == 400
    assert client.put(f"/api/platform/tenants/{tenant_id}", json={"brand_color": "blue"}, headers=headers).status_code == 400
    assert client.put(f"/api/platform/tenants/{tenant_id}", json={"vertical": "b2x"}, headers=headers).status_code == 400


def test_only_the_platform_admin_can_edit_tenants(client):
    tenant_id = _default_tenant_id(client)
    assert client.put(f"/api/platform/tenants/{tenant_id}", json={"name": "Hijack"}).status_code == 403

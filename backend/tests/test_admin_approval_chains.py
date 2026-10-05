def test_list_seeded_approval_chains(client):
    resp = client.get("/api/admin/approval-chains")
    assert resp.status_code == 200
    templates = resp.json()
    assert len(templates) == 4
    assert templates[0]["name"] == "Auto-Approved"
    assert templates[-1]["tiers"] == [
        {"sequence": 1, "approver_tier": "VP Sales"},
        {"sequence": 2, "approver_tier": "CFO"},
    ]


def test_create_and_deactivate_approval_chain(client):
    create_resp = client.post(
        "/api/admin/approval-chains",
        json={
            "name": "Wholesale Carrier Escalation",
            "segment": "B2B-Wholesale",
            "min_discount_pct": 0,
            "max_discount_pct": 100,
            "tiers": [{"approver_tier": "Carrier Relations Lead"}, {"approver_tier": "COO"}],
        },
    )
    assert create_resp.status_code == 201
    body = create_resp.json()
    assert body["segment"] == "B2B-Wholesale"
    assert [t["approver_tier"] for t in body["tiers"]] == ["Carrier Relations Lead", "COO"]
    assert body["is_active"] is True

    deactivate_resp = client.patch(f"/api/admin/approval-chains/{body['id']}/deactivate")
    assert deactivate_resp.status_code == 200
    assert deactivate_resp.json()["is_active"] is False


def test_create_rejects_inverted_discount_range(client):
    resp = client.post(
        "/api/admin/approval-chains",
        json={"name": "Bad Range", "min_discount_pct": 50, "max_discount_pct": 10, "tiers": [{"approver_tier": "X"}]},
    )
    assert resp.status_code == 400


def test_segment_specific_template_wins_over_generic(client):
    client.post(
        "/api/admin/approval-chains",
        json={
            "name": "Wholesale Fast Track",
            "segment": "B2B-Wholesale",
            "min_discount_pct": 0,
            "max_discount_pct": 15,
            "tiers": [{"approver_tier": "Carrier Relations Lead"}],
        },
    )

    launch_resp = client.post(
        "/api/intents",
        json={
            "text": "Launch a new bundle. name: IPLC Trunk, composition: Fiber1000, "
            "segment: B2B-Wholesale, channel: B2B, price: 5000, discount: 0"
        },
    )
    assert launch_resp.json()["status"] == "executed"

    qualify_resp = client.post(
        "/api/intents",
        json={"text": "Qualify this lead. company_name: CarrierCo, source: referral, region: North"},
    )
    opp_id = qualify_resp.json()["result"]["opportunity_id"]

    quote_resp = client.post(
        "/api/intents",
        json={
            "text": f"Configure and quote this bundle. opportunity_id: {opp_id}, "
            f"offering_name: IPLC Trunk, discount: 12, justification: carrier deal"
        },
    )
    quote_id = quote_resp.json()["result"]["quote_id"]

    approve_resp = client.post(
        "/api/intents", json={"text": f"Approve this quote. quote_id: {quote_id}, reasoning: ok"}
    ).json()
    assert approve_resp["result"]["status"] == "approved"

    quote = client.get(f"/api/quotes/{quote_id}").json()
    assert quote["approval_chain"]["template_name"] == "Wholesale Fast Track"
    assert quote["approval_chain"]["steps"][0]["approver_tier"] == "Carrier Relations Lead"

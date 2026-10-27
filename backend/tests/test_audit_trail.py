def test_audit_feed_and_story_read_like_a_trail(client):
    client.post("/api/intents", json={"text": "Launch a new bundle. name: Fiber 500 Bundle, composition: Fiber500, segment: B2B-SMB, channel: B2B, price: 1000, discount: 0"})
    client.post("/api/intents", json={"text": "Qualify this lead. company_name: Trail Co, source: referral, region: North"})
    client.post("/api/intents", json={"text": "Approve this quote. quote_id: 999"})

    feed = client.get("/api/audit/feed").json()
    assert feed["stats"]["total"] == 3 and feed["stats"]["done"] == 2 and feed["stats"]["review"] == 1
    newest = feed["items"][0]
    assert newest["status"] == "review" and "missing" in newest["outcome"].lower()
    lead = next(i for i in feed["items"] if i["code"] == "INT-04")
    assert lead["label"] == "Qualify a lead" and "Created" in lead["outcome"] and any(r["label"] == "Lead #1" for r in lead["records"])

    assert client.get("/api/audit/feed", params={"status": "review"}).json()["total"] == 1
    assert client.get("/api/audit/feed", params={"record": "lead:1"}).json()["items"][0]["id"] == lead["id"]
    assert client.get("/api/audit/feed", params={"q": "trail"}).json()["total"] == 1

    story = client.get(f"/api/audit/{lead['id']}/story").json()
    assert [s["action"] for s in story["steps"]][:2] == ["Enrich lead", "Score lead"]
    assert any(u["label"] == "Company" and u["value"] == "Trail Co" for u in story["understood"])
    assert story["events"][0]["type"] == "lead.qualified" and story["rerun"] is None
    review = client.get(f"/api/audit/{newest['id']}/story").json()
    assert review["rerun"]["code"] == "INT-06" and any(c["value"] == "not found" for c in review["checks"])
    assert client.get("/api/audit").status_code == 200  # the old list is unchanged

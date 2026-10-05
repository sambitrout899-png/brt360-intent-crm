def test_trigger_test_run_executes_real_suite_and_reports_results(client):
    resp = client.post("/api/admin/test-runs")
    assert resp.status_code == 201
    body = resp.json()
    assert body["status"] == "passed"
    assert body["passed"] > 0
    assert body["failed"] == 0
    assert body["errors"] == 0
    assert body["duration_seconds"] > 0
    assert "passed" in body["output"]
    run_id = body["id"]

    listing = client.get("/api/admin/test-runs").json()
    assert any(r["id"] == run_id for r in listing)
    assert "output" not in listing[0]  # summary endpoint omits the full output

    detail = client.get(f"/api/admin/test-runs/{run_id}").json()
    assert detail["id"] == run_id
    assert len(detail["output"]) > 0


def test_get_unknown_test_run_404s(client):
    resp = client.get("/api/admin/test-runs/999999")
    assert resp.status_code == 404

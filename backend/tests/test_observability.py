"""/metrics: request counts and latency per route template (app/observability.py)."""


def _value(text, metric, **labels):
    want = ",".join(f'{k}="{v}"' for k, v in labels.items())
    for line in text.splitlines():
        if line.startswith(f"{metric}{{") and all(f'{k}="{v}"' in line for k, v in labels.items()):
            return float(line.rsplit(" ", 1)[1])
    raise AssertionError(f"no {metric}{{{want}}} in /metrics")


def test_metrics_count_requests_by_route_template_not_raw_path(client):
    before = client.get("/metrics").text
    try:
        start = _value(before, "brt360_http_requests_total", method="GET", route="/api/orders/{order_id}", status="404")
    except AssertionError:
        start = 0.0
    for order_id in (987654, 987655):  # two raw paths, one route
        assert client.get(f"/api/orders/{order_id}").status_code == 404
    after = client.get("/metrics")
    assert after.status_code == 200 and after.headers["content-type"].startswith("text/plain")
    assert _value(after.text, "brt360_http_requests_total", method="GET", route="/api/orders/{order_id}", status="404") == start + 2
    assert "987654" not in after.text  # ids never become series
    assert _value(after.text, "brt360_http_request_duration_seconds_count", method="GET", route="/api/orders/{order_id}") >= 2
    assert 'route="/metrics"' not in after.text  # scrapes don't count themselves

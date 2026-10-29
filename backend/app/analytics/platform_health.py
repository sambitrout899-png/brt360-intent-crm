"""Platform health: what Grafana's "BRT 360 - API" and "BRT 360 - Services"
dashboards show (deploy/compose/monitoring/grafana/dashboards), drawn inside
Analytics & Reports. The API asks Prometheus on the caller's behalf, so
Grafana and Prometheus stay private on the server.

These numbers cover the whole server and every organization, so only the
admins of the operator's own organizations see them (PLATFORM_HEALTH_TENANTS,
default "default"). Without Prometheus (PROMETHEUS_URL empty, or the
monitoring profile not running - staging) the tab says so instead."""

from __future__ import annotations

import datetime as dt
import math
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import httpx
from sqlalchemy.orm import Session

from app.config import get_settings
from app.crm_core.access_service import Access
from app.models.auth import UserRole
from app.models.tenant import Tenant

# user-facing services: the AI gateway's calls take seconds by nature (as in alerts.yml)
_USER_FACING = 'job!="brt360-ai-gateway"'
_HTTP = "brt360_http_requests_total"
_DUR = "brt360_http_request_duration_seconds_bucket"

SERVICES = {
    "brt360-api": "Core", "brt360-training": "Training", "brt360-bim": "BIM", "brt360-product": "Product",
    "brt360-metadata": "Metadata", "brt360-intent": "Intent", "brt360-flow": "Flow",
    "brt360-notification": "Notification", "brt360-ai-gateway": "AI gateway",
}
RANGES = {1: 60, 6: 300, 24: 900, 72: 1800, 168: 3600}  # hours shown -> seconds between points


class Unavailable(Exception):
    pass


def allowed(db: Session, access: Access) -> bool:
    if access.user.role != UserRole.admin:
        return False
    slugs = {s.strip() for s in get_settings().platform_health_tenants.split(",") if s.strip()}
    tenant = db.get(Tenant, access.tenant_id)
    return tenant is not None and tenant.slug in slugs


def _get(path: str, params: dict[str, Any]) -> list[dict[str, Any]]:
    url = get_settings().prometheus_url.rstrip("/")
    if not url:
        raise Unavailable("Monitoring isn't set up for this installation (PROMETHEUS_URL is empty).")
    try:
        resp = httpx.get(f"{url}{path}", params=params, timeout=5.0)
        body = resp.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise Unavailable("Monitoring isn't running here - start it with: docker compose --profile monitoring up -d") from exc
    if body.get("status") != "success":
        raise Unavailable(f"Prometheus refused a query: {body.get('error', resp.status_code)}")
    return body["data"]["result"]


def _num(v: Any) -> float | None:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def _instant(expr: str) -> list[tuple[dict[str, str], float | None]]:
    return [(r["metric"], _num(r["value"][1])) for r in _get("/api/v1/query", {"query": expr})]


def _one(expr: str) -> float | None:
    rows = _instant(expr)
    return rows[0][1] if rows else None


def _range(expr: str, start: float, end: float, step: int) -> list[tuple[dict[str, str], dict[float, float | None]]]:
    rows = _get("/api/v1/query_range", {"query": expr, "start": start, "end": end, "step": step})
    return [(r["metric"], {float(t): _num(v) for t, v in r["values"]}) for r in rows]


def _chart(key: str, title: str, fmt: str | None, expr: str, label: str, start: float, end: float, step: int,
           scale: float = 1.0, names: dict[str, str] | None = None) -> dict[str, Any]:
    stamps = [start + i * step for i in range(int((end - start) // step) + 1)]
    fmt_t = "%H:%M" if end - start <= 86400 else "%d %b %H:%M"
    series = []
    for metric, points in _range(expr, start, end, step):
        name = metric.get(label, "")
        name = (names or {}).get(name, name) or "all"
        vals = [points.get(t) for t in stamps]
        if any(v is not None for v in vals):
            series.append({"name": name, "values": [None if v is None else round(v * scale, 3) for v in vals]})
    series.sort(key=lambda s: -max((v for v in s["values"] if v is not None), default=0))
    return {"key": key, "title": title, "kind": "line", "fmt": fmt,
            "labels": [dt.datetime.fromtimestamp(t, dt.timezone.utc).strftime(fmt_t) for t in stamps], "series": series[:8]}


def snapshot(hours: int = 6) -> dict[str, Any]:
    hours = min(RANGES, key=lambda h: abs(h - hours))
    step = RANGES[hours]
    end = math.floor(time.time() / step) * step
    start = end - hours * 3600
    window = "5m" if hours <= 24 else "30m"

    def p95(by: str = "", sel: str = "", w: str = "15m") -> str:
        return f"histogram_quantile(0.95, sum by (le{', ' + by if by else ''}) (rate({_DUR}{{{sel}}}[{w}])))"

    def errors(by: str = "", sel: str = _USER_FACING, w: str = "15m") -> str:
        grp = f" by ({by})" if by else ""
        return (f'100 * sum{grp} (rate({_HTTP}{{{sel},status=~"5.."}}[{w}])) '
                f"/ clamp_min(sum{grp} (rate({_HTTP}{{{sel}}}[{w}])), 1e-9)")

    try:
        _get("/api/v1/query", {"query": "1"})  # reachable at all - one clear message instead of many
    except Unavailable as exc:
        return {"available": False, "reason": str(exc)}

    jobs = {
        "up": lambda: _instant('up{job=~"brt360-.*"}'),
        "requests": lambda: _instant(f"sum by (job) (increase({_HTTP}[24h]))"),
        "p95_job": lambda: _instant(p95("job", w="1h")),
        "errors_job": lambda: _instant(errors("job", sel='job=~"brt360-.*"', w="1h")),
        "site": lambda: _one('probe_success{job="site"}'),
        "uptime": lambda: _one(f'100 * avg_over_time(probe_success{{job="site"}}[{hours}h])'),
        "err": lambda: _one(errors()),
        "p95": lambda: _one(p95(sel=_USER_FACING)),
        "rps": lambda: _one(f"sum(rate({_HTTP}[5m]))"),
        "cert": lambda: _one('(probe_ssl_earliest_cert_expiry{job="site"} - time()) / 86400'),
        "cpu": lambda: _one('100 * (1 - avg(rate(node_cpu_seconds_total{mode="idle"}[5m])))'),
        "mem": lambda: _one("100 * (1 - node_memory_MemAvailable_bytes / node_memory_MemTotal_bytes)"),
        "disk": lambda: _one('100 * (1 - node_filesystem_avail_bytes{mountpoint="/"} / node_filesystem_size_bytes{mountpoint="/"})'),
        "backup": lambda: _one("(time() - brt360_backup_last_success_timestamp_seconds) / 3600"),
        "failing": lambda: _instant(f'sort_desc(sum by (method, route, status) (increase({_HTTP}{{status=~"5.."}}[1h])) > 0)'),
        "busiest": lambda: _instant(f'topk(15, sum by (route) (increase({_HTTP}{{job="brt360-api"}}[24h])))'),
        "ai_tokens": lambda: _instant("sort_desc(sum by (tenant) (increase(brt360_ai_tokens_total[24h])))"),
        "c_traffic": lambda: _chart("traffic", "Requests per second, by service", None,
                                    f"sum by (job) (rate({_HTTP}[{window}]))", "job", start, end, step, names=SERVICES),
        "c_outcome": lambda: _chart("outcome", "Requests per second, by outcome", None,
                                    f'sum by (class) (label_replace(rate({_HTTP}[{window}]), "class", "${{1}}xx", "status", "(.).*"))',
                                    "class", start, end, step),
        "c_p95": lambda: _chart("p95", "p95 response time, by service (ms)", None, p95("job", w=window), "job", start, end, step,
                                scale=1000, names=SERVICES),
        "c_slow": lambda: _chart("slow", "p95 response time - slowest routes (ms)", None,
                                 f"topk(8, {p95('route', w=window)})", "route", start, end, step, scale=1000),
        "c_errors": lambda: _chart("errors", "Server errors (5xx), by service", "pct",
                                   errors("job", sel='job=~"brt360-.*"', w=window), "job", start, end, step, names=SERVICES),
        "c_ai": lambda: _chart("ai", "AI gateway calls per minute, by outcome", None,
                               f"60 * sum by (outcome) (rate(brt360_ai_calls_total[{window}]))", "outcome", start, end, step),
    }
    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = {k: pool.submit(fn) for k, fn in jobs.items()}
    out: dict[str, Any] = {}
    for k, f in futures.items():
        try:
            out[k] = f.result()
        except Unavailable:
            out[k] = [] if not k.startswith("c_") else None

    def by_job(rows: list) -> dict[str, float | None]:
        return {m.get("job", ""): v for m, v in rows}

    up, reqs, lat, errs = by_job(out["up"]), by_job(out["requests"]), by_job(out["p95_job"]), by_job(out["errors_job"])
    services = [{"job": j, "name": SERVICES.get(j, j), "up": None if up.get(j) is None else up[j] == 1,
                 "requests_24h": reqs.get(j), "p95_ms": None if lat.get(j) is None else round(lat[j] * 1000),
                 "error_pct": errs.get(j)} for j in sorted(set(up) | set(reqs), key=lambda j: (j != "brt360-api", SERVICES.get(j, j)))]
    down = [s["name"] for s in services if s["up"] is False]

    def kpi(key, label, value, fmt=None, hint="", tone="info"):
        return {"key": key, "label": label, "value": None if value is None else round(value, 2), "fmt": fmt, "hint": hint, "tone": tone}

    def level(v, warn, bad):
        return "info" if v is None else "bad" if v >= bad else "warn" if v >= warn else "good"

    p95_ms = None if out["p95"] is None else out["p95"] * 1000
    kpis = [
        kpi("site", "Public site", None if out["site"] is None else out["site"] * 100, "pct",
            "answering now" if out["site"] == 1 else "NOT answering" if out["site"] == 0 else "not probed",
            "good" if out["site"] == 1 else "bad" if out["site"] == 0 else "info"),
        kpi("uptime", f"Uptime, last {hours} h", out["uptime"], "pct", "public health check",
            "info" if out["uptime"] is None else "good" if out["uptime"] >= 99.9 else "warn" if out["uptime"] >= 99 else "bad"),
        kpi("services_up", "Services up", sum(1 for s in services if s["up"]), None,
            f"{', '.join(down)} down" if down else f"of {len(services)}", "bad" if down else "good"),
        kpi("errors", "Server errors, 15 min", out["err"], "pct", "5xx share of requests", level(out["err"], 1, 5)),
        kpi("p95", "p95 response, 15 min", p95_ms, "ms", "95% answer faster", level(p95_ms, 300, 1000)),
        kpi("rps", "Requests per second", out["rps"], None, "all services, last 5 min"),
        kpi("cpu", "Server CPU", out["cpu"], "pct", "4 vCPU, shared with other sites", level(out["cpu"], 70, 90)),
        kpi("mem", "Server memory", out["mem"], "pct", "in use", level(out["mem"], 80, 90)),
        kpi("disk", "Disk /", out["disk"], "pct", "full", level(out["disk"], 75, 85)),
        kpi("backup", "Last backup", out["backup"], "hours", "ago (nightly at 02:00 UTC)",
            "bad" if out["backup"] is not None and out["backup"] > 26 else "good" if out["backup"] is not None else "warn"),
        kpi("cert", "HTTPS certificate", out["cert"], "days", "until it expires", "info" if out["cert"] is None else "bad" if out["cert"] < 14 else "good"),
    ]
    notes = []
    if down:
        notes.append({"tone": "bad", "text": f"Down now: {', '.join(down)}. Check: docker compose ps; docker compose logs --tail 50 <service>"})
    if out["site"] == 0:
        notes.append({"tone": "bad", "text": "The public site is not answering its health check."})
    for k in kpis:
        if k["tone"] in ("warn", "bad") and k["key"] not in ("services_up", "site"):
            unit = {"pct": "%", "ms": " ms", "hours": " h", "days": " days"}.get(k["fmt"], "")
            notes.append({"tone": k["tone"], "text": f"{k['label']}: {k['value']:g}{unit} {k['hint']}".strip()})
    if out["backup"] is None:
        notes.append({"tone": "warn", "text": "No backup has reported success yet (the backup container writes it after each nightly dump)."})

    return {
        "available": True,
        "generated_at": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S"),
        "hours": hours,
        "kpis": kpis,
        "notes": notes,
        "services": services,
        "charts": [c for c in (out[k] for k in ("c_traffic", "c_p95", "c_outcome", "c_errors", "c_slow", "c_ai")) if c and c["series"]],
        "failing_routes": [{"method": m.get("method", ""), "route": m.get("route", ""), "status": m.get("status", ""), "count": round(v or 0)}
                           for m, v in out["failing"]][:20],
        "busiest_routes": [{"route": m.get("route", ""), "count": round(v or 0)} for m, v in out["busiest"]],
        "ai_tokens": [{"tenant": m.get("tenant", ""), "tokens": round(v or 0)} for m, v in out["ai_tokens"]],
    }

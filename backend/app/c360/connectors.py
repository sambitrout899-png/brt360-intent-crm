"""External systems behind the Customer 360 page.

Each tenant runs its own billing, service desk, network OSS and inventory -
usually separate products with their own APIs and their own field names.
A connector says how to call one of them (base URL + auth) and, per
dataset it serves, which endpoint to call for one customer and how its
fields map onto ours. fetch() returns rows already in our canonical shape
(catalog.DATASETS), so every chart works the same whatever system fed it.

suggest_mapping() is the "don't make me think" part: given one sample
record from the system (pasted, or fetched by Test), it matches their
field names to ours by meaning (synonyms + fuzzy matching, and the LLM
when the tenant has one configured) and reports how sure it is.

kind "demo" simulates a system with realistic, stable per-customer data,
so a tenant can see the page working before real endpoints are wired.
"""

from __future__ import annotations

import datetime as dt
import difflib
import ipaddress
import json
import random
import re
import socket
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import quote, urlparse

from app.c360.catalog import DATASETS, DOMAINS
from app.timeutils import utcnow

AUTH_TYPES = ("none", "bearer", "api_key", "basic")
KINDS = ("rest", "demo")
TIMEOUT_SECONDS = 8.0
MAX_BYTES = 2_000_000
MAX_ROWS = 500

# Which datasets a system of each domain usually serves.
DOMAIN_DATASETS: dict[str, list[str]] = {
    "billing": ["invoices", "payments", "usage"],
    "service": ["tickets", "work_orders"],
    "network": ["network_health", "resources"],
    "inventory": ["devices", "assets"],
    "sales": ["opportunities", "orders"],
}
DEMO_NAMES = {
    "billing": "Billing system (demo)",
    "service": "Service desk (demo)",
    "network": "Network OSS (demo)",
    "inventory": "Network inventory (demo)",
    "sales": "Partner sales system (demo)",
}
DEFAULT_PATHS = {
    "invoices": "/customers/{party_id}/invoices",
    "payments": "/customers/{party_id}/payments",
    "usage": "/customers/{party_id}/usage",
    "tickets": "/customers/{party_id}/tickets",
    "work_orders": "/customers/{party_id}/work-orders",
    "network_health": "/customers/{party_id}/elements",
    "resources": "/customers/{party_id}/resources",
    "devices": "/customers/{party_id}/devices",
    "assets": "/customers/{party_id}/assets",
    "opportunities": "/customers/{party_id}/opportunities",
    "orders": "/customers/{party_id}/orders",
}


class ConnectorError(RuntimeError):
    """A connector could not be called or its answer could not be read."""


@dataclass
class ConnectorSpec:
    key: str
    name: str
    domain: str
    kind: str = "rest"
    base_url: str = ""
    auth_type: str = "none"
    auth_header: str = ""
    secret: str = ""
    endpoints: dict[str, dict[str, Any]] = field(default_factory=dict)
    is_active: bool = True
    id: int | None = None

    def serves(self, dataset: str) -> bool:
        return dataset in self.endpoints


def spec_from_model(row: Any) -> ConnectorSpec:
    try:
        endpoints = json.loads(row.endpoints_json or "{}")
    except json.JSONDecodeError:
        endpoints = {}
    return ConnectorSpec(
        key=row.key, name=row.name, domain=row.domain, kind=row.kind, base_url=row.base_url or "",
        auth_type=row.auth_type or "none", auth_header=row.auth_header or "", secret=row.secret or "",
        endpoints=endpoints if isinstance(endpoints, dict) else {}, is_active=bool(row.is_active), id=row.id,
    )


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:50] or "system"


def clean_endpoints(raw: Any) -> dict[str, dict[str, Any]]:
    """Only datasets we know, only the three keys we use, mapping only onto our fields."""
    out: dict[str, dict[str, Any]] = {}
    if not isinstance(raw, dict):
        return out
    for dataset, cfg in raw.items():
        if dataset not in DATASETS or not isinstance(cfg, dict):
            continue
        ours = set(DATASETS[dataset].field_keys())
        fmap = cfg.get("field_map") if isinstance(cfg.get("field_map"), dict) else {}
        out[dataset] = {
            "path": str(cfg.get("path") or DEFAULT_PATHS.get(dataset, "/"))[:300],
            "records_path": str(cfg.get("records_path") or "")[:200],
            "field_map": {k: str(v)[:200] for k, v in fmap.items() if k in ours and isinstance(v, str) and v.strip()},
        }
    return out


def demo_endpoints(domain: str) -> dict[str, dict[str, Any]]:
    return {d: {"path": "", "records_path": "", "field_map": {}} for d in DOMAIN_DATASETS.get(domain, []) if d in DATASETS}


# ---------------------------------------------------------------- calling a REST system


def check_url(url: str) -> None:
    """Admin-configured URLs only go to http(s) hosts - never to file:, never
    to cloud metadata / link-local addresses. Private addresses are allowed
    on purpose: on-premise BSS/OSS usually live on the internal network."""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise ConnectorError("The address must start with http:// or https:// and name a host")
    try:
        infos = socket.getaddrinfo(parsed.hostname, None)
    except socket.gaierror as exc:
        raise ConnectorError(f"Can't resolve host '{parsed.hostname}'") from exc
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if ip.is_link_local or ip.is_multicast or ip.is_unspecified or str(ip) == "169.254.169.254":
            raise ConnectorError(f"'{parsed.hostname}' points to a reserved address - not allowed")


def _headers(spec: ConnectorSpec) -> dict[str, str]:
    headers = {"Accept": "application/json", "User-Agent": "BRT360-Customer360/1.0"}
    if spec.auth_type == "bearer" and spec.secret:
        headers["Authorization"] = f"Bearer {spec.secret}"
    elif spec.auth_type == "api_key" and spec.secret:
        headers[spec.auth_header or "X-API-Key"] = spec.secret
    elif spec.auth_type == "basic" and spec.secret:
        import base64

        headers["Authorization"] = "Basic " + base64.b64encode(spec.secret.encode()).decode()
    return headers


def build_url(spec: ConnectorSpec, dataset: str, party: dict[str, Any]) -> str:
    path = spec.endpoints.get(dataset, {}).get("path") or DEFAULT_PATHS.get(dataset, "/")
    values = {
        "party_id": party.get("id", ""),
        "customer_id": party.get("id", ""),
        "party_name": party.get("name", ""),
        "customer_name": party.get("name", ""),
        "email": party.get("email") or "",
    }
    filled = re.sub(r"\{(\w+)\}", lambda m: quote(str(values.get(m.group(1), m.group(0))), safe=""), path)
    if filled.startswith(("http://", "https://")):
        return filled
    return spec.base_url.rstrip("/") + "/" + filled.lstrip("/")


def http_get(url: str, headers: dict[str, str]) -> Any:
    """The one network call (tests replace this)."""
    import httpx

    with httpx.Client(timeout=TIMEOUT_SECONDS, follow_redirects=False) as client:
        with client.stream("GET", url, headers=headers) as resp:
            if resp.status_code >= 400:
                raise ConnectorError(f"The system answered {resp.status_code} {resp.reason_phrase}")
            body = b""
            for chunk in resp.iter_bytes():
                body += chunk
                if len(body) > MAX_BYTES:
                    raise ConnectorError("The answer is larger than 2 MB - point the endpoint at one customer's data")
    try:
        return json.loads(body or b"null")
    except json.JSONDecodeError as exc:
        raise ConnectorError("The system did not answer with JSON") from exc


def dig(obj: Any, path: str) -> Any:
    """'data.items' / 'result[0].lines' / '' (the object itself)."""
    if not path:
        return obj
    for part in re.findall(r"[^.\[\]]+|\[\d+\]", path):
        if part.startswith("["):
            idx = int(part[1:-1])
            obj = obj[idx] if isinstance(obj, list) and len(obj) > idx else None
        else:
            obj = obj.get(part) if isinstance(obj, dict) else None
        if obj is None:
            return None
    return obj


def find_records(payload: Any, records_path: str = "") -> list[dict[str, Any]]:
    found = dig(payload, records_path)
    if isinstance(found, list):
        return [r for r in found if isinstance(r, dict)]
    if isinstance(found, dict):
        if records_path:
            return [found]
        # No path given: the first list of objects, breadth-first.
        queue: list[Any] = [found]
        while queue:
            node = queue.pop(0)
            if isinstance(node, dict):
                for value in node.values():
                    if isinstance(value, list) and value and all(isinstance(v, dict) for v in value):
                        return value
                    if isinstance(value, dict):
                        queue.append(value)
        return [found]
    return []


def guess_records_path(payload: Any) -> str:
    if isinstance(payload, list):
        return ""
    queue: list[tuple[str, Any]] = [("", payload)]
    while queue:
        prefix, node = queue.pop(0)
        if isinstance(node, dict):
            for key, value in node.items():
                path = f"{prefix}.{key}" if prefix else key
                if isinstance(value, list) and value and all(isinstance(v, dict) for v in value):
                    return path
                if isinstance(value, dict):
                    queue.append((path, value))
    return ""


def map_record(record: dict[str, Any], dataset: str, field_map: dict[str, str]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key in DATASETS[dataset].field_keys():
        source = field_map.get(key, key)
        value = dig(record, source)
        if value is not None and not isinstance(value, (dict, list)):
            out[key] = value
    return out


def fetch(spec: ConnectorSpec, dataset: str, party: dict[str, Any]) -> list[dict[str, Any]]:
    if not spec.serves(dataset):
        raise ConnectorError(f"{spec.name} is not set up to provide {DATASETS[dataset].label.lower()}")
    if spec.kind == "demo":
        return demo_rows(spec.key, dataset, party)
    if not spec.base_url and not spec.endpoints[dataset].get("path", "").startswith("http"):
        raise ConnectorError(f"{spec.name} has no address yet")
    url = build_url(spec, dataset, party)
    check_url(url)
    payload = http_get(url, _headers(spec))
    cfg = spec.endpoints[dataset]
    rows = find_records(payload, cfg.get("records_path", ""))[:MAX_ROWS]
    return [map_record(r, dataset, cfg.get("field_map", {})) for r in rows]


def sample_raw(spec: ConnectorSpec, dataset: str, party: dict[str, Any]) -> tuple[Any, list[dict[str, Any]]]:
    """For Test: the raw answer and the records found in it."""
    if spec.kind == "demo":
        rows = demo_rows(spec.key, dataset, party)
        return rows, rows
    url = build_url(spec, dataset, party)
    check_url(url)
    payload = http_get(url, _headers(spec))
    return payload, find_records(payload, spec.endpoints.get(dataset, {}).get("records_path", ""))


def mask(secret: str | None) -> str:
    if not secret:
        return ""
    return "•" * 8 + secret[-4:] if len(secret) > 8 else "•" * len(secret)


# ---------------------------------------------------------------- schema mapping

_SYNONYMS: dict[str, list[str]] = {
    "invoice_id": ["invoice_number", "invoiceno", "inv_no", "bill_id", "bill_no", "document_number", "number", "id"],
    "period": ["bill_period", "billing_period", "month", "cycle", "invoice_date", "bill_date", "date", "period_start"],
    "amount": ["total", "total_amount", "amount_due", "grand_total", "value", "charge", "net_amount", "amt"],
    "status": ["state", "payment_status", "stage", "lifecycle_status", "condition", "oper_status", "operational_state"],
    "due_date": ["due", "payment_due", "due_on", "dueDate"],
    "payment_id": ["receipt_no", "receipt_id", "transaction_id", "txn_id", "reference", "id"],
    "date": ["paid_on", "payment_date", "received_at", "timestamp", "created"],
    "method": ["payment_method", "channel", "mode", "instrument"],
    "data_gb": ["data", "data_usage", "gb", "data_volume", "mobile_data", "bytes_gb"],
    "voice_min": ["voice", "minutes", "voice_minutes", "mou", "call_minutes"],
    "sms": ["sms_count", "messages", "texts"],
    "element": ["element_name", "ne_name", "node", "device_name", "link", "hostname", "name"],
    "site": ["location", "site_name", "address", "pop", "site_id"],
    "utilization_pct": ["utilization", "util", "load", "usage_pct", "bandwidth_utilization", "cpu"],
    "latency_ms": ["latency", "rtt", "delay", "ping_ms", "round_trip"],
    "availability_pct": ["availability", "uptime", "uptime_pct", "sla_pct"],
    "device_id": ["serial", "serial_number", "asset_tag", "id", "device"],
    "model": ["device_model", "hardware", "product", "type"],
    "firmware": ["fw", "firmware_version", "software_version", "os_version", "version"],
    "category": ["type", "issue_type", "problem", "classification"],
    "priority": ["urgency", "prio", "impact"],
    "severity": ["sev", "impact_level"],
    "created_at": ["opened", "opened_at", "created", "create_date", "reported_at", "timestamp"],
    "id": ["number", "ticket_number", "ref", "reference", "identifier"],
    "asset_type": ["type", "category", "class"],
    "identifier": ["serial", "msisdn", "circuit_id", "name"],
    "inventory_ref": ["inventory_id", "ref", "external_id"],
    "resource_type": ["type", "kind", "class"],
    "technician": ["engineer", "assignee", "assigned_to"],
    "scheduled_window": ["window", "slot", "appointment"],
    "kind": ["type", "work_type"],
}


def _norm(name: str) -> str:
    name = re.sub(r"([a-z])([A-Z])", r"\1_\2", name)
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")


def flatten_keys(record: Any, prefix: str = "", depth: int = 0) -> list[str]:
    keys: list[str] = []
    if isinstance(record, dict) and depth < 3:
        for k, v in record.items():
            path = f"{prefix}.{k}" if prefix else str(k)
            if isinstance(v, dict):
                keys += flatten_keys(v, path, depth + 1)
            elif not isinstance(v, list):
                keys.append(path)
    return keys


def _rule_mapping(dataset: str, sample: dict[str, Any]) -> dict[str, dict[str, Any]]:
    candidates = flatten_keys(sample)
    leaf = {c: _norm(c.split(".")[-1]) for c in candidates}
    used: set[str] = set()
    out: dict[str, dict[str, Any]] = {}
    for f in DATASETS[dataset].fields:
        best, score = None, 0.0
        names = [f.key, *_SYNONYMS.get(f.key, [])]
        for cand, norm in leaf.items():
            if cand in used:
                continue
            for i, name in enumerate(names):
                n = _norm(name)
                if norm == n:
                    s = 1.0 if i == 0 else 0.92 - min(i, 6) * 0.02
                elif n in norm or norm in n:
                    s = 0.72 if len(min(n, norm, key=len)) >= 3 else 0.0
                else:
                    s = difflib.SequenceMatcher(None, norm, n).ratio() * 0.8
                if s > score:
                    best, score = cand, s
        if best is not None and score >= 0.6:
            used.add(best)
            out[f.key] = {"source": best, "confidence": round(score, 2)}
    return out


def _llm_mapping(target: Any, dataset: str, sample: dict[str, Any]) -> dict[str, dict[str, Any]]:
    from app.crm_core import llm

    ds = DATASETS[dataset]
    keys = flatten_keys(sample)
    prompt = (
        f"Map an external system's fields onto our '{ds.label}' dataset. Our fields: "
        + ", ".join(f"{f.key} ({f.label}, {f.type})" for f in ds.fields)
        + f". Their fields (dot paths) with a sample record: {json.dumps(sample, default=str)[:3000]}. "
        'Answer JSON {"mapping": {"<our field>": {"source": "<their dot path>", "confidence": 0-1}}}. '
        "Only use their paths exactly as listed; leave a field out when nothing fits."
    )
    parsed = llm.chat_json(target, "You map API fields between systems. Answer with JSON only.", prompt).get("mapping") or {}
    if not isinstance(parsed, dict):
        return {}
    out = {}
    for ours, m in parsed.items():
        if ours in ds.field_keys() and isinstance(m, dict) and m.get("source") in keys:
            out[ours] = {"source": m["source"], "confidence": float(m.get("confidence") or 0.8)}
    return out


def suggest_mapping(dataset: str, payload: Any, target: Any = None) -> dict[str, Any]:
    """payload = a raw answer or one record. Returns records_path, mapping
    {ours: {source, confidence}}, unmapped fields and a preview."""
    records_path = guess_records_path(payload)
    records = find_records(payload, records_path)
    sample = records[0] if records else (payload if isinstance(payload, dict) else {})
    mapping: dict[str, dict[str, Any]] = {}
    engine = "rules"
    if target is not None and sample:
        try:
            mapping = _llm_mapping(target, dataset, sample)
            engine = "ai"
        except Exception:  # noqa: BLE001 - any LLM failure falls back to rules
            mapping = {}
    if not mapping:
        mapping = _rule_mapping(dataset, sample)
        engine = "rules"
    field_map = {k: v["source"] for k, v in mapping.items() if v["source"] != k}
    preview = [map_record(r, dataset, {k: v["source"] for k, v in mapping.items()}) for r in records[:5]]
    return {
        "dataset": dataset,
        "records_path": records_path,
        "mapping": mapping,
        "field_map": field_map,
        "unmapped": [f.key for f in DATASETS[dataset].fields if f.key not in mapping],
        "their_fields": flatten_keys(sample),
        "records_found": len(records),
        "preview": preview,
        "engine": engine,
    }


# ---------------------------------------------------------------- demo systems


def _months_back(n: int) -> list[dt.date]:
    today = utcnow().date().replace(day=1)
    out = []
    y, m = today.year, today.month
    for _ in range(n):
        out.append(dt.date(y, m, 1))
        m -= 1
        if m == 0:
            y, m = y - 1, 12
    return list(reversed(out))


def demo_rows(key: str, dataset: str, party: dict[str, Any]) -> list[dict[str, Any]]:
    """Stable, plausible data per (system, customer) - the same customer
    always shows the same story, different customers different ones."""
    rnd = random.Random(f"{key}:{dataset}:{party.get('id')}:{party.get('name')}")
    size = rnd.choice([1, 1, 2, 3])  # account size multiplier
    months = _months_back(12)
    if dataset == "invoices":
        base = rnd.randint(18, 60) * 1000 * size
        rows = []
        for i, month in enumerate(months):
            amount = round(base * (1 + 0.04 * i) * rnd.uniform(0.9, 1.12), -1)
            age = len(months) - 1 - i
            status = "due" if age == 0 else ("overdue" if age == 1 and rnd.random() < 0.45 else "paid")
            rows.append({"invoice_id": f"INV-{month:%y%m}-{party.get('id', 0):04d}", "period": month.isoformat(),
                         "amount": amount, "status": status, "due_date": (month + dt.timedelta(days=45)).isoformat()})
        return rows
    if dataset == "payments":
        return [{"payment_id": f"RCPT-{month:%y%m}{rnd.randint(100, 999)}", "date": (month + dt.timedelta(days=rnd.randint(20, 40))).isoformat(),
                 "amount": round(rnd.randint(18, 60) * 1000 * size, -1), "method": rnd.choice(["NEFT", "UPI", "Cheque", "Auto-debit"])}
                for month in months[:-2]]
    if dataset == "usage":
        base = rnd.uniform(80, 400) * size
        return [{"period": month.isoformat(), "data_gb": round(base * (1 + 0.06 * i) * rnd.uniform(0.85, 1.15), 1),
                 "voice_min": rnd.randint(2000, 9000) * size, "sms": rnd.randint(100, 1500)} for i, month in enumerate(months)]
    if dataset == "network_health":
        sites = ["Head office", "Branch - Andheri", "Branch - Powai", "DC - Navi Mumbai"][: 2 + size]
        rows = []
        for site in sites:
            for element in ("Access link", "CPE router"):
                util = rnd.randint(25, 96)
                rows.append({"element": f"{element} · {site}", "site": site,
                             "status": "degraded" if util > 88 else ("down" if rnd.random() < 0.04 else "up"),
                             "utilization_pct": util, "latency_ms": rnd.randint(4, 38) + (30 if util > 88 else 0),
                             "availability_pct": round(rnd.uniform(99.2, 99.99) if util < 88 else rnd.uniform(97.5, 99.3), 2)})
        return rows
    if dataset == "resources":
        return [{"id": i + 1, "resource_type": t, "identifier": ident, "status": "active", "site": "Head office"}
                for i, (t, ident) in enumerate([("circuit", f"MPLS-{rnd.randint(10000, 99999)}"), ("ip_block", f"10.{rnd.randint(1, 250)}.0.0/28")])]
    if dataset == "devices":
        models = ["Cisco ISR 1100", "Juniper SRX300", "Nokia 7210 SAS", "Huawei AR6120"]
        return [{"device_id": f"SN{rnd.randint(10_000_000, 99_999_999)}", "model": rnd.choice(models),
                 "site": rnd.choice(["Head office", "Branch - Andheri", "Branch - Powai"]),
                 "status": rnd.choices(["in_service", "in_service", "in_service", "faulty", "spare"])[0],
                 "firmware": f"{rnd.randint(15, 17)}.{rnd.randint(0, 9)}.{rnd.randint(1, 5)}"} for _ in range(2 + 2 * size)]
    if dataset == "assets":
        return [{"id": i + 1, "asset_type": t, "identifier": f"{t[:3].upper()}-{rnd.randint(1000, 9999)}", "status": "active",
                 "inventory_ref": f"INV-{rnd.randint(10000, 99999)}", "created_at": months[rnd.randint(0, 11)].isoformat()}
                for i, t in enumerate(["connection", "CPE", "circuit", "SIM"][: 2 + size])]
    if dataset == "tickets":
        cats = ["Connectivity", "Billing query", "Slow speed", "Outage", "Change request"]
        return [{"id": 9000 + i, "category": rnd.choice(cats), "severity": rnd.choice(["low", "medium", "high"]),
                 "priority": rnd.choice(["low", "medium", "high", "critical"]),
                 "status": rnd.choices(["open", "resolved", "resolved", "in_progress"])[0], "sla_class": "premium",
                 "created_at": months[rnd.randint(0, 11)].isoformat()} for i in range(3 + 2 * size)]
    if dataset == "work_orders":
        return [{"id": 7000 + i, "kind": rnd.choice(["installation", "repair", "survey"]), "status": rnd.choice(["scheduled", "complete"]),
                 "scheduled_window": "Tue 10:00-13:00", "technician": rnd.choice(["R. Iyer", "S. Khan", "P. Nair"]),
                 "created_at": months[rnd.randint(6, 11)].isoformat()} for i in range(1 + size)]
    if dataset in ("opportunities", "orders"):
        return []
    return []


def domain_label(domain: str) -> str:
    return DOMAINS.get(domain, {"label": domain.title()})["label"]

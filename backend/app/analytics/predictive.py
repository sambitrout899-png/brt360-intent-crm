"""Telecom predictions, learned from the tenant's own history.

  churn()        which live customers are likely to leave next, and why -
                 logistic regression on behaviour before each churn (tickets,
                 late bills, failed payments, falling usage, surveys ...);
                 an expert scorecard until there are enough churned customers.
                 Each customer also gets a lifetime value (CLV).
  win()          each open opportunity's chance to close, its expected value
                 and the weighted pipeline.
  segments()     customers grouped by behaviour (k-means), each group named.
  next_offer()   the product each customer is most likely to take next
                 (what customers like them bought together - association rules).
  usage_alerts() customers whose usage jumped far above their own baseline
                 (bill shock, fraud).
  forecast()     any measure of any entity, projected ahead (ml.forecast).

Everything reads through the reporting model, so it respects what the user may see.
"""
from __future__ import annotations

import datetime as dt
import math
from collections import Counter, defaultdict
from typing import Any

from sqlalchemy.orm import Session

from app.analytics import engine, ml, semantic
from app.analytics.semantic import CLOSED_TICKET, LIVE_ORDER, LOST_STAGES
from app.models.catalog import ProductOffering

CHURNED_ORDER = ("cancelled", "terminated")


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)


def _dt(v: Any) -> dt.datetime | None:
    return engine._as_datetime(v)


def _rows(db: Session, tenant_id: int, access: Any, entity: str, keys: list[str], filters: list[dict] | None = None) -> list[dict[str, Any]]:
    m = semantic.model(db, tenant_id, entity)
    return engine.read(db, tenant_id, access, m, [k for k in keys if k in m.fields], filters or [])[0]


def _band(p: float) -> str:
    return "High" if p >= 0.5 else "Medium" if p >= 0.25 else "Low"


# ---------------------------------------------------------------- churn

# (feature, what a high value means, what a low value means, expert weight for the scorecard)
CHURN_FEATURES = [
    ("tenure_months", "Long-standing customer", "New customer (short tenure)", -0.4),
    ("orders_total", "Many orders over time", "Few orders", -0.2),
    ("avg_price", "High-value services", "Low-value services", -0.1),
    ("tickets_90", "Many tickets in the last 90 days", "Few tickets", 0.6),
    ("critical_180", "High/critical faults in the last 6 months", "No serious faults", 0.8),
    ("overdue_invoices", "Overdue invoices", "Pays on time", 0.7),
    ("max_days_late", "Bills paid very late", "Bills paid on time", 0.5),
    ("failed_payments", "Failed payments", "No failed payments", 0.6),
    ("usage_trend", "Usage growing", "Usage falling", -0.8),
    ("survey_score", "Satisfied in surveys", "Unhappy in surveys", -0.6),
    ("fraud_alerts", "Fraud alerts on the account", "No fraud alerts", 0.3),
    ("b2b", "Business customer", "Consumer customer", -0.1),
]


def _customer_data(db: Session, tenant_id: int, access: Any) -> dict[str, Any]:
    return {
        "orders": _rows(db, tenant_id, access, "order", ["id", "status", "monthly_price", "contract_months", "created_at", "updated_at",
                                                         "customer.id", "customer.name", "customer.created_at", "customer_profile.segment"]),
        "tickets": _rows(db, tenant_id, access, "ticket", ["customer.id", "created_at", "severity", "status"]),
        "invoices": _rows(db, tenant_id, access, "invoice", ["customer.id", "issue_date", "due_date", "total", "paid", "credited", "outstanding"]),
        "payments": _rows(db, tenant_id, access, "payment", ["customer.id", "status", "received_at"]),
        "usage": _rows(db, tenant_id, access, "usage", ["customer.id", "at", "amount"], [{"field": "at", "op": "last_n_days", "value": 800}]),
        "surveys": _rows(db, tenant_id, access, "survey", ["ticket.party_id", "overall"]),
        "fraud": _rows(db, tenant_id, access, "fraud_alert", ["customer.id", "created_at"]),
    }


def _by(rows: list[dict[str, Any]], key: str) -> dict[Any, list[dict[str, Any]]]:
    out: dict[Any, list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        if r.get(key) is not None:
            out[r[key]].append(r)
    return out


def _within(v: Any, start: dt.datetime, end: dt.datetime) -> bool:
    d = _dt(v)
    return d is not None and start < d <= end


def _churn_frame(data: dict[str, Any]) -> list[dict[str, Any]]:
    """One row per customer that ever had an order: features as of its churn date (or today) and whether it churned."""
    now = _now()
    orders, tickets, invoices = _by(data["orders"], "customer.id"), _by(data["tickets"], "customer.id"), _by(data["invoices"], "customer.id")
    payments, usage, fraud = _by(data["payments"], "customer.id"), _by(data["usage"], "customer.id"), _by(data["fraud"], "customer.id")
    surveys = _by(data["surveys"], "ticket.party_id")
    all_scores = [s["overall"] for s in data["surveys"] if s.get("overall")]
    mean_score = sum(all_scores) / len(all_scores) if all_scores else 3.0
    frame = []
    for pid, os_ in orders.items():
        live = [o for o in os_ if o["status"] in LIVE_ORDER]
        gone = [o for o in os_ if o["status"] in CHURNED_ORDER]
        churned = not live and bool(gone)
        if not live and not churned:
            continue  # never went live (still being ordered, or rejected) - neither a customer to keep nor one lost
        as_of = max((_dt(o["updated_at"]) or now for o in gone), default=now) if churned else now
        first = min((_dt(o["created_at"]) or now for o in os_), default=now)
        u = usage.get(pid, [])
        u30 = sum(x["amount"] or 0 for x in u if _within(x["at"], as_of - dt.timedelta(days=30), as_of))
        u_prev = sum(x["amount"] or 0 for x in u if _within(x["at"], as_of - dt.timedelta(days=60), as_of - dt.timedelta(days=30)))
        inv = [i for i in invoices.get(pid, []) if (_dt(i["issue_date"]) or now) <= as_of]
        late = [i for i in inv if (i["outstanding"] or 0) > 0.005 and (_dt(i["due_date"]) or as_of) < as_of]
        scores = [s["overall"] for s in surveys.get(pid, []) if s.get("overall")]
        tks = tickets.get(pid, [])
        f = {
            "tenure_months": max(0.0, (as_of - first).days / 30.44),
            "orders_total": float(sum(1 for o in os_ if (_dt(o["created_at"]) or now) <= as_of)),
            "avg_price": sum(o["monthly_price"] or 0 for o in os_) / len(os_),
            "tickets_90": float(sum(1 for t in tks if _within(t["created_at"], as_of - dt.timedelta(days=90), as_of))),
            "critical_180": float(sum(1 for t in tks if t["severity"] in ("high", "critical") and _within(t["created_at"], as_of - dt.timedelta(days=180), as_of))),
            "overdue_invoices": float(len(late)),
            "max_days_late": max((max(0.0, (as_of - (_dt(i["due_date"]) or as_of)).days) for i in late), default=0.0),
            "failed_payments": float(sum(1 for p in payments.get(pid, []) if p["status"] in ("failed", "reversed")
                                         and _within(p["received_at"], as_of - dt.timedelta(days=180), as_of))),
            "usage_trend": max(-1.0, min(3.0, (u30 - u_prev) / max(u_prev, 1.0))) if (u30 or u_prev) else 0.0,
            "survey_score": sum(scores) / len(scores) if scores else mean_score,
            "fraud_alerts": float(sum(1 for x in fraud.get(pid, []) if (_dt(x["created_at"]) or now) <= as_of)),
            "b2b": 1.0 if str(os_[0].get("customer_profile.segment") or "").upper().startswith("B2B") else 0.0,
        }
        frame.append({"party_id": pid, "name": os_[0].get("customer.name") or f"Customer #{pid}", "segment": os_[0].get("customer_profile.segment"),
                      "churned": churned, "mrr": round(sum(o["monthly_price"] or 0 for o in live), 2), "services": len(live),
                      "features": f, "since": first.date().isoformat()})
    return frame


def churn(db: Session, tenant_id: int, access: Any, data: dict[str, Any] | None = None) -> dict[str, Any]:
    frame = _churn_frame(data or _customer_data(db, tenant_id, access))
    names = [f[0] for f in CHURN_FEATURES]
    text = {f[0]: (f[1], f[2]) for f in CHURN_FEATURES}
    X = [[row["features"][n] for n in names] for row in frame]
    y = [1 if row["churned"] else 0 for row in frame]
    trained = ml.train_and_score(names, X, y) if frame else None
    base_rate = round(sum(y) / len(y), 3) if y else None
    if trained:
        model: Any = trained["model"]
        method = "Logistic regression, trained on your customers' history"
        metrics = {k: trained[k] for k in ("auc_holdout", "auc_train", "trained_on", "positives", "accuracy")} | {"base_rate": base_rate}
        importance = [{**i, "label": text[i["feature"]][0 if i["weight"] > 0 else 1]} for i in model.importance()]
    else:
        model = _Scorecard(names, [f[3] for f in CHURN_FEATURES], X)
        method = "Expert scorecard (the model trains itself once 8+ customers have churned and 8+ stayed)"
        metrics = {"trained_on": len(frame), "positives": sum(y), "base_rate": base_rate, "auc_train": ml.auc([model.proba(x) for x in X], y) if X else None}
        importance = [{"feature": n, "weight": w, "share": round(100 * abs(w) / sum(abs(v) for v in model.w), 1), "label": text[n][0 if w > 0 else 1]}
                      for n, w in sorted(zip(names, model.w), key=lambda t: -abs(t[1]))]
    out = []
    for row, x in zip(frame, X):
        if row["churned"]:
            continue
        p = model.proba(x)
        drivers = [text[d["feature"]][0 if _direction(model, names, d["feature"], x) > 0 else 1] for d in model.drivers(x)]
        hazard = 1 - (1 - min(p, 0.99)) ** (1 / 12)  # p is read as a 12-month churn chance
        lifetime = min(60.0, 1 / max(hazard, 1 / 60))
        out.append({"party_id": row["party_id"], "name": row["name"], "segment": row["segment"], "mrr": row["mrr"], "services": row["services"],
                    "probability": round(p, 3), "band": _band(p), "drivers": drivers, "tenure_months": round(row["features"]["tenure_months"], 1),
                    "clv": round(row["mrr"] * lifetime, 2), "expected_lifetime_months": round(lifetime, 1), "since": row["since"]})
    out.sort(key=lambda r: (-r["probability"], -r["mrr"]))
    at_risk = [r for r in out if r["band"] != "Low"]
    return {"method": method, "metrics": metrics, "importance": importance, "customers": out,
            "summary": {"customers": len(out), "high": sum(1 for r in out if r["band"] == "High"), "medium": sum(1 for r in out if r["band"] == "Medium"),
                        "mrr_at_risk": round(sum(r["mrr"] for r in at_risk), 2),
                        "expected_mrr_loss": round(sum(r["mrr"] * r["probability"] for r in out), 2),
                        "total_clv": round(sum(r["clv"] for r in out), 2)}}


def _direction(model: Any, names: list[str], feature: str, x: list[float]) -> float:
    j = names.index(feature)
    return (x[j] - model.mean[j])


class _Scorecard:
    """Fixed expert weights on standardised features - the same interface as ml.Logistic."""

    def __init__(self, names: list[str], weights: list[float], X: list[list[float]]):
        self.names, self.w = names, weights
        d = len(names)
        n = max(1, len(X))
        self.mean = [sum(r[j] for r in X) / n for j in range(d)] if X else [0.0] * d
        self.std = [max(1e-6, math.sqrt(sum((r[j] - self.mean[j]) ** 2 for r in X) / n)) if X else 1.0 for j in range(d)]
        self.std = [s if s > 1e-6 else 1.0 for s in self.std]
        self.b = -1.6  # ~17% at the average customer

    def _z(self, x: list[float]) -> list[float]:
        return [max(-3.0, min(3.0, (v - m) / s)) for v, m, s in zip(x, self.mean, self.std)]

    def proba(self, x: list[float]) -> float:
        return 1 / (1 + math.exp(-(self.b + sum(w * v for w, v in zip(self.w, self._z(x))))))

    def drivers(self, x: list[float], top: int = 3) -> list[dict[str, Any]]:
        parts = sorted(((w * v, n) for w, v, n in zip(self.w, self._z(x), self.names)), reverse=True)
        return [{"feature": n, "impact": round(c, 3)} for c, n in parts[:top] if c > 0.05]


# ---------------------------------------------------------------- deals

STAGE_PRIOR = {"draft": 0.15, "qualified": 0.25, "configured": 0.3, "in_feasibility": 0.35, "feasible": 0.45,
               "feasibility_failed": 0.1, "quoted": 0.5}
STAGE_PROGRESS = {"draft": 0.0, "qualified": 0.1, "configured": 0.35, "in_feasibility": 0.55, "feasible": 0.8}  # of the way to a quote


def win(db: Session, tenant_id: int, access: Any) -> dict[str, Any]:
    now = _now()
    opps = _rows(db, tenant_id, access, "opportunity", ["id", "name", "stage", "created_at", "updated_at", "offering_name", "quantity",
                                                        "lead.source", "lead.region", "lead.propensity_score", "customer.name", "customer_profile.segment"])
    quotes = _by(_rows(db, tenant_id, access, "quote", ["opportunity.id", "total_price", "requested_discount_pct", "revision_number", "status", "created_at"]),
                 "opportunity.id")
    closed = [o for o in opps if o["stage"] == "won" or o["stage"] in LOST_STAGES]
    open_ = [o for o in opps if o["stage"] != "won" and o["stage"] not in LOST_STAGES]
    # an opportunity not quoted yet is valued at its offering's list price
    prices = dict(db.query(ProductOffering.name, ProductOffering.price).filter(ProductOffering.tenant_id == tenant_id).all())
    cats = {}
    for key in ("lead.source", "customer_profile.segment", "offering_name", "lead.region"):
        counts = Counter(o.get(key) for o in closed if o.get(key))
        cats[key] = [v for v, c in counts.most_common(8) if c >= 3]
    names = ["quantity", "max_discount", "quotes", "revisions", "propensity", "days_open", "log_value", "stage_prior"] + \
        [f"{k}={v}" for k, vs in cats.items() for v in vs]

    def value(o: dict[str, Any]) -> float:
        qs = sorted(quotes.get(o["id"], []), key=lambda q: (_dt(q["created_at"]) or now))
        return float(qs[-1]["total_price"] or 0) if qs else float(prices.get(o.get("offering_name")) or 0) * float(o["quantity"] or 1)

    def row(o: dict[str, Any]) -> list[float]:
        qs = quotes.get(o["id"], [])
        done = o["stage"] == "won" or o["stage"] in LOST_STAGES
        as_of = _dt(o["updated_at"]) if done else now
        stage = o["stage"] if not done else ("quoted" if qs else "qualified")
        return [float(o["quantity"] or 1), max((q["requested_discount_pct"] or 0 for q in qs), default=0.0), float(len(qs)),
                float(max((q["revision_number"] or 1 for q in qs), default=0)), float(o.get("lead.propensity_score") or 0.5),
                max(0.0, ((as_of or now) - (_dt(o["created_at"]) or now)).days), math.log1p(value(o)), STAGE_PRIOR.get(stage, 0.3)] + \
            [1.0 if o.get(k) == v else 0.0 for k, vs in cats.items() for v in vs]

    X = [row(o) for o in closed]
    y = [1 if o["stage"] == "won" else 0 for o in closed]
    trained = ml.train_and_score(names, X, y, min_class=5) if closed else None
    base = (sum(y) + 1) / (len(y) + 2)
    by_source: dict[Any, list[int]] = defaultdict(list)
    for o, t in zip(closed, y):
        by_source[o.get("lead.source")].append(t)
    # closed deals that never got a quote mostly died early, so "no quote yet" on an open deal is not "lost": score it as
    # (chance it reaches a quote, higher the further it has got) x (chance it wins once quoted)
    quoted_rate = sum(1 for o in closed if quotes.get(o["id"])) / len(closed) if closed else 1.0
    out = []
    for o in open_:
        x = row(o)
        if trained:
            reach = 1.0
            if not quotes.get(o["id"]):
                reach = quoted_rate ** (1 - STAGE_PROGRESS.get(o["stage"], 0.0))
                x = x[:]
                x[names.index("quotes")], x[names.index("revisions")], x[names.index("stage_prior")] = 1.0, 1.0, STAGE_PRIOR["quoted"]
            p = trained["model"].proba(x) * reach
            drivers = [_deal_text(d["feature"], x[names.index(d["feature"])] - trained["model"].mean[names.index(d["feature"])]) for d in trained["model"].drivers(x)]
            if reach < 1:
                drivers = [f"Not quoted yet ({round(100 * reach)}% of deals at this stage reach a quote)"] + drivers[:2]
        else:
            hist = by_source.get(o.get("lead.source"), [])
            rate = (sum(hist) + 2 * base) / (len(hist) + 2)  # the source's record, pulled towards the overall rate
            p = 0.5 * STAGE_PRIOR.get(o["stage"], 0.3) + 0.5 * rate if closed else STAGE_PRIOR.get(o["stage"], 0.3)
            drivers = [f"Stage: {o['stage'].replace('_', ' ')}"] + ([f"{o.get('lead.source')} leads win {round(100 * sum(hist) / len(hist))}%"] if hist else [])
        v = value(o)
        out.append({"opportunity_id": o["id"], "name": o["name"], "customer": o.get("customer.name"), "stage": o["stage"], "value": round(v, 2),
                    "probability": round(p, 3), "expected_value": round(p * v, 2), "drivers": drivers, "segment": o.get("customer_profile.segment"),
                    "age_days": max(0, (now - (_dt(o["created_at"]) or now)).days)})
    out.sort(key=lambda r: -r["expected_value"])
    return {"method": "Logistic regression on your won and lost deals" if trained else
            "Stage and lead-source win rates (the model trains itself once 5+ deals are won and 5+ lost)",
            "metrics": ({k: trained[k] for k in ("auc_holdout", "auc_train", "trained_on", "positives", "accuracy")} if trained else
                        {"trained_on": len(closed), "positives": sum(y)}) | {"historic_win_rate": round(100 * sum(y) / len(y), 1) if y else None},
            "importance": trained["model"].importance()[:8] if trained else [],
            "opportunities": out,
            "summary": {"open": len(out), "pipeline": round(sum(r["value"] for r in out), 2), "weighted_pipeline": round(sum(r["expected_value"] for r in out), 2),
                        "expected_wins": round(sum(r["probability"] for r in out), 1)}}


def _deal_text(feature: str, delta: float) -> str:
    if "=" in feature:
        k, v = feature.split("=", 1)
        return f"{semantic.humanize(k.split('.')[-1])}: {v}"
    up = delta > 0
    return {"quantity": "Large order quantity" if up else "Small quantity", "max_discount": "High discount asked" if up else "Little discount asked",
            "quotes": "Several quotes" if up else "Few quotes", "revisions": "Many quote revisions" if up else "Few revisions",
            "propensity": "High lead propensity" if up else "Low lead propensity", "days_open": "Open a long time" if up else "Recently opened",
            "log_value": "Large deal" if up else "Small deal", "stage_prior": "Advanced stage" if up else "Early stage"}.get(feature, feature)


# ---------------------------------------------------------------- segments


SEGMENT_FEATURES = ["mrr", "tenure_months", "services", "tickets_180", "usage_30", "outstanding"]


def segments(db: Session, tenant_id: int, access: Any, data: dict[str, Any] | None = None) -> dict[str, Any]:
    data = data or _customer_data(db, tenant_id, access)
    now = _now()
    frame = [r for r in _churn_frame(data) if not r["churned"]]
    tickets, usage, invoices = _by(data["tickets"], "customer.id"), _by(data["usage"], "customer.id"), _by(data["invoices"], "customer.id")
    rows = []
    for r in frame:
        pid = r["party_id"]
        rows.append({"party_id": pid, "name": r["name"], "segment": r["segment"], "values": {
            "mrr": r["mrr"], "tenure_months": r["features"]["tenure_months"], "services": float(r["services"]),
            "tickets_180": float(sum(1 for t in tickets.get(pid, []) if _within(t["created_at"], now - dt.timedelta(days=180), now))),
            "usage_30": sum(u["amount"] or 0 for u in usage.get(pid, []) if _within(u["at"], now - dt.timedelta(days=30), now)),
            "outstanding": sum(i["outstanding"] or 0 for i in invoices.get(pid, []))}})
    if len(rows) < 6:
        return {"segments": [], "method": "k-means", "note": f"Segmentation needs at least 6 live customers - {len(rows)} so far."}
    X = [[math.log1p(max(0.0, r["values"][f])) for f in SEGMENT_FEATURES] for r in rows]
    result = ml.kmeans(X)
    Z = ml.standardise(X)
    out = []
    for c in range(result["k"]):
        members = [r for r, lab in zip(rows, result["labels"]) if lab == c]
        if not members:
            continue
        zc = [sum(z[j] for z, lab in zip(Z, result["labels"]) if lab == c) / len(members) for j in range(len(SEGMENT_FEATURES))]
        avg = {f: round(sum(m["values"][f] for m in members) / len(members), 2) for f in SEGMENT_FEATURES}
        out.append({"id": c, "name": _segment_name(zc), "size": len(members), "share": round(100 * len(members) / len(rows), 1), "averages": avg,
                    "profile": {f: round(z, 2) for f, z in zip(SEGMENT_FEATURES, zc)},
                    "mrr": round(sum(m["values"]["mrr"] for m in members), 2),
                    "members": sorted(({"party_id": m["party_id"], "name": m["name"], "mrr": m["values"]["mrr"]} for m in members),
                                      key=lambda m: -m["mrr"])[:25],
                    "action": _segment_action(zc)})
    out.sort(key=lambda s: -s["mrr"])
    return {"segments": out, "method": f"k-means on MRR, tenure, services, tickets (6 months), usage (30 days) and outstanding balance "
                                       f"(k = {result['k']}, chosen by silhouette)",
            "silhouette": result["silhouette"], "customers": len(rows)}


def _segment_name(z: list[float]) -> str:
    f = dict(zip(SEGMENT_FEATURES, z))
    words = []
    if f["mrr"] > 0.5:
        words.append("High-value")
    elif f["mrr"] < -0.5:
        words.append("Low-value")
    if f["outstanding"] > 0.6:
        words.append("payment-risk")
    if f["tickets_180"] > 0.6:
        words.append("service-heavy")
    if f["usage_30"] > 0.6:
        words.append("heavy users")
    if f["tenure_months"] > 0.5:
        words.append("loyal")
    elif f["tenure_months"] < -0.5:
        words.append("new")
    if not words:
        return "Core customers"
    name = " ".join(words[:3])
    return name[:1].upper() + name[1:] + ("" if words[-1] in ("heavy users",) else " customers")


def _segment_action(z: list[float]) -> str:
    f = dict(zip(SEGMENT_FEATURES, z))
    if f["outstanding"] > 0.6:
        return "Tighten collections: payment reminders, arrangements, credit checks on new orders."
    if f["tickets_180"] > 0.6:
        return "Proactive care: review repeat faults, assign a service manager, offer a goodwill credit."
    if f["mrr"] > 0.5 and f["tenure_months"] > 0.5:
        return "Protect and grow: renewal offers ahead of contract end, upsell bandwidth and managed services."
    if f["tenure_months"] < -0.5:
        return "Onboard well: welcome call, check installation quality, early-life satisfaction survey."
    if f["usage_30"] > 0.6:
        return "Move to a bigger plan before overage bills arrive."
    if f["mrr"] < -0.5:
        return "Cross-sell add-ons (security, voice, extra lines) to raise ARPU."
    return "Keep them engaged: periodic check-ins and relevant bundles."


# ---------------------------------------------------------------- next best offer


def next_offer(db: Session, tenant_id: int, access: Any, limit: int = 50) -> dict[str, Any]:
    lines = _rows(db, tenant_id, access, "order_item", ["offering.name", "order.status", "customer.id", "customer.name", "customer_profile.segment"])
    owned: dict[Any, set[str]] = defaultdict(set)
    names, segs = {}, {}
    for li in lines:
        if li["order.status"] in LIVE_ORDER and li.get("customer.id") and li.get("offering.name"):
            owned[li["customer.id"]].add(li["offering.name"])
            names[li["customer.id"]] = li.get("customer.name")
            segs[li["customer.id"]] = li.get("customer_profile.segment")
    n = len(owned)
    if n < 3:
        return {"rules": [], "recommendations": [], "note": f"Recommendations need at least 3 customers with live products - {n} so far."}
    single = Counter(p for items in owned.values() for p in items)
    pairs = Counter((a, b) for items in owned.values() for a in items for b in items if a != b)
    rules = []
    for (a, b), both in pairs.items():
        conf = both / single[a]
        lift = conf / (single[b] / n)
        if both >= 2 and lift > 1.0:
            rules.append({"if": a, "then": b, "support": round(both / n, 3), "confidence": round(conf, 3), "lift": round(lift, 2), "customers": both})
    rules.sort(key=lambda r: (-r["lift"] * r["confidence"], -r["customers"]))
    popular_by_seg: dict[Any, Counter] = defaultdict(Counter)
    for pid, items in owned.items():
        popular_by_seg[segs.get(pid)].update(items)
    recs = []
    for pid, items in owned.items():
        best = None
        for r in rules:
            if r["if"] in items and r["then"] not in items and (best is None or r["confidence"] * r["lift"] > best["score"]):
                best = {"offer": r["then"], "score": r["confidence"] * r["lift"], "confidence": r["confidence"],
                        "reason": f"{round(100 * r['confidence'])}% of customers with {r['if']} also have it (lift {r['lift']})"}
        if best is None:
            pop = [(p, c) for p, c in popular_by_seg[segs.get(pid)].most_common() if p not in items]
            if pop:
                p, c = pop[0]
                share = c / max(1, sum(1 for q in owned if segs.get(q) == segs.get(pid)))
                best = {"offer": p, "score": share * 0.5, "confidence": round(share, 3), "reason": f"Popular with {segs.get(pid) or 'similar'} customers ({round(100 * share)}%)"}
        if best:
            recs.append({"party_id": pid, "name": names.get(pid), "segment": segs.get(pid), "owns": sorted(items), **best})
    recs.sort(key=lambda r: -r["score"])
    return {"rules": rules[:20], "recommendations": recs[:limit], "customers": n,
            "method": "Association rules (support, confidence, lift) on the products customers hold together; segment popularity when no rule applies"}


# ---------------------------------------------------------------- usage alerts


def usage_alerts(db: Session, tenant_id: int, access: Any, z_threshold: float = 3.0) -> dict[str, Any]:
    now = _now()
    usage = _rows(db, tenant_id, access, "usage", ["customer.id", "customer.name", "at", "amount", "international", "premium"],
                  [{"field": "at", "op": "last_n_days", "value": 36}])
    by_day: dict[Any, dict[dt.date, float]] = defaultdict(lambda: defaultdict(float))
    names, flags = {}, defaultdict(Counter)
    for u in usage:
        d = _dt(u["at"])
        if d is None or not u.get("customer.id"):
            continue
        by_day[u["customer.id"]][d.date()] += u["amount"] or 0
        names[u["customer.id"]] = u.get("customer.name")
        if d >= now - dt.timedelta(days=7):
            flags[u["customer.id"]]["international"] += 1 if u.get("international") else 0
            flags[u["customer.id"]]["premium"] += 1 if u.get("premium") else 0
    out = []
    today = now.date()
    for pid, days in by_day.items():
        base = [days.get(today - dt.timedelta(days=i), 0.0) for i in range(8, 36)]
        recent = [days.get(today - dt.timedelta(days=i), 0.0) for i in range(0, 7)]
        mean = sum(base) / len(base)
        sd = math.sqrt(sum((b - mean) ** 2 for b in base) / len(base))
        level = sum(recent) / len(recent)
        z = (level - mean) / max(sd, 0.1 * mean, 1.0)
        if z >= z_threshold and level > 0:
            reasons = [f"daily spend {round(level, 2)} vs usual {round(mean, 2)}"]
            if flags[pid]["international"]:
                reasons.append(f"{flags[pid]['international']} international events this week")
            if flags[pid]["premium"]:
                reasons.append(f"{flags[pid]['premium']} premium-rate events")
            out.append({"party_id": pid, "name": names.get(pid), "z": round(z, 2), "recent_daily": round(level, 2), "baseline_daily": round(mean, 2),
                        "increase_pct": round(100 * (level - mean) / mean, 1) if mean else None, "reasons": reasons,
                        "risk": "Possible fraud" if flags[pid]["premium"] or flags[pid]["international"] else "Bill shock"})
    out.sort(key=lambda r: -r["z"])
    return {"alerts": out, "customers_checked": len(by_day),
            "method": f"Last 7 days' average daily usage vs each customer's own 4-week baseline; flagged at z ≥ {z_threshold}"}


# ---------------------------------------------------------------- forecasts

PRESETS = [
    {"key": "new_mrr", "label": "New recurring revenue booked", "entity": "order", "date_field": "created_at",
     "measure": {"field": "monthly_price", "agg": "sum"}, "grain": "month", "fmt": "money"},
    {"key": "orders", "label": "New orders", "entity": "order", "date_field": "created_at", "measure": {"agg": "count"}, "grain": "month"},
    {"key": "billed", "label": "Billed revenue", "entity": "invoice", "date_field": "issue_date", "measure": {"field": "total", "agg": "sum"},
     "grain": "month", "fmt": "money"},
    {"key": "collections", "label": "Payments collected", "entity": "payment", "date_field": "received_at", "measure": {"field": "amount", "agg": "sum"},
     "grain": "month", "fmt": "money"},
    {"key": "churned", "label": "Churned services", "entity": "order", "date_field": "updated_at", "measure": {"agg": "count"}, "grain": "month",
     "filters": [{"field": "status", "op": "in", "value": ["cancelled", "terminated"]}]},
    {"key": "tickets", "label": "Ticket volume", "entity": "ticket", "date_field": "created_at", "measure": {"agg": "count"}, "grain": "week"},
    {"key": "leads", "label": "New leads", "entity": "lead", "date_field": "created_at", "measure": {"agg": "count"}, "grain": "month"},
    {"key": "data_usage", "label": "Data traffic (GB)", "entity": "usage", "date_field": "at", "measure": {"field": "gb", "agg": "sum"}, "grain": "day"},
    {"key": "usage_revenue", "label": "Rated usage revenue", "entity": "usage", "date_field": "at", "measure": {"field": "amount", "agg": "sum"},
     "grain": "month", "fmt": "money"},
]


def forecast(db: Session, tenant_id: int, access: Any, spec: dict[str, Any]) -> dict[str, Any]:
    preset = next((p for p in PRESETS if p["key"] == spec.get("preset")), None)
    spec = {**(preset or {}), **{k: v for k, v in spec.items() if v not in (None, "", [])}}
    grain = spec.get("grain") or "month"
    if grain not in ("day", "week", "month", "quarter", "year"):
        raise ValueError("Forecasts run by day, week, month, quarter or year")
    measure = spec.get("measure") or {"agg": "count"}
    pts = engine.series(db, tenant_id, access, spec["entity"], spec.get("date_field"), measure, grain, spec.get("filters") or [])
    if grain == "day":
        pts = pts[-400:]
    # the period still under way is partial - it is shown, but never learned from (it would read as a drop)
    current = engine.grain_value(_now(), grain)
    partial = bool(pts) and pts[-1][0] == current and len(pts) > 2
    if partial:
        pts = pts[:-1]
    labels, values = [p for p, _ in pts], [v for _, v in pts]
    horizon = int(spec.get("horizon") or {"day": 30, "week": 12, "month": 6, "quarter": 4, "year": 2}[grain])
    if not pts:
        return {"label": spec.get("label") or "Forecast", "history": [], "forecast": [], "anomalies": [], "points": 0,
                "note": "No history yet for this measure - nothing to forecast from."}
    out = ml.forecast(labels, values, grain, horizon, lambda lab: engine.next_period(lab, grain))
    m = semantic.model(db, tenant_id, spec["entity"])
    fmt = spec.get("fmt") or (m.fields[measure["field"]].fmt if measure.get("field") and measure.get("agg") not in ("count", "count_distinct") else None)
    return {**out, "label": spec.get("label") or engine._measure_label(m, measure), "entity": spec["entity"], "grain": grain, "fmt": fmt,
            "anomalies": ml.anomalies(labels, values), "partial_period": current if partial else None}

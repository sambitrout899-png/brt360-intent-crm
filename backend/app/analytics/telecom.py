"""Telecom KPIs: the operator's scorecard, read through the reporting model
(so every number respects what the user may see).

  Revenue      MRR, ARR, ARPU, MRR trend, revenue by segment and product
  Customers    active customers, net adds, order and customer churn
  Sales        funnel, conversion, win rate, deal size, sales cycle, pipeline
  Fulfilment   order backlog, fallout, time to activate
  Assurance    open tickets, MTTR, SLA breaches, repeat faults, CSAT, NPS
  Billing      billed, collection rate, DSO, overdue receivables, ageing
  Usage        data / voice / SMS volumes, international share
  Fraud        open alerts, confirmed share

Each KPI: {key, label, value, fmt, hint, lower_is_better}; each chart:
{key, title, kind, labels, series: [{name, values}], fmt}.
"""
from __future__ import annotations

import datetime as dt
from collections import Counter, defaultdict
from typing import Any

from sqlalchemy.orm import Session

from app.analytics import engine, semantic
from app.analytics.semantic import CLOSED_TICKET, LIVE_ORDER, LOST_STAGES

CHURNED_ORDER = ("cancelled", "terminated")


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)


def _dt(v: Any) -> dt.datetime | None:
    return engine._as_datetime(v)


def _rows(db: Session, tenant_id: int, access: Any, entity: str, keys: list[str], filters: list[dict] | None = None) -> list[dict[str, Any]]:
    m = semantic.model(db, tenant_id, entity)
    return engine.read(db, tenant_id, access, m, [k for k in keys if k in m.fields], filters or [])[0]


def _kpi(key: str, label: str, value: Any, fmt: str | None = None, hint: str = "", lower_is_better: bool = False) -> dict[str, Any]:
    if isinstance(value, float):
        value = round(value, 2)
    return {"key": key, "label": label, "value": value, "fmt": fmt, "hint": hint, "lower_is_better": lower_is_better}


def _pct(a: float, b: float) -> float | None:
    return round(100 * a / b, 1) if b else None


def _months(n: int) -> list[dt.datetime]:
    """The starts of the last n months, oldest first."""
    today = _now()
    y, m = today.year, today.month
    out = []
    for _ in range(n):
        out.append(dt.datetime(y, m, 1))
        y, m = (y - 1, 12) if m == 1 else (y, m - 1)
    return list(reversed(out))


def _month_end(start: dt.datetime) -> dt.datetime:
    return dt.datetime(start.year + start.month // 12, start.month % 12 + 1, 1)


def scorecard(db: Session, tenant_id: int, access: Any, months: int = 12) -> dict[str, Any]:
    now = _now()
    d30, d90 = now - dt.timedelta(days=30), now - dt.timedelta(days=90)
    groups: list[dict[str, Any]] = []
    charts: list[dict[str, Any]] = []
    month_starts = _months(months)
    mlabels = [s.strftime("%b %y") for s in month_starts]

    # ---------------- revenue and customers
    orders = _rows(db, tenant_id, access, "order", ["id", "status", "monthly_price", "contract_months", "created_at", "updated_at",
                                                     "customer.id", "customer_profile.segment", "order_type"])
    live = [o for o in orders if o["status"] in LIVE_ORDER]
    mrr = sum(o["monthly_price"] or 0 for o in live)
    customers_live = {o["customer.id"] for o in live}
    churned = [o for o in orders if o["status"] in CHURNED_ORDER]
    churned_90 = [o for o in churned if (_dt(o["updated_at"]) or now) >= d90]
    new_30 = [o for o in orders if (_dt(o["created_at"]) or now) >= d30 and o["status"] in LIVE_ORDER]
    churned_30 = [o for o in churned if (_dt(o["updated_at"]) or now) >= d30]
    base_90 = len(live) + len(churned_90)
    lost_customers = {o["customer.id"] for o in churned_90} - customers_live
    groups.append({"title": "Revenue & customers", "kpis": [
        _kpi("mrr", "MRR", mrr, "money", f"{len(live)} live services"),
        _kpi("arr", "ARR", mrr * 12, "money", "MRR x 12"),
        _kpi("arpu", "ARPU", mrr / len(customers_live) if customers_live else None, "money", f"per paying customer ({len(customers_live)})"),
        _kpi("net_adds", "Net adds (30 d)", len(new_30) - len(churned_30), None, f"+{len(new_30)} new, -{len(churned_30)} churned"),
        _kpi("service_churn", "Service churn (90 d)", _pct(len(churned_90), base_90), "pct", f"{len(churned_90)} cancelled or terminated", True),
        _kpi("customer_churn", "Customer churn (90 d)", _pct(len(lost_customers), len(customers_live) + len(lost_customers)), "pct",
             f"{len(lost_customers)} customers left with nothing live", True),
    ]})

    mrr_series, new_series, churn_series, churn_rate = [], [], [], []
    for start in month_starts:
        end = _month_end(start)
        active = 0.0
        live_at_start = 0
        for o in orders:
            created, updated = _dt(o["created_at"]), _dt(o["updated_at"])
            if not created or created >= end:
                continue
            ended = o["status"] in CHURNED_ORDER and updated is not None
            if o["status"] in LIVE_ORDER or (ended and updated >= end):
                active += o["monthly_price"] or 0
            if created < start and (o["status"] in LIVE_ORDER or (ended and updated >= start)):
                live_at_start += 1
        new = sum(1 for o in orders if (c := _dt(o["created_at"])) and start <= c < end and (o["status"] in LIVE_ORDER or o["status"] in CHURNED_ORDER))
        lost = sum(1 for o in churned if (u := _dt(o["updated_at"])) and start <= u < end)
        mrr_series.append(round(active, 2))
        new_series.append(new)
        churn_series.append(lost)
        churn_rate.append(_pct(lost, live_at_start) or 0.0)
    charts.append({"key": "mrr_trend", "title": "Recurring revenue (MRR)", "kind": "area", "labels": mlabels, "fmt": "money",
                   "series": [{"name": "MRR", "values": mrr_series}]})
    charts.append({"key": "adds_vs_churn", "title": "New vs churned services", "kind": "bar", "labels": mlabels,
                   "series": [{"name": "New", "values": new_series}, {"name": "Churned", "values": churn_series}]})
    charts.append({"key": "churn_rate", "title": "Monthly service churn rate", "kind": "line", "labels": mlabels, "fmt": "pct",
                   "series": [{"name": "Churn %", "values": churn_rate}]})
    by_segment: Counter = Counter()
    for o in live:
        by_segment[o["customer_profile.segment"] or "Unspecified"] += o["monthly_price"] or 0
    charts.append({"key": "mrr_by_segment", "title": "MRR by segment", "kind": "donut", "labels": list(by_segment), "fmt": "money",
                   "series": [{"name": "MRR", "values": [round(v, 2) for v in by_segment.values()]}]})
    lines = _rows(db, tenant_id, access, "order_item", ["offering.name", "line_value", "order.status"])
    by_product: Counter = Counter()
    for li in lines:
        if li["order.status"] in LIVE_ORDER:
            by_product[li["offering.name"] or "Unknown"] += li["line_value"] or 0
    top_products = by_product.most_common(10)
    charts.append({"key": "mrr_by_product", "title": "MRR by product (top 10)", "kind": "hbar", "labels": [p for p, _ in top_products], "fmt": "money",
                   "series": [{"name": "MRR", "values": [round(v, 2) for _, v in top_products]}]})

    # ---------------- sales
    leads = _rows(db, tenant_id, access, "lead", ["id", "status", "created_at", "source"])
    opps = _rows(db, tenant_id, access, "opportunity", ["id", "stage", "created_at", "updated_at"])
    quotes = _rows(db, tenant_id, access, "quote", ["id", "status", "total_price", "opportunity.stage", "opportunity.id"])
    won = [o for o in opps if o["stage"] == "won"]
    lost = [o for o in opps if o["stage"] in LOST_STAGES]
    open_opps = [o for o in opps if o["stage"] != "won" and o["stage"] not in LOST_STAGES]
    approved_quotes = [q for q in quotes if q["status"] == "approved"]
    open_quote_value = sum(q["total_price"] or 0 for q in quotes
                           if q["status"] in ("draft", "pending_approval", "approved") and q.get("opportunity.stage") != "won"
                           and q.get("opportunity.stage") not in LOST_STAGES)
    cycles = [engine._num(semantic._days(o["created_at"], o["updated_at"])) for o in won]
    cycles = [c for c in cycles if c is not None]
    groups.append({"title": "Sales", "kpis": [
        _kpi("leads_30", "New leads (30 d)", sum(1 for x in leads if (_dt(x["created_at"]) or now) >= d30)),
        _kpi("lead_conversion", "Lead → opportunity", _pct(len(opps), len(leads)), "pct", f"{len(opps)} of {len(leads)} leads"),
        _kpi("win_rate", "Win rate", _pct(len(won), len(won) + len(lost)), "pct", f"{len(won)} won, {len(lost)} lost"),
        _kpi("pipeline", "Open pipeline", open_quote_value, "money", f"{len(open_opps)} open opportunities"),
        _kpi("deal_size", "Average deal size", sum(q["total_price"] or 0 for q in approved_quotes) / len(approved_quotes) if approved_quotes else None,
             "money", "approved quotes"),
        _kpi("sales_cycle", "Sales cycle", sum(cycles) / len(cycles) if cycles else None, "days", "opened → won", True),
    ]})
    qualified = sum(1 for x in leads if x["status"] == "qualified")
    quoted_opps = {q.get("opportunity.id") for q in quotes} - {None}
    charts.append({"key": "funnel", "title": "Sales funnel", "kind": "funnel",
                   "labels": ["Leads", "Qualified", "Opportunities", "Quoted", "Won", "Live services"],
                   "series": [{"name": "Count", "values": [len(leads), qualified, len(opps), len(quoted_opps) or sum(1 for o in opps if o["stage"] == "quoted"),
                                                           len(won), len(live)]}]})
    by_source = defaultdict(lambda: [0, 0])
    for x in leads:
        by_source[x["source"] or "Unknown"][0] += 1
        if x["status"] == "qualified":
            by_source[x["source"] or "Unknown"][1] += 1
    src = sorted(by_source.items(), key=lambda kv: -kv[1][0])[:8]
    charts.append({"key": "lead_sources", "title": "Leads by source (and qualified)", "kind": "bar", "labels": [s for s, _ in src],
                   "series": [{"name": "Leads", "values": [v[0] for _, v in src]}, {"name": "Qualified", "values": [v[1] for _, v in src]}]})

    # ---------------- fulfilment
    from app.models.orders import OrderMilestone

    order_ids = [o["id"] for o in orders]
    live_at: dict[int, dt.datetime] = {}
    for i in range(0, len(order_ids), 900):
        for ms in db.query(OrderMilestone).filter(OrderMilestone.order_id.in_(order_ids[i:i + 900]), OrderMilestone.code == "live").all():
            live_at[ms.order_id] = min(ms.reached_at, live_at.get(ms.order_id, ms.reached_at))
    activation = [semantic._days(o["created_at"], live_at[o["id"]]) for o in orders if o["id"] in live_at]
    activation = [a for a in activation if a is not None and a >= 0]
    backlog = [o for o in orders if o["status"] in ("created", "activating", "pending_approval")]
    fallout = [o for o in orders if o["status"] == "rejected"]
    groups.append({"title": "Order fulfilment", "kpis": [
        _kpi("backlog", "Order backlog", len(backlog), None, "created, awaiting approval or activating", True),
        _kpi("activation_days", "Time to activate", sum(activation) / len(activation) if activation else None, "days",
             f"order → service live ({len(activation)} orders)", True),
        _kpi("fallout", "Order fallout", _pct(len(fallout), len(orders)), "pct", f"{len(fallout)} rejected", True),
        _kpi("orders_30", "Orders (30 d)", sum(1 for o in orders if (_dt(o["created_at"]) or now) >= d30)),
    ]})
    status_mix = Counter(o["status"] for o in orders)
    charts.append({"key": "order_status", "title": "Orders by status", "kind": "donut", "labels": list(status_mix),
                   "series": [{"name": "Orders", "values": list(status_mix.values())}]})

    # ---------------- assurance
    tickets = _rows(db, tenant_id, access, "ticket", ["id", "status", "severity", "category", "created_at", "updated_at", "customer.id"])
    surveys = _rows(db, tenant_id, access, "survey", ["score", "would_recommend", "overall"])
    open_t = [t for t in tickets if t["status"] not in CLOSED_TICKET]
    resolved_90 = [t for t in tickets if t["status"] in CLOSED_TICKET and (_dt(t["updated_at"]) or now) >= d90]
    mttr = [(semantic._days(t["created_at"], t["updated_at"]) or 0) * 24 for t in resolved_90]
    recent = [t for t in tickets if (_dt(t["created_at"]) or now) >= d30]
    per_customer = Counter(t["customer.id"] for t in recent if t["customer.id"])
    rec = [s["would_recommend"] for s in surveys if s["would_recommend"] is not None]
    nps = round(100 * (sum(1 for r in rec if r >= 9) - sum(1 for r in rec if r <= 6)) / len(rec), 1) if rec else None
    csat = [s["overall"] for s in surveys if s["overall"]]
    from app.models.sla import SlaClock

    clocks = db.query(SlaClock.status).filter(SlaClock.tenant_id == tenant_id).all() if (access is None or access.unrestricted) else []
    breached = sum(1 for (s,) in clocks if engine.plain(s) == "breached")
    decided = sum(1 for (s,) in clocks if engine.plain(s) in ("breached", "completed"))
    groups.append({"title": "Service assurance", "kpis": [
        _kpi("open_tickets", "Open tickets", len(open_t), None, f"{sum(1 for t in open_t if t['severity'] in ('high', 'critical'))} high or critical", True),
        _kpi("mttr", "MTTR", sum(mttr) / len(mttr) if mttr else None, "hours", f"mean time to resolve ({len(mttr)} in 90 d)", True),
        _kpi("sla_breach", "SLA breaches", _pct(breached, decided), "pct", f"{breached} of {decided} clocks" if decided else "no SLA clocks yet", True),
        _kpi("repeat", "Repeat faults (30 d)", _pct(sum(1 for c in per_customer.values() if c > 1), len(per_customer)), "pct",
             "customers with 2+ tickets", True),
        _kpi("csat", "CSAT", round(sum(csat) / len(csat), 2) if csat else None, None, f"average of {len(csat)} surveys (1-5)"),
        _kpi("nps", "NPS", nps, None, f"{len(rec)} answers"),
    ]})
    t_series, r_series = [], []
    for start in month_starts:
        end = _month_end(start)
        t_series.append(sum(1 for t in tickets if (c := _dt(t["created_at"])) and start <= c < end))
        r_series.append(sum(1 for t in tickets if t["status"] in CLOSED_TICKET and (u := _dt(t["updated_at"])) and start <= u < end))
    charts.append({"key": "tickets_trend", "title": "Tickets opened vs resolved", "kind": "line", "labels": mlabels,
                   "series": [{"name": "Opened", "values": t_series}, {"name": "Resolved", "values": r_series}]})
    cat = Counter(t["category"] or "Other" for t in tickets).most_common(8)
    charts.append({"key": "ticket_categories", "title": "Tickets by category", "kind": "hbar", "labels": [c for c, _ in cat],
                   "series": [{"name": "Tickets", "values": [n for _, n in cat]}]})

    # ---------------- billing
    invoices = _rows(db, tenant_id, access, "invoice", ["issue_date", "due_date", "total", "paid", "credited", "outstanding", "aging_bucket"])
    billed_30 = sum(i["total"] or 0 for i in invoices if (_dt(i["issue_date"]) or now) >= d30)
    inv_90 = [i for i in invoices if (_dt(i["issue_date"]) or now) >= d90]
    billed_90 = sum(i["total"] or 0 for i in inv_90)
    collected_90 = sum((i["paid"] or 0) + (i["credited"] or 0) for i in inv_90)
    ar = sum(i["outstanding"] or 0 for i in invoices)
    overdue = sum(i["outstanding"] or 0 for i in invoices if i["aging_bucket"] not in ("Settled", "Current"))
    groups.append({"title": "Billing & collections", "kpis": [
        _kpi("billed_30", "Billed (30 d)", billed_30, "money"),
        _kpi("collection_rate", "Collection rate (90 d)", _pct(collected_90, billed_90), "pct", "paid or credited of billed"),
        _kpi("dso", "DSO", ar / (billed_90 / 90) if billed_90 else None, "days", "days sales outstanding", True),
        _kpi("overdue", "Overdue receivables", overdue, "money", f"of {round(ar, 2):,} outstanding", True),
    ]})
    buckets = ["Current", "1-30 days", "31-60 days", "61-90 days", "90+ days"]
    aging = defaultdict(float)
    for i in invoices:
        aging[i["aging_bucket"]] += i["outstanding"] or 0
    charts.append({"key": "ar_aging", "title": "Receivables ageing", "kind": "bar", "labels": buckets, "fmt": "money",
                   "series": [{"name": "Outstanding", "values": [round(aging[b], 2) for b in buckets]}]})

    # ---------------- usage and fraud
    usage = _rows(db, tenant_id, access, "usage", ["at", "event_type", "quantity", "amount", "international", "gb", "minutes", "messages"],
                  [{"field": "at", "op": "last_n_days", "value": 31 * 6}])
    u30 = [u for u in usage if (_dt(u["at"]) or now) >= d30]
    groups.append({"title": "Network usage", "kpis": [
        _kpi("data_gb", "Data (30 d)", sum(u["gb"] or 0 for u in u30), "gb"),
        _kpi("voice_min", "Voice (30 d)", sum(u["minutes"] or 0 for u in u30), None, "minutes"),
        _kpi("sms", "SMS (30 d)", sum(u["messages"] or 0 for u in u30), None, "messages"),
        _kpi("intl_share", "International share", _pct(sum(1 for u in u30 if u["international"]), len(u30)), "pct", "of usage events"),
        _kpi("usage_revenue", "Rated usage (30 d)", sum(u["amount"] or 0 for u in u30), "money"),
    ]})
    six = month_starts[-6:]
    charts.append({"key": "usage_mix", "title": "Usage by type (rated amount)", "kind": "stacked", "labels": [s.strftime("%b %y") for s in six], "fmt": "money",
                   "series": [{"name": kind.title(), "values": [round(sum(u["amount"] or 0 for u in usage if u["event_type"] == kind
                                                                          and (a := _dt(u["at"])) and s <= a < _month_end(s)), 2) for s in six]}
                              for kind in ("data", "voice", "sms")]})
    fraud = _rows(db, tenant_id, access, "fraud_alert", ["status", "fraud_type", "observed"])
    confirmed = sum(1 for f in fraud if f["status"] == "confirmed")
    closed = sum(1 for f in fraud if f["status"] in ("confirmed", "false_positive", "closed"))
    groups.append({"title": "Fraud", "kpis": [
        _kpi("fraud_open", "Open fraud alerts", sum(1 for f in fraud if f["status"] in ("open", "investigating")), None, "", True),
        _kpi("fraud_confirmed", "Confirmed fraud", _pct(confirmed, closed), "pct", f"{confirmed} of {closed} decided alerts"),
    ]})
    return {"groups": groups, "charts": charts, "generated_at": now.isoformat(timespec="seconds"),
            "notes": _notes(groups)}


def _notes(groups: list[dict[str, Any]]) -> list[dict[str, str]]:
    """The scorecard in a few sentences - what stands out, good and bad."""
    k = {x["key"]: x for g in groups for x in g["kpis"]}
    out = []

    def val(key: str) -> Any:
        return (k.get(key) or {}).get("value")

    if (val("service_churn") or 0) > 5:
        out.append({"tone": "bad", "text": f"Service churn is {val('service_churn')}% over 90 days - see Predictions → Churn risk for who is next."})
    if (val("collection_rate") or 100) < 85:
        out.append({"tone": "warn", "text": f"Only {val('collection_rate')}% of the last 90 days' billing is collected; DSO is {val('dso') or '-'} days."})
    if (val("sla_breach") or 0) > 10:
        out.append({"tone": "bad", "text": f"{val('sla_breach')}% of SLA clocks breached."})
    if (val("win_rate") or 0) >= 40:
        out.append({"tone": "good", "text": f"Win rate is healthy at {val('win_rate')}%."})
    if (val("net_adds") or 0) < 0:
        out.append({"tone": "bad", "text": f"Net adds are negative ({val('net_adds')}) for the last 30 days - more services left than joined."})
    elif (val("net_adds") or 0) > 0:
        out.append({"tone": "good", "text": f"{val('net_adds')} net new services in the last 30 days."})
    if (val("repeat") or 0) > 20:
        out.append({"tone": "warn", "text": f"{val('repeat')}% of customers who raised a ticket raised another within 30 days - look for repeat faults."})
    return out

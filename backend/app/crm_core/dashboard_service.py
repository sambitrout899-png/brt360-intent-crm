"""Tenant dashboard insights - KPIs with trends, the lead-to-cash funnel,
pipeline, revenue mix, what needs attention, intent activity, the team and
plain-language insights.

Every figure is computed over the records the caller can see (the same
visibility as the rest of the CRM), so a sales executive reads their own
book and a department head their department's.
"""

from __future__ import annotations

import datetime as dt
from collections import Counter, defaultdict
from typing import Any

from sqlalchemy.orm import Session

from app.crm_core import access_service
from app.crm_core.access_service import Access
from app.models.assurance import TroubleTicket, WorkOrder
from app.models.auth import User
from app.models.catalog import ProductOffering
from app.models.crm import Lead, LeadStatus, Opportunity, OpportunityStage, Party, Quote, QuoteStatus
from app.models.intent_log import IntentExecutionLog
from app.models.orders import Order, OrderStatus
from app.timeutils import utcnow

LIVE = (OrderStatus.active, OrderStatus.changed)
OPEN_STAGES = (OpportunityStage.draft, OpportunityStage.qualified, OpportunityStage.quoted, OpportunityStage.configured,
               OpportunityStage.in_feasibility, OpportunityStage.feasibility_failed, OpportunityStage.feasible)
STAGE_LABELS = {"draft": "New", "qualified": "Qualified", "configured": "Configured", "in_feasibility": "With Presales",
                "feasibility_failed": "Not feasible", "feasible": "Feasible", "quoted": "Quoted", "won": "Won", "lost": "Lost"}


def _pct(new: float, old: float) -> float | None:
    if not old:
        return None if not new else 100.0
    return round((new - old) / old * 100, 1)


def _days(a: dt.datetime | None, b: dt.datetime | None) -> float | None:
    return round((b - a).total_seconds() / 86400, 1) if a and b else None


def _month_end(d: dt.datetime) -> dt.datetime:
    nxt = (d.replace(day=28) + dt.timedelta(days=4)).replace(day=1)
    return nxt - dt.timedelta(microseconds=1)


def insights(db: Session, tenant_id: int, access: Access, days: int = 30) -> dict[str, Any]:
    from app.intent_engine import intent_registry

    labels: dict[str, str] = {}

    def label_of(code: str) -> str:
        if code not in labels:
            labels[code] = intent_registry.label_of(db, tenant_id, code) or code
        return labels[code]

    now = utcnow()
    start, prev_start = now - dt.timedelta(days=days), now - dt.timedelta(days=2 * days)

    def seen(model, entity_type):
        return access_service.apply(db.query(model).filter(model.tenant_id == tenant_id), model, db, access, entity_type)

    parties = seen(Party, "party").all()
    leads = seen(Lead, "lead").all()
    opps = seen(Opportunity, "opportunity").all()
    quotes = seen(Quote, "quote").all()
    orders = seen(Order, "order").all()
    live = [o for o in orders if o.status in LIVE]
    from types import SimpleNamespace
    from app.crm_core import abo_service
    from app.models.fulfillment import AssetStatus
    import json
    for party in parties:
        for asset in abo_service.customer_assets(db, tenant_id, party.id):
            if asset.status != AssetStatus.active:
                continue
            data = json.loads(asset.instance_json or "{}")
            live.append(SimpleNamespace(party_id=party.id, monthly_price=abo_service.recurring_price(asset),
                created_at=asset.created_at, sys_created_by=asset.sys_created_by,
                items=[SimpleNamespace(product_offering_id=data.get("product_offering_id"))]))
    offering_names = {o.id: o.name for o in db.query(ProductOffering).filter(ProductOffering.tenant_id == tenant_id)}

    # ---------------- KPIs
    mrr = sum(o.monthly_price for o in live)
    mrr_before = sum(o.monthly_price for o in live if o.created_at <= start)
    new_customers = sum(1 for p in parties if p.created_at >= start)
    new_customers_prev = sum(1 for p in parties if prev_start <= p.created_at < start)
    new_orders = [o for o in orders if o.created_at >= start]
    new_orders_prev = [o for o in orders if prev_start <= o.created_at < start]
    new_mrr = sum(o.monthly_price for o in live if o.created_at >= start)
    new_mrr_prev = sum(o.monthly_price for o in live if prev_start <= o.created_at < start)
    open_quotes = [q for q in quotes if q.status in (QuoteStatus.draft, QuoteStatus.pending_approval, QuoteStatus.approved)
                   and not any(o.quote_id == q.id for o in orders)]
    pipeline = sum(q.total_price for q in open_quotes)
    won = sum(1 for o in opps if o.stage == OpportunityStage.won)
    lost = sum(1 for o in opps if o.stage == OpportunityStage.lost)
    # an opportunity whose quote became an order counts as won too
    ordered_opps = {q.opportunity_id for q in quotes if any(o.quote_id == q.id for o in orders)}
    won_total = len({o.id for o in opps if o.stage == OpportunityStage.won} | ordered_opps)
    win_rate = round(won_total / (won_total + lost) * 100, 1) if (won_total + lost) else None
    quote_by_id = {q.id: q for q in quotes}
    cycle = [d for o in new_orders if o.quote_id in quote_by_id
             and (d := _days(quote_by_id[o.quote_id].created_at, o.created_at)) is not None]
    avg_cycle = round(sum(cycle) / len(cycle), 1) if cycle else None
    logs = db.query(IntentExecutionLog).filter(IntentExecutionLog.tenant_id == tenant_id, IntentExecutionLog.created_at >= prev_start)
    if not access.unrestricted:
        logs = logs.filter(IntentExecutionLog.user_id == access.user.id) if access.scope == "own" else logs
    logs = logs.all()
    period_logs = [log for log in logs if log.created_at >= start]
    prev_logs = [log for log in logs if log.created_at < start]
    automation = round(sum(1 for log in period_logs if log.status == "executed") / len(period_logs) * 100, 1) if period_logs else None
    automation_prev = round(sum(1 for log in prev_logs if log.status == "executed") / len(prev_logs) * 100, 1) if prev_logs else None
    arpa_accounts = {o.party_id for o in live}
    arpa = round(mrr / len(arpa_accounts), 2) if arpa_accounts else 0.0

    kpis = [
        {"key": "mrr", "label": "Monthly recurring revenue", "value": round(mrr, 2), "format": "money", "delta": _pct(mrr, mrr_before),
         "hint": f"{len(live)} live services"},
        {"key": "new_mrr", "label": f"New MRR ({days} days)", "value": round(new_mrr, 2), "format": "money", "delta": _pct(new_mrr, new_mrr_prev),
         "hint": f"{len(new_orders)} new orders"},
        {"key": "pipeline", "label": "Open pipeline", "value": round(pipeline, 2), "format": "money", "delta": None,
         "hint": f"{len(open_quotes)} open quotes · {sum(1 for o in opps if o.stage in OPEN_STAGES)} open opportunities"},
        {"key": "win_rate", "label": "Win rate", "value": win_rate, "format": "pct", "delta": None, "hint": f"{won_total} won · {lost} lost"},
        {"key": "new_customers", "label": f"New customers ({days} days)", "value": new_customers, "format": "count",
         "delta": _pct(new_customers, new_customers_prev), "hint": f"{len(parties)} in total"},
        {"key": "arpa", "label": "Revenue per account", "value": arpa, "format": "money", "delta": None, "hint": f"{len(arpa_accounts)} paying accounts"},
        {"key": "cycle", "label": "Quote to order", "value": avg_cycle, "format": "days", "delta": None, "lower_is_better": True,
         "hint": f"average over {len(cycle)} orders" if cycle else "no orders from quotes yet"},
        {"key": "automation", "label": "Intents done straight through", "value": automation, "format": "pct",
         "delta": round(automation - automation_prev, 1) if automation is not None and automation_prev is not None else None,
         "delta_points": True, "hint": f"{len(period_logs)} intents run"},
    ]

    # ---------------- MRR trend (6 months, live services by when they started)
    trend = []
    month = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    for i in range(5, -1, -1):
        m = month
        for _ in range(i):
            m = (m - dt.timedelta(days=1)).replace(day=1)
        end = _month_end(m) if i else now
        trend.append({"month": m.strftime("%b %Y"), "mrr": round(sum(o.monthly_price for o in live if o.created_at <= end), 2),
                      "new": round(sum(o.monthly_price for o in live if m <= o.created_at <= end), 2)})

    # ---------------- lead-to-cash funnel (all time, visible records)
    prospects = len(parties)
    qualified = sum(1 for lead in leads if lead.status == LeadStatus.qualified)
    quoted_opps = {q.opportunity_id for q in quotes}
    approved = sum(1 for q in quotes if q.status == QuoteStatus.approved or any(o.quote_id == q.id for o in orders))
    funnel_steps = [("Customers & prospects", prospects), ("Leads", len(leads)), ("Qualified leads", qualified), ("Opportunities", len(opps)),
                    ("Quoted", len(quoted_opps)), ("Quotes approved", approved), ("Orders", len(orders)), ("Live services", len(live))]
    funnel = []
    for i, (label, count) in enumerate(funnel_steps):
        prev = funnel_steps[i - 1][1] if i else None
        funnel.append({"label": label, "count": count, "conversion": round(count / prev * 100, 1) if prev else None})

    # ---------------- pipeline by stage
    latest_quote: dict[int, Quote] = {}
    for q in sorted(quotes, key=lambda q: q.id):
        if q.status not in (QuoteStatus.cancelled, QuoteStatus.superseded):
            latest_quote[q.opportunity_id] = q
    stages = defaultdict(lambda: {"count": 0, "value": 0.0})
    for o in opps:
        s = stages[o.stage.value]
        s["count"] += 1
        s["value"] += latest_quote[o.id].total_price if o.id in latest_quote else 0.0
    order_of_stages = ["draft", "qualified", "configured", "in_feasibility", "feasible", "feasibility_failed", "quoted", "won", "lost"]
    pipeline_by_stage = [{"stage": k, "label": STAGE_LABELS.get(k, k), "count": stages[k]["count"], "value": round(stages[k]["value"], 2)}
                         for k in order_of_stages if k in stages]

    # ---------------- revenue mix
    by_product: Counter = Counter()
    by_segment: Counter = Counter()
    party_by_id = {p.id: p for p in parties}
    for o in live:
        name = offering_names.get(o.items[0].product_offering_id, "Other") if o.items else "Other"
        by_product[name] += o.monthly_price
        party = party_by_id.get(o.party_id)
        seg = (party.profile.segment if party is not None and party.profile is not None and party.profile.segment else None) or \
              ("B2C" if party is not None and party.party_type == "individual" else "B2B")
        by_segment[seg] += o.monthly_price
    revenue_by_product = [{"name": n, "mrr": round(v, 2), "share": round(v / mrr * 100, 1) if mrr else 0} for n, v in by_product.most_common(8)]
    revenue_by_segment = [{"name": n, "mrr": round(v, 2), "share": round(v / mrr * 100, 1) if mrr else 0} for n, v in by_segment.most_common()]

    # ---------------- needs attention
    attention: list[dict[str, Any]] = []

    def stuck(title: str, items: list, entity_type: str, label, older_than: float, severity: str, action: str | None = None) -> None:
        old = [i for i in items if (_days(getattr(i, "updated_at", None) or i.created_at, now) or 0) >= older_than]
        if old:
            attention.append({
                "title": title, "count": len(old), "severity": severity, "entity_type": entity_type, "intent": action,
                "records": [{"id": i.id, "label": label(i), "age_days": _days(getattr(i, "updated_at", None) or i.created_at, now)}
                            for i in sorted(old, key=lambda i: (getattr(i, "updated_at", None) or i.created_at))[:5]],
            })

    stuck("Quotes waiting for approval", [q for q in quotes if q.status == QuoteStatus.pending_approval], "quote",
          lambda q: f"Quote #{q.id} · {q.total_price:,.0f}", 2, "high", "INT-36")
    stuck("Approved quotes with no customer PO", [q for q in quotes if q.status == QuoteStatus.approved and q.purchase_order is None
                                                  and not any(o.quote_id == q.id for o in orders)],
          "quote", lambda q: f"Quote #{q.id} · {q.total_price:,.0f}", 5, "medium", "INT-39")
    stuck("Leads waiting to be qualified", [lead for lead in leads if lead.status == LeadStatus.pending_qualification], "lead",
          lambda lead: f"Lead #{lead.id} · {lead.party.name if lead.party else ''}", 7, "medium", "INT-17")
    stuck("Opportunities with Presales", [o for o in opps if o.stage == OpportunityStage.in_feasibility], "opportunity",
          lambda o: o.name, 5, "medium", "INT-18")
    stuck("Orders not yet live", [o for o in orders if o.status in (OrderStatus.created, OrderStatus.pending_approval, OrderStatus.activating)],
          "order", lambda o: f"Order #{o.id} · {o.status.value.replace('_', ' ')}", 3, "high", None)
    ticket_q = db.query(TroubleTicket).filter(TroubleTicket.tenant_id == tenant_id, TroubleTicket.status != "resolved")
    tickets = ticket_q.all()
    visible_parties = access_service.visible_ids(db, access, "party")
    if visible_parties is not None:
        tickets = [t for t in tickets if t.party_id is None or t.party_id in visible_parties]
    urgent = [t for t in tickets if t.severity in ("critical", "high")]
    if urgent:
        attention.append({"title": "Critical / high tickets open", "count": len(urgent), "severity": "critical", "entity_type": "ticket", "intent": None,
                          "records": [{"id": t.id, "label": f"#{t.id} {t.severity} · {(t.description or '')[:50]}", "age_days": _days(t.created_at, now)}
                                      for t in sorted(urgent, key=lambda t: t.created_at)[:5]]})
    failed = [log for log in period_logs if log.status == "failed"]
    if failed:
        attention.append({"title": f"Intents that failed ({days} days)", "count": len(failed), "severity": "low", "entity_type": "intent", "intent": None,
                          "records": [{"id": log.id, "label": f"{label_of(log.intent_type)}: {(log.rationale or '')[:60]}",
                                       "age_days": _days(log.created_at, now)} for log in failed[-5:]]})
    if access.user.role.value == "admin":
        from app.crm_core import product_hub

        changed = [n for n in {o for o in offering_names.values()} if product_hub.model_status(db, tenant_id, n)["status"] == "released_changed"]
        if changed:
            attention.append({"title": "Product models with unreleased changes", "count": len(changed), "severity": "low", "entity_type": "product",
                              "intent": "INT-48", "records": [{"id": 0, "label": n, "age_days": None} for n in sorted(changed)[:5]]})
    rank = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    attention.sort(key=lambda a: (rank[a["severity"]], -a["count"]))

    # ---------------- intent activity
    by_day: dict[str, Counter] = {}
    for i in range(min(days, 30) - 1, -1, -1):
        by_day[(now - dt.timedelta(days=i)).strftime("%Y-%m-%d")] = Counter()
    for log in period_logs:
        key = log.created_at.strftime("%Y-%m-%d")
        if key in by_day:
            by_day[key][log.status] += 1
    activity = [{"date": k, "executed": c["executed"], "failed": c["failed"], "review": c["needs_review"] + c["pending"]} for k, c in by_day.items()]
    top = Counter(log.intent_type for log in period_logs if log.intent_type)
    top_intents = [{"code": code, "label": label_of(code), "count": n} for code, n in top.most_common(6)]

    # ---------------- team
    users = {u.id: u for u in db.query(User).filter(User.tenant_id == tenant_id)}
    team: dict[int, dict[str, Any]] = {}
    for o in live:
        uid = o.sys_created_by
        if uid in users:
            team.setdefault(uid, {"mrr": 0.0, "orders": 0, "intents": 0})
            team[uid]["mrr"] += o.monthly_price
            team[uid]["orders"] += 1
    for log in period_logs:
        if log.user_id in users and log.status == "executed":
            team.setdefault(log.user_id, {"mrr": 0.0, "orders": 0, "intents": 0})["intents"] += 1
    leaderboard = sorted(({"user": users[u].name or users[u].email, "mrr": round(v["mrr"], 2), "orders": v["orders"], "intents": v["intents"]}
                          for u, v in team.items()), key=lambda r: (-r["mrr"], -r["intents"]))[:6]

    severities = ["critical", "high", "medium", "low"]
    tickets_by_severity = {s: sum(1 for t in tickets if t.severity == s) for s in severities}
    open_work_orders = db.query(WorkOrder).filter(WorkOrder.tenant_id == tenant_id, WorkOrder.status.notin_(("complete", "cancelled"))).count()

    out = {
        "period_days": days, "as_of": now, "scope": access.scope, "kpis": kpis, "mrr_trend": trend, "funnel": funnel,
        "pipeline_by_stage": pipeline_by_stage, "revenue_by_product": revenue_by_product, "revenue_by_segment": revenue_by_segment,
        "attention": attention, "activity": activity, "top_intents": top_intents, "leaderboard": leaderboard,
        "tickets_by_severity": tickets_by_severity, "open_work_orders": open_work_orders,
    }
    out["insights"] = _narrate(out, days)
    return out


def _narrate(d: dict[str, Any], days: int) -> list[dict[str, str]]:
    """Plain-language observations, most useful first."""
    k = {x["key"]: x for x in d["kpis"]}
    out: list[dict[str, str]] = []
    mrr = k["mrr"]
    if mrr["delta"] is not None and mrr["value"]:
        out.append({"tone": "good" if mrr["delta"] >= 0 else "bad", "icon": "📈" if mrr["delta"] >= 0 else "📉",
                    "text": f"MRR is {mrr['value']:,.0f}, {'up' if mrr['delta'] >= 0 else 'down'} {abs(mrr['delta']):g}% on {days} days ago."})
    if d["revenue_by_product"] and d["revenue_by_product"][0]["share"] >= 40 and len(d["revenue_by_product"]) > 1:
        top = d["revenue_by_product"][0]
        out.append({"tone": "warn", "icon": "🎯", "text": f"{top['name']} brings {top['share']:g}% of MRR - the book leans heavily on one product."})
    elif d["revenue_by_product"]:
        top = d["revenue_by_product"][0]
        out.append({"tone": "info", "icon": "🏆", "text": f"Top product: {top['name']} with {top['mrr']:,.0f} a month ({top['share']:g}% of MRR)."})
    funnel = d["funnel"]
    weakest = min((f for f in funnel[1:] if f["conversion"] is not None and funnel[funnel.index(f) - 1]["count"] >= 3), key=lambda f: f["conversion"], default=None)
    if weakest and weakest["conversion"] < 60:
        prev = funnel[funnel.index(weakest) - 1]["label"]
        out.append({"tone": "warn", "icon": "🔍", "text": f"Biggest drop in the funnel: only {weakest['conversion']:g}% of {prev.lower()} become {weakest['label'].lower()}."})
    if k["win_rate"]["value"] is not None:
        wr = k["win_rate"]["value"]
        out.append({"tone": "good" if wr >= 50 else "warn", "icon": "🤝", "text": f"Win rate is {wr:g}% ({k['win_rate']['hint']})."})
    stuck = [a for a in d["attention"] if a["severity"] in ("critical", "high")]
    if stuck:
        out.append({"tone": "bad", "icon": "⏳", "text": "Needs attention now: " + "; ".join(f"{a['count']} {a['title'].lower()}" for a in stuck[:3]) + "."})
    if k["pipeline"]["value"]:
        out.append({"tone": "info", "icon": "🧮", "text": f"{k['pipeline']['value']:,.0f} of monthly value is in open quotes ({k['pipeline']['hint']})."})
    if k["cycle"]["value"] is not None:
        out.append({"tone": "info", "icon": "⏱️", "text": f"A quote takes {k['cycle']['value']:g} days on average to become an order."})
    if k["automation"]["value"] is not None:
        out.append({"tone": "good" if k["automation"]["value"] >= 70 else "warn", "icon": "🤖",
                    "text": f"{k['automation']['value']:g}% of intents ran straight through without anyone stepping in."})
    if d["top_intents"]:
        t = d["top_intents"][0]
        out.append({"tone": "info", "icon": "⚡", "text": f"Most used action: {t['label']} ({t['count']} times)."})
    if not out:
        out.append({"tone": "info", "icon": "🌱", "text": "Not much data yet - add prospects, quote and activate orders and this page fills in."})
    return out[:6]

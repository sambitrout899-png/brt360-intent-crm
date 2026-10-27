"""Builds one customer's 360 view from a layout.

For every dataset the layout uses, rows are loaded once - from BRT 360's
own records (respecting the caller's data visibility, like the classic
party_360 endpoint) or from the connector the widget names - then each
widget aggregates them into exactly what its chart draws: a value, a
list of categories, a monthly series or table rows. One failing system
never breaks the page: its widgets carry the error, the rest render.

insights() reads across all of it - tickets, invoices, network, deals -
and says what deserves attention, each with the intent that acts on it.
"""

from __future__ import annotations

import datetime as dt
from collections import defaultdict
from typing import Any

from sqlalchemy.orm import Session

from app.c360 import catalog, connectors
from app.c360.catalog import DATASETS, RECIPES
from app.c360.connectors import ConnectorError, ConnectorSpec
from app.crm_core import access_service
from app.crm_core.access_service import Access
from app.models.accounts import ServiceAccount
from app.models.assurance import TroubleTicket, WorkOrder
from app.models.crm import Lead, Opportunity, Party, Quote
from app.models.fulfillment import Asset
from app.models.identity import IdentityVerification
from app.models.orders import BillingAccount, Order, Resource, ServiceOrder

BRT = "brt"
BRT_LABEL = "BRT 360"


def _v(value: Any) -> Any:
    return getattr(value, "value", value)


def _iso(value: Any) -> Any:
    return value.isoformat() if isinstance(value, (dt.datetime, dt.date)) else value


# ---------------------------------------------------------------- BRT 360's own data


class InternalData:
    """Lazy loader of this CRM's records for one customer."""

    def __init__(self, db: Session, tenant_id: int, access: Access | None, party: Party):
        self.db, self.tenant_id, self.access, self.party = db, tenant_id, access, party
        self._cache: dict[str, list[dict[str, Any]]] = {}

    def _apply(self, query, model, entity_type: str):
        return access_service.apply(query, model, self.db, self.access, entity_type) if self.access else query

    def _lead_ids(self) -> list[int]:
        return [i for (i,) in self.db.query(Lead.id).filter(Lead.tenant_id == self.tenant_id, Lead.party_id == self.party.id).all()]

    def _opp_ids(self) -> list[int]:
        lead_ids = self._lead_ids()
        return [i for (i,) in self.db.query(Opportunity.id).filter(Opportunity.lead_id.in_(lead_ids)).all()] if lead_ids else []

    def rows(self, dataset: str) -> list[dict[str, Any]]:
        if dataset not in self._cache:
            loader = getattr(self, f"_load_{dataset}", None)
            self._cache[dataset] = loader() if loader else []
        return self._cache[dataset]

    def _load_leads(self) -> list[dict[str, Any]]:
        q = self._apply(self.db.query(Lead).filter(Lead.tenant_id == self.tenant_id, Lead.party_id == self.party.id), Lead, "lead")
        return [{"id": l.id, "status": _v(l.status), "source": l.source, "region": l.region,
                 "propensity_score": round((l.propensity_score or 0) * (100 if (l.propensity_score or 0) <= 1 else 1), 1),
                 "offering": l.interested_offering_name, "created_at": _iso(l.created_at)} for l in q.all()]

    def _load_opportunities(self) -> list[dict[str, Any]]:
        lead_ids = self._lead_ids()
        if not lead_ids:
            return []
        q = self._apply(self.db.query(Opportunity).filter(Opportunity.tenant_id == self.tenant_id, Opportunity.lead_id.in_(lead_ids)),
                        Opportunity, "opportunity")
        out = []
        for o in q.all():
            latest = max(o.quotes, key=lambda x: x.id) if o.quotes else None
            out.append({"id": o.id, "name": o.name, "stage": _v(o.stage), "offering": o.offering_name, "quantity": o.quantity,
                        "value": latest.total_price if latest else 0.0, "created_at": _iso(o.created_at)})
        return out

    def _load_quotes(self) -> list[dict[str, Any]]:
        opp_ids = self._opp_ids()
        if not opp_ids:
            return []
        q = self._apply(self.db.query(Quote).filter(Quote.tenant_id == self.tenant_id, Quote.opportunity_id.in_(opp_ids)), Quote, "quote")
        return [{"id": x.id, "status": _v(x.status), "amount": x.total_price, "discount_pct": x.requested_discount_pct,
                 "created_at": _iso(x.created_at)} for x in q.all()]

    def _load_orders(self) -> list[dict[str, Any]]:
        q = self._apply(self.db.query(Order).filter(Order.tenant_id == self.tenant_id, Order.party_id == self.party.id), Order, "order")
        return [{"id": o.id, "status": _v(o.status), "order_type": _v(o.order_type), "monthly_price": o.monthly_price,
                 "contract_months": o.contract_months, "contract_value": round(o.monthly_price * o.contract_months, 2),
                 "created_at": _iso(o.created_at)} for o in q.all()]

    def _load_tickets(self) -> list[dict[str, Any]]:
        q = self.db.query(TroubleTicket).filter(TroubleTicket.tenant_id == self.tenant_id, TroubleTicket.party_id == self.party.id)
        return [{"id": t.id, "category": t.category, "severity": t.severity, "priority": t.priority, "status": t.status,
                 "sla_class": t.sla_class, "created_at": _iso(t.created_at)} for t in q.all()]

    def _load_work_orders(self) -> list[dict[str, Any]]:
        ticket_ids = [t["id"] for t in self.rows("tickets")]
        order_ids = [o["id"] for o in self.rows("orders")]
        if not ticket_ids and not order_ids:
            return []
        q = self.db.query(WorkOrder).filter(WorkOrder.tenant_id == self.tenant_id)
        q = q.filter((WorkOrder.ticket_id.in_(ticket_ids or [-1])) | (WorkOrder.order_id.in_(order_ids or [-1])))
        return [{"id": w.id, "kind": w.kind, "status": w.status, "scheduled_window": w.scheduled_window,
                 "technician": w.assignment.technician.name if w.assignment else None, "created_at": _iso(w.created_at)} for w in q.all()]

    def _billing_account(self) -> BillingAccount | None:
        return self.db.query(BillingAccount).filter(BillingAccount.tenant_id == self.tenant_id, BillingAccount.party_id == self.party.id).first()

    def _load_account(self) -> list[dict[str, Any]]:
        acct = self._billing_account()
        return [{"balance": acct.balance, "status": acct.status, "rate_plan": acct.rate_plan}] if acct else []

    def _load_adjustments(self) -> list[dict[str, Any]]:
        acct = self._billing_account()
        return [{"id": a.id, "amount": a.amount, "reason": a.reason, "created_at": _iso(a.created_at)} for a in acct.adjustments] if acct else []

    # ---- Network Inventory (crm_core/inventory_service.customer_network): live, not the copy on the order

    def has_inventory(self) -> bool:
        if "_inv_in_use" not in self._cache:
            from app.crm_core import inventory_service

            self._cache["_inv_in_use"] = [{"yes": inventory_service.in_use(self.db, self.tenant_id)}]
        return self._cache["_inv_in_use"][0]["yes"]

    def network(self) -> dict[str, Any]:
        """The customer's network from Network Inventory - for the orders this caller may see."""
        if "_network" not in self._cache:
            from app.crm_core import inventory_service

            self._cache["_network"] = [inventory_service.customer_network(self.db, self.tenant_id, self.party.id, [o["id"] for o in self.rows("orders")])]
        return self._cache["_network"][0]

    def _load_network_services(self) -> list[dict[str, Any]]:
        return [{k: v for k, v in s.items() if k != "uses"} for s in self.network()["services"]]

    def _load_network_health(self) -> list[dict[str, Any]]:
        return self.network()["elements"]

    def _load_devices(self) -> list[dict[str, Any]]:
        return self.network()["devices"]

    def _load_resources(self) -> list[dict[str, Any]]:
        if self.has_inventory():
            return [{k: v for k, v in r.items() if k != "attributes"} for r in self.network()["resources"]]
        order_ids = [o["id"] for o in self.rows("orders")]
        if not order_ids:
            return []
        q = self.db.query(Resource, ServiceOrder.site_label).join(ServiceOrder, Resource.service_order_id == ServiceOrder.id)
        return [{"id": r.id, "resource_type": r.resource_type, "identifier": r.identifier, "status": _v(r.status), "site": site}
                for r, site in q.filter(ServiceOrder.order_id.in_(order_ids)).all()]

    def _load_assets(self) -> list[dict[str, Any]]:
        from app.crm_core import abo_service
        return [{"id": a.id, "asset_type": a.asset_type, "identifier": a.identifier, "status": _v(a.status),
                 "inventory_ref": a.inventory_system_ref, "created_at": _iso(a.created_at)} for a in abo_service.customer_assets(self.db, self.tenant_id, self.party.id)]

    def identity_verified(self) -> bool:
        return self.db.query(IdentityVerification).filter(
            IdentityVerification.tenant_id == self.tenant_id, IdentityVerification.party_id == self.party.id,
        ).count() > 0


# ---------------------------------------------------------------- sources


class Sources:
    """Resolves (dataset, source) to rows, each fetched at most once per view."""

    def __init__(self, internal: InternalData, specs: list[ConnectorSpec]):
        self.internal = internal
        self.specs = {s.key: s for s in specs if s.is_active}
        self.party = {"id": internal.party.id, "name": internal.party.name, "email": internal.party.email}
        self._cache: dict[tuple[str, str], tuple[list[dict[str, Any]], str | None]] = {}
        self.used: dict[tuple[str, str], dict[str, Any]] = {}

    def resolve(self, dataset: str, source: str | None) -> str | None:
        """Explicit source wins; then BRT 360 for data it holds; then any connector serving it; then BRT 360's
        Network Inventory for the datasets it can answer (a tenant recording its network there)."""
        ds = DATASETS[dataset]
        if source == BRT and (ds.internal or (ds.inventory and self.internal.has_inventory())):
            return BRT
        if source and source in self.specs and self.specs[source].serves(dataset):
            return source
        if source and source != BRT:
            return f"!{source}"
        if ds.internal:
            return BRT
        connector = next((k for k, s in self.specs.items() if s.serves(dataset)), None)
        if connector is None and ds.inventory and self.internal.has_inventory():
            return BRT
        return connector

    def label(self, source: str | None, dataset: str | None = None) -> str:
        if source == BRT:
            if dataset and (DATASETS[dataset].inventory or dataset in ("network_services", "resources")) and self.internal.has_inventory():
                return f"{BRT_LABEL} Network Inventory"
            return BRT_LABEL
        if source and source in self.specs:
            return self.specs[source].name
        return "Not connected"

    def load(self, dataset: str, source: str) -> tuple[list[dict[str, Any]], str | None]:
        key = (dataset, source)
        if key not in self._cache:
            if source == BRT:
                result: tuple[list[dict[str, Any]], str | None] = (self.internal.rows(dataset), None)
            else:
                try:
                    result = (connectors.fetch(self.specs[source], dataset, self.party), None)
                except ConnectorError as exc:
                    result = ([], str(exc))
                except Exception as exc:  # noqa: BLE001 - an unreachable system must not break the page
                    result = ([], f"Could not reach {self.specs[source].name}: {type(exc).__name__}")
            self._cache[key] = result
            self.used[key] = {"dataset": dataset, "source": source, "label": self.label(source, dataset),
                              "rows": len(result[0]), "error": result[1]}
        return self._cache[key]

    def rows_for(self, dataset: str) -> list[dict[str, Any]]:
        """Whatever is available for a dataset (for insights)."""
        src = self.resolve(dataset, None)
        if not src or src.startswith("!"):
            return []
        return self.load(dataset, src)[0]


# ---------------------------------------------------------------- aggregation


def _num(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _month(value: Any) -> str | None:
    if isinstance(value, (dt.datetime, dt.date)):
        return f"{value.year:04d}-{value.month:02d}"
    if isinstance(value, str) and len(value) >= 7 and value[4] == "-":
        return value[:7]
    if isinstance(value, (int, float)) and value > 10_000_000:  # epoch seconds / ms
        ts = value / 1000 if value > 10_000_000_000 else value
        d = dt.datetime.fromtimestamp(ts, dt.timezone.utc)
        return f"{d.year:04d}-{d.month:02d}"
    return None


def _apply_filter(rows: list[dict[str, Any]], flt: dict[str, list[str]] | None) -> list[dict[str, Any]]:
    if not flt:
        return rows
    return [r for r in rows if all(str(r.get(k, "")).lower() in {v.lower() for v in vals} for k, vals in flt.items())]


def _agg(rows: list[dict[str, Any]], agg: str, field: str | None) -> float:
    if agg == "count" or not field:
        return float(len(rows))
    nums = [n for n in (_num(r.get(field)) for r in rows) if n is not None]
    if not nums:
        return 0.0
    if agg == "sum":
        return round(sum(nums), 2)
    if agg == "avg":
        return round(sum(nums) / len(nums), 2)
    if agg == "max":
        return max(nums)
    if agg == "min":
        return min(nums)
    return nums[-1]  # latest


def _format_of(widget: dict[str, Any]) -> str:
    metric = widget["metric"]
    if metric.get("format"):
        return metric["format"]
    ds = DATASETS.get(widget.get("dataset") or "")
    f = ds.field(metric.get("field", "")) if ds and metric.get("field") else None
    if f and f.type in ("money", "percent") and metric.get("agg") != "count":
        return f.type
    return "number"


def compute(widget: dict[str, Any], rows: list[dict[str, Any]]) -> dict[str, Any]:
    metric = widget["metric"]
    rows = _apply_filter(rows, metric.get("filter"))
    agg, fld = metric.get("agg", "count"), metric.get("field")
    wtype = widget["type"]
    if wtype in catalog.SCALAR_TYPES:
        value = _agg(rows, agg, fld)
        out: dict[str, Any] = {"kind": "value", "value": value, "count": len(rows)}
        if wtype == "gauge":
            out["max"] = metric.get("max") or (100 if _format_of(widget) == "percent" else max(value, 1) * 1.25)
        return out
    if wtype in ("line", "area") or (wtype == "bar" and metric.get("x")):
        x = metric.get("x")
        buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for r in rows:
            m = _month(r.get(x)) if x else None
            if m:
                buckets[m].append(r)
        points = [{"label": m, "value": _agg(buckets[m], agg, fld)} for m in sorted(buckets)][-12:]
        return {"kind": "series", "points": points, "total": round(sum(p["value"] for p in points), 2)}
    if wtype in catalog.CATEGORY_TYPES or metric.get("group_by"):
        groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for r in rows:
            groups[str(r.get(metric.get("group_by", ""), "") or "—")].append(r)
        cats = sorted(({"label": k, "value": _agg(v, agg, fld)} for k, v in groups.items()), key=lambda c: -c["value"])
        if len(cats) > 8:
            cats = cats[:7] + [{"label": "Other", "value": round(sum(c["value"] for c in cats[7:]), 2)}]
        return {"kind": "categories", "items": cats, "total": round(sum(c["value"] for c in cats), 2)}
    if wtype == "table":
        ds = DATASETS[widget["dataset"]]
        cols = metric.get("columns") or ds.field_keys()[:5]
        if ds.date_field:
            rows = sorted(rows, key=lambda r: str(r.get(ds.date_field) or ""), reverse=True)
        limit = metric.get("limit", 6)
        return {
            "kind": "rows",
            "columns": [{"key": c, "label": ds.field(c).label, "type": ds.field(c).type} for c in cols if ds.field(c)],
            "rows": [{c: _iso(r.get(c)) for c in cols} for r in rows[:limit]],
            "total": len(rows),
        }
    return {"kind": "none"}


# ---------------------------------------------------------------- insights


def _money(v: float) -> str:
    return f"₹{v:,.0f}"


def insights(src: Sources) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    tickets = src.internal.rows("tickets")
    open_t = [t for t in tickets if str(t.get("status")).lower() not in ("resolved", "closed")]
    hot = [t for t in open_t if str(t.get("priority")).lower() in ("high", "critical") or str(t.get("severity")).lower() in ("high", "critical")]
    if hot:
        t = hot[0]
        out.append({"tone": "risk", "title": f"{len(hot)} urgent ticket{'s' if len(hot) > 1 else ''} open",
                    "detail": f"#{t['id']} {t.get('category')} is {t.get('priority')} priority - resolve it before any upsell.",
                    "action": {"label": "Diagnose", "text": f"Diagnose the root cause. ticket_id: {t['id']}"}})
    invoices = src.rows_for("invoices")
    overdue = [i for i in invoices if str(i.get("status")).lower() in ("overdue", "unpaid", "past_due")]
    if overdue:
        total = sum(_num(i.get("amount")) or 0 for i in overdue)
        out.append({"tone": "risk", "title": f"{_money(total)} overdue",
                    "detail": f"{len(overdue)} invoice{'s' if len(overdue) > 1 else ''} past due in billing - consider a collection call or a payment plan.",
                    "action": None})
    elif invoices:
        amounts = [_num(i.get("amount")) or 0 for i in sorted(invoices, key=lambda r: str(r.get("period")))]
        if len(amounts) >= 6 and sum(amounts[:3]) > 0:
            growth = (sum(amounts[-3:]) - sum(amounts[:3])) / sum(amounts[:3]) * 100
            if growth > 15:
                out.append({"tone": "opportunity", "title": f"Spend up {growth:.0f}%",
                            "detail": "Billed revenue is growing fast - a good moment to offer a bigger bundle or a longer contract.",
                            "action": None})
    # Network Inventory: a service of this customer runs on something down, degraded or in maintenance
    inventory_risk = src.internal.network()["at_risk"] if src.internal.has_inventory() else []
    if inventory_risk:
        r = inventory_risk[0]
        element = r["reasons"][0].split(" is ")[0]
        out.append({"tone": "risk", "title": f"Service {r['service']} at risk" + (f" (+{len(inventory_risk) - 1} more)" if len(inventory_risk) > 1 else ""),
                    "detail": f"{'; '.join(r['reasons'][:2])} - tell the customer before they call, or move the service.",
                    "action": {"label": "See impact", "text": f"What is affected if {element} goes down?"}})
    elements = src.rows_for("network_health")
    hot_links = sorted((e for e in elements if (_num(e.get("utilization_pct")) or 0) >= 85), key=lambda e: -(_num(e.get("utilization_pct")) or 0))
    orders = src.internal.rows("orders")
    live = [o for o in orders if o.get("status") in ("active", "completed", "changed", "pending_cancellation")]
    if hot_links:
        e = hot_links[0]
        action = {"label": "Upgrade", "text": f"Upgrade this service. order_id: {live[0]['id']}, new_price: "} if live else None
        out.append({"tone": "opportunity", "title": f"{e.get('element')} at {e.get('utilization_pct')}%",
                    "detail": f"{len(hot_links)} link{'s' if len(hot_links) > 1 else ''} near capacity - propose a bandwidth upgrade.",
                    "action": action})
    down = [e for e in elements if str(e.get("status")).lower() in ("down", "degraded")]
    if down and not hot and not inventory_risk:
        e = down[0]
        out.append({"tone": "risk", "title": f"{e.get('element')} is {e.get('status')}",
                    "detail": "The network shows a problem the customer may not have reported yet - open a proactive ticket.",
                    "action": {"label": "Open ticket", "text": f"{src.internal.party.name}'s {e.get('element')} is {e.get('status')}"}})
    quotes = src.internal.rows("quotes")
    draft = [q for q in quotes if q.get("status") == "draft"]
    if draft:
        q = draft[0]
        out.append({"tone": "action", "title": f"Quote #{q['id']} waiting",
                    "detail": f"{_money(_num(q.get('amount')) or 0)} still in draft - approve it to keep the deal moving.",
                    "action": {"label": "Approve", "text": f"Approve this quote. quote_id: {q['id']}"}})
    opps = src.internal.rows("opportunities")
    quoted = {o["id"] for o in opps if (o.get("value") or 0) > 0}
    unquoted = [o for o in opps if o["id"] not in quoted and o.get("stage") not in ("won", "lost")]
    if unquoted:
        o = unquoted[0]
        out.append({"tone": "action", "title": "Deal without a quote",
                    "detail": f"{o.get('name')} has no price yet.",
                    "action": {"label": "Quote it", "text": f"Create a quote. opportunity_id: {o['id']}, offering_name: {o.get('offering') or ''}"}})
    if not src.internal.identity_verified() and (orders or opps):
        out.append({"tone": "info", "title": "Identity not verified",
                    "detail": "Verify the customer's ID before activation to avoid order fallout.",
                    "action": {"label": "Verify", "text": f"Verify customer identity. party_id: {src.internal.party.id}, id_number: "}})
    if not out:
        out.append({"tone": "good", "title": "All clear", "detail": "No open risks or pending actions for this customer.", "action": None})
    return out[:6]


def health(src: Sources) -> dict[str, Any]:
    score = 100.0
    reasons = []
    tickets = [t for t in src.internal.rows("tickets") if str(t.get("status")).lower() not in ("resolved", "closed")]
    if tickets:
        score -= min(30, 8 * len(tickets))
        reasons.append(f"{len(tickets)} open ticket{'s' if len(tickets) > 1 else ''}")
    overdue = [i for i in src.rows_for("invoices") if str(i.get("status")).lower() in ("overdue", "unpaid", "past_due")]
    if overdue:
        score -= min(25, 12 * len(overdue))
        reasons.append("overdue invoices")
    at_risk = src.internal.network()["at_risk"] if src.internal.has_inventory() else []
    if at_risk:  # Network Inventory knows exactly which services are on equipment in trouble
        score -= min(25, 15 * len(at_risk))
        reasons.append(f"{len(at_risk)} service{'s' if len(at_risk) > 1 else ''} on equipment in trouble")
    elif not (src.internal.has_inventory() and src.resolve("network_health", None) == BRT):
        avail = [_num(e.get("availability_pct")) for e in src.rows_for("network_health")]
        avail = [a for a in avail if a is not None]
        if avail and sum(avail) / len(avail) < 99.5:
            score -= 15
            reasons.append("network below 99.5%")
    if src.internal.rows("orders") and any(o.get("status") in ("active", "completed", "changed", "pending_cancellation") for o in src.internal.rows("orders")):
        score += 0
    else:
        score -= 5
    score = max(5, min(100, round(score)))
    label = "Healthy" if score >= 80 else ("Watch" if score >= 55 else "At risk")
    if at_risk and label == "Healthy":
        label = "Watch"  # a service on equipment in trouble is never "healthy", whatever else is fine
    return {"score": score, "label": label, "reasons": reasons}


# ---------------------------------------------------------------- the whole view


def build_view(
    db: Session, tenant_id: int, access: Access | None, party: Party, layout: dict[str, Any], specs: list[ConnectorSpec]
) -> dict[str, Any]:
    from app.crm_core import process_service
    context = {
        "customer_type": process_service.account_status(party) or ("prospect" if party.party_type == "prospect" else "account"),
        "segment": process_service.party_segment(party),
    }
    rules = layout.get("component_audiences", {})
    visible = {key: catalog.audience_matches(rules.get(key), context) for key in catalog.COMPONENTS}
    src = Sources(InternalData(db, tenant_id, access, party), specs)

    def render(widget: dict[str, Any]) -> dict[str, Any]:
        out = {**widget, "format": None, "data": None, "status": "ok", "source_label": None, "message": None}
        if widget["type"] in ("records", "insights"):
            out["source_label"] = BRT_LABEL if widget["type"] == "records" else "AI"
            return out
        dataset = widget["dataset"]
        resolved = src.resolve(dataset, widget.get("source"))
        out["format"] = _format_of(widget)
        if resolved is None:
            ds = DATASETS[dataset]
            out.update(status="not_connected", source_label="Not connected",
                       message=f"Connect a {connectors.domain_label(ds.domain).lower()} system to see {ds.label.lower()}.")
            return out
        if resolved.startswith("!"):
            out.update(status="error", source_label=resolved[1:], message=f"Data source '{resolved[1:]}' is missing or switched off.")
            return out
        rows, error = src.load(dataset, resolved)
        out["source_label"] = src.label(resolved, dataset)
        if error:
            out.update(status="error", message=error)
            return out
        out["data"] = compute(widget, rows)
        if not rows:
            out["status"] = "empty"
        return out

    # Filter before rendering: excluded widgets must never fetch external data.
    panes = []
    for pane in layout["panes"]:
        if not catalog.audience_matches(pane.get("audience"), context):
            continue
        widgets = [w for w in pane["widgets"] if catalog.audience_matches(w.get("audience"), context)]
        if widgets:
            panes.append({**pane, "widgets": [render(w) for w in widgets]})
    kpis = []
    for rid in layout["header"]["kpis"]:
        recipe = RECIPES.get(rid)
        if recipe and catalog.audience_matches(rules.get("kpi:" + rid), context):
            kpis.append({"recipe": rid, "domain": recipe.domain, **render(recipe.widget())})
    return {
        "party": {"id": party.id, "name": party.name, "party_type": party.party_type, "email": party.email, "phone": party.phone,
                  "created_at": _iso(party.created_at)},
        "layout": layout,
        "audience": context,
        "components": visible,
        "theme": {**catalog.THEMES[layout["theme"]["preset"]], **{k: v for k, v in layout["theme"].items() if v is not None}},
        "header": {"kpis": kpis, "health": health(src) if layout["header"]["show_health"] and visible["health"] else None},
        "insights": insights(src) if visible["insights"] or any(w["type"] == "insights" for p in panes for w in p["widgets"]) else [],
        "panes": panes,
        "sources": list(src.used.values()),
    }

"""Analytics & Reports (app/analytics): slice and dice every entity, drill
to the records, save reports and dashboards, the telecom scorecard, and the
predictions. Everything needs the Analytics page (Admin -> Roles) and shows
only the records the caller's position may see."""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.access import require_page
from app.analytics import engine, insights, nl, platform_health, predictive, semantic, telecom
from app.crm_core.access_service import Access
from app.db import get_db
from app.models.analytics import AnalyticsReport
from app.models.auth import UserRole
from app.models.tenant import Tenant

router = APIRouter(prefix="/api/analytics", tags=["analytics"])
_PAGE = require_page("analytics")


def _run(fn, *args):
    try:
        return fn(*args)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


# ---------------------------------------------------------------- the model and queries


@router.get("/catalog")
def analytics_catalog(db: Session = Depends(get_db), access: Access = Depends(_PAGE)) -> dict[str, Any]:
    return {"entities": semantic.catalog(db, access.tenant_id), "operators": list(engine.OPERATORS), "grains": list(semantic.GRAINS),
            "aggregations": list(semantic.AGGREGATIONS), "calculations": list(engine.CALCS), "charts": list(nl.CHARTS),
            "presets": [{k: p.get(k) for k in ("key", "label", "entity", "grain", "fmt")} for p in predictive.PRESETS],
            "platform_health": platform_health.allowed(db, access)}


class QueryIn(BaseModel):
    entity: str
    dimensions: list[dict[str, Any]] = []
    measures: list[dict[str, Any]] = []
    filters: list[dict[str, Any]] = []
    sort: list[dict[str, Any]] = []
    top: dict[str, Any] | None = None
    limit: int | None = None


@router.post("/query")
def run_query(payload: QueryIn, db: Session = Depends(get_db), access: Access = Depends(_PAGE)) -> dict[str, Any]:
    result = _run(engine.query, db, access.tenant_id, access, payload.model_dump())
    result["narrative"] = insights.narrate(result)
    return result


class RecordsIn(BaseModel):
    entity: str
    columns: list[str] = []
    filters: list[dict[str, Any]] = []
    drill: list[dict[str, Any]] = []
    sort: dict[str, Any] = {}
    offset: int = 0
    limit: int = 100


@router.post("/records")
def drill_records(payload: RecordsIn, db: Session = Depends(get_db), access: Access = Depends(_PAGE)) -> dict[str, Any]:
    return _run(engine.records, db, access.tenant_id, access, payload.model_dump())


class ExportIn(BaseModel):
    kind: str = "query"  # query (the report's table) / records (every record behind it)
    query: dict[str, Any] = {}
    name: str = "report"


@router.post("/export")
def export_csv(payload: ExportIn, db: Session = Depends(get_db), access: Access = Depends(_PAGE)) -> Response:
    if payload.kind == "records":
        out = _run(engine.records, db, access.tenant_id, access, {**payload.query, "export": True})
    else:
        out = _run(engine.query, db, access.tenant_id, access, payload.query)
    name = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in payload.name)[:60] or "report"
    return Response(engine.to_csv(out["columns"], out["rows"]), media_type="text/csv",
                    headers={"Content-Disposition": f'attachment; filename="{name}.csv"'})


class AskIn(BaseModel):
    text: str = Field(min_length=3, max_length=500)
    use_ai: bool = True


@router.post("/ask")
def ask(payload: AskIn, db: Session = Depends(get_db), access: Access = Depends(_PAGE)) -> dict[str, Any]:
    """A question in plain English -> a report, run."""
    read = nl.ask(db, access.tenant_id, payload.text, payload.use_ai)
    spec = read["spec"]
    result = _run(engine.query, db, access.tenant_id, access, spec)
    result["narrative"] = insights.narrate(result)
    return {**read, "result": result}


class ExplainIn(BaseModel):
    title: str = "Report"
    facts: list[str] = []
    table: list[dict[str, Any]] = []
    use_ai: bool = True


@router.post("/explain")
def explain(payload: ExplainIn, db: Session = Depends(get_db), access: Access = Depends(_PAGE)) -> dict[str, Any]:
    return insights.explain(db, access.tenant_id, payload.title, payload.facts, payload.table, payload.use_ai)


# ---------------------------------------------------------------- telecom and predictions


@router.get("/telecom")
def telecom_scorecard(months: int = 12, db: Session = Depends(get_db), access: Access = Depends(_PAGE)) -> dict[str, Any]:
    return telecom.scorecard(db, access.tenant_id, access, max(3, min(months, 36)))


@router.get("/platform-health")
def platform_health_snapshot(hours: int = 6, db: Session = Depends(get_db), access: Access = Depends(_PAGE)) -> dict[str, Any]:
    if not platform_health.allowed(db, access):
        raise HTTPException(403, "Platform health is for the platform operator's administrators.")
    out = platform_health.snapshot(hours)
    if out.get("ai_tokens"):
        names = {str(t.id): t.name for t in db.query(Tenant).all()}
        for row in out["ai_tokens"]:
            row["tenant"] = names.get(row["tenant"], row["tenant"])
    return out


class ForecastIn(BaseModel):
    preset: str | None = None
    entity: str | None = None
    date_field: str | None = None
    measure: dict[str, Any] | None = None
    grain: str | None = None
    horizon: int | None = None
    filters: list[dict[str, Any]] = []
    label: str | None = None


@router.post("/forecast")
def forecast(payload: ForecastIn, db: Session = Depends(get_db), access: Access = Depends(_PAGE)) -> dict[str, Any]:
    if not payload.preset and not payload.entity:
        raise HTTPException(422, "Pick a preset or an entity to forecast")
    return _run(predictive.forecast, db, access.tenant_id, access, payload.model_dump())


@router.get("/predict/churn")
def churn(db: Session = Depends(get_db), access: Access = Depends(_PAGE)) -> dict[str, Any]:
    return predictive.churn(db, access.tenant_id, access)


@router.get("/predict/win")
def win(db: Session = Depends(get_db), access: Access = Depends(_PAGE)) -> dict[str, Any]:
    return predictive.win(db, access.tenant_id, access)


@router.get("/predict/segments")
def segments(db: Session = Depends(get_db), access: Access = Depends(_PAGE)) -> dict[str, Any]:
    return predictive.segments(db, access.tenant_id, access)


@router.get("/predict/next-offer")
def next_offer(db: Session = Depends(get_db), access: Access = Depends(_PAGE)) -> dict[str, Any]:
    return predictive.next_offer(db, access.tenant_id, access)


@router.get("/predict/usage-alerts")
def usage_alerts(db: Session = Depends(get_db), access: Access = Depends(_PAGE)) -> dict[str, Any]:
    return predictive.usage_alerts(db, access.tenant_id, access)


# ---------------------------------------------------------------- saved reports and dashboards


class ReportIn(BaseModel):
    kind: str = "report"
    name: str = Field(min_length=1, max_length=150)
    description: str = ""
    spec: dict[str, Any] = {}
    shared: bool = True


def _out(r: AnalyticsReport, access: Access) -> dict[str, Any]:
    try:
        spec = json.loads(r.spec_json or "{}")
    except ValueError:
        spec = {}
    return {"id": r.id, "kind": r.kind, "name": r.name, "description": r.description, "spec": spec, "shared": r.shared,
            "owner_user_id": r.owner_user_id, "mine": r.owner_user_id == access.user.id, "can_edit": _can_edit(r, access),
            "updated_at": r.updated_at.isoformat() if r.updated_at else None}


def _can_edit(r: AnalyticsReport, access: Access) -> bool:
    return r.owner_user_id == access.user.id or access.user.role == UserRole.admin


def _get(db: Session, access: Access, report_id: int) -> AnalyticsReport:
    r = db.get(AnalyticsReport, report_id)
    if r is None or r.tenant_id != access.tenant_id or not (r.shared or r.owner_user_id == access.user.id):
        raise HTTPException(404, "Report not found")
    return r


def _check(payload: ReportIn) -> None:
    if payload.kind not in ("report", "dashboard"):
        raise HTTPException(422, "kind is report or dashboard")


@router.get("/reports")
def list_reports(kind: str | None = None, db: Session = Depends(get_db), access: Access = Depends(_PAGE)) -> list[dict[str, Any]]:
    q = db.query(AnalyticsReport).filter(AnalyticsReport.tenant_id == access.tenant_id)
    if kind:
        q = q.filter(AnalyticsReport.kind == kind)
    rows = [r for r in q.order_by(AnalyticsReport.updated_at.desc()).all() if r.shared or r.owner_user_id == access.user.id]
    return [_out(r, access) for r in rows]


@router.post("/reports", status_code=201)
def create_report(payload: ReportIn, db: Session = Depends(get_db), access: Access = Depends(_PAGE)) -> dict[str, Any]:
    _check(payload)
    r = AnalyticsReport(tenant_id=access.tenant_id, owner_user_id=access.user.id, kind=payload.kind, name=payload.name.strip(),
                        description=payload.description, spec_json=json.dumps(payload.spec), shared=payload.shared)
    db.add(r)
    db.commit()
    return _out(r, access)


@router.put("/reports/{report_id}")
def update_report(report_id: int, payload: ReportIn, db: Session = Depends(get_db), access: Access = Depends(_PAGE)) -> dict[str, Any]:
    _check(payload)
    r = _get(db, access, report_id)
    if not _can_edit(r, access):
        raise HTTPException(403, "Only its owner or an administrator can change this report - save a copy instead")
    r.name, r.description, r.spec_json, r.shared = payload.name.strip(), payload.description, json.dumps(payload.spec), payload.shared
    db.commit()
    return _out(r, access)


@router.delete("/reports/{report_id}", status_code=204)
def delete_report(report_id: int, db: Session = Depends(get_db), access: Access = Depends(_PAGE)) -> Response:
    r = _get(db, access, report_id)
    if not _can_edit(r, access):
        raise HTTPException(403, "Only its owner or an administrator can delete this report")
    db.delete(r)
    db.commit()
    return Response(status_code=204)


STARTER = [
    ("MRR by segment", {"entity": "order", "dimensions": [{"field": "customer_profile.segment"}], "measures": [{"field": "mrr", "agg": "sum"}],
                        "chart": "donut"}),
    ("Orders booked per month", {"entity": "order", "dimensions": [{"field": "created_at", "grain": "month"}], "measures": [{"agg": "count"},
                                 {"field": "monthly_price", "agg": "sum"}], "filters": [{"field": "created_at", "op": "last_n_days", "value": 365}],
                                 "chart": "combo"}),
    ("Pipeline by stage", {"entity": "opportunity", "dimensions": [{"field": "stage"}], "measures": [{"agg": "count"}], "chart": "funnel"}),
    ("Top 10 products by MRR", {"entity": "order_item", "dimensions": [{"field": "offering.name"}], "measures": [{"field": "line_value", "agg": "sum"}],
                                "filters": [{"field": "order.status", "op": "in", "value": list(semantic.LIVE_ORDER)}], "top": {"n": 10, "other": False},
                                "chart": "hbar"}),
    ("Tickets by category and severity", {"entity": "ticket", "dimensions": [{"field": "category"}, {"field": "severity"}],
                                          "measures": [{"agg": "count"}], "chart": "stacked"}),
    ("Average resolution time by severity", {"entity": "ticket", "dimensions": [{"field": "severity"}],
                                             "measures": [{"field": "resolution_hours", "agg": "avg"}], "chart": "bar"}),
    ("Receivables ageing", {"entity": "invoice", "dimensions": [{"field": "aging_bucket"}], "measures": [{"field": "outstanding", "agg": "sum"}],
                            "chart": "bar"}),
    ("Usage by day of week and hour", {"entity": "usage", "dimensions": [{"field": "at", "grain": "weekday"}, {"field": "at", "grain": "hour"}],
                                       "measures": [{"field": "amount", "agg": "sum"}], "filters": [{"field": "at", "op": "last_n_days", "value": 90}],
                                       "chart": "heatmap"}),
]


@router.post("/dashboards/starter", status_code=201)
def starter_dashboard(db: Session = Depends(get_db), access: Access = Depends(_PAGE)) -> dict[str, Any]:
    """The telecom executive dashboard, ready to edit: its reports, then the dashboard that lays them out."""
    ids = []
    for name, spec in STARTER:
        r = AnalyticsReport(tenant_id=access.tenant_id, owner_user_id=access.user.id, kind="report", name=name,
                            description="Starter report", spec_json=json.dumps({**spec, "title": name}), shared=True)
        db.add(r)
        db.flush()
        ids.append(r.id)
    tiles = [{"report_id": rid, "w": 2 if i in (1, 7) else 1} for i, rid in enumerate(ids)]
    dash = AnalyticsReport(tenant_id=access.tenant_id, owner_user_id=access.user.id, kind="dashboard", name="Telecom executive dashboard",
                           description="Revenue, sales, assurance, billing and usage at a glance", spec_json=json.dumps({"tiles": tiles, "columns": 3}),
                           shared=True)
    db.add(dash)
    db.commit()
    return _out(dash, access)

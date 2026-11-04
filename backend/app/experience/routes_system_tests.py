"""System test suite API (orchestration/system_tests.py) - administrators.

  POST /api/system-tests/suites                     generate a suite from the tenant's system ({name})
  GET  /api/system-tests/suites                     suites, newest first
  GET  /api/system-tests/suites/{id}                a suite with its cases (?area=&kind=&q=)
  GET  /api/system-tests/suites/{id}/cases.csv      the cases as a manual test script
  PATCH /api/system-tests/cases/{id}                switch a case on / off ({is_active})
  POST /api/system-tests/suites/{id}/runs           run it in the background ({areas, kinds, case_ids}; ?wait=true runs it now)
  GET  /api/system-tests/runs                       runs, newest first (?suite_id=)
  GET  /api/system-tests/runs/{id}                  progress and results with their evidence (?status=)
  GET  /api/system-tests/runs/{id}/report.html      the evidence report - open, share or print to PDF
  POST /api/system-tests/runs/{id}/cancel
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException
from fastapi.responses import HTMLResponse, PlainTextResponse, Response
from sqlalchemy.orm import Session, sessionmaker

from app.access import get_access
from app.auth import get_current_user, require_roles
from app.crm_core.access_service import Access
from app.db import get_db
from app.models.auth import User, UserRole
from app.models.system_tests import SystemTestCase, SystemTestRun, SystemTestSuite
from app.orchestration import system_tests as st
from app.tenancy import get_current_tenant_id

router = APIRouter(prefix="/api/system-tests", tags=["system-tests"], dependencies=[Depends(require_roles(UserRole.admin))])
Payload = dict[str, Any]


def _suite(db: Session, tenant_id: int, suite_id: int) -> SystemTestSuite:
    suite = db.get(SystemTestSuite, suite_id)
    if suite is None or suite.tenant_id != tenant_id:
        raise HTTPException(404, "Suite not found")
    return suite


def _run(db: Session, tenant_id: int, run_id: int) -> SystemTestRun:
    run = db.get(SystemTestRun, run_id)
    if run is None or run.tenant_id != tenant_id:
        raise HTTPException(404, "Run not found")
    return run


@router.post("/suites", status_code=201)
def generate(data: Payload = Body(default={}), db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id),
             user: User = Depends(get_current_user)) -> Payload:
    suite = st.generate(db, tenant_id, user, (data or {}).get("name"))
    db.commit()
    return st.suite_out(db, suite)


@router.get("/suites")
def suites(db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)) -> list[Payload]:
    rows = db.query(SystemTestSuite).filter(SystemTestSuite.tenant_id == tenant_id).order_by(SystemTestSuite.id.desc()).limit(30)
    return [st.suite_out(db, s) for s in rows]


@router.get("/suites/{suite_id}")
def suite(suite_id: int, area: str = "", kind: str = "", q: str = "", db: Session = Depends(get_db),
          tenant_id: int = Depends(get_current_tenant_id)) -> Payload:
    found = _suite(db, tenant_id, suite_id)
    query = db.query(SystemTestCase).filter(SystemTestCase.suite_id == found.id)
    if area:
        query = query.filter(SystemTestCase.area == area)
    if kind:
        query = query.filter(SystemTestCase.kind == kind)
    if q.strip():
        query = query.filter(SystemTestCase.title.ilike(f"%{q.strip()}%"))
    return {**st.suite_out(db, found), "cases": [st.case_out(c) for c in query.order_by(SystemTestCase.id).limit(2000)]}


@router.get("/suites/{suite_id}/cases.csv", response_class=PlainTextResponse)
def cases_csv(suite_id: int, db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)) -> PlainTextResponse:
    found = _suite(db, tenant_id, suite_id)
    return PlainTextResponse(st.cases_csv(db, found), media_type="text/csv",
                             headers={"Content-Disposition": f'attachment; filename="test-cases-suite-{found.id}.csv"'})


@router.patch("/cases/{case_id}")
def toggle_case(case_id: int, data: Payload = Body(...), db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)) -> Payload:
    case = db.get(SystemTestCase, case_id)
    if case is None or case.tenant_id != tenant_id:
        raise HTTPException(404, "Case not found")
    case.is_active = bool(data.get("is_active"))
    db.commit()
    return st.case_out(case)


@router.post("/suites/{suite_id}/runs", status_code=201)
def start(suite_id: int, data: Payload = Body(default={}), wait: bool = False, db: Session = Depends(get_db),
          tenant_id: int = Depends(get_current_tenant_id), user: User = Depends(get_current_user),
          access: Access = Depends(get_access)) -> Payload:
    found = _suite(db, tenant_id, suite_id)
    busy = db.query(SystemTestRun.id).filter(SystemTestRun.tenant_id == tenant_id, SystemTestRun.status.in_(("queued", "running"))).first()
    if busy is not None:
        raise HTTPException(409, "A test run is already going - wait for it, or cancel it")
    selection = {k: v for k, v in (data or {}).items() if k in ("areas", "kinds", "case_ids", "use_ai") and v}
    try:
        run = st.start_run(db, tenant_id, user, access, found, selection)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    db.commit()
    st.launch(run.id, sessionmaker(bind=db.get_bind(), autoflush=False), wait=wait)
    db.refresh(run)
    return st.run_out(run)


@router.get("/runs")
def runs(suite_id: int | None = None, db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)) -> list[Payload]:
    query = db.query(SystemTestRun).filter(SystemTestRun.tenant_id == tenant_id)
    if suite_id:
        query = query.filter(SystemTestRun.suite_id == suite_id)
    return [st.run_out(r) for r in query.order_by(SystemTestRun.id.desc()).limit(30)]


@router.get("/runs/{run_id}")
def run(run_id: int, status: str = "", db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)) -> Payload:
    found = _run(db, tenant_id, run_id)
    return {**st.run_out(found), "results": st.results(db, found, status)}


@router.get("/runs/{run_id}/report.html", response_class=HTMLResponse)
def report(run_id: int, download: bool = False, db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)) -> HTMLResponse:
    found = _run(db, tenant_id, run_id)
    headers = {"Content-Disposition": f'attachment; filename="{found.number}-evidence.html"'} if download else {}
    return HTMLResponse(st.report_html(db, found), headers=headers)


@router.get("/runs/{run_id}/report.pdf")
def report_pdf(run_id: int, all_steps: bool = False, db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)) -> Response:
    """The evidence report as a PDF (all_steps: the steps of passed cases too)."""
    found = _run(db, tenant_id, run_id)
    return Response(st.report_pdf(db, found, all_steps), media_type="application/pdf",
                    headers={"Content-Disposition": f'attachment; filename="{found.number}-evidence.pdf"'})


@router.get("/runs/{run_id}/results.xlsx")
def results_xlsx(run_id: int, db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)) -> Response:
    found = _run(db, tenant_id, run_id)
    return Response(st.results_xlsx(db, found), media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": f'attachment; filename="{found.number}-results.xlsx"'})


@router.get("/suites/{suite_id}/cases.xlsx")
def cases_xlsx(suite_id: int, db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)) -> Response:
    """The suite as a formatted manual test script."""
    from app.documents import xlsx

    found = _suite(db, tenant_id, suite_id)
    rows = [["Case", "Area", "Kind", "Priority", "Title", "Steps", "Expected", "Target", "Active", "Actual result", "Pass / Fail", "Tested by"]]
    for c in db.query(SystemTestCase).filter(SystemTestCase.suite_id == found.id).order_by(SystemTestCase.id):
        rows.append([c.code, c.area, c.kind, c.priority, c.title, "\n".join(f"{i}. {s.get('say') or s.get('do')}" for i, s in enumerate(c.steps or [], 1)),
                     c.expected, c.target, "yes" if c.is_active else "no", "", "", ""])
    return Response(xlsx([("Test cases", rows)]), media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": f'attachment; filename="test-cases-suite-{found.id}.xlsx"'})


@router.post("/runs/{run_id}/cancel")
def cancel(run_id: int, db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)) -> Payload:
    found = _run(db, tenant_id, run_id)
    if found.status in ("queued", "running"):
        found.status = "cancelled"
        db.commit()
    return st.run_out(found)

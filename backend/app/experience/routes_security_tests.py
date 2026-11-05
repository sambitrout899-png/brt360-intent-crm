"""Security testing API (orchestration/security_tests.py) - administrators.

  GET  /api/security-tests/catalog                      every check, the manual procedures, the standards
  POST /api/security-tests/runs                         start an assessment ({name, categories}; ?wait=true runs it now)
  GET  /api/security-tests/runs                         assessments, newest first
  GET  /api/security-tests/runs/{id}                    progress, score and every result with its evidence
  POST /api/security-tests/runs/{id}/cancel
  PUT  /api/security-tests/runs/{id}/procedures/{code}  record a manual VAPT procedure ({status, note, severity})
  GET  /api/security-tests/runs/{id}/report.pdf         the Security Assessment Report (the organization's report design)
  GET  /api/security-tests/runs/{id}/results.xlsx       results and evidence as a workbook
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException
from fastapi.responses import Response
from sqlalchemy.orm import Session, sessionmaker

from app.auth import get_current_user, require_roles
from app.db import get_db
from app.models.auth import User, UserRole
from app.models.security_tests import SecurityTestRun
from app.orchestration import security_tests as sec
from app.tenancy import get_current_tenant_id

router = APIRouter(prefix="/api/security-tests", tags=["security-tests"], dependencies=[Depends(require_roles(UserRole.admin))])
Payload = dict[str, Any]


def _run(db: Session, tenant_id: int, run_id: int) -> SecurityTestRun:
    run = db.get(SecurityTestRun, run_id)
    if run is None or run.tenant_id != tenant_id:
        raise HTTPException(404, "Assessment not found")
    return run


@router.get("/catalog")
def catalog() -> Payload:
    return sec.catalog()


@router.post("/runs", status_code=201)
def start(data: Payload = Body(default={}), wait: bool = False, db: Session = Depends(get_db),
          tenant_id: int = Depends(get_current_tenant_id), user: User = Depends(get_current_user)) -> Payload:
    data = data or {}
    categories = data.get("categories") or None
    if categories is not None and (not isinstance(categories, list) or not all(isinstance(c, str) for c in categories)):
        raise HTTPException(422, "categories must be a list of category keys")
    try:
        run = sec.start(db, tenant_id, user, str(data.get("name") or "")[:200], categories)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    db.commit()
    sec.launch(run.id, sessionmaker(bind=db.get_bind(), autoflush=False), wait=wait)
    db.refresh(run)
    return sec.run_out(run)


@router.get("/runs")
def runs(db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)) -> list[Payload]:
    rows = db.query(SecurityTestRun).filter(SecurityTestRun.tenant_id == tenant_id).order_by(SecurityTestRun.id.desc()).limit(50).all()
    return [sec.run_out(r) for r in rows]


@router.get("/runs/{run_id}")
def run(run_id: int, db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)) -> Payload:
    found = _run(db, tenant_id, run_id)
    return {**sec.run_out(found), "results": sec.results(db, found)}


@router.post("/runs/{run_id}/cancel")
def cancel(run_id: int, db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)) -> Payload:
    found = _run(db, tenant_id, run_id)
    if found.status in ("queued", "running"):
        found.status = "cancelled"
        db.commit()
    return sec.run_out(found)


@router.put("/runs/{run_id}/procedures/{code}")
def record(run_id: int, code: str, data: Payload = Body(...), db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id),
           user: User = Depends(get_current_user)) -> Payload:
    found = _run(db, tenant_id, run_id)
    try:
        sec.record_manual(db, found, code, str(data.get("status") or ""), str(data.get("note") or ""), data.get("severity"), user)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    db.commit()
    return {**sec.run_out(found), "results": sec.results(db, found)}


@router.get("/runs/{run_id}/report.pdf")
def report(run_id: int, db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)) -> Response:
    found = _run(db, tenant_id, run_id)
    return Response(sec.report_pdf(db, found), media_type="application/pdf",
                    headers={"Content-Disposition": f'attachment; filename="security-assessment-{found.number}.pdf"'})


@router.get("/runs/{run_id}/results.xlsx")
def workbook(run_id: int, db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)) -> Response:
    from app.documents import MEDIA

    found = _run(db, tenant_id, run_id)
    return Response(sec.results_xlsx(db, found), media_type=MEDIA["xlsx"],
                    headers={"Content-Disposition": f'attachment; filename="security-assessment-{found.number}.xlsx"'})

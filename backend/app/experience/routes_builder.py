"""The Process Builder API (orchestration/process_builder.py): a process
described in plain English becomes approval rules, Flow Studio flows, Page
Designer changes and assignment rules - asked about where it is unclear,
rehearsed, and built only on the administrator's consent."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.auth import get_current_user, require_roles
from app.crm_core import llm
from app.db import get_db
from app.experience.routes_change import current_workspace
from app.models.change_control import Workspace
from app.models.auth import User, UserRole
from app.orchestration import process_builder
from app.tenancy import get_current_tenant_id

# It changes the tenant's own configuration - administrators only, like the screens it drives.
# The most AI time one request may use - well inside nginx's proxy_read_timeout (120 s in
# deploy/brt360.sh); a call that would go past it is skipped and its rules answer instead.
AI_SECONDS = 45.0

router = APIRouter(prefix="/api/process-builder", tags=["process-builder"], dependencies=[Depends(require_roles(UserRole.admin))])


class StepIn(BaseModel):
    kind: str = ""
    text: str = Field(min_length=1, max_length=2000)


class DesignIn(BaseModel):
    request: str = Field(min_length=5, max_length=6000)
    name: str | None = Field(default=None, max_length=150)
    steps: list[StepIn] | None = Field(default=None, max_length=process_builder.MAX_COMPONENTS)
    answers: dict[str, Any] = {}
    graphs: dict[str, Any] = {}


class Placement(BaseModel):
    mode: str = "new"  # new: a process of its own on the map | append: added to an existing process
    label: str | None = Field(default=None, max_length=120)
    process_key: str | None = Field(default=None, max_length=80)


class BuildIn(DesignIn):
    mode: str = "rehearse"  # rehearse | simulate | execute
    placement: Placement | None = None


class StageIn(DesignIn):
    placement: Placement | None = None
    version_id: int | None = None  # a draft to add it to; none = a new version from the map in use


class BrdIn(BaseModel):
    filename: str = Field(min_length=1, max_length=200)
    content_base64: str = Field(min_length=4, max_length=3_000_000)


def _steps(payload: DesignIn) -> list[dict[str, str]] | None:
    return [s.model_dump() for s in payload.steps] if payload.steps else None


@router.get("/recipes")
def process_recipes(db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)) -> list[dict]:
    from app.orchestration import process_memory
    return [{"id": r["id"], "name": r["name"], "request": r["request"]}
            for r in process_memory.recipes(db, tenant_id)]


@router.post("/design")
def design_process(payload: DesignIn, db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id),
                   user: User = Depends(get_current_user)) -> dict:
    """The blueprint - components per engine, open questions, engineering
    changes; or the agent's plan when the request is a chain of actions. Nothing is saved."""
    with llm.budget(AI_SECONDS):
        return process_builder.design(db, tenant_id, payload.request.strip(), payload.answers, _steps(payload), payload.graphs, payload.name, user)


@router.post("/build")
def build_process(payload: BuildIn, db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id),
                  user: User = Depends(get_current_user), ws: Workspace | None = Depends(current_workspace)) -> dict:
    """rehearse: built and rolled back, to show exactly what would be created.
    simulate: the same, with every flow played through on a sample record.
    execute (the administrator's consent): through the Functional Map - a new
    version of the map in use, with the process in it, activated."""
    if payload.mode not in ("rehearse", "simulate", "execute"):
        raise HTTPException(422, "mode must be rehearse, simulate or execute")
    if payload.mode == "execute":  # it activates a map version - the root administrator's call; others save it as a draft version
        from app.orchestration import change_control

        if not change_control.is_root(db, user):
            raise HTTPException(403, "Only the organization's root administrator activates a process - save it as a draft map version instead")
        if ws is not None:
            raise HTTPException(409, f"You are working in the workspace “{ws.name}” - save the process as a draft map version there; "
                                     "it goes live with the workspace's release")
    with llm.budget(AI_SECONDS):
        steps, answers, graphs = _steps(payload), payload.answers, payload.graphs
        if not steps:
            blueprint = process_builder.design(db, tenant_id, payload.request.strip(), answers, None, payload.graphs, payload.name)
            steps, answers = blueprint["steps"], blueprint.get("answers", answers)
            graphs = {**{c["id"]: c["spec"]["graph"] for c in blueprint["components"] if c["kind"] == "flow"}, **graphs}
        return process_builder.build(db, tenant_id, payload.request.strip(), steps, answers, graphs, payload.mode, payload.name,
                                     user_id=user.id, placement=payload.placement.model_dump() if payload.placement else None)


@router.post("/stage")
def stage_process(payload: StageIn, db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id),
                  user: User = Depends(get_current_user), ws: Workspace | None = Depends(current_workspace)) -> dict:
    """Puts the designed process into a draft version of the Functional Map -
    nothing is built until that version is activated."""
    from app.orchestration import function_map

    with llm.budget(AI_SECONDS):
        spec = {"request": payload.request.strip(), "steps": _steps(payload), "answers": payload.answers, "graphs": payload.graphs, "name": payload.name}
        try:
            return function_map.stage(db, tenant_id, user.id, spec, payload.placement.model_dump() if payload.placement else None,
                                      payload.version_id, ws)
        except LookupError as exc:
            raise HTTPException(404, str(exc)) from exc
        except function_map.VersionError as exc:
            raise HTTPException(409, str(exc)) from exc


class ArchitectIn(BaseModel):
    text: str = Field(default="", max_length=60000)
    name: str | None = Field(default=None, max_length=150)
    placement: Placement | None = None
    # answers to the architect's own questions (a new design round) / to the builder's (checked again, no model call)
    decisions: dict[str, str] = {}
    answers: dict[str, Any] = {}
    analysis: dict[str, Any] | None = None


class ArchitectImplementIn(BaseModel):
    analysis: dict[str, Any]
    version_id: int | None = None


@router.post("/architect")
def architect(payload: ArchitectIn, db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)) -> dict:
    """The AI solution architect: a BRD read end to end into a verified design
    for this organization - requirements, fit-gap, steps, links, pieces,
    assumptions, blocking questions. Nothing is saved. With an earlier
    analysis and only the builder's questions answered, it is checked again
    without a new model round."""
    from app.orchestration import process_architect

    placement = payload.placement.model_dump() if payload.placement else None
    try:
        if payload.analysis and not payload.decisions:
            done = process_architect.verify(db, tenant_id, process_architect._clean(payload.analysis) | {
                "questions": [q for q in payload.analysis.get("questions") or [] if not q.get("builder")],
                **({"placement": placement} if placement else {})}, payload.answers)
            return {**done, "by": payload.analysis.get("by"), "model": payload.analysis.get("model"), "text": payload.analysis.get("text")}
        with llm.budget(110.0):  # inside nginx's 120 s
            return process_architect.architect(db, tenant_id, payload.text or (payload.analysis or {}).get("text") or "", payload.name, placement,
                                               payload.decisions, payload.answers)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.post("/architect/implement")
def architect_implement(payload: ArchitectImplementIn, db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id),
                        user: User = Depends(get_current_user), ws: Workspace | None = Depends(current_workspace)) -> dict:
    """The design into a draft version of the Functional Map (in the caller's workspace) - built when it goes live."""
    from app.orchestration import function_map, process_architect

    try:
        return process_architect.implement(db, tenant_id, user.id, payload.analysis, ws, payload.version_id)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except function_map.VersionError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.post("/architect/document")
def architect_document(payload: ArchitectImplementIn) -> Response:
    """The solution design as a Word document."""
    from app.orchestration import process_architect

    return Response(process_architect.document(payload.analysis), media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                    headers={"Content-Disposition": 'attachment; filename="solution-design.docx"'})


@router.get("/placements")
def placements(db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)) -> dict:
    """Where a new process can go: the map's business areas and its processes (to append to)."""
    from app.orchestration import function_map

    now = function_map.current(db, tenant_id)
    return {"processes": [{"key": p["key"], "label": p["label"], "steps": len(p["steps"]), "tenant": bool(p.get("tenant"))} for p in now["processes"]]}


@router.post("/brd")
def read_brd(payload: BrdIn, db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)) -> dict:
    """A BRD (Word or text) read into the builder's request - nothing is designed or saved yet."""
    import base64
    import binascii

    from app.orchestration import function_map, process_brd

    try:
        data = base64.b64decode(payload.content_base64, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise HTTPException(422, "The file didn't arrive whole - upload it again") from exc
    try:
        text = process_brd.read(payload.filename, data)
        return process_brd.to_request(text, function_map.current(db, tenant_id)["processes"])
    except process_brd.BrdError as exc:
        raise HTTPException(422, str(exc)) from exc


# ---------------------------------------------------------------- use cases (orchestration/use_cases.py)

_DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


class UseCasesIn(BaseModel):
    name: str = Field(min_length=1, max_length=150)
    use_cases: list[dict[str, Any]] = Field(max_length=40)


class UseCaseCommitIn(BaseModel):
    answers: dict[str, dict[str, Any]] = {}  # {group: {question id: answer}} - the builder's questions, per process
    activate: bool = False  # the root administrator: activate the map version at once


def _use_case_set(db: Session, tenant_id: int, set_id: int):
    from app.models.use_cases import UseCaseSet

    s = db.query(UseCaseSet).filter(UseCaseSet.tenant_id == tenant_id, UseCaseSet.id == set_id).first()
    if s is None:
        raise HTTPException(404, "No such use case set")
    return s


@router.get("/use-case-template")
def use_case_template() -> Response:
    """The BRT 360 use case template (Word): one worked example, a delta on B2B Sales to Order."""
    from app.orchestration import use_cases

    return Response(use_cases.template_docx(), media_type=_DOCX,
                    headers={"Content-Disposition": 'attachment; filename="BRT360-use-case-template.docx"'})


@router.get("/use-cases")
def list_use_case_sets(db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)) -> list[dict]:
    from app.models.use_cases import UseCaseSet
    from app.orchestration import use_cases

    rows = db.query(UseCaseSet).filter(UseCaseSet.tenant_id == tenant_id).order_by(UseCaseSet.id.desc()).limit(50).all()
    return [use_cases.out(s, full=False) for s in rows]


@router.post("/use-cases")
def upload_use_cases(payload: BrdIn, db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id),
                     user: User = Depends(get_current_user)) -> dict:
    """A use case document (Word, RTF or text) read into structured use cases,
    mapped on the Functional Map and saved for review - nothing is built."""
    import base64
    import binascii
    import json

    from app.models.use_cases import UseCaseSet
    from app.orchestration import process_brd, use_cases

    try:
        data = base64.b64decode(payload.content_base64, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise HTTPException(422, "The file didn't arrive whole - upload it again") from exc
    try:
        with llm.budget(100.0):  # a free-text document is restructured by the model - inside nginx's 120 s
            read = use_cases.read(db, tenant_id, payload.filename, data)
    except (process_brd.BrdError, use_cases.UseCaseError) as exc:
        raise HTTPException(422, str(exc)) from exc
    mapped = use_cases.map_cases(db, tenant_id, read["use_cases"])
    s = UseCaseSet(tenant_id=tenant_id, name=read["name"][:150], filename=payload.filename[:200], read_by=read["read_by"],
                   use_cases_json=json.dumps(mapped), created_by_id=user.id)
    db.add(s)
    db.commit()
    return {**use_cases.out(s), "examples_dropped": read["examples_dropped"]}


@router.get("/use-cases/{set_id}")
def get_use_case_set(set_id: int, db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)) -> dict:
    from app.orchestration import use_cases

    return use_cases.out(_use_case_set(db, tenant_id, set_id))


@router.put("/use-cases/{set_id}")
def save_use_case_set(set_id: int, payload: UseCasesIn, db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)) -> dict:
    """The reviewed use cases, mapped again. A committed set goes back to draft -
    commit it again for the change to reach the map; its test cases are marked out of date."""
    import json

    from app.orchestration import use_cases
    from app.timeutils import utcnow

    s = _use_case_set(db, tenant_id, set_id)
    cases = use_cases.normalize(payload.use_cases)
    if not cases:
        raise HTTPException(422, "A use case set needs at least one use case")
    s.name = payload.name.strip()[:150]
    s.use_cases_json = json.dumps(use_cases.map_cases(db, tenant_id, cases))
    s.status = "draft"
    tests = json.loads(s.tests_json or "{}")
    if tests:
        s.tests_json = json.dumps({**tests, "stale": True})
    s.updated_at = utcnow()
    db.commit()
    return use_cases.out(s)


@router.delete("/use-cases/{set_id}", status_code=204)
def delete_use_case_set(set_id: int, db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)) -> Response:
    """The set only - a map version it was committed into stays (retire or delete it on the map)."""
    db.delete(_use_case_set(db, tenant_id, set_id))
    db.commit()
    return Response(status_code=204)


@router.post("/use-cases/{set_id}/preview")
def preview_use_cases(set_id: int, payload: UseCaseCommitIn, db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)) -> dict:
    """What committing would build, per process: steps, hand-offs, configuration,
    gaps, the decisions taken and the questions still open - nothing is changed."""
    from app.orchestration import use_cases

    with llm.budget(AI_SECONDS):
        return {"designs": use_cases.preview(db, tenant_id, _use_case_set(db, tenant_id, set_id), payload.answers)}


@router.post("/use-cases/{set_id}/commit")
def commit_use_cases(set_id: int, payload: UseCaseCommitIn, db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id),
                     user: User = Depends(get_current_user), ws: Workspace | None = Depends(current_workspace)) -> dict:
    """The use cases into a draft Functional Map version - the existing actions
    placed and linked, the customizations and new actions staged as builds -
    activated at once only by the root administrator outside a workspace.
    Questions the builder can't decide come back unanswered: nothing is changed."""
    from app.orchestration import change_control, function_map, use_cases

    if payload.activate:
        if not change_control.is_root(db, user):
            raise HTTPException(403, "Only the organization's root administrator activates a map version - commit it as a draft instead")
        if ws is not None:
            raise HTTPException(409, f"You are working in the workspace “{ws.name}” - commit as a draft; it goes live with the workspace's release")
    s = _use_case_set(db, tenant_id, set_id)
    with llm.budget(AI_SECONDS):
        try:
            done = use_cases.commit(db, tenant_id, user.id, s, payload.answers, ws, payload.activate)
        except LookupError as exc:
            raise HTTPException(404, str(exc)) from exc
        except function_map.VersionError as exc:
            raise HTTPException(409, str(exc)) from exc
    return {**done, "set": use_cases.out(s)}


@router.post("/use-cases/{set_id}/tests")
def generate_use_case_tests(set_id: int, db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)) -> dict:
    """Manual and automation test cases - after the use cases are committed."""
    import json

    from app.orchestration import use_cases

    s = _use_case_set(db, tenant_id, set_id)
    if s.status == "draft":
        raise HTTPException(409, "Commit the use cases first - the test cases are written for what was implemented")
    s.tests_json = json.dumps(use_cases.generate_tests(s))
    db.commit()
    return use_cases.out(s)


@router.get("/use-cases/{set_id}/tests/{kind}")
def download_use_case_tests(set_id: int, kind: str, db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)) -> Response:
    """manual.docx | manual.csv | automation.py"""
    import json
    import re

    from app.orchestration import use_cases

    s = _use_case_set(db, tenant_id, set_id)
    tests = json.loads(s.tests_json or "{}")
    if not tests.get("manual"):
        raise HTTPException(404, "Generate the test cases first")
    stem = re.sub(r"[^\w-]+", "-", s.name).strip("-").lower() or "use-cases"
    if kind == "manual.docx":
        return Response(use_cases.tests_docx(s, tests["manual"]), media_type=_DOCX,
                        headers={"Content-Disposition": f'attachment; filename="test-cases-{stem}.docx"'})
    if kind == "manual.csv":
        return Response("﻿" + use_cases.tests_csv(tests["manual"]), media_type="text/csv; charset=utf-8",
                        headers={"Content-Disposition": f'attachment; filename="test-cases-{stem}.csv"'})
    if kind == "automation.py":
        return Response(tests["automation"], media_type="text/x-python; charset=utf-8",
                        headers={"Content-Disposition": f'attachment; filename="test_{stem.replace("-", "_")}.py"'})
    raise HTTPException(404, "manual.docx, manual.csv or automation.py")


@router.get("/brd-template")
def brd_template() -> Response:
    """The BRD template (Word), its sections read back by /brd."""
    from app.orchestration import process_brd

    return Response(process_brd.template_docx(), media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                    headers={"Content-Disposition": 'attachment; filename="BRT360-process-BRD-template.docx"'})

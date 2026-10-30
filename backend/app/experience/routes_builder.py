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


@router.get("/brd-template")
def brd_template() -> Response:
    """The BRD template (Word), its sections read back by /brd."""
    from app.orchestration import process_brd

    return Response(process_brd.template_docx(), media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                    headers={"Content-Disposition": 'attachment; filename="BRT360-process-BRD-template.docx"'})

"""The Process Builder API (orchestration/process_builder.py): a process
described in plain English becomes approval rules, Flow Studio flows, Page
Designer changes and assignment rules - asked about where it is unclear,
rehearsed, and built only on the administrator's consent."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.auth import get_current_user, require_roles
from app.crm_core import llm
from app.db import get_db
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


class BuildIn(DesignIn):
    mode: str = "rehearse"  # rehearse | execute


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
def build_process(payload: BuildIn, db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)) -> dict:
    """rehearse: built and rolled back, to show exactly what would be created.
    execute (the administrator's consent): built together, kept, verified."""
    if payload.mode not in ("rehearse", "execute"):
        raise HTTPException(422, "mode must be rehearse or execute")
    with llm.budget(AI_SECONDS):
        steps, answers, graphs = _steps(payload), payload.answers, payload.graphs
        if not steps:
            blueprint = process_builder.design(db, tenant_id, payload.request.strip(), answers, None, payload.graphs, payload.name)
            steps, answers = blueprint["steps"], blueprint.get("answers", answers)
            graphs = {**{c["id"]: c["spec"]["graph"] for c in blueprint["components"] if c["kind"] == "flow"}, **graphs}
        return process_builder.build(db, tenant_id, payload.request.strip(), steps, answers, graphs, payload.mode, payload.name)

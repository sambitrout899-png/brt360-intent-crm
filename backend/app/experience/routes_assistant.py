"""Ask anything (orchestration/assistant.py): one box for every request - routed
to an action, the agent, or the process builder - and the journal of them all."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.auth import get_current_user, require_roles
from app.crm_core import llm
from app.db import get_db
from app.models.auth import User, UserRole
from app.orchestration import assistant
from app.schemas.intent import IntentContext
from app.tenancy import get_current_tenant_id

# As much AI time as one request may use (nginx gives up at 120 s) - the rules answer past it.
AI_SECONDS = 45.0

# Like the intents: read-only viewers can't act.
router = APIRouter(prefix="/api/assistant", tags=["assistant"], dependencies=[Depends(require_roles(UserRole.admin, UserRole.agent))])


class UnderstandIn(BaseModel):
    text: str = Field(min_length=2, max_length=6000)
    context: IntentContext | None = None
    autonomous: bool = False


class OutcomeIn(BaseModel):
    status: str = Field(min_length=1, max_length=30)
    summary: str = Field(default="", max_length=500)


@router.post("/understand")
def understand(payload: UnderstandIn, db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id),
               user: User = Depends(get_current_user)) -> dict[str, Any]:
    """How the request is understood and where it goes - nothing runs or is built yet. Recorded in the journal."""
    with llm.budget(AI_SECONDS):
        return assistant.understand(db, tenant_id, user, payload.text, payload.context.model_dump(exclude_none=True) if payload.context else None,
                                   autonomous=payload.autonomous)


@router.get("/journal")
def journal(everyone: bool = False, db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id),
            user: User = Depends(get_current_user)) -> list[dict[str, Any]]:
    """My requests (administrators: everyone=true for the whole tenant's), newest first, with their outcomes."""
    return assistant.journal(db, tenant_id, user, everyone)


class ScratchDraftIn(BaseModel):
    request: str = Field(min_length=3, max_length=2000)


class ScratchBuildIn(BaseModel):
    spec: dict[str, Any]
    journal_id: int | None = None


@router.post("/scratch/draft", dependencies=[Depends(require_roles(UserRole.admin))])
def scratch_draft(payload: ScratchDraftIn, db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id)) -> dict[str, Any]:
    """A new action for a request nothing covers yet - what would be made. Nothing is saved."""
    from app.orchestration import scratch

    with llm.budget(AI_SECONDS):
        return scratch.draft(db, tenant_id, payload.request)


@router.post("/scratch/build", dependencies=[Depends(require_roles(UserRole.admin))])
def scratch_build(payload: ScratchBuildIn, db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id),
                  user: User = Depends(get_current_user)) -> dict[str, Any]:
    """Builds the action the admin consented to and switches it on."""
    from app.orchestration import scratch

    try:
        built = scratch.build(db, tenant_id, user, payload.spec)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    if payload.journal_id:
        try:
            assistant.record_outcome(db, tenant_id, user, payload.journal_id, "built", f"Built a new action: {built['intent']['label']}")
        except ValueError:
            pass  # not this user's request - the action is built all the same
    return built


@router.post("/journal/{journal_id}/outcome", status_code=204)
def outcome(journal_id: int, payload: OutcomeIn, db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id),
            user: User = Depends(get_current_user)) -> None:
    try:
        assistant.record_outcome(db, tenant_id, user, journal_id, payload.status, payload.summary)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc

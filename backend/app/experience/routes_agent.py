"""The agent API (orchestration/agent.py): a goal in plain words becomes a
chain of intents, which can be rehearsed (nothing kept) or run together."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.access import get_access
from app.auth import get_current_user, require_roles
from app.crm_core import llm
from app.crm_core.access_service import Access
from app.db import get_db
from app.models.auth import User, UserRole
from app.orchestration import agent
from app.schemas.intent import IntentContext
from app.tenancy import get_current_tenant_id

# The most AI time one request may use - a goal of many steps classifies each one; past this
# the rules answer, so the request finishes well inside nginx's proxy_read_timeout (120 s).
AI_SECONDS = 45.0

# Like the intents themselves: read-only viewers can't act.
router = APIRouter(prefix="/api/agent", tags=["agent"], dependencies=[Depends(require_roles(UserRole.admin, UserRole.agent))])


class AgentPlanIn(BaseModel):
    goal: str = Field(min_length=3, max_length=4000)
    context: IntentContext | None = None


class AgentStepIn(BaseModel):
    intent_type: str | None = None
    entities: dict[str, Any] = {}
    text: str = ""


class AgentRunIn(BaseModel):
    goal: str = ""
    steps: list[AgentStepIn] = Field(min_length=1, max_length=agent.MAX_STEPS)
    mode: str = "rehearse"  # rehearse | execute
    context: IntentContext | None = None


def _context(c: IntentContext | None) -> dict | None:
    return c.model_dump(exclude_none=True) if c else None


@router.post("/plan")
def plan_goal(payload: AgentPlanIn, db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id),
              user: User = Depends(get_current_user)) -> dict:
    """The steps the agent would take - nothing runs."""
    with llm.budget(AI_SECONDS):
        return agent.plan(db, tenant_id, user, payload.goal.strip(), _context(payload.context))


@router.post("/annotate")
def annotate_steps(payload: AgentRunIn, db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id),
                   user: User = Depends(get_current_user)) -> list[dict]:
    """Re-checks a plan the user edited (an action swapped, a value filled in)."""
    catalog = agent._catalog(db, tenant_id, user)
    with llm.budget(AI_SECONDS):
        return agent.annotate(db, tenant_id, [s.model_dump() for s in payload.steps], _context(payload.context), catalog)


@router.post("/run")
def run_plan(payload: AgentRunIn, db: Session = Depends(get_db), tenant_id: int = Depends(get_current_tenant_id),
             user: User = Depends(get_current_user), access: Access = Depends(get_access)) -> dict:
    if payload.mode not in ("rehearse", "execute"):
        raise HTTPException(422, "mode must be rehearse or execute")
    with llm.budget(AI_SECONDS):
        return agent.run(db, tenant_id, user, access, [s.model_dump() for s in payload.steps], payload.mode,
                     _context(payload.context), payload.goal)

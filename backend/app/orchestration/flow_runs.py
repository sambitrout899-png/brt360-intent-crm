"""Durable flow runs: a flow that waits, for time or for someone's approval.

A Wait or Wait for approval step (flow_engine) pauses the run. pause()
saves it here - the step it waits at, its context, trace and errors so
far, and the graph it runs on - in the same transaction as everything the
run did before the step, so a rolled-back intent leaves no waiting run.

The rest runs later, in its own transaction, through resume():
- a Wait continues when its time has come (run_due, a worker job every minute);
- a Wait for approval continues when the approver decides (decide), down its
  "approved" or "rejected" output - or, once its expiry passes, "expired".

What the rest of the run does commits or rolls back as one: if it fails,
nothing it did stays and the run is marked failed with the error. Records
it moved set off their own state flows, as they would during an intent.
{{approval.decision}}, {{approval.note}} and {{approval.by}} are there for
the steps after an approval. A waiting run can be cancelled by an admin.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
from typing import Any

from sqlalchemy.orm import Session

from app import rls
from app.crm_core import lifecycle_service
from app.data_intelligence import event_bus
from app.models.auth import User, UserRole
from app.models.flows import FlowDefinition, FlowRun
from app.models.lifecycle import PositionMember
from app.orchestration import flow_engine, flow_errors
from app.timeutils import utcnow

log = logging.getLogger(__name__)

MAX_STATE_FLOW_CASCADE = 3  # as for an intent (routes_intent.MAX_STATE_FLOW_CASCADE)


# ---------------------------------------------------------------- pausing


def pause(db: Session, tenant_id: int, execution_id: int, flow: FlowDefinition, graph: dict[str, Any], node: dict[str, Any],
          entry: dict[str, Any], ctx: dict[str, Any], trace: list[dict[str, Any]], caught: list[dict[str, Any]],
          touched: list[tuple[str, int]], entity_type: str | None, entity_id: int | None) -> FlowRun:
    """Saves a run that reached a wait step (entry: its trace entry, already in trace)."""
    config = entry.get("input") or {}
    now = utcnow()
    run = FlowRun(tenant_id=tenant_id, flow_id=flow.id, execution_id=execution_id, entity_type=entity_type, entity_id=entity_id,
                  status="waiting", node_id=node["id"], graph_json=json.dumps(graph), state_json="{}", subject="", message="",
                  decision_note="")
    if node["type"] == "wait":
        run.wait_kind = "timer"
        run.resume_at = now + dt.timedelta(minutes=flow_engine.wait_minutes(config) or 0)
    else:
        run.wait_kind = "approval"
        approver = str(config.get("approver") or "").strip()
        user = lifecycle_service.resolve_user(db, tenant_id, approver)
        position = None if user is not None else lifecycle_service.resolve_position(db, tenant_id, approver)
        if user is None and position is None:
            raise flow_errors.FlowError("NOT_FOUND", f"approver '{approver}' is not a user or a position").at(flow, node, entry["label"])
        run.approver_user_id = user.id if user is not None else None
        run.approver_position_id = position.id if position is not None else None
        run.subject = str(config.get("subject") or f"Approve: {flow.name}")[:300]
        run.message = str(config.get("message") or "")
        days = str(config.get("expire_days") or "").strip()
        run.resume_at = now + dt.timedelta(days=int(days)) if days.isdigit() else None
    db.add(run)
    db.flush()
    entry.update(run_id=run.id, waiting=describe(db, run))
    run.state_json = json.dumps({"ctx": ctx, "trace": trace, "caught": caught, "touched": touched}, default=str)
    return run


def describe(db: Session, run: FlowRun) -> dict[str, Any]:
    """What the run waits for, for people: {"kind", "until"} or {"kind", "approver", "expires"}."""
    if run.wait_kind == "timer":
        return {"kind": "timer", "until": run.resume_at.isoformat() if run.resume_at else None}
    if run.approver_user_id:
        user = db.get(User, run.approver_user_id)
        approver = user.name if user else f"user {run.approver_user_id}"
    else:
        from app.models.lifecycle import Position

        position = db.get(Position, run.approver_position_id) if run.approver_position_id else None
        approver = position.name if position else "a position"
    return {"kind": "approval", "approver": approver, "subject": run.subject,
            "expires": run.resume_at.isoformat() if run.resume_at else None}


# ---------------------------------------------------------------- continuing


def resume(db: Session, run: FlowRun, branch: str) -> dict[str, Any]:
    """Runs the rest of the flow from the step `run` waits at, down `branch`.
    Sets the run's status and outcome; the caller commits."""
    flow = db.get(FlowDefinition, run.flow_id)
    if flow is None:  # the flow was deleted while the run waited
        run.status = "cancelled"
        run.outcome_json = json.dumps({"cancelled": "the flow no longer exists"})
        return {"outcome": "cancelled"}
    state = json.loads(run.state_json or "{}")
    ctx = {**(state.get("ctx") or {}), "entity_type": run.entity_type, "entity_id": run.entity_id}
    if run.wait_kind == "approval":
        by = db.get(User, run.decided_by) if run.decided_by else None
        ctx["approval"] = {"decision": run.decision, "note": run.decision_note, "by": by.name if by else ""}
        steps = ctx.setdefault("steps", {})
        steps[run.node_id] = {**steps.get(run.node_id, {}), "output": ctx["approval"]}  # {{steps.<id>.output.decision}}
    savepoint = db.begin_nested()
    try:
        summary = flow_engine.run_flow(db, run.tenant_id, run.execution_id, flow, ctx, resume={
            "graph": json.loads(run.graph_json), "node_id": run.node_id, "branch": branch,
            "trace": state.get("trace") or [], "caught": state.get("caught") or [], "touched": state.get("touched") or []})
        _follow_up(db, run, summary, ctx)
        savepoint.commit()
        run.status = "continued" if summary.get("outcome") == "waiting" else summary.get("outcome") or "completed"
    except flow_engine.FlowRejected as exc:
        if savepoint.is_active:
            savepoint.rollback()
        summary = {**(getattr(exc, "run", None) or {}), "outcome": "rejected", "message": str(exc)}
        run.status = "rejected"
    except Exception as exc:  # noqa: BLE001 - whatever the rest of the run hit, it is undone and recorded on the run
        if savepoint.is_active:
            savepoint.rollback()
        err = flow_errors.classify(exc, flow)
        summary = {**(err.run or {}), "outcome": "failed", "error": err.as_dict()}
        run.status = "failed"
        event_bus.publish(db, run.execution_id, "flow.failed", {"flow_id": flow.id, "flow_name": flow.name, "code": err.code,
                                                                "message": err.message, "run_id": run.id})
    run.outcome_json = json.dumps(summary, default=str)
    return summary


def _follow_up(db: Session, run: FlowRun, summary: dict[str, Any], ctx: dict[str, Any]) -> None:
    """What an intent does after its flows (routes_intent's lifecycle hook):
    state history and assignment rules for the records the rest of the run
    moved, then their state flows, cascading a bounded number of times."""
    touched = [tuple(t) for t in summary.get("touched", [])]
    if run.entity_type and run.entity_id and (run.entity_type, run.entity_id) not in touched:
        touched.insert(0, (run.entity_type, run.entity_id))
    base = {k: ctx.get(k) for k in ("entities", "result", "trigger") if ctx.get(k) is not None}
    entries = lifecycle_service.sync_records(db, run.tenant_id, touched, "flow")
    for _ in range(MAX_STATE_FLOW_CASCADE):
        moved: list[tuple[str, int]] = []
        for entry in entries:
            for flow in flow_engine.flows_for_state(db, run.tenant_id, entry["entity_type"], entry["to_state"]):
                done = flow_engine.run_flow_guarded(db, run.tenant_id, run.execution_id, flow,
                                                    {**base, "entity_type": entry["entity_type"], "entity_id": entry["entity_id"]})
                if done.get("failed") or done.get("skipped"):
                    continue
                moved.append((entry["entity_type"], entry["entity_id"]))
                moved.extend(tuple(t) for t in done.get("touched", []) if tuple(t) not in moved)
        if not moved:
            return
        entries = lifecycle_service.sync_records(db, run.tenant_id, moved, "flow")


def run_due(session_factory, now: dt.datetime | None = None, limit: int = 100) -> int:
    """The worker's job: waits whose time has come continue, and approval
    requests past their expiry continue down "expired". Each in its own
    transaction; returns how many continued."""
    now = now or utcnow()
    with session_factory() as db:
        due = [row.id for row in db.query(FlowRun.id).filter(
            FlowRun.status == "waiting", FlowRun.resume_at.isnot(None), FlowRun.resume_at <= now,
        ).order_by(FlowRun.resume_at).limit(limit)]
    done = 0
    for run_id in due:
        with session_factory() as db:
            try:
                run = db.query(FlowRun).filter(FlowRun.id == run_id, FlowRun.status == "waiting").with_for_update().first()
                if run is None:  # decided or cancelled meanwhile
                    continue
                rls.scope(db, run.tenant_id)
                if run.wait_kind == "approval":
                    run.decision, run.decided_at = "expired", now
                resume(db, run, "expired" if run.wait_kind == "approval" else "")
                db.commit()
                done += 1
            except Exception:  # noqa: BLE001 - one run's trouble never stops the others
                db.rollback()
                log.exception("Flow run %s could not continue", run_id)
    return done


# ---------------------------------------------------------------- approvals


def _position_ids(db: Session, user: User) -> list[int]:
    return [row.position_id for row in db.query(PositionMember.position_id).filter(PositionMember.user_id == user.id)]


def approvals_for(db: Session, tenant_id: int, user: User) -> list[FlowRun]:
    """Approval requests waiting for this user - named, or through a position they hold."""
    positions = _position_ids(db, user)
    query = db.query(FlowRun).filter(FlowRun.tenant_id == tenant_id, FlowRun.status == "waiting", FlowRun.wait_kind == "approval")
    mine = FlowRun.approver_user_id == user.id
    if positions:
        mine = mine | FlowRun.approver_position_id.in_(positions)
    return query.filter(mine).order_by(FlowRun.created_at).all()


def may_decide(db: Session, run: FlowRun, user: User) -> bool:
    """The approver, a holder of the approver position, or a tenant admin (to unblock a run)."""
    return (user.role == UserRole.admin or run.approver_user_id == user.id
            or (run.approver_position_id is not None and run.approver_position_id in _position_ids(db, user)))


def decide(db: Session, run: FlowRun, user: User, approve: bool, note: str = "") -> dict[str, Any]:
    if run.status != "waiting" or run.wait_kind != "approval":
        raise ValueError("This request has already been decided, expired or cancelled")
    if not may_decide(db, run, user):
        raise PermissionError("You are not the approver of this request")
    run.decision = "approved" if approve else "rejected"
    run.decided_by, run.decided_at, run.decision_note = user.id, utcnow(), note.strip()[:2000]
    event_bus.publish(db, run.execution_id, "flow.approval_decided", {"run_id": run.id, "decision": run.decision, "by": user.id})
    return resume(db, run, run.decision)


def cancel(db: Session, run: FlowRun, user: User) -> None:
    if run.status != "waiting":
        raise ValueError("Only a waiting run can be cancelled")
    run.status = "cancelled"
    run.decided_by, run.decided_at = user.id, utcnow()
    run.outcome_json = json.dumps({"cancelled_by": user.name})
    event_bus.publish(db, run.execution_id, "flow.cancelled", {"run_id": run.id, "by": user.id})


def view(db: Session, run: FlowRun) -> dict[str, Any]:
    """A run for the screens."""
    flow = db.get(FlowDefinition, run.flow_id)
    outcome = json.loads(run.outcome_json) if run.outcome_json else None
    return {"id": run.id, "flow_id": run.flow_id, "flow_name": flow.name if flow else "(deleted flow)", "status": run.status,
            "wait": describe(db, run), "entity_type": run.entity_type, "entity_id": run.entity_id, "subject": run.subject,
            "message": run.message, "decision": run.decision, "decision_note": run.decision_note, "created_at": run.created_at,
            "decided_at": run.decided_at,
            "error": (outcome or {}).get("error") if run.status == "failed" else None}

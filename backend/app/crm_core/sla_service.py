"""SLA clock lifecycle + breach sweep (Phase 1 primitive #3).

start_clock/complete_clock are the two calls a Phase 2 intent step would
make (e.g. INT-09 starting a retention-window clock, then completing it
if the customer responds in time). process_due_clocks is the sweep - run
it from a cron/systemd timer hitting POST /api/admin/sla/process-due, or
call it directly. Every sweep writes one system-channel
IntentExecutionLog row (intent_type "SYS-SLA") so breaches show up in the
existing Audit Log alongside everything else, with one sla.breached
DomainEvent per clock it broke.
"""

from __future__ import annotations

import datetime as dt
from typing import Any, Callable

from sqlalchemy.orm import Session

from app.data_intelligence import event_bus
from app.models.intent_log import IntentExecutionLog
from app.models.sla import SlaClock, SlaClockStatus, SlaPolicy
from app.timeutils import utcnow

# Breach actions an admin can configure (BRD vocabulary: auto-approve KYC,
# auto-cancel on unpaid invoice, auto-proceed past a retention window, or
# escalate to a human as "Jeopardy"). Only "escalate" has a real,
# domain-agnostic handler today - any other configured action falls back
# to escalate (and says so in the clock's detail) until Phase 2 registers
# a real handler for that specific clock_type via register_breach_handler.
ESCALATE = "escalate"
AUTO_APPROVE = "auto_approve"
AUTO_CANCEL = "auto_cancel"
AUTO_PROCEED = "auto_proceed"
KNOWN_BREACH_ACTIONS = [ESCALATE, AUTO_APPROVE, AUTO_CANCEL, AUTO_PROCEED]

#  (db, clock, correlation_id) -> outcome dict. correlation_id is the
# sweep's own SYS-SLA IntentExecutionLog id - handlers that publish their
# own domain events (via event_bus.publish) must use this, not clock.id,
# since DomainEvent.correlation_id is a FK to intent_execution_log.
BreachHandler = Callable[[Session, SlaClock, int], dict[str, Any]]
_BREACH_HANDLERS: dict[str, BreachHandler] = {}


def register_breach_handler(clock_type: str, handler: BreachHandler) -> None:
    """Phase 2 hook: wire a real action for breaches of this clock_type,
    e.g. register_breach_handler("kyc_verification", _auto_approve_kyc).
    """
    _BREACH_HANDLERS[clock_type] = handler


def _escalate(db: Session, clock: SlaClock, correlation_id: int) -> dict[str, Any]:
    if clock.on_breach_action != ESCALATE:
        detail = (
            f"No handler registered yet for action '{clock.on_breach_action}' on clock_type "
            f"'{clock.clock_type}' - escalating instead."
        )
    else:
        detail = f"{clock.clock_type} on {clock.entity_type} #{clock.entity_id} is in Jeopardy."
    return {"outcome": "escalated", "detail": detail}


def start_clock(db: Session, tenant_id: int, clock_type: str, entity_type: str, entity_id: int) -> SlaClock | None:
    """Starts a clock from the tenant's active policy for this clock_type.
    Returns None (no-op) if no such policy is configured - not every
    tenant needs every SLA.
    """
    policy = (
        db.query(SlaPolicy)
        .filter(SlaPolicy.tenant_id == tenant_id, SlaPolicy.clock_type == clock_type, SlaPolicy.is_active.is_(True))
        .first()
    )
    if policy is None:
        return None
    now = utcnow()
    clock = SlaClock(
        tenant_id=tenant_id,
        clock_type=clock_type,
        entity_type=entity_type,
        entity_id=entity_id,
        on_breach_action=policy.on_breach_action,
        started_at=now,
        deadline=now + dt.timedelta(hours=policy.duration_hours),
        status=SlaClockStatus.running,
    )
    db.add(clock)
    db.flush()
    return clock


def complete_clock(db: Session, tenant_id: int, clock_type: str, entity_type: str, entity_id: int) -> SlaClock | None:
    """Marks the running clock for this entity resolved in time. No-op if
    none is running (e.g. no policy was configured when it would have
    started).
    """
    clock = (
        db.query(SlaClock)
        .filter(
            SlaClock.tenant_id == tenant_id,
            SlaClock.clock_type == clock_type,
            SlaClock.entity_type == entity_type,
            SlaClock.entity_id == entity_id,
            SlaClock.status == SlaClockStatus.running,
        )
        .first()
    )
    if clock is None:
        return None
    clock.status = SlaClockStatus.completed
    clock.resolved_at = utcnow()
    db.flush()
    return clock


def process_due_clocks(db: Session, tenant_id: int, as_of: dt.datetime | None = None) -> dict[str, Any]:
    """Sweep: breach every running clock past its deadline, run its
    handler, and record one audit log entry for the whole sweep.
    """
    as_of = as_of or utcnow()
    due = (
        db.query(SlaClock)
        .filter(SlaClock.tenant_id == tenant_id, SlaClock.status == SlaClockStatus.running, SlaClock.deadline <= as_of)
        .all()
    )

    log = IntentExecutionLog(
        tenant_id=tenant_id,
        channel="system",
        intent_type="SYS-SLA",
        raw_text="[system] SLA breach sweep",
        entities_json="{}",
        context_json="{}",
        plan_json="[]",
        confidence=1.0,
        status="executed",
        rationale=f"Checked for clocks past deadline as of {as_of.isoformat()}.",
        completed_at=as_of,
    )
    db.add(log)
    db.flush()

    breached: list[dict[str, Any]] = []
    for clock in due:
        handler = _BREACH_HANDLERS.get(clock.clock_type, _escalate)
        outcome = handler(db, clock, log.id)
        clock.status = SlaClockStatus.breached
        clock.resolved_at = as_of
        clock.detail = outcome.get("detail", "")
        db.flush()
        event_bus.publish(
            db,
            log.id,
            "sla.breached",
            {
                "clock_id": clock.id,
                "clock_type": clock.clock_type,
                "entity_type": clock.entity_type,
                "entity_id": clock.entity_id,
                "configured_action": clock.on_breach_action,
                **outcome,
            },
        )
        breached.append({"clock_id": clock.id, "clock_type": clock.clock_type, **outcome})

    log.rationale += f" {len(due)} clock(s) checked, {len(breached)} breached."
    db.flush()
    return {"execution_id": log.id, "checked": len(due), "breached": breached}

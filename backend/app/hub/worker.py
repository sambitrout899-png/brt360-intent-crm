"""The queue behind the hub: sends what is due, processes async inbound calls, turns
events into webhooks, runs scheduled pulls and keeps the log from growing for ever.

Claims are safe with several workers: FOR UPDATE SKIP LOCKED plus a lease, so a
worker that dies mid-send lets go of its message when the lease runs out. Delivery
is therefore at-least-once; the correlation id goes out as Idempotency-Key so a
receiver can tell a retry from a new request.

A dedicated loop (start_loop) polls every few seconds - a queued call shouldn't
wait for the minute-by-minute scheduler. housekeeping() is the scheduler's job.
"""

from __future__ import annotations

import datetime as dt
import fnmatch
import hashlib
import json
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app import rls
from app.config import get_settings
from app.hub import outbound
from app.models.integration_hub import HubConnection, HubMessage, HubOperation, HubSubscription
from app.models.intent_log import DomainEvent, IntentExecutionLog
from app.timeutils import utcnow

log = logging.getLogger("brt360.integration")

LEASE = dt.timedelta(minutes=5)
POLL_SECONDS = 3
WORKERS = 4
BATCH = 40
EVENT_OVERLAP = 2000  # how far behind the cursor to look again: a long transaction's event can commit after later ones
EVENT_BATCH = 500
_started = False
_start_lock = threading.Lock()


def claim(session_factory, limit: int = BATCH) -> list[int]:
    """Takes what is due, and what a dead worker left behind."""
    now = utcnow()
    with session_factory() as db:
        for stuck in db.query(HubMessage).filter(HubMessage.status.in_(("sending", "processing")), HubMessage.locked_until.isnot(None),
                                                 HubMessage.locked_until < now).with_for_update(skip_locked=True).limit(limit):
            # a send that may have gone out: retry (the receiver has its Idempotency-Key). Inbound work is not re-run blind.
            if stuck.direction == "out" and stuck.attempts < stuck.max_attempts:
                stuck.status, stuck.next_attempt_at, stuck.locked_until = "queued", now, None
            else:
                stuck.status, stuck.finished_at, stuck.locked_until = "dead", now, None
                stuck.error = "The worker stopped while handling this - check the target before replaying it"
        db.flush()
        rows = db.query(HubMessage).filter(HubMessage.status == "queued", HubMessage.next_attempt_at <= now).order_by(
            HubMessage.next_attempt_at, HubMessage.id).with_for_update(skip_locked=True).limit(limit).all()
        ids = []
        for msg in rows:
            msg.status = "sending" if msg.direction == "out" else "processing"
            msg.locked_until = now + LEASE
            ids.append(msg.id)
        db.commit()
    return ids


def handle(session_factory, message_id: int) -> None:
    with session_factory() as db:
        msg = db.get(HubMessage, message_id)
        if msg is None:
            return
        rls.scope(db, msg.tenant_id)
        try:
            if msg.direction == "out":
                outbound.send_queued(db, msg)
            else:
                from app.hub import inbound

                inbound.process_queued(db, msg)
            db.commit()
        except Exception as exc:  # noqa: BLE001 - recorded on the message; the next one goes on
            db.rollback()
            log.exception("Integration message %s failed unexpectedly", message_id)
            rls.scope(db, None)
            msg = db.get(HubMessage, message_id)
            if msg is not None:
                msg.status, msg.locked_until, msg.finished_at = "dead", None, utcnow()
                msg.error = f"Unexpected: {type(exc).__name__}: {exc}"[:2000]
                db.commit()


def run_due(session_factory, limit: int = BATCH, workers: int = 1) -> int:
    ids = claim(session_factory, limit)
    if workers > 1 and len(ids) > 1:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            list(pool.map(lambda message_id: handle(session_factory, message_id), ids))
    else:
        for message_id in ids:
            handle(session_factory, message_id)
    return len(ids)


# ---------------------------------------------------------------- replies


def reply_received(db: Session, msg: HubMessage, op: HubOperation, answer: Any) -> None:
    """A request-reply call got its answer - as its HTTP response, or later on its callback."""
    reply = outbound.map_reply(op, answer)
    msg.reply_json = json.dumps(reply, default=str) if reply is not None else ""
    msg.status, msg.finished_at, msg.error, msg.locked_until = "replied", utcnow(), "", None
    if op.reply_interface_id:
        _load_reply(db, msg, op, reply)
    _continue_flow(db, msg, "replied", {"reply": reply, "correlation_id": msg.correlation_id, "http_status": msg.http_status})


def _load_reply(db: Session, msg: HubMessage, op: HubOperation, reply: Any) -> None:
    from app.crm_core import bim_service
    from app.models.bim import BimInterface

    interface = db.get(BimInterface, op.reply_interface_id)
    if interface is None:
        return
    rows = bim_service.records_in(reply, interface.pull_records_path)
    if rows:
        savepoint = db.begin_nested()
        try:
            job, _ = bim_service.run(db, msg.tenant_id, interface.entity, rows, "api", interface, None, False, True, None)
            savepoint.commit()
            msg.source = f"{msg.source} -> BIM job {job.id}"[:100]
        except ValueError as exc:
            savepoint.rollback()
            msg.error = f"The reply could not be loaded: {exc}"[:2000]


def delivery_failed(db: Session, msg: HubMessage) -> None:
    _continue_flow(db, msg, "failed", {"error": msg.error, "correlation_id": msg.correlation_id, "http_status": msg.http_status})


def _continue_flow(db: Session, msg: HubMessage, branch: str, output: dict[str, Any]) -> None:
    if not msg.flow_run_id:
        return
    from app.models.flows import FlowRun
    from app.orchestration import flow_runs

    run = db.query(FlowRun).filter(FlowRun.id == msg.flow_run_id, FlowRun.status == "waiting", FlowRun.wait_kind == "reply").with_for_update().first()
    if run is not None:  # else it expired or was cancelled meanwhile: the reply is kept on the message, and that's all
        flow_runs.reply_arrived(db, run, branch, output)


def callback(db: Session, token: str, body: bytes, content_type: str) -> tuple[int, dict[str, Any]]:
    """An external system's reply to an async request. The token in the address is the only credential:
    long, random, single-use, and stored only as its hash."""
    hashed = hashlib.sha256(token.encode()).hexdigest()
    msg = db.query(HubMessage).filter(HubMessage.reply_token_hash == hashed).with_for_update().first()
    if msg is None:
        return 404, {"detail": "No request is waiting at this address"}
    rls.scope(db, msg.tenant_id)
    if msg.status != "awaiting_reply":
        return 409, {"detail": f"This request has already ended ({msg.status})", "correlation_id": msg.correlation_id}
    op = db.get(HubOperation, msg.operation_id)
    answer, fault = outbound.read_answer(outbound.HttpResult(200, {"content-type": content_type}, body, 0), (op.protocol if op else "") == "soap")
    msg.response_body = body.decode("utf-8", "replace")[:outbound.MAX_STORED]
    if fault:
        msg.status, msg.finished_at, msg.error = "failed", utcnow(), f"SOAP fault {fault['code']}: {fault['message']}"[:2000]
        delivery_failed(db, msg)
    elif op is None:
        msg.status, msg.finished_at = "replied", utcnow()
    else:
        reply_received(db, msg, op, answer)
    correlation_id = msg.correlation_id
    db.commit()
    return 200, {"received": True, "correlation_id": correlation_id}


# ---------------------------------------------------------------- events -> webhooks


def event_envelope(event: DomainEvent, tenant_id: int, subscription: HubSubscription) -> dict[str, Any]:
    """CloudEvents 1.0: the shape webhook receivers and event routers already understand."""
    try:
        data = json.loads(event.payload_json or "{}")
    except ValueError:
        data = {}
    created = event.created_at.isoformat(timespec="seconds") + "Z" if event.created_at else None
    return {"specversion": "1.0", "id": event.brt_id or str(event.id), "source": f"brt360/tenant/{tenant_id}", "type": event.event_type,
            "time": created, "datacontenttype": "application/json", "subscription": subscription.name, "data": data}


def enqueue_events(session_factory, limit: int = EVENT_BATCH) -> int:
    """New events of each subscription become messages, once each (the unique index holds that even if two
    workers race). It looks a little behind its cursor: event ids are given out when a transaction writes them,
    so a slow transaction's event can commit after a later id has been read."""
    with session_factory() as db:
        subs = [(s.id, s.tenant_id) for s in db.query(HubSubscription.id, HubSubscription.tenant_id).filter(HubSubscription.is_active.is_(True))]
    made = 0
    for sub_id, tenant_id in subs:
        with session_factory() as db:
            rls.scope(db, tenant_id)
            sub = db.get(HubSubscription, sub_id)
            op = db.get(HubOperation, sub.operation_id) if sub else None
            if sub is None or op is None or not op.is_active:
                continue
            conn = db.get(HubConnection, op.connection_id)
            if conn is None:
                continue
            since = max(sub.start_event_id, sub.last_event_id - EVENT_OVERLAP)
            done = {row[0] for row in db.query(HubMessage.event_id).filter(HubMessage.subscription_id == sub.id, HubMessage.event_id > since)}
            rows = db.query(DomainEvent).join(IntentExecutionLog, IntentExecutionLog.id == DomainEvent.correlation_id).filter(
                IntentExecutionLog.tenant_id == tenant_id, DomainEvent.id > since).order_by(DomainEvent.id).limit(limit).all()
            top = sub.last_event_id
            for event in rows:
                top = max(top, event.id)
                if event.id in done or not fnmatch.fnmatchcase(event.event_type, sub.event_pattern):
                    continue
                if event.event_type.startswith("integration."):
                    continue
                msg = outbound.enqueue(db, tenant_id, op, conn, event_envelope(event, tenant_id, sub), kind="webhook",
                                       source=f"event:{event.event_type}"[:100], subscription_id=sub.id, event_id=event.id)
                msg.mode = "async"
                made += 1
            sub.last_event_id = top
            db.commit()
    return made


def current_event_id(db: Session) -> int:
    return db.query(func.max(DomainEvent.id)).scalar() or 0


# ---------------------------------------------------------------- scheduled pulls (BIM interfaces)


def run_pulls(session_factory) -> int:
    """Interfaces that fetch their own records: when due, call the operation and load what comes back. The time of the
    last good pull is {{cursor}} in the operation's address or body, so only what changed needs to come."""
    from app.crm_core import bim_service
    from app.models.bim import BimInterface

    now = utcnow()
    with session_factory() as db:
        due = [(i.id, i.tenant_id) for i in db.query(BimInterface).filter(BimInterface.is_active.is_(True), BimInterface.pull_operation_id.isnot(None),
                                                                           BimInterface.pull_every_minutes > 0)
               if i.pull_last_at is None or i.pull_last_at + dt.timedelta(minutes=i.pull_every_minutes) <= now]
    done = 0
    for interface_id, tenant_id in due:
        with session_factory() as db:
            rls.scope(db, tenant_id)
            interface = db.get(BimInterface, interface_id)
            op = db.get(HubOperation, interface.pull_operation_id)
            conn = db.get(HubConnection, op.connection_id) if op else None
            if op is None or conn is None or not op.is_active or not conn.is_active:
                continue
            cursor = interface.pull_cursor or ""
            interface.pull_last_at = now  # a failing source is tried again next period, not every few seconds
            msg = outbound._message(tenant_id, op, conn, {"cursor": cursor}, kind="pull", status="sending", source=f"pull:{interface.code}"[:100])
            msg.mode, msg.attempts = "sync", 1
            db.add(msg)
            db.commit()
            rls.scope(db, tenant_id)
            try:
                answer, result, sent = outbound.attempt(db, op, conn, {"cursor": cursor}, msg.correlation_id, extra={"cursor": cursor})
                outbound._record_exchange(msg, sent, result)
                rows = bim_service.records_in(outbound.map_reply(op, answer), interface.pull_records_path)
                job = None
                if rows:
                    job, _ = bim_service.run(db, tenant_id, interface.entity, rows, "pull", interface, None, False, True, None)
                msg.status, msg.finished_at = "replied", utcnow()
                msg.reply_json = json.dumps({"records": len(rows), "job_id": job.id if job else None})
                interface.pull_cursor = now.isoformat(timespec="seconds") + "Z"
                done += 1
            except (outbound.CallFailed, outbound.NotConfigured, ValueError) as exc:
                msg.status, msg.finished_at, msg.error = "failed", utcnow(), str(exc)[:2000]
                if isinstance(exc, outbound.CallFailed):
                    outbound._record_exchange(msg, getattr(exc, "sent", None), exc.response)
                log.warning("Pull of interface %s failed: %s", interface.code, exc)
            db.commit()
    return done


# ---------------------------------------------------------------- housekeeping


def expire_replies(session_factory) -> int:
    with session_factory() as db:
        rows = db.query(HubMessage).filter(HubMessage.status == "awaiting_reply", HubMessage.reply_deadline < utcnow()).all()
        for msg in rows:
            msg.status, msg.finished_at, msg.error = "timed_out", utcnow(), "No reply came before the deadline"
            msg.reply_token_hash = None
        db.commit()
    return len(rows)


FINAL = ("delivered", "replied", "succeeded", "failed", "timed_out", "cancelled")


def purge(session_factory) -> int:
    """The log is kept for INTEGRATION_LOG_DAYS. The dead-letter queue is kept three times as long: someone has yet to act on it."""
    days = get_settings().integration_log_days
    if days <= 0:
        return 0
    now = utcnow()
    with session_factory() as db:
        removed = db.query(HubMessage).filter(or_(
            HubMessage.status.in_(FINAL) & (HubMessage.created_at < now - dt.timedelta(days=days)),
            (HubMessage.status == "dead") & (HubMessage.created_at < now - dt.timedelta(days=days * 3)),
        )).delete(synchronize_session=False)
        db.commit()
    return removed


def housekeeping(session_factory) -> None:
    """The scheduler's job, every minute. Each part on its own: one failing never stops the others."""
    for name, part in (("reply deadlines", expire_replies), ("pulls", run_pulls), ("log retention", purge)):
        try:
            part(session_factory)
        except Exception:  # noqa: BLE001
            log.exception("Integration %s failed", name)


def tick(session_factory, workers: int = WORKERS) -> int:
    enqueue_events(session_factory)
    return run_due(session_factory, workers=workers)


def _loop(session_factory) -> None:
    while True:
        try:
            busy = tick(session_factory)
        except Exception:  # noqa: BLE001 - the database restarting mustn't end the loop
            log.exception("Integration worker pass failed")
            busy = 0
        if not busy:
            time.sleep(POLL_SECONDS)


def start_loop(session_factory) -> None:
    """One daemon thread per process that runs the scheduler (it has claimed the scheduler's lock)."""
    global _started
    with _start_lock:
        if _started:
            return
        _started = True
    threading.Thread(target=_loop, args=(session_factory,), name="integration-worker", daemon=True).start()

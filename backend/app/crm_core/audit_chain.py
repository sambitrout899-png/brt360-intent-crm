"""Tamper-evident audit: a hash chain over what happened, per tenant.

extend() (a worker job, every minute) appends one entry for every finished
record not yet chained - intent runs that ended (executed / failed / ...,
not ones still waiting for review) once they have settled for two minutes,
and training audit entries. Each entry holds the record's fingerprint and
hash = SHA-256(previous entry's hash + this entry), so

- a record changed after it was chained no longer matches its fingerprint;
- an entry changed, removed or slipped in breaks the hashes from there on.

verify() walks a tenant's chain and says where it breaks, if it does. It
proves the records weren't changed since they were chained, not that they
were right when written. The chain reads the intent and training logs
directly for now; with an event stream it would be fed from their events.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
from typing import Any

from sqlalchemy import exists
from sqlalchemy.orm import Session

from app.models.audit_chain import AuditChainEntry
from app.models.intent_log import IntentExecutionLog
from app.models.training import TrainingAudit
from app.timeutils import utcnow

GENESIS = "0" * 64
SETTLE = dt.timedelta(minutes=2)  # an intent run may still be written to just after it completes
OPEN_STATUSES = ("pending", "needs_review")

# source -> (model, the fields its fingerprint covers)
SOURCES: dict[str, tuple[type, tuple[str, ...]]] = {
    "intent_execution_log": (IntentExecutionLog, (
        "id", "tenant_id", "user_id", "channel", "intent_type", "raw_text", "entities_json", "context_json", "plan_json",
        "confidence", "status", "rationale", "human_override_json", "created_at", "completed_at")),
    "training_audit": (TrainingAudit, (
        "id", "tenant_id", "actor_id", "action", "entity", "entity_id", "before", "after", "ip", "at")),
}


def _sha(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str, separators=(",", ":")).encode()).hexdigest()


def fingerprint(source: str, row: Any) -> str:
    _, fields = SOURCES[source]
    return _sha({f: getattr(row, f) for f in fields})


def entry_hash(prev_hash: str, tenant_id: int, seq: int, source: str, source_id: int, record_sha256: str) -> str:
    return _sha([prev_hash, tenant_id, seq, source, source_id, record_sha256])


def _chainable(db: Session, source: str, batch: int) -> list[Any]:
    model, _ = SOURCES[source]
    chained = exists().where(AuditChainEntry.source == source, AuditChainEntry.source_id == model.id)
    query = db.query(model).filter(~chained)
    if model is IntentExecutionLog:
        query = query.filter(IntentExecutionLog.completed_at.isnot(None), IntentExecutionLog.completed_at <= utcnow() - SETTLE,
                             IntentExecutionLog.status.notin_(OPEN_STATUSES))
    return query.order_by(model.id).limit(batch).all()


def extend(db: Session, batch: int = 500) -> int:
    """Chains every finished record not chained yet; returns how many."""
    heads: dict[int, tuple[int, str]] = {}  # tenant -> (last seq, last hash)

    def head(tenant_id: int) -> tuple[int, str]:
        if tenant_id not in heads:
            last = (db.query(AuditChainEntry).filter(AuditChainEntry.tenant_id == tenant_id)
                    .order_by(AuditChainEntry.seq.desc()).first())
            heads[tenant_id] = (last.seq, last.hash) if last else (0, GENESIS)
        return heads[tenant_id]

    added = 0
    for source in SOURCES:
        for row in _chainable(db, source, batch):
            seq, prev = head(row.tenant_id)
            record = fingerprint(source, row)
            digest = entry_hash(prev, row.tenant_id, seq + 1, source, row.id, record)
            db.add(AuditChainEntry(tenant_id=row.tenant_id, seq=seq + 1, at=utcnow(), source=source, source_id=row.id,
                                   record_sha256=record, prev_hash=prev, hash=digest))
            heads[row.tenant_id] = (seq + 1, digest)
            added += 1
    db.commit()
    return added


def verify(db: Session, tenant_id: int, limit: int = 20) -> dict[str, Any]:
    """Walks the tenant's chain: {"entries", "ok", "problems": [first `limit`]}."""
    problems: list[dict[str, Any]] = []
    prev, expected_seq = GENESIS, 1
    entries = db.query(AuditChainEntry).filter(AuditChainEntry.tenant_id == tenant_id).order_by(AuditChainEntry.seq).all()
    for entry in entries:
        where = {"seq": entry.seq, "source": entry.source, "source_id": entry.source_id}
        if entry.seq != expected_seq:
            problems.append({**where, "problem": f"entry {expected_seq} is missing"})
        if entry.prev_hash != prev:
            problems.append({**where, "problem": "does not follow the entry before it"})
        if entry.hash != entry_hash(entry.prev_hash, entry.tenant_id, entry.seq, entry.source, entry.source_id, entry.record_sha256):
            problems.append({**where, "problem": "the entry was changed"})
        model = SOURCES.get(entry.source, (None,))[0]
        record = db.get(model, entry.source_id) if model is not None else None
        if record is None:
            problems.append({**where, "problem": "the record was deleted"})
        elif fingerprint(entry.source, record) != entry.record_sha256:
            problems.append({**where, "problem": "the record was changed after it was chained"})
        prev, expected_seq = entry.hash, entry.seq + 1
    last = entries[-1] if entries else None
    return {"entries": len(entries), "ok": not problems, "problems": problems[:limit], "problem_count": len(problems),
            "last_hash": last.hash if last else None, "last_at": last.at if last else None}

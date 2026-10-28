"""The tamper-evident audit chain (app/crm_core/audit_chain.py)."""

import datetime as dt

from app.crm_core import audit_chain
from app.models.audit_chain import AuditChainEntry
from app.models.intent_log import IntentExecutionLog
from app.models.tenant import Tenant
from app.models.training import TrainingAudit
from app.timeutils import utcnow


def _run(db, tenant_id, text, status="executed", minutes_ago=10):
    log = IntentExecutionLog(tenant_id=tenant_id, channel="agent", intent_type="INT-01", raw_text=text, entities_json="{}",
                             context_json="{}", plan_json="[]", confidence=0.9, status=status,
                             completed_at=utcnow() - dt.timedelta(minutes=minutes_ago))
    db.add(log)
    db.flush()
    return log


def _setup(client):
    db = client.TestSessionLocal()
    tenant_id = db.query(Tenant.id).first()[0]
    other = Tenant(name="Other", slug="other-chain")
    db.add(other)
    db.flush()
    return db, tenant_id, other.id


def test_chain_takes_finished_records_once_and_verifies(client):
    db, tid, other = _setup(client)
    try:
        a = _run(db, tid, "first")
        _run(db, tid, "waiting", status="needs_review")  # may still change: not chained
        _run(db, tid, "just finished", minutes_ago=0)  # not settled yet
        _run(db, other, "another tenant")
        db.add(TrainingAudit(tenant_id=tid, actor_id=1, action="create", entity="program", entity_id="7", after={"name": "X"}))
        db.commit()

        assert audit_chain.extend(db) == 3
        assert audit_chain.extend(db) == 0  # each record once
        mine = db.query(AuditChainEntry).filter_by(tenant_id=tid).order_by(AuditChainEntry.seq).all()
        assert [(e.seq, e.source) for e in mine] == [(1, "intent_execution_log"), (2, "training_audit")]
        assert mine[0].source_id == a.id and mine[0].prev_hash == audit_chain.GENESIS and mine[1].prev_hash == mine[0].hash
        assert db.query(AuditChainEntry).filter_by(tenant_id=other).one().seq == 1  # each tenant its own chain

        result = audit_chain.verify(db, tid)
        assert result["ok"] and result["entries"] == 2 and result["last_hash"] == mine[1].hash
    finally:
        db.close()


def test_verify_finds_a_changed_record_and_a_changed_entry(client):
    db, tid, _ = _setup(client)
    try:
        a = _run(db, tid, "approve 10% discount")
        b = _run(db, tid, "second")
        db.commit()
        audit_chain.extend(db)

        a.raw_text = "approve 50% discount"  # someone rewrites history in the log
        db.commit()
        result = audit_chain.verify(db, tid)
        assert not result["ok"]
        assert result["problems"] == [{"seq": 1, "source": "intent_execution_log", "source_id": a.id,
                                       "problem": "the record was changed after it was chained"}]

        a.raw_text = "approve 10% discount"
        entry = db.query(AuditChainEntry).filter_by(tenant_id=tid, seq=2).one()
        entry.record_sha256 = "f" * 64
        db.commit()  # SQLite has no trigger; on Postgres this UPDATE is refused outright
        problems = {p["problem"] for p in audit_chain.verify(db, tid)["problems"]}
        assert "the entry was changed" in problems and "the record was changed after it was chained" in problems

        db.delete(entry)
        db.commit()
        db.delete(b)
        db.commit()
        assert audit_chain.verify(db, tid)["ok"]  # the last entry gone with its record: only the hash it left behind shows it
    finally:
        db.close()


def test_verify_finds_a_removed_entry_in_the_middle(client):
    db, tid, _ = _setup(client)
    try:
        for i in range(3):
            _run(db, tid, f"run {i}")
        db.commit()
        audit_chain.extend(db)
        db.delete(db.query(AuditChainEntry).filter_by(tenant_id=tid, seq=2).one())
        db.commit()
        problems = audit_chain.verify(db, tid)["problems"]
        assert {"seq": 3, "problem": "entry 2 is missing"}.items() <= problems[0].items()
        assert any(p["problem"] == "does not follow the entry before it" for p in problems)
    finally:
        db.close()


def test_admins_can_check_their_chain(client):
    resp = client.get("/api/admin/audit-chain/verify")
    assert resp.status_code == 200 and resp.json()["ok"] is True and resp.json()["entries"] == 0

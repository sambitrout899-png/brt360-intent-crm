"""Tenant-local, versioned process recipes stored in the existing audit store.

Only successfully verified builds are remembered. Recall supplies design input,
never executable configuration; the builder validates it against today's setup.
"""
import json

from app.models.intent_log import IntentExecutionLog

KIND = "PROCESS-RECIPE"


def recipes(db, tenant_id):
    rows = db.query(IntentExecutionLog).filter(
        IntentExecutionLog.tenant_id == tenant_id, IntentExecutionLog.intent_type == KIND,
        IntentExecutionLog.status == "executed").order_by(IntentExecutionLog.id.desc()).limit(50).all()
    result = []
    for row in rows:
        try:
            value = json.loads(row.context_json)
            if value.get("version") == 1 and isinstance(value.get("steps"), list):
                result.append({**value, "id": row.id})
        except (ValueError, TypeError, AttributeError):
            continue
    return result


def recall(db, tenant_id, request):
    normalized = " ".join(request.lower().split())
    return next((r for r in recipes(db, tenant_id)
                 if " ".join(r["request"].lower().split()) == normalized), None)


def remember(db, tenant_id, blueprint, answers):
    from app.data_intelligence import audit
    log = audit.start_execution(db, tenant_id, blueprint["request"], channel="process_memory")
    log.intent_type = KIND
    audit.record_context(log, {"version": 1, "name": blueprint["name"], "request": blueprint["request"],
                               "steps": blueprint["steps"], "answers": answers or {},
                               "graphs": {c["id"]: c["spec"]["graph"] for c in blueprint["components"] if c["kind"] == "flow"}})
    audit.complete(log, "executed")
    db.commit()
    return log.id

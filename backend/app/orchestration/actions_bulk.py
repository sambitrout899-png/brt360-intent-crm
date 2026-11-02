"""Bulk ordering intents INT-149 / INT-150 (orchestration/bulk_orders.py does the work).

INT-149 only creates and starts the request - its rows then run one by one as intents of their own
(each with its own audit record, approvals and flows), moved along by the scheduler or "process now";
running them inside this intent's transaction would make one row's failure undo the others."""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.orchestration import bulk_orders as bulk
from app.orchestration.orchestrator import SagaStep


def _actor(db: Session, entities: dict[str, Any]):
    from app.crm_core import access_service
    from app.models.auth import User

    user = db.get(User, int(entities["_actor_user_id"])) if entities.get("_actor_user_id") else None
    if user is None:
        raise ValueError("A bulk order is started by a signed-in person - its rows run as them")
    return user, access_service.resolve(db, user)


def build_int149_steps(db: Session, tenant_id: int, execution_id: int, e: dict[str, Any]):
    def step() -> dict[str, Any]:
        user, access = _actor(db, e)
        rows = e.get("bulk_rows")
        data = {"process": e.get("bulk_process"), "name": e.get("bulk_name") or "", "defaults": bulk._pairs(e.get("bulk_defaults")),
                "auto_approve": str(e.get("auto_approve", True)).lower() not in ("false", "0", "no"),
                "send_to_fulfillment": str(e.get("send_to_fulfillment", True)).lower() not in ("false", "0", "no"), "source": "intent"}
        if isinstance(rows, list):
            data["rows"] = rows
        else:
            data["csv"] = str(rows or "")
        made = bulk.create(db, tenant_id, user, access, data)
        out = bulk.summary(db, made)
        invalid = (made.counts or {}).get("invalid", 0)
        out["summary"] = (f"{made.number}: {out['rows']} row(s), {invalid} invalid - "
                          + ("running: each row is placed, approved and fulfilled as a single order would be" if made.status == "running"
                             else "nothing to run"))
        return out
    return [SagaStep("start_bulk_order", step)]


def build_int150_steps(db: Session, tenant_id: int, execution_id: int, e: dict[str, Any]):
    def step() -> dict[str, Any]:
        found = bulk.of(db, tenant_id, e.get("bulk_order"))
        out = bulk.summary(db, found)
        counts = ", ".join(f"{v} {k.replace('_', ' ')}" for k, v in sorted(out["counts"].items()))
        out["summary"] = f"{found.number} is {found.status.replace('_', ' ')}: {counts}"
        out["note"] = "Rows move along every minute - or with 'process now' on Orders -> Bulk orders"
        return out
    return [SagaStep("bulk_order_status", step)]


BUILDERS = {"INT-149": build_int149_steps, "INT-150": build_int150_steps}

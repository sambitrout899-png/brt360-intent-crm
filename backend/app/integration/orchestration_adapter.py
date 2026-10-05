"""Internal adapter representing the handoff to an external Order
Orchestration system (TMF640 Service Activation & Configuration /
TMF641 Service Ordering territory) - in a real deployment, actions_o2a.py
would submit the order HERE and an external orchestrator would drive the
actual network/field provisioning asynchronously, calling back when done.
Today this platform's own saga does that work directly (order_adapter +
ticket_adapter); this adapter is the seam that work would move behind if
a real Order Orchestration product were introduced, without
actions_o2a.py's callers needing to change. Deterministic stand-in:
returns a fabricated orchestration reference, synchronously "confirming"
submission.
"""

from __future__ import annotations

import uuid


def submit_order_for_orchestration(order_id: int) -> str:
    return f"ORCH-{uuid.uuid4().hex[:10].upper()}"

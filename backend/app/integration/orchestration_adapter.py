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
from typing import Any


def submit_order_for_orchestration(order_id: int) -> str:
    return f"ORCH-{uuid.uuid4().hex[:10].upper()}"


def decompose_order(order_id: int) -> dict[str, Any]:
    """Phase 5: what a real downstream Order Orchestration system would
    hand back after decomposing an approved Order - which work orders to
    raise, in what sequence. This deterministic stand-in always returns
    the same fixed installation -> connectivity_test -> acceptance_letter
    chain (actions_fulfillment.py creates each one in turn as the
    previous closes, rather than all at once, matching how a real
    orchestrator would issue them progressively) - but the seam exists
    here so a real system's actual decomposition plan slots in without
    actions_fulfillment.py's callers changing.
    """
    return {
        "orchestration_ref": f"ORCH-{uuid.uuid4().hex[:10].upper()}",
        "work_order_sequence": ["installation", "connectivity_test", "acceptance_letter"],
    }

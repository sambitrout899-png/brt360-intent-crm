"""Internal adapter representing the actual "command to network" that
activates a connection at the core-network level (TMF639 Resource
Activation territory) - distinct from provisioning_adapter.py (which
confirms a network element was *configured*) and orchestration_adapter.py
(the order-workflow handoff): this is the final activation trigger,
fired when the Installation work order (Phase 5 fulfillment chain)
closes. Deterministic stand-in: confirms activation synchronously with a
fabricated reference.
"""

from __future__ import annotations

import uuid


def activate_connection(order_item_id: int) -> str:
    return f"NETACT-{uuid.uuid4().hex[:10].upper()}"

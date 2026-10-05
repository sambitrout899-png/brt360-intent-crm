"""Internal adapter representing an external network Provisioning system
(TMF639 Resource Inventory / the network-element-configuration layer
underneath Order Orchestration) - distinct from orchestration_adapter.py,
which represents the order-workflow handoff; this one represents the
actual "configure the network element" confirmation a real deployment
would receive asynchronously. Deterministic stand-in: confirms
provisioning synchronously with a fabricated reference, matching the
same "swap this module's internals, no upstream caller changes"
convention as every other adapter here.
"""

from __future__ import annotations

import uuid


def confirm_provisioning(service_order_id: int) -> str:
    return f"PROV-{uuid.uuid4().hex[:10].upper()}"

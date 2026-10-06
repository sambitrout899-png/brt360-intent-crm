"""Internal adapter representing the external Billing system being told
to start billing a service account for real (TMF678 Billing territory) -
fired when the Acceptance Letter work order (Phase 5 fulfillment chain)
closes, distinct from order_adapter.apply_billing_adjustment (ongoing
proration against an already-billing account) and
order_adapter.set_billing_account_status (this platform's own
BillingAccount record). Deterministic stand-in: confirms billing
initiation synchronously with a fabricated reference.
"""

from __future__ import annotations

import uuid


def initiate_billing(service_account_id: int) -> str:
    return f"BILL-{uuid.uuid4().hex[:10].upper()}"

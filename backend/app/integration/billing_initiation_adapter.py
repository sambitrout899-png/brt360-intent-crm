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


# Customer order management registers the billing hierarchy before anything is
# provisioned (eTOM Billing & Revenue Management - TMF666 Account Management):
# customer -> billing account -> billing profile. Each returns the billing
# system's reference for the record. Deterministic stand-ins.


def register_customer(party_id: int, name: str) -> str:
    return f"BCUST-{party_id:06d}"


def register_billing_account(billing_account_id: int, customer_ref: str) -> str:
    return f"BACCT-{billing_account_id:06d}"


def register_billing_profile(billing_profile_id: int, billing_account_ref: str) -> str:
    return f"BPROF-{billing_profile_id:06d}"

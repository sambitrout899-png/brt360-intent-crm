"""Internal adapter shaped like an ERP system touch point (customer
master data / financial account sync - billing's system of record for
revenue recognition in most real telecom deployments, as distinct from
this platform's own BillingAccount which is operational, not financial).
Deterministic stand-in: returns a fabricated ERP customer reference; a
real deployment swaps this for an actual ERP API call.
"""

from __future__ import annotations

import uuid


def sync_customer(party_name: str) -> str:
    return f"ERP-CUST-{uuid.uuid4().hex[:8].upper()}"

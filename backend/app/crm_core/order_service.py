"""Order decomposition / provisioning / billing logic (INT-07/08/09) -
HLD Layer 3. Network/SIM/IP allocation are deterministic stand-ins for the
real OSM/UIM and mobile-core calls the Integration layer would make against
an actual vendor system - see order_adapter.py for the TMF-shaped seam.
"""

from __future__ import annotations

import uuid

NOTICE_PERIOD_DAYS = 30
EARLY_TERMINATION_FEE_RATE = 0.5  # fraction of remaining contract value


def decompose_tasks(composition: str) -> list[str]:
    """Which provisioning tasks an order needs, based on its bundle composition."""
    tasks = ["billing"]
    lower = composition.lower()
    if "fiber" in lower:
        tasks.insert(0, "network")
        tasks.append("field_service")  # physical connectivity install
    if "mobile" in lower:
        tasks.append("mobile_core")
    if "voip" in lower:
        tasks.append("network")
    # de-dup, preserve order
    seen = set()
    return [t for t in tasks if not (t in seen or seen.add(t))]


def reserve_network_path() -> str:
    return f"PATH-{uuid.uuid4().hex[:8].upper()}"


def allocate_sim_profile() -> str:
    return f"SIM-{uuid.uuid4().hex[:10].upper()}"


def allocate_ip_block() -> str:
    return f"10.{uuid.uuid4().int % 256}.{(uuid.uuid4().int >> 8) % 256}.0/28"


def calculate_early_termination_fee(monthly_price: float, contract_months: int, months_elapsed: int) -> float:
    months_remaining = max(0, contract_months - months_elapsed)
    return round(monthly_price * months_remaining * EARLY_TERMINATION_FEE_RATE, 2)

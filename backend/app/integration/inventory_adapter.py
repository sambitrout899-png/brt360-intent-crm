"""Internal adapter shaped like a Telecom Inventory Management system
(TMF639 Resource Inventory) - the thing a real deployment would call to
check fiber route capacity, port availability and switch capacity before
committing to a quote. Deterministic stand-in, same convention as
order_service's network/SIM/IP functions: a region/address containing
"NOCOVERAGE" has no fiber route; "NOPORTS"/"NOSWITCH" simulate exhausted
capacity. Everything else is available. Swapping this module's internals
for a real OSS/inventory system call is the only change a real deployment
needs - nothing upstream (feasibility_service) touches inventory data
directly.
"""

from __future__ import annotations


def check_resource_availability(resource_type: str, locator: str) -> tuple[bool, str]:
    """Returns (available, detail). locator is whatever identifies the
    thing being checked for this resource_type - an address for "fiber",
    a site/exchange code for "port"/"switch", a SKU for "inventory".
    """
    upper = (locator or "").upper()
    if resource_type == "fiber" and "NOCOVERAGE" in upper:
        return False, f"No fiber route found serving '{locator}'."
    if resource_type == "port" and "NOPORTS" in upper:
        return False, f"No free ports at '{locator}'."
    if resource_type == "switch" and "NOSWITCH" in upper:
        return False, f"Switch capacity exhausted at '{locator}'."
    if resource_type == "inventory" and "OUTOFSTOCK" in upper:
        return False, f"No inventory on hand for '{locator}'."
    return True, f"{resource_type.capitalize()} available at '{locator}'."

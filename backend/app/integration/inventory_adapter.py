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

import uuid


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


# Resource types a technical action can ask for - logical (numbers, addresses, ids the
# network assigns) and physical (equipment, ports, fibre the field touches).
LOGICAL_RESOURCES = ("service_id", "circuit_id", "ip_block", "ipv6_prefix", "vlan", "phone_number", "sip_trunk")
PHYSICAL_RESOURCES = ("port", "splitter_port", "fibre_strand", "ont", "cpe", "router", "sim")
TECHNICAL_ACTIONS = ("design", "assign", "reserve", "activate", "release")


def _identifier(resource_type: str, seed: int) -> str:
    n = seed % 250 + 2
    return {
        "service_id": f"SVC-{seed:06d}", "circuit_id": f"CKT/{seed:05d}/FTTH", "ip_block": f"10.{seed % 200}.{n}.0/29",
        "ipv6_prefix": f"2001:db8:{seed % 65535:x}::/56", "vlan": str(100 + seed % 3900), "phone_number": f"+91 80 {4000_0000 + seed % 9999_999}",
        "sip_trunk": f"SIP-TRK-{seed:05d}", "port": f"OLT-{1 + seed % 8:02d}/1/{1 + seed % 16}/{1 + seed % 64}",
        "splitter_port": f"SPL-{seed % 40:02d}:{1 + seed % 32}", "fibre_strand": f"FDB-{seed % 90:02d}/strand-{1 + seed % 24}",
        "ont": f"HWTC{seed:08X}", "cpe": f"CPE-{seed:08d}", "router": f"RTR-{seed:07d}", "sim": f"8991{seed:015d}",
    }.get(resource_type, f"{resource_type.upper()}-{seed:06d}")


def technical_action(action: str, resources: list[str], context: dict | None = None, db=None, tenant_id: int | None = None) -> dict:
    """A service-order technical action to the inventory system (TMF640 / TMF639):
    design the service, assign logical resources, reserve physical ones, activate,
    release. Returns {"request_id", "resources": [{"type", "kind", "identifier",
    "status"}]}.

    When the tenant keeps its inventory in BRT 360 (Inventory: locations, equipment or
    pools recorded), the resource types it keeps are allocated there for real
    (crm_core/inventory_service.technical_action - the item, its location, the order
    it is held for) and only the types it doesn't keep come from the stand-in below.

    Deterministic stand-in, same conventions as the checks above: an address with
    NOCOVERAGE can't be designed, NOPORTS has no free port and OUTOFSTOCK no
    equipment. A real inventory's answer comes back the same way - synchronously
    here, or later through the orchestration callback."""
    context = context or {}
    if action not in TECHNICAL_ACTIONS:
        raise ValueError(f"Unknown technical action '{action}' - use {', '.join(TECHNICAL_ACTIONS)}")
    if db is not None and tenant_id is not None:
        from app.crm_core import inventory_service

        if inventory_service.in_use(db, tenant_id):
            kept = inventory_service.technical_action(db, tenant_id, action, list(resources), context)
            rest = _stand_in(action, kept["unmanaged"], context) if kept["unmanaged"] else {"resources": []}
            return {"request_id": f"INVREQ-{uuid.uuid4().hex[:10].upper()}", "action": action, "resources": kept["resources"] + rest["resources"],
                    "location": kept["location"], "system": "BRT 360 Inventory"}
    return _stand_in(action, resources, context)


def _stand_in(action: str, resources: list[str], context: dict) -> dict:
    address = str(context.get("address") or "").upper()
    seed = int(context.get("order_id") or 0) * 97 + int(context.get("order_item_id") or 0)
    if action == "design" and "NOCOVERAGE" in address:
        raise ValueError(f"Inventory: no network route can serve '{context.get('address')}'")
    out = []
    for i, rtype in enumerate(r.strip() for r in resources if r and r.strip()):
        kind = "logical" if rtype in LOGICAL_RESOURCES else "physical" if rtype in PHYSICAL_RESOURCES else "logical"
        if rtype in ("port", "splitter_port") and "NOPORTS" in address:
            raise ValueError(f"Inventory: no free {rtype.replace('_', ' ')} at '{context.get('address')}'")
        if rtype in ("ont", "cpe", "router", "sim") and "OUTOFSTOCK" in address:
            raise ValueError(f"Inventory: no {rtype.upper()} in stock for '{context.get('address')}'")
        status = {"design": "designed", "assign": "assigned", "reserve": "reserved", "activate": "active", "release": "released"}[action]
        out.append({"type": rtype, "kind": kind, "identifier": _identifier(rtype, seed + i * 7919), "status": status})
    return {"request_id": f"INVREQ-{uuid.uuid4().hex[:10].upper()}", "action": action, "resources": out}


def sync_asset_to_inventory(asset_type: str, identifier: str, attributes: dict | None = None) -> str:
    """Phase 5: pushes a newly-created Asset (app/models/fulfillment.py)
    to the primary external inventory/CMDB system - this platform keeps
    its own lightweight copy (the Asset row) for CRM-side querying, same
    relationship a ServiceNow CRM app has to ServiceNow's own CMDB, but
    the external system stays the system of record for physical/logical
    inventory. Deterministic stand-in: confirms the sync synchronously
    with a fabricated reference. attributes: the product's technical
    attributes (VLAN, CPE, IP block ...) the inventory record carries.
    """
    return f"INV-{uuid.uuid4().hex[:10].upper()}"

"""Inventory intents (crm_core/inventory_service.py and inventory_ai.py do the work;
orchestration/actions_inventory.py builds the steps).

  INT-120 Find inventory - a question in plain English ("free GPON ports at BLR-EX-01")
  INT-121 Check network capacity - equipment and pools, overall or at a location
  INT-122 Receive equipment - N items of a type into a store / site
  INT-123 Reserve inventory for an order - outside a fulfillment pattern
  INT-124 Release an order's inventory - everything it holds goes back
  INT-125 Change an item's status - lifecycle (maintenance, retired ...) or operational (down, up)
  INT-126 Analyse impact - what an item takes down with it: services, customers, orders
  INT-127 Create a resource pool - VLAN / number ranges, IP blocks, id sequences
  INT-128 Design an item type - described in plain English, drafted by the tenant's model

Recognised by phrase rules before any model runs, like the other later intents.
"""
from __future__ import annotations

import re
from typing import Any

CODES = ("INT-120", "INT-121", "INT-122", "INT-123", "INT-124", "INT-125", "INT-126", "INT-127", "INT-128")

# code -> (label, description, category, icon, target, fields, examples)
SPECS: dict[str, tuple[str, str, str, str, str | None, list[str], list[str]]] = {
    "INT-120": ("Find inventory", "Ask the network inventory in plain English - what is free, reserved or down, where, for which order or "
                "customer. AI (or the built-in reader) turns it into a search.", "orders", "🔎", None, ["question"],
                ["Free GPON ports at BLR-EX-01", "Which ONTs are in stock in the Bangalore store?"]),
    "INT-121": ("Check network capacity", "How much is free, reserved and in use - ports, equipment in stock, VLANs, addresses and numbers - "
                "overall or at a location.", "orders", "📊", None, ["location"],
                ["Check capacity at BLR-EX-01", "How full is the network in Bangalore?"]),
    "INT-122": ("Receive equipment", "Record new equipment arriving at a store or site - ONTs, CPEs, routers, SIMs - named by the item "
                "type's naming rule, ready to be reserved for orders.", "orders", "📦", None, ["item_type", "quantity", "location", "vendor", "model"],
                ["Receive 20 ONTs into BLR-STORE", "Add 5 business routers to the Bangalore store"]),
    "INT-123": ("Reserve inventory", "Hold ports, equipment and logical resources for a deal (Presales' feasibility) or an order - from "
                "the location serving its site, nearest first. A deal's hold moves to the order created from it.", "orders", "📌", None,
                ["opportunity_id", "order_id", "resources"],
                ["Reserve inventory for opportunity 7", "Reserve a port, an ONT and a VLAN for order 12"]),
    "INT-124": ("Release inventory", "Everything a deal or an order holds goes back: ports and equipment become available, VLANs, "
                "addresses and numbers return to their pools.", "orders", "↩️", None, ["opportunity_id", "order_id", "note"],
                ["Release the inventory of order 12 - order cancelled", "Release the inventory held for opportunity 7"]),
    "INT-125": ("Change an inventory item's status", "Move an item along its lifecycle (available, maintenance, retired ...) or record "
                "its operational status (up, down, degraded). Maintenance on something services use needs a reason.", "orders", "🔧", None,
                ["item", "lifecycle", "oper_status", "note"],
                ["Put BLR-EX-01-OLT-01 into maintenance - firmware upgrade", "Mark BLR-SPL-01 down"]),
    "INT-126": ("Analyse impact", "What an item takes down with it: what it holds, what depends on it or connects through it, and the "
                "services, customers and orders on any of that.", "orders", "💥", None, ["item"],
                ["What is affected if BLR-EX-01-OLT-01 goes down?", "Impact of BLR-CAB-17"]),
    "INT-127": ("Create a resource pool", "A range logical resources are handed out from - VLANs, phone numbers, IP blocks split into "
                "fixed-size subnets, circuit or service id sequences - for one location or shared.", "workspace", "🧮", None,
                ["pool_type_code", "range", "block_size", "location", "pool_name"],
                ["Create a VLAN pool 1000-1999 at BLR-EX-02", "Create an IP pool 10.30.0.0/16 in /29 blocks"]),
    "INT-128": ("Design an inventory item type", "Describe a kind of equipment, place or logical resource in plain English - what it is, "
                "where it sits, what it records - and AI drafts the item type. Saved to the Inventory schema.", "workspace", "🧩", None,
                ["type_description"],
                ["Design an item type: a DSLAM is a network device in an exchange or cabinet with a port count (required) and firmware"]),
}
LABELS: dict[str, str] = {code: spec[0] for code, spec in SPECS.items()}
LIB: dict[str, dict[str, Any]] = {
    "question": {"label": "What are you looking for?", "type": "textarea", "placeholder": "e.g. free GPON ports at BLR-EX-01"},
    "location": {"label": "Location", "help": "A location's identifier or name (e.g. BLR-EX-01) - blank = everywhere"},
    "item_type": {"label": "Item type", "help": "e.g. ont, cpe, router, sim"},
    "quantity": {"label": "Quantity", "type": "number"},
    "vendor": {"label": "Vendor"},
    "model": {"label": "Model"},
    "resources": {"label": "Resources", "type": "list", "placeholder": "port, ont, vlan, ip_block",
                  "help": "Resource types to reserve - the ones Inventory keeps (Inventory -> Item types)"},
    "item": {"label": "Item", "help": "Its identifier, name or BRT_ID (e.g. BLR-EX-01-OLT-01)"},
    "lifecycle": {"label": "Lifecycle", "type": "picklist", "values": ["available", "reserved", "in_use", "maintenance", "retired", "planned"]},
    "oper_status": {"label": "Operational status", "type": "picklist", "values": ["up", "down", "degraded", "unknown"]},
    "pool_type_code": {"label": "Hands out", "type": "picklist", "values": ["vlan", "ip_block", "ipv6_prefix", "phone_number", "circuit_id", "service_id", "sip_trunk"]},
    "range": {"label": "Range", "placeholder": "1000-1999, or a network like 10.30.0.0/16"},
    "block_size": {"label": "Block size (IP prefix length)", "type": "number", "help": "e.g. 29 hands out /29s"},
    "pool_name": {"label": "Pool name"},
    "type_description": {"label": "The item type", "type": "textarea",
                         "placeholder": "e.g. A DSLAM is a network device in an exchange or cabinet with a management IP, a port count (required) and firmware",
                         "help": "What it is, where it sits, what it records - AI drafts it"},
}
OVERRIDES: dict[tuple[str, str], dict[str, Any]] = {
    ("INT-124", "note"): {"label": "Why"},
    ("INT-125", "note"): {"label": "Why", "help": "Needed for maintenance on something services use"},
}
REQUIRED: dict[str, list[str]] = {
    "INT-120": ["question"], "INT-121": [], "INT-122": ["item_type", "quantity", "location"], "INT-123": [],
    "INT-124": [], "INT-125": ["item"], "INT-126": ["item"], "INT-127": ["pool_type_code", "range"], "INT-128": ["type_description"],
}


def missing(code: str, entities: dict[str, Any]) -> list[str]:
    if code in ("INT-123", "INT-124") and not (entities.get("order_id") or entities.get("opportunity_id")):
        return ["opportunity_id"]  # one of the two - a deal (Presales) or an order
    if code == "INT-123" and entities.get("order_id") and not entities.get("resources"):
        return ["resources"]
    if code == "INT-125" and not (entities.get("lifecycle") or entities.get("oper_status")):
        return ["lifecycle"]
    return []


_ITEM = r"(?:['\"“][^'\"”]+['\"”]|[A-Za-z][\w]*(?:[-/:.][\w]+)*[-/:][\w/:.-]*\d[\w/:.-]*)"
_THINGS = (r"(?:ports?|onts?|cpes?|routers?|vlans?|ip\s*blocks?|ipv6|phone\s+numbers|splitters?|olts?|switch(?:es)?|devices?|equipment|"
           r"sims?|line\s+cards?|fibre\s+strands?|fiber\s+strands?|cabinets?|exchanges?|stock|inventory)")
RULES: list[tuple[str, re.Pattern]] = [
    ("INT-128", re.compile(r"\b(?:design|define|create|add|model|draft)\w*\s+(?:an?\s+|the\s+)?(?:new\s+)?(?:inventory\s+)?(?:item|equipment|resource)\s+type\b"
                           r"|\bnew\s+(?:kind|type)\s+of\s+(?:equipment|device|inventory\s+item)\b", re.I)),
    ("INT-127", re.compile(r"\b(?:create|add|set\s*up|define|open|make)\w*\b[^.]{0,30}\b(?:vlans?|ip|ipv6|number|phone\s+number|circuit(?:\s+id)?|"
                           r"service\s+id|sip\s+trunk)s?\s+(?:pool|range)\b|\b(?:create|add|set\s*up|define)\w*\s+(?:an?\s+)?(?:resource\s+)?pool\b", re.I)),
    ("INT-126", re.compile(r"\bimpact\s+(?:of|analysis|on)\b|\bwhat\s+(?:is|would\s+be|gets)\s+affected\b|\bwho\s+(?:is|would\s+be)\s+affected\b"
                           rf"|\bwhat\s+(?:breaks|goes\s+down)\b|\bif\s+{_ITEM}\s+(?:goes|went|is|were)\s+(?:down|offline)\b", re.I)),
    ("INT-124", re.compile(r"\b(?:release|free\s+up|give\s+back|return|unreserve)\w*\b[^.]{0,40}\b(?:inventory|resources?|ports?|equipment|vlans?|ips?|numbers?)\b"
                           r"[^.]{0,30}\b(?:order|opportunity|opp|deal)\s*#?\s*\d+|\brelease\s+order\s*#?\s*\d+'?s?\s+(?:inventory|resources)", re.I)),
    ("INT-123", re.compile(rf"\b(?:reserve|allocate|hold|book)\w*\b[^.]{{0,60}}\b{_THINGS}[^.]{{0,40}}\bfor\s+(?:order|opportunity|opp|deal)\s*#?\s*\d+", re.I)),
    # stock arriving at a place - never "add 2 x Router CPE to quote 1" (a quote line) or numbers to a list
    ("INT-122", re.compile(r"\b(?:receive|received|add|stock|load|register|put|record)\w*\s+\d{1,3}\s+(?!x\b)(?:new\s+)?(?:[A-Za-z][\w-]*\s+){0,2}?"
                           r"(?:onts?|cpes?|routers?|sims?|splitters?|switch(?:es)?|line\s+cards?|cards?|olts?|devices?|units?|pieces?)\s+"
                           r"(?:in|into|to|at)\s+(?!(?:the\s+|this\s+|a\s+)?(?:quote|order|basket|cart|proposal|agreement|opportunity|deal)\b)"
                           r"(?![^.]*\blist\b)", re.I)),
    ("INT-125", re.compile(rf"\b(?:put|move|set|mark|take|bring)\w*\s+(?:the\s+)?{_ITEM}\s+(?:into|in|to|as|back\s+(?:in|into|to)|out\s+of)?\s*"
                           r"(?:maintenance|service|retired|available|planned|down|up|degraded|operational)\b"
                           rf"|\b(?:retire|decommission)\w*\s+(?:the\s+)?{_ITEM}", re.I)),
    ("INT-121", re.compile(r"\b(?:capacity|utili[sz]ation|headroom|how\s+full|spare\s+capacity)\b[^.]{0,60}"
                           r"|\bhow\s+many\s+(?:free|spare|available)\b", re.I)),
    ("INT-120", re.compile(rf"\b(?:find|show|list|search|which|what|where|any)\b[^.]{{0,60}}\b{_THINGS}\b(?![^.]{{0,20}}\bfor\s+order\b[^.]*\breserve)"
                           rf"|\b(?:free|available|spare|reserved|in\s+stock)\s+(?:\w+\s+){{0,2}}{_THINGS}\b|\binventory\s+(?:of|for)\s+order\s*#?\s*\d+", re.I)),
]

_RESOURCE_WORDS = [(r"\bsplitter\s+ports?\b", "splitter_port"), (r"(?<!splitter )\bports?\b", "port"), (r"\bonts?\b", "ont"), (r"\bcpes?\b", "cpe"),
                   (r"\brouters?\b", "router"), (r"\bsims?\b", "sim"), (r"\bvlans?\b", "vlan"), (r"\bipv6\b", "ipv6_prefix"),
                   (r"\bip(?:v4)?\s*(?:block|address|range)?s?\b(?!v6)", "ip_block"), (r"\bphone\s+numbers?\b|\bnumbers?\b", "phone_number"),
                   (r"\bfib(?:re|er)\s+strands?\b", "fibre_strand"), (r"\bcircuit(?:\s+ids?)?\b", "circuit_id"), (r"\bsip\s+trunks?\b", "sip_trunk")]


def _item(text: str) -> str | None:
    if m := re.search(r"['\"“]([^'\"”]+)['\"”]", text):
        return m.group(1).strip()
    if m := re.search(r"\bitem\s+#?(\d+)\b", text, re.I):
        return m.group(1)
    candidates = re.findall(r"\b[A-Za-z][\w]*(?:[-/:.][\w]+)*[-/:][\w/:.-]*\d[\w/:.-]*", text)
    return max(candidates, key=len).rstrip(".:,") if candidates else None


def _location(text: str) -> str | None:
    if m := re.search(rf"\b(?:at|in|into|to|for|near)\s+(?:the\s+)?({_ITEM})", text, re.I):
        return m.group(1).strip("'\"“”")
    if m := re.search(r"\b(?:at|in|into|to|near)\s+(?:the\s+)?([A-Z][\w]+(?:\s+[A-Z][\w]+){0,3})(?:\s+(?:store|exchange|warehouse|cabinet))?", text):
        return m.group(0).split(None, 1)[1].removeprefix("the ").strip()
    return None


def extract(code: str, text: str, fields: dict[str, Any], loose: dict[str, Any]) -> dict[str, Any]:
    e: dict[str, Any] = {k: fields[k] for k in SPECS[code][5] if fields.get(k) not in (None, "")}
    lowered = text.lower()
    if "opportunity_id" in SPECS[code][5] and "opportunity_id" not in e and "order_id" not in e:
        if m := re.search(r"\b(?:opportunity|opp|deal)\s*#?\s*(\d+)\b", text, re.I):
            e["opportunity_id"] = int(m.group(1))
        elif loose.get("opportunity_id") not in (None, "") and not re.search(r"\border\s*#?\s*\d", text, re.I):
            e["opportunity_id"] = loose["opportunity_id"]
    if "order_id" in SPECS[code][5] and "order_id" not in e and "opportunity_id" not in e:
        if loose.get("order_id") not in (None, ""):
            e["order_id"] = loose["order_id"]
        elif m := re.search(r"\border\s*#?\s*(\d+)\b", text, re.I):
            e["order_id"] = int(m.group(1))
    if code == "INT-120":
        e.setdefault("question", text.strip())
    elif code == "INT-121":
        if "location" not in e and (loc := _location(text)):
            e["location"] = loc
    elif code == "INT-122":
        if m := re.search(r"\b(\d+)\s+(?:new\s+)?(?:([A-Z][\w-]+)\s+)?([a-z][\w -]*?)\s+(?:in|into|to|at)\s+(?:the\s+)?(.+?)\s*(?:[.,;]|$)", text, re.I):
            e.setdefault("quantity", int(m.group(1)))
            if m.group(2) and m.group(2).lower() not in ("new", "more"):
                e.setdefault("vendor", m.group(2))
            e.setdefault("item_type", m.group(3).strip())
            e.setdefault("location", m.group(4).strip())
        if "model" not in e and (m := re.search(r"\bmodel\s+([\w-]+)", text, re.I)):
            e["model"] = m.group(1)
    elif code == "INT-123":
        if "resources" not in e:
            found = [r for pattern, r in _RESOURCE_WORDS if re.search(pattern, re.sub(r"\border\s*#?\s*\d+", "", lowered))]
            if found:
                e["resources"] = list(dict.fromkeys(found))
    elif code == "INT-124":
        if "note" not in e and (m := re.search(r"(?:\bbecause\b|\s[-–—]\s)\s*(.+)$", text, re.I)):
            e["note"] = m.group(1).strip(" .")[:300]
    elif code in ("INT-125", "INT-126"):
        if "item" not in e and (item := _item(text)):
            e["item"] = item
        if code == "INT-125":
            if "lifecycle" not in e and "oper_status" not in e:
                for pattern, key, value in ((r"\bmaintenance\b", "lifecycle", "maintenance"), (r"\b(?:retire|decommission|retired)\w*", "lifecycle", "retired"),
                                            (r"\bback\s+(?:in|into|to)\s+service\b|\bavailable\b", "lifecycle", "available"),
                                            (r"\bplanned\b", "lifecycle", "planned"), (r"\bdown\b|\boffline\b|\bfaulty\b", "oper_status", "down"),
                                            (r"\bdegraded\b", "oper_status", "degraded"), (r"\bup\b|\boperational\b", "oper_status", "up")):
                    if re.search(pattern, lowered):
                        e[key] = value
                        break
            if "note" not in e and (m := re.search(r"(?:\bbecause\b|\bfor\s+an?\b|\s[-–—]\s)\s*(.+)$", text, re.I)):
                e["note"] = m.group(1).strip(" .")[:300]
    elif code == "INT-127":
        if "pool_type_code" not in e:
            for pattern, value in ((r"\bvlan", "vlan"), (r"\bipv6\b", "ipv6_prefix"), (r"\bip\b|\bipv4\b|\d+\.\d+\.\d+\.\d+/", "ip_block"),
                                   (r"\bphone\s+number|\bnumber", "phone_number"), (r"\bcircuit", "circuit_id"), (r"\bservice\s+id", "service_id"),
                                   (r"\bsip\s+trunk", "sip_trunk")):
                if re.search(pattern, lowered):
                    e["pool_type_code"] = value
                    break
        if "range" not in e:
            if m := re.search(r"\b([0-9a-f:.]+/\d{1,3})\b", lowered):
                e["range"] = m.group(1)
            elif m := re.search(r"\b(\d+)\s*(?:-|–|to|through)\s*(\d+)\b", lowered):
                e["range"] = f"{m.group(1)}-{m.group(2)}"
        if "block_size" not in e and (m := re.search(r"(?:in\s+)?/(\d{1,3})\s*(?:blocks?|subnets?|prefixes|s)\b|\bblocks?\s+of\s+/(\d{1,3})", lowered)):
            e["block_size"] = int(m.group(1) or m.group(2))
        if "location" not in e and (m := re.search(rf"\b(?:at|for)\s+(?:the\s+)?({_ITEM})", text, re.I)):
            e["location"] = m.group(1).strip("'\"“”")
        if "pool_name" not in e and (m := re.search(r"\b(?:called|named)\s+['\"“]([^'\"”]+)['\"”]", text, re.I)):
            e["pool_name"] = m.group(1)
    elif code == "INT-128":
        body = re.sub(r"^\s*(?:please\s+)?(?:design|define|create|add|model|draft)\w*\s+(?:an?\s+|the\s+)?(?:new\s+)?(?:inventory\s+)?(?:item|equipment|resource)\s+type\s*[:,-]?\s*",
                      "", text, flags=re.I).strip()
        e.setdefault("type_description", body or text.strip())
    return e


NEXT: dict[str, list[tuple[str, str]]] = {
    "INT-120": [("INT-126", "Analyse the impact of one of them")],
    "INT-121": [("INT-122", "Receive more equipment"), ("INT-127", "Add a resource pool")],
    "INT-122": [("INT-121", "Check capacity")],
    "INT-123": [("INT-117", "Send the order to fulfillment")],
    "INT-124": [("INT-121", "Check capacity")],
    "INT-125": [("INT-126", "Analyse its impact")],
    "INT-126": [("INT-125", "Put it into maintenance")],
    "INT-127": [("INT-121", "Check capacity")],
    "INT-128": [("INT-122", "Receive equipment of the new type")],
}

TRAINING: list[tuple[str, list[tuple[str, dict[str, Any]]]]] = [
    ("INT-120", [("Free GPON ports at BLR-EX-01", {"question": "Free GPON ports at BLR-EX-01"}),
                 ("which ONTs are in stock in the Bangalore store", {"question": "which ONTs are in stock in the Bangalore store"})]),
    ("INT-121", [("Check capacity at BLR-EX-01", {"location": "BLR-EX-01"})]),
    ("INT-122", [("Receive {n} ONTs into BLR-STORE", {"quantity": "{n}", "item_type": "ONTs", "location": "BLR-STORE"})]),
    ("INT-123", [("Reserve a port and a VLAN for order {id}", {"order_id": "{id}", "resources": ["port", "vlan"]})]),
    ("INT-124", [("Release the inventory of order {id}", {"order_id": "{id}"})]),
    ("INT-125", [("Put BLR-EX-01-OLT-01 into maintenance - firmware upgrade", {"item": "BLR-EX-01-OLT-01", "lifecycle": "maintenance",
                                                                               "note": "firmware upgrade"})]),
    ("INT-126", [("What is affected if BLR-EX-01-OLT-01 goes down?", {"item": "BLR-EX-01-OLT-01"})]),
    ("INT-127", [("Create a VLAN pool 1000-1999 at BLR-EX-02", {"pool_type_code": "vlan", "range": "1000-1999", "location": "BLR-EX-02"})]),
    ("INT-128", [("Design an item type: a DSLAM is a network device in an exchange with a port count", {
        "type_description": "a DSLAM is a network device in an exchange with a port count"})]),
]

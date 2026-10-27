"""Fulfillment intents (orchestration/fulfillment_engine.py does the work).

  INT-117 Send an order to Fulfillment - the CRM order goes to COM, which
          decomposes it through SOM and TOM on the tenant's fulfillment pattern
          (Order Control approval normally does this on its own).
  INT-118 Resolve a fulfillment fallout - retry the failed step, skip it,
          or complete it by hand.
  INT-119 Design a fulfillment process - described in plain English, drafted
          by the tenant's model (or the built-in reader) into a fulfillment
          pattern, saved as a draft for review (orchestration/fulfillment_ai.py).

Recognised by phrase rules before any model runs, like the other later intents.
"""
from __future__ import annotations

import re
from typing import Any

CODES = ("INT-117", "INT-118", "INT-119")

# code -> (label, description, category, icon, target, fields, examples)
SPECS: dict[str, tuple[str, str, str, str, str | None, list[str], list[str]]] = {
    "INT-117": ("Send an order to Fulfillment", "Hand a CRM order to Fulfillment (COM) - it is decomposed into service (SOM) "
                "and technical (TOM) orders on the tenant's fulfillment pattern: billing registration, inventory, field work, then "
                "billing start and installed assets.", "orders", "🧭", "order", ["order_id", "fulfillment_pattern"],
                ["Send order 12 to fulfillment", "Submit order 12 to fulfillment on the Fixed broadband pattern"]),
    "INT-118": ("Resolve a fulfillment fallout", "An order stuck in fulfillment fallout: retry the failed step (after fixing the "
                "cause), skip it, or record it as done by hand.", "orders", "🩹", "order", ["order_id", "fallout_action", "step", "note"],
                ["Retry the fallout on order 12", "Skip the failed fulfillment step on order 12 - done by the partner"]),
    "INT-119": ("Design a fulfillment process", "Describe how orders are fulfilled in plain English - billing registration, inventory "
                "actions, field work orders, billing start - and AI drafts the COM / SOM / TOM fulfillment pattern, with the products "
                "and the intents that start it. Saved as a draft to review and activate under Admin -> Fulfillment.", "workspace", "🏗️",
                None, ["process_description", "pattern_name"],
                ["Design a fulfillment process for Fibre 500: register the customer in billing, reserve a port and an ONT, install, "
                 "test the connection, then start billing",
                 "Build a fulfillment pattern - when Upgrade bandwidth runs, assign a VLAN, activate the service and start billing"]),
}
LABELS: dict[str, str] = {code: spec[0] for code, spec in SPECS.items()}
LIB: dict[str, dict[str, Any]] = {
    "fulfillment_pattern": {"label": "Fulfillment pattern", "help": "Blank = the active pattern that fits the order (Admin -> Fulfillment)"},
    "fallout_action": {"label": "What to do", "type": "picklist", "values": ["retry", "skip", "complete"], "default": "retry",
                       "help": "retry after fixing the cause · skip the step · complete = it was done another way"},
    "step": {"label": "Step", "help": "Blank = the first failed step of the order"},
    "process_description": {"label": "The fulfillment process", "type": "textarea",
                            "placeholder": "e.g. For Fibre 500 business orders: register the customer, billing account and monthly billing "
                                           "profile; reserve a port and an ONT and assign a VLAN; install, then test the connection and get "
                                           "the acceptance signed; activate, start billing and create the installed assets.",
                            "help": "In your own words - which steps in billing, inventory and the field, in what order, for which "
                                    "products, and which intents start it. AI drafts it; you review it before it goes live."},
    "pattern_name": {"label": "Pattern name", "help": "Blank = named from what it fulfils"},
}
OVERRIDES: dict[tuple[str, str], dict[str, Any]] = {
    ("INT-118", "note"): {"label": "What was done / why"},
}
REQUIRED: dict[str, list[str]] = {"INT-117": ["order_id"], "INT-118": ["order_id"], "INT-119": ["process_description"]}


def missing(code: str, entities: dict[str, Any]) -> list[str]:
    return []


RULES: list[tuple[str, re.Pattern]] = [
    ("INT-119", re.compile(r"\b(?:design|build|create|draft|set\s*up|make|generate|define|model)\w*\b[^.:]{0,30}\bfulfil+ment\s+(?:process|pattern|flow|"
                           r"orchestration|plan|journey)\b|\bfulfil+ment\s+(?:process|pattern)\s+(?:for|that|which|where)\b", re.I)),
    ("INT-118", re.compile(r"\b(?:retry|re-?try|skip|resolve|fix|complete|clear)\w*\b[^.]{0,40}\b(?:fallout|(?:orchestration|fulfil+ment)\s+(?:step|task|error))\b", re.I)),
    ("INT-117", re.compile(r"\b(?:send|submit|route|release|hand|pass|push)\w*\b[^.]{0,40}\border\s*#?\s*\d+[^.]{0,40}\b(?:orchestration|fulfil+ment|com)\b"
                           r"|\borchestrate\s+order\s*#?\s*\d+", re.I)),
]


def extract(code: str, text: str, fields: dict[str, Any], loose: dict[str, Any]) -> dict[str, Any]:
    e: dict[str, Any] = {k: fields[k] for k in SPECS[code][5] if fields.get(k) not in (None, "")}
    if code == "INT-119":
        # the whole request is the description - the drafter reads the products, steps and triggers from it
        if "process_description" not in e and len(text.strip()) >= 10:
            e["process_description"] = text.strip()
        if "pattern_name" not in e and (m := re.search(r"\b(?:called|named)\s+['\"“]([^'\"”]{3,80})['\"”]", text, re.I)):
            e["pattern_name"] = m.group(1).strip()
        return e
    if "order_id" not in e:
        if loose.get("order_id") not in (None, ""):
            e["order_id"] = loose["order_id"]
        elif m := re.search(r"\border\s*#?\s*(\d+)\b", text, re.I):
            e["order_id"] = int(m.group(1))
    if code == "INT-117" and "fulfillment_pattern" not in e and (m := re.search(r"\b(?:on|using|with)\s+(?:the\s+)?(.+?)\s+pattern\b", text, re.I)):
        e["fulfillment_pattern"] = m.group(1).strip()
    if code == "INT-118":
        if "fallout_action" not in e:
            lowered = text.lower()
            e["fallout_action"] = "skip" if "skip" in lowered else "complete" if re.search(r"\b(?:complete|done by hand|manually)\b", lowered) else "retry"
        if "note" not in e and (m := re.search(r"(?:\bbecause\b|\s[-–—]\s)\s*(.+)$", text, re.I)):
            e["note"] = m.group(1).strip(" .")[:300]
    return e


NEXT: dict[str, list[tuple[str, str]]] = {
    "INT-117": [("INT-71", "Track the order")],
    "INT-118": [("INT-118", "Resolve the next fallout")],
    "INT-119": [("INT-117", "Send an order to it")],
}

TRAINING: list[tuple[str, list[tuple[str, dict[str, Any]]]]] = [
    ("INT-117", [
        ("Send order {id} to orchestration", {"order_id": "{id}"}),
        ("submit order {id} to fulfillment", {"order_id": "{id}"}),
    ]),
    ("INT-118", [
        ("Retry the fallout on order {id}", {"order_id": "{id}", "fallout_action": "retry"}),
        ("skip the failed orchestration step on order {id} - done by the partner", {"order_id": "{id}", "fallout_action": "skip",
                                                                                     "note": "done by the partner"}),
    ]),
    ("INT-119", [
        ("Design a fulfillment process: register the customer in billing, reserve a port, install and start billing",
         {"process_description": "Design a fulfillment process: register the customer in billing, reserve a port, install and start billing"}),
        ("build a fulfillment pattern for business orders - assign a VLAN, activate the service, then complete the order",
         {"process_description": "build a fulfillment pattern for business orders - assign a VLAN, activate the service, then complete the order"}),
    ]),
]

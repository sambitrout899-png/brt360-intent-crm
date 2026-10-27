"""Siebel asset-based ordering intents, beside Change an asset (INT-107) and
Confirm order fulfillment (INT-108) - crm_core/abo_service.py does the work.

  INT-110 Cancel an asset change  - stop a change order before it is fulfilled;
          the installed service stays exactly as it is.
  INT-111 Revise an asset change  - Siebel's Revise: a new revision of an
          in-flight change order (another quantity, speed, product, site or
          due date) replaces it.
  INT-112 Bulk asset change       - Siebel's bulk request: one change applied
          to many installed services, each its own change order.
  INT-113 Asset history           - the service's revisions and change orders,
          the assets it has been, what it was on a date, and what it will be
          once its open orders are fulfilled (the requested asset).
  INT-114 Quote an asset change   - the change priced as a quote first.
  INT-115 Order a change quote    - the quoted change becomes its change order.

Like the other intents added after the model was trained, they are recognised
by phrase rules before any model runs, so the trained model's prompt stays
unchanged; their training examples are appended last.
"""
from __future__ import annotations

import re
from typing import Any

from app.intent_engine.b2c_intents import _asset_change

CODES = ("INT-110", "INT-111", "INT-112", "INT-113", "INT-114", "INT-115")

_CHANGE_FIELDS = ["asset_operation", "offering_name", "quantity", "pricing_attributes", "technical_attributes", "requested_discount_pct",
                  "target_service_account_id", "target_site", "target_party_id", "port_number", "donor_operator", "recipient_operator",
                  "promotion", "waive_penalty", "due_date"]

# code -> (label, description, category, icon, target, fields, examples)
SPECS: dict[str, tuple[str, str, str, str, str | None, list[str], list[str]]] = {
    "INT-110": ("Cancel an asset change", "Cancel a change order before it is fulfilled - the installed service stays exactly as it is.",
                "orders", "🚫", "order", ["order_id", "asset_id", "reason"],
                ["Cancel change order 12", "Cancel the pending change on asset 4"]),
    "INT-111": ("Revise an asset change", "Revise a change order in flight (Siebel's Revise) - a new revision with another quantity, "
                "speed, product, site or due date replaces it.", "orders", "✏️", "order",
                ["order_id", "asset_id", *_CHANGE_FIELDS, "reason"],
                ["Revise change order 12 to 500 Mbps", "Amend the pending change on asset 4: quantity 3"]),
    "INT-112": ("Bulk asset change", "One change for many installed services (Siebel's bulk request) - each gets its own change order; "
                "one that can't change is reported and the rest go ahead.", "orders", "📦", "order",
                ["asset_ids", "party_id", "current_offering", *_CHANGE_FIELDS],
                ["Suspend assets 4, 5 and 6", "Upgrade all Home Fiber services of customer 7 to 500 Mbps"]),
    "INT-113": ("Asset history", "An installed service's revisions and change orders, the assets it has been, what it was on a date, "
                "and what it will be once its open orders are fulfilled.", "orders", "🕘", None, ["asset_id", "as_of"],
                ["History of asset 4", "What did asset 4 look like on 2026-01-31"]),
    "INT-114": ("Quote an asset change", "Price a change of an installed service as a quote first - its delta lines, the new monthly "
                "price and any penalty, valid for 30 days.", "orders", "🧾", "order", ["asset_id", "order_id", *_CHANGE_FIELDS],
                ["Quote an upgrade of asset 4 to 500 Mbps", "Quote relocating asset 4 to 5 MG Road, Bengaluru"]),
    "INT-115": ("Order a change quote", "Turn a quoted asset change into its change order - while the quote is valid and the service "
                "hasn't changed since.", "orders", "🛒", "order", ["quote_number"], ["Order change quote AQ-3"]),
}
LABELS: dict[str, str] = {code: spec[0] for code, spec in SPECS.items()}
LIB: dict[str, dict[str, Any]] = {
    "due_date": {"label": "Due date", "placeholder": "YYYY-MM-DD - blank = as soon as possible",
                 "help": "A future-dated change; a later one queues behind a change still open on the service"},
    "asset_ids": {"label": "Installed asset numbers", "placeholder": "e.g. 4, 5, 9",
                  "help": "Or leave blank and pick the customer - every live service of theirs (of the product below) changes"},
    "current_offering": {"label": "Only services of product", "help": "With a customer: change only their live services of this product"},
    "quote_number": {"label": "Change quote", "placeholder": "e.g. AQ-3"},
}
OVERRIDES: dict[tuple[str, str], dict[str, Any]] = {
    ("INT-110", "order_id"): {"label": "Change order number", "help": "Or the asset - its change still to be fulfilled is cancelled"},
    ("INT-111", "order_id"): {"label": "Change order number", "help": "Or the asset - its change still to be fulfilled is revised"},
    ("INT-111", "asset_operation"): {"help": "Blank keeps the change's operation", "default": None},
    ("INT-113", "as_of"): {"label": "As of", "placeholder": "YYYY-MM-DD - blank = now"},
    ("INT-114", "order_id"): {"label": "Order that installed it", "help": "Instead of the asset number"},
    ("INT-112", "offering_name"): {"label": "New product", "help": "Product change: the product the services change to"},
}
REQUIRED: dict[str, list[str]] = {"INT-110": [], "INT-111": [], "INT-112": ["asset_operation"], "INT-113": ["asset_id"],
                                  "INT-114": ["asset_operation"], "INT-115": ["quote_number"]}
EITHER: dict[str, list[tuple[str, ...]]] = {"INT-110": [("order_id", "asset_id")], "INT-111": [("order_id", "asset_id")],
                                            "INT-112": [("asset_ids", "party_id")], "INT-114": [("asset_id", "order_id")]}


def missing(code: str, entities: dict[str, Any]) -> list[str]:
    return [" or ".join(g) for g in EITHER.get(code, []) if not any(entities.get(k) not in (None, "", []) for k in g)]


_OPS = r"(?:upgrade|downgrade|modify|change|switch|suspend|resume|reactivate|terminate|disconnect|cease|relocat\w*|move|transfer|port)"
_MANY = r"(?:assets|services|connections|lines|subscriptions)"
RULES: list[tuple[str, re.Pattern]] = [
    ("INT-115", re.compile(r"\b(?:order|accept|convert|confirm|place|book)\w*\b[^.]{0,30}\b(?:change|asset)\s+quote\b|\bAQ-?\s*\d+\b", re.I)),
    ("INT-114", re.compile(r"\bquote\b[^.]{0,60}\basset\s*#?\s*\d+|\b(?:price|cost)\s+(?:up\s+|out\s+)?(?:a\s+|the\s+)?"
                           r"(?:change|upgrade|downgrade|relocation|move)\b[^.]{0,40}\basset\s*#?\s*\d+", re.I)),
    ("INT-110", re.compile(r"\bcancel\w*\s+(?:the\s+|this\s+)?(?:asset\s+)?change\s+order\b"
                           r"|\bcancel\w*\b[^.]{0,30}\b(?:pending|open|queued|in-?flight|asset)\s+change\b", re.I)),
    ("INT-111", re.compile(r"\b(?:revise|amend)\w*\s+(?:the\s+|this\s+)?(?:asset\s+)?change\s+order\b"
                           r"|\b(?:revise|amend)\w*\b[^.]{0,30}\b(?:pending|open|queued|in-?flight|asset)\s+change\b", re.I)),
    ("INT-112", re.compile(rf"\bbulk\b[^.]{{0,40}}\b(?:{_OPS}\w*|change|request|promotion)\b"
                           rf"|\b{_OPS}\w*\b[^.]{{0,20}}\b(?:all|every)\b[^.]{{0,40}}\b{_MANY}\b[^.]{{0,20}}\b(?:of|for)\s+(?:customer|party|account)\s*#?\s*\d+"
                           rf"|\b{_OPS}\w*\s+assets\s+\d+(?:\s*(?:,|and|&)\s*\d+)+", re.I)),
    ("INT-113", re.compile(r"\b(?:history|lineage|timeline|revisions)\b[^.]{0,20}\basset\s*#?\s*\d+"
                           r"|\basset\s*#?\s*\d+\b[^.]{0,20}\b(?:history|lineage|revisions|as\s+(?:of|on|at)\s+\d)"
                           r"|\bwhat\s+(?:did|was)\s+asset\s*#?\s*\d+\b[^.]{0,30}\b(?:look\s+like|on|as\s+of)\b", re.I)),
]

_DATE = r"(\d{4}-\d{2}-\d{2})"


def _details(text: str) -> dict[str, Any]:
    """The change's details as INT-107 reads them, with its due date."""
    e = _asset_change(text)
    if m := re.search(rf"\b(?:due|from|effective|starting|dated)\s+(?:on\s+|date\s+)?{_DATE}|\bon\s+{_DATE}", text, re.I):
        e["due_date"] = m.group(1) or m.group(2)
    return e


def extract(code: str, text: str, fields: dict[str, Any], loose: dict[str, Any]) -> dict[str, Any]:
    e: dict[str, Any] = {k: fields[k] for k in SPECS[code][5] if fields.get(k) not in (None, "")}
    keys = SPECS[code][5]
    if "order_id" in keys and "order_id" not in e and (m := re.search(r"\border\s*#?\s*(\d+)\b", text, re.I)):
        e["order_id"] = int(m.group(1))
    if "asset_id" in keys and "asset_id" not in e and (m := re.search(r"\basset\s*#?\s*(\d+)\b", text, re.I)):
        e["asset_id"] = int(m.group(1))
    if code == "INT-110" and "reason" not in e and (m := re.search(r"\b(?:because|reason[:\s]+|as\s+the\s+customer)\s*(.+)$", text, re.I)):
        e["reason"] = m.group(1).strip(" .")[:200]
    if code == "INT-111":
        found = _details(re.sub(r"\b(?:revise|amend)\w*\b", "", text, flags=re.I))
        found.pop("asset_operation", None)  # a revision keeps its operation unless one is set on the form
        e.update({k: v for k, v in found.items() if k not in e and k != "asset_id"})
    if code == "INT-114":
        e.update({k: v for k, v in _details(re.sub(r"\bquote\b", "", text, flags=re.I)).items() if k not in e})
    if code == "INT-112":
        if "asset_ids" not in e and (m := re.search(r"\bassets\s+((?:\d+\s*(?:,|and|&)?\s*)+)", text, re.I)):
            e["asset_ids"] = ", ".join(re.findall(r"\d+", m.group(1)))
        if "party_id" not in e:
            if loose.get("party_id") not in (None, ""):
                e["party_id"] = loose["party_id"]
            elif m := re.search(r"\b(?:customer|party|account)\s*#?\s*(\d+)\b", text, re.I):
                e["party_id"] = int(m.group(1))
        if "current_offering" not in e and (m := re.search(rf"\b(?:all|every)\s+(?:the\s+|of\s+the\s+|their\s+)?(.+?)\s+{_MANY}\b", text, re.I)):
            product = re.sub(r"^(?:live|active|installed|current)\s+", "", m.group(1).strip(), flags=re.I)
            if product and product.lower() not in ("live", "active", "installed", "current", "of the", "the"):
                e["current_offering"] = product
        rest = re.sub(r"\bbulk\b|\bassets\s+(?:\d+\s*(?:,|and|&)?\s*)+", " ", text, flags=re.I)
        e.update({k: v for k, v in _details(rest).items() if k not in e and k != "asset_id"})
    if code == "INT-113" and "as_of" not in e and (m := re.search(rf"\b(?:as\s+(?:of|on|at)|on)\s+{_DATE}", text, re.I)):
        e["as_of"] = m.group(1)
    if code == "INT-115" and "quote_number" not in e and (m := re.search(r"\bAQ-?\s*(\d+)\b|\bquote\s*#?\s*(\d+)\b", text, re.I)):
        e["quote_number"] = f"AQ-{m.group(1) or m.group(2)}"
    return e


NEXT: dict[str, list[tuple[str, str]]] = {
    "INT-110": [("INT-107", "Order another change"), ("INT-113", "See the service's history")],
    "INT-111": [("INT-108", "Confirm fulfillment once orchestration completes"), ("INT-110", "Cancel the change instead")],
    "INT-112": [("INT-108", "Confirm fulfillment of each change order")],
    "INT-113": [("INT-107", "Change the service"), ("INT-114", "Quote a change first")],
    "INT-114": [("INT-115", "Order the quoted change"), ("INT-114", "Quote another option")],
    "INT-115": [("INT-108", "Confirm fulfillment once orchestration completes"), ("INT-111", "Revise the change order")],
}

# Generated training templates (appended at the END of training_data._T).
TRAINING: list[tuple[str, list[tuple[str, dict[str, Any]]]]] = [
    ("INT-110", [
        ("Cancel change order {id}", {"order_id": "{id}"}),
        ("cancel the pending change on asset {id}", {"asset_id": "{id}"}),
        ("Cancel change order {id} because the customer changed their mind", {"order_id": "{id}", "reason": "the customer changed their mind"}),
    ]),
    ("INT-111", [
        ("Revise change order {id} to 500 Mbps", {"order_id": "{id}", "pricing_attributes": {"Bandwidth": "500 Mbps"}}),
        ("amend the pending change on asset {id}: quantity 3", {"asset_id": "{id}", "quantity": 3}),
    ]),
    ("INT-112", [
        ("Suspend assets {id}, {id2} and 9", {"asset_ids": "{id}, {id2}, 9", "asset_operation": "suspend"}),
        ("Upgrade all {product} services of customer {id} to 500 Mbps",
         {"party_id": "{id}", "current_offering": "{product}", "asset_operation": "modify", "pricing_attributes": {"Bandwidth": "500 Mbps"}}),
        ("bulk change the promotion on all services of customer {id} to SUMMER25",
         {"party_id": "{id}", "asset_operation": "promotion_change", "promotion": "SUMMER25"}),
    ]),
    ("INT-113", [
        ("History of asset {id}", {"asset_id": "{id}"}),
        ("what did asset {id} look like on 2026-01-31", {"asset_id": "{id}", "as_of": "2026-01-31"}),
    ]),
    ("INT-114", [
        ("Quote an upgrade of asset {id} to 500 Mbps", {"asset_id": "{id}", "asset_operation": "modify", "pricing_attributes": {"Bandwidth": "500 Mbps"}}),
        ("quote relocating asset {id} to {address}", {"asset_id": "{id}", "asset_operation": "relocate", "target_site": "{address}"}),
    ]),
    ("INT-115", [
        ("Order change quote AQ-{id}", {"quote_number": "AQ-{id}"}),
        ("the customer accepted change quote AQ-{id} - order it", {"quote_number": "AQ-{id}"}),
    ]),
]

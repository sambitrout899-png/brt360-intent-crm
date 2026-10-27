"""Quote negotiation intents (crm_core/quote_override_service.py does the work).

  INT-116 Override the quote price - the total negotiated with the customer
          replaces the computed price of a draft quote; its effective
          discount is what the approval matrix routes on. "Go back to the
          system price" removes it.

Like the other later intents it is recognised by phrase rules before any
model runs, so the trained model's prompt stays unchanged; its training
examples are appended last.
"""
from __future__ import annotations

import re
from typing import Any

CODES = ("INT-116",)

# code -> (label, description, category, icon, target, fields, examples)
SPECS: dict[str, tuple[str, str, str, str, str | None, list[str], list[str]]] = {
    "INT-116": ("Override the quote price", "Set the monthly total negotiated with the customer on a draft quote - it replaces the "
                "computed price, and its effective discount decides who must approve it. The products' price floors still hold.",
                "sales", "🤝", "quote", ["quote_id", "override_price", "reason", "remove_override"],
                ["Override the price of quote 12 to 4500 - customer has a competing offer",
                 "Remove the price override on quote 12"]),
}
LABELS: dict[str, str] = {code: spec[0] for code, spec in SPECS.items()}

_UNLESS_REMOVING = {"field": "remove_override", "in": ["", "false"]}
LIB: dict[str, dict[str, Any]] = {
    "override_price": {"label": "Negotiated monthly total", "type": "money",
                       "help": "What the customer pays a month for the whole quote - replaces the computed price"},
    "remove_override": {"label": "Go back to the system price", "type": "boolean", "default": False,
                        "help": "Removes the negotiated price - the quote is priced as the system worked it out"},
}
OVERRIDES: dict[tuple[str, str], dict[str, Any]] = {
    ("INT-116", "quote_id"): {"help": "A draft quote - a locked one needs a new revision first"},
    ("INT-116", "override_price"): {"show_when": _UNLESS_REMOVING},
    ("INT-116", "reason"): {"label": "Why this price?", "placeholder": "e.g. matching a competitor's offer of 4,400 on a 36-month term",
                            "help": "The customer's ask, a competing offer, the deal context - the approvers see it",
                            "show_when": _UNLESS_REMOVING},
}
REQUIRED: dict[str, list[str]] = {"INT-116": ["quote_id"]}


def _removing(entities: dict[str, Any]) -> bool:
    return str(entities.get("remove_override") or "").lower() in ("true", "1", "yes")


def missing(code: str, entities: dict[str, Any]) -> list[str]:
    if code != "INT-116" or _removing(entities):
        return []
    return [k for k in ("override_price", "reason") if entities.get(k) in (None, "")]


_QUOTE = r"\b(?:quote|quotation)\s*#?\s*(\d+)\b|\bQ-0*(\d+)\b"
_REMOVE = re.compile(r"\b(?:remove|drop|clear|undo|cancel|delete|withdraw)\w*\b[^.]{0,30}\b(?:price\s+)?override\b"
                     r"|\b(?:back|revert|return|reset)\w*\b[^.]{0,20}\b(?:system|computed|original|calculated)\s+price\b", re.I)
RULES: list[tuple[str, re.Pattern]] = [
    # never about an order, asset or bill - "override the price of order 12" is not a quote negotiation
    ("INT-116", re.compile(r"^(?!.*\b(?:order|asset|bill|invoice)s?\s*#?\s*\d)(?:.*?)"
                           r"(?:\bprice\s+override\b|\boverrid\w*\b[^.]{0,40}\b(?:price|total|amount)\b"
                           r"|\bnegotiated\s+(?:price|total|amount|rate)\b|\b(?:back|revert|return|reset)\w*\b[^.]{0,20}\b(?:system|computed|original|calculated)\s+price\b)",
                           re.I | re.S)),
]

_AMOUNT = re.compile(r"(?<![\w.%-])(?:[A-Z]{3}\s*|[₹$€£]\s*)?(\d{1,3}(?:,\d{3})+|\d+)(\.\d+)?\s*(k\b)?(?!\s*%|\d)", re.I)


def _amount(text: str) -> float | None:
    """The first money amount, once the quote's own number is taken out."""
    rest = re.sub(_QUOTE, " ", text, flags=re.I)
    if m := _AMOUNT.search(rest):
        value = float(m.group(1).replace(",", "") + (m.group(2) or ""))
        return value * 1000 if m.group(3) else value
    return None


def extract(code: str, text: str, fields: dict[str, Any], loose: dict[str, Any]) -> dict[str, Any]:
    e: dict[str, Any] = {k: fields[k] for k in SPECS[code][5] if fields.get(k) not in (None, "")}
    if "quote_id" not in e:
        if loose.get("quote_id") not in (None, ""):
            e["quote_id"] = loose["quote_id"]
        elif m := re.search(_QUOTE, text, re.I):
            e["quote_id"] = int(m.group(1) or m.group(2))
    if "remove_override" not in e and _REMOVE.search(text):
        e["remove_override"] = True
    if _removing(e):
        return e
    if "override_price" not in e:
        given = fields.get("price") or fields.get("negotiated_price") or fields.get("total")
        amount = _amount(str(given)) if given not in (None, "") else _amount(text)
        if amount is not None:
            e["override_price"] = amount
    if "reason" not in e and (m := re.search(r"(?:\bbecause\b|\bsince\b|\breason\s*[:=-]?|\s[-–—]\s)\s*(.+)$", text, re.I)):
        e["reason"] = m.group(1).strip(" .")[:300]
    return e


NEXT: dict[str, list[tuple[str, str]]] = {
    "INT-116": [("INT-06", "Submit the negotiated price for approval"), ("INT-116", "Counter with another price")],
}

# Generated training templates (appended at the END of training_data._T).
TRAINING: list[tuple[str, list[tuple[str, dict[str, Any]]]]] = [
    ("INT-116", [
        ("Override the price of quote {id} to {price} - customer has a competing offer",
         {"quote_id": "{id}", "override_price": "{price}", "reason": "customer has a competing offer"}),
        ("negotiated price for quote {id} is {price} because they sign for 36 months",
         {"quote_id": "{id}", "override_price": "{price}", "reason": "they sign for 36 months"}),
        ("Remove the price override on quote {id}", {"quote_id": "{id}", "remove_override": True}),
    ]),
]

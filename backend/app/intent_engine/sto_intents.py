"""B2B Sales-to-Order document intents (crm_core/quote_documents.py does the work).

  INT-109 Generate the proposal - once the quote's discount is approved, the
          customer proposal is written as a PDF and attached to the quote, to be
          checked before it is sent to the customer (INT-22).

Like the other intents added after the model was trained, it is recognised by
phrase rules before any model runs - "prepare the proposal", "generate the
proposal PDF" - so the trained model's prompt stays unchanged ("Generate
proposal for this quote" stays INT-22, as before); its training examples are
appended last.
"""
from __future__ import annotations

import re
from typing import Any

CODES = ("INT-109",)

# code -> (label, description, category, icon, target, fields, examples)
SPECS: dict[str, tuple[str, str, str, str, str | None, list[str], list[str]]] = {
    "INT-109": ("Generate the proposal", "Write the customer proposal for an approved quote as a PDF and attach it to the quote - "
                "check it, then send it to the customer.", "sales", "📄", "quote", ["quote_id", "notes"],
                ["Prepare the proposal for quote 12", "Generate the proposal PDF for this quote"]),
}
LABELS: dict[str, str] = {code: spec[0] for code, spec in SPECS.items()}
LIB: dict[str, dict[str, Any]] = {}
OVERRIDES: dict[tuple[str, str], dict[str, Any]] = {
    ("INT-109", "notes"): {"label": "Note on this version", "help": "Optional - e.g. what changed since the last one"},
}
REQUIRED: dict[str, list[str]] = {"INT-109": ["quote_id"]}


def missing(code: str, entities: dict[str, Any]) -> list[str]:
    return []


_DET = r"(?:the\s+|a\s+|an\s+|this\s+|their\s+|customer'?s?\s+|new\s+)*"
RULES: list[tuple[str, re.Pattern]] = [
    ("INT-109", re.compile(rf"\b(?:prepare|draft|build|write|regenerate|re-generate)\w*\s+{_DET}proposal\b"
                           rf"|\b(?:generate|create|make|produce)\w*\s+{_DET}proposal\s+(?:document|pdf|doc)\b", re.I)),
]


def extract(code: str, text: str, fields: dict[str, Any], loose: dict[str, Any]) -> dict[str, Any]:
    e: dict[str, Any] = {k: fields[k] for k in SPECS[code][5] if fields.get(k) not in (None, "")}
    if "quote_id" not in e:
        if loose.get("quote_id") not in (None, ""):
            e["quote_id"] = loose["quote_id"]
        elif m := re.search(r"\b(?:quote|quotation)\s*#?\s*(\d+)\b", text, re.I):
            e["quote_id"] = int(m.group(1))
    return e


NEXT: dict[str, list[tuple[str, str]]] = {
    "INT-109": [("INT-22", "Send the proposal to the customer"), ("INT-109", "Generate a new version")],
}

# Generated training templates (appended at the END of training_data._T).
TRAINING: list[tuple[str, list[tuple[str, dict[str, Any]]]]] = [
    ("INT-109", [
        ("Prepare the proposal for quote {id}", {"quote_id": "{id}"}),
        ("generate the proposal PDF for quote {id}", {"quote_id": "{id}"}),
        ("draft a proposal for quote {id}", {"quote_id": "{id}"}),
    ]),
]

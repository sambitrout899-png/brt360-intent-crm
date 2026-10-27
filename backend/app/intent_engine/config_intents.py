"""Configuration intents - the tenant's own setup, said in plain English.

  INT-103 Customize a page - change, enrich, copy, switch on/off or give to
          roles a Page Designer page: fields, sections, buttons that run an
          intent or a Flow Studio flow, text, and the flows its events start.
          Administrators only; it changes this tenant's pages, never the
          base build every tenant shares.

Like the Order Management and Communications intents they are recognised by
phrase rules before any model runs, so the trained model's prompt stays
unchanged; their training examples are appended last.
"""

from __future__ import annotations

import re
from typing import Any

CODES = ("INT-103",)

# code -> (label, description, category, icon, target, fields, examples)
SPECS: dict[str, tuple[str, str, str, str, str | None, list[str], list[str]]] = {
    "INT-103": ("Customize a page", "Change or enrich a page (fields, sections, buttons that run an intent or a Flow Studio flow, text), "
                "copy it as a new page, switch it on or off, give it to roles, or start a flow when it is opened or saved.",
                "workspace", "🛠️", None, ["page", "instruction", "copy_as"],
                ["On the B2B prospect page add a button 'Send welcome kit' that runs the flow Welcome kit",
                 "Copy the B2B prospect page as Enterprise onboarding and give it to the Enterprise Sales role"]),
}
LABELS: dict[str, str] = {code: spec[0] for code, spec in SPECS.items()}

LIB: dict[str, dict[str, Any]] = {
    "page": {"label": "Page", "type": "picklist", "source": "@pages", "help": "The page to change (or to copy)"},
    "copy_as": {"label": "Copy it as", "placeholder": "e.g. Enterprise onboarding", "help": "Blank = change the page itself"},
}
OVERRIDES: dict[tuple[str, str], dict[str, Any]] = {
    ("INT-103", "instruction"): {"label": "What should change?", "type": "textarea",
                                 "placeholder": "e.g. add a button 'Credit check' that runs INT-75; make GSTIN mandatory; "
                                                "when saved run the flow Welcome kit; give it to the Sales role; activate",
                                 "help": "Fields, sections, buttons, text, page events, roles, switch on / off - one change a line or sentence"},
}

REQUIRED: dict[str, list[str]] = {"INT-103": ["page"]}
EITHER: dict[str, list[tuple[str, ...]]] = {"INT-103": [("instruction", "copy_as")]}


def missing(code: str, entities: dict[str, Any]) -> list[str]:
    return [" or ".join(g) for g in EITHER.get(code, []) if not any(entities.get(k) not in (None, "", []) for k in g)]


_VERBS = (r"customi[sz]e|design|redesign|change|update|edit|enrich|modify|copy|clone|duplicate|activate|deactivate|enable|disable|publish"
          r"|unpublish|switch\s+(?:on|off)|turn\s+(?:on|off)")
_PAGE = r"(?:['\"“”‘’][^'\"“”‘’]{1,60}['\"“”‘’]|(?:the\s+|this\s+)?[\w&/-]+(?:\s+[\w&/-]+){0,4})\s+page\b"
RULES: list[tuple[str, re.Pattern]] = [
    ("INT-103", re.compile(
        rf"^(?!.*\b(?:customer\s*)?360\b)(?:.*\b(?:{_VERBS})\b[^.]{{0,60}}?\b{_PAGE}"
        rf"|.*\b(?:on|in|to|of|for)\s+{_PAGE}[^.]{{0,20}}?[,:]?\s*(?:add|put|remove|hide|show|make|rename|move|give|assign|when|after|only)\b"
        rf"|\s*(?:add|put|place)\s+(?:a\s+|an\s+)?(?:\w+\s+)?(?:button|field|section|note|text)\b.*\b(?:on|to|in)\s+{_PAGE}"
        rf"|.*\b(?:give|show|assign)\s+{_PAGE}\s+(?:only\s+)?to\b)", re.I)),
]


def _clean_ref(ref: str) -> str:
    ref = ref.strip().strip("'\"“”‘’").strip()
    ref = re.sub(r"^(?:on|in|to|of|for|the|this|a)\s+", "", ref, flags=re.I)
    ref = re.sub(r"^(?:on|in|to|of|for|the|this|a)\s+", "", ref, flags=re.I)
    return ref.strip()


def extract(code: str, text: str, fields: dict[str, Any], loose: dict[str, Any]) -> dict[str, Any]:
    e: dict[str, Any] = {k: fields[k] for k in SPECS[code][5] if fields.get(k) not in (None, "")}
    rest = text.strip()
    if "page" not in e:
        m = re.search(r"['\"“”‘’]([^'\"“”‘’]{1,60})['\"“”‘’]\s+page\b", rest) \
            or re.search(r"\b(?:the|this)\s+((?:[\w&/-]+\s+){0,4}?[\w&/-]+)\s+page\b", rest, re.I) \
            or re.search(r"\b((?:[\w&/-]+\s+){0,3}?[\w&/-]+)\s+page\b", rest, re.I)
        if m:
            ref = _clean_ref(re.sub(rf"^(?:{_VERBS})\s+", "", m.group(1), flags=re.I))
            if ref and ref.lower() not in ("this", "the", "a"):
                e["page"] = ref
            rest = (rest[: m.start()] + rest[m.end():]).strip()
    if "copy_as" not in e and (m := re.search(r"\b(?:copy|clone|duplicate)\b[^.]*?\bas\s+(?:a\s+new\s+page\s+)?['\"“”‘’]?([^,.;'\"“”‘’]+?)['\"“”‘’]?"
                                               r"(?=\s+(?:and|then|,)\b|[,.;]|$)", text, re.I)):
        e["copy_as"] = m.group(1).strip()
    if "instruction" not in e:
        instruction = rest
        if m := re.search(r"\bpage\b['\"“”‘’]?\s*:\s*(.+)$", text, re.S | re.I):  # "Customize the lead page: ..."
            instruction = m.group(1)
        else:
            instruction = re.sub(rf"^\s*(?:please\s+)?(?:{_VERBS})\b(?:\s+it)?\s*(?:so\s+that|to|and|by|-|,)?\s*", "", instruction, flags=re.I)
            instruction = re.sub(r"^\s*(?:on|in|to|for|of)\s*[,:]?\s*", "", instruction, flags=re.I)
            if e.get("copy_as"):
                instruction = re.sub(r"^\s*as\s+['\"“”‘’]?" + re.escape(e["copy_as"]) + r"['\"“”‘’]?\s*(?:and|then|,)?\s*", "", instruction, flags=re.I)
        instruction = re.sub(r"\s{2,}", " ", instruction)
        instruction = re.sub(r"\s+(?:to|on|in|of|for)\s*$", "", instruction, flags=re.I)  # "... to [the X page]"
        instruction = re.sub(r"^(give|show|assign)\s+to\b", r"\1 it to", instruction, flags=re.I)
        instruction = instruction.strip(" ,.;-")
        if re.match(rf"^(?:{_VERBS})$", instruction, re.I) and re.match(r"^(?:activate|deactivate|enable|disable|publish|unpublish|switch|turn)",
                                                                         text.strip(), re.I):
            instruction = re.match(r"^\s*(\w+(?:\s+(?:on|off))?)", text).group(1)
        elif re.match(r"^(?:activate|deactivate|enable|disable|publish|unpublish|switch\s+(?:on|off)|turn\s+(?:on|off))\b", text.strip(), re.I) \
                and not instruction:
            instruction = re.match(r"^\s*(\w+(?:\s+(?:on|off))?)", text).group(1)
        if instruction:
            e["instruction"] = instruction
    return e


NEXT: dict[str, list[tuple[str, str]]] = {"INT-103": [("INT-103", "Change it further")]}

# Generated training templates (appended at the END of training_data._T).
TRAINING: list[tuple[str, list[tuple[str, dict[str, Any]]]]] = [
    ("INT-103", [
        ("On the B2B prospect page add a button 'Send welcome kit' that runs the flow Welcome kit",
         {"page": "B2B prospect", "instruction": "add a button 'Send welcome kit' that runs the flow Welcome kit"}),
        ("Customize the lead page: make budget mandatory; add a section Competition",
         {"page": "lead", "instruction": "make budget mandatory; add a section Competition"}),
        ("Copy the B2B prospect page as Enterprise onboarding", {"page": "B2B prospect", "copy_as": "Enterprise onboarding"}),
        ("Give the Enterprise onboarding page to the Enterprise Sales role",
         {"page": "Enterprise onboarding", "instruction": "give it to the Enterprise Sales role"}),
        ("Activate the Enterprise onboarding page", {"page": "Enterprise onboarding", "instruction": "activate"}),
        ("On the quote page, when saved run the flow Notify finance", {"page": "quote", "instruction": "when saved run the flow Notify finance"}),
    ]),
]

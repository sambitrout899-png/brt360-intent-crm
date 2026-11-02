"""Bulk ordering intents (orchestration/bulk_orders.py does the work; actions_bulk.py builds the steps).

  INT-149 Start a bulk order  many orders in one request - B2C new connects, B2B quotes to orders, mobile
                              lines or asset changes - each run through the single-order process and
                              followed to fulfilment completion, assets and billing start
  INT-150 Bulk order status   where a bulk order's rows are (Orders -> Bulk orders "process now" moves it along at once)

Recognised by phrase rules before any model runs, like the other later intents.
"""
from __future__ import annotations

import re
from typing import Any

CODES = ("INT-149", "INT-150")

SPECS: dict[str, tuple[str, str, str, str, str | None, list[str], list[str]]] = {
    "INT-149": ("Start a bulk order", "Many orders in one request: B2C new connects, B2B quotes to orders, mobile lines or asset changes - "
                "each row runs the same process as a single order (customer, order, approval, fulfilment) and is followed until its "
                "services are installed and billing has started.", "orders", "📚", None,
                ["bulk_process", "bulk_rows", "bulk_defaults", "bulk_name", "auto_approve", "send_to_fulfillment"],
                ["Start a bulk order of B2C new connects from these rows", "Bulk order mobile lines for these customers"]),
    "INT-150": ("Bulk order status", "Where a bulk order's rows are - placed, waiting for approval, in fulfilment, in fallout, completed - "
                "with their orders, assets and billing.", "orders", "📈", None,
                ["bulk_order"], ["Status of BULK-000012", "How far is bulk order 12?"]),
}
LABELS: dict[str, str] = {code: spec[0] for code, spec in SPECS.items()}
LIB: dict[str, dict[str, Any]] = {
    "bulk_process": {"label": "Process", "type": "picklist", "values": ["b2c_new_connect", "b2b_quote_order", "mobile_lines", "asset_change"],
                     "help": "What every row does - the same steps as one order of that kind"},
    "bulk_rows": {"label": "Rows (CSV)", "type": "textarea",
                  "placeholder": "customer_name,email,products,installation_address\nPriya Sharma,priya@example.com,Home Fiber 100,12 MG Road",
                  "help": "A header line, then one order per line - Orders -> Bulk orders has the columns of each process and a template"},
    "bulk_defaults": {"label": "Every row gets", "type": "textarea", "placeholder": "order_channel=store; products=Home Fiber 100",
                      "help": "Values every row shares unless it gives its own (name=value; ...)"},
    "bulk_name": {"label": "Name", "placeholder": "e.g. October campus connects"},
    "auto_approve": {"label": "Approve each order along the way", "type": "boolean", "default": True,
                     "help": "Order Control approval (INT-25) for each booked order - off: each waits for its approver"},
    "send_to_fulfillment": {"label": "Send orders to Fulfillment", "type": "boolean", "default": True,
                            "help": "An order no fulfilment pattern started goes to one that fits (INT-117)"},
    "bulk_order": {"label": "Bulk order", "placeholder": "e.g. BULK-000012"},
}
OVERRIDES: dict[tuple[str, str], dict[str, Any]] = {}
REQUIRED: dict[str, list[str]] = {"INT-149": ["bulk_process", "bulk_rows"], "INT-150": ["bulk_order"]}


def missing(code: str, entities: dict[str, Any]) -> list[str]:
    return []


RULES: list[tuple[str, re.Pattern]] = [
    ("INT-150", re.compile(r"\bBULK-\d+\b|\b(?:status|progress|track\w*|how\s+far|where)\b[^.]{0,30}\bbulk\s+orders?\b", re.I)),
    ("INT-149", re.compile(r"\bbulk\s+(?:order|ordering|upload|activation|new\s+connect|connect|import)\w*\b|\b(?:upload|import)\w*\b[^.]{0,30}\borders\b", re.I)),
]


def extract(code: str, text: str, fields: dict[str, Any], loose: dict[str, Any]) -> dict[str, Any]:
    e: dict[str, Any] = {k: fields[k] for k in SPECS[code][5] if fields.get(k) not in (None, "")}
    lowered = text.lower()
    if code == "INT-150":
        if "bulk_order" not in e and (m := re.search(r"\bBULK-\d+\b|\bbulk\s+order\s*#?\s*(\d+)\b", text, re.I)):
            e["bulk_order"] = m.group(0) if m.group(0).upper().startswith("BULK-") else m.group(1)
    else:
        if "bulk_process" not in e:
            for pattern, value in ((r"\bmobile|\bsims?\b|\blines?\b", "mobile_lines"), (r"\bquotes?\b", "b2b_quote_order"),
                                   (r"\bassets?\b|\bchanges?\b|\bupgrades?\b|\bsuspen", "asset_change"), (r"\bb2c\b|\bconnects?\b|\bconsumer", "b2c_new_connect")):
                if re.search(pattern, lowered):
                    e["bulk_process"] = value
                    break
        if "bulk_rows" not in e and "\n" in text:
            body = text.split("\n", 1)[1].strip()
            if "," in body:
                e["bulk_rows"] = body
    return e


NEXT: dict[str, list[tuple[str, str]]] = {
    "INT-149": [("INT-150", "Follow the bulk order")],
    "INT-150": [("INT-118", "Resolve a fulfilment fallout on one of its orders")],
}
TRAINING: list[tuple[str, list[tuple[str, dict[str, Any]]]]] = [
    ("INT-149", [("Start a bulk order of B2C new connects", {"bulk_process": "b2c_new_connect"}),
                 ("bulk order mobile lines for these customers", {"bulk_process": "mobile_lines"})]),
    ("INT-150", [("Status of BULK-0000{n}", {"bulk_order": "BULK-0000{n}"}), ("how far is bulk order {id}", {"bulk_order": "{id}"})]),
]
FLOW_FIELDS: dict[str, list[str]] = {code: list(spec[5]) for code, spec in SPECS.items()}
RESULTS: dict[str, dict[str, list[str]]] = {
    "INT-149": {"bulk_order_id": [], "number": [], "rows": [], "status": ["draft", "running", "completed", "completed_with_errors", "cancelled"]},
    "INT-150": {"bulk_order_id": [], "number": [], "completed": [], "failed": [], "in_progress": [],
                "status": ["draft", "running", "completed", "completed_with_errors", "cancelled"]},
}

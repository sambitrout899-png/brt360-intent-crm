"""Order Management intents (INT-51..71) - everything the classifier,
the intent catalog, reasoning and the flow designer need to know about them,
in one place. They are recognised by phrase rules before any model runs
(classifier._process_step), so the trained model's prompt stays unchanged.

  INT-51 Add line items          INT-61 Cancel a sales order
  INT-52 Discount a line         INT-62 Supplemental order (revise in flight)
  INT-53 Verify quote / order    INT-63 Follow-on order
  INT-54 Check availability      INT-64 Ship / deliver an order
  INT-55 Shipping details        INT-65 Quote to agreement (AutoContract)
  INT-56 Calculate tax           INT-66 Update the opportunity from a quote
  INT-57 Add a payment           INT-67 Return items (RMA)
  INT-58 Credit check            INT-68 Activity plan on an order
  INT-59 Submit an order         INT-69 Apply a promotion
  INT-60 Hold / release          INT-70 Resume a suspended service
                                 INT-71 Track an order
"""

from __future__ import annotations

import re
from typing import Any

CODES = tuple(f"INT-{n}" for n in range(51, 72))

LABELS: dict[str, str] = {
    "INT-51": "Add line items", "INT-52": "Discount a line", "INT-53": "Verify a quote or order",
    "INT-54": "Check availability", "INT-55": "Set shipping details", "INT-56": "Calculate tax", "INT-57": "Add a payment",
    "INT-58": "Run a credit check", "INT-59": "Submit the order", "INT-60": "Hold or release an order",
    "INT-61": "Cancel a sales order", "INT-62": "Revise a submitted order", "INT-63": "Create a follow-on order",
    "INT-64": "Ship or deliver an order", "INT-65": "Convert a quote to an agreement", "INT-66": "Update the opportunity from a quote",
    "INT-67": "Return items", "INT-68": "Add an activity plan", "INT-69": "Apply a promotion", "INT-70": "Resume a service",
    "INT-71": "Track an order",
}

# Field library entries these intents add (intent_registry._LIB).
LIB: dict[str, dict[str, Any]] = {
    "product": {"label": "Product", "placeholder": "e.g. Router CPE, Static IP, Installation fee",
                "help": "A catalog product, stocked equipment, or anything else as a write-in (give its price)"},
    "unit_price": {"label": "Price", "type": "money", "help": "Blank = the pricelist / stock price"},
    "price_type": {"label": "Charged", "type": "picklist", "values": ["recurring", "one_time", "usage"],
                   "help": "Blank = monthly for services, once for equipment"},
    "requested_date": {"label": "Requested date", "placeholder": "e.g. 2026-11-15 or 'in 2 weeks'"},
    "favorite": {"label": "Favorite", "placeholder": "e.g. Branch office kit", "help": "A saved set of products added in one go"},
    "line": {"label": "Line", "placeholder": "Product name or line number"},
    "discount_pct": {"label": "Discount", "type": "percent"},
    "discount_amount": {"label": "Discount amount", "type": "money", "help": "Off each unit"},
    "split": {"label": "Split lines", "type": "boolean", "help": "Ship what is in stock now and backorder the rest"},
    "shipping_method": {"label": "Shipping method", "placeholder": "e.g. Standard, Express"},
    "carrier": {"label": "Carrier"},
    "ship_region": {"label": "Ship-to region", "help": "Decides the tax jurisdiction"},
    "jurisdiction": {"label": "Tax jurisdiction", "help": "Blank = the ship-to region, else where the deal was sold"},
    "payment_method": {"label": "Payment method", "type": "picklist",
                       "values": ["credit_card", "purchase_order", "bank_transfer", "on_account", "cash", "cheque"]},
    "amount": {"label": "Amount", "type": "money", "help": "Blank = what is due now"},
    "card_last4": {"label": "Card - last 4 digits", "placeholder": "e.g. 4242",
                   "help": "Never the full card number - the card itself is handled by the payment provider"},
    "card_brand": {"label": "Card brand", "type": "picklist", "values": ["visa", "mastercard", "amex", "mada"]},
    "payment_terms": {"label": "Payment terms", "placeholder": "e.g. Net 30"},
    "hold_action": {"label": "Hold or release", "type": "picklist", "values": ["hold", "release"], "default": "hold"},
    "tracking_number": {"label": "Tracking number"},
    "location": {"label": "Stock location", "placeholder": "e.g. Central Warehouse", "help": "Blank = where availability found it"},
    "ship_action": {"label": "What happened", "type": "picklist", "values": ["ship", "deliver"], "default": "ship"},
    "refund": {"label": "Credit the customer", "type": "boolean", "default": True},
    "term_months": {"label": "Term (months)", "type": "number", "default": 12},
    "response_hours": {"label": "Response time (hours)", "type": "number", "help": "Sets the customer's service entitlement"},
    "coverage": {"label": "Coverage", "type": "picklist", "values": ["business", "24x7"], "default": "business"},
    "template": {"label": "Activity template", "placeholder": "e.g. Fiber installation"},
    "promotion": {"label": "Promotion", "placeholder": "Code or name, e.g. ROUTER10"},
    "changes": {"label": "What changes", "type": "textarea", "placeholder": "e.g. add 2 x Router CPE, remove Static IP, change SFP module 1G to 3"},
}

# code -> (label, description, category, icon, target, fields, examples)
SPECS: dict[str, tuple[str, str, str, str, str, list[str], list[str]]] = {
    "INT-51": ("Add line items", "Add products to a quote or an order - catalog products, equipment, write-ins or a favorite; related products are suggested.",
               "sales", "➕", "quote", ["quote_id", "order_id", "product", "quantity", "unit_price", "price_type", "requested_date", "favorite"],
               ["Add 2 x Router CPE to quote 12", "add the Branch office kit favorite to order 7"]),
    "INT-52": ("Discount a line", "Give a manual discount on one line of a quote or order - % or an amount, with a reason above 10%.",
               "sales", "🏷️", "quote", ["quote_id", "order_id", "line", "discount_pct", "discount_amount", "discount_justification"],
               ["Discount the Router CPE line on quote 12 by 15% - multi-site deal"]),
    "INT-53": ("Verify a quote or order", "Check a quote or order before it goes out: products, prices, expiry, stock, ship-to, tax and payment.",
               "sales", "🔎", "quote", ["quote_id", "order_id"], ["Verify quote 12", "verify order 7"]),
    "INT-54": ("Check availability", "Available-to-promise for each line - from which warehouse and when; split a line to ship what is in stock.",
               "orders", "📦", "order", ["order_id", "quote_id", "split"], ["Check availability for order 7", "check stock availability on quote 12 and split"]),
    "INT-55": ("Set shipping details", "Ship-to address, requested date and shipping method - freight is calculated.",
               "orders", "🚚", "order", ["order_id", "address", "shipping_method", "requested_date", "carrier", "ship_region"],
               ["Ship to 12 King Fahd Road, Riyadh for order 7 by Express"]),
    "INT-56": ("Calculate tax", "Tax for each line by jurisdiction - one-time and monthly.", "orders", "🧮", "order",
               ["order_id", "quote_id", "jurisdiction"], ["Calculate tax for order 7"]),
    "INT-57": ("Add a payment", "Take payment for an order: card (last 4 digits only), purchase order or on account (credit checked), transfer or cash.",
               "orders", "💳", "order", ["order_id", "payment_method", "amount", "card_last4", "card_brand", "po_number", "payment_terms"],
               ["Add a payment by card ending 4242 for order 7", "payment by purchase order PO-881 for order 7"]),
    "INT-58": ("Run a credit check", "Check the customer's credit limit against what they owe, their open orders and this order.",
               "orders", "🏦", "order", ["order_id", "party_id", "amount"], ["Credit check for order 7"]),
    "INT-59": ("Submit the order", "Verify, reserve stock and book the order - discounts above the limit go to a manager first; the customer gets an acknowledgement.",
               "orders", "📨", "order", ["order_id", "decision"], ["Submit order 7", "approve the submitted order 7"]),
    "INT-60": ("Hold or release an order", "Stop an order temporarily with a reason, or release it.", "orders", "✋", "order",
               ["order_id", "hold_action", "reason"], ["Put order 7 on hold - waiting for site access", "release order 7 from hold"]),
    "INT-61": ("Cancel a sales order", "Cancel an order that has not shipped - a new revision cancels every line, releases stock and voids payments.",
               "orders", "🚫", "order", ["order_id", "reason"], ["Cancel sales order 7 - customer changed their mind"]),
    "INT-62": ("Revise a submitted order", "Supplemental order: change an order in flight before its point of no return - Add / Update / Delete lines.",
               "orders", "📝", "order", ["order_id", "changes", "reason"], ["Supplemental order for order 7: add 2 x Router CPE, remove Static IP"]),
    "INT-63": ("Create a follow-on order", "After the point of no return: a new, future-dated order for the same service, starting from the original.",
               "orders", "⏭️", "order", ["order_id", "changes", "requested_date"], ["Follow-on order for order 7: add 1 x Router CPE from 2026-12-01"]),
    "INT-64": ("Ship or deliver an order", "Ship the equipment (stock moves, tracking number) or confirm delivery - trackable items become the customer's assets.",
               "orders", "📬", "order", ["order_id", "ship_action", "location", "carrier", "tracking_number"],
               ["Ship order 7 from Central Warehouse", "order 7 delivered"]),
    "INT-65": ("Convert a quote to an agreement", "AutoContract: an agreement from the quote's lines and term - with a service level that becomes the customer's entitlement.",
               "sales", "📜", "quote", ["quote_id", "term_months", "response_hours", "coverage", "name"],
               ["Create an agreement from quote 12 for 24 months with 4 hour response"]),
    "INT-66": ("Update the opportunity from a quote", "Copy the quote's forecastable lines to the opportunity's revenue forecast.",
               "sales", "📈", "quote", ["quote_id"], ["Update the opportunity from quote 12"]),
    "INT-67": ("Return items", "Return delivered equipment (RMA) - it goes to the repair center and the customer is credited.",
               "orders", "↩️", "order", ["order_id", "product", "quantity", "reason", "refund", "location"],
               ["Return 1 Router CPE on order 7 - dead on arrival"]),
    "INT-68": ("Add an activity plan", "Add the activities an order needs - installation, site survey - from a template; they appear on the dispatch board.",
               "orders", "🗓️", "order", ["order_id", "template", "line"], ["Add the Fiber installation activity plan to order 7"]),
    "INT-69": ("Apply a promotion", "Apply a promotion to a quote or order - its discount and commitment go on the qualifying lines.",
               "sales", "🎁", "quote", ["quote_id", "order_id", "promotion"], ["Apply promotion ROUTER10 to quote 12"]),
    "INT-70": ("Resume a service", "Resume a suspended service - billing restarts.", "orders", "▶️", "order", ["order_id"], ["Resume order 7"]),
    "INT-71": ("Track an order", "Where an order is - status, lines, shipments, milestones and what happens next.", "orders", "📍", "order",
               ["order_id"], ["Track order 7", "where is order 7"]),
}

OVERRIDES: dict[tuple[str, str], dict[str, Any]] = {
    ("INT-55", "address"): {"label": "Ship-to address", "placeholder": "e.g. 12 King Fahd Road, Riyadh"},
    ("INT-59", "decision"): {"label": "Approval (only for an order waiting for one)", "values": ["approve", "reject"], "default": None},
    ("INT-51", "quote_id"): {"help": "Or pick an order"}, ("INT-51", "order_id"): {"help": "Or pick a quote"},
    ("INT-52", "quote_id"): {"help": "Or pick an order"}, ("INT-52", "order_id"): {"help": "Or pick a quote"},
    ("INT-53", "quote_id"): {"help": "Or pick an order"}, ("INT-53", "order_id"): {"help": "Or pick a quote"},
    ("INT-54", "quote_id"): {"help": "Or pick an order"}, ("INT-56", "quote_id"): {"help": "Or pick an order"},
    ("INT-69", "quote_id"): {"help": "Or pick an order"}, ("INT-69", "order_id"): {"help": "Or pick a quote"},
    ("INT-58", "party_id"): {"help": "Or pick an order"}, ("INT-58", "amount"): {"help": "Blank = the order's amount due"},
    ("INT-65", "name"): {"label": "Agreement name", "placeholder": "Blank = customer and product"},
    ("INT-67", "reason"): {"label": "Why is it coming back?"}, ("INT-61", "reason"): {"label": "Cancellation reason"},
    ("INT-60", "reason"): {"label": "Hold reason"}, ("INT-62", "reason"): {"label": "Why the change"},
    ("INT-68", "line"): {"label": "For line", "help": "Optional - the line it installs"},
}

REQUIRED: dict[str, list[str]] = {
    "INT-51": [], "INT-52": ["line"], "INT-53": [], "INT-54": [], "INT-55": ["order_id"], "INT-56": [], "INT-57": ["order_id", "payment_method"],
    "INT-58": [], "INT-59": ["order_id"], "INT-60": ["order_id", "hold_action"], "INT-61": ["order_id", "reason"],
    "INT-62": ["order_id", "changes"], "INT-63": ["order_id", "changes"], "INT-64": ["order_id"], "INT-65": ["quote_id"],
    "INT-66": ["quote_id"], "INT-67": ["order_id", "product", "reason"], "INT-68": ["order_id", "template"], "INT-69": ["promotion"],
    "INT-70": ["order_id"], "INT-71": ["order_id"],
}

# One of these must be given.
EITHER: dict[str, tuple[str, ...]] = {
    "INT-51": ("quote_id", "order_id"), "INT-52": ("quote_id", "order_id"), "INT-53": ("quote_id", "order_id"),
    "INT-54": ("quote_id", "order_id"), "INT-56": ("quote_id", "order_id"), "INT-58": ("order_id", "party_id"),
    "INT-69": ("quote_id", "order_id"),
}
EITHER_PRODUCT = {"INT-51": ("product", "favorite")}


def missing(code: str, entities: dict[str, Any]) -> list[str]:
    out = []
    for group in (EITHER.get(code), EITHER_PRODUCT.get(code)):
        if group and not any(entities.get(k) not in (None, "", []) for k in group):
            out.append(" or ".join(group))
    return out


FLOW_FIELDS: dict[str, list[str]] = {code: list(spec[5]) for code, spec in SPECS.items()}

_N = r"\s*#?\s*(?:SO-0*)?(\d+)"
_DET = r"(?:the\s+|a\s+|an\s+|this\s+|that\s+)*"
RULES: list[tuple[str, re.Pattern]] = [
    ("INT-63", re.compile(r"\bfollow[\s-]?on\s+order\b", re.I)),
    ("INT-62", re.compile(r"\bsupplemental\s+order\b|\brevise\s+(?:the\s+|this\s+)?(?:submitted|open|in[\s-]flight)\s+order\b"
                          r"|\bamend\s+(?:the\s+|this\s+)?(?:sales\s+)?order\b", re.I)),
    ("INT-61", re.compile(r"\bcancel\w*\s+(?:the\s+|this\s+)?(?:sales\s+order|(?:un)?submitted\s+order)\b"
                          r"|\bcancel\w*\b[^.]{0,30}\border\b[^.]{0,30}\bbefore\s+it\s+ships?\b|\bcancel\w*\s+SO-\d+", re.I)),
    ("INT-59", re.compile(r"\bsubmit\s+(?:the\s+|this\s+)?(?:sales\s+)?order\b|\bplace\s+(?:the\s+|this\s+)?order" + _N + r"\b"
                          r"|\b(?:approve|reject)\s+(?:the\s+|this\s+)?(?:submitted|sales)\s+order\b", re.I)),
    ("INT-60", re.compile(r"\b(?:put|place)\b[^.]{0,30}\bon\s+hold\b|\bhold\s+(?:the\s+|this\s+)?order\b"
                          r"|\brelease\b[^.]{0,30}\bfrom\s+hold\b|\b(?:remove|lift)\s+(?:the\s+)?hold\b", re.I)),
    ("INT-64", re.compile(r"\bship\s+(?:the\s+|this\s+)?(?:sales\s+)?(?:order|equipment|goods|items?)\b"
                          r"|\bproof\s+of\s+delivery\b|\border" + _N + r"\b[^.]{0,30}\b(?:was\s+|has\s+been\s+)?delivered\b"
                          r"|\bdeliver(?:ed|y)\s+(?:of\s+|for\s+)?(?:the\s+)?order\b", re.I)),
    ("INT-55", re.compile(r"\bship(?:ping)?\s+to\b|\bshipping\s+(?:address|method|details?|info)\b|\bset\s+(?:the\s+)?shipping\b"
                          r"|\bfreight\s+for\b", re.I)),
    ("INT-54", re.compile(r"\b(?:check|confirm)\s+(?:the\s+)?(?:stock\s+)?availability\b|\bavailable[\s-]to[\s-]promise\b|\bATP\b", re.I)),
    ("INT-56", re.compile(r"\b(?:calculate|compute|work\s+out|add)\s+(?:the\s+)?tax(?:es)?\b|\btax\s+(?:for|on)\s+(?:the\s+)?(?:quote|order)\b", re.I)),
    ("INT-58", re.compile(r"\bcredit[\s-]check\b|\bcheck\s+(?:the\s+|their\s+|customer'?s?\s+)?credit\b", re.I)),
    ("INT-57", re.compile(r"\b(?:add|take|record|enter|capture)\s+(?:a\s+|the\s+)?payment\b|\bpay(?:ment)?\s+(?:by|via|with)\b"
                          r"|\bcard\s+ending\b|\bpay\s+for\s+(?:the\s+)?order\b", re.I)),
    ("INT-53", re.compile(r"\b(?:verify|validate)\s+(?:the\s+|this\s+)?(?:quote|quotation|order|sales\s+order)\b", re.I)),
    ("INT-52", re.compile(r"\bdiscount\b[^.]{0,60}\bline\b|\bline\b[^.]{0,60}\b(?:discount|\d+(?:\.\d+)?\s*%\s*off)\b"
                          r"|\b\d+(?:\.\d+)?\s*%\s*off\s+(?:the\s+)?[\w -]{1,40}?\s+line\b", re.I)),
    ("INT-69", re.compile(r"\b(?:apply|use|add)\b[^.]{0,30}\b(?:promotion|promo(?:\s+code)?|coupon)\b", re.I)),
    ("INT-65", re.compile(r"\bauto\s*-?contract\b|\b(?:agreement|contract)\b[^.]{0,40}\bfrom\s+(?:the\s+|this\s+)?quote\b"
                          r"|\bconvert\w*\b[^.]{0,30}\bquote\b[^.]{0,30}\b(?:agreement|contract)\b", re.I)),
    ("INT-66", re.compile(r"\bupdate\s+(?:the\s+)?opportunity\s+(?:from|with)\s+(?:the\s+)?quote\b"
                          r"|\b(?:forecast|revenue)\b[^.]{0,30}\bfrom\s+(?:the\s+)?quote\b", re.I)),
    ("INT-67", re.compile(r"\breturn\b[^.]{0,60}\b(?:on|from)\s+(?:the\s+)?order\b|\bRMA\b|\breturn\s+authori[sz]ation\b", re.I)),
    ("INT-68", re.compile(r"\bactivity\s+plan\b|\b(?:add|attach)\b[^.]{0,30}\bactivities\b[^.]{0,20}\border\b", re.I)),
    ("INT-70", re.compile(r"\b(?:resume|reactivate|unsuspend|un-suspend)\b[^.]{0,30}\b(?:service|order)\b", re.I)),
    ("INT-71", re.compile(r"\btrack\s+(?:the\s+|this\s+)?(?:order|shipment)\b|\bwhere\s+is\s+(?:the\s+|my\s+|this\s+)?order\b"
                          r"|\b(?:status\s+of|order\s+status\s+(?:of|for))\s+(?:the\s+)?order\b", re.I)),
    ("INT-51", re.compile(r"\b(?:add|include)\b[^.]{0,60}\bto\s+(?:the\s+|this\s+)?(?:quote|quotation|order|sales\s+order)" + _N + r"\b"
                          r"|\badd\b[^.]{0,40}\bfavou?rite\b", re.I)),
]


def _num(pattern: str, text: str) -> int | None:
    m = re.search(pattern, text, re.I)
    return int(m.group(1)) if m else None


def _money(text: str) -> float | None:
    m = re.search(r"(?:₹|rs\.?|sar|usd|\$|for|at|price|amount)\s*([\d,]+(?:\.\d+)?)", text, re.I)
    return float(m.group(1).replace(",", "")) if m else None


def _after_dash(text: str) -> str | None:
    m = re.search(r"\s[-–—:]\s+(.+)$|\bbecause\b\s+(.+)$|\breason:?\s+(.+)$", text, re.I)
    return next((g.strip(" .") for g in m.groups() if g), None) if m else None


def extract(code: str, text: str, fields: dict[str, Any], loose: dict[str, Any]) -> dict[str, Any]:
    """Pull the fields of one of these intents out of free text."""
    e: dict[str, Any] = {}

    def take(*keys: str) -> None:
        for k in keys:
            v = fields.get(k)
            if v not in (None, ""):
                e[k] = v

    take(*SPECS[code][5])
    if "order_id" in SPECS[code][5] and "order_id" not in e and (n := _num(r"\b(?:sales\s+)?order" + _N, text)):
        e["order_id"] = n
    if "quote_id" in SPECS[code][5] and "quote_id" not in e and (n := _num(r"\b(?:quote|quotation)\s*#?\s*(\d+)", text)):
        e["quote_id"] = n
    if code == "INT-51":
        if m := re.search(r"\b(?:add|include)\s+(?:the\s+)?(.+?)\s+favou?rite\b", text, re.I):
            e.setdefault("favorite", m.group(1).strip())
        elif m := re.search(r"\b(?:add|include)\s+(?:(\d+(?:\.\d+)?)\s*(?:x|×)?\s+)?(?:an?\s+)?(.+?)\s+to\s+(?:the\s+|this\s+)?(?:quote|quotation|order|sales\s+order)\b",
                            text, re.I):
            if m.group(1):
                e.setdefault("quantity", float(m.group(1)))
            e.setdefault("product", m.group(2).strip())
        if (p := _money(text)) is not None:
            e.setdefault("unit_price", p)
        if re.search(r"\b(one[\s-]?time|once)\b", text, re.I):
            e.setdefault("price_type", "one_time")
        elif re.search(r"\b(monthly|per\s+month|a\s+month|recurring)\b", text, re.I):
            e.setdefault("price_type", "recurring")
    elif code == "INT-52":
        if m := re.search(r"(\d+(?:\.\d+)?)\s*%", text):
            e.setdefault("discount_pct", float(m.group(1)))
        if m := re.search(r"\b(?:discount\s+(?:the\s+)?|off\s+the\s+)(.+?)\s+line\b", text, re.I):
            e.setdefault("line", m.group(1).strip())
        elif m := re.search(r"\bline\s+(\d+)\b", text, re.I):
            e.setdefault("line", m.group(1))
        if (j := _after_dash(text)) and "discount_justification" not in e:
            e["discount_justification"] = j
    elif code == "INT-54":
        if re.search(r"\bsplit\b", text, re.I):
            e["split"] = True
    elif code == "INT-55":
        if m := re.search(r"\bship(?:ping)?\s+to\s+(.+?)(?:\s+for\s+(?:the\s+)?order\b|\s+(?:by|via|using)\s+\w+|[.;]|$)", text, re.I):
            e.setdefault("address", m.group(1).strip(" ,"))
        if m := re.search(r"\b(?:by|via|using)\s+([A-Z][\w-]*)", text):
            e.setdefault("shipping_method", m.group(1))
        if m := re.search(r"\b(?:by|on|for)\s+(\d{4}-\d{2}-\d{2})\b", text):
            e.setdefault("requested_date", m.group(1))
    elif code == "INT-56":
        if m := re.search(r"\b(?:in|for)\s+(?:the\s+)?([A-Z][\w ]{1,30}?)\s+(?:jurisdiction|region)\b", text):
            e.setdefault("jurisdiction", m.group(1).strip())
    elif code == "INT-57":
        if re.search(r"\bcard\b", text, re.I):
            e.setdefault("payment_method", "credit_card")
            if m := re.search(r"\bending(?:\s+in)?\s+(\d{4})\b", text, re.I):
                e.setdefault("card_last4", m.group(1))
            if m := re.search(r"\b(visa|mastercard|amex|mada)\b", text, re.I):
                e.setdefault("card_brand", m.group(1).lower())
        elif re.search(r"\b(purchase\s+order|PO)\b", text):
            e.setdefault("payment_method", "purchase_order")
            if m := re.search(r"\b(PO[-/#]?[\w-]*\d[\w-]*)", text):
                e.setdefault("po_number", m.group(1))
        elif m := re.search(r"\b(bank\s+transfer|transfer|on\s+account|account|cash|cheque|check)\b", text, re.I):
            e.setdefault("payment_method", m.group(1).lower().replace(" ", "_"))
        if (p := _money(text)) is not None:
            e.setdefault("amount", p)
        if m := re.search(r"\b(net\s*\d+)\b", text, re.I):
            e.setdefault("payment_terms", m.group(1).title())
    elif code == "INT-58":
        if "party_id" not in e and (n := _num(r"\b(?:customer|account|party)\s*#?\s*(\d+)", text)):
            e["party_id"] = n
        if (p := _money(text)) is not None:
            e.setdefault("amount", p)
    elif code == "INT-59":
        if re.search(r"\breject\b", text, re.I):
            e["decision"] = "reject"
        elif re.search(r"\bapprove\b", text, re.I):
            e["decision"] = "approve"
    elif code == "INT-60":
        e.setdefault("hold_action", "release" if re.search(r"\b(release|remove|lift)\b", text, re.I) else "hold")
        if (r := _after_dash(text)) and "reason" not in e:
            e["reason"] = r
    elif code == "INT-61":
        if (r := _after_dash(text)) and "reason" not in e:
            e["reason"] = r
    elif code in ("INT-62", "INT-63"):
        if m := re.search(r":\s*(.+?)(?:\s+(?:from|on)\s+(\d{4}-\d{2}-\d{2}))?\s*$", text):
            e.setdefault("changes", m.group(1).strip(" ."))
            if m.group(2):
                e.setdefault("requested_date", m.group(2))
        if code == "INT-63" and (m := re.search(r"\b(?:from|on|dated)\s+(\d{4}-\d{2}-\d{2})\b", text)):
            e.setdefault("requested_date", m.group(1))
    elif code == "INT-64":
        e.setdefault("ship_action", "deliver" if re.search(r"\bdeliver(ed|y)?\b|\bproof\s+of\s+delivery\b", text, re.I)
                     and not re.search(r"\bship\s+(?:the\s+|this\s+)?(?:sales\s+)?(?:order|equipment|goods|items?)\b", text, re.I) else "ship")
        if m := re.search(r"\bfrom\s+(?:the\s+)?([A-Z][\w ]{2,40}?)(?:\s+(?:with|via|by)\b|[.,;]|$)", text):
            e.setdefault("location", m.group(1).strip())
        if m := re.search(r"\b(?:with|via|by)\s+([A-Z][\w-]+)", text):
            e.setdefault("carrier", m.group(1))
        if m := re.search(r"\btracking\s+(?:number\s+|no\.?\s+|#\s*)?([\w-]+)", text, re.I):
            e.setdefault("tracking_number", m.group(1))
    elif code == "INT-65":
        if m := re.search(r"\b(\d+)\s*(?:months?|mo)\b", text, re.I):
            e.setdefault("term_months", int(m.group(1)))
        elif m := re.search(r"\b(\d+)\s*years?\b", text, re.I):
            e.setdefault("term_months", 12 * int(m.group(1)))
        if m := re.search(r"\b(\d+(?:\.\d+)?)\s*(?:-\s*)?(?:hour|hr|h)s?\b[^.]{0,15}\bresponse\b|\bresponse\b[^.\d]{0,15}(\d+(?:\.\d+)?)\s*(?:hour|hr|h)", text, re.I):
            e.setdefault("response_hours", float(m.group(1) or m.group(2)))
        if re.search(r"\b24\s*[x/]\s*7\b", text, re.I):
            e.setdefault("coverage", "24x7")
    elif code == "INT-67":
        if m := re.search(r"\breturn\s+(?:(\d+)\s*(?:x|×)?\s+)?(?:the\s+|an?\s+)?(.+?)\s+(?:on|from)\s+(?:the\s+)?order\b", text, re.I):
            if m.group(1):
                e.setdefault("quantity", int(m.group(1)))
            e.setdefault("product", m.group(2).strip())
        if (r := _after_dash(text)) and "reason" not in e:
            e["reason"] = r
        if re.search(r"\bno\s+(refund|credit)\b", text, re.I):
            e["refund"] = False
    elif code == "INT-68":
        if m := re.search(r"\b(?:add|attach|use)\s+(?:the\s+)?(.+?)\s+activity\s+plan\b", text, re.I):
            e.setdefault("template", m.group(1).strip())
        elif m := re.search(r"\bactivity\s+plan\s+(?:of\s+|from\s+)?(?:the\s+)?['\"]?([^'\"]+?)['\"]?\s+(?:to|for|on)\s+(?:the\s+)?order\b", text, re.I):
            e.setdefault("template", m.group(1).strip())
    elif code == "INT-69":
        if m := re.search(r"\b(?:promotion|promo(?:\s+code)?|coupon)\s+['\"]?([\w-]+)", text, re.I):
            e.setdefault("promotion", m.group(1))
    return e


NEXT: dict[str, list[tuple[str, str]]] = {
    "INT-51": [("INT-53", "Verify it"), ("INT-52", "Discount a line"), ("INT-54", "Check availability")],
    "INT-52": [("INT-53", "Verify it")],
    "INT-54": [("INT-55", "Set the shipping details")],
    "INT-55": [("INT-56", "Calculate tax")],
    "INT-56": [("INT-57", "Add a payment")],
    "INT-57": [("INT-59", "Submit the order")],
    "INT-58": [("INT-59", "Submit the order")],
    "INT-53": [("INT-59", "Submit the order")],
    "INT-59": [("INT-64", "Ship the equipment"), ("INT-68", "Add the installation activities"), ("INT-71", "Track it")],
    "INT-60": [("INT-71", "Track the order")],
    "INT-62": [("INT-71", "Track the revised order")],
    "INT-63": [("INT-59", "Submit the follow-on order")],
    "INT-64": [("INT-71", "Track the order")],
    "INT-65": [("INT-24", "Create the order")],
    "INT-66": [("INT-06", "Send the quote for approval")],
    "INT-69": [("INT-53", "Verify it")],
}

# Generated training templates (appended at the END of training_data._T).
TRAINING: list[tuple[str, list[tuple[str, dict[str, Any]]]]] = [
    ("INT-51", [("Add {n} x Router CPE to quote {id}", {"quote_id": "{id}", "product": "Router CPE", "quantity": "{n}"}),
                ("add the Branch office kit favorite to order {id}", {"order_id": "{id}", "favorite": "Branch office kit"})]),
    ("INT-52", [("Discount the Router CPE line on quote {id} by {pct}% - multi-site deal",
                 {"quote_id": "{id}", "line": "Router CPE", "discount_pct": "{pct}", "discount_justification": "multi-site deal"})]),
    ("INT-53", [("Verify quote {id}", {"quote_id": "{id}"}), ("validate order {id}", {"order_id": "{id}"})]),
    ("INT-54", [("Check availability for order {id}", {"order_id": "{id}"}), ("check stock availability on quote {id} and split", {"quote_id": "{id}", "split": True})]),
    ("INT-55", [("Ship to the Riyadh office for order {id} by Express", {"order_id": "{id}", "address": "the Riyadh office", "shipping_method": "Express"})]),
    ("INT-56", [("Calculate tax for order {id}", {"order_id": "{id}"})]),
    ("INT-57", [("Add a payment by card ending 4242 for order {id}", {"order_id": "{id}", "payment_method": "credit_card", "card_last4": "4242"}),
                ("payment by purchase order PO-{n} for order {id}", {"order_id": "{id}", "payment_method": "purchase_order", "po_number": "PO-{n}"})]),
    ("INT-58", [("Credit check for order {id}", {"order_id": "{id}"})]),
    ("INT-59", [("Submit order {id}", {"order_id": "{id}"}), ("approve the submitted order {id}", {"order_id": "{id}", "decision": "approve"})]),
    ("INT-60", [("Put order {id} on hold - waiting for site access", {"order_id": "{id}", "hold_action": "hold", "reason": "waiting for site access"}),
                ("release order {id} from hold", {"order_id": "{id}", "hold_action": "release"})]),
    ("INT-61", [("Cancel sales order {id} - customer changed their mind", {"order_id": "{id}", "reason": "customer changed their mind"})]),
    ("INT-62", [("Supplemental order for order {id}: add {n} x Router CPE", {"order_id": "{id}", "changes": "add {n} x Router CPE"})]),
    ("INT-63", [("Follow-on order for order {id}: add 1 x Router CPE", {"order_id": "{id}", "changes": "add 1 x Router CPE"})]),
    ("INT-64", [("Ship order {id} from Central Warehouse", {"order_id": "{id}", "ship_action": "ship", "location": "Central Warehouse"}),
                ("order {id} delivered", {"order_id": "{id}", "ship_action": "deliver"})]),
    ("INT-65", [("Create an agreement from quote {id} for 24 months with 4 hour response",
                 {"quote_id": "{id}", "term_months": 24, "response_hours": 4})]),
    ("INT-66", [("Update the opportunity from quote {id}", {"quote_id": "{id}"})]),
    ("INT-67", [("Return 1 Router CPE on order {id} - dead on arrival", {"order_id": "{id}", "product": "Router CPE", "quantity": 1, "reason": "dead on arrival"})]),
    ("INT-68", [("Add the Fiber installation activity plan to order {id}", {"order_id": "{id}", "template": "Fiber installation"})]),
    ("INT-69", [("Apply promotion ROUTER10 to quote {id}", {"quote_id": "{id}", "promotion": "ROUTER10"})]),
    ("INT-70", [("Resume order {id}", {"order_id": "{id}"}), ("reactivate the suspended service on order {id}", {"order_id": "{id}"})]),
    ("INT-71", [("Track order {id}", {"order_id": "{id}"}), ("where is order {id}", {"order_id": "{id}"})]),
]

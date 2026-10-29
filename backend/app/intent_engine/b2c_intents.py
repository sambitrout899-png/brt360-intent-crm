"""B2C direct-order intents (crm_core/b2c_order_service.py does the work).

  INT-104 Place a B2C order - a B2C prospect or consumer goes straight to an
          order: no lead, no opportunity, no quote, no Presales. The Order
          Module checks feasibility (service at the installation address,
          eligibility, availability, credit), then submits and books it.
          Any channel: IVR, retail point, store, WhatsApp, SMS, USSD, web, app.
  INT-105 Log an order interaction - the same order touched on another
          channel (a WhatsApp follow-up, a store visit, an IVR status call);
          its status comes back, and the order's channel timeline grows.

Like the Order Management, Communications and Configuration intents they are
recognised by phrase rules before any model runs, so the trained model's
prompt stays unchanged; their training examples are appended last.
"""
from __future__ import annotations

import re
from typing import Any

from app.crm_core.b2c_order_service import ALIASES, CHANNELS

CODES = ("INT-104", "INT-105", "INT-106", "INT-107", "INT-108")

# code -> (label, description, category, icon, target, fields, examples)
SPECS: dict[str, tuple[str, str, str, str, str | None, list[str], list[str]]] = {
    "INT-107": ("Change an asset", "Create a separate change order from an installed asset - upgrade / downgrade, product or promotion change, "
                "suspend, resume, terminate, relocation, account transfer, MNP port-in / port-out. The asset updates only after fulfillment.",
                "orders", "🔧", "order",
                ["asset_id", "asset_operation", "offering_name", "quantity", "pricing_attributes", "technical_attributes", "requested_discount_pct",
                 "target_service_account_id", "target_site", "port_number", "donor_operator", "order_id", "target_party_id", "promotion",
                 "recipient_operator", "waive_penalty", "due_date"], ["Modify an installed asset", "Upgrade asset 4 to 500 Mbps", "Port out number 9876543210 to another operator"]),
    "INT-108": ("Confirm order fulfillment", "Record fulfillment completion and assetize the order lines. Only confirm after orchestration has completed.", "orders", "✅", "order", ["order_id"], ["Confirm fulfillment of order 12"]),
    "INT-106": ("Order an offering", "Create an order and its line without a quote. Silent uses product defaults and no discount; interactive opens configuration before explicit provisioning submission.",
                "orders", "🛒", "order",
                ["party_id", "offering_name", "service_account_id", "order_mode", "quantity", "pricing_attributes", "technical_attributes", "requested_discount_pct", "submit_for_provisioning"],
                ["Order an offering for customer 12", "Order an offering in silent mode"]),
    "INT-104": ("Place a B2C order", "A consumer's order straight from the prospect - no lead, opportunity or quote. The Order Module checks "
                "feasibility (service at the address, eligibility, stock, credit) and books it. From any channel: IVR, retail point, store, "
                "chat, SMS, USSD, web, app.",
                "orders", "🛒", "order",
                ["party_id", "products", "order_channel", "channel_reference", "installation_address", "payment_method", "card_last4"],
                ["Place a B2C order for customer 12: Home Fiber 100 at 4 Park Street, Kolkata via chat",
                 "Book a home broadband order for prospect 9 at the store - Home Fiber 100 and Router CPE"]),
    "INT-105": ("Log an order interaction", "The customer came back about an order on another channel - a chat message, a store visit, an "
                "IVR call - it is added to the order's channel timeline and the order's status comes back.",
                "orders", "💬", "order", ["order_id", "party_id", "order_channel", "interaction", "channel_reference", "notes"],
                ["Customer asked about order 7 on chat", "Log a store visit for order 7 - came to pick up the router"]),
}
LABELS: dict[str, str] = {code: spec[0] for code, spec in SPECS.items()}

LIB: dict[str, dict[str, Any]] = {
    "service_account_id": {"label": "Service account number", "type": "number", "help": "Select the service account from order enrichment. It must belong to this customer."},
    "asset_id": {"label": "Installed asset number", "type": "number"},
    "asset_operation": {"label": "Change operation", "type": "picklist", "values": ["modify", "disconnect", "terminate", "suspend", "resume", "relocate", "customer_transfer", "number_portability",
                                                                                    "product_change", "promotion_change", "port_out"], "default": "modify",
                        "help": "modify = upgrade / downgrade; number_portability = MNP port-in; port_out = MNP port-out; customer_transfer = account transfer"},
    "target_service_account_id": {"label": "Destination service account number", "type": "number",
                                  "help": "Optional - relocation makes one for the new site, an account transfer one for the new owner"},
    "target_site": {"label": "Destination site", "help": "Relocation: the new address"},
    "port_number": {"label": "Number to port"},
    "donor_operator": {"label": "Donor operator", "help": "MNP port-in: the operator the number comes from"},
    "recipient_operator": {"label": "Recipient operator", "help": "MNP port-out: the operator the number moves to"},
    "target_party_id": {"label": "New owner (customer number)", "type": "number", "help": "Account transfer: the customer taking the service over"},
    "promotion": {"label": "Promotion", "help": "Promotion change: its code or name - 'none' removes the current one"},
    "waive_penalty": {"label": "Waive the early termination charge", "type": "boolean", "default": False,
                      "help": "Breaking a promotion's commitment early adds its penalty to the change order - tick to waive it"},
    "order_mode": {"label": "Order mode", "type": "picklist", "values": ["interactive", "silent"], "default": "interactive",
                   "help": "Silent submits with product defaults and zero discount. Interactive waits for your explicit submission."},
    "submit_for_provisioning": {"label": "Submit configured order", "type": "boolean", "default": False},
    "order_channel": {"label": "Channel", "type": "picklist", "values": list(CHANNELS), "default": "store",
                      "help": "Where the order came in - " + ", ".join(CHANNELS.values())},
    "channel_reference": {"label": "Channel reference", "placeholder": "e.g. IVR call id, chat conversation, POS terminal",
                          "help": "Links this order to the conversation or transaction on that channel"},
    "installation_address": {"label": "Installation address", "placeholder": "e.g. 4 Park Street, Kolkata",
                             "help": "The Order Module checks service feasibility here"},
    "interaction": {"label": "What happened", "type": "picklist",
                    "values": ["enquiry", "status_check", "update", "payment", "appointment", "complaint", "cancellation_request"],
                    "default": "enquiry"},
}
OVERRIDES: dict[tuple[str, str], dict[str, Any]] = {
    ("INT-106", "requested_discount_pct"): {"label": "Order line discount (%)", "default": 0},
    ("INT-104", "party_id"): {"label": "B2C customer", "help": "A B2C prospect or consumer - no lead, opportunity or quote is needed"},
    ("INT-104", "products"): {"label": "Products", "help": "Services sold to B2C customers, and equipment such as a router"},
    ("INT-105", "notes"): {"label": "What the customer said or asked"},
}
# Change an asset: each operation shows only the details it uses
_WHEN_OP = {
    "quantity": ["modify", "product_change"], "pricing_attributes": ["modify", "product_change"],
    "technical_attributes": ["modify", "product_change"], "requested_discount_pct": ["modify", "product_change"],
    "target_site": ["relocate"], "target_service_account_id": ["relocate", "customer_transfer"],
    "target_party_id": ["customer_transfer"], "port_number": ["number_portability", "port_out"],
    "donor_operator": ["number_portability"], "recipient_operator": ["port_out"], "promotion": ["promotion_change"],
    "waive_penalty": ["disconnect", "terminate", "port_out", "promotion_change"],
}
for _key, _ops in _WHEN_OP.items():
    OVERRIDES[("INT-107", _key)] = {**OVERRIDES.get(("INT-107", _key), {}), "show_when": {"field": "asset_operation", "in": _ops}}

REQUIRED: dict[str, list[str]] = {"INT-104": ["party_id", "products", "order_channel"], "INT-105": ["order_channel"]}
REQUIRED["INT-106"] = ["party_id", "offering_name"]
REQUIRED["INT-107"] = ["asset_operation"]
REQUIRED["INT-108"] = ["order_id"]
EITHER: dict[str, list[tuple[str, ...]]] = {"INT-105": [("order_id", "party_id")], "INT-107": [("asset_id", "order_id")]}


def missing(code: str, entities: dict[str, Any]) -> list[str]:
    return [" or ".join(g) for g in EITHER.get(code, []) if not any(entities.get(k) not in (None, "", []) for k in g)]


_CH = r"(?:ivr|whats\s?app|chat|sms|text\s+message|ussd|the\s+store|a\s+store|store\s+visit|walk-?in|retail(?:\s+(?:sales\s+)?point|\s+pos)?|pos|web(?:site)?|online|(?:mobile\s+|self-?care\s+)?app|call\s+cent(?:re|er)|field\s+agent)"
_OPS = r"(?:upgrade|downgrade|modify|change(?!\s+requests?\b)|switch|suspend|resume|reactivate|terminate|disconnect|cancel|cease|relocat\w*|move|shift|transfer|port)"
_SERVICE = r"(?:asset|service|connection|line|subscription|order)"
# Post-order changes to an installed service. "Upgrade this service. order_id: 5, new_price: ..." stays INT-08
# (Change a live service) and "Suspend this service. order_id: 5" INT-09 - these need an asset, MNP, a new
# site / owner / promotion / product, or a speed to move to.
_ASSET_CHANGE = re.compile(
    rf"\b{_OPS}\w*\b[^.]{{0,40}}\basset\s*#?\s*\d+|\basset\s*#?\s*\d+\b[^.]{{0,30}}\b{_OPS}"
    r"|\bmnp\b|\bport(?:ing)?[\s-]+(?:in|out)\b|\bnumber\s+portability\b|\bport\s+(?:the\s+|my\s+|a\s+)?(?:mobile\s+)?number\b"
    rf"|\b(?:relocat\w*|shift|move)\b[^.]{{0,40}}\b{_SERVICE}\b[^.]{{0,30}}\bto\s+\S"
    rf"|\b(?:account|ownership)\s+transfer\b|\btransfer\b[^.]{{0,30}}\b(?:{_SERVICE}|ownership)\b[^.]{{0,30}}\bto\s+(?:customer|party|account|consumer)\b"
    rf"|\b(?:change|switch|replace|swap|remove|update)\b[^.]{{0,20}}\bpromotion\b[^.]{{0,40}}\b(?:on|of|for)\s+(?:the\s+|this\s+)?{_SERVICE}"
    rf"|\b(?:upgrade|downgrade)\b[^.]{{0,40}}\bto\s+\d+(?:\.\d+)?\s*(?:k|m|g)bps\b|\bdowngrade\b[^.]{{0,40}}\b{_SERVICE}"
    rf"|\b(?:change|switch|migrate)\s+(?:the\s+)?(?:product|plan|tariff)\b[^.]{{0,40}}\b(?:of|on|for)\s+(?:the\s+|this\s+)?{_SERVICE}",
    re.I)

RULES: list[tuple[str, re.Pattern]] = [
    ("INT-107", _ASSET_CHANGE),
    ("INT-106", re.compile(r"\border\s+an?\s+offering\b", re.I)),
    # an interaction on an existing order: "asked about order 7 on chat", "log a store visit for order 7"
    ("INT-105", re.compile(rf"\b(?:log|record|note)\w*\b[^.]{{0,40}}\b(?:interaction|contact|touch-?point|visit|call|chat|message)\b[^.]{{0,40}}\border\s*#?\s*\d"
                           rf"|\b(?:asked|enquired|called|messaged|texted|came\s+back|walked\s+in|visited)\b[^.]{{0,40}}\border\s*#?\s*\d+[^.]{{0,30}}\b(?:on|via|over|through|at|from)\s+{_CH}"
                           rf"|\border\s*#?\s*\d+\b[^.]{{0,30}}\b(?:on|via|over|through|at|from)\s+{_CH}\b[^.]{{0,40}}\b(?:asked|enquir\w*|status|update|follow)", re.I)),
    # a new consumer order: "place a B2C order ...", "book a home broadband order via chat"
    ("INT-104", re.compile(rf"\b(?:place|book|take|capture|create|raise|start|open)\w*\s+(?:a\s+|an\s+|the\s+)?(?:new\s+)?"
                           rf"(?:b2c|consumer|home(?:\s+broadband|\s+fibre|\s+fiber)?|retail|residential|omni-?channel)\s+order\b(?!\s*#?\s*\d)"
                           rf"|\b(?:place|book|take|capture)\w*\s+(?:a\s+|an\s+|the\s+)?(?:new\s+)?order\b(?!\s*#?\s*\d)[^.]{{0,60}}\b(?:via|over|through|on|from|at)\s+{_CH}\b", re.I)),
]


def _channel(text: str) -> str | None:
    lowered = text.lower()
    for word, key in [("whatsapp", "chat"), ("whats app", "chat"), ("chat", "chat"), ("ussd", "ussd"), ("ivr", "ivr"), ("sms", "sms"), ("text message", "sms"),
                      ("retail", "retail_pos"), ("pos", "retail_pos"), ("store", "store"), ("walk-in", "store"), ("walk in", "store"),
                      ("mobile app", "app"), ("self-care app", "app"), (" app", "app"), ("website", "web"), ("online", "web"), (" web", "web"),
                      ("call centre", "call_center"), ("call center", "call_center"), ("field agent", "field_agent")]:
        if re.search(rf"(?<![\w-]){re.escape(word.strip())}(?![\w-])", lowered):
            return key
    return None


_SPEED = r"\d+(?:\.\d+)?\s*(?:k|m|g)bps"


def _asset_change(text: str) -> dict[str, Any]:
    """The operation and its details, from a post-order change said in English."""
    t = " ".join(text.split())
    low = t.lower()
    e: dict[str, Any] = {}
    if m := re.search(r"\basset\s*#?\s*(\d+)\b", t, re.I):
        e["asset_id"] = int(m.group(1))
    if m := re.search(r"\b(?:\d{10,13}|\+\d{10,13})\b", t):
        e["port_number"] = m.group(0)
    operator = r"([A-Z][\w&.-]*(?:\s+[A-Z][\w&.-]*){0,2})"
    if re.search(r"\bport(?:ing)?[\s-]+out\b|\bmnp[\s-]+out\b|\bport\b[^.]{0,40}\bto\s+(?:another|other|a\s+different)?\s*(?:operator|network|[A-Z])", t, re.I) \
            and not re.search(r"\bport(?:ing)?[\s-]+in\b|\bfrom\b", t, re.I):
        e["asset_operation"] = "port_out"
        if m := re.search(rf"\bto\s+(?:the\s+)?(?:operator\s+)?{operator}", t):
            e["recipient_operator"] = m.group(1)
    elif re.search(r"\bport(?:ing)?[\s-]+in\b|\bmnp\b|\bnumber\s+portability\b|\bport\b[^.]{0,40}\bfrom\b", t, re.I):
        e["asset_operation"] = "number_portability"
        if m := re.search(rf"\bfrom\s+(?:the\s+)?(?:operator\s+)?{operator}", t):
            e["donor_operator"] = m.group(1)
    elif "promotion" in low:
        e["asset_operation"] = "promotion_change"
        if re.search(r"\bremove\b|\bno\s+promotion\b|\bdrop\b", low):
            e["promotion"] = "none"
        elif m := re.search(r"\bto\s+([\w-]+(?:\s+[\w-]+){0,3})\s*$", t.rstrip(" ."), re.I) or re.search(r"\bpromotion\s+([A-Z0-9][\w-]*)", t):
            e["promotion"] = m.group(1)
    elif re.search(r"\b(?:account|ownership)\s+transfer\b|\btransfer\b", low):
        e["asset_operation"] = "customer_transfer"
        if m := re.search(r"\bto\s+(?:customer|party|account|consumer)\s*#?\s*(\d+)\b", t, re.I):
            e["target_party_id"] = int(m.group(1))
    elif re.search(r"\b(?:relocat\w*|shift|move)\b", low):
        e["asset_operation"] = "relocate"
        if m := re.search(r"\bto\s+(.+?)\s*$", t.rstrip(" ."), re.I):
            e["target_site"] = m.group(1)
    elif re.search(r"\bresume\b|\breactivate\b|\bunsuspend\b", low):
        e["asset_operation"] = "resume"
    elif "suspend" in low:
        e["asset_operation"] = "suspend"
    elif "terminate" in low:
        e["asset_operation"] = "terminate"
    elif re.search(r"\bdisconnect\b|\bcancel\b|\bcease\b", low):
        e["asset_operation"] = "disconnect"
    else:
        speed = re.search(rf"\bto\s+({_SPEED})\b", t, re.I)
        product = re.search(r"\b(?:to|into)\s+(?:the\s+)?(.+?)\s*$", t.rstrip(" ."), re.I)
        if speed:
            e["asset_operation"] = "modify"
            e["pricing_attributes"] = {"Bandwidth": re.sub(r"\s*([kmg])bps", lambda x: f" {x.group(1).upper()}bps", speed.group(1), flags=re.I)}
        elif re.search(r"\b(?:product|plan|tariff)\b", low) and product:
            e["asset_operation"] = "product_change"
            e["offering_name"] = product.group(1)
        elif re.search(r"\b(?:upgrade|downgrade)\b", low) and product and not re.match(r"\d", product.group(1)):
            e["asset_operation"] = "product_change"  # "upgrade asset 4 to Fiber 900"
            e["offering_name"] = product.group(1)
        else:
            e["asset_operation"] = "modify"
        if m := re.search(r"\bquantity\s*(?:to|of|=|:)?\s*(\d+)\b|\bto\s+(\d+)\s+(?:lines|seats|units|users)\b", t, re.I):
            e["quantity"] = int(m.group(1) or m.group(2))
    return e


def extract(code: str, text: str, fields: dict[str, Any], loose: dict[str, Any]) -> dict[str, Any]:
    e: dict[str, Any] = {k: fields[k] for k in SPECS[code][5] if fields.get(k) not in (None, "")}
    if code == "INT-106":
        e.setdefault("order_mode", "silent" if re.search(r"\bsilent\b", text, re.I) else "interactive")
    for key in ("party_id", "order_id"):
        if key in SPECS[code][5] and key not in e and loose.get(key) not in (None, ""):
            e[key] = loose[key]
    if "party_id" in SPECS[code][5] and "party_id" not in e:
        if m := re.search(r"\b(?:customer|prospect|consumer|party)\s*#?\s*(\d+)\b", text, re.I):
            e["party_id"] = int(m.group(1))
    if "order_id" in SPECS[code][5] and "order_id" not in e and (m := re.search(r"\border\s*#?\s*(\d+)\b", text, re.I)):
        e["order_id"] = int(m.group(1))
    channel = fields.get("order_channel") or fields.get("channel_name")
    if channel:
        key = str(channel).strip().lower()
        e["order_channel"] = key if key in CHANNELS else ALIASES.get(key, key)
    elif found := _channel(text):
        e["order_channel"] = found
    if code == "INT-104":
        if "products" not in e:
            raw = fields.get("product") or fields.get("offering_name") or fields.get("offering")
            if raw:
                e["products"] = [p.strip() for p in str(raw).split(",") if p.strip()]
            elif m := re.search(r":\s*([^:]+?)(?:\s+(?:at|in|to)\s+\d|\s+(?:via|over|through|on|from)\s+" + _CH + r"\b|$)", text, re.I):
                items = [p.strip(" .") for p in re.split(r",|\band\b", m.group(1)) if p.strip(" .")]
                e["products"] = [p for p in items if not re.match(r"^(?:at|via|on)\b", p, re.I)]
        if "installation_address" not in e:
            address = fields.get("address") or fields.get("installation_address")
            if not address and (m := re.search(r"\bat\s+(\d[^.;]*?)(?=\s+(?:via|over|through|on|from)\s+" + _CH + r"\b|[.;]|$)", text, re.I)):
                address = m.group(1)
            if address:
                e["installation_address"] = str(address).strip(" ,")
        if fields.get("payment_method"):
            e["payment_method"] = fields["payment_method"]
    if code == "INT-107":
        e.update({k: v for k, v in _asset_change(text).items() if k not in e})
    if code == "INT-105":
        lowered = text.lower()
        e.setdefault("interaction", "status_check" if "status" in lowered or "where is" in lowered
                     else "payment" if "pay" in lowered else "complaint" if "complain" in lowered
                     else "cancellation_request" if "cancel" in lowered else "appointment" if "appointment" in lowered else "enquiry")
        if "notes" not in e:
            note = re.sub(r"^\s*(?:log|record|note)\s+(?:a|an|the)?\s*", "", text, flags=re.I).strip(" .")
            e["notes"] = note[:300]
    return e


NEXT: dict[str, list[tuple[str, str]]] = {
    "INT-104": [("INT-25", "Start fulfilment - installation, activation, billing"), ("INT-57", "Take a payment"),
                ("INT-105", "Log the customer's next contact, on any channel")],
    "INT-105": [("INT-71", "Track the order"), ("INT-105", "Log another interaction")],
}

# Generated training templates (appended at the END of training_data._T).
TRAINING: list[tuple[str, list[tuple[str, dict[str, Any]]]]] = [
    ("INT-104", [
        ("Place a B2C order for customer {id}: {product} at 4 Park Street, Kolkata via chat",
         {"party_id": "{id}", "products": ["{product}"], "installation_address": "4 Park Street, Kolkata", "order_channel": "chat"}),
        ("book a home order for prospect {id} at the store: {product}", {"party_id": "{id}", "products": ["{product}"], "order_channel": "store"}),
        ("Take a consumer order over IVR for customer {id}: {product}", {"party_id": "{id}", "products": ["{product}"], "order_channel": "ivr"}),
    ]),
    ("INT-105", [
        ("Customer asked about order {id} on chat", {"order_id": "{id}", "order_channel": "chat", "interaction": "enquiry"}),
        ("Log a store visit for order {id} - came to pick up the router", {"order_id": "{id}", "order_channel": "store", "interaction": "enquiry"}),
        ("order {id} via USSD - customer checked the status", {"order_id": "{id}", "order_channel": "ussd", "interaction": "status_check"}),
    ]),
    ("INT-107", [
        ("Upgrade asset {id} to 500 Mbps", {"asset_id": "{id}", "asset_operation": "modify", "pricing_attributes": {"Bandwidth": "500 Mbps"}}),
        ("Downgrade asset {id} to 100 Mbps", {"asset_id": "{id}", "asset_operation": "modify", "pricing_attributes": {"Bandwidth": "100 Mbps"}}),
        ("Change the product of asset {id} to {product}", {"asset_id": "{id}", "asset_operation": "product_change", "offering_name": "{product}"}),
        ("Port out number 9876543210 of asset {id} to Northwind Mobile", {"asset_id": "{id}", "asset_operation": "port_out", "port_number": "9876543210",
                                                                           "recipient_operator": "Northwind Mobile"}),
        ("Port in number 9876543210 from Contoso Telecom to asset {id}", {"asset_id": "{id}", "asset_operation": "number_portability", "port_number": "9876543210",
                                                                           "donor_operator": "Contoso Telecom"}),
        ("Transfer asset {id} to customer 7", {"asset_id": "{id}", "asset_operation": "customer_transfer", "target_party_id": 7}),
        ("Relocate asset {id} to 5 MG Road, Bengaluru", {"asset_id": "{id}", "asset_operation": "relocate", "target_site": "5 MG Road, Bengaluru"}),
        ("Change the promotion on asset {id} to SUMMER25", {"asset_id": "{id}", "asset_operation": "promotion_change", "promotion": "SUMMER25"}),
    ]),
]

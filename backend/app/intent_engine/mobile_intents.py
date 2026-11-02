"""Mobile subscriber intents - prepaid, postpaid, SIM and number lifecycle, wholesale
(crm_core/comm_mobile.py does the work; orchestration/actions_mobile.py builds the steps).

  prepaid    INT-129 Recharge a line (amount or voucher PIN)    INT-132 Transfer balance
             INT-133 Give emergency credit                      INT-131 Check balance & usage
  postpaid   INT-135 Set a spend limit                          INT-148 Take or refund a deposit
             INT-134 Switch prepaid <-> postpaid
  both       INT-136 Activate mobile lines (one or an SME's many)  INT-130 Add or remove a pack
             INT-142 Change a line's plan                       INT-137 Swap a SIM
             INT-138 Change the mobile number                   INT-139 Bar or unbar a line
             INT-140 Turn roaming on or off                     INT-141 Share a plan
             INT-143 Suspend, resume or terminate a line        INT-144 Charge usage on a line
  wholesale  INT-145 Generate recharge vouchers                 INT-146 Onboard a wholesale partner
             INT-147 Settle with a partner

Recognised by phrase rules before any model runs, like the other later intents. Every rule that
could be read as another intent needs a mobile number (8+ digits) in the sentence - "switch
customer 7 to prepaid" stays the billing profile (INT-74), "switch 9100000001 to postpaid" is a line.
"""
from __future__ import annotations

import re
from typing import Any

CODES = tuple(f"INT-{n}" for n in range(129, 149))

# code -> (label, description, category, icon, target, fields, examples)
SPECS: dict[str, tuple[str, str, str, str, str | None, list[str], list[str]]] = {
    "INT-129": ("Recharge a prepaid line", "Top up a prepaid line by card, cash, UPI, wallet or a voucher PIN - its validity is extended, "
                "emergency credit is recovered first and a barring for no balance lifts.", "mobile", "💳", None,
                ["msisdn", "amount", "payment_method", "voucher_pin", "card_last4", "reference", "dealer"],
                ["Recharge 9100000001 with 199 by UPI", "Top up 9100000001 with voucher PIN 12345678901234"]),
    "INT-130": ("Add or remove a pack", "Buy a data, voice, SMS, combo or roaming pack or a value-added service (caller tune, voicemail) "
                "on a line - prepaid pays from the wallet, postpaid on the bill - or cancel one.", "mobile", "📦", None,
                ["msisdn", "pack", "action", "auto_renew"],
                ["Add the DATA10 pack to 9100000001", "Remove the caller tune from 9100000001"]),
    "INT-131": ("Check balance & usage", "A line's wallet and validity (prepaid) or spend against its limit (postpaid), what is left of "
                "its packs and shared plan, barrings and recent usage.", "mobile", "📊", None, ["msisdn"],
                ["What's the balance on 9100000001?", "How much data is left on 9100000001"]),
    "INT-132": ("Transfer balance", "Move prepaid balance from one line to another - within the transfer limit, with the fee.",
                "mobile", "🔀", None, ["msisdn", "to_msisdn", "amount"],
                ["Transfer 50 from 9100000001 to 9100000002"]),
    "INT-133": ("Give emergency credit", "Lend a prepaid line a small balance when it runs out - recovered, with the fee, from its next "
                "recharge.", "mobile", "🆘", None, ["msisdn"], ["Give emergency credit to 9100000001"]),
    "INT-134": ("Switch prepaid / postpaid", "Move a line between prepaid and postpaid, keeping its number and SIM: the prepaid balance "
                "is carried to the bill (or the deposit to the wallet), with the new plan, limit and deposit.", "mobile", "🔄", None,
                ["msisdn", "to_payment_type", "plan", "credit_limit", "deposit"],
                ["Switch 9100000001 to postpaid on POST_399", "Move 9100000002 to prepaid"]),
    "INT-135": ("Set a spend limit", "A postpaid line's credit limit and the % of it that raises a spend alert - past the limit, "
                "outgoing use is barred until the next cycle (bill-shock control).", "mobile", "🛑", None,
                ["msisdn", "credit_limit", "spend_alert_pct"], ["Set the credit limit of 9100000001 to 3000 with an alert at 75%"]),
    "INT-136": ("Activate mobile lines", "New prepaid or postpaid lines for a customer - one, or an SME's lines in one go - with numbers "
                "(a golden number if asked), physical SIMs or eSIMs, the plan, the first recharge or the deposit and credit limit.",
                "mobile", "📱", "party",
                ["party_id", "plan", "payment_type", "quantity", "msisdn", "iccid", "sim_type", "user_name", "wholesale_partner",
                 "initial_recharge", "payment_method", "deposit", "credit_limit"],
                ["Activate 5 postpaid lines on BIZ_599 for customer 7 as eSIMs", "Activate a prepaid line on PRE_SMART for customer 7 with a recharge of 299"]),
    "INT-137": ("Swap a SIM", "A new SIM (or an eSIM) for a lost, stolen, damaged or upgraded one - identity checked first; "
                "international, premium and roaming wait out a cool-down against SIM-swap fraud.", "mobile", "🔁", None,
                ["msisdn", "reason", "identity_verified", "sim_type", "new_iccid", "waive_fee"],
                ["Swap the SIM of 9100000001 to an eSIM - phone lost, identity verified"]),
    "INT-138": ("Change the mobile number", "Give a line a new number (or a chosen golden number) - the old one is quarantined before "
                "anyone else gets it.", "mobile", "🔢", None, ["msisdn", "new_msisdn", "reason", "waive_fee"],
                ["Change the number of 9100000001 to 9100009999"]),
    "INT-139": ("Bar or unbar a line", "Bar a line - lost or stolen, fraud, non-payment, outgoing, incoming, international, premium-rate, "
                "data or roaming - or lift a barring.", "mobile", "⛔", None, ["msisdn", "action", "barring", "reason", "identity_verified"],
                ["Bar 9100000001 - phone stolen", "Unbar international calls on 9100000001"]),
    "INT-140": ("Turn roaming on or off", "Open roaming on a line (credit checked, with a roaming pack if asked) or close it.",
                "mobile", "✈️", None, ["msisdn", "action", "pack"],
                ["Turn on roaming for 9100000001 with ROAM7", "Switch off roaming on 9100000001"]),
    "INT-141": ("Share a plan", "Add a line to - or remove it from - a shared (family / business pool) plan: members draw on the "
                "plan owner's allowances.", "mobile", "👨‍👩‍👧", None, ["msisdn", "member_msisdn", "action"],
                ["Add 9100000002 to the shared plan of 9100000001"]),
    "INT-142": ("Change a line's plan", "Move a line to another plan of the same payment type - prepaid pays the new plan from the wallet, "
                "postpaid gets the unused part of the month credited.", "mobile", "🧾", None, ["msisdn", "plan"],
                ["Change the plan of 9100000001 to BIZ_599"]),
    "INT-143": ("Suspend, resume or terminate a line", "Suspend a line (temporarily), resume it, or terminate it - the prepaid "
                "balance refunded or forfeited, the postpaid deposit applied to the final bill, the number quarantined.",
                "mobile", "⏯️", None, ["msisdn", "action", "reason", "refund_balance"],
                ["Suspend 9100000001 - customer travelling", "Terminate 9100000001 - moving abroad, refund the balance"]),
    "INT-144": ("Charge usage on a line", "Charge a call, SMS or data session online the way the network's charging system asks: packs "
                "first, then the wallet or the bill - refused, with the reason, when the line is barred or out of money.",
                "mobile", "📶", None, ["msisdn", "event_type", "quantity", "international", "premium", "roaming", "destination"],
                ["Charge a 12 minute call on 9100000001", "Charge 500 MB of data on 9100000001 while roaming"]),
    "INT-145": ("Generate recharge vouchers", "A batch of prepaid vouchers of one value, for a dealer or the shops - the PINs are "
                "downloaded once and only their hashes are kept.", "wholesale", "🎟️", None,
                ["count", "face_value", "validity_days", "expires_in_days", "dealer"],
                ["Generate 500 recharge vouchers of 100 for dealer CityMobile"]),
    "INT-146": ("Onboard a wholesale partner", "An MVNO, MVNE, reseller or dealer: brand, wholesale rates per minute / SMS / MB, revenue "
                "share and the number range its lines use - or change one.", "wholesale", "🤝", None,
                ["wholesale_partner", "partner_kind", "brand", "wholesale_rates", "revenue_share_pct", "number_range", "contact_email", "partner_status"],
                ["Onboard MVNO Zoom Mobile with wholesale rates voice 0.2, sms 0.05, data 0.01 and range 9180000000-9180999999"]),
    "INT-147": ("Settle with a partner", "A partner's statement for a period: its lines' usage at the wholesale rates, less the commission "
                "on the recharges it sold.", "wholesale", "🧮", None, ["wholesale_partner", "period_start", "period_end"],
                ["Run the settlement for Zoom Mobile for 2026-10"]),
    "INT-148": ("Take or refund a deposit", "A postpaid line's security deposit - take one (for roaming, a higher limit, a new account) "
                "or return it once the account owes nothing.", "mobile", "🏦", None, ["msisdn", "action", "amount", "payment_method"],
                ["Take a deposit of 1000 on 9100000001", "Refund the deposit on 9100000001"]),
}
LABELS: dict[str, str] = {code: spec[0] for code, spec in SPECS.items()}

LIB: dict[str, dict[str, Any]] = {
    "msisdn": {"label": "Mobile number", "placeholder": "e.g. 9100000001", "help": "The line's number (MSISDN)"},
    "to_msisdn": {"label": "To mobile number", "placeholder": "e.g. 9100000002"},
    "member_msisdn": {"label": "Member's mobile number", "placeholder": "e.g. 9100000002"},
    "new_msisdn": {"label": "New number", "help": "Blank = the next free number; or a chosen (golden) number"},
    "voucher_pin": {"label": "Voucher PIN", "help": "Instead of an amount - the PIN scratched off the voucher"},
    "dealer": {"label": "Dealer", "help": "The partner that sold the recharge or holds the vouchers - their commission"},
    "pack": {"label": "Pack", "placeholder": "e.g. DATA10, ROAM7, TUNE", "help": "A pack's code or name - Mobile -> Catalog"},
    "plan": {"label": "Plan", "placeholder": "e.g. PRE_SMART, POST_399, BIZ_599", "help": "A plan's code or name - Mobile -> Catalog"},
    "auto_renew": {"label": "Renew automatically", "type": "boolean", "default": False},
    "to_payment_type": {"label": "Switch to", "type": "picklist", "values": ["postpaid", "prepaid"]},
    "credit_limit": {"label": "Credit limit", "type": "money", "help": "Postpaid: outgoing use stops when the cycle's spend reaches it (0 = none)"},
    "spend_alert_pct": {"label": "Alert at (% of the limit)", "type": "percent"},
    "deposit": {"label": "Security deposit", "type": "money"},
    "iccid": {"label": "SIM ICCIDs", "type": "list", "help": "Blank = from Inventory's SIM stock (eSIMs are made for you)"},
    "sim_type": {"label": "SIM", "type": "picklist", "values": ["physical", "esim"], "default": "physical"},
    "new_iccid": {"label": "New SIM ICCID", "help": "Blank = from Inventory's SIM stock (eSIMs are made for you)"},
    "user_name": {"label": "Users", "type": "list", "help": "Who uses each line - e.g. the employees of a business account"},
    "wholesale_partner": {"label": "Wholesale partner", "help": "An MVNO, MVNE, reseller or dealer - its brand and number range"},
    "initial_recharge": {"label": "First recharge", "type": "money", "help": "Prepaid: at least the plan's price"},
    "identity_verified": {"label": "Identity verified", "type": "boolean", "default": False, "help": "You checked the customer's ID"},
    "waive_fee": {"label": "Waive the fee", "type": "boolean", "default": False},
    "barring": {"label": "Barring", "type": "picklist",
                "values": ["lost_stolen", "fraud", "non_payment", "outgoing", "incoming", "international", "premium", "data", "roaming"]},
    "refund_balance": {"label": "Refund the prepaid balance", "type": "boolean", "default": False},
    "event_type": {"label": "Usage", "type": "picklist", "values": ["voice", "sms", "data"], "default": "voice"},
    "international": {"label": "International", "type": "boolean", "default": False},
    "premium": {"label": "Premium-rate number", "type": "boolean", "default": False},
    "roaming": {"label": "While roaming", "type": "boolean", "default": False},
    "destination": {"label": "Called number / destination"},
    "count": {"label": "How many", "type": "number"},
    "face_value": {"label": "Value of each", "type": "money"},
    "validity_days": {"label": "Validity (days)", "type": "number", "help": "How long a recharge with it keeps the line valid"},
    "expires_in_days": {"label": "Unused vouchers expire after (days)", "type": "number", "default": 365},
    "partner_kind": {"label": "Partner type", "type": "picklist", "values": ["mvno", "mvne", "reseller", "dealer"]},
    "brand": {"label": "Brand"},
    "wholesale_rates": {"label": "Wholesale rates", "placeholder": "voice 0.2, sms 0.05, data 0.01", "help": "Per minute, per SMS, per MB"},
    "revenue_share_pct": {"label": "Revenue share / commission", "type": "percent"},
    "number_range": {"label": "Number range", "placeholder": "9180000000-9180999999"},
    "contact_email": {"label": "Contact email"},
    "partner_status": {"label": "Status", "type": "picklist", "values": ["active", "suspended"]},
    "period_start": {"label": "From", "placeholder": "YYYY-MM-DD - blank = last month"},
}
_PAY = ["card", "cash", "upi", "wallet_app", "bank_transfer"]
OVERRIDES: dict[tuple[str, str], dict[str, Any]] = {
    ("INT-129", "amount"): {"help": "Or a voucher PIN below"},
    ("INT-129", "payment_method"): {"values": _PAY, "default": "cash"},
    ("INT-129", "card_last4"): {"show_when": {"field": "payment_method", "in": ["card"]}},
    ("INT-130", "action"): {"label": "Add or remove", "values": ["add", "remove"], "default": "add"},
    ("INT-132", "msisdn"): {"label": "From mobile number"},
    ("INT-132", "amount"): {"help": "Within the transfer limit; the fee comes on top"},
    ("INT-134", "plan"): {"label": "New plan", "help": "Blank = the first plan of that payment type"},
    ("INT-136", "party_id"): {"label": "Customer"},
    ("INT-136", "msisdn"): {"label": "Numbers", "type": "list", "help": "Blank = the next free numbers; or chosen (golden) numbers"},
    ("INT-136", "quantity"): {"label": "How many lines", "help": "An SME's lines in one go"},
    ("INT-136", "payment_type"): {"help": "Blank = the plan's"},
    ("INT-136", "payment_method"): {"label": "First recharge / deposit paid by", "values": _PAY, "default": "cash"},
    ("INT-136", "initial_recharge"): {"show_when": {"field": "payment_type", "in": ["", "prepaid"]}},
    ("INT-136", "deposit"): {"show_when": {"field": "payment_type", "in": ["", "postpaid"]}},
    ("INT-136", "credit_limit"): {"show_when": {"field": "payment_type", "in": ["", "postpaid"]}},
    ("INT-137", "reason"): {"type": "picklist", "values": ["lost", "stolen", "damaged", "upgrade", "esim"], "default": "damaged"},
    ("INT-139", "action"): {"label": "Bar or unbar", "values": ["bar", "unbar"], "default": "bar"},
    ("INT-140", "action"): {"label": "Roaming", "values": ["on", "off"], "default": "on"},
    ("INT-140", "pack"): {"label": "Roaming pack", "help": "Optional - e.g. ROAM7"},
    ("INT-141", "msisdn"): {"label": "Plan owner's number", "help": "The line on the shared plan"},
    ("INT-141", "action"): {"label": "Add or remove", "values": ["add", "remove"], "default": "add"},
    ("INT-142", "plan"): {"label": "New plan"},
    ("INT-143", "action"): {"label": "What should happen", "values": ["suspend", "resume", "terminate"]},
    ("INT-143", "refund_balance"): {"show_when": {"field": "action", "in": ["terminate"]}},
    ("INT-144", "quantity"): {"label": "How much", "default": None, "help": "Minutes, messages or MB"},
    ("INT-145", "count"): {"label": "How many vouchers"},
    ("INT-145", "validity_days"): {"default": 30},
    ("INT-146", "wholesale_partner"): {"label": "Partner name"},
    ("INT-147", "wholesale_partner"): {"label": "Partner"},
    ("INT-147", "period_end"): {"label": "To", "placeholder": "YYYY-MM-DD - blank = the end of that month"},
    ("INT-148", "action"): {"label": "Take or refund", "values": ["take", "refund"], "default": "take"},
    ("INT-148", "amount"): {"help": "Refund: blank = the whole deposit"},
    ("INT-148", "payment_method"): {"values": _PAY, "default": "cash"},
}
REQUIRED: dict[str, list[str]] = {
    "INT-129": ["msisdn"], "INT-130": ["msisdn", "pack"], "INT-131": ["msisdn"], "INT-132": ["msisdn", "to_msisdn", "amount"],
    "INT-133": ["msisdn"], "INT-134": ["msisdn"], "INT-135": ["msisdn"], "INT-136": ["party_id", "plan"], "INT-137": ["msisdn"],
    "INT-138": ["msisdn"], "INT-139": ["msisdn", "barring"], "INT-140": ["msisdn"], "INT-141": ["msisdn", "member_msisdn"],
    "INT-142": ["msisdn", "plan"], "INT-143": ["msisdn", "action"], "INT-144": ["msisdn", "event_type", "quantity"],
    "INT-145": ["count", "face_value"], "INT-146": ["wholesale_partner"], "INT-147": ["wholesale_partner"], "INT-148": ["msisdn"],
}
ADMIN_ONLY = {"INT-145", "INT-146"}


def _empty(v: Any) -> bool:
    return v in (None, "", [])


def missing(code: str, entities: dict[str, Any]) -> list[str]:
    if code == "INT-129" and _empty(entities.get("amount")) and _empty(entities.get("voucher_pin")):
        return ["amount"]
    if code == "INT-135" and _empty(entities.get("credit_limit")) and _empty(entities.get("spend_alert_pct")):
        return ["credit_limit"]
    if code == "INT-143" and str(entities.get("action") or "").lower() == "terminate" and _empty(entities.get("reason")):
        return ["reason"]
    if code == "INT-148" and str(entities.get("action") or "take").lower() == "take" and _empty(entities.get("amount")):
        return ["amount"]
    return []


# ------------------------------------------------------------------ phrase rules

_N = r"(?<![\d.])\+?\d{8,15}(?![\d.])"
_HAS_N = rf"(?=.*?{_N})"
_RULE_FLAGS = re.I | re.S
RULES: list[tuple[str, re.Pattern]] = [
    ("INT-145", re.compile(r"\b(?:generate|create|print|issue|make)\w*\b[^.]{0,30}\b(?:recharge\s+|prepaid\s+|scratch\s+)?vouchers?\b"
                           r"|\bvoucher\s+batch\b", re.I)),
    ("INT-146", re.compile(r"\b(?:onboard|add|set\s*up|register|create|sign\s+up|change|update)\w*\b[^.]{0,30}\b(?:mvno|mvne|reseller|dealer|"
                           r"wholesale\s+partner)\b|\bwholesale\s+rates?\b", re.I)),
    ("INT-147", re.compile(r"\b(?:settle|settlement|reconcile)\w*\b[^.]{0,40}\b(?:mvno|mvne|reseller|dealer|partner|wholesale)\b"
                           r"|\b(?:partner|wholesale|mvno)\s+settlement\b|\b(?:run|do|make)\s+(?:the\s+)?settlement\b", re.I)),
    ("INT-137", re.compile(r"\bsim\s*-?\s*swap\b|\bswap\w*\b[^.]{0,20}\be?sim\b|\breplace\w*\s+(?:the\s+|a\s+|my\s+)?(?:lost\s+|damaged\s+|stolen\s+|broken\s+)?e?sim\b"
                           r"|\b(?:move|convert|switch)\w*\b[^.]{0,30}\bto\s+(?:an?\s+)?esim\b|\bnew\s+e?sim\s+for\b", re.I)),
    ("INT-141", re.compile(r"\b(?:shared|family|pooled?)\s+(?:data\s+)?(?:plan|pool|group)\b|\bdata\s+pool\b", re.I)),
    ("INT-144", re.compile(rf"{_HAS_N}.*?\b(?:charge|rate|record)\w*\b[^.]{{0,25}}\b(?:usage|call|sms|messages?|data|session)\b", _RULE_FLAGS)),
    ("INT-143", re.compile(rf"(?:{_HAS_N}|(?=.*\b(?:line|sim)\b))(?!.*\b(?:pack|bundle|tune|voicemail|roaming|bolt-?\s?on|add-?\s?on|booster)\b)"
                           r".*?\b(?:suspend|resume|reactivate|terminate|disconnect|deactivate)\w*\b"
                           r"(?![^.]{0,25}\b(?:order|service\s+account|asset)\b)", _RULE_FLAGS)),
    ("INT-140", re.compile(r"\broaming\b(?![^.]{0,10}\bpack\s+(?:list|catalog))", re.I)),
    ("INT-138", re.compile(rf"{_HAS_N}.*?\b(?:change|replace)\w*\s+(?:the\s+|my\s+|his\s+|her\s+|their\s+)?(?:mobile\s+|phone\s+)?number\b"
                           rf"|{_HAS_N}.*?\bnew\s+(?:mobile\s+)?number\s+for\b|\bgolden\s+number\b", _RULE_FLAGS)),
    ("INT-134", re.compile(rf"{_HAS_N}.*?\b(?:switch|move|migrate|convert|change)\w*\b[^.]{{0,40}}\bto\s+(?:pre-?\s?paid|post-?\s?paid)\b", _RULE_FLAGS)),
    ("INT-142", re.compile(rf"{_HAS_N}.*?(?:\b(?:change|move|switch|upgrade|downgrade|migrate)\w*\b[^.]{{0,40}}\b(?:to|onto)\s+(?:the\s+)?[\w-]+\s+plan\b"
                           rf"|\b(?:change|upgrade|downgrade)\w*\s+(?:the\s+|its\s+|his\s+|her\s+)?(?:tariff|plan)\b)", _RULE_FLAGS)),
    ("INT-135", re.compile(rf"{_HAS_N}.*?\b(?:credit|spend(?:ing)?|usage)\s+(?:limit|cap)\b|\bbill\s*-?\s*shock\b|{_HAS_N}.*?\bspend\s+alert\b", _RULE_FLAGS)),
    ("INT-148", re.compile(rf"{_HAS_N}.*?\bdeposit\b", _RULE_FLAGS)),
    ("INT-133", re.compile(r"\b(?:emergency|advance)\s+(?:credit|balance|talk\s*time|loan|top\s*-?\s*up)\b|\bcredit\s+loan\b", re.I)),
    ("INT-132", re.compile(rf"{_HAS_N}.*?\b(?:transfer|send|share|gift)\w*\b[^.]{{0,30}}\b(?:balance|credit|talk\s*time|airtime)\b"
                           rf"|{_HAS_N}.*?\b(?:transfer|send|gift)\w*\s+\d+(?:\.\d+)?\s+(?:from|to)\s+\+?\d{{8,}}", _RULE_FLAGS)),
    ("INT-130", re.compile(rf"{_HAS_N}.*?\b(?:buy|add|activate|subscribe|give|get|remove|cancel|unsubscribe|stop|deactivate)\w*\b[^.]{{0,40}}"
                           r"\b(?:pack|bundle|bolt-?\s?on|add-?\s?on|booster|caller\s*tune|voicemail|vas)\b", _RULE_FLAGS)),
    ("INT-129", re.compile(rf"(?:{_HAS_N}|(?=.*\bvoucher\b)).*?(?<!auto )(?<!auto-)\b(?:recharge|top\s*-?\s*up|reload)\w*\b", _RULE_FLAGS)),
    ("INT-131", re.compile(rf"{_HAS_N}.*?\b(?:balance|data\s+left|minutes\s+left|validity|how\s+much\s+(?:data|balance|credit))\b", _RULE_FLAGS)),
    ("INT-139", re.compile(rf"{_HAS_N}.*?(?:\b(?:bar|unbar|block|unblock|barring)\w*\b|\breport\w*\b[^.]{{0,20}}\b(?:lost|stolen)\b|\b(?:lost|stolen)\b)", _RULE_FLAGS)),
    ("INT-136", re.compile(r"\b(?:activate|provision|open|issue|add|create|order|sell)\w*\s+(?:an?\s+|the\s+)?(?:\d+\s+)?(?:new\s+)?"
                           r"(?:(?:pre-?\s?paid|post-?\s?paid|mobile|business|corporate|employee)\s+)+(?:lines?|connections?|sims?|numbers?|subscriptions?)\b(?!\s+items?)"
                           r"|\b(?:activate|issue|provision)\w*\s+(?:an?\s+|the\s+)?(?:\d+\s+)?(?:new\s+)?e?sims?\b", re.I)),
]

# ------------------------------------------------------------------ entities from the wording

_NUM_RE = re.compile(_N)
_MONTHS = {m: i for i, m in enumerate(("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"), start=1)}


def _numbers(text: str) -> list[str]:
    """Mobile numbers in the order they appear - never a voucher PIN or an ICCID."""
    cleaned = re.sub(r"\b(?:pin|voucher)\s*(?:no\.?|number|code)?\s*[:#]?\s*\d{8,20}", " ", text, flags=re.I)
    cleaned = re.sub(r"\b(?:iccid|sim)\s*[:#]?\s*\d{16,22}", " ", cleaned, flags=re.I)
    cleaned = re.sub(r"\b\d{10}\s*-\s*\d{10}\b", " ", cleaned)  # a number range
    return [m.group(0).lstrip("+") for m in _NUM_RE.finditer(cleaned)]


def _amount(text: str) -> float | None:
    rest = _NUM_RE.sub(" ", text)
    rest = re.sub(r"\b\d{4}-\d{2}(?:-\d{2})?\b|\b\d+\s*%|\b\d+(?:\.\d+)?\s*(?:mb|gb|min\w*|sms|messages?|days?)\b", " ", rest, flags=re.I)
    m = re.search(r"(?:\b(?:rs\.?|inr|usd|aed|sar|eur|gbp|of|with|for|by|limit\s+(?:of|to)|to)\s*|[₹$€£]\s*)(\d{1,3}(?:,\d{3})+|\d+)(\.\d+)?\b", rest, re.I) \
        or re.search(r"(?<![\w.])(\d{1,3}(?:,\d{3})+|\d{1,6})(\.\d+)?(?![\w.])", rest)
    return float(m.group(1).replace(",", "") + (m.group(2) or "")) if m else None


def _code_after(text: str, words: str) -> str | None:
    """A plan / pack named after a word: 'on POST_399', 'the DATA10 pack', 'pack "Data 10 GB"'."""
    if m := re.search(r"['\"“]([^'\"”]+)['\"”]", text):
        return m.group(1).strip()
    if m := re.search(rf"\b([A-Z][A-Z0-9]*(?:_[A-Z0-9]+)*\d*)\s+(?:{words})\b", text):
        return m.group(1)
    if m := re.search(rf"\b(?:{words})\s+([A-Z][A-Z0-9_]*\d*[A-Z0-9_]*)\b", text):
        return m.group(1)
    if m := re.search(r"\b(?:on|to|onto|with)\s+(?:the\s+)?([A-Z][A-Z0-9]*_[A-Z0-9_]+|[A-Z]{2,}\d+[A-Z0-9]*)\b", text):
        return m.group(1)
    if m := re.search(r"\b([A-Z]{2,}\d+[A-Z0-9]*|[A-Z][A-Z0-9]*_[A-Z0-9_]+)\b", text):
        return m.group(1)
    return None


def _reason(text: str) -> str | None:
    if m := re.search(r"(?:\bbecause\b|\breason\s*[:=-]?|\s[-–—]\s)\s*(.+)$", text, re.I):
        return m.group(1).strip(" .")[:300]
    return None


def _bool_word(text: str, *words: str) -> bool:
    return any(re.search(rf"\b{w}\b", text, re.I) for w in words)


def _period(text: str) -> dict[str, str]:
    if m := re.search(r"\b(\d{4})-(\d{2})-(\d{2})\b[^.]{0,15}?\b(\d{4})-(\d{2})-(\d{2})\b", text):
        return {"period_start": "-".join(m.group(1, 2, 3)), "period_end": "-".join(m.group(4, 5, 6))}
    if m := re.search(r"\b(\d{4})-(\d{2})\b(?!-\d)", text):
        return {"period_start": f"{m.group(1)}-{m.group(2)}-01"}
    if m := re.search(r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)\w*\s+(\d{4})\b", text, re.I):
        return {"period_start": f"{m.group(2)}-{_MONTHS[m.group(1).lower()[:3]]:02d}-01"}
    return {}


def extract(code: str, text: str, fields: dict[str, Any], loose: dict[str, Any]) -> dict[str, Any]:
    e: dict[str, Any] = {k: fields[k] for k in SPECS[code][5] if fields.get(k) not in (None, "")}
    nums = _numbers(text)
    lowered = text.lower()
    if "msisdn" in SPECS[code][5] and "msisdn" not in e and nums and code != "INT-136":
        e["msisdn"] = nums[0]
    if code == "INT-129":
        if "voucher_pin" not in e and (m := re.search(r"\b(?:pin|voucher)\s*(?:no\.?|number|code)?\s*[:#]?\s*(\d{10,20})\b", text, re.I)):
            e["voucher_pin"] = m.group(1)
        if "voucher_pin" not in e and "amount" not in e and (a := _amount(text)) is not None:
            e["amount"] = a
        if "payment_method" not in e:
            for word, method in (("upi", "upi"), ("card", "card"), ("cash", "cash"), ("wallet", "wallet_app"), ("bank", "bank_transfer"),
                                 ("net banking", "bank_transfer")):
                if word in lowered:
                    e["payment_method"] = method
                    break
        if "card_last4" not in e and (m := re.search(r"\bending\s+(?:in\s+)?(\d{4})\b", text, re.I)):
            e["card_last4"] = m.group(1)
        if "dealer" not in e and (m := re.search(r"\b(?:at|via|through)\s+(?:dealer|retailer|shop)\s+([\w &'-]+?)(?:\s*[,.]|$)", text, re.I)):
            e["dealer"] = m.group(1).strip()
    elif code in ("INT-130", "INT-140"):
        pack = fields.get("pack") or _code_after(text, "pack|bundle|booster|bolt-on|add-on")
        if not pack and code == "INT-130":
            for word, name in (("caller tune", "TUNE"), ("callertune", "TUNE"), ("voicemail", "VOICEMAIL")):
                if word in lowered:
                    pack = name
                    break
        if pack:
            e.setdefault("pack", pack)
        if "action" not in e:
            off = re.search(r"\b(?:remove|cancel|unsubscribe|stop|deactivate|turn\w*\s+off|switch\w*\s+off|disable)\b|\boff\b", lowered)
            e["action"] = ("remove" if off else "add") if code == "INT-130" else ("off" if off else "on")
        if code == "INT-130" and "auto_renew" not in e and re.search(r"\bauto[\s-]?renew\w*\b|\brenew\w*\s+(?:it\s+)?automatically\b", lowered):
            e["auto_renew"] = True
    elif code == "INT-132":
        if len(nums) > 1:
            if re.search(rf"\bto\s+\+?{nums[0]}\b", text) and re.search(rf"\bfrom\s+\+?{nums[1]}\b", text):
                e["msisdn"], e["to_msisdn"] = nums[1], nums[0]
            else:
                e.setdefault("to_msisdn", nums[1])
        if "amount" not in e and (a := _amount(text)) is not None:
            e["amount"] = a
    elif code == "INT-134":
        if "to_payment_type" not in e and (m := re.search(r"\bto\s+(pre|post)-?\s?paid\b", text, re.I)):
            e["to_payment_type"] = f"{m.group(1).lower()}paid"
        if "plan" not in e and (p := _code_after(re.sub(r"\bto\s+(?:pre|post)-?\s?paid\b", "", text, flags=re.I), "plan")):
            e["plan"] = p
        if "credit_limit" not in e and (m := re.search(r"\blimit\s+(?:of\s+)?(\d[\d,]*)", text, re.I)):
            e["credit_limit"] = float(m.group(1).replace(",", ""))
        if "deposit" not in e and (m := re.search(r"\bdeposit\s+(?:of\s+)?(\d[\d,]*)", text, re.I)):
            e["deposit"] = float(m.group(1).replace(",", ""))
    elif code == "INT-135":
        if "spend_alert_pct" not in e and (m := re.search(r"(\d{1,3})\s*%", text)):
            e["spend_alert_pct"] = int(m.group(1))
        if "credit_limit" not in e:
            if re.search(r"\b(?:remove|no)\s+(?:the\s+)?(?:credit\s+|spend\s+)?limit\b", lowered):
                e["credit_limit"] = 0
            elif (a := _amount(re.sub(r"\d{1,3}\s*%", " ", text))) is not None:
                e["credit_limit"] = a
    elif code == "INT-136":
        if "quantity" not in e and (m := re.search(r"\b(\d{1,3})\s+(?:new\s+)?(?:(?:pre-?\s?paid|post-?\s?paid|mobile|business|corporate|employee)\s+)*"
                                                 r"(?:lines?|connections?|e?sims?|numbers?|subscriptions?)\b", text, re.I)):
            e["quantity"] = int(m.group(1))
        if "payment_type" not in e and (m := re.search(r"\b(pre|post)-?\s?paid\b", text, re.I)):
            e["payment_type"] = f"{m.group(1).lower()}paid"
        if "plan" not in e and (p := _code_after(text, "plan|tariff")):
            e["plan"] = p
        if "sim_type" not in e:
            e["sim_type"] = "esim" if re.search(r"\besims?\b", lowered) else "physical"
        if "iccid" not in e and (found := re.findall(r"\b(89\d{16,20})\b", text)):
            e["iccid"] = found
        if "msisdn" not in e and nums:
            e["msisdn"] = nums
        if "initial_recharge" not in e and (m := re.search(r"\b(?:recharge|top\s*-?\s*up)\s+(?:of\s+)?(\d[\d,]*)", text, re.I)):
            e["initial_recharge"] = float(m.group(1).replace(",", ""))
        if "deposit" not in e and (m := re.search(r"\bdeposit\s+(?:of\s+)?(\d[\d,]*)", text, re.I)):
            e["deposit"] = float(m.group(1).replace(",", ""))
        if "credit_limit" not in e and (m := re.search(r"\blimit\s+(?:of\s+)?(\d[\d,]*)", text, re.I)):
            e["credit_limit"] = float(m.group(1).replace(",", ""))
        if "party_id" not in e:
            if m := re.search(r"\b(?:customer|account|party)\s*#?\s*(\d{1,7})\b", text, re.I):
                e["party_id"] = int(m.group(1))
            elif loose.get("party_id") not in (None, ""):
                e["party_id"] = loose["party_id"]
        if "wholesale_partner" not in e and (m := re.search(r"\b(?:for|under|on)\s+(?:the\s+)?(?:mvno|brand|partner)\s+([\w &'-]+?)(?:\s*[,.]|\s+(?:on|with|as)\b|$)", text, re.I)):
            e["wholesale_partner"] = m.group(1).strip()
    elif code == "INT-137":
        if "identity_verified" not in e and re.search(r"\b(?:identity|id|kyc)\s+(?:is\s+|was\s+)?(?:verified|checked|confirmed)\b|\bverified\s+(?:the\s+)?(?:identity|id)\b", lowered):
            e["identity_verified"] = True
        if "sim_type" not in e and re.search(r"\besim\b", lowered):
            e["sim_type"] = "esim"
        if "reason" not in e:
            for word in ("stolen", "lost", "damaged", "broken", "upgrade", "esim"):
                if word in lowered:
                    e["reason"] = "damaged" if word == "broken" else word
                    break
        if "new_iccid" not in e and (m := re.search(r"\b(89\d{16,20})\b", text)):
            e["new_iccid"] = m.group(1)
        if "waive_fee" not in e and re.search(r"\b(?:waive|no|free\s+of)\s+(?:the\s+)?(?:fee|charge)\b", lowered):
            e["waive_fee"] = True
    elif code == "INT-138":
        if "new_msisdn" not in e and len(nums) > 1:
            e["new_msisdn"] = nums[1]
        if "reason" not in e and (r := _reason(text)):
            e["reason"] = r
        if "waive_fee" not in e and re.search(r"\b(?:waive|no|free\s+of)\s+(?:the\s+)?(?:fee|charge)\b", lowered):
            e["waive_fee"] = True
    elif code == "INT-139":
        if "action" not in e:
            e["action"] = "unbar" if re.search(r"\b(?:unbar|unblock|lift|remove\s+the\s+barring|restore)\w*\b", lowered) else "bar"
        if "barring" not in e:
            for pattern, value in ((r"\b(?:lost|stolen)\b", "lost_stolen"), (r"\bfraud\w*\b", "fraud"), (r"\bnon[\s-]?payment\b|\bunpaid\b", "non_payment"),
                                   (r"\binternational\b|\bisd\b", "international"), (r"\bpremium\b", "premium"), (r"\bdata\b|\binternet\b", "data"),
                                   (r"\broaming\b", "roaming"), (r"\bincoming\b", "incoming"), (r"\boutgoing\b", "outgoing")):
                if re.search(pattern, lowered):
                    e["barring"] = value
                    break
        if "reason" not in e and (r := _reason(text)):
            e["reason"] = r
        if "identity_verified" not in e and re.search(r"\b(?:identity|id|kyc)\s+(?:is\s+|was\s+)?(?:verified|checked)\b", lowered):
            e["identity_verified"] = True
    elif code == "INT-141":
        if "member_msisdn" not in e and len(nums) > 1:
            # "add 9100000002 to the shared plan of 9100000001": the plan owner is the one after "of / on"
            owner = re.search(rf"\b(?:of|on|owned\s+by|with)\s+\+?(\d{{8,15}})\b", text)
            if owner and owner.group(1) in nums:
                e["msisdn"] = owner.group(1)
                e["member_msisdn"] = next(n for n in nums if n != owner.group(1))
            else:
                e["member_msisdn"] = nums[1]
        if "action" not in e:
            e["action"] = "remove" if re.search(r"\b(?:remove|drop|take\s+off|leave)\w*\b", lowered) else "add"
    elif code == "INT-142":
        if "plan" not in e and (p := _code_after(text, "plan|tariff")):
            e["plan"] = p
    elif code == "INT-143":
        if "action" not in e:
            for pattern, value in ((r"\bterminat\w*|\bdisconnect\w*|\bdeactivat\w*", "terminate"), (r"\bsuspend\w*", "suspend"),
                                   (r"\bresum\w*|\breactivat\w*", "resume")):
                if re.search(pattern, lowered):
                    e["action"] = value
                    break
        if "reason" not in e and (r := _reason(text)):
            e["reason"] = r
        if "refund_balance" not in e and re.search(r"\brefund\w*\b", lowered):
            e["refund_balance"] = True
    elif code == "INT-144":
        if "event_type" not in e:
            e["event_type"] = ("data" if re.search(r"\b(?:data|mb|gb|internet|session)\b", lowered)
                               else "sms" if re.search(r"\b(?:sms|messages?|texts?)\b", lowered) else "voice")
        if "quantity" not in e:
            rest = _NUM_RE.sub(" ", text)
            if m := re.search(r"(\d+(?:\.\d+)?)\s*(gb|mb|min\w*|sms|messages?|texts?|seconds?|secs?)?", rest, re.I):
                qty, unit = float(m.group(1)), (m.group(2) or "").lower()
                e["quantity"] = qty * 1024 if unit == "gb" else qty / 60 if unit.startswith("sec") else qty
        for key, pattern in (("international", r"\binternational\b|\bisd\b|\boverseas\s+call"), ("premium", r"\bpremium\b"),
                             ("roaming", r"\broaming\b|\babroad\b")):
            if key not in e and re.search(pattern, lowered):
                e[key] = True
        if "destination" not in e and len(nums) > 1:
            e["destination"] = nums[1]
    elif code == "INT-145":
        if "count" not in e and (m := re.search(r"\b(\d{1,5})\s+(?:\w+\s+){0,2}vouchers?\b", text, re.I)):
            e["count"] = int(m.group(1))
        if "face_value" not in e and (m := re.search(r"\bvouchers?\s+(?:of|worth|at|for)\s+(?:rs\.?\s*|inr\s*|[₹$€£]\s*)?(\d[\d,]*(?:\.\d+)?)", text, re.I)
                                      or re.search(r"\b(?:of|worth)\s+(?:rs\.?\s*|inr\s*|[₹$€£]\s*)?(\d[\d,]*(?:\.\d+)?)\b", text, re.I)):
            e["face_value"] = float(m.group(1).replace(",", ""))
        if "validity_days" not in e and (m := re.search(r"\b(\d{1,3})\s*days?\s+validity\b|\bvalidity\s+(?:of\s+)?(\d{1,3})\s*days?\b", text, re.I)):
            e["validity_days"] = int(m.group(1) or m.group(2))
        if "dealer" not in e and (m := re.search(r"\bfor\s+(?:the\s+)?(?:dealer|reseller|retailer)\s+([\w &'-]+?)(?:\s*[,.]|\s+(?:with|valid)\b|$)", text, re.I)):
            e["dealer"] = m.group(1).strip()
    elif code == "INT-146":
        if "partner_kind" not in e and (m := re.search(r"\b(mvno|mvne|reseller|dealer)\b", lowered)):
            e["partner_kind"] = m.group(1)
        if "wholesale_partner" not in e and (m := re.search(r"\b(?i:mvno|mvne|reseller|dealer|partner)\s+(?i:called\s+|named\s+)?['\"“]?([A-Z][\w&'-]*(?:\s+[A-Z][\w&'-]*){0,3})", text)):
            e["wholesale_partner"] = m.group(1).strip()
        if "wholesale_rates" not in e and (m := re.search(r"\brates?\s*(?:of|:)?\s*((?:(?:voice|sms|data|minutes?|messages?|mb)\s*[:=]?\s*\d+(?:\.\d+)?\s*,?\s*(?:and\s+)?)+)", text, re.I)):
            e["wholesale_rates"] = re.sub(r"\s*,?\s*and\s+", ", ", m.group(1)).strip(" ,")
        if "number_range" not in e and (m := re.search(r"\b(\d{6,15})\s*(?:-|–|to)\s*(\d{6,15})\b", text)):
            e["number_range"] = f"{m.group(1)}-{m.group(2)}"
        if "revenue_share_pct" not in e and (m := re.search(r"(\d{1,2}(?:\.\d+)?)\s*%\s*(?:revenue\s+share|commission|share)", text, re.I)):
            e["revenue_share_pct"] = float(m.group(1))
        if "contact_email" not in e and (m := re.search(r"[\w.+-]+@[\w-]+\.[\w.]+", text)):
            e["contact_email"] = m.group(0)
        if "brand" not in e and (m := re.search(r"\bbrand\s+['\"“]?([\w &'-]+?)['\"”]?(?:\s*[,.]|\s+with\b|$)", text, re.I)):
            e["brand"] = m.group(1).strip()
        if "partner_status" not in e and re.search(r"\bsuspend\w*\b", lowered):
            e["partner_status"] = "suspended"
    elif code == "INT-147":
        if "wholesale_partner" not in e and (m := re.search(r"\b(?i:for|with)\s+(?i:the\s+)?(?i:mvno\s+|mvne\s+|partner\s+|dealer\s+|reseller\s+)?"
                                                           r"([A-Z][\w&'-]*(?:\s+[A-Z][\w&'-]*){0,3})", text)):
            e["wholesale_partner"] = m.group(1).strip()
        for k, v in _period(text).items():
            e.setdefault(k, v)
    elif code == "INT-148":
        if "action" not in e:
            e["action"] = "refund" if re.search(r"\b(?:refund|return|release|give\s+back)\w*\b", lowered) else "take"
        if "amount" not in e and (a := _amount(text)) is not None:
            e["amount"] = a
    return e


NEXT: dict[str, list[tuple[str, str]]] = {
    "INT-136": [("INT-130", "Add a pack"), ("INT-129", "Recharge a prepaid line"), ("INT-141", "Put the lines on a shared plan")],
    "INT-129": [("INT-130", "Buy a pack with it"), ("INT-131", "Check the balance")],
    "INT-130": [("INT-131", "Check what is left")],
    "INT-131": [("INT-129", "Recharge"), ("INT-130", "Add a pack")],
    "INT-133": [("INT-129", "Recharge - the credit is recovered first")],
    "INT-134": [("INT-135", "Set the spend limit"), ("INT-148", "Take a deposit")],
    "INT-135": [("INT-148", "Take a deposit for a higher limit")],
    "INT-137": [("INT-139", "Lift a barring once the customer is safe")],
    "INT-139": [("INT-137", "Swap the SIM of a lost or stolen phone")],
    "INT-140": [("INT-130", "Add a roaming pack")],
    "INT-143": [("INT-131", "Check the line")],
    "INT-144": [("INT-131", "Check the balance and what is left")],
    "INT-145": [("INT-129", "Redeem a voucher on a line")],
    "INT-146": [("INT-136", "Activate lines under the partner's brand"), ("INT-145", "Generate vouchers for a dealer")],
    "INT-147": [("INT-146", "Change the partner's rates")],
}

_TRAIN = "9100000001"
TRAINING: list[tuple[str, list[tuple[str, dict[str, Any]]]]] = [
    ("INT-129", [(f"Recharge {_TRAIN} with {{price}} by UPI", {"msisdn": _TRAIN, "amount": "{price}", "payment_method": "upi"}),
                 (f"top up {_TRAIN} using voucher pin 12345678901234", {"msisdn": _TRAIN, "voucher_pin": "12345678901234"})]),
    ("INT-130", [(f"Add the DATA10 pack to {_TRAIN}", {"msisdn": _TRAIN, "pack": "DATA10", "action": "add"}),
                 (f"remove the caller tune from {_TRAIN}", {"msisdn": _TRAIN, "pack": "TUNE", "action": "remove"})]),
    ("INT-131", [(f"What's the balance on {_TRAIN}?", {"msisdn": _TRAIN}), (f"how much data is left on {_TRAIN}", {"msisdn": _TRAIN})]),
    ("INT-132", [(f"Transfer {{n}} from {_TRAIN} to 9100000002", {"msisdn": _TRAIN, "to_msisdn": "9100000002", "amount": "{n}"})]),
    ("INT-133", [(f"Give emergency credit to {_TRAIN}", {"msisdn": _TRAIN})]),
    ("INT-134", [(f"Switch {_TRAIN} to postpaid on POST_399", {"msisdn": _TRAIN, "to_payment_type": "postpaid", "plan": "POST_399"})]),
    ("INT-135", [(f"Set the credit limit of {_TRAIN} to {{price}} with an alert at {{pct}}%",
                  {"msisdn": _TRAIN, "credit_limit": "{price}", "spend_alert_pct": "{pct}"})]),
    ("INT-136", [("Activate {n} postpaid lines on BIZ_599 for customer {id} as eSIMs",
                  {"party_id": "{id}", "quantity": "{n}", "payment_type": "postpaid", "plan": "BIZ_599", "sim_type": "esim"}),
                 ("activate a prepaid line on PRE_SMART for customer {id} with a recharge of 299",
                  {"party_id": "{id}", "payment_type": "prepaid", "plan": "PRE_SMART", "initial_recharge": 299})]),
    ("INT-137", [(f"Swap the SIM of {_TRAIN} to an eSIM - phone lost, identity verified",
                  {"msisdn": _TRAIN, "sim_type": "esim", "reason": "lost", "identity_verified": True})]),
    ("INT-138", [(f"Change the number of {_TRAIN} to 9100009999", {"msisdn": _TRAIN, "new_msisdn": "9100009999"})]),
    ("INT-139", [(f"Bar {_TRAIN} - phone stolen", {"msisdn": _TRAIN, "action": "bar", "barring": "lost_stolen", "reason": "phone stolen"}),
                 (f"unbar international calls on {_TRAIN}", {"msisdn": _TRAIN, "action": "unbar", "barring": "international"})]),
    ("INT-140", [(f"Turn on roaming for {_TRAIN} with ROAM7", {"msisdn": _TRAIN, "action": "on", "pack": "ROAM7"}),
                 (f"switch off roaming on {_TRAIN}", {"msisdn": _TRAIN, "action": "off"})]),
    ("INT-141", [(f"Add 9100000002 to the shared plan of {_TRAIN}", {"msisdn": _TRAIN, "member_msisdn": "9100000002", "action": "add"})]),
    ("INT-142", [(f"Change the plan of {_TRAIN} to BIZ_599", {"msisdn": _TRAIN, "plan": "BIZ_599"})]),
    ("INT-143", [(f"Suspend {_TRAIN} - customer travelling", {"msisdn": _TRAIN, "action": "suspend", "reason": "customer travelling"}),
                 (f"terminate {_TRAIN} - moving abroad, refund the balance",
                  {"msisdn": _TRAIN, "action": "terminate", "reason": "moving abroad, refund the balance", "refund_balance": True})]),
    ("INT-144", [(f"Charge a {{n}} minute call on {_TRAIN}", {"msisdn": _TRAIN, "event_type": "voice", "quantity": "{n}"}),
                 (f"charge 500 MB of data on {_TRAIN} while roaming", {"msisdn": _TRAIN, "event_type": "data", "quantity": 500, "roaming": True})]),
    ("INT-145", [("Generate {n} recharge vouchers of {price} for dealer CityMobile", {"count": "{n}", "face_value": "{price}", "dealer": "CityMobile"})]),
    ("INT-146", [("Onboard MVNO Zoom Mobile with wholesale rates voice 0.2, sms 0.05, data 0.01",
                  {"wholesale_partner": "Zoom Mobile", "partner_kind": "mvno", "wholesale_rates": "voice 0.2, sms 0.05, data 0.01"})]),
    ("INT-147", [("Run the settlement for Zoom Mobile for 2026-10", {"wholesale_partner": "Zoom Mobile", "period_start": "2026-10-01"})]),
    ("INT-148", [(f"Take a deposit of {{price}} on {_TRAIN}", {"msisdn": _TRAIN, "action": "take", "amount": "{price}"}),
                 (f"refund the deposit on {_TRAIN}", {"msisdn": _TRAIN, "action": "refund"})]),
]

# Flow Studio: what each intent takes (inputs) and returns (result.<name>)
FLOW_FIELDS: dict[str, list[str]] = {code: list(spec[5]) for code, spec in SPECS.items()}
RESULTS: dict[str, dict[str, list[str]]] = {
    "INT-129": {"msisdn": [], "recharged": ["true", "false"], "amount": [], "balance": [], "valid_until": [], "recovered": []},
    "INT-130": {"msisdn": [], "action": ["add", "remove"], "pack": [], "price": [], "paid_from": ["balance", "bill", "free"]},
    "INT-131": {"msisdn": [], "payment_type": ["prepaid", "postpaid"], "status": ["active", "suspended", "terminated"], "balance": [], "cycle_spend": []},
    "INT-132": {"msisdn": [], "to_msisdn": [], "amount": [], "balance": []},
    "INT-133": {"msisdn": [], "credited": [], "owed": [], "balance": []},
    "INT-134": {"msisdn": [], "payment_type": ["prepaid", "postpaid"], "plan": [], "carried_over": []},
    "INT-135": {"msisdn": [], "credit_limit": [], "spend_alert_pct": [], "barred": ["true", "false"]},
    "INT-136": {"party_id": [], "count": [], "msisdn": [], "line_id": [], "payment_type": ["prepaid", "postpaid"], "plan": []},
    "INT-137": {"msisdn": [], "iccid": [], "sim_type": ["physical", "esim"], "cooldown_until": []},
    "INT-138": {"msisdn": [], "old_msisdn": [], "quarantined_until": []},
    "INT-139": {"msisdn": [], "action": ["bar", "unbar"], "barring": []},
    "INT-140": {"msisdn": [], "roaming": ["true", "false"]},
    "INT-141": {"msisdn": [], "member": [], "action": ["add", "remove"]},
    "INT-142": {"msisdn": [], "plan": [], "previous_plan": []},
    "INT-143": {"msisdn": [], "action": ["suspend", "resume", "terminate"], "status": ["active", "suspended", "terminated"]},
    "INT-144": {"msisdn": [], "granted": ["true", "false"], "charged": [], "reason": [], "alert": ["low_balance", "alert", "barred"]},
    "INT-145": {"batch": [], "count": [], "face_value": []},
    "INT-146": {"partner_id": [], "partner": [], "created": ["true", "false"]},
    "INT-147": {"settlement_id": [], "number": [], "net": [], "wholesale_due": [], "commission_due": []},
    "INT-148": {"msisdn": [], "action": ["take", "refund"], "amount": [], "deposit": []},
}

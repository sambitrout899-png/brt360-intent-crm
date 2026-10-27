"""NLU/LLM Intent Classifier (HLD 4.1).

Primary path: OpenAI structured-output classification - casual,
loosely-structured English, no field:value syntax required. The key
used is resolved per-tenant first (app/models/ai_settings.py, admin-
configurable - see /api/admin/ai-settings), falling back to the global
OPENAI_API_KEY in app.config when a tenant hasn't configured its own.
Fallback path (no key anywhere): a deterministic, keyword + key:value
field parser - fully functional for dev/test with zero external calls
or cost, and exercised directly by the pytest suite so CI never needs a
real API key.

Fallback phrasing convention (documented here, not a general NLP system):
free text may contain `field: value` pairs (comma- or newline-separated,
list-valued fields use `+` between items since `,` is the field separator),
read in addition to a keyword that selects the intent type. See each
INT-xx block in _classify_with_rules for its keyword + expected fields.
This rigid format is ONLY the no-key fallback's contract - the LLM path
needs no particular structure at all.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.orm import Session

from app.intent_engine import abo_intents, b2c_intents, comm_intents, config_intents, om_intents, fulfillment_intents, inventory_intents, quote_intents, sto_intents, understanding

SUPPORTED_INTENTS = (
    "INT-01", "INT-02", "INT-03",
    "INT-04", "INT-05", "INT-06",
    "INT-07", "INT-08", "INT-09",
    "INT-10", "INT-11", "INT-12",
    "INT-13", "INT-14",
    "INT-15", "INT-16", "INT-17", "INT-18", "INT-19", "INT-20", "INT-21",
    "INT-22", "INT-23", "INT-24", "INT-25", "INT-26", "INT-27", "INT-28",
    "INT-29", "INT-30", "INT-31", "INT-32", "INT-33", "INT-34",
    # Sales-to-Order / B2C additions - see NEW_PROCESS_INTENTS below
    "INT-35", "INT-36", "INT-37", "INT-38", "INT-39", "INT-40", "INT-41",
    "INT-42", "INT-43", "INT-44", "INT-45", "INT-46",
    "INT-47", "INT-48", "INT-49", "INT-50",
    # Order Management - see om_intents.py
    "INT-51", "INT-52", "INT-53", "INT-54", "INT-55", "INT-56", "INT-57", "INT-58", "INT-59", "INT-60", "INT-61",
    "INT-62", "INT-63", "INT-64", "INT-65", "INT-66", "INT-67", "INT-68", "INT-69", "INT-70", "INT-71",
    # Communications - see comm_intents.py; configuration - config_intents.py; B2C direct order - b2c_intents.py
) + comm_intents.CODES + config_intents.CODES + b2c_intents.CODES + sto_intents.CODES + abo_intents.CODES + quote_intents.CODES + fulfillment_intents.CODES \
    + inventory_intents.CODES

_SYSTEM_PROMPT = """You are the NLU classifier for BRT 360, a telecom Intent CRM.

The user's input is ordinary, casual English - never require any particular
sentence structure, keyword, or "field: value" syntax (that rigid format is
only a fallback parser's contract when no AI is available, not yours). The
input may be ungrammatical, loosely worded, missing punctuation, or contain
typos and misspellings (of product names, company names, or anything else) -
silently correct and normalize these when extracting entities; do not let a
typo or awkward phrasing prevent a confident classification when the intent
is otherwise clear. Infer entity values from context and common sense (e.g.
"the Fiber 500 plan" -> offering_name "Fiber 500 Bundle" if that is the
closest known name), and leave an entity out of your JSON only when the text
truly gives no basis for it - never guess a numeric id that was not stated or
clearly implied.

Classify the user's free text into exactly one of these intent types, or UNKNOWN:

INT-01 "Launch a new bundle" - entities: name (str), composition (list[str] of
  product names, first is the primary connectivity line), segment (str, e.g.
  B2C/B2B-SMB), channel (str, B2C/B2B), region (str, default ALL),
  list_price (number), requested_discount_pct (number, default 0).

INT-02 "Modify a live bundle" - entities: offering_name (str), new_list_price
  (number, optional), new_segment (str, optional), new_channel (str, optional).

INT-03 "Retire a bundle" - entities: offering_name (str), replacement_offering_name
  (str - required per policy).

INT-04 "Qualify this lead" - entities: company_name (str), source (str),
  region (str, default ALL).

INT-05 "Configure & quote this bundle" - entities: opportunity_id (int),
  offering_name (str, an existing active bundle), quantity (int, default 1),
  requested_discount_pct (number, default 0), discount_justification (str,
  optional - required if discount exceeds 10%).

INT-06 "Check feasibility & approve" - entities: quote_id (int), decision
  ("approve"|"reject"|"conditional"), reasoning (str, optional).

INT-07 "Activate this order end-to-end" - entities: quote_id (int),
  service_account_name (str, optional - which service account this order
  belongs to, e.g. a site or bundle name; defaults to "<offering name>
  Service" if omitted. Re-activating under the same name on the same
  account reuses that service account rather than creating a new one),
  sites (list[str], optional - B2B multi-site process pack: e.g. an IPLC
  circuit's A-End and Z-End. Omitted or a single site: the whole order
  provisions and activates in this one call, exactly as always. Two or
  more sites: each gets its own independently-provisioned ServiceOrder -
  the order itself stays "activating" until every site is separately
  provisioned via INT-14).

INT-08 "Change this live service" - entities: order_id (int), new_monthly_price
  (number).

INT-09 "Suspend, cancel or terminate" - entities: order_id (int), action
  ("suspend"|"cancel"|"terminate"), decision (str, optional - "retain" to
  accept a retention offer and cancel a pending retention window instead
  of letting it lapse into an actual cancellation).

INT-10 "Log & classify this complaint" - entities: description (str),
  company_name (str, optional), sla_class (str, default "standard").

INT-11 "Diagnose the root cause" - entities: ticket_id (int).

INT-12 "Assign & track the field team" - entities: ticket_id (int, optional),
  order_id (int, optional - at least one required), required_skill (str,
  default "general"), region (str, default ALL), scheduled_window (str,
  optional), work_order_id (int, optional - if set with action "complete",
  this call records completion instead of dispatching), action (str, optional).

INT-13 "Verify customer identity" - B2C process pack, blocked for tenants
  configured B2B-only. Entities: party_id (int), id_number (str), id_type
  (str, default "national_id"), provider (str, optional - defaults to
  "Yakeen"; other configured providers include WATHQ, SIMAH, Abshir).

INT-14 "Provision this site" - B2B process pack (multi-site orders),
  blocked for tenants configured B2C-only. Entities: order_id (int),
  site_label (str - must match one of the order's sites from INT-07,
  e.g. "A-End"). Provisions just that one site independently; once every
  site under the order is provisioned, the whole order goes active and
  billing starts.

INT-15 "Add a prospect" - entities: name (str), email (str, optional),
  phone (str, optional). The entry point ahead of any Lead.

INT-16 "Add a product to this lead" - entities: lead_id (int),
  offering_name (str, an existing active offering).

INT-17 "Approve this lead" - Sales Director qualification gate, only
  reachable when a tenant has a "lead" approval chain configured.
  Entities: lead_id (int), decision ("approve"|"reject", default
  "approve"), reasoning (str, optional).

INT-18 "Run feasibility assessment" - B2B, at the Opportunity level
  before quoting. Entities: opportunity_id (int), address (str,
  optional - site/locator to check network availability/inventory
  against).

INT-19 "Revise this quote" - entities: quote_id (int),
  requested_discount_pct (number, optional), quantity (int, optional).
  Blocked once the quote has passed its configured PONR.

INT-20 "Cancel this quote" - entities: quote_id (int), reasoning (str,
  optional). Same PONR gate as INT-19.

INT-21 "Revise this order" - entities: order_id (int),
  new_monthly_price (number, optional), new_quantity (int, optional).
  Distinct from INT-08 (which is for an already-active order with
  billing proration): this is a plain pre-PONR edit, blocked once the
  order has passed its configured PONR (default: anything past
  "created"/"activating").

INT-22 "Generate proposal for this quote" - entities: quote_id (int, must
  be an approved quote).

INT-23 "Record customer approval of the proposal" - the customer's
  decision over email, recorded by a human. Entities: proposal_id (int),
  decision ("approve"|"reject", default "approve").

INT-24 "Generate order from quote" - entities: quote_id (int). Requires
  the quote approved AND a customer-approved proposal on file (INT-22/23).

INT-25 "Approve this order" - the Order Control Unit gate, only
  reachable once an order has been generated (INT-24). Entities:
  order_id (int), decision ("approve"|"reject", default "approve"),
  reasoning (str, optional).

INT-26 "Close Work Order 1 (Installation)" - entities: work_order_id
  (int), technical_parameters (str, optional - the finalized
  field/core-network parameters).

INT-27 "Close Work Order 2 (Connectivity Test & Acceptance)" -
  entities: work_order_id (int), signature (str, optional - the
  customer's signed acceptance).

INT-28 "Close Work Order 3 (Acceptance Letter)" - entities:
  work_order_id (int), document_ref (str, optional - defaults to a
  fabricated document-store reference if omitted).

INT-29 "Update this record" - modify a saved customer/prospect (party), lead,
  opportunity, quote or order in place. Entities: entity_type ("party"|"lead"|
  "opportunity"|"quote"|"order" - a customer or prospect is "party"),
  entity_id (int), fields (object of field name -> new value, including any
  tenant custom fields). Blocked once the record's state is locked.

INT-30 "Create a new revision of this quote" - for a quote that is locked
  (e.g. approved) and so can no longer be edited: supersedes it with a new
  draft revision. Entities: quote_id (int), requested_discount_pct (number,
  optional), quantity (int, optional).

INT-31 "Move this record to a state" - a lifecycle action such as close won,
  close lost, reopen, deactivate, or a custom state. Entities: entity_type
  (as INT-29), entity_id (int), action (str, optional, e.g. "close_won"),
  to_state (str, optional - at least one of action/to_state), reasoning (str,
  optional).

INT-32 "Assign / transfer this record" - to another user and/or position.
  Entities: entity_type (as INT-29), entity_id (int), to_user (str, email or
  name, optional), to_position (str, optional - at least one of the two),
  reason (str, optional).

INT-34 "Sync inventory check types" - refresh feasibility check types from the tenant inventory API. No entities.

INT-33 "Configure 360" - change the Customer 360 page itself (panes, charts,
  theme/colours, layout) or connect it to billing/service/network/inventory
  systems. Entities: instruction (str - the user's whole request, verbatim).

Interpret the request the way a helpful colleague would: map everyday
wording onto the closest intent above (e.g. "add a new product/plan/package"
-> INT-01, "approve it" while viewing a quote -> INT-06 for that quote,
"the customer is complaining their internet is down" -> INT-10). A message
may include a line starting "Context:" describing the record or page the
user is looking at - use it to fill ids the text leaves implicit ("this",
"it"), but never let it override an id the text states. If the request is
genuinely ambiguous, still return your best guess with lower confidence and
list the other plausible intents in "alternatives".

Respond with strict JSON only:
{"intent_type": "INT-01".."INT-33"|"FLOW-<n>"|"UNKNOWN", "entities": {...},
 "confidence": 0.0-1.0, "rationale": "one sentence explaining the classification",
 "alternatives": ["up to 3 other plausible intent codes, best first - empty when sure"]}
"""


INTENT_LABELS: dict[str, str] = {
    "INT-01": "Launch a new bundle",
    "INT-02": "Modify a live bundle",
    "INT-03": "Retire a bundle",
    "INT-04": "Qualify this lead",
    "INT-05": "Configure & quote this bundle",
    "INT-06": "Check feasibility & approve",
    "INT-07": "Activate this order end-to-end",
    "INT-08": "Change this live service",
    "INT-09": "Suspend, cancel or terminate",
    "INT-10": "Log & classify this complaint",
    "INT-11": "Diagnose the root cause",
    "INT-12": "Assign & track the field team",
    "INT-13": "Verify customer identity",
    "INT-14": "Provision this site",
    "INT-15": "Add a prospect",
    "INT-16": "Add a product to this lead",
    "INT-17": "Approve this lead",
    "INT-18": "Run feasibility assessment",
    "INT-34": "Sync inventory check types",
    "INT-19": "Revise this quote",
    "INT-20": "Cancel this quote",
    "INT-21": "Revise this order",
    "INT-22": "Generate proposal for this quote",
    "INT-23": "Record customer approval of the proposal",
    "INT-24": "Generate order from quote",
    "INT-25": "Approve this order",
    "INT-26": "Close Work Order 1 (Installation)",
    "INT-27": "Close Work Order 2 (Connectivity Test & Acceptance)",
    "INT-28": "Close Work Order 3 (Acceptance Letter)",
    "INT-29": "Update this record",
    "INT-30": "Create a new revision of this quote",
    "INT-31": "Move this record to a state",
    "INT-32": "Assign / transfer this record",
    "INT-33": "Configure 360",
    "INT-35": "Convert to an account",
    "INT-36": "Approve or reject",
    "INT-37": "Configure the product",
    "INT-38": "Send to Presales",
    "INT-39": "Attach the customer's PO",
    "INT-40": "Match all",
    "INT-41": "Add a consumer",
    "INT-42": "Design product attributes",
    "INT-43": "Add a product to a pricelist",
    "INT-44": "Apply a pricelist",
    "INT-45": "Set technical attributes",
    "INT-46": "Generate a lead",
    "INT-47": "Configure with AI",
    "INT-48": "Release a product model",
    "INT-49": "Check what a customer can buy",
    "INT-50": "Change a live service's configuration",
}
INTENT_LABELS.update(om_intents.LABELS)
INTENT_LABELS.update(comm_intents.LABELS)
INTENT_LABELS.update(config_intents.LABELS)
INTENT_LABELS.update(b2c_intents.LABELS)
INTENT_LABELS.update(sto_intents.LABELS)
INTENT_LABELS.update(abo_intents.LABELS)
INTENT_LABELS.update(quote_intents.LABELS)
INTENT_LABELS.update(fulfillment_intents.LABELS)
INTENT_LABELS.update(inventory_intents.LABELS)

# The Sales-to-Order / B2C steps added after the intent model was trained
# (INT-35..41). They are recognised by these phrase rules before any model
# runs - like Configure 360 above - so the model's prompt, and with it what
# a fine-tuned model was trained on, stays exactly as it was. A model
# retrained on the exported examples may return these codes too.
NEW_PROCESS_INTENTS = ("INT-35", "INT-36", "INT-37", "INT-38", "INT-39", "INT-40", "INT-41", "INT-42", "INT-43", "INT-44", "INT-45", "INT-46",
                       "INT-47", "INT-48", "INT-49", "INT-50") + om_intents.CODES + comm_intents.CODES + config_intents.CODES + b2c_intents.CODES \
    + sto_intents.CODES + abo_intents.CODES + quote_intents.CODES + fulfillment_intents.CODES + inventory_intents.CODES
# Each pattern needs its words together ("attach the PO", "send ... to
# Presales") so a sentence about something else that merely mentions a PO
# or an account is never taken for one of these steps.
_DET = r"(?:the\s+|a\s+|an\s+|this\s+|their\s+|customer'?s?\s+)*"
_PROCESS_RULES: list[tuple[str, re.Pattern]] = [
    ("INT-48", re.compile(r"\brelease\b[^.]{0,40}\b(product\s+model|model|configuration\s+model|model\s+version)\b", re.I)),
    ("INT-50", re.compile(r"\b(reconfigure|re-configure)\b[^.]{0,30}\b(order|service)\b|\bchange\b[^.]{0,20}\bconfiguration\b[^.]{0,30}\border\s*#?\s*\d", re.I)),
    # never "qualify (this lead) for the X offer" - that is INT-04
    ("INT-49", re.compile(r"\bwhat\s+can\b[^.]{0,40}\bbuy\b|\b(eligible|eligibility)\b[^.]{0,30}\b(products?|offers?|offerings?)\b"
                          r"|\b(products?|offers?|offerings?)\b[^.]{0,25}\b(eligible|qualif\w*)\s+for\b", re.I)),
    ("INT-47", re.compile(r"\bconfigure\s+with\s+ai\b|\b(configure|recommend|suggest|build)\b[^.:]{0,40}\b(best|cheapest|right)\s+(configuration|setup|config)\b", re.I)),
    ("INT-40", re.compile(rf"\bmatch\s+all\b|\bmatch\s+{_DET}(po|purchase\s+order)\b", re.I)),
    ("INT-39", re.compile(rf"\b(attach|upload)\w*\s+{_DET}(po|purchase\s+order)\b|\b(received|got)\s+{_DET}(po|purchase\s+order)\b"
                          r"|\b(po|purchase\s+order)\s+(?:\S+\s+){0,3}(received|attached|arrived)\b", re.I)),
    ("INT-38", re.compile(r"\b(send|hand|pass|route|forward)\w*\b[^.]{0,40}?\bto\s+pre-?\s?sales\b", re.I)),
    ("INT-35", re.compile(r"\bconvert\w*\b[^.]{0,60}?\b(to|into)\s+(an?\s+)?(active\s+)?account\b"
                          r"|\bmake\b[^.]{0,40}?\b(an?\s+)?active\s+account\b", re.I)),
    ("INT-41", re.compile(r"\b(add|create|register)\s+(a\s+)?(new\s+)?(consumer|b2c\s+customer|retail\s+customer|individual\s+customer)\b", re.I)),
    # "generate a lead" / "raise a lead for prospect 7" / "lead for this prospect" - never "New lead Acme ..." (INT-04)
    ("INT-46", re.compile(r"\b(generate|raise|open)\s+(a\s+|the\s+)?(new\s+)?lead\b|\blead\s+for\s+(the\s+|this\s+)?prospect\b", re.I)),
    ("INT-45", re.compile(r"\btechnical\s+attributes?\b[^.]{0,40}\border\s*#?\s*\d|\border\s*#?\s*\d+\s*[:,-]?\s*(?:set\s+)?(?:vlan|cpe|ip\s+block|technical)", re.I)),
    ("INT-42", re.compile(r"^\s*(?:please\s+)?(?:design|define)\b[^:]{0,80}:", re.I)),
    ("INT-43", re.compile(r"\b(?:put|add|place|list)\s+(?!.*\bquote\b).{1,60}?\s+(?:on|to|in)\s+(?:the\s+)?[\w-]+\s+price\s?list\b", re.I)),
    ("INT-44", re.compile(r"\b(?:use|apply|assign|set)\s+(?:the\s+)?[\w-]+\s+price\s?list\b[^.]{0,30}\b(?:for|to|on)\s+(?:the\s+)?"
                          r"(?:quote|quotation|opportunity|opp|deal|customer|account|party|prospect)\s*#?\s*\d+", re.I)),
    ("INT-37", re.compile(rf"\bconfigure\s+{_DET}(product|opportunity|opp|deal|pricing)\b(?!.*\bquote\b)|\bset\s+{_DET}pricing\s+attributes?\b", re.I)),
    ("INT-36", re.compile(rf"\bapprove\s+or\s+reject\b|\b(approve|reject|decline)\s+{_DET}(opportunity|opp|deal|prospect|account|party)\b", re.I)),
]
# Communications and Order Management phrases are the most specific - checked first
# (communications first: its billing phrases name a bill, never an order).
_PROCESS_RULES = comm_intents.RULES + om_intents.RULES + _PROCESS_RULES
# A page of the tenant's own is the most specific of all - "... the B2B prospect page" (never Customer 360: INT-33).
_PROCESS_RULES = config_intents.RULES + _PROCESS_RULES
# A consumer order "via WhatsApp / at the store" names its channel - checked before the Order Management phrases.
_PROCESS_RULES = b2c_intents.RULES + _PROCESS_RULES
# "Prepare the proposal" / "the proposal PDF" - the proposal document of a quote (never plain "generate proposal": INT-22).
_PROCESS_RULES = sto_intents.RULES + _PROCESS_RULES
# Asset-based ordering around a change order - "cancel change order 12", "quote an upgrade of asset 4",
# "history of asset 4" - checked before "Change an asset" (INT-107), whose wording they contain.
_PROCESS_RULES = abo_intents.RULES + _PROCESS_RULES
# A negotiated quote price - "override the price of quote 12 to 4500" - before "revise this quote" wording reaches INT-19.
_PROCESS_RULES = quote_intents.RULES + _PROCESS_RULES
# Fulfillment - "send order 12 to fulfillment", "retry the fallout on order 12".
_PROCESS_RULES = fulfillment_intents.RULES + _PROCESS_RULES
# Inventory - "free ports at BLR-EX-01", "what is affected if OLT-01 goes down", "create a VLAN pool 100-999".
_PROCESS_RULES = inventory_intents.RULES + _PROCESS_RULES
_ENTITY_OF_ID = {"party_id": "party", "lead_id": "lead", "opportunity_id": "opportunity", "quote_id": "quote", "order_id": "order"}


def _process_step(text: str, context: dict[str, Any] | None) -> ClassificationResult | None:
    code = next((c for c, pattern in _PROCESS_RULES if pattern.search(text)), None)
    if code is None:
        return None
    fields = _coerce_ids(_extract_fields(text))
    loose = understanding.extract_loose_entities(text)
    entities: dict[str, Any] = {}

    def take(*keys: str) -> None:
        for key in keys:
            value = fields.get(key, loose.get(key))
            if value not in (None, ""):
                entities[key] = value

    if code in om_intents.CODES or code in comm_intents.CODES or code in config_intents.CODES or code in b2c_intents.CODES \
            or code in sto_intents.CODES or code in abo_intents.CODES or code in quote_intents.CODES or code in fulfillment_intents.CODES \
            or code in inventory_intents.CODES:
        mod = (om_intents if code in om_intents.CODES else comm_intents if code in comm_intents.CODES
               else config_intents if code in config_intents.CODES else sto_intents if code in sto_intents.CODES
               else abo_intents if code in abo_intents.CODES else quote_intents if code in quote_intents.CODES else fulfillment_intents if code in fulfillment_intents.CODES
               else inventory_intents if code in inventory_intents.CODES else b2c_intents)
        entities.update(mod.extract(code, text, fields, loose))
        required = mod.REQUIRED[code]
        if context and context.get("entity_id"):
            understanding.apply_context(code, entities, context, required)
        ok = all(entities.get(k) not in (None, "") for k in required) and not mod.missing(code, entities)
        return ClassificationResult(code, entities, 0.9 if ok else 0.6, f"Matched \"{INTENT_LABELS[code]}\" from the wording.")

    if code == "INT-35":
        take("party_id", "industry")
        price_list = fields.get("price_list") or fields.get("pricelist")
        if not price_list and (m := re.search(r"\bon\s+(?:the\s+)?([\w-]+)\s+price\s?list\b", text, re.I)):
            price_list = m.group(1)
        if price_list:
            entities["price_list"] = price_list
    elif code == "INT-36":
        lowered = text.lower()
        entities["decision"] = "reject" if re.search(r"\b(reject|decline|deny|refuse)", lowered) else "approve"
        if fields.get("entity_type") and fields.get("entity_id"):
            entities["entity_type"], entities["entity_id"] = fields["entity_type"], fields["entity_id"]
        else:
            m = re.search(r"\b(opportunity|opp|deal|prospect|account|customer|party|quote|lead|order)\s*#?\s*(\d+)", text, re.I)
            if m:
                kind = {"opp": "opportunity", "deal": "opportunity", "prospect": "party", "account": "party", "customer": "party"}.get(
                    m.group(1).lower(), m.group(1).lower())
                entities["entity_type"], entities["entity_id"] = kind, int(m.group(2))
        if m := re.search(r"\bbecause\b(.+)$", text, re.I):
            entities["reasoning"] = m.group(1).strip()
        take("reasoning")
    elif code == "INT-37":
        take("opportunity_id", "offering_name", "quantity", "pricing_attributes")
    elif code == "INT-38":
        take("opportunity_id", "technical_parameters", "to_user")
        address = fields.get("address") or fields.get("site")
        if not address and (m := re.search(r"\b(?:site|address)\s+(?:at\s+|is\s+)?(.+?)(?:,\s*(?:tech|with)\b|$)", text, re.I)):
            address = m.group(1).strip()
        if address:
            entities["address"] = address
    elif code == "INT-39":
        take("quote_id", "po_amount", "document_ref")
        po = fields.get("po_number") or fields.get("po")
        if not po and (m := re.search(r"\bPO[-/#]?[\w-]*\d[\w-]*", text, re.I)):
            po = m.group(0)
        if not po and (m := re.search(r"\b(?:po|purchase\s+order)\s+(?:number\s+|no\.?\s+|#\s*)?([\w-]*\d[\w-]*)", text, re.I)):
            po = m.group(1)
        if po:
            entities["po_number"] = str(po)
        if "po_amount" not in entities and loose.get("price") is not None:
            entities["po_amount"] = loose["price"]
    elif code == "INT-40":
        take("quote_id")
    elif code == "INT-42":
        m = re.match(r"^\s*(?:please\s+)?(?:design|define)\s+(?:the\s+)?(?:product\s+)?(?:attributes?\s+(?:for|of|on)\s+)?"
                     r"(?P<name>[^:]+?)\s*(?:attributes?)?\s*:\s*(?P<rest>.+)$", text, re.I | re.S)
        if m:
            entities["offering_name"] = m.group("name").strip()
            entities["instruction"] = entities["design"] = m.group("rest").strip()
    elif code == "INT-43":
        m = re.search(r"\b(?:put|add|place|list)\s+(?P<o>.+?)\s+(?:on|to|in)\s+(?:the\s+)?(?P<pl>[\w-]+)\s+price\s?list\b"
                      r"(?:[^.\d]{0,20}?(?P<p>\d[\d,]*(?:\.\d+)?))?", text, re.I)
        if m:
            entities.update(offering_name=m.group("o").strip(), price_list=m.group("pl"))
            if m.group("p"):
                entities["price"] = float(m.group("p").replace(",", ""))
    elif code == "INT-44":
        if m := re.search(r"\b(?P<pl>[\w-]+)\s+price\s?list\b", text, re.I):
            entities["price_list"] = m.group("pl")
        if m := re.search(r"\b(quote|quotation|opportunity|opp|deal|customer|account|party|prospect)\s*#?\s*(\d+)", text, re.I):
            kind = {"quotation": "quote", "opp": "opportunity", "deal": "opportunity", "customer": "party", "account": "party",
                    "prospect": "party"}.get(m.group(1).lower(), m.group(1).lower())
            entities["entity_type"], entities["entity_id"] = kind, int(m.group(2))
    elif code == "INT-45":
        take("order_id")
        after = re.split(r"\border\s*#?\s*\d+\s*[:,-]?\s*", text, maxsplit=1, flags=re.I)
        rest = after[1] if len(after) > 1 else text
        rest = re.sub(r"^\s*(?:set\s+)?(?:the\s+)?technical\s+attributes?\s*(?:to|:)?\s*", "", rest, flags=re.I).strip(" .")
        if rest:
            # Kept as written: the product's design knows which words are attribute names.
            entities["technical_attributes"] = rest
    elif code == "INT-46":
        take("party_id", "source", "region")
        if "party_id" not in entities and (m := re.search(r"\b(?:prospect|customer|party)\s*#?\s*(\d+)", text, re.I)):
            entities["party_id"] = int(m.group(1))
        if m := re.search(r"\binterested\s+in\s+(.+?)(?:\s+(?:from|via|in the)\b|[.;]|$)", text, re.I):
            products = [p.strip() for p in re.split(r",|\s+and\s+", m.group(1)) if p.strip()]
            if products:
                entities["products"] = products
    elif code == "INT-47":
        take("opportunity_id", "offering_name")
        if "opportunity_id" not in entities and (m := re.search(r"\b(?:opportunity|opp|deal)\s*#?\s*(\d+)", text, re.I)):
            entities["opportunity_id"] = int(m.group(1))
        need = re.split(r":", text, maxsplit=1)
        entities["need"] = need[1].strip() if len(need) > 1 else text
    elif code == "INT-48":
        m = re.search(r"\b(?:model|version)\s+(?:of|for)\s+(.+?)\s*(?:from\b|$)", text, re.I)
        if m:
            entities["offering_name"] = m.group(1).strip(" .")
        if m := re.search(r"\bfrom\s+(\d{4}-\d{2}-\d{2})", text):
            entities["valid_from"] = m.group(1)
    elif code == "INT-49":
        take("party_id")
        if "party_id" not in entities and (m := re.search(r"\b(?:customer|prospect|account|party)\s*#?\s*(\d+)", text, re.I)):
            entities["party_id"] = int(m.group(1))
    elif code == "INT-50":
        take("order_id")
        if "order_id" not in entities and (m := re.search(r"\border\s*#?\s*(\d+)", text, re.I)):
            entities["order_id"] = int(m.group(1))
        rest = re.split(r"\border\s*#?\s*\d+\s*[:,-]?\s*", text, maxsplit=1, flags=re.I)
        if len(rest) > 1 and rest[1].strip():
            entities["pricing_attributes"] = rest[1].strip(" .")
    elif code == "INT-41":
        take("email", "phone")
        name = fields.get("name")
        if not name and (m := re.search(r"\bconsumer\b\s*(?:customer)?\s*:?\s*([A-Z][\w.'-]*(?:\s+[A-Z][\w.'-]*)*)", text)):
            name = m.group(1)
        if name:
            entities["name"] = name

    required = {"INT-35": ["party_id"], "INT-36": ["entity_type", "entity_id"], "INT-37": ["opportunity_id"],
                "INT-38": ["opportunity_id"], "INT-39": ["quote_id", "po_number"], "INT-40": ["quote_id"], "INT-41": ["name"],
                "INT-42": ["offering_name", "design"], "INT-43": ["price_list", "offering_name", "price"],
                "INT-44": ["entity_type", "entity_id", "price_list"], "INT-45": ["order_id", "technical_attributes"],
                "INT-46": ["party_id"], "INT-47": ["opportunity_id", "need"], "INT-48": ["offering_name"], "INT-49": ["party_id"],
                "INT-50": ["order_id", "pricing_attributes"]}[code]
    if context and context.get("entity_id"):
        understanding.apply_context(code, entities, context, required)
    ok = all(entities.get(k) not in (None, "") for k in required)
    return ClassificationResult(code, entities, 0.9 if ok else 0.6, f"Matched \"{INTENT_LABELS[code]}\" from the wording.")

# "Configure 360: ..." / "customer 360 ... pane|chart|theme|connect" - the
# whole sentence is the instruction for the Configure 360 agent, so it is
# recognised before either classifier tries to pull entities out of it.
_C360 = re.compile(r"\b(configure|customi[sz]e|design|set ?up|personali[sz]e)\s+(the\s+)?(customer\s*)?360\b", re.I)
_C360_LOOSE = re.compile(
    r"\b(customer\s*360|360\s*(page|view|screen))\b.*\b(pane|panes|panel|widget|chart|graph|layout|theme|look|colou?r|"
    r"connect|integrat\w*|system|api|section|column)s?\b"
    r"|\b(pane|panes|panel|widget|chart|graph|layout|theme|look|colou?r|connect|integrat\w*|section)s?\b.*\b(customer\s*360|360\s*(page|view|screen))\b",
    re.I,
)


def _configure_360(text: str) -> ClassificationResult | None:
    if _C360.search(text):
        instruction = re.sub(r"^\s*(please\s+)?" + _C360.pattern + r"\s*[:,-]?\s*", "", text, flags=re.I).strip() or text
        return ClassificationResult("INT-33", {"instruction": instruction}, 0.95, "Matched 'Configure 360' - the rest is the page instruction.")
    if _C360_LOOSE.search(text):
        return ClassificationResult("INT-33", {"instruction": text.strip()}, 0.85, "Talks about the Customer 360 page's panes, charts, look or systems.")
    return None


@dataclass
class ClassificationResult:
    intent_type: str | None
    entities: dict[str, Any] = field(default_factory=dict)
    confidence: float = 0.0
    rationale: str = ""
    # Other plausible intents, best first - offered as "did you mean".
    alternatives: list[str] = field(default_factory=list)


def classify(
    text: str, db: Session | None = None, tenant_id: int | None = None, context: dict[str, Any] | None = None, live: bool = False,
    use_model: bool = True,
) -> ClassificationResult:
    """db/tenant_id pick the tenant's AI provider (app/crm_core/llm.py:
    external API, the platform's own local model, or rules only); both
    are optional - without them the platform default is used. live=True
    is the as-you-type path (a CPU-hosted local model sits it out unless
    the tenant enabled it). Any model failure falls back to the rules.
    use_model=False: the rules only, whatever the tenant's AI setting.
    context = what the user is looking at ({page, entity_type, entity_id,
    label}) - it disambiguates "this"/"it" on both paths.
    """
    from app.crm_core import llm

    c360 = _configure_360(text)
    if c360 is not None:
        return c360
    step = _process_step(text, context)
    if step is not None:
        return step
    custom_intents: list[dict[str, Any]] = []
    if db is not None and tenant_id is not None:
        custom_intents = custom_intent_specs(db, tenant_id)
    target = llm.resolve(db, tenant_id, live=live) if use_model else None
    if target is not None:
        try:
            result = _classify_with_llm(text, target, custom_intents, context, prompt_entries(db, tenant_id))
        except llm.LlmUnavailable as exc:
            result = _classify_with_rules(text, custom_intents, context)
            result.rationale = f"{result.rationale} ({exc} - understood with the built-in rules.)".strip()
            return result
        spec = next((s for s in custom_intents if s["code"] == result.intent_type and not s.get("builtin")), None)
        if spec is not None and not custom_fits(text, spec):
            # a small model drifts to a tenant flow on one shared word ("create a new B2B prospect" -> "NGO Prospect to
            # Account Conversion") - a flow is picked only when the request names it, else the rules decide
            ruled = _classify_with_rules(text, custom_intents, context)
            if ruled.intent_type is not None:
                ruled.alternatives = [c for c in [result.intent_type, *ruled.alternatives] if c != ruled.intent_type][:3]
                ruled.rationale = f"{ruled.rationale} (The model suggested “{spec['label']}”, but the request doesn't name it.)"
                return ruled
            result.confidence = min(result.confidence, 0.4)
            result.rationale += f" - unsure: the request doesn't name “{spec['label']}”."
        return result
    return _classify_with_rules(text, custom_intents, context)


_FIT_STOP = {"the", "and", "for", "with", "this", "that", "into", "from", "your", "our", "new", "all", "any", "one", "run", "flow",
             "intent", "action", "process", "create", "make", "add", "set", "get", "please", "very", "first", "time", "whenever", "when"}


def _stems(text: str) -> set[str]:
    return {w[:5] for w in re.findall(r"[a-z0-9]{2,}", (text or "").lower()) if w not in _FIT_STOP and not w.isdigit()}


def custom_fits(text: str, spec: dict[str, Any]) -> bool:
    """Does the request name this tenant flow - one of its phrases, or most of its name's words
    (not a single shared word, and never just its description)?"""
    lowered = " ".join((text or "").lower().split())
    if any(p and len(p) > 3 and p in lowered for p in spec.get("phrases") or []):
        return True
    said = _stems(text)
    for name in [spec.get("label") or "", *(spec.get("phrases") or [])]:
        words = _stems(name)
        if not words:
            continue
        needed = 1 if len(words) == 1 else max(2, (len(words) + 1) // 2)
        if len(words & said) >= needed:
            return True
    return False


def custom_intent_specs(db: Session, tenant_id: int) -> list[dict[str, Any]]:
    """Tenant-defined intents (Phase 7): every active custom_intent flow
    from the Flow Studio, addressed as FLOW-<id>."""
    from app.intent_engine import intent_registry
    from app.orchestration import flow_engine

    return intent_registry.extra_phrase_specs(db, tenant_id) + [
        {
            "code": flow_engine.custom_intent_code(f),
            "label": f.name,
            "description": f.description or "",
            "phrases": [p.strip().lower() for p in (f.trigger_phrases or "").split(",") if p.strip()],
            "required_fields": flow_engine.required_fields(f),
            "entity_type": f.entity_type,
        }
        for f in flow_engine.custom_intent_flows(db, tenant_id)
    ]


def prompt_entries(db: Session | None, tenant_id: int | None) -> list[dict[str, Any]]:
    """The intents the model may be shown: the tenant's active ones (its labels,
    phrases and flows), else the built-in catalog."""
    from app.intent_engine import intent_registry, shortlist

    if db is None or tenant_id is None:
        return shortlist.builtin_entries()
    return [e for e in intent_registry.all_intents(db, tenant_id) if e["is_active"]]


def _custom_prompt_section(custom_intents: list[dict[str, Any]]) -> str:
    if not custom_intents:
        return ""
    lines = ["", "Tenant-defined intents (choose one only when the text asks for exactly what it does - "
                 "a request that merely shares a word with one, e.g. 'prospect', means the built-in intent above):"]
    for spec in custom_intents:
        if spec.get("builtin"):
            lines.append(f'{spec["code"]} is also what this tenant means by: {"; ".join(spec["phrases"])}.')
            continue
        fields = ", ".join(spec["required_fields"]) or "none required"
        target = f" Target record: {spec['entity_type']} (entity id as '{spec['entity_type']}_id')." if spec["entity_type"] else ""
        lines.append(
            f'{spec["code"]} "{spec["label"]}" - {spec["description"]} Example phrases: '
            f'{"; ".join(spec["phrases"]) or "n/a"}. Entities: {fields}.{target}'
        )
    return "\n".join(lines)


# _SYSTEM_PROMPT in its three parts - the per-request prompt keeps its wording
# and only lists the intents the request could mean (shortlist.py).
_HEAD, _rest = _SYSTEM_PROMPT.split("\nINT-01 ", 1)
_BLOCKS_TEXT, _TAIL = ("INT-01 " + _rest).split("\nInterpret the request", 1)
_TAIL = "Interpret the request" + _TAIL
_BLOCKS = {b.split(" ", 1)[0]: b.strip() for b in re.split(r"\n\s*\n(?=INT-\d+ )", _BLOCKS_TEXT) if b.strip()}
_SHORT_TAIL = (
    _TAIL.replace(' (e.g. "add a new product/plan/package"\n-> INT-01, "approve it" while viewing a quote -> INT-06 for that quote,\n'
                  '"the customer is complaining their internet is down" -> INT-10)', "")
    .replace('"INT-01".."INT-33"|"FLOW-<n>"|"UNKNOWN"', 'one of the codes above|"UNKNOWN"')
)


def _block(entry: dict[str, Any]) -> str:
    """An intent the hand-written prompt doesn't describe, in the same shape."""
    fields = ", ".join(f"{f['key']} ({f.get('type') or 'text'}{'' if f.get('required') else ', optional'})" for f in entry.get("fields") or [])
    examples = "; ".join([*(entry.get("examples") or []), *(entry.get("phrases") or [])][:3])
    return (f'{entry["code"]} "{entry["label"]}" - {(entry.get("description") or "").rstrip(".")}. '
            f'Entities: {fields or "none"}.' + (f" Examples: {examples}." if examples else ""))


def system_prompt(custom_intents: list[dict[str, Any]] | None = None, text: str | None = None,
                  entries: list[dict[str, Any]] | None = None, context: dict[str, Any] | None = None,
                  must_include: tuple[str, ...] = ()) -> str:
    """Without text: the original prompt (what the first fine-tuned model was
    trained on). With the request's text: only the intents it could mean
    (must_include: training's answer, appended when the shortlist missed it)."""
    if text is None:
        return _SYSTEM_PROMPT + _custom_prompt_section(custom_intents or [])
    from app.intent_engine import shortlist

    entries = entries if entries is not None else shortlist.builtin_entries()
    by_code = {e["code"]: e for e in entries}
    codes = shortlist.pick(text, entries, context)
    codes += [c for c in must_include if c in by_code and c not in codes]
    blocks = [_BLOCKS[c] if c in _BLOCKS and not by_code[c].get("kind") == "custom" else _block(by_code[c])
              for c in codes if c in by_code and not c.startswith("FLOW-")]
    custom = [s for s in custom_intents or [] if s["code"] in codes]
    return (_HEAD + "\n" + "\n\n".join(blocks) + "\n\n" + _SHORT_TAIL.rstrip("\n") + _custom_prompt_section(custom))


def user_prompt(text: str, context: dict[str, Any] | None = None) -> str:
    return text + (f"\n\nContext: {understanding.describe_context(context)}" if context else "")


def _classify_with_llm(
    text: str,
    target: Any,
    custom_intents: list[dict[str, Any]] | None = None,
    context: dict[str, Any] | None = None,
    entries: list[dict[str, Any]] | None = None,
) -> ClassificationResult:
    from app.crm_core import llm

    custom_intents = custom_intents or []
    parsed = llm.chat_json(target, system_prompt(custom_intents, text, entries, context), user_prompt(text, context))

    known = set(SUPPORTED_INTENTS) | {s["code"] for s in custom_intents}
    intent_type = parsed.get("intent_type")
    if intent_type not in known:
        intent_type = None
    alternatives = [a for a in parsed.get("alternatives") or [] if a in known and a != intent_type][:3]
    try:
        confidence = max(0.0, min(1.0, float(parsed.get("confidence", 0.0))))
    except (TypeError, ValueError):
        confidence = 0.5
    entities = parsed.get("entities")
    return ClassificationResult(
        intent_type=intent_type,
        entities=entities if isinstance(entities, dict) else {},
        confidence=confidence,
        rationale=str(parsed.get("rationale", "")) + f" [{target.label}]",
        alternatives=alternatives,
    )


_FIELD_PATTERN = re.compile(r"([a-zA-Z_][a-zA-Z0-9_ ]*?)\s*:\s*([^,\n]+)")


def _extract_fields(text: str) -> dict[str, str]:
    fields: dict[str, str] = {}
    for key, value in _FIELD_PATTERN.findall(text):
        normalized_key = key.strip().lower().replace(" ", "_")
        fields[normalized_key] = value.strip()
    return fields


def _as_int(fields: dict[str, str], key: str) -> int | None:
    value = fields.get(key)
    try:
        return int(value) if value is not None else None
    except ValueError:
        return None


def _as_float(fields: dict[str, str], key: str, default: float = 0.0) -> float:
    try:
        return float(fields[key]) if key in fields else default
    except ValueError:
        return default


def _coerce_ids(fields: dict[str, str]) -> dict[str, Any]:
    return {k: (int(v) if (k == "entity_id" or k.endswith("_id")) and v.isdigit() else v) for k, v in fields.items()}


def _classify_with_rules(
    text: str, custom_intents: list[dict[str, Any]] | None = None, context: dict[str, Any] | None = None
) -> ClassificationResult:
    """Keyword parser first (its exact behaviour is what the test suite
    pins), then the synonym ranking in understanding.py for everything the
    keywords miss - or when they land on an intent the text has no
    evidence for. Ranked matches stay below the auto-execute threshold so
    they are always confirmed first."""
    ranked = understanding.rank_intents(text, custom_intents, (context or {}).get("entity_type"))
    result = _classify_with_keywords(text, custom_intents, context)
    ranked_codes = [code for code, _ in ranked]
    choice = understanding.pick(ranked)

    if result.intent_type is not None and not (
        result.confidence < 0.75 and choice and choice != result.intent_type and result.intent_type not in ranked_codes
    ):
        if result.confidence < 0.75:
            result.alternatives = [c for c in ranked_codes if c != result.intent_type][:3]
        return result

    if choice is None:
        return ClassificationResult(
            None, {}, 0.0,
            "Not sure which action you mean" + (" - see the suggestions." if ranked else "."),
            alternatives=ranked_codes[:3],
        )
    loose = understanding.extract_loose_entities(text)
    entities = understanding.entities_for(choice, {**loose, **_coerce_ids(_extract_fields(text))}, text)
    labels = {**INTENT_LABELS, **{s["code"]: s["label"] for s in custom_intents or []}}
    return ClassificationResult(
        choice, entities, 0.6,
        f"Understood as \"{labels.get(choice, choice)}\" from the wording - confirm to run it.",
        alternatives=[c for c in ranked_codes if c != choice][:3],
    )


def _classify_with_keywords(
    text: str, custom_intents: list[dict[str, Any]] | None = None, context: dict[str, Any] | None = None
) -> ClassificationResult:
    result = _keyword_chain(text, custom_intents, context)
    context_key = understanding.context_id_field(context)
    if context_key and result.entities.get(context_key) == context["entity_id"] and context_key not in _stated_fields(text):
        result.rationale += f" Used {context['entity_type']} #{context['entity_id']} from the record you are viewing."
    return result


def _stated_fields(text: str) -> dict[str, Any]:
    fields: dict[str, Any] = dict(_extract_fields(text))
    for key, value in understanding.extract_loose_entities(text).items():
        fields.setdefault(key, value)
    return fields


def _keyword_chain(
    text: str, custom_intents: list[dict[str, Any]] | None = None, context: dict[str, Any] | None = None
) -> ClassificationResult:
    lowered = text.lower()
    if "inventory" in lowered and any(v in lowered for v in ("sync", "refresh", "import", "load")) and any(v in lowered for v in ("check types", "feasibility")):
        return ClassificationResult("INT-34", {}, 0.95, "Matched inventory check type synchronization")
    fields = _extract_fields(text)
    # Values written naturally ("quote 12", "10%", "₹999", "called X") fill
    # in for any `field: value` the text did not spell out...
    for key, value in understanding.extract_loose_entities(text).items():
        if key not in fields:
            fields[key] = " + ".join(value) if isinstance(value, list) else str(value)
    # ...and the record on screen stands in for "this quote"/"it", which
    # also steers approve/proposal/revision rules to the right record type.
    context_key = understanding.context_id_field(context)
    if context_key and context_key not in fields:
        fields[context_key] = str(context["entity_id"])

    # Tenant-defined intents first - their phrases are the tenant's own,
    # more specific vocabulary than the built-in keywords below.
    for spec in custom_intents or []:
        if any(phrase in lowered for phrase in spec["phrases"]):
            entities = _coerce_ids(fields)
            missing = [f for f in spec["required_fields"] if entities.get(f) in (None, "")]
            confidence = 0.4 if missing else 0.9
            return ClassificationResult(spec["code"], entities, confidence, f"Matched custom intent phrase for '{spec['label']}'")

    # Phase 7 generic lifecycle intents - keyed on the explicit
    # entity_type field, so they never steal text meant for INT-02's
    # "modify/update a bundle" or INT-12's field dispatch.
    if "entity_type" in fields:
        entities: dict[str, Any] = {
            "entity_type": fields["entity_type"].strip().lower(),
            "entity_id": _as_int(fields, "entity_id"),
        }
        rest = {k: v for k, v in fields.items() if k not in ("entity_type", "entity_id")}
        if re.search(r"\b(assign|reassign|transfer|hand over|route)\b", lowered):
            entities.update(
                to_user=rest.get("to_user") or rest.get("user"),
                to_position=rest.get("to_position") or rest.get("position"),
                reason=rest.get("reason") or rest.get("reasoning"),
            )
            if rest.get("to_department") or rest.get("department"):
                entities["to_department"] = rest.get("to_department") or rest.get("department")
            if rest.get("include_related") not in (None, ""):
                entities["include_related"] = rest["include_related"]
            ok = entities["entity_id"] and (entities["to_user"] or entities["to_position"] or entities.get("to_department"))
            return ClassificationResult("INT-32", entities, 0.9 if ok else 0.4, "Matched 'assign'/'transfer' + entity_type")
        if "to_state" in rest or "action" in rest or re.search(r"\b(move|transition|mark|close|reopen|deactivate|reactivate)\b", lowered):
            entities.update(action=rest.get("action"), to_state=rest.get("to_state"), reasoning=rest.get("reasoning"))
            ok = entities["entity_id"] and (entities["action"] or entities["to_state"])
            return ClassificationResult("INT-31", entities, 0.9 if ok else 0.4, "Matched lifecycle action + entity_type")
        if re.search(r"\b(update|edit|modify|change|save|set)\b", lowered):
            entities["fields"] = rest
            ok = entities["entity_id"] and rest
            return ClassificationResult("INT-29", entities, 0.9 if ok else 0.4, "Matched 'update' + entity_type + fields")

    if "revision" in lowered and "quote_id" in fields:
        entities = {
            "quote_id": _as_int(fields, "quote_id"),
            "requested_discount_pct": _as_float(fields, "discount") if "discount" in fields else None,
            "quantity": _as_int(fields, "quantity"),
        }
        return ClassificationResult("INT-30", entities, 0.9 if entities["quote_id"] else 0.4, "Matched 'revision' + quote_id")

    if "launch" in lowered:
        composition_raw = fields.get("composition", "")
        composition = [p.strip() for p in re.split(r"[,+]", composition_raw) if p.strip()]
        entities = {
            "name": fields.get("name", "").strip(),
            "composition": composition,
            "segment": fields.get("segment", "B2C"),
            "channel": fields.get("channel", fields.get("segment", "B2C")),
            "region": fields.get("region", "ALL"),
            "list_price": _as_float(fields, "price"),
            "requested_discount_pct": _as_float(fields, "discount"),
        }
        confidence = 0.9 if entities["name"] and composition and entities["list_price"] else 0.4
        return ClassificationResult("INT-01", entities, confidence, "Matched keyword 'launch' + parsed fields")

    if "modify" in lowered or "update" in lowered:
        entities = {"offering_name": fields.get("offering_name", fields.get("name", "")).strip()}
        if "new_price" in fields:
            entities["new_list_price"] = _as_float(fields, "new_price")
        if "new_segment" in fields:
            entities["new_segment"] = fields["new_segment"]
        if "new_channel" in fields:
            entities["new_channel"] = fields["new_channel"]
        confidence = 0.85 if entities["offering_name"] else 0.4
        return ClassificationResult("INT-02", entities, confidence, "Matched keyword 'modify'/'update' + parsed fields")

    if "retire" in lowered:
        entities = {
            "offering_name": fields.get("offering_name", fields.get("name", "")).strip(),
            "replacement_offering_name": fields.get("replacement_offering_name", fields.get("replacement", "")).strip()
            or None,
        }
        confidence = 0.85 if entities["offering_name"] else 0.4
        return ClassificationResult("INT-03", entities, confidence, "Matched keyword 'retire' + parsed fields")

    if "prospect" in lowered:
        entities = {
            "name": fields.get("name", "").strip(),
            "email": fields.get("email"),
            "phone": fields.get("phone"),
        }
        confidence = 0.9 if entities["name"] else 0.4
        return ClassificationResult("INT-15", entities, confidence, "Matched keyword 'prospect' + parsed fields")

    if "lead_id" in fields and ("product" in lowered or "offering" in lowered) and "approve" not in lowered and "reject" not in lowered:
        entities = {
            "lead_id": _as_int(fields, "lead_id"),
            "offering_name": fields.get("offering_name", "").strip(),
        }
        confidence = 0.85 if entities["lead_id"] and entities["offering_name"] else 0.4
        return ClassificationResult("INT-16", entities, confidence, "Matched field 'lead_id' + 'product'/'offering' + parsed fields")

    if ("approve" in lowered or "reject" in lowered) and "lead_id" in fields:
        decision = "approve" if "approve" in lowered and "reject" not in lowered else (
            "reject" if "reject" in lowered else fields.get("decision", "approve")
        )
        entities = {
            "lead_id": _as_int(fields, "lead_id"),
            "decision": fields.get("decision", decision),
            "reasoning": fields.get("reasoning"),
        }
        confidence = 0.9 if entities["lead_id"] else 0.4
        return ClassificationResult("INT-17", entities, confidence, "Matched keyword 'approve'/'reject' + 'lead' + parsed fields")

    if "proposal" in lowered and "quote_id" in fields:
        entities = {"quote_id": _as_int(fields, "quote_id")}
        confidence = 0.85 if entities["quote_id"] else 0.4
        return ClassificationResult("INT-22", entities, confidence, "Matched keyword 'proposal' + 'quote_id' field")

    if "proposal" in lowered and "proposal_id" in fields:
        decision = "approve" if "approve" in lowered and "reject" not in lowered else (
            "reject" if "reject" in lowered else fields.get("decision", "approve")
        )
        entities = {"proposal_id": _as_int(fields, "proposal_id"), "decision": fields.get("decision", decision)}
        confidence = 0.85 if entities["proposal_id"] else 0.4
        return ClassificationResult("INT-23", entities, confidence, "Matched keyword 'proposal' + 'proposal_id' field")

    if "generate" in lowered and "order" in lowered and "quote_id" in fields:
        entities = {"quote_id": _as_int(fields, "quote_id")}
        confidence = 0.85 if entities["quote_id"] else 0.4
        return ClassificationResult("INT-24", entities, confidence, "Matched keyword 'generate'/'order' + 'quote_id' field")

    if ("approve" in lowered or "reject" in lowered) and "order_id" in fields:
        decision = "approve" if "approve" in lowered and "reject" not in lowered else (
            "reject" if "reject" in lowered else fields.get("decision", "approve")
        )
        entities = {
            "order_id": _as_int(fields, "order_id"),
            "decision": fields.get("decision", decision),
            "reasoning": fields.get("reasoning"),
        }
        confidence = 0.9 if entities["order_id"] else 0.4
        return ClassificationResult("INT-25", entities, confidence, "Matched keyword 'approve'/'reject' + 'order_id' field")

    if "installation" in lowered and "work_order_id" in fields:
        entities = {
            "work_order_id": _as_int(fields, "work_order_id"),
            "technical_parameters": fields.get("technical_parameters", ""),
        }
        confidence = 0.85 if entities["work_order_id"] else 0.4
        return ClassificationResult("INT-26", entities, confidence, "Matched keyword 'installation' + 'work_order_id' field")

    if "connectivity" in lowered and "work_order_id" in fields:
        entities = {
            "work_order_id": _as_int(fields, "work_order_id"),
            "signature": fields.get("signature", ""),
        }
        confidence = 0.85 if entities["work_order_id"] else 0.4
        return ClassificationResult("INT-27", entities, confidence, "Matched keyword 'connectivity' + 'work_order_id' field")

    if "letter" in lowered and "work_order_id" in fields:
        entities = {
            "work_order_id": _as_int(fields, "work_order_id"),
            "document_ref": fields.get("document_ref"),
        }
        confidence = 0.85 if entities["work_order_id"] else 0.4
        return ClassificationResult("INT-28", entities, confidence, "Matched keyword 'letter' + 'work_order_id' field")

    if "feasibility" in lowered and "opportunity_id" in fields:
        entities = {
            "opportunity_id": _as_int(fields, "opportunity_id"),
            "address": fields.get("address", fields.get("site")),
        }
        # Presales' conclusion, when stated (Sales-to-Order).
        entities.update({k: fields[k] for k in ("decision", "notes") if fields.get(k)})
        confidence = 0.85 if entities["opportunity_id"] else 0.4
        return ClassificationResult("INT-18", entities, confidence, "Matched keyword 'feasibility' + parsed fields")

    if "qualify" in lowered:
        entities = {
            "company_name": fields.get("company_name", fields.get("name", "")).strip(),
            "source": fields.get("source", "unknown"),
            "region": fields.get("region", "ALL"),
        }
        confidence = 0.9 if entities["company_name"] else 0.4
        return ClassificationResult("INT-04", entities, confidence, "Matched keyword 'qualify' + parsed fields")

    if "configure" in lowered:
        entities = {
            "opportunity_id": _as_int(fields, "opportunity_id"),
            "offering_name": fields.get("offering_name", "").strip(),
            "quantity": _as_int(fields, "quantity") or 1,
            "requested_discount_pct": _as_float(fields, "discount"),
            "discount_justification": fields.get("justification"),
        }
        confidence = 0.9 if entities["opportunity_id"] and entities["offering_name"] else 0.4
        return ClassificationResult("INT-05", entities, confidence, "Matched keyword 'configure' + parsed fields")

    if "approve" in lowered or "reject" in lowered:
        decision = "approve" if "approve" in lowered and "reject" not in lowered else (
            "reject" if "reject" in lowered else fields.get("decision", "approve")
        )
        entities = {
            "quote_id": _as_int(fields, "quote_id"),
            "decision": fields.get("decision", decision),
            "reasoning": fields.get("reasoning"),
        }
        confidence = 0.9 if entities["quote_id"] else 0.4
        return ClassificationResult("INT-06", entities, confidence, "Matched keyword 'approve'/'reject' + parsed fields")

    if "activate" in lowered:
        sites_raw = fields.get("sites", "")
        sites = [s.strip() for s in re.split(r"[,+]", sites_raw) if s.strip()]
        entities = {
            "quote_id": _as_int(fields, "quote_id"),
            "service_account_name": fields.get("service_account_name"),
            "sites": sites,
        }
        confidence = 0.9 if entities["quote_id"] else 0.4
        return ClassificationResult("INT-07", entities, confidence, "Matched keyword 'activate' + parsed fields")

    if "provision" in lowered and ("site" in lowered or "end" in lowered):
        entities = {
            "order_id": _as_int(fields, "order_id"),
            "site_label": fields.get("site_label", "").strip(),
        }
        confidence = 0.9 if entities["order_id"] and entities["site_label"] else 0.4
        return ClassificationResult("INT-14", entities, confidence, "Matched keyword 'provision'/'site' + parsed fields")

    if "revise" in lowered and "order" in lowered:
        entities = {
            "order_id": _as_int(fields, "order_id"),
            "new_monthly_price": _as_float(fields, "new_price") if "new_price" in fields else None,
            "new_quantity": _as_int(fields, "new_quantity"),
        }
        confidence = 0.85 if entities["order_id"] else 0.4
        return ClassificationResult("INT-21", entities, confidence, "Matched keyword 'revise' + 'order' + parsed fields")

    if "revise" in lowered:
        entities = {
            "quote_id": _as_int(fields, "quote_id"),
            "requested_discount_pct": _as_float(fields, "discount") if "discount" in fields else None,
            "quantity": _as_int(fields, "quantity"),
        }
        confidence = 0.85 if entities["quote_id"] else 0.4
        return ClassificationResult("INT-19", entities, confidence, "Matched keyword 'revise' + parsed fields")

    if "cancel" in lowered and "quote" in lowered:
        entities = {
            "quote_id": _as_int(fields, "quote_id"),
            "reasoning": fields.get("reasoning"),
        }
        confidence = 0.85 if entities["quote_id"] else 0.4
        return ClassificationResult("INT-20", entities, confidence, "Matched keyword 'cancel' + 'quote' + parsed fields")

    if "upgrade" in lowered or "relocate" in lowered or "change this service" in lowered:
        entities = {
            "order_id": _as_int(fields, "order_id"),
            "new_monthly_price": _as_float(fields, "new_price"),
        }
        confidence = 0.85 if entities["order_id"] else 0.4
        return ClassificationResult("INT-08", entities, confidence, "Matched keyword 'upgrade'/'relocate' + parsed fields")

    if "suspend" in lowered or "cancel" in lowered or "terminate" in lowered or "retain" in lowered:
        action = "suspend" if "suspend" in lowered else ("terminate" if "terminate" in lowered else "cancel")
        entities = {
            "order_id": _as_int(fields, "order_id"),
            "action": fields.get("action", action),
            "decision": fields.get("decision"),
        }
        confidence = 0.85 if entities["order_id"] else 0.4
        return ClassificationResult("INT-09", entities, confidence, "Matched keyword 'suspend'/'cancel'/'terminate' + parsed fields")

    if "verify" in lowered and ("identity" in lowered or "kyc" in lowered):
        entities = {
            "party_id": _as_int(fields, "party_id"),
            "id_number": fields.get("id_number"),
            "id_type": fields.get("id_type", "national_id"),
            "provider": fields.get("provider"),
        }
        confidence = 0.9 if entities["party_id"] and entities["id_number"] else 0.4
        return ClassificationResult("INT-13", entities, confidence, "Matched keyword 'verify identity'/'kyc' + parsed fields")

    if "complaint" in lowered:
        entities = {
            "description": fields.get("description", text).strip(),
            "company_name": fields.get("company_name"),
            "sla_class": fields.get("sla_class", "standard"),
        }
        confidence = 0.9 if entities["description"] else 0.4
        return ClassificationResult("INT-10", entities, confidence, "Matched keyword 'complaint' + parsed fields")

    if "diagnose" in lowered:
        entities = {"ticket_id": _as_int(fields, "ticket_id")}
        confidence = 0.9 if entities["ticket_id"] else 0.4
        return ClassificationResult("INT-11", entities, confidence, "Matched keyword 'diagnose' + parsed fields")

    if "dispatch" in lowered or ("complete" in lowered and "work_order_id" in fields):
        entities = {
            "ticket_id": _as_int(fields, "ticket_id"),
            "order_id": _as_int(fields, "order_id"),
            "required_skill": fields.get("required_skill", "general"),
            "region": fields.get("region", "ALL"),
            "scheduled_window": fields.get("scheduled_window", "next business day"),
            "work_order_id": _as_int(fields, "work_order_id"),
            "action": "complete" if "complete" in lowered else "dispatch",
        }
        confidence = 0.85 if (entities["ticket_id"] or entities["order_id"] or entities["work_order_id"]) else 0.4
        return ClassificationResult("INT-12", entities, confidence, "Matched keyword 'dispatch'/'complete' + parsed fields")

    return ClassificationResult(None, {}, 0.0, "No known intent keyword found")

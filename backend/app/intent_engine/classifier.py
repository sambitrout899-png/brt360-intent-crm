"""NLU/LLM Intent Classifier (HLD 4.1).

Primary path: OpenAI structured-output classification (requires
OPENAI_API_KEY). Fallback path: a deterministic, keyword + key:value field
parser - fully functional for dev/test with zero external calls or cost, and
exercised directly by the pytest suite so CI never needs a real API key.

Fallback phrasing convention (documented here, not a general NLP system):
free text may contain `field: value` pairs (comma- or newline-separated,
list-valued fields use `+` between items since `,` is the field separator),
read in addition to a keyword that selects the intent type. See each
INT-xx block in _classify_with_rules for its keyword + expected fields.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from app.config import get_settings

SUPPORTED_INTENTS = (
    "INT-01", "INT-02", "INT-03",
    "INT-04", "INT-05", "INT-06",
    "INT-07", "INT-08", "INT-09",
    "INT-10", "INT-11", "INT-12",
    "INT-13", "INT-14",
    "INT-15", "INT-16", "INT-17", "INT-18", "INT-19", "INT-20", "INT-21",
)

_SYSTEM_PROMPT = """You are the NLU classifier for BRT 360, a telecom Intent CRM.
Classify the user's free text into exactly one of these 14 intent types, or UNKNOWN:

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

Respond with strict JSON only:
{"intent_type": "INT-01".."INT-21"|"UNKNOWN", "entities": {...},
 "confidence": 0.0-1.0, "rationale": "one sentence explaining the classification"}
"""


@dataclass
class ClassificationResult:
    intent_type: str | None
    entities: dict[str, Any] = field(default_factory=dict)
    confidence: float = 0.0
    rationale: str = ""


def classify(text: str) -> ClassificationResult:
    settings = get_settings()
    if settings.openai_api_key:
        return _classify_with_openai(text, settings.openai_api_key, settings.openai_model)
    return _classify_with_rules(text)


def _classify_with_openai(text: str, api_key: str, model: str) -> ClassificationResult:
    from openai import OpenAI

    client = OpenAI(api_key=api_key)
    response = client.chat.completions.create(
        model=model,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": text},
        ],
        temperature=0,
    )
    raw = response.choices[0].message.content or "{}"
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return ClassificationResult(None, {}, 0.0, "Classifier returned invalid JSON")

    intent_type = parsed.get("intent_type")
    if intent_type not in SUPPORTED_INTENTS:
        intent_type = None
    return ClassificationResult(
        intent_type=intent_type,
        entities=parsed.get("entities", {}),
        confidence=float(parsed.get("confidence", 0.0)),
        rationale=parsed.get("rationale", ""),
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


def _classify_with_rules(text: str) -> ClassificationResult:
    lowered = text.lower()
    fields = _extract_fields(text)

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

    if "feasibility" in lowered and "opportunity_id" in fields:
        entities = {
            "opportunity_id": _as_int(fields, "opportunity_id"),
            "address": fields.get("address", fields.get("site")),
        }
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

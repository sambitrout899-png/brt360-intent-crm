"""NLU/LLM Intent Classifier (HLD 4.1).

Primary path: OpenAI structured-output classification (requires
OPENAI_API_KEY). Fallback path: a deterministic, keyword + key:value field
parser - fully functional for dev/test with zero external calls or cost, and
exercised directly by the pytest suite so CI never needs a real API key.

Fallback phrasing convention (documented here, not a general NLP system):
free text may contain `field: value` pairs (comma- or newline-separated),
read in addition to a keyword that selects the intent type:

  "Launch a new bundle. name: Fiber 500 Bundle, composition: Fiber500, VOIP,
   segment: B2C, channel: B2C, price: 999, discount: 10"

  "Modify this bundle. offering_name: Fiber 500 Bundle, new_price: 899"

  "Retire this bundle. offering_name: Fiber 100 Bundle,
   replacement_offering_name: Fiber 500 Bundle"
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from app.config import get_settings

SUPPORTED_INTENTS = ("INT-01", "INT-02", "INT-03")

_SYSTEM_PROMPT = """You are the NLU classifier for BRT 360, a telecom Intent CRM.
Classify the user's free text into exactly one of these intent types, or UNKNOWN:

INT-01 "Launch a new bundle" - entities: name (str), composition (list[str] of
  product names, first is the primary connectivity line), segment (str, e.g.
  B2C/B2B-SMB), channel (str, B2C/B2B), region (str, default ALL),
  list_price (number), requested_discount_pct (number, default 0).

INT-02 "Modify a live bundle" - entities: offering_name (str, the existing
  bundle's name), new_list_price (number, optional), new_segment (str,
  optional), new_channel (str, optional).

INT-03 "Retire a bundle" - entities: offering_name (str), replacement_offering_name
  (str - required per policy; if the user gives no replacement, still extract
  offering_name and omit replacement_offering_name).

Respond with strict JSON only:
{"intent_type": "INT-01"|"INT-02"|"INT-03"|"UNKNOWN", "entities": {...},
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
            "list_price": float(fields["price"]) if "price" in fields else 0.0,
            "requested_discount_pct": float(fields.get("discount", 0)),
        }
        confidence = 0.9 if entities["name"] and composition and entities["list_price"] else 0.4
        return ClassificationResult("INT-01", entities, confidence, "Matched keyword 'launch' + parsed fields")

    if "modify" in lowered or "update" in lowered:
        entities = {
            "offering_name": fields.get("offering_name", fields.get("name", "")).strip(),
        }
        if "new_price" in fields:
            entities["new_list_price"] = float(fields["new_price"])
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

    return ClassificationResult(None, {}, 0.0, "No known intent keyword found")

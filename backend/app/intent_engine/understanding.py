"""Looser natural-language understanding for the Intent Engine.

The no-key fallback classifier (classifier._classify_with_rules) matches
one trigger keyword per intent ("launch", "qualify", ...), so ordinary
phrasings like "add a new product" or "approve quote 12" fell through to
UNKNOWN. This module adds, without any external calls:

- extract_loose_entities: ids, emails, prices, discounts, quantities and
  names written the way people actually write them ("quote 12", "10% off",
  "for ₹999", "called Fiber Max") rather than as `field: value` pairs.
- rank_intents: synonym-based scoring of every intent (verb + object
  vocabulary), so a phrasing can be matched without its exact keyword,
  and the runners-up can be offered back as "did you mean" suggestions.
- apply_context: fills the record ids a request leaves implicit ("approve
  this") from what the user is looking at - the open record panel or page.

Everything here only *proposes*: a match found by ranking stays below the
auto-execute threshold, so it is confirmed (and its missing fields asked
for) before anything runs.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

# ---------------------------------------------------------------- vocabulary

_VERBS: dict[str, set[str]] = {
    "create": {"add", "create", "new", "launch", "introduce", "setup", "make", "register", "onboard", "build", "start", "open", "capture", "enter", "log"},
    "modify": {"modify", "update", "change", "edit", "adjust", "reprice", "increase", "decrease", "raise", "lower", "amend", "set", "rename", "correct", "fix"},
    "retire": {"retire", "discontinue", "withdraw", "sunset", "deprecate", "decommission", "remove", "delete", "kill"},
    "approve": {"approve", "accept", "ok", "okay", "greenlight", "authorise", "authorize", "signoff", "confirm", "reject", "decline", "deny", "refuse"},
    "cancel": {"cancel", "terminate", "stop", "end", "suspend", "pause", "disconnect", "churn", "retain", "deactivate"},
    "activate": {"activate", "provision", "golive", "enable", "turnon", "connect", "install", "fulfil", "fulfill"},
    "diagnose": {"diagnose", "troubleshoot", "investigate", "analyse", "analyze", "rootcause", "why"},
    "dispatch": {"dispatch", "send", "schedule", "assign", "book"},
    "quote": {"quote", "configure", "estimate"},
    "verify": {"verify", "kyc", "validate", "check", "authenticate"},
    "revise": {"revise", "revision", "rework", "redo", "renegotiate"},
    "assign": {"assign", "reassign", "transfer", "handover", "route", "delegate", "give", "move"},
    "transition": {"close", "win", "won", "lose", "lost", "reopen", "mark", "move", "progress", "advance", "reactivate", "deactivate"},
    "generate": {"generate", "create", "produce", "draft", "prepare", "send", "make", "raise"},
    "qualify": {"qualify", "signup", "prospect", "convert"},
    "complain": {"complain", "complaint", "issue", "problem", "outage", "down", "broken", "fault", "slow", "fail", "failing", "failed", "error", "notworking"},
    "feasibility": {"feasibility", "feasible", "coverage", "availability", "serviceable", "survey"},
    "close": {"close", "complete", "finish", "done", "sign", "signed"},
}

_NOUNS: dict[str, set[str]] = {
    "product": {"product", "bundle", "offering", "plan", "package", "tariff", "sku", "catalog", "catalogue"},
    "lead": {"lead"},
    "prospect": {"prospect", "customer", "client", "account", "company", "contact", "party", "business"},
    "opportunity": {"opportunity", "opp", "deal"},
    "quote": {"quote", "quotation"},
    "proposal": {"proposal"},
    "order": {"order"},
    "service": {"service", "subscription", "connection", "line", "circuit", "internet", "broadband", "wifi", "network", "phone", "speed", "signal"},
    "ticket": {"ticket", "case", "incident"},
    "technician": {"technician", "engineer", "tech", "crew", "field", "visit"},
    "identity": {"identity", "id", "kyc", "passport", "iqama"},
    "site": {"site", "aend", "zend", "end", "location"},
    "workorder": {"workorder", "wo"},
    "installation": {"installation", "install"},
    "connectivity": {"connectivity", "acceptance", "test"},
    "letter": {"letter", "certificate"},
    "record": {"record", "lead", "opportunity", "deal", "quote", "order", "customer", "prospect", "party"},
}

# Multi-word expressions collapsed into single tokens before matching.
_PHRASES = {
    "set up": "setup", "sign up": "signup", "sign off": "signoff", "go live": "golive", "turn on": "turnon",
    "hand over": "handover", "root cause": "rootcause", "not working": "notworking", "work order": "workorder",
    "a-end": "aend", "z-end": "zend", "a end": "aend", "z end": "zend",
}


@dataclass(frozen=True)
class _Spec:
    code: str
    verbs: tuple[str, ...]          # any of these verb groups
    nouns: tuple[str, ...]          # any of these noun groups
    all_nouns: tuple[str, ...] = ()  # and every one of these noun groups
    boost: tuple[str, ...] = ()      # literal substrings worth extra
    verb_alone: tuple[str, ...] = ()  # verb groups that need no object ("my internet keeps dropping")


_SPECS: tuple[_Spec, ...] = (
    _Spec("INT-01", ("create",), ("product",)),
    _Spec("INT-02", ("modify",), ("product",)),
    _Spec("INT-03", ("retire",), ("product",), boost=("replace",)),
    _Spec("INT-04", ("qualify", "create"), ("lead",), boost=("qualify", "new lead")),
    _Spec("INT-15", ("create",), ("prospect",), boost=("prospect",)),
    _Spec("INT-05", ("quote",), ("product", "opportunity"), boost=("quote for", "configure")),
    _Spec("INT-06", ("approve",), ("quote",)),
    _Spec("INT-07", ("activate",), ("order", "service", "quote"), boost=("end-to-end", "end to end")),
    _Spec("INT-08", ("modify",), ("service",), boost=("upgrade", "downgrade", "relocate")),
    _Spec("INT-09", ("cancel",), ("service", "order")),
    _Spec("INT-10", ("complain",), ("ticket", "service"), boost=("complain", "not working", "outage", "is down"), verb_alone=("complain",)),
    _Spec("INT-11", ("diagnose",), ("ticket", "service")),
    _Spec("INT-12", ("dispatch",), ("technician",)),
    _Spec("INT-13", ("verify",), ("identity",)),
    _Spec("INT-14", ("activate",), ("site",)),
    _Spec("INT-16", ("create", "modify"), ("product",), all_nouns=("lead",)),
    _Spec("INT-17", ("approve",), ("lead",)),
    _Spec("INT-18", ("feasibility", "verify"), ("opportunity", "site"), boost=("feasibility",)),
    _Spec("INT-19", ("revise", "modify"), ("quote",)),
    _Spec("INT-20", ("cancel", "retire"), ("quote",)),
    _Spec("INT-21", ("revise", "modify"), ("order",)),
    _Spec("INT-22", ("generate",), ("proposal",)),
    _Spec("INT-23", ("approve",), ("proposal",), boost=("customer approv", "customer accept")),
    _Spec("INT-24", ("generate",), ("order",), all_nouns=("quote",)),
    _Spec("INT-25", ("approve",), ("order",)),
    _Spec("INT-26", ("close",), ("workorder", "installation"), all_nouns=("installation",)),
    _Spec("INT-27", ("close",), ("workorder", "connectivity"), all_nouns=("connectivity",)),
    _Spec("INT-28", ("close",), ("workorder", "letter"), all_nouns=("letter",)),
    _Spec("INT-29", ("modify",), ("record",)),
    _Spec("INT-30", ("revise",), ("quote",), boost=("new revision", "new version")),
    _Spec("INT-31", ("transition",), ("record",), boost=("close won", "closed won", "close lost", "closed lost", "mark as")),
    _Spec("INT-32", ("assign",), ("record",)),
)

# Which noun group a record type counts as when it is only implied by context.
_ENTITY_NOUN = {"party": "prospect", "lead": "lead", "opportunity": "opportunity", "quote": "quote", "order": "order"}


def _tokens(text: str) -> set[str]:
    lowered = text.lower()
    for phrase, token in _PHRASES.items():
        lowered = lowered.replace(phrase, f" {token} ")
    words = re.findall(r"[a-z][a-z0-9]*", lowered)
    out: set[str] = set()
    for w in words:
        out.add(w)
        if len(w) > 4 and w.endswith("ies"):
            out.add(w[:-3] + "y")
        elif len(w) > 3 and w.endswith("s"):
            out.add(w[:-1])
        if len(w) > 5 and w.endswith("ing"):
            out.add(w[:-3])
            out.add(w[:-3] + "e")
        if len(w) > 4 and w.endswith("ed"):
            out.add(w[:-2])
            out.add(w[:-1])
    return out


def rank_intents(
    text: str,
    custom_intents: list[dict[str, Any]] | None = None,
    context_entity_type: str | None = None,
) -> list[tuple[str, float]]:
    """Every intent scored by verb + object vocabulary, best first; only
    intents with some evidence are returned. A record the user is looking
    at counts as the object when the text names none ("approve this")."""
    tokens = _tokens(text)
    lowered = text.lower()

    def hit(groups: tuple[str, ...], table: dict[str, set[str]]) -> bool:
        return any(tokens & table[g] for g in groups)

    implied = _ENTITY_NOUN.get(context_entity_type or "")
    any_noun = any(tokens & words for words in _NOUNS.values() if words is not _NOUNS["record"])

    scores: dict[str, float] = {}
    for spec in _SPECS:
        noun_hit = hit(spec.nouns, _NOUNS) or (bool(spec.verb_alone) and hit(spec.verb_alone, _VERBS))
        if not noun_hit and implied and not any_noun and (implied in spec.nouns or "record" in spec.nouns):
            noun_hit = True
        if not noun_hit or (spec.all_nouns and not all(tokens & _NOUNS[g] for g in spec.all_nouns)):
            continue
        score = 2.0 + (2.0 if hit(spec.verbs, _VERBS) else 0.0)
        score += sum(1.5 for b in spec.boost if b in lowered)
        score += 0.5 * len(spec.all_nouns)  # more specific specs win ties
        if spec.code == "INT-29" and tokens & _NOUNS["product"]:
            continue  # "update a product" is INT-02, not a generic record edit
        if spec.code in _GENERIC_RECORD_INTENTS:
            score -= 0.5  # a specific intent for the same object wins
        scores[spec.code] = score

    for spec in custom_intents or []:
        phrase_hit = any(p and p in lowered for p in spec.get("phrases", []))
        label_tokens = _tokens(spec.get("label", "")) - {"this", "the", "a", "an", "to", "of", "for"}
        overlap = len(tokens & label_tokens)
        if phrase_hit or overlap >= 2:
            # max: a built-in intent's admin-added phrases only ever add evidence
            scores[spec["code"]] = max(scores.get(spec["code"], 0.0), 5.0 if phrase_hit else 1.5 + overlap)

    return sorted(scores.items(), key=lambda kv: kv[1], reverse=True)


def pick(ranked: list[tuple[str, float]]) -> str | None:
    """A ranked intent is chosen only with a verb+object match and a clear
    lead over the runner-up; otherwise the caller offers suggestions."""
    if not ranked or ranked[0][1] < 3.5:
        return None
    if len(ranked) > 1 and ranked[0][1] - ranked[1][1] < 0.5:
        return None
    return ranked[0][0]


# ---------------------------------------------------------------- entities

_ID_PATTERN = re.compile(
    r"\b(work\s*order|wo|quotation|quote|order|lead|opportunity|opp|deal|ticket|case|proposal|customer|party|prospect)"
    r"\s*(?:#|no\.?|number|id)?\s*#?\s*(\d+)\b",
    re.IGNORECASE,
)
_ID_KEYS = {
    "work order": "work_order_id", "workorder": "work_order_id", "wo": "work_order_id",
    "quotation": "quote_id", "quote": "quote_id", "order": "order_id", "lead": "lead_id",
    "opportunity": "opportunity_id", "opp": "opportunity_id", "deal": "opportunity_id",
    "ticket": "ticket_id", "case": "ticket_id", "proposal": "proposal_id",
    "customer": "party_id", "party": "party_id", "prospect": "party_id",
}
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_PERCENT = re.compile(r"(\d+(?:\.\d+)?)\s*%")
_MONEY = re.compile(r"(?:₹|rs\.?\s*|inr\s*|\$|sar\s*|usd\s*|(?:priced?|costs?|at|for)\s+(?:of\s+|at\s+)?(?:₹|rs\.?\s*|inr\s*|\$)?)(\d[\d,]*(?:\.\d+)?)", re.IGNORECASE)
_QUANTITY = re.compile(r"\b(\d+)\s*(?:x\b|units?\b|lines?\b|licen[cs]es?\b|seats?\b|connections?\b)", re.IGNORECASE)
_NAMED = re.compile(r"\b(?:called|named|titled)\s+[\"']?([^,.;\"'\n]+?)[\"']?(?=$|[,.;\n]|\s+(?:with|at|for|priced|costing|containing|including)\b)", re.IGNORECASE)
_COMPANY = re.compile(r"\b(?:for|from|customer|company|client)\s+((?:[A-Z][\w&.'-]*)(?:\s+[A-Z][\w&.'-]*)*)")
_INCLUDING = re.compile(r"\b(?:with|including|containing|includes|made of|composed of)\s+(?!(?:the|a|an|my|our|this|that|its|their)\b)([^.;\n]+?)(?=$|[.;\n]|\s+(?:at|for|priced|costing)\b)", re.IGNORECASE)


def extract_loose_entities(text: str) -> dict[str, Any]:
    """Generic values found in free text, under the generic keys the rule
    parser understands (quote_id, price, discount, quantity, name, ...)."""
    out: dict[str, Any] = {}
    for kind, number in _ID_PATTERN.findall(text):
        key = _ID_KEYS[re.sub(r"\s+", " ", kind.lower())]
        out.setdefault(key, int(number))
    if m := _EMAIL.search(text):
        out["email"] = m.group(0)
    if m := _PERCENT.search(text):
        out["discount"] = float(m.group(1))
    if m := _MONEY.search(text):
        value = m.group(1).replace(",", "")
        if not (m.end() < len(text) and text[m.end():m.end() + 1] == "%"):
            out["price"] = float(value)
    if m := _QUANTITY.search(text):
        out["quantity"] = int(m.group(1))
    if m := _NAMED.search(text):
        out["name"] = m.group(1).strip()
    if m := _COMPANY.search(text):
        out["company_name"] = m.group(1).strip()
    if m := _INCLUDING.search(text):
        parts = [p.strip() for p in re.split(r",|\+|\band\b", m.group(1)) if p.strip()]
        if parts:
            out["composition"] = parts
    lowered = text.lower()
    if re.search(r"\b(reject|decline|deny|refuse)", lowered):
        out["decision"] = "reject"
    elif re.search(r"\b(approve|accept|ok(ay)?\b|sign(ed)? off)", lowered):
        out["decision"] = "approve"
    return out


# Generic loose keys -> each intent's own entity names.
_REMAP: dict[str, dict[str, str]] = {
    "INT-01": {"price": "list_price", "discount": "requested_discount_pct"},
    "INT-02": {"name": "offering_name", "price": "new_list_price"},
    "INT-03": {"name": "offering_name"},
    "INT-04": {"name": "company_name"},
    "INT-05": {"name": "offering_name", "discount": "requested_discount_pct"},
    "INT-08": {"price": "new_monthly_price"},
    "INT-15": {"company_name": "name"},
    "INT-16": {"name": "offering_name"},
    "INT-19": {"discount": "requested_discount_pct"},
    "INT-21": {"price": "new_monthly_price", "quantity": "new_quantity"},
    "INT-30": {"discount": "requested_discount_pct"},
}


def entities_for(intent_type: str, loose: dict[str, Any], text: str) -> dict[str, Any]:
    remap = _REMAP.get(intent_type, {})
    entities = {remap.get(k, k): v for k, v in loose.items()}
    if intent_type == "INT-10":
        entities.setdefault("description", text.strip())
    if intent_type == "INT-09":
        lowered = text.lower()
        entities["action"] = "suspend" if "suspend" in lowered or "pause" in lowered else ("terminate" if "terminate" in lowered else "cancel")
    if intent_type == "INT-31":
        lowered = text.lower()
        for phrase, action in (("won", "close_won"), ("win", "close_won"), ("lost", "close_lost"), ("lose", "close_lost"), ("reopen", "reopen"), ("reactivate", "reactivate"), ("deactivate", "deactivate")):
            if re.search(rf"\b{phrase}\b", lowered):
                entities["action"] = action
                break
    return entities


# ---------------------------------------------------------------- context

_ID_FIELD = {"party": "party_id", "lead": "lead_id", "opportunity": "opportunity_id", "quote": "quote_id", "order": "order_id"}
_GENERIC_RECORD_INTENTS = {"INT-29", "INT-31", "INT-32", "INT-36", "INT-44"}


def apply_context(intent_type: str | None, entities: dict[str, Any], context: dict[str, Any] | None, required: list[str]) -> list[str]:
    """Fills ids the request left implicit from the record the user is
    viewing. Mutates entities; returns the keys it filled (for the
    rationale). Never overrides anything the user actually said."""
    if not intent_type or not context:
        return []
    entity_type, entity_id = context.get("entity_type"), context.get("entity_id")
    if not entity_type or not entity_id:
        return []
    filled: list[str] = []
    if intent_type in _GENERIC_RECORD_INTENTS:
        if not entities.get("entity_type") and not entities.get("entity_id"):
            entities["entity_type"], entities["entity_id"] = entity_type, entity_id
            filled += ["entity_type", "entity_id"]
        return filled
    key = _ID_FIELD.get(entity_type)
    if key and key in required and entities.get(key) in (None, ""):
        entities[key] = entity_id
        filled.append(key)
    return filled


def context_id_field(context: dict[str, Any] | None) -> str | None:
    """The `<type>_id` entity the viewed record supplies, if any."""
    if not context or not context.get("entity_id"):
        return None
    return _ID_FIELD.get(context.get("entity_type") or "")


def describe_context(context: dict[str, Any] | None) -> str:
    if not context:
        return ""
    parts = []
    if context.get("entity_type") and context.get("entity_id"):
        label = f" ({context['label']})" if context.get("label") else ""
        parts.append(f"The user is currently viewing {context['entity_type']} #{context['entity_id']}{label} - "
                     f"'this'/'it' refers to that record unless the text names another.")
    if context.get("page"):
        parts.append(f"They are on the '{context['page']}' page.")
    return " ".join(parts)


def normalize_list_fields(entities: dict[str, Any]) -> dict[str, Any]:
    """composition / sites as typed into a form ("Fiber500 + VOIP")."""
    for key in ("composition", "sites"):
        value = entities.get(key)
        if isinstance(value, str):
            entities[key] = [p.strip() for p in re.split(r"[,+]", value) if p.strip()]
    return entities

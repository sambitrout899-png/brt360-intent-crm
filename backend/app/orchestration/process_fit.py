"""Process fit - a business process described in plain English, read against
the vanilla processes, so only what is really missing gets built.

    description ──► the closest vanilla process (Functional Map PROCESSES)
                ──► every sentence placed:
                      step      already a step of the process (or another action you have)   nothing to build
                      data      information to capture on a record ("capture the GST number")  custom field + page (Page Designer)
                      config    a rule or automation ("needs CFO approval", "when ..., notify")  Approvals / Flow Studio / assignment
                      new_step  something no action does yet ("the customer signs an NDA")      a new action, built from scratch
                      journey   a whole stage-by-stage journey that vanilla doesn't cover       the journey builder
                ──► build_steps: the gaps, written the way the process builder reads them

Nothing is built here. The report is shown, the user agrees to what is missing,
and the existing builders do the work - the process builder (rehearse, then
build all or nothing, on consent) and build-from-scratch for new steps. Every
check is rules only (no model call), so the report comes back at once.
"""
from __future__ import annotations

import re
from typing import Any

from sqlalchemy.orm import Session

# words that point to a vanilla process (besides the names of its steps)
_HINTS: dict[str, tuple[str, ...]] = {
    "b2b_sto": ("enterprise", "business", "b2b", "corporate", "company", "companies", "sme", "smb", "presales", "pre-sales", "feasibility",
                "purchase order", "po", "proposal", "account manager", "sales ops", "cpmo", "opportunity", "opportunities", "quote", "quotes"),
    "b2c": ("consumer", "consumers", "b2c", "home", "household", "retail", "individual", "walk-in", "store", "whatsapp", "kyc", "ivr", "app"),
    "l2q": ("lead", "leads", "qualify", "qualified", "quote", "quotation", "pricelist", "discount", "revision"),
    "order_capture": ("cart", "checkout", "shipping", "delivery", "tax", "promotion", "credit check", "payment", "order lines"),
    "fulfilment": ("install", "installation", "activation", "activate", "provision", "provisioning", "work order", "technician", "acceptance",
                   "go live", "go-live", "commission"),
    "inlife": ("upgrade", "downgrade", "suspend", "suspension", "cancel", "cancellation", "terminate", "resume", "change plan", "modify"),
    "assurance": ("complaint", "complaints", "ticket", "tickets", "fault", "outage", "diagnose", "repair", "sla", "incident", "trouble"),
    "accounts": ("billing account", "service account", "contact", "address", "billing profile", "account setup", "onboard"),
    "billing": ("bill", "bills", "invoice", "invoices", "collection", "collections", "dunning", "credit note", "adjustment", "overdue", "payment plan"),
    "fraud": ("fraud", "suspicious", "blacklist", "blacklisted"),
    "product": ("launch", "bundle", "catalog", "catalogue", "retire", "product attributes", "price list"),
}
_SEGMENT_OF_PROCESS = {"b2b_sto": "B2B", "b2c": "B2C"}

# what the business is, not a step of it ("We sell fibre to enterprise customers")
_CONTEXT = re.compile(r"^\s*(?:we|our\s+company|our\s+business|our\s+team)\s+(?:are|is|sell|offer|serve|provide|run|operate|deliver|work\s+with)\b"
                      r"|^\s*(?:our|the)\s+(?:customers?|clients?|market)\s+(?:are|is|include)\b", re.I)

# a description of how the business works, not a command on one record
_DESCRIPTIVE = re.compile(r"\b(?:we|our|us|every|each|all|always|usually|typically|should|must|needs?\s+to|has\s+to|have\s+to|process|journey|workflow|"
                          r"customers?\s+(?:must|should|need|can|will|gets?|signs?|pays?|sends?)|then\s+(?:the|our|we)|before\s+(?:the|a|we|it)|"
                          r"after\s+(?:the|a|we|it)|once\s+(?:the|a|we|it))\b", re.I)

# ---------------------------------------------------------------- data to capture

_ENTITY = (r"(?P<entity>customers?|prospects?|accounts?|clients?|companies|company|consumers?|subscribers?|leads?|opportunit(?:y|ies)|deals?|"
           r"quot(?:e|es|ation|ations)|proposals?|orders?)")
_VERB = r"(?:capture|collect|record|store|keep|track|note|take|enter|fill\s+in|ask\s+(?:the\s+customer\s+)?for|get|gather|need|require|want)"
_COUNT = r"(?:a\s+|an\s+|the\s+|some\s+|(?:one|two|three|four|five|six|seven|eight|\d+)\s+)?"
_FIELD_WORD = r"(?:new\s+)?(?:custom\s+|extra\s+|additional\s+|mandatory\s+|required\s+)*(?:fields?|attributes?|columns?|data\s+points?)"
_ADD_FIELDS = [
    # "Add two new custom fields to Prospect - Education and Marital Status"
    re.compile(rf"^\s*(?:please\s+|can\s+you\s+|i\s+want\s+to\s+|we\s+need\s+to\s+)?(?:add|create|introduce|include|set\s+up|capture|collect|record)\s+"
               rf"{_COUNT}{_FIELD_WORD}\s+(?:to|on|for|in|into)\s+(?:the\s+|all\s+|every\s+|each\s+|our\s+)?(?:(?:b2b|b2c|business|consumer)\s+)?"
               rf"{_ENTITY}(?:\s+(?:page|form|record|screen)s?)?\s*(?:[-–—:,]|\s+(?:called|named|namely|like|as|for|-))\s*(?P<fields>.+)$", re.I),
    # "Add Education and Marital Status as new custom fields on the prospect"
    re.compile(rf"^\s*(?:please\s+)?(?:add|create|introduce|include)\s+(?P<fields>.+?)\s+as\s+{_COUNT}{_FIELD_WORD}\s+(?:to|on|for|in)\s+"
               rf"(?:the\s+|all\s+|every\s+|each\s+)?(?:(?:b2b|b2c|business|consumer)\s+)?{_ENTITY}\b", re.I),
]
_DATA_PATTERNS = [
    *_ADD_FIELDS,
    # "capture the customer's GST number and PAN" / "the customers' ..."
    re.compile(rf"\b{_VERB}\w*\s+(?:the\s+|their\s+|each\s+|every\s+|a\s+|an\s+)?{_ENTITY}(?:'s|’s|'|’)\s+(?P<fields>.+)$", re.I),
    # "capture the GST number and PAN for every customer" / "on the lead"
    re.compile(rf"\b{_VERB}\w*\s+(?:the\s+|their\s+|a\s+|an\s+)?(?P<fields>.+?)\s+(?:on|for|of|from|against|in|with)\s+"
               rf"(?:the\s+|each\s+|every\s+|all\s+|a\s+|an\s+|new\s+|our\s+)*(?:b2b\s+|b2c\s+|business\s+|enterprise\s+|consumer\s+)?{_ENTITY}\b", re.I),
    # "every opportunity must have a site contact and site photos"
    re.compile(rf"\b{_ENTITY}\s+(?:must|should|needs?\s+to|has\s+to|have\s+to|will)\s+(?:have|include|carry|record|capture|provide|give|show)\s+"
               r"(?:a\s+|an\s+|the\s+|their\s+)?(?P<fields>.+)$", re.I),
    # "the PAN number is mandatory for customers"
    re.compile(rf"^(?:the\s+)?(?P<fields>.+?)\s+(?:is|are)\s+(?:mandatory|required|compulsory)\s+(?:on|for)\s+(?:the\s+|each\s+|every\s+|all\s+)?{_ENTITY}\b", re.I),
]
_NOT_DATA = re.compile(r"\b(?:approv\w*|sign[- ]?off|notify|email|sms|assign|route|when|whenever|if|automatic\w*)\b"
                       # "I need to create a process which will ..." - an automation to design, not information to capture
                       r"|\b(?:create|build|set\s*up|design|define|make|run)\s+(?:a\s+|an\s+|the\s+)?(?:new\s+)?(?:process|workflow|automation|flow)\b", re.I)
_MANDATORY = re.compile(r"\b(?:must|mandatory|required|compulsory|has\s+to|have\s+to|always)\b", re.I)  # "we need to capture" = capture it, not force it
_DOCUMENT = re.compile(r"\b(?:copy|copies|document|documents|photo|photos|picture|scan|pdf|certificate|nda|contract|agreement|proof|letter)\b", re.I)
_TYPE_HINTS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"\b(?:email|e-mail)\b", re.I), "email"),
    (re.compile(r"\b(?:phone|mobile|contact\s+number|whatsapp\s+number)\b", re.I), "phone"),
    (re.compile(r"\b(?:date|deadline|expiry|expires|due|birthday|dob|anniversary)\b", re.I), "date"),
    (re.compile(r"\b(?:amount|value|budget|revenue|turnover|price|cost|spend|count|employees|headcount|seats|users|size|quantity|"
                r"how\s+many|bandwidth\s+mbps|percentage|%)\b", re.I), "number"),
    (re.compile(r"^(?:is|has|whether|consent|agreed|opt(?:ed)?[- ]in)\b|\b(?:yes/no|received|signed|verified|consent)\b", re.I), "boolean"),
    (re.compile(r"\b(?:notes?|remarks?|comments?|description|reason|requirements?|summary)\b", re.I), "textarea"),
]
_ENTITY_WORD = {"customer": "party", "prospect": "party", "account": "party", "client": "party", "company": "party", "companie": "party",
                "consumer": "party", "subscriber": "party", "lead": "lead", "opportunit": "opportunity", "deal": "opportunity", "quot": "quote",
                "proposal": "quote", "order": "order"}
_ALIASES = {"gst": ("gstin", "tax id", "tax_id", "vat"), "gstin": ("gst", "tax id", "tax_id", "vat"), "pan": ("pan number", "pan card"),
            "mobile": ("phone",), "phone": ("mobile",), "e-mail": ("email",), "company name": ("legal name", "name")}


def _entity_type(word: str) -> str | None:
    w = word.lower()
    return next((et for stem, et in _ENTITY_WORD.items() if w.startswith(stem)), None)


def _split_fields(raw: str) -> list[str]:
    raw = re.split(r"\s+(?:before|after|when|so\s+that|because|which|that\s+(?:is|are)|to\s+(?:check|verify|make|ensure))\b", raw, flags=re.I)[0]
    out = []
    for part in re.split(r",|;|\s+and\s+|\s+&\s+|\s+plus\s+|\s+as\s+well\s+as\s+", raw, flags=re.I):
        part = re.sub(rf"^\s*to\s+{_VERB}\w*\s+", "", part, flags=re.I)  # "(we need) to capture the GST number" -> "the GST number"
        label = re.sub(r"^(?:also\s+|the\s+|their\s+|its\s+|a\s+|an\s+|his\s+|her\s+|our\s+)+", "", part.strip(" .:'\"")).strip()
        label = re.sub(r"\s+(?:details?|info(?:rmation)?)$", "", label, flags=re.I) if len(label.split()) > 1 else label
        if 1 < len(label) <= 50 and len(label.split()) <= 6 and not re.match(r"^(?:it|them|this|that|everything|all|data|details?|information)$", label, re.I) \
                and not re.match(r"^to\s+\w+", label, re.I):  # "to create a process" is something to do, never a field
            out.append(label[:1].upper() + label[1:])
    return out


def _data_type(label: str) -> str:
    if _DOCUMENT.search(label):
        return "text"  # a reference / link - PDFs themselves are attached on the quote
    return next((t for pattern, t in _TYPE_HINTS if pattern.search(label)), "text")


def read_data(text: str) -> dict[str, Any] | None:
    """{entity_type, fields: [label], mandatory} when the sentence asks to capture information."""
    if _NOT_DATA.search(text) and not re.match(rf"^\s*{_VERB}", text, re.I):
        return None
    for pattern in _DATA_PATTERNS:
        m = pattern.search(text)
        if not m:
            continue
        entity = _entity_type(m.group("entity"))
        fields = _split_fields(m.group("fields"))
        if entity and fields:
            return {"entity_type": entity, "fields": fields, "mandatory": bool(_MANDATORY.search(text)),
                    "segment": "B2C" if re.search(r"\b(?:consumer|b2c|individual|household|home)\b", text, re.I)
                    else "B2B" if re.search(r"\b(?:business|enterprise|company|companies|corporate|b2b)\b", text, re.I) else None}
    return None


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()


def _known_field(label: str, fields: list[dict[str, Any]]) -> dict[str, Any] | None:
    """The record's existing field (core or custom) this label means, if any."""
    want = _norm(label)
    names = {want, want.replace(" ", "_"), *(_ALIASES.get(want, ())), *(_ALIASES.get(want.split()[0], ()) if want else ())}
    for f in fields:
        have = {_norm(f.get("label", "")), _norm(f.get("key", "")).replace(" ", "_"), _norm(f.get("key", ""))}
        if names & have or any(n and len(n) > 2 and (n in _norm(f.get("label", "")).split() or n == _norm(f.get("label", ""))) for n in names):
            return f
    return None


# ---------------------------------------------------------------- the vanilla process


def _words(text: str) -> set[str]:
    from app.orchestration.assistant import _concepts

    return _concepts(text)


def _hits(process_key: str, text: str) -> int:
    low = f" {text.lower()} "
    return sum(1 for h in _HINTS.get(process_key, ()) if re.search(rf"(?<![\w-]){re.escape(h)}(?![\w-])", low))


def _step_match(db: Session, tenant_id: int, text: str, catalog: dict[str, dict[str, Any]]) -> tuple[str | None, float]:
    """The action a sentence describes: the classifier's rules, then the words of the action's name."""
    from app.intent_engine import classifier

    best, score = None, 0.0
    try:
        guess = classifier.classify(text, db, tenant_id, None, live=True)  # rules only - no model call
        if guess.intent_type in catalog and guess.confidence >= 0.6:
            best, score = guess.intent_type, 2.0 + guess.confidence
    except Exception:  # noqa: BLE001 - a sentence the classifier can't read is matched by words alone
        pass
    wanted = _words(text)
    for code, e in catalog.items():
        overlap = len(wanted & _words(e.get("label", ""))) * 1.2 + 0.3 * len(wanted & _words(e.get("description", "")))
        if overlap > score and overlap >= 2.0:
            best, score = code, overlap
    return best, score


# actions that belong to a process although its map lists a sibling (B2B qualifies with INT-04 or INT-17 ...)
_ALSO: dict[str, tuple[str, ...]] = {"b2b_sto": ("INT-04", "INT-16", "INT-35", "INT-24", "INT-07"), "b2c": ("INT-07", "INT-106"),
                                     "l2q": ("INT-46", "INT-17", "INT-37", "INT-18")}


def _steps_in(db: Session, tenant_id: int, sentence: str, catalog: dict[str, dict[str, Any]], process: dict[str, Any]) -> list[str]:
    """Every action a sentence names ("qualifies the lead and configures the product" = two) -
    the process's own steps first, then any other action."""
    own = {c: catalog[c] for c in [*process["steps"], *_ALSO.get(process["key"], ())] if c in catalog}
    clauses = [c for c in re.split(r",?\s+(?:and\s+then|and|then)\s+|,\s+|;\s*", sentence, flags=re.I) if len(c.strip()) > 3] or [sentence]
    found: list[str] = []
    for clause in clauses:
        code = _step_match(db, tenant_id, clause, own)[0] or _step_match(db, tenant_id, clause, catalog)[0]
        if code and code not in found:
            found.append(code)
    return found


# ---------------------------------------------------------------- variants: some records take a shorter road

_COND = re.compile(r"\bfor\s+(?:a\s+|an\s+|all\s+|any\s+|every\s+|each\s+)?(?:(?P<seg>b2b|b2c|business|consumer|enterprise)\s+)?"
                   r"(?P<entity>prospects?|customers?|accounts?|companies|company)\s+(?:of|with|where|whose|having)\s+(?:the\s+|a\s+)?"
                   r"(?P<field>[A-Za-z][\w /-]{1,40}?)\s+(?:as|is|=|of|being|equal\s+to)\s+[\"“'‘]?(?P<value>[A-Za-z0-9][^\"”'’.,;]{0,40}?)[\"”'’]?"
                   r"(?=\s*(?:[-–—,.;:]|\s+(?:and|then|create|go|skip|we|it)\b|$))", re.I)
_SKIPS = [re.compile(r"\bskip\w*\s+(?:certain\s+|the\s+|these\s+|some\s+|all\s+)?(?:vanilla\s+)?(?:steps?\s+)?(?:like|such\s+as|:|-|namely)?\s*(?P<list>[^.]+)", re.I),
          re.compile(r"(?:vanilla\s+)?steps?\s+(?:to\s+)?(?P<list>[^.]+?)\s+(?:should|must|can|will)\s+be\s+(?:skipped|bypassed|removed|avoided|left\s+out)", re.I)]
_DIRECT = re.compile(r"\b(?:directly|straight(?:\s+away)?|immediately)\b[^.]{0,40}?\b(?P<what>quot(?:e|ation)|opportunity)\b"
                     r"|\b(?:create|raise|open|generate|make)\s+(?:a\s+|the\s+)?(?P<what2>quot(?:e|ation)|opportunity)\s+directly\b", re.I)
_PRODUCT = re.compile(r"\bproduct\s+(?:as|is|=|:|called|named)?\s*[\"“'‘](?P<p>[^\"”'’]+)[\"”'’]", re.I)
_AFTER_TARGET = re.compile(r"\b(?:send|hand|pass|route)\w*\s+it\s+(?:on\s+)?to\s+pre-?\s?sales\b|\bthen\s+(?:the\s+)?(?:pre-?\s?sales|feasibility)\b", re.I)
# the steps that lead from a prospect to each target - run by themselves in the variant
_ROAD = {"INT-05": ["INT-46", "INT-17"], "INT-37": ["INT-46", "INT-17"]}
_TARGET = {"quot": "INT-05", "opportunity": "INT-37"}


def _variant(db: Session, tenant_id: int, text: str, catalog: dict[str, dict[str, Any]], process: dict[str, Any]) -> dict[str, Any] | None:
    """"For a B2B prospect of Company Type Government, create a quote directly ... skip Generate lead,
    Approve a lead": a condition, the steps to skip, the step to land on."""
    from app.crm_core import page_service

    cond, direct = _COND.search(text), _DIRECT.search(text)
    skip_lists = [m.group("list") for p in _SKIPS for m in p.finditer(text)]
    if not cond or not (direct or skip_lists):
        return None
    skipped: list[str] = []
    for raw in skip_lists:
        for part in re.split(r",|;|\s+and\s+|->|→|\s+or\s+", raw):
            part = part.strip(" .\"'")
            if len(part) < 4:
                continue
            code = _step_match(db, tenant_id, part, {c: catalog[c] for c in process["steps"] if c in catalog})[0]
            if code and code not in skipped:
                skipped.append(code)
    what = (direct.group("what") or direct.group("what2") or "").lower()[:5] if direct else ""
    target = next((code for word, code in _TARGET.items() if what.startswith(word[:5])), None)
    if target is None and "INT-46" in skipped:
        target = "INT-05"
    road = [c for c in _ROAD.get(target or "", []) if c in catalog]
    entity = "party"
    segment = "B2C" if (cond.group("seg") or "").lower() in ("b2c", "consumer") else "B2B"
    page = _page_for(db, tenant_id, entity, segment)
    known: list[dict[str, Any]] = []
    for seg in dict.fromkeys([page.get("segment") if page else None, segment]):
        known += page_service.catalog(db, tenant_id, entity, seg)
    label = cond.group("field").strip()
    label = label[:1].upper() + label[1:]
    existing = _known_field(label, known)
    key = existing["key"] if existing else re.sub(r"[^a-z0-9]+", "_", label.lower()).strip("_")[:60]
    product = (_PRODUCT.search(text) or [None, None])[1]
    conflicts = []
    if target == "INT-05" and _AFTER_TARGET.search(text):
        conflicts.append({
            "what": "Presales after the quote",
            "why": "In the vanilla process feasibility comes before the quote: a quote is blocked while Presales is checking, and Presales "
                   "takes only a configured opportunity - a quoted one can't be sent. Doing it the other way round changes those rules "
                   "(an engineering change).",
            "instead": "Keep the check before the quote: the variant can also configure the product with its defaults and send it to "
                       "Presales by itself - the quote then opens once feasibility passes."})
    return {"condition": {"entity_type": entity, "segment": segment, "field": label, "key": key, "value": cond.group("value").strip(),
                          "exists": existing is not None, "existing": existing.get("label") if existing else None,
                          "page": page["name"] if page else None},
            "skipped": skipped, "automated": road, "target": target, "product": product, "conflicts": conflicts}


# ---------------------------------------------------------------- rules: "if <field> is <value>, <action>"

_IF_RULE = re.compile(r"^\s*(?:if|when|whenever|where|in\s+case)\s+(?:the\s+|a\s+|an\s+)?(?:(?:b2b\s+|b2c\s+)?(?P<entity>prospects?|customers?|accounts?|"
                      r"compan(?:y|ies)|part(?:y|ies)|clients?|leads?|opportunit(?:y|ies)|quotes?|orders?)(?:'s|’s)?\s+)?"
                      r"(?P<field>[A-Za-z][\w /-]{1,40}?)\s+(?:is|=|equals|is\s+equal\s+to|is\s+set\s+to)\s+[\"“'‘]?(?P<value>[A-Za-z0-9][^\"”'’,;]{0,40}?)[\"”'’]?"
                      r"\s*(?:,\s*(?:then\s+)?|\s+then\s+)(?P<action>.+)$", re.I)
# "a generic process called after the Add prospect intent" - when the rule runs
_AFTER = re.compile(r"\bafter\s+(?:the\s+|an?\s+|every\s+|each\s+)?[\"“'‘]?(?P<what>[^\"”'’.,;]+?)[\"”'’]?\s+(?:intent|action|step)\b", re.I)
# a remark about the rule - "this is not related to any particular product" - never a step of its own
_NOTE = re.compile(r"^\s*(?:this|it|that|these|the\s+(?:rule|process|flow|automation))\s+(?:is|isn't|are|aren't|does|doesn't|should|shouldn't|applies|"
                   r"has|must|can|will|need)\b[^.]*\b(?:related|specific|particular|limited|applicable|appl(?:y|ies)|generic|tied|any|every|all|regardless)\b", re.I)
_MAKE_PROCESS = re.compile(r"^\s*(?:please\s+)?(?:create|set\s*up|build|design|define|make|add)\s+(?:a\s+|an\s+|the\s+)?(?:new\s+)?(?:generic\s+)?"
                           r"(?:process|workflow|automation|rule|flow)\b", re.I)
# the intent a rule on a record runs after, when the request doesn't say
_DEFAULT_AFTER = {"party": "INT-15"}
_RECORD_WORD = {"party": "prospect", "lead": "lead", "opportunity": "opportunity", "quote": "quote", "order": "order"}


def _field_of(db: Session, tenant_id: int, label: str, entity_types: list[str]) -> tuple[str, dict[str, Any]] | None:
    """The record type and existing field (core or custom, any segment) a label names."""
    from app.crm_core import page_service

    for et in entity_types:
        known: list[dict[str, Any]] = []
        for seg in (None, "B2B", "B2C"):
            known += page_service.catalog(db, tenant_id, et, seg)
        found = _known_field(label, known)
        if found is not None:
            return et, found
    return None


def rule_variant(db: Session, tenant_id: int, sentence: str, whole: str | None = None,
                 catalog: dict[str, dict[str, Any]] | None = None) -> str | None:
    """"If the Company type is PSU, then convert the prospect into an account" as the process
    builder's variant line - after an intent, when a field has a value, run an action - or None.
    The field must be one the record really has, so "when an order is approved" stays a stage trigger."""
    from app.intent_engine import intent_registry

    m = _IF_RULE.match(sentence)
    if not m:
        return None
    if catalog is None:
        catalog = {e["code"]: e for e in intent_registry.all_intents(db, tenant_id) if e.get("is_active", True)}
    said = _entity_type(m.group("entity")) if m.group("entity") else None
    found = _field_of(db, tenant_id, m.group("field").strip(), [said] if said else ["party", "lead", "opportunity", "quote", "order"])
    if found is None:
        return None
    entity_type, field = found
    # the CRM's own action when the words name one ("convert the prospect into an account" = INT-35) - a custom
    # flow whose name happens to share the words ("NGO Prospect to Account Conversion") only when none does
    from app.orchestration.process_builder import _business_action
    business = _business_action(m.group("action"))
    built_in = {c: e for c, e in catalog.items() if not c.startswith("FLOW-")}
    action = (business[0] if business and business[0] in catalog else None) or _step_match(db, tenant_id, m.group("action"), built_in)[0] \
        or _step_match(db, tenant_id, m.group("action"), catalog)[0]
    if action is None:
        return None
    after = None
    for text in (sentence, whole or ""):
        a = _AFTER.search(text)
        if a:
            after = _step_match(db, tenant_id, a.group("what"), catalog)[0]
            if after:
                break
    after = after or _DEFAULT_AFTER.get(entity_type)
    if after is None or after not in catalog:
        return None
    label = field.get("label") or m.group("field").strip()
    return (f"Variant: after {catalog[after]['label']} ({after}), when the {_RECORD_WORD.get(entity_type, entity_type)}'s {label} "
            f"[{field['key']}] is {m.group('value').strip()}, run {catalog[action]['label']} ({action})")


def _sentences(text: str) -> list[str]:
    from app.orchestration import process_builder

    return [s["text"] for s in process_builder.split(text) if len(s["text"]) > 2]


def _page_for(db: Session, tenant_id: int, entity_type: str, segment: str | None) -> dict[str, Any] | None:
    from app.crm_core import page_service

    listing = [p for p in page_service.pages(db, tenant_id) if p["entity_type"] == entity_type and p.get("is_active", True)]
    return (next((p for p in listing if segment and p.get("segment") == segment), None)
            or next((p for p in listing if not p.get("segment")), None) or (listing[0] if listing else None))


def read(db: Session, tenant_id: int, user, text: str) -> dict[str, Any] | None:
    """The fit report - or None when the text isn't a description of a business process."""
    from app.crm_core import page_service
    from app.intent_engine import intent_registry
    from app.orchestration import function_map, process_builder
    from app.orchestration.process_journey import parse as parse_journey

    text = " ".join((text or "").split())
    journey = parse_journey(text)
    # "Add two fields to Prospect - Education and Marital Status" is one request, whatever its dashes
    parts = journey[1] if journey else [text] if any(p.search(text) for p in _ADD_FIELDS) else _sentences(text)
    if not parts:
        return None
    descriptive = sum(1 for p in parts if _DESCRIPTIVE.search(p) or read_data(p))
    data_said = any(read_data(p) for p in parts)
    if not (journey or data_said or (len(parts) >= 2 and descriptive * 2 >= len(parts)) or re.search(r"\b(?:our|the)\s+(?:\w+\s+){0,3}process\b", text, re.I)):
        return None

    catalog = {e["code"]: e for e in intent_registry.all_intents(db, tenant_id) if e.get("is_active", True)}
    processes = [p for p in function_map.PROCESSES if p["key"] != "workspace"]
    codes = [_step_match(db, tenant_id, p_text, catalog)[0] for p_text in parts]
    # "if <field> is <value>, <action>" - an automation to build, not the vanilla step its action names
    rules = {i: line for i, p_text in enumerate(parts) if not journey and (line := rule_variant(db, tenant_id, p_text, text, catalog))}

    def score(p: dict[str, Any]) -> float:
        steps = set(p["steps"])
        return sum(3.0 for c in codes if c in steps) + 1.2 * _hits(p["key"], text) + (1.5 if _words(p["label"]) & _words(text) else 0)

    ranked = sorted(processes, key=score, reverse=True)
    best = ranked[0]
    top = score(best)
    if top < 3.0 and not data_said and not rules:
        return None
    segment = _SEGMENT_OF_PROCESS.get(best["key"]) or ("B2C" if re.search(r"\b(?:consumer|b2c|household|home)\b", text, re.I) else None)

    items: list[dict[str, Any]] = []
    build_steps: list[dict[str, str]] = []
    covered_steps: set[str] = set()
    variant = None if journey else _variant(db, tenant_id, text, catalog, best)
    if variant is not None:
        c = variant["condition"]
        if not c["exists"] and c["page"]:
            where = c["page"] if re.search(r"\b(?:page|form|screen)$", c["page"], re.I) else f"{c['page']} page"
            build_steps.append({"kind": "page", "text": f"On the {where} add a field {c['field']} with values {c['value']}, Other"})
        if variant["automated"]:
            runs = ", then ".join(f"{catalog[x]['label']} ({x})" + (f" for {variant['product']}" if x == "INT-46" and variant["product"] else "")
                                  for x in variant["automated"])
            trigger = "INT-15" if "INT-15" in catalog else best["steps"][0]
            build_steps.append({"kind": "variant", "text": f"Variant: after {catalog[trigger]['label']} ({trigger}), when the "
                                                           f"{'prospect' if c['entity_type'] == 'party' else c['entity_type']}'s {c['field']} [{c['key']}] is "
                                                           f"{c['value']}, run {runs}"})
    for i, sentence in enumerate(parts):
        code = codes[i]
        if variant is not None and (_COND.search(sentence) or _DIRECT.search(sentence) or any(p.search(sentence) for p in _SKIPS)):
            c = variant["condition"]
            items.append({"text": sentence, "kind": "variant", "status": "gap",
                          "note": f"A variant for {c['field']} = {c['value']}: "
                                  + (f"{', '.join(catalog[x]['label'] for x in variant['automated'])} run by themselves, then "
                                     f"{catalog[variant['target']]['label']} opens" if variant["target"] in catalog and variant["automated"] else
                                     "the steps you named are left out")})
            continue
        if i in rules:
            build_steps.append({"kind": "variant", "text": rules[i]})
            m = process_builder._VARIANT.match(rules[i])
            items.append({"text": sentence, "kind": "rule", "status": "gap",
                          "note": f"An automation: after “{m.group('trigger_label')}”, when {m.group('field')} is {m.group('value')}, "
                                  f"{re.sub(r' [(]INT-[0-9]+[)]', '', m.group('actions'))} runs by itself"})
            continue
        if not journey and _NOTE.match(sentence):
            items.append({"text": sentence, "kind": "note", "status": "covered", "note": "A note about the rule - nothing to build"})
            continue
        if rules and _AFTER.search(sentence):
            items.append({"text": sentence, "kind": "note", "status": "covered", "note": "When the rule runs - part of the automation"})
            continue
        if rules and _MAKE_PROCESS.match(sentence):
            items.append({"text": sentence, "kind": "intro", "status": "covered", "note": "What the process is for - the rule below does it"})
            continue
        data = None if journey else read_data(sentence)
        if data:
            # the segment said (or, inside a described process, the process's); none said = every page of that record
            seg = data["segment"] or (segment if len(parts) > 1 else None)
            entity_pages = [p for p in page_service.pages(db, tenant_id) if p["entity_type"] == data["entity_type"] and p.get("is_active", True)]
            pages = [p for p in [_page_for(db, tenant_id, data["entity_type"], seg)] if p] if seg else entity_pages[:4]
            fields, added = [], 0
            for label in data["fields"]:
                found_on = []
                for page in pages:
                    # what the record already holds - for this page's segment (and the process's: B2B-only fields count too)
                    known: list[dict[str, Any]] = []
                    for s in dict.fromkeys([page.get("segment"), seg]):
                        known += page_service.catalog(db, tenant_id, data["entity_type"], s)
                    existing = _known_field(label, known)
                    found_on.append(existing)
                    where = page["name"] if re.search(r"\b(?:page|form|screen)$", page["name"], re.I) else f"{page['name']} page"
                    kind = "" if _data_type(label) == "text" else f" as {_data_type(label)}"
                    if existing is None:
                        added += 1
                        build_steps.append({"kind": "page", "text": f"On the {where} add a {'mandatory ' if data['mandatory'] else ''}field {label}{kind}"})
                    elif data["mandatory"]:
                        build_steps.append({"kind": "page", "text": f"On the {where} make {existing['label']} mandatory"})
                have = [e for e in found_on if e is not None]
                fields.append({"label": label, "data_type": _data_type(label), "exists": bool(pages) and len(have) == len(pages),
                               "existing": have[0].get("label") if have else None, "mandatory": data["mandatory"]})
            names = " and ".join(p["name"] for p in pages) or data["entity_type"]
            new = [f for f in fields if not f["exists"]]
            items.append({"text": sentence, "kind": "data", "entity_type": data["entity_type"], "page": names, "fields": fields,
                          "status": "gap" if added else "covered",
                          "note": (f"{len(new)} new field{'s' if len(new) != 1 else ''} on the {names}" if added
                                   else "Already captured - " + ", ".join(f["existing"] or f["label"] for f in fields))})
            continue
        if not journey and process_builder._recognised(sentence) and not process_builder._INTRO.match(sentence):
            build_steps.append({"kind": "", "text": sentence})
            items.append({"text": sentence, "kind": "config", "status": "gap", "note": "A rule or automation - built with Approvals / Flow Studio"})
            continue
        if not journey and process_builder._INTRO.match(sentence):
            items.append({"text": sentence, "kind": "intro", "status": "covered", "note": "The process's name"})
            continue
        if not journey and _CONTEXT.match(sentence):
            items.append({"text": sentence, "kind": "context", "status": "covered",
                          "note": f"About your business - it points to {best['label']}" + (f" ({segment})" if segment else "")})
            continue
        found = _steps_in(db, tenant_id, sentence, catalog, best) if not journey else ([code] if code else [])
        if found:
            own = set(best["steps"]) | set(_ALSO.get(best["key"], ()))
            covered_steps.update(c for c in found if c in own)
            labels = [catalog[c]["label"] for c in found]
            outside = [catalog[c]["label"] for c in found if c not in own]
            items.append({"text": sentence, "kind": "step", "status": "covered", "code": found[0], "codes": found, "label": " + ".join(labels),
                          "note": (f"Vanilla step{'s' if len(found) > 1 else ''}: {', '.join(labels)}" if not outside else
                                   f"You already have {', '.join(f'“{o}”' for o in outside)} - outside {best['label']}, but it runs as it is")})
            continue
        items.append({"text": sentence, "kind": "new_step", "status": "gap",
                      "note": "No action does this yet - it can be built from scratch as a new action"})

    # one Page Designer change per page, however many fields it gets
    merged: dict[str, dict[str, str]] = {}
    for step in build_steps:
        m = re.match(r"^On the (.+? (?:page|form|screen)) (.+)$", step["text"]) if step["kind"] == "page" else None
        if m and m.group(1) in merged:
            merged[m.group(1)]["text"] += f"; {m.group(2)}"
        elif m:
            merged[m.group(1)] = step
    build_steps = [s for s in build_steps if s["kind"] != "page" or s in merged.values()]

    gaps = [it for it in items if it["status"] == "gap"]
    if journey and any(it["kind"] == "new_step" for it in items):
        build_steps = [{"kind": "journey", "text": text}]  # the stages vanilla lacks belong to the journey as a whole
    steps_view = [{"code": c, "label": catalog[c]["label"], "icon": catalog[c].get("icon"), "said": c in covered_steps}
                  for c in best["steps"] if c in catalog]
    return {
        "process": {"key": best["key"], "label": best["label"], "steps": steps_view, "segment": segment},
        "alternatives": [{"key": p["key"], "label": p["label"]} for p in ranked[1:3] if score(p) > 0],
        "confidence": round(top / (top + score(ranked[1]) + 1e-9), 2) if len(ranked) > 1 else 1.0,
        "journey": bool(journey),
        "items": items,
        "covered": len(items) - len(gaps),
        "gaps": len(gaps),
        "build_steps": build_steps,
        # only fields asked for - no process to place them in (the screen leaves the vanilla process out)
        "data_only": bool(items) and all(it["kind"] == "data" for it in items),
        "new_steps": [it["text"] for it in items if it["kind"] == "new_step" and not journey],
        "variant": variant and {**variant, "skipped_labels": [catalog[x]["label"] for x in variant["skipped"]],
                                "automated_labels": [catalog[x]["label"] for x in variant["automated"]],
                                "target_label": catalog[variant["target"]]["label"] if variant["target"] in catalog else None},
    }


def summary(fit: dict[str, Any]) -> str:
    p = fit["process"]["label"]
    if fit.get("data_only"):
        new = [f for it in fit["items"] for f in it.get("fields", []) if not f["exists"]]
        pages = " and ".join(dict.fromkeys(it["page"] for it in fit["items"] if it.get("page")))
        if not new:
            return f"Already captured on the {pages} - nothing to add."
        return (f"{len(new)} new custom field{'s' if len(new) != 1 else ''} ({', '.join(f['label'] for f in new)}) on the {pages}"
                " - built with the Page Designer once you agree.")
    v = fit.get("variant")
    if v:
        c = v["condition"]
        out = (f"A variant of your vanilla “{p}” process for {c['field']} = {c['value']}: "
               + (f"{' and '.join(v['automated_labels'])} run by themselves, so {v['target_label']} comes straight after the prospect"
                  if v["automated_labels"] and v["target_label"] else "the steps you named are skipped")
               + (f" (with {v['product']})" if v["product"] else "") + ".")
        if not c["exists"]:
            out += f" A {c['field']} field is added to the {c['page'] or 'prospect page'} for it."
        if v["conflicts"]:
            out += " One part conflicts with the vanilla rules - see below."
        return out + " I'll build it once you agree."
    if not fit["gaps"]:
        return f"This is your vanilla “{p}” process - everything you described is already in it. Nothing to build."
    if all(it["kind"] in ("rule", "note", "intro") for it in fit["items"]):
        return (" ".join(f"{it['note']}." for it in fit["items"] if it["kind"] == "rule")
                + " It applies to every record, whatever the product. I'll build it once you agree.")
    what = []
    fields = sum(len([f for f in it.get("fields", []) if not f["exists"]]) for it in fit["items"] if it["kind"] == "data")
    if fields:
        what.append(f"{fields} field{'s' if fields != 1 else ''} to capture")
    rules = sum(1 for it in fit["items"] if it["kind"] in ("config", "rule"))
    if rules:
        what.append(f"{rules} rule{'s' if rules != 1 else ''} / automation{'s' if rules != 1 else ''}")
    new = len(fit["new_steps"])
    if new:
        what.append(f"{new} new step{'s' if new != 1 else ''}")
    if fit["journey"] and not what:
        what.append("the stages vanilla doesn't have")
    return (f"This fits your vanilla “{p}” process - {fit['covered']} of {len(fit['items'])} parts are already in it. "
            f"Missing: {', '.join(what)}. I'll build only that, once you agree.")

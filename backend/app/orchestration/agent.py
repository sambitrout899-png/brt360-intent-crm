"""The BRT 360 agent - one goal in plain words, carried out as a chain of
intents.

    "Add prospect Globex, ops@globex.com, then generate a lead for them
     interested in Fiber 800, then approve the lead"

1. Plan. The goal is broken into steps, each one an intent the user may run:
   - by the rules first - the goal is split at "then" / "after that" / new
     lines / numbered points and each part goes through the classifier's
     rules (no model call per part - each would cost seconds);
   - by the tenant's AI model (llm.resolve: its own on-server model, or an
     external API), in ONE call for the whole goal, when the rules can't
     account for every part with confidence. The model
     only chooses from the intents this user is allowed to run - an
     unknown or forbidden code is refused, never guessed at.
   Each step says what it still needs and which earlier step will supply it.

2. Rehearse or run. The steps run one after another through exactly the path
   a single intent takes (routes_intent._handle: visibility, availability,
   approvals, flows, assignment rules, audit) inside ONE transaction:
   - memory: every id a step produces (party_id, lead_id, quote_id,
     order_id, ...) is handed to the following steps that need it, and
     "$2.quote_id" names a specific step's output;
   - a step that can't go ahead (a missing detail, a blocked intent, a
     failed check) pauses the agent with the question to ask;
   - rehearse: everything is rolled back and the combined consequences come
     back (rehearsal.summarize) - nothing is kept;
   - run: all the steps are committed together, or none of them is.
"""

from __future__ import annotations

import copy
import json
import re
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any

from sqlalchemy.orm import Session

from app.models.auth import User

MAX_STEPS = 12
# a part the rules read below this confidence is left to the model's planner
SURE = 0.75
_ACTION_VERB = r"(?:add|create|generate|open|raise|approve|auto[- ]?approve|configure|submit|send|qualify|convert|book|place|quote|make|prepare)"
_SPLIT = re.compile(
    r"\s*(?:\n+|;|\.\s+(?=(?-i:[A-Z])(?!\w*:))|,?\s*\b(?:and then|then|after that|afterwards|after which|once done|finally)\b[,:]?|(?:^|\s)\d+[.)]\s"
    # "... with address "vvvvv"- auto approve" / "... - configure the default"
    r"|\s+[-–—]+\s+|(?<=[\"'”’)])\s*[-–—]+\s*"
    # "... ->auto approve the quote -> generate an order"
    r"|\s*(?:-+>|=>|→)\s*"
    # 'Create a new Prospect "GGG", auto approve it' - a comma before an automatic action starts a step
    r"|,\s+(?=auto(?:matically)?[- ]?[a-z]))\s*",
    re.IGNORECASE,
)
# "auto approve and add a lead" - two actions joined by "and" (but "Configure and quote this bundle" is one)
_AND_ACTION = re.compile(rf",?\s+and\s+(?=(?:auto(?:matically)?[- ]?)?{_ACTION_VERB}\s)", re.IGNORECASE)
_REF = re.compile(r"^\$(\d+)\.(\w+)$")
# "build a process where ..." / "automate the following: ..." - the goal is what follows
_PREAMBLE = re.compile(r"^\s*(?:please\s+|can\s+you\s+|i\s+want\s+(?:you\s+)?to\s+)?(?:build|create|make|set\s*up|design|automate|run|do)\s+"
                       r"(?:a\s+|an\s+|the\s+|this\s+)?(?:new\s+)?(?:process|flow|workflow|journey|sequence|chain|following)\s*"
                       r"(?:where(?:\s+in)?|in\s+which|that|which|to|so\s+that|for|:|,|-)\s*", re.IGNORECASE)


# ---------------------------------------------------------------- planning


def split_goal(goal: str) -> list[str]:
    goal = _PREAMBLE.sub("", goal or "", count=1)
    parts = []
    for part in _SPLIT.split(goal):
        pieces = _AND_ACTION.split(part or "")
        merged = [pieces[0]]
        for piece in pieces[1:]:
            left = merged[-1].strip()
            # the left side must be an action of its own - not a bare verb sharing the object ("Configure and quote this bundle")
            if len(left.split()) >= 3 or _BARE_APPROVE.match(left):
                merged.append(piece)
            else:
                merged[-1] = f"{merged[-1]} and {piece}"
        parts.extend(merged)
    parts = [p.strip(" ,.-–—") for p in parts]
    parts = [re.sub(r"^(?:and|also|then)\s+", "", p, flags=re.IGNORECASE) for p in parts if p and len(p.strip(" ,.-–—")) > 2]
    return parts[:MAX_STEPS]


# Sales-chain phrasings the single-intent classifier reads differently when they
# arrive as one part of a longer goal. Checked before it; each only when the
# user may run that intent. (code, pattern)
_AUTO = r"(?:auto(?:matically)?[- ]?)?"
_CHAIN_RULES: list[tuple[str, re.Pattern]] = [
    ("INT-46", re.compile(r"^(?:\w+\s+){0,2}?(?:add|create|generate|open|raise|log)\s+(?:a\s+|an\s+|the\s+)?(?:new\s+)?lead\b(?!\s+(?:is|was)\b)", re.I)),
    ("INT-05", re.compile(r"\b(?:generate|create|make|prepare|raise|build|produce|issue)\s+(?:a\s+|an\s+|the\s+)?(?:new\s+)?quot(?:e|ation)\b", re.I)),
    ("INT-37", re.compile(r"^(?:\w+\s+){0,1}?configure\s+(?:it|the\s+product|this|the\s+(?:default\s+)?configuration|(?:with\s+)?(?:the\s+)?defaults?)\b", re.I)),
    # "auto qualify the lead" (a plain "Qualify this lead. company_name: ..." still opens one - INT-04)
    ("INT-17", re.compile(r"^auto(?:matically)?[- ]?(?:qualify|approve)\s+(?:the\s+|this\s+|that\s+)?lead\b", re.I)),
    ("INT-38", re.compile(rf"^{_AUTO}(?:send|hand|pass|forward|move)\w*\b[^.]{{0,30}}?\bpre-?\s?sales\b", re.I)),
    ("INT-18", re.compile(rf"^{_AUTO}(?:feasib\w*\s+(?:pass\w*|ok|clear\w*|success\w*|approv\w*)|(?:pass|clear|approve|mark)\w*\s+(?:the\s+)?feasib\w*"
                          r"|feasib\w*\s*$|(?:mark|record)\w*\s+(?:it\s+)?(?:as\s+)?feasible)", re.I)),
    ("INT-06", re.compile(rf"^{_AUTO}approve\w*\s+(?:the\s+|this\s+|that\s+)?quot(?:e|ation)\b", re.I)),
    ("INT-24", re.compile(r"\b(?:generate|create|make|place|raise|book)\s+(?:an?\s+|the\s+)?(?:new\s+)?order\b", re.I)),
]
_DECISIONS = {"INT-17": {"decision": "approve"}, "INT-18": {"decision": "feasible"}, "INT-06": {"decision": "approve"}}
# "give 10 percent discount" - a detail of the quote, not a step of its own
_DISCOUNT = re.compile(r"^(?:give|apply|with|add|offer|at|plus)?\s*(?:an?\s+)?(\d+(?:\.\d+)?)\s*(?:%|percent|per\s+cent|pc)\s+(?:discount|off)\b"
                       r"|^(?:give|apply|add|offer)\s+(?:an?\s+)?discount\s+of\s+(\d+(?:\.\d+)?)\s*(?:%|percent)", re.I)
# "generate an opportunity" next to qualifying the lead - qualifying opens it
_OPPORTUNITY = re.compile(r"^(?:generate|create|open|make|raise)\s+(?:an?\s+|the\s+)?(?:new\s+)?opportunit(?:y|ies)\s*$", re.I)
_BARE_APPROVE = re.compile(r"^(?:auto(?:matically)?[- ]?)?approv(?:e|al)(?:\s+(?:it|them|this|that|automatically))?$", re.I)
_QUOTED = r"[\"“'‘]([^\"”'’]{1,120})[\"”'’]"


def _refine(code: str | None, part: str, entities: dict, catalog: dict) -> tuple[str | None, dict]:
    """Chain phrasings first, then the values said in quotes ('a company
    called "XXX" with address "vvvvv"', 'a product "Fiber 500 Bundle"')."""
    for rule_code, pattern in _CHAIN_RULES:
        if rule_code in catalog and pattern.search(part):
            if rule_code != code:
                entities = {}
            code = rule_code
            break
    e = dict(entities)
    for key, value in _DECISIONS.get(code or "", {}).items():  # "auto approve the quote" - the decision is said
        if e.get(key) in (None, "") and re.search(r"\b(?:auto|approv|qualif|feasib|pass)", part, re.I):
            e[key] = value
    if code == "INT-15":
        m = re.search(rf"\b(?:company|customer|prospect|organi[sz]ation|business|firm|account)?\s*(?:called|named|name(?:d)?\s*:?)\s*{_QUOTED}", part, re.I) \
            or re.search(rf"\b(?:prospect|customer|company|account)\s*{_QUOTED}", part, re.I) \
            or re.search(r"\b(?:add|create|new|register|onboard)\s+(?:a\s+)?(?:new\s+)?(?:prospect|customer|company)\s+"
                         r"((?-i:[A-Z0-9])[\w&.'-]*(?:\s+(?-i:[A-Z0-9&])[\w&.'-]*){0,5})", part, re.I)
        if m and m.group(1).strip():
            e["name"] = m.group(1).strip().rstrip(".")
        m = re.search(rf"\baddress\s*(?:is|of|:)?\s*{_QUOTED}|\baddress\s*:\s*([^,;]+)", part, re.I)
        if m:
            details = dict(e.get("details") or {})
            details["address_line1"] = (m.group(1) or m.group(2)).strip()
            e["details"] = details
    if code in ("INT-46", "INT-05", "INT-37"):
        m = re.search(rf"\bproducts?\s*(?:called|named|:)?\s*{_QUOTED}|\binterested\s+in\s+{_QUOTED}", part, re.I)
        if m:
            name = (m.group(1) or m.group(2)).strip()
            if code == "INT-46":
                e["products"] = [name]
            else:
                e["offering_name"] = name
    return code, e


def _resolve_bare_approvals(steps: list[dict], catalog: dict) -> None:
    """'auto approve' with no record named: the record a neighbouring step
    makes that has an approval gate - a lead (INT-17), else Approve or reject
    on that step's record."""
    for i, s in enumerate(steps):
        if s.get("drop") or s["intent_type"] not in (None, "INT-36", "INT-17", "INT-06") or not _BARE_APPROVE.match(s["text"].strip()):
            continue
        # "approve it" - the record just before; otherwise the nearest, the one after first
        backwards = bool(re.search(r"\b(?:it|this|that)\b", s["text"], re.I))
        near = sorted(range(len(steps)), key=lambda j: (abs(j - i), (j > i) if backwards else (j < i)))
        for j in near:
            if j == i or steps[j].get("drop") or not steps[j]["intent_type"]:
                continue
            target = (catalog.get(steps[j]["intent_type"]) or {}).get("target")
            if target == "party":
                s["drop"] = f"“{s['text']}” - a new prospect needs no approval"
                break
            if target == "lead" and "INT-17" in catalog:
                s.update(intent_type="INT-17", entities={"decision": "approve"}, why="Approves the lead this goal creates")
                break
            if target in ("quote",) and "INT-06" in catalog:
                s.update(intent_type="INT-06", entities={"decision": "approve"}, why="Approves the quote this goal creates")
                break
            if target in ("order", "opportunity", "party") and "INT-36" in catalog:
                s.update(intent_type="INT-36", entities={"entity_type": target, "decision": "approve"}, why=f"Approves the {target} this goal creates")
                break


def _catalog(db: Session, tenant_id: int, user: User) -> dict[str, dict]:
    from app.intent_engine import intent_registry

    return {e["code"]: e for e in intent_registry.intents_for_user(db, tenant_id, user)}


def intent_registry_codes(db: Session, tenant_id: int, user: User) -> list[str]:
    return list(_catalog(db, tenant_id, user))


# Records an intent creates besides its own target (for the plan preview
# only - when the steps run, whatever a step really returns is passed on).
_ALSO_PRODUCES = {
    "INT-04": ["opportunity_id"],
    "INT-17": ["opportunity_id"],  # approving a lead opens its opportunity
    "INT-22": ["proposal_id"],
    "INT-25": ["work_order_id"],
    "INT-40": ["order_id"],
}


def _produces(entry: dict | None) -> list[str]:
    """The record ids a step creates. Its target counts only when the step
    doesn't take that record as an input ("Approve a lead" acts on a lead,
    "Generate a lead" makes one)."""
    target = (entry or {}).get("target")
    inputs = {f["key"] for f in (entry or {}).get("fields", [])}
    own = [f"{target}_id"] if target and not str(target).startswith("@") and f"{target}_id" not in inputs else []
    return own + _ALSO_PRODUCES.get((entry or {}).get("code"), [])


def _is_id(key: str) -> bool:
    return key.endswith("_id") or key == "entity_id"


def _invented(key: str, value: Any, text: str, context: dict | None) -> bool:
    """A record id the user never gave: not in their words, not the record on
    screen, not a reference to another step. A model fills such gaps with a
    plausible-looking number - the step would then act on the wrong record or
    none, instead of on the one an earlier step creates."""
    if not _is_id(key) or value in (None, ""):
        return False
    if isinstance(value, str) and _REF.match(value):
        return False
    if context and str(context.get("entity_id")) == str(value):
        return False
    return not re.search(rf"(?<!\d){re.escape(str(value))}(?!\d)", text or "")


def _clean(entities: dict, text: str, context: dict | None) -> dict:
    return {k: v for k, v in entities.items() if v not in (None, "", [], {}) and not _invented(k, v, text, context)}


def _order(steps: list[dict], catalog: dict) -> list[dict]:
    """Dependencies first: a step that needs a record a LATER step creates
    is moved after that step (stable otherwise; a cycle keeps the order)."""
    def needs(s: dict) -> set[str]:
        entry = catalog.get(s.get("intent_type")) or {}
        wanted = {f["key"] for f in entry.get("fields", []) if _is_id(f["key"])}
        return {k for k in wanted if (s.get("entities") or {}).get(k) in (None, "")}

    def makes(s: dict) -> set[str]:
        return set(_produces(catalog.get(s.get("intent_type"))))

    pending, ordered = list(steps), []
    while pending:
        made = set().union(*(makes(s) for s in ordered)) if ordered else set()
        later = lambda i: set().union(*(makes(s) for j, s in enumerate(pending) if j != i)) if len(pending) > 1 else set()  # noqa: E731
        pick = next((i for i, s in enumerate(pending) if not ((needs(s) - made) & later(i))), 0)
        ordered.append(pending.pop(pick))
    return ordered


def _rules_plan(db: Session, tenant_id: int, goal: str, context: dict | None, catalog: dict) -> list[dict]:
    from app.intent_engine import classifier

    steps: list[dict] = []
    discount: float | None = None
    for part in split_goal(goal):
        m = _DISCOUNT.match(part.strip())
        if m:  # a detail of the quote step (before or after it)
            discount = float(m.group(1) or m.group(2))
            quote = next((s for s in reversed(steps) if s["intent_type"] == "INT-05"), None)
            if quote is not None:
                quote["entities"]["requested_discount_pct"] = discount
                discount = None
            steps.append({"text": part, "intent_type": None, "entities": {}, "drop": f"“{part}” - set as the quote's discount"})
            continue
        if _OPPORTUNITY.match(part.strip()):
            steps.append({"text": part, "intent_type": None, "entities": {}, "opportunity": True,
                          "why": "I couldn't match this part to an action you can run - rephrase it or pick the action."})
            continue
        # the rules only - a model call per part costs seconds each; parts they aren't sure of go to one planner call (plan())
        result = classifier.classify(part, db, tenant_id, context, use_model=False)
        code = result.intent_type if result.intent_type in catalog else None
        entities = _clean(result.entities or {}, part, context) if code else {}
        refined, entities = _refine(code, part, entities, catalog)
        why = result.rationale if refined == code else "Recognised as a step of the sales chain"
        code = refined
        if code == "INT-05" and discount is not None:
            entities["requested_discount_pct"], discount = discount, None
        steps.append({"text": part, "intent_type": code, "entities": entities, "sure": result.confidence >= SURE, "why": why if code else
                      "I couldn't match this part to an action you can run - rephrase it or pick the action."})
    # "generate an opportunity" beside a step that qualifies the lead - qualifying opens it
    if any(s["intent_type"] in ("INT-17", "INT-04") for s in steps):
        for s in steps:
            if s.pop("opportunity", False):
                s["drop"] = f"“{s['text']}” - qualifying the lead opens the opportunity"
    _resolve_bare_approvals(steps, catalog)
    return steps


_PLAN_PROMPT = (
    "You are the planning agent of BRT 360, a telecom CRM. Break the user's goal into an ordered list of steps; "
    "each step is ONE action from the catalog below, chosen by its exact code. Fill in the fields you can from the goal "
    "(use the field keys shown). When a step needs a record that an earlier step creates, leave that field out - the "
    "system passes it on automatically - or write \"$<step number>.<field>\" (e.g. \"$1.party_id\"). Never invent a code, "
    "never use an action that is not listed, and keep to what the goal asks for. "
    "Reuse existing custom flows as well as built-in intents by their meaning, phrases and examples. "
    "Respect the customer's B2B/B2C segment; never substitute a process for the other segment. "
    "Leave unknown business values empty so the system can ask for them in a form. "
    'Answer with JSON only: {"steps": [{"text": "<the part of the goal>", "intent_type": "<code>", '
    '"entities": {...}, "why": "<one short sentence>"}]}'
)


PROMPT_ACTIONS = 24


def _prompt_codes(catalog: dict, goal: str, context: dict | None, must_include: tuple[str, ...] = ()) -> list[str]:
    """The actions the goal could use - the whole catalog is too long for a small model's context.
    The goal as a whole and each of its parts get their best matches."""
    from app.intent_engine import shortlist

    entries = list(catalog.values())
    codes: list[str] = []
    for part in split_goal(goal):
        codes += shortlist.pick(part, entries, context, limit=5)
    codes += shortlist.pick(goal, entries, context, limit=12)
    codes = list(dict.fromkeys(codes))[:PROMPT_ACTIONS]
    # training: the answer's actions must be listed - appended, so the list's order never gives the answer away
    return codes + [c for c in dict.fromkeys(must_include) if c in catalog and c not in codes]


def plan_prompt(catalog: dict, goal: str, context: dict | None, must_include: tuple[str, ...] = ()) -> str:
    """The planner's user message - also what the own model is trained on (crm_core/training_reasoning.py)."""
    lines = []
    for code in _prompt_codes(catalog, goal, context, must_include):
        e = catalog[code]
        fields = ", ".join(f"{f['key']}{'*' if f.get('required') else ''}" for f in e.get("fields", []))
        phrases = "; ".join([*(e.get("phrases") or []), *(e.get("examples") or [])][:3])
        lines.append(f"{code} | {e['label']} | {e.get('description', '')} | segment: {e.get('segment') or 'any'} | phrases: {phrases} | fields: {fields}")
    prompt = "Catalog (code | action | what it does | fields, * = required):\n" + "\n".join(lines)
    return prompt + f"\n\nWhat the user is looking at: {json.dumps(context or {})}\nGoal: {goal}"


# One request can plan the same goal more than once (the assistant, then the
# process builder) - inside reuse_plans() the model is asked only the first time.
_MODEL_PLANS: ContextVar[dict[str, Any] | None] = ContextVar("agent_model_plans", default=None)


@contextmanager
def reuse_plans() -> Iterator[None]:
    token = _MODEL_PLANS.set({})
    try:
        yield
    finally:
        _MODEL_PLANS.reset(token)


def _ask_model(target: Any, catalog: dict, goal: str, context: dict | None) -> dict[str, Any] | None:
    from app.crm_core import llm

    memo = _MODEL_PLANS.get()
    key = json.dumps([target.label, goal, context, sorted(catalog)], sort_keys=True, default=str)
    if memo is not None and key in memo:
        return copy.deepcopy(memo[key])
    try:
        answer = llm.chat_json(target, _PLAN_PROMPT, plan_prompt(catalog, goal, context))
    except llm.LlmUnavailable:
        answer = None  # remembered too: a model that just failed or ran out of time isn't asked again
    if memo is not None:
        memo[key] = copy.deepcopy(answer)
    return answer


def _llm_plan(db: Session, tenant_id: int, goal: str, context: dict | None, catalog: dict) -> list[dict] | None:
    from app.crm_core import llm

    target = llm.resolve(db, tenant_id)
    if target is None:
        return None
    answer = _ask_model(target, catalog, goal, context)
    if answer is None:
        return None
    raw = answer.get("steps")
    if not isinstance(raw, list) or not raw:
        return None
    from app.intent_engine import classifier

    steps = []
    for s in raw[:MAX_STEPS]:
        if not isinstance(s, dict):
            continue
        code = s.get("intent_type")
        entities = s.get("entities") if isinstance(s.get("entities"), dict) else {}
        part = str(s.get("text") or "") or goal
        if code in catalog and str(code).startswith("FLOW-") and not classifier.custom_fits(
                part, {"label": catalog[code].get("label", ""), "phrases": [p.lower() for p in catalog[code].get("phrases") or []]}):
            # a small model drifts to a tenant flow on one shared word - the step is what the rules read in it instead
            ruled = classifier.classify(part, db, tenant_id, context, use_model=False)
            code = ruled.intent_type if ruled.intent_type in catalog and not str(ruled.intent_type).startswith("FLOW-") else None
            entities = ruled.entities or {} if code else {}
            s = {**s, "why": ruled.rationale if code else
                 f"The model suggested “{catalog[s['intent_type']]['label']}”, but the request doesn't name it - pick the action yourself."}
        steps.append({
            "text": str(s.get("text") or ""),
            "intent_type": code if code in catalog else None,
            "entities": _clean(entities, f"{s.get('text') or ''} {goal}", context) if code in catalog else {},
            "why": str(s.get("why") or "") if code in catalog or code is None and s.get("intent_type") in catalog else
                   f"The model suggested \"{code}\", which you can't run here - pick the action yourself.",
        })
    return steps or None


def plan(db: Session, tenant_id: int, user: User, goal: str, context: dict | None = None, prefer_ai: bool = False) -> dict:
    """The steps for a goal, each annotated with what it needs."""
    catalog = _catalog(db, tenant_id, user)
    from app.orchestration import chain_memory

    remembered = chain_memory.recall(db, tenant_id, goal) if not context else None
    if remembered and all(s.get("intent_type") in catalog for s in remembered["steps"]):
        # this exact request ran to the end before - its proven plan, checked against today's catalog
        steps = [{"text": s.get("text") or "", "intent_type": s["intent_type"], "entities": dict(s.get("entities") or {}),
                  "why": "The plan that worked last time"} for s in remembered["steps"]]
        steps = annotate(db, tenant_id, steps, context, catalog)
        return {"goal": goal, "planner": "memory", "memory_id": remembered["id"], "steps": steps,
                "notes": [*(remembered.get("notes") or []), "Reusing the plan that carried this exact request out before."],
                **prerun_scan(db, tenant_id, user, steps)}
    steps = _rules_plan(db, tenant_id, goal, context, catalog)
    notes = [s["drop"] for s in steps if s.get("drop")]
    steps = [s for s in steps if not s.get("drop")]
    sure = sum(s["intent_type"] is not None and s.get("sure", True) for s in steps)
    for s in steps:
        s.pop("sure", None)
    planner = "rules"
    if prefer_ai or not steps or sure < len(steps) or len(steps) == 1 and len(goal.split()) > 14:
        drafted = _llm_plan(db, tenant_id, goal, context, catalog)
        if drafted and sum(s["intent_type"] is not None for s in drafted) >= sure:
            steps, planner = drafted, "ai"
    steps = annotate(db, tenant_id, _order(steps, catalog), context, catalog)
    return {"goal": goal, "planner": planner, "steps": steps, "notes": notes if planner == "rules" else [],
            **prerun_scan(db, tenant_id, user, steps)}


def prerun_scan(db: Session, tenant_id: int, user: User, steps: list[dict], flow_ids: list[int] | None = None) -> dict:
    """Conflicts this plan touches - its steps' automations, its own steps - for the admin to settle
    (priority and conditions) before it runs. Never stops a plan on its own."""
    from app.models.auth import UserRole
    from app.orchestration import conflicts

    try:
        found = conflicts.relevant(db, tenant_id, [s.get("intent_type") for s in steps], flow_ids or [], steps)
    except Exception:  # noqa: BLE001 - a scan that can't run must not stop the plan
        found = []
    return {"conflicts": found, "can_settle": getattr(user, "role", None) == UserRole.admin}


def annotate(db: Session, tenant_id: int, steps: list[dict], context: dict | None, catalog: dict | None = None) -> list[dict]:
    """Label, icon, required fields - and for each missing one, the earlier
    step that will supply it (or that the user has to)."""
    from app.experience.routes_intent import _required_fields

    supplied: dict[str, int] = {}
    if context and context.get("entity_type") and context.get("entity_id"):
        supplied[f"{context['entity_type']}_id"] = 0
    out = []
    for n, step in enumerate(steps, 1):
        code = step.get("intent_type")
        entry = (catalog or {}).get(code) if catalog is not None else None
        if entry is None and code:
            from app.intent_engine import intent_registry

            entry = intent_registry.entry(db, tenant_id, code)
        required, label = _required_fields(db, tenant_id, code) if code else ([], None)
        entities = dict(step.get("entities") or {})
        needs = []
        for f in required:
            if " or " in f or entities.get(f) not in (None, "", [], {}):
                continue
            needs.append({"field": f, "from_step": supplied.get(f)})
        out.append({
            "n": n, "text": step.get("text") or "", "intent_type": code, "label": label or (entry or {}).get("label") or "Not understood",
            "icon": (entry or {}).get("icon") or "❔", "entities": entities, "why": step.get("why") or "",
            "required": required, "needs": needs, "fields": [f["key"] for f in (entry or {}).get("fields", [])],
        })
        for produced in _produces(entry):
            supplied[produced] = n  # the latest step that makes it - as when the steps run
        if _product_of(entities) or code in ("INT-04", "INT-37"):
            supplied["offering_name"] = n  # the product chosen here carries on to configure / quote
    return out


def _product_of(entities: dict) -> str | None:
    """The product a step names - passed on to later steps that need one."""
    if isinstance(entities.get("offering_name"), str) and entities["offering_name"].strip():
        return entities["offering_name"].strip()
    products = entities.get("products")
    if isinstance(products, list) and products:
        first = products[0]
        name = first.get("offering_name") or first.get("name") if isinstance(first, dict) else first
        return str(name).strip() if name else None
    return None


# ---------------------------------------------------------------- running


def _resolve_refs(entities: dict, outputs: dict[int, dict]) -> dict:
    out = {}
    for k, v in entities.items():
        m = _REF.match(v) if isinstance(v, str) else None
        if m:
            v = outputs.get(int(m.group(1)), {}).get(m.group(2))
        out[k] = v
    return out


_NOT_FOUND = re.compile(r"^([a-z][a-z ]*?)(?: \S+)? \(not found\)$")


def _missing(reported: list[str], entities: dict) -> tuple[list[str], list[str]]:
    """The intent engine reports a record that doesn't exist as "lead (not
    found)" - a reason, not a field. Back to the field to ask for (lead_id),
    plus the record kinds that were not found."""
    fields, unknown = [], []
    for item in reported or []:
        m = _NOT_FOUND.match(item)
        key = f"{m.group(1).replace(' ', '_')}_id" if m else item
        if m and key not in entities and "entity_id" in entities:
            key = "entity_id"
        if m:
            unknown.append(m.group(1))
        if key not in fields:
            fields.append(key)
    return fields, unknown


# A step blocked by a rule of the sales process the goal skipped over: the
# steps that satisfy it, offered as a choice (never inserted silently).
# (pattern on the reason, what to tell the user, steps to insert before the blocked step)
_REPAIRS: list[tuple[re.Pattern, str, str, list[dict]]] = [
    (re.compile(r"is configured - it can be quoted once Presales finds it feasible", re.I), "presales",
     "Quoting needs Presales to find the opportunity feasible first. Send it to Presales and record it as feasible, then quote?",
     [{"intent_type": "INT-38", "entities": {}, "text": "Send it to Presales"},
      {"intent_type": "INT-18", "entities": {"decision": "feasible"}, "text": "Presales: feasible"}]),
    (re.compile(r"is in feasibility - it can be quoted once Presales finds it feasible", re.I), "feasible",
     "The opportunity is with Presales. Record it as feasible, then quote?",
     [{"intent_type": "INT-18", "entities": {"decision": "feasible"}, "text": "Presales: feasible"}]),
    (re.compile(r"no customer-approved proposal on file", re.I), "proposal",
     "An order needs the customer's acceptance first. Send the proposal and record that the customer accepted it, then create the order?",
     [{"intent_type": "INT-22", "entities": {}, "text": "Send the proposal"},
      {"intent_type": "INT-23", "entities": {"decision": "approve"}, "text": "The customer accepted the proposal"}]),
]


# A step that failed for want of a detail only the user knows: ask for that field instead of "couldn't go ahead".
_ASKS: list[tuple[re.Pattern, str, str]] = [
    (re.compile(r"Give the site address", re.I), "address", "the site address Presales should check"),
]


def _repairs(rationale: str, catalog_codes: set[str], n: int, address: str | None) -> list[dict]:
    out = []
    for pattern, key, message, insert in _REPAIRS:
        if pattern.search(rationale or "") and all(s["intent_type"] in catalog_codes for s in insert):
            steps = json.loads(json.dumps(insert))
            if address:
                for s in steps:
                    s["entities"].setdefault("address", address)
            out.append({"id": key, "message": message, "at": n, "insert": steps})
    return out


def _address(entities: dict) -> str | None:
    details = entities.get("details") if isinstance(entities.get("details"), dict) else {}
    value = entities.get("address") or details.get("address_line1") or details.get("address")
    return str(value).strip() if value else None


# How far a record has come - a step that would bring it to a stage it has already reached (often
# an automation did it a moment ago) is done, not failed.
_PROGRESS: dict[str, dict[str, int]] = {
    "lead": {"pending_qualification": 0, "qualified": 1},
    "opportunity": {"draft": 0, "qualified": 0, "configured": 1, "in_feasibility": 2, "feasibility_failed": 2, "feasible": 3, "quoted": 4, "won": 5},
    "quote": {"draft": 0, "pending_approval": 1, "approved": 2},
}
# step -> (record, id field, the stage it brings the record to, also skipped when already exactly there)
_BRINGS_TO: dict[str, tuple[str, str, str, bool]] = {
    "INT-17": ("lead", "lead_id", "qualified", True),
    "INT-37": ("opportunity", "opportunity_id", "configured", False),  # re-configuring at "configured" is a real change
    "INT-38": ("opportunity", "opportunity_id", "in_feasibility", True),
    "INT-18": ("opportunity", "opportunity_id", "feasible", True),
    "INT-06": ("quote", "quote_id", "approved", True),
}


def _already_done(db: Session, tenant_id: int, code: str, entities: dict) -> str | None:
    """'Lead #1 is already qualified' - when this step's record has already reached its stage."""
    from app.crm_core import lifecycle_service

    spec = _BRINGS_TO.get(code)
    if spec is None or str(entities.get("decision") or "approve").lower() not in ("approve", "feasible", "approved"):
        return None
    entity_type, key, stage, inclusive = spec
    if code == "INT-37" and (entities.get("pricing_attributes") or entities.get("technical_attributes")):
        return None
    record = lifecycle_service.get_record(db, tenant_id, entity_type, entities.get(key))
    if record is None:
        return None
    current = getattr(getattr(record, "stage", None) or getattr(record, "status", None), "value", None)
    order = _PROGRESS[entity_type]
    if current not in order:
        return None
    if order[current] > order[stage] or (inclusive and order[current] == order[stage]):
        return f"{entity_type.capitalize()} #{record.id} is already {current.replace('_', ' ')}"
    return None


def _linked(db: Session, tenant_id: int, memory: dict) -> None:
    """Ids that follow from the ones known: a qualified lead's opportunity, an opportunity's latest quote."""
    from app.models.crm import Opportunity, Quote

    if memory.get("lead_id") and not memory.get("opportunity_id"):
        opp = db.query(Opportunity).filter(Opportunity.tenant_id == tenant_id, Opportunity.lead_id == memory["lead_id"]).order_by(Opportunity.id.desc()).first()
        if opp is not None:
            memory["opportunity_id"] = opp.id
    if memory.get("opportunity_id") and not memory.get("quote_id"):
        quote = db.query(Quote).filter(Quote.opportunity_id == memory["opportunity_id"]).order_by(Quote.id.desc()).first()
        if quote is not None:
            memory["quote_id"] = quote.id


def _ids(result: dict | None) -> dict:
    return {k: v for k, v in (result or {}).items() if k.endswith("_id") and isinstance(v, int) and not isinstance(v, bool)}


def run(db: Session, tenant_id: int, user: User, access, steps: list[dict], mode: str = "rehearse",
        context: dict | None = None, goal: str = "") -> dict:
    """Runs the steps in one transaction. mode "rehearse" always rolls back;
    "execute" commits only when every step went through."""
    from app.experience.routes_intent import _handle, _required_fields
    from app.intent_engine import intent_registry
    from app.orchestration import rehearsal
    from app.schemas.intent import IntentContext, IntentRequest, StructuredIntent

    memory: dict[str, Any] = {}
    if context and context.get("entity_type") and context.get("entity_id"):
        memory[f"{context['entity_type']}_id"] = context["entity_id"]
    made: set[str] = set()  # ids created by the steps so far (not the record on screen)
    outputs: dict[int, dict] = {}
    report: list[dict] = []
    flows_run: list[dict] = []
    stopped: dict | None = None
    site_address: str | None = None  # an address the goal gave (the prospect's) - the site Presales checks
    by_automation: dict[str, set[int]] = {}  # ids the tenant's automations created during this run
    catalog_codes = set(intent_registry_codes(db, tenant_id, user))

    with rehearsal.capture(db) as rows:
        for n, step in enumerate(steps[:MAX_STEPS], 1):
            code = step.get("intent_type")
            if not code:
                stopped = {"step": n, "kind": "unclear", "message": f"Step {n} isn't an action I can run - choose one for \"{step.get('text', '')}\"."}
                report.append({"n": n, "status": "skipped", "summary": stopped["message"]})
                break
            entities = _resolve_refs(dict(step.get("entities") or {}), outputs)
            entry = intent_registry.entry(db, tenant_id, code)
            wanted = set(_required_fields(db, tenant_id, code)[0]) | {f["key"] for f in (entry or {}).get("fields", [])}
            filled = []
            for key in sorted(wanted):
                empty = entities.get(key) in (None, "", [], {})
                # a record an earlier step of this run created beats an id the user never gave
                guessed = key in made and _invented(key, entities.get(key), step.get("text") or "", context)
                if (empty or guessed) and key in memory and entities.get(key) != memory[key]:
                    entities[key] = memory[key]
                    filled.append(key)
            # "Approve or reject" names its record by type + id
            if entities.get("entity_type") and entities.get("entity_id") in (None, ""):
                key = f"{entities['entity_type']}_id"
                if key in memory:
                    entities["entity_id"] = memory[key]
                    filled.append("entity_id")
            # what this position can see was cached by the step before - the records it just created aren't in it
            if hasattr(access, "_cache"):
                access._cache = {}
            # "Create a quote" when an automation of this run just created the draft quote for this opportunity:
            # give that quote the asked discount / quantity instead of making a second one
            note = ""
            if code == "INT-05" and memory.get("quote_id") and memory["quote_id"] in by_automation.get("quote_id", set()):
                from app.models.crm import Quote, QuoteStatus

                quote = db.get(Quote, memory["quote_id"])
                if quote is not None and quote.status == QuoteStatus.draft and quote.opportunity_id == entities.get("opportunity_id"):
                    changes = {k: entities[k] for k in ("requested_discount_pct", "quantity") if entities.get(k) not in (None, "", 0, 0.0)}
                    if "INT-19" in catalog_codes and changes:
                        code, entities = "INT-19", {"quote_id": quote.id, **changes}
                        note = f"Your automation already created Quote #{quote.id} - revised it instead of making a second one. "
                    elif not changes:
                        report.append({"n": n, "intent_type": code, "label": (entry or {}).get("label") or code, "icon": (entry or {}).get("icon") or "",
                                       "status": "done", "summary": f"Already done - your automation created Quote #{quote.id}. Carried on.",
                                       "entities_used": {"quote_id": quote.id}, "filled_from_memory": filled, "missing_fields": []})
                        outputs[n] = {"quote_id": quote.id}
                        continue
            # an automation (or someone) already did this step's work - done, and on to the next
            already = _already_done(db, tenant_id, code, entities)
            if already:
                report.append({"n": n, "intent_type": code, "label": (entry or {}).get("label") or code, "icon": (entry or {}).get("icon") or "",
                               "status": "done", "summary": f"Already done - {already} (an automation or a colleague did it). Carried on.",
                               "entities_used": {k: v for k, v in entities.items() if not str(k).startswith("_")}, "filled_from_memory": filled,
                               "missing_fields": []})
                outputs[n] = {k: v for k, v in entities.items() if _is_id(k) and isinstance(v, int)}
                _linked(db, tenant_id, memory)
                continue
            request = IntentRequest(text=step.get("text") or step.get("label") or code,
                                    structured=StructuredIntent(intent_type=code, entities=entities),
                                    context=IntentContext(**context) if context else None)
            response = _handle(request, db, tenant_id, user, access, hold=True)
            done = response.status == "executed"
            missing, unknown = _missing(response.missing_fields, entities)
            report.append({
                "n": n, "intent_type": code, "label": response.intent_label or code, "icon": response.intent_icon or "",
                "status": "done" if done else response.status,
                "summary": note + (response.summary or response.rationale or "")
                + (f" Note: {(response.result or {}).get('note')}" if done and isinstance((response.result or {}).get("note"), str) else ""),
                "entities_used": {k: v for k, v in entities.items() if not str(k).startswith("_")}, "filled_from_memory": filled,
                "missing_fields": missing,
            })
            configuration = (response.result or {}).get("configuration_request")
            if done and configuration:
                required = ["submit_for_provisioning"] if configuration.get("intent") == code else []
                stopped = {"step": n, "kind": "needs_input" if required else "blocked", "missing_fields": required,
                           "message": "This step needs its configuration form completed before the plan can continue."}
                report[-1].update(status="needs_review", missing_fields=required)
                break
            if not done:
                kind = "needs_input" if response.status == "needs_review" and missing else "blocked"
                asks = [f"which {u} (the one given doesn't exist or isn't visible to you)" for u in unknown]
                asks += [f.replace("_", " ") for f in missing if f not in {f"{u.replace(' ', '_')}_id" for u in unknown}]
                stopped = {"step": n, "kind": kind, "missing_fields": missing,
                           "message": (f"Step {n} needs {', '.join(asks)} before I can go on."
                                       if kind == "needs_input" else f"Step {n} couldn't go ahead: {response.rationale}")}
                ask = next(((field, said) for pattern, field, said in _ASKS if pattern.search(response.rationale or "")), None) if kind == "blocked" else None
                if ask:
                    kind, missing = "needs_input", [ask[0]]
                    report[-1].update(status="needs_review", missing_fields=missing)
                    stopped = {"step": n, "kind": kind, "missing_fields": missing, "message": f"Step {n} needs {ask[1]} before I can go on."}
                reasons = " ".join([response.rationale or "", *(response.missing_fields or [])])
                fixes = _repairs(reasons, set(intent_registry_codes(db, tenant_id, user)), n, site_address) if not ask else []
                if fixes:
                    stopped.update(kind="blocked", missing_fields=[], repairs=fixes, message=f"Step {n} - {fixes[0]['message']}")
                    report[-1]["missing_fields"] = []
                break
            produced = _ids(response.result)
            outputs[n] = {**produced, **{k: v for k, v in (response.result or {}).items() if isinstance(v, (str, int, float))}}
            memory.update(produced)
            made.update(produced)
            # records the step's automations created (the lead's opportunity, a quote) - passed on like the step's own
            for run in (response.result or {}).get("flows_run", []):
                for node in run.get("trace", []):
                    by_flow = {k: v for k, v in (node.get("result_ids") or {}).items() if isinstance(v, int)}
                    memory.update(by_flow)
                    made.update(by_flow)
                    for k, v in by_flow.items():
                        by_automation.setdefault(k, set()).add(v)
            _linked(db, tenant_id, memory)
            product = (response.result or {}).get("offering_name") if isinstance((response.result or {}).get("offering_name"), str) else None
            if product or _product_of(entities):
                memory["offering_name"] = product or _product_of(entities)
            site_address = _address(entities) or site_address
            flows_run.extend((response.result or {}).get("flows_run", []))

        db.flush() if stopped is None else None
        impact = rehearsal.summarize(db, rows, {"flows_run": flows_run})

    if mode == "execute" and stopped is None:
        db.commit()
        status = "done"
        try:  # remember the chain that worked, for an exact repeat of this request
            from app.orchestration import chain_memory

            chain_memory.remember(db, tenant_id, getattr(user, "id", None), goal, steps[:MAX_STEPS])
        except Exception:  # noqa: BLE001 - memory is a convenience; the run itself is already committed
            db.rollback()
    else:
        db.rollback()
        status = "rehearsed" if stopped is None else "paused"

    ok = len([r for r in report if r["status"] == "done"])
    if status == "done":
        message = f"Done - all {ok} step{'s' if ok != 1 else ''} carried out."
    elif status == "rehearsed":
        message = f"Rehearsed all {ok} step{'s' if ok != 1 else ''} on your live data - nothing was kept. {impact['headline']}"
    else:
        message = stopped["message"] + (" Nothing was changed - the steps run together or not at all." if mode == "execute" else "")
    numbered = [{**s, "n": s.get("n") or n} for n, s in enumerate(steps[:MAX_STEPS], 1)]
    scan = prerun_scan(db, tenant_id, user, numbered, [f.get("flow_id") for f in flows_run])
    return {"goal": goal, "mode": mode, "status": status, "message": message, "steps": report, "stopped": stopped,
            "impact": impact, "memory": memory if status == "done" else {}, **scan}

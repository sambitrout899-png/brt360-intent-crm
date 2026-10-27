"""The reasoning step of the Intent Command Bar - an automation asked for in plain English,
understood, reasoned over what the tenant already has, and planned as flows built on consent.

    "I need a process which creates an order automatically for B2B prospects with Company Type Government"

    understand()  the goal - who it is for (record, segment, a field = value), the outcome wanted
                  (lead ... order), the product - by the rules here, or by the tenant's AI model when
                  the rules can't place it (checked against the real fields, values and products);
    reason        the road the vanilla process takes to that outcome, as the existing intents that do
                  each step (Generate a lead -> Approve a lead -> Create a quote -> ...), the steps that
                  need a person and can't be automated (the customer's yes), the choices that are the
                  business's to make (skip Presales? approve the quote automatically?), and flows that
                  already do it - recommended instead of building again;
    plan()        (process_builder._Design.goal) the questions still open, then one flow per stretch of
                  road between waits: the first runs after "Add a prospect" when the condition holds,
                  each next one when the record the wait was for moves on (the quote is approved, the
                  customer accepts) - conditions on the customer (flow_engine.customer_of).

Nothing is built without consent: the plan is a process-builder blueprint - rehearsed, then built.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.orm import Session

# ---------------------------------------------------------------- the B2B road from a saved prospect

# (outcome key, the intent that reaches it, what the user gets, words that ask for it) - furthest first
OUTCOMES: list[tuple[str, str, str, re.Pattern]] = [
    ("order", "INT-24", "an order", re.compile(
        r"\b(?:create|place|raise|generate|book|make|open|produce)\w*\s+(?:an?\s+|the\s+)?(?:new\s+)?(?:sales\s+)?order\b"
        r"|\border\s+(?:is\s+|gets\s+)?(?:created|placed|raised|generated)\b|\b(?:in)?to\s+(?:an?\s+)?order\b|\bconvert\w*\b[^.]{0,30}\border\b", re.I)),
    ("proposal", "INT-22", "a proposal sent to the customer", re.compile(r"\b(?:send|generate|create|prepare)\w*\s+(?:an?\s+|the\s+)?proposal\b", re.I)),
    ("approved_quote", "INT-06", "an approved quote", re.compile(r"\bapprove\w*\s+(?:the\s+|a\s+)?quot(?:e|ation)\b|\bapproved\s+quot(?:e|ation)\b", re.I)),
    ("quote", "INT-05", "a quote", re.compile(r"\b(?:create|generate|make|prepare|raise|build|produce|issue)\w*\s+(?:an?\s+|the\s+)?(?:new\s+)?quot(?:e|ation)\b"
                                             r"|\b(?:in)?to\s+(?:an?\s+)?quot(?:e|ation)\b", re.I)),
    ("opportunity", "INT-17", "a qualified lead with its opportunity", re.compile(
        r"\b(?:create|open|generate)\w*\s+(?:an?\s+|the\s+)?opportunit(?:y|ies)\b|\bqualif\w*\s+(?:the\s+|a\s+)?lead\b", re.I)),
    ("lead", "INT-46", "a lead", re.compile(r"\b(?:create|open|generate|raise|make)\w*\s+(?:an?\s+|the\s+)?(?:new\s+)?leads?\b", re.I)),
    ("account", "INT-35", "an account", re.compile(r"\bconvert\w*\b[^.]{0,40}?\b(?:(?:in)?to|as)\s+(?:an?\s+|the\s+)?account\b|\bbecomes?\s+an?\s+account\b", re.I)),
]
OUTCOME_CODES = {key: code for key, code, _l, _p in OUTCOMES}
ROAD = ["INT-46", "INT-17", "INT-05", "INT-06", "INT-22", "INT-24"]  # vanilla B2B steps the automation runs (Presales optional)
PRESALES = ["INT-37", "INT-38"]  # configure with defaults, send to Presales - then wait for "feasible"

_AUTOMATION = re.compile(r"\b(?:automatic\w*|auto[- ]?(?:create|generat|convert|approv|place)\w*|process|workflow|automation|whenever|always|"
                         r"straight\s+away|directly|on\s+its\s+own|by\s+itself|without\s+(?:any\s+)?(?:manual|human)|for\s+(?:any|every|all|each)\b)", re.I)
_START = re.compile(r"\b(?:prospects?|customers?|compan(?:y|ies)|accounts?|parties|party|clients?|b2b|enterprises?|businesses)\b", re.I)
_B2C = re.compile(r"\b(?:b2c|consumers?|households?|individuals?|retail\s+customers?)\b", re.I)
# a request about one named record ("a company called "XXX"", "quote 12") is a chain of things to DO now - the agent's
_SPECIFIC = re.compile(r"\b(?:called|named)\s+[\"“'‘]?\w|\b(?:company|customer|prospect)\s+[\"“'‘]\w|"
                       r"\b(?:quote|order|lead|opportunity|prospect|customer|party)\s+(?:#|no\.?\s*|number\s+)?\d+\b", re.I)
_QUESTIONY = re.compile(r"^\s*(?:how|what|why|where|which|who|can\s+i|could\s+i|is\s+there)\b|\?\s*$", re.I)
_ANY_PRODUCT = re.compile(r"\b(?:any|every|all|each|whichever|whatever)\s+products?\b|\bproducts?\s+(?:they|the\s+(?:prospect|customer))\s+(?:want|choose|pick|select)", re.I)
_PRODUCT = re.compile(r"\bproduct\s+(?:as|is|=|:|called|named)?\s*[\"“'‘](?P<p>[^\"”'’]+)[\"”'’]", re.I)
# "with Company Type Government", "whose company type is PSU", "where Account tier = Strategic", "with Company Type" (value left out)
_CONDITION = re.compile(
    r"\b(?:with|whose|where|having|if|when|of)\s+(?:the\s+|a\s+|an\s+|its\s+|their\s+)?(?P<phrase>[A-Za-z][^,.;:]{1,70}?)"
    r"(?=\s+(?:for|and|then|in|to|on|which|that|so|it|we|create|place|generate|convert)\b|\s*[,.;:]|\s*$)", re.I)


@dataclass
class Goal:
    text: str
    outcome: str  # an OUTCOMES key
    segment: str = "B2B"
    field_label: str | None = None
    field_key: str | None = None
    field_values: list[str] = field(default_factory=list)  # the field's picklist - the question's options
    value: str | None = None
    product: str | None = None  # an offering name, "*" = the products picked on Add a prospect, None = not said
    choices: list[tuple[str, str, str]] = field(default_factory=list)  # (key, label, value) a value said alone could mean
    understood_by: str = "rules"
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {"outcome": self.outcome, "segment": self.segment, "field": self.field_label, "key": self.field_key, "value": self.value,
                "product": self.product, "understood_by": self.understood_by}


# ---------------------------------------------------------------- understanding


def _party_fields(db: Session, tenant_id: int) -> list[dict[str, Any]]:
    from app.crm_core import page_service

    seen: dict[str, dict[str, Any]] = {}
    for seg in (None, "B2B", "B2C"):
        for f in page_service.catalog(db, tenant_id, "party", seg):
            seen.setdefault(f.get("key"), f)
    return list(seen.values())


def _values(f: dict[str, Any]) -> list[str]:
    raw = f.get("values") or f.get("options") or []
    return [str(v.get("value") if isinstance(v, dict) else v) for v in raw if v not in (None, "")]


def _offerings(db: Session, tenant_id: int) -> list[str]:
    from app.integration import tmf620_adapter

    return list(tmf620_adapter.active_offering_names(db, tenant_id))


def _condition(db: Session, tenant_id: int, text: str, fields: list[dict[str, Any]]) -> tuple[dict[str, Any] | None, str | None]:
    """(the party field the request names, its value or None) - "with Company Type Government", or a picklist
    value said on its own ("for Government prospects") when exactly one field has it (see value_fields)."""
    from app.orchestration.process_fit import _known_field

    for m in _CONDITION.finditer(text):
        words = re.sub(r"[\"“”'‘’]", "", m.group("phrase")).split()
        # "with Company Type Government" - the field is the longest leading run of words that names one
        for n in range(min(len(words), 5), 0, -1):
            head = " ".join(words[:n])
            found = _known_field(head, fields) if len(head) >= 3 and words[0].lower() not in ("a", "an", "the", "any", "every", "all") else None
            if found is None or not _values(found) and found.get("data_type") not in ("text", "number", "boolean"):
                continue
            rest = re.sub(r"^(?:is|as|=|of|being|equal\s+to|set\s+to)\s+", "", " ".join(words[n:]), flags=re.I).strip()
            return found, _pick_value(rest, _values(found)) if rest else None
    candidates = value_fields(text, fields)
    return candidates[0] if len(candidates) == 1 else (None, None)


def value_fields(text: str, fields: list[dict[str, Any]]) -> list[tuple[dict[str, Any], str]]:
    """Every (field, value) a picklist value said on its own could mean - "Government" is an Industry and a Company type."""
    out = []
    for f in fields:
        for v in _values(f):
            if len(v) >= 3 and not re.fullmatch(r"(?i)b2[bc]|yes|no|true|false|other", v) and re.search(rf"(?<![\w-]){re.escape(v)}(?![\w-])", text, re.I):
                out.append((f, v))
                break
    return out


def _pick_value(said: str, values: list[str]) -> str | None:
    if not said:
        return None
    if not values:
        return said
    exact = next((v for v in values if v.lower() == said.lower()), None)
    if exact:
        return exact
    starts = [v for v in values if said.lower().startswith(v.lower()) or v.lower().startswith(said.lower())]
    return starts[0] if len(starts) == 1 else None


def _outcome(text: str) -> str | None:
    return next((key for key, _c, _l, pattern in OUTCOMES if pattern.search(text)), None)


def understand(db: Session, tenant_id: int, text: str, use_ai: bool = True) -> Goal | None:
    """The automation the request asks for, or None when it isn't one (a question, a single action to do now,
    a page or approval change - the other builders' work)."""
    text = " ".join((text or "").split())
    if not text or _QUESTIONY.search(text) or _B2C.search(text) or _SPECIFIC.search(text):
        return None
    automation, start = _AUTOMATION.search(text), _START.search(text)
    outcome = _outcome(text) if automation and start else None
    goal = None
    if outcome:
        goal = Goal(text=text, outcome=outcome)
    elif use_ai and automation and start:
        goal = _ai_goal(db, tenant_id, text)
    if goal is None:
        return None
    fields = _party_fields(db, tenant_id)
    if goal.field_label is None:
        found, value = _condition(db, tenant_id, text, fields)
        if found is not None:
            goal.field_label, goal.field_key, goal.field_values = found.get("label") or found["key"], found["key"], _values(found)
            goal.value = goal.value or value
        else:
            goal.choices = [(f["key"], f.get("label") or f["key"], v) for f, v in value_fields(text, fields)]
    if goal.product is None:
        named = _PRODUCT.search(text)
        offerings = _offerings(db, tenant_id)
        goal.product = (named.group("p") if named else None) or next(
            (o for o in offerings if re.search(rf"(?<![\w-]){re.escape(o)}(?![\w-])", text, re.I)), None) or ("*" if _ANY_PRODUCT.search(text) else None)
    return goal


_AI_PROMPT = (
    "You read a telecom CRM administrator's request for an AUTOMATION on B2B prospects and return strict JSON "
    '{"automation": true|false, "outcome": one of %s or null, "field": the prospect field the rule depends on or null, '
    '"value": that field\'s value or null, "product": the product named or "*" for any product or null}. '
    "outcome = the furthest thing that should happen automatically after a prospect is saved. Use only fields from: %s. "
    "A field's value must be one of its values when it lists them; never guess a value that was not said."
)


def _ai_goal(db: Session, tenant_id: int, text: str) -> Goal | None:
    """The tenant's model reads what the rules couldn't - checked against the outcomes and real fields."""
    from app.crm_core import llm

    target = llm.resolve(db, tenant_id)
    if target is None:
        return None
    fields = [f for f in _party_fields(db, tenant_id) if _values(f)][:25]
    catalog = {f.get("label") or f["key"]: _values(f)[:15] for f in fields}
    try:
        with llm.budget(45):
            answer = llm.chat_json(target, _AI_PROMPT % (json.dumps(list(OUTCOME_CODES)), json.dumps(catalog)), text, max_tokens=300)
    except Exception:  # noqa: BLE001 - the rules' answer stands
        return None
    if not answer.get("automation") or answer.get("outcome") not in OUTCOME_CODES:
        return None
    goal = Goal(text=text, outcome=answer["outcome"], understood_by="ai")
    label = str(answer.get("field") or "").strip()
    found = next((f for f in fields if label and label.lower() in ((f.get("label") or "").lower(), f["key"].lower())), None)
    if found is not None:
        goal.field_label, goal.field_key, goal.field_values = found.get("label") or found["key"], found["key"], _values(found)
        value = str(answer.get("value") or "").strip()
        goal.value = _pick_value(value, goal.field_values) if value and re.search(rf"(?i)\b{re.escape(value)}\b", text) else None
    product = str(answer.get("product") or "").strip()
    goal.product = "*" if product == "*" else (product if product and product.lower() in text.lower() else None)
    return goal


def summary(goal: Goal) -> str:
    who = f"B2B prospects whose {goal.field_label} is {goal.value}" if goal.field_label and goal.value else (
        f"B2B prospects by their {goal.field_label} (which value?)" if goal.field_label else "every B2B prospect")
    label = next(l for k, _c, l, _p in OUTCOMES if k == goal.outcome)
    by = "your AI model" if goal.understood_by == "ai" else "the built-in reasoning"
    return f"An automation: {who} get {label} automatically (understood by {by})."


# ---------------------------------------------------------------- reasoning: the road, the waits, what exists


@dataclass
class Stretch:
    """One flow: a trigger, and the intents it runs one after another."""
    trigger: dict[str, Any]
    entity_type: str
    condition_path: str
    steps: list[tuple[str, dict[str, Any]]]
    why: str


def road(goal: Goal, presales: bool, auto_approve: bool) -> tuple[list[Stretch], list[str]]:
    """The flows that take a matching prospect to the outcome, and the reasoning to show for them."""
    reasons: list[str] = []
    if goal.outcome == "account":
        return [Stretch({"trigger_type": "intent", "trigger_intent_type": "INT-15", "timing": "after", "entity_type": "party",
                         "summary": "After “Add a prospect”"}, "party", "record", [("INT-35", {})], "Converts the prospect")], reasons
    target = OUTCOME_CODES[goal.outcome]
    codes = ROAD[: ROAD.index(target) + 1]
    products: Any = "{{entities.products}}" if goal.product in (None, "*") else [goal.product]
    first = Stretch({"trigger_type": "intent", "trigger_intent_type": "INT-15", "timing": "after", "entity_type": "party",
                     "summary": "After “Add a prospect”"}, "party", "record", [], "From the saved prospect")
    stretches, current = [first], first
    ids = {"INT-46": {"party_id": "{{record.id}}", "products": products},
           "INT-17": {"lead_id": "{{result.lead_id}}", "decision": "approve"},
           # the product the lead was opened for (Generate a lead's output) - a flow step doesn't look it up by itself
           "INT-05": {"opportunity_id": "{{result.opportunity_id}}",
                      "offering_name": goal.product if goal.product not in (None, "*") else "{{result.interested_products.0.offering_name}}"},
           "INT-06": {"quote_id": "{{result.quote_id}}", "decision": "approve"},
           "INT-22": {"quote_id": "{{result.quote_id}}"},
           "INT-24": {"quote_id": "{{result.quote_id}}"}}
    for code in codes:
        if code == "INT-05" and presales:
            # the site Presales checks: the address given when the prospect was added
            site = "{{entities.installation_address}} {{record.address_line1}}, {{record.city}}"
            current.steps += [("INT-37", {"opportunity_id": "{{result.opportunity_id}}"}),
                              ("INT-38", {"opportunity_id": "{{result.opportunity_id}}", "address": site})]
            reasons.append("Presales checks feasibility as usual - the automation waits, and carries on by itself once they mark it feasible.")
            current = Stretch({"trigger_type": "state_enter", "entity_type": "opportunity", "trigger_state": "feasible",
                               "summary": "When an opportunity is marked feasible"}, "opportunity", "customer", [], "After Presales says feasible")
            stretches.append(current)
            current.steps.append(("INT-05", {"opportunity_id": "{{record.id}}", "offering_name": goal.product if goal.product not in (None, "*")
                                             else "{{record.offering_name}}"}))
            continue
        if code == "INT-06" and not auto_approve:
            reasons.append("The quote waits for its approver as usual - the automation carries on by itself once it is approved.")
            if target == "INT-06":
                break
            current = Stretch({"trigger_type": "state_enter", "entity_type": "quote", "trigger_state": "approved",
                               "summary": "When a quote is approved"}, "quote", "customer", [], "After the quote is approved")
            stretches.append(current)
            continue
        if code == "INT-24":
            reasons.append("An order needs the customer's yes - nobody can decide that for them. The proposal goes out automatically; "
                           "the order is created automatically the moment their acceptance is recorded (Record the customer's answer).")
            current = Stretch({"trigger_type": "intent", "trigger_intent_type": "INT-23", "timing": "after", "entity_type": "quote",
                               "summary": "After “Record the customer's answer” - when they accept"}, "quote", "customer",
                              [("INT-24", {"quote_id": "{{record.id}}"})], "After the customer accepts")
            stretches.append(current)
            continue
        entities = dict(ids[code])
        if current is not first and not current.steps and code in ("INT-22",):
            entities = {"quote_id": "{{record.id}}"}
        current.steps.append((code, entities))
    if goal.product == "*":
        reasons.append("Any product: the lead takes the product(s) picked when the prospect is added - a prospect added without one stops at the lead.")
    if not presales and target in ("INT-05", "INT-06", "INT-22", "INT-24"):
        reasons.append("Presales feasibility is skipped for these prospects, as you chose.")
    return [s for s in stretches if s.steps], reasons


def graph_for(stretch: Stretch, goal: Goal, labels: dict[str, str]) -> dict[str, Any]:
    nodes: list[dict[str, Any]] = [{"id": "start", "type": "start", "label": "Start", "config": {}}]
    edges: list[dict[str, Any]] = []
    tail, tail_label = "start", ""
    checks = []
    if goal.field_key and goal.value:
        checks.append((f"{stretch.condition_path}.{goal.field_key}", goal.value, f"{goal.field_label} is {goal.value}"))
    if stretch.trigger.get("trigger_intent_type") == "INT-23":
        checks.append(("result.status", "customer_approved", "The customer accepted"))
    for i, (path, value, label) in enumerate(checks, start=1):
        nodes.append({"id": f"check{i}", "type": "condition", "label": label[:120], "config": {"field": path, "operator": "eq", "value": value}})
        edges.append({"id": f"e{len(edges)}", "source": tail, "target": f"check{i}", "label": tail_label})
        tail, tail_label = f"check{i}", "true"
    for i, (code, entities) in enumerate(stretch.steps, start=1):
        nodes.append({"id": f"run{i}", "type": "action", "label": labels.get(code, code), "config": {"intent": code, "entities": entities}})
        edges.append({"id": f"e{len(edges)}", "source": tail, "target": f"run{i}", "label": tail_label})
        tail, tail_label = f"run{i}", ""
    nodes.append({"id": "end", "type": "end", "label": "End", "config": {}})
    edges.append({"id": f"e{len(edges)}", "source": tail, "target": "end", "label": tail_label})
    for i in range(1, len(checks) + 1):
        edges.append({"id": f"e{len(edges)}", "source": f"check{i}", "target": "end", "label": "false"})
    return {"nodes": nodes, "edges": edges}


def existing_flow(db: Session, tenant_id: int, stretch: Stretch, graph: dict[str, Any]) -> Any:
    """An active flow that already does this stretch: same trigger, same conditions, at least these steps."""
    from app.models.flows import FlowDefinition

    trigger = stretch.trigger
    query = db.query(FlowDefinition).filter(FlowDefinition.tenant_id == tenant_id, FlowDefinition.is_active.is_(True),
                                            FlowDefinition.trigger_type == trigger["trigger_type"])
    if trigger.get("trigger_intent_type"):
        query = query.filter(FlowDefinition.trigger_intent_type == trigger["trigger_intent_type"])
    if trigger.get("trigger_state"):
        query = query.filter(FlowDefinition.trigger_state == trigger["trigger_state"], FlowDefinition.entity_type == stretch.entity_type)

    def shape(g: dict[str, Any]) -> tuple[set, list]:
        nodes = g.get("nodes") or []
        conds = {(str((n.get("config") or {}).get("field")).split(".")[-1], str((n.get("config") or {}).get("value")).lower())
                 for n in nodes if n.get("type") == "condition"}
        return conds, [(n.get("config") or {}).get("intent") for n in nodes if n.get("type") == "action"]

    want_conds, want_steps = shape(graph)
    for flow in query.all():
        try:
            conds, steps = shape(json.loads(flow.graph_json or "{}"))
        except ValueError:
            continue
        if conds == want_conds and all(s in steps for s in want_steps):
            return flow
    return None


def intents_today(codes: list[str], labels: dict[str, str]) -> str:
    return " → ".join(f"{labels.get(c, c)} ({c})" for c in ["INT-15", *codes])

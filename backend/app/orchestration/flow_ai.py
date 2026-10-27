"""Flow Studio AI assist (Phase 7) - drafts a flow graph from a plain-
English description of a business process.

Primary path: the tenant's configured LLM returns a graph using ONLY the
closed node vocabulary in flow_engine.NODE_TYPES; the result is checked
with flow_engine.validate_graph before it is ever shown, and a draft
that fails validation is discarded in favor of the fallback. Fallback
(no key, or an unusable answer): a deterministic clause parser that
understands the common shapes ("if X is Y ...", "otherwise ...", "assign
to ...", "set X to Y", "move to <state>", "notify ...", "reject ...").
Either way the admin gets an editable draft on the canvas - never an
auto-activated flow.
"""

from __future__ import annotations

import json
import re
from typing import Any

from sqlalchemy.orm import Session

from app.orchestration import flow_engine


def _field_line(f: dict[str, Any]) -> str:
    """One field as a short line - "path (type, settable) "Label" = a|b|c". JSON objects per field made
    the prompt several thousand tokens, which a CPU-hosted model can't read inside its time limit."""
    label = f.get("label") or ""
    said = re.sub(r"[^a-z0-9]+", "_", re.split(r"[→:]", label)[-1].lower()).strip("_")
    line = f"{f['path']} ({f['data_type']}{', settable' if f['settable'] else ''})"
    if said and said not in f["path"].lower():
        line += f' "{label}"'
    if f["values"]:
        line += " = " + "|".join(map(str, f["values"][:15]))
    return line


def _context_for_prompt(catalog: dict[str, Any]) -> dict[str, Any]:
    """The Studio's field catalog (flow_schema.field_catalog), trimmed to
    what the model needs to reference real fields and values."""
    return {
        "fields": [_field_line(f) for f in catalog.get("fields", [])],
        "states": [s["code"] for s in catalog.get("states", [])],
        "positions": catalog.get("positions", []),
        "users": [u["email"] for u in catalog.get("users", [])][:50],
    }


PROMPT_TABLES = 6
# a description that reads or writes records directly - only then are table schemas worth their prompt space
_RECORD_WORDS = re.compile(r"\b(?:records?|tables?|rows?|insert\w*|upsert\w*|delet\w*|updat\w*|enter|look\s*up|read|fetch|find)\b|\b(?:order|line)\s+items?\b",
                           re.I)


def _tables_for_prompt(entities: list[dict[str, Any]], prompt: str, entity_type: str | None) -> list[dict[str, Any]]:
    """The tables the description is about, compacted. The whole schema (well over a hundred
    tables) is many times a local model's context - Ollama would silently drop most of the prompt."""
    said = {w[:4] for w in re.findall(r"[a-z0-9]{3,}", f"{prompt} {entity_type or ''}".lower())}
    scored = []
    for e in entities:
        words = [w for w in e["name"].split("_") if len(w) >= 3]
        hits = sum(w[:4] in said for w in words)
        if hits:
            scored.append((hits == len(words), hits, -len(words), e))
    scored.sort(key=lambda t: t[:3], reverse=True)
    return [{"name": e["name"], "fields": [{k: v for k, v in f.items() if v not in (False, [], None)} for f in e["fields"]]}
            for *_, e in scored[:PROMPT_TABLES]]


def _llm_generate(target: Any, prompt: str, entity_type: str | None, info: dict[str, Any]) -> dict[str, Any]:
    from app.crm_core import llm

    node_spec = "; ".join(
        f"{name} (config: {', '.join(spec['config']) or 'none'}; outgoing edge labels: {', '.join(o or '\"\"' for o in spec['outputs']) or 'none - final'})"
        for name, spec in flow_engine.NODE_TYPES.items()
    )
    system = (
        "You design business-process flows for a telecom CRM's Flow Studio. Output strict JSON "
        '{"nodes": [{"id": "n1", "type": "...", "label": "...", "config": {...}}], '
        '"edges": [{"source": "n1", "target": "n2", "label": ""}], "notes": "one sentence"}. '
        f"Allowed node types ONLY: {node_spec}. Condition operators: {', '.join(flow_engine.OPERATORS)}. "
        "Exactly one start node. Condition/ai_decision edges must be labeled true/false. "
        "Condition and set_field `field` must be one of the field paths given (e.g. record.total_price, "
        "entities.requested_discount_pct); set_field only on settable fields; picklist comparisons must use "
        "one of that field's values exactly; templates are {{path}}. "
        f"The flow targets entity type: {entity_type or 'none'}. Known context: {json.dumps(info)}. "
        "Only use states, fields, values, users and positions from that context. "
        "For direct entity CRUD use record nodes: entity is a table name from record_entities; operation is read, insert, update, upsert or delete; "
        "match and values are JSON objects. Match must identify one record. Supply all required fields on inserts. "
        "Map foreign keys from earlier nodes using {{steps.NODE_ID.output.id}}; full rows are under output.record. "
        "Use map nodes with a values object for output mapping. List required trigger inputs in notes; never invent record IDs. "
        "An assign node hands over the flow's record; add config \"target\": \"installation_work_order\" (or \"work_order\") to hand over "
        "the order's open (installation) work order instead. "
        "Error handling - only when the description asks for it ('if it fails', 'on error', 'otherwise tell ops'): link a step's "
        "\"error\" edge to a recovery path, or add one catch node (config codes: blank for any error, or codes/categories such as "
        "ACTION_FAILED, NOT_FOUND, validation) that nothing links into - failed steps jump to it; a throw node (config code in "
        "CAPITALS, message, category business|validation) raises an error of the flow's own; {{error.message}} and {{error.code}} "
        "describe the failure on those paths. Never use an error edge as the normal path. "
        "When the description says WHEN the flow runs (e.g. 'when an order gets "
        "approved'), also return \"trigger\": {\"trigger_type\": \"state_enter\", \"entity_type\": ..., \"trigger_state\": a state from "
        "states_by_record, \"summary\": \"...\"} - or {\"trigger_type\": \"intent\", \"trigger_intent_type\": \"INT-..\", \"timing\": "
        "\"before\"|\"after\"} - and do NOT turn that 'when' into a condition node."
    )
    return llm.chat_json(target, system, prompt)


_COMPARATORS = [
    (r"(?:is\s+)?(?:greater than or equal to|at least|>=)", "gte"),
    (r"(?:is\s+)?(?:less than or equal to|at most|<=)", "lte"),
    (r"(?:is\s+)?(?:greater than|more than|above|over|exceeds|>)", "gt"),
    (r"(?:is\s+)?(?:less than|below|under|<)", "lt"),
    (r"(?:is not|isn't|!=|does not equal)", "ne"),
    (r"(?:contains|includes)", "contains"),
    (r"(?:is|equals|=|==)", "eq"),
]


def _parse_condition(text: str) -> dict[str, Any] | None:
    body = re.sub(r"^(if|when|only if)\s+", "", text.strip(), flags=re.I)
    empty = re.match(r"(?:the\s+)?([a-z_][a-z0-9_ ]*?)\s+is\s+(empty|missing|blank|not set)$", body, re.I)
    if empty:
        return {"field": empty.group(1).strip().replace(" ", "_").lower(), "operator": "empty", "value": ""}
    for pattern, operator in _COMPARATORS:
        match = re.match(rf"(?:the\s+)?([a-z_][a-z0-9_ ]*?)\s+{pattern}\s+(.+)$", body, re.I)
        if match:
            value = match.group(2).strip().strip("'\"“”‘’").rstrip("%")
            return {"field": match.group(1).strip().replace(" ", "_").lower(), "operator": operator, "value": value}
    return None


# ---------------------------------------------------------------- triggers ("When the order gets approved ...")

_ENTITY_WORDS = {"order": "order", "orders": "order", "sales order": "order", "quote": "quote", "quotes": "quote", "quotation": "quote",
                 "lead": "lead", "leads": "lead", "opportunity": "opportunity", "opportunities": "opportunity", "deal": "opportunity",
                 "opp": "opportunity", "prospect": "party", "customer": "party", "account": "party"}
# what people say -> the state the record enters (checked against the tenant's state model)
_STATE_WORDS: dict[str, dict[str, str]] = {
    "order": {"approved": "activating", "accepted": "activating", "activated": "active", "active": "active", "live": "active",
              "cancelled": "cancelled", "canceled": "cancelled", "suspended": "suspended", "rejected": "rejected", "terminated": "terminated",
              "created": "created", "placed": "created", "raised": "created", "changed": "changed"},
    "quote": {"approved": "approved", "rejected": "rejected", "submitted": "pending_approval", "created": "draft", "cancelled": "cancelled"},
    "lead": {"approved": "qualified", "qualified": "qualified", "rejected": "rejected", "created": "pending_qualification"},
    "opportunity": {"won": "won", "closed won": "won", "lost": "lost", "closed lost": "lost", "feasible": "feasible", "quoted": "quoted",
                    "configured": "configured", "created": "draft", "qualified": "qualified"},
    "party": {"converted": "account", "becomes an account": "account"},
}
_TRIGGER = re.compile(
    r"^\s*(?:when|whenever|once|after|as soon as)\s+(?:an?\s+|the\s+|any\s+)?(?P<entity>sales\s+order|order|quote|quotation|lead|opportunity|deal|opp|"
    r"prospect|customer|account)s?\s+(?:gets\s+|is\s+|has\s+been\s+|have\s+been\s+|are\s+|becomes\s+|got\s+|was\s+)?(?P<state>closed\s+won|closed\s+lost|"
    r"[a-z]+)(?:\s+by\s+(?:the\s+)?(?P<by>[\w&/' -]+?))?(?=\s*[,;:]|\s+(?:the|then|it|its|a|an|we|assign|notify|send|set|move|create)\b|$)",
    re.I,
)


# "... after the Prospect is saved" / "after the Add prospect intent" - the flow runs after that intent
_AFTER_INTENT: list[tuple[re.Pattern, str, str, str]] = [
    (re.compile(r",?\s*\b(?:after|when|once|whenever|as\s+soon\s+as)\s+(?:the\s+|a\s+|an?\s+|any\s+)?(?:new\s+)?(?:b2b\s+|b2c\s+)?(?:prospect|customer)s?\s+"
                r"(?:is|are|gets?|has\s+been|have\s+been)\s+(?:saved|created|added|registered|captured|entered)\b"
                r"|,?\s*\bafter\s+(?:the\s+)?[\"“']?add\s+(?:a\s+)?prospect[\"”']?(?:\s+(?:intent|action|step))?", re.I),
     "INT-15", "party", "Add a prospect"),
]
# "Create a process for any B2B prospect, if ..." - the lead-in before the rule itself
# - or just the scope: "For any B2B Prospect if ..."
_LEAD_IN = re.compile(r"^\s*(?:(?:please\s+)?(?:create|build|set\s*up|make|design|define|add)\s+(?:a\s+|an\s+|the\s+)?(?:new\s+)?(?:generic\s+)?"
                      r"(?:process|flow|rule|automation|workflow)\b[^,]*?|for\s+(?:any|all|every|each)\s+(?:new\s+)?(?:b2b\s+|b2c\s+)?"
                      r"(?:prospect|customer|lead|account|part(?:y|ies))s?)\s*,?\s*\b(?=(?:if|when|only\s+if)\b)", re.I)


def detect_after_intent(prompt: str) -> tuple[dict[str, Any] | None, str]:
    """A flow that runs after an intent, said anywhere in the description, and the description without it."""
    for pattern, code, entity_type, label in _AFTER_INTENT:
        m = pattern.search(prompt)
        if m:
            rest = (prompt[: m.start()] + prompt[m.end():]).strip()
            return ({"trigger_type": "intent", "entity_type": entity_type, "trigger_intent_type": code, "timing": "after",
                     "summary": f"After “{label}” ({code})"}, rest)
    return None, prompt


def detect_trigger(prompt: str, states: dict[str, list[str]] | None = None) -> tuple[dict[str, Any] | None, str]:
    """The flow's trigger said at the start ("When the order gets approved by
    Order Control, ...") and the rest of the description."""
    m = _TRIGGER.match(prompt)
    if not m:
        return None, prompt
    entity_type = _ENTITY_WORDS.get(re.sub(r"\s+", " ", m.group("entity").lower()), m.group("entity").lower())
    word = re.sub(r"\s+", " ", m.group("state").lower())
    state = _STATE_WORDS.get(entity_type, {}).get(word) or (word if states and word in states.get(entity_type, []) else None)
    if state is None and word.endswith("ed") and states and word[:-2] in states.get(entity_type, []):
        state = word[:-2]
    if state is None:
        return None, prompt
    if states and entity_type in states and state not in states[entity_type]:
        return None, prompt
    rest = prompt[m.end():].lstrip(" ,;:").strip()
    rest = re.sub(r"^then\s+", "", rest, flags=re.I)
    by = f" by {m.group('by').strip()}" if m.group("by") else ""
    return ({"trigger_type": "state_enter", "entity_type": entity_type, "trigger_state": state,
             "summary": f"When {'an' if entity_type[0] in 'aeiou' else 'a'} {entity_type} is {word}{by} (enters '{state}')"}, rest)


# the record an Assign step hands over: the flow's own, or one that belongs to it
_TARGETS = [
    (re.compile(r"\binstall(?:ation)?\b[^.]{0,30}\bwork\s*orders?\b|\bwork\s*orders?\b[^.]{0,30}\binstall(?:ation)?\b|\bWFM1\b", re.I), "installation_work_order",
     "installation work order"),
    (re.compile(r"\bwork\s*orders?\b", re.I), "work_order", "work order"),
]


def _parse_action(clause: str, positions: list[str]) -> dict[str, Any] | None:
    text = clause.strip().rstrip(".")
    lowered = text.lower()
    m = re.search(r"\b(?:reject|block|stop|deny|refuse)\b(?:\s+(?:it|this|the \w+))?(?:\s+(?:with|saying|because)\s+(.+))?", lowered)
    if m:
        message = text[m.start(1):] if m.group(1) else "Rejected by business rule"
        return {"type": "reject", "label": "Reject", "config": {"message": message}}
    m = re.search(r"\b(?:assign\w*|re-?assign\w*|route[sd]?|transfer\w*|hand(?:ed)?\s+over|give[ns]?|allocat\w*)\b.*?\bto\s+(?:the\s+|a\s+|our\s+)?(.+)$",
                  text, re.I)
    if m:
        target = re.split(r"\s+(?:who|which|that|and|so)\b|,", m.group(1).strip().strip("'\""), maxsplit=1)[0].strip()
        position = next((p for p in positions if p.lower() == target.lower()), None) or next(
            (p for p in positions if p.lower() in target.lower() or target.lower() in p.lower()), None)
        config: dict[str, Any] = {"to_position": position} if position else (
            {"to_user": target} if "@" in target else {"to_position": target}
        )
        if re.search(r"round[- ]robin|next available|rotate", lowered):
            config["strategy"] = "round_robin"
        what = ""
        subject = text[: m.start()]
        for pattern, key, words in _TARGETS:
            if pattern.search(subject) or pattern.search(text[: m.end(0) - len(m.group(1))]):
                config["target"], what = key, f"{words} "
                break
        return {"type": "assign", "label": f"Assign {what}to {position or target}".replace("Assign to", "Assign to"), "config": config}
    m = re.search(r"\bset\s+(?:the\s+)?([a-z_][a-z0-9_ ]*?)\s+(?:to|=|as)\s+(.+)$", text, re.I)
    if m:
        field = m.group(1).strip().replace(" ", "_").lower()
        return {"type": "set_field", "label": f"Set {field}", "config": {"field": field, "value": m.group(2).strip().strip("'\"")}}
    m = re.search(r"\b(?:move|transition|change|mark|put)\b.*?\b(?:to|as|into)\s+(?:state\s+)?['\"]?([a-z_ ]+?)['\"]?(?:\s+state)?$", text, re.I)
    if m:
        state = m.group(1).strip().replace(" ", "_").lower()
        return {"type": "transition", "label": f"Move to {state}", "config": {"to_state": state}}
    # "show a message box saying ..." / "pop up a message to the Sales Manager: ..." - a box on their screen
    m = re.search(r"\b(?:show|display|pop\s*up|open|raise)\b[^.]*?\b(?:message\s*box|pop-?up|alert(?:\s*box)?|dialog(?:ue)?|message)\b"
                  r"(?:\s+(?:to|for)\s+(?:the\s+)?(?P<who>[\w@. -]+?))?(?:\s*(?:saying|that\s+says|with|:|-)\s*['\"“]?(?P<msg>.+?)['\"”]?)?$", text, re.I)
    if m:
        audience = (m.group("who") or "me").strip()
        message = (m.group("msg") or text).strip()
        return {"type": "notify", "label": "Message box", "config": {"channel": "popup", "audience": audience, "message": message, "level": "info"}}
    m = re.search(r"\b(?:notify|email|alert|inform|message|sms)\b\s*(?:the\s+)?([a-z@._ -]*?)(?:\s+(?:that|saying|about|with)\s+(.+))?$", text, re.I)
    if m:
        audience = (m.group(1) or "owner").strip() or "owner"
        channel = "popup" if re.search(r"\b(?:on\s+(?:the\s+|their\s+|his\s+|her\s+)?screen|pop-?up|message\s*box)\b", lowered) else "sms" if "sms" in lowered else "email"
        audience = re.sub(r"\s+on\s+(?:the\s+|their\s+|his\s+|her\s+)?screen$", "", audience, flags=re.I).strip() or "owner"
        message = (m.group(2) or text).strip()
        return {"type": "notify", "label": f"Notify {audience}", "config": {"channel": channel, "audience": audience, "message": message}}
    # an action of the CRM itself ("convert the prospect to an account", "create a quote") - a Run an action step
    from app.intent_engine.intent_registry import BUILTIN
    from app.orchestration.process_builder import _business_action

    found = _business_action(text)
    if found:
        code, entities = found
        return {"type": "action", "label": BUILTIN[code].label if code in BUILTIN else code, "config": {"intent": code, "entities": entities}}
    return None


def _order_record_draft(prompt: str) -> dict[str, Any] | None:
    # A deliberately narrow recipe: never discard extra conditions or actions.
    if not re.fullmatch(
        r"\s*(?:create|build|make|generate)\s+(?:a\s+)?flow\s+to\s+(?:enter|create|insert|add)\s+"
        r"(?:an?\s+)?order(?:\s+record)?\s+(?:and|with)\s+(?:an?\s+)?(?:order\s+items?|line\s+items?)"
        r"(?:\s+records?)?\s*[.!]?\s*", prompt, re.I
    ):
        return None
    nodes = [{"id": "start", "type": "start", "label": "Start", "config": {}}]
    def record(node_id, label, entity, operation, **config):
        nodes.append({"id": node_id, "type": "record", "label": label,
                      "config": {"entity": entity, "operation": operation, **config}})
    record("customer", "Read customer", "parties", "read", match={"id": "{{entities.party_id}}"})
    record("product", "Read product", "product_offerings", "read", match={"id": "{{entities.product_offering_id}}"})
    record("billing", "Find or create billing account", "billing_accounts", "upsert", match={"party_id": "{{steps.customer.output.id}}"})
    record("service", "Find or create service account", "service_accounts", "upsert",
           match={"billing_account_id": "{{steps.billing.output.id}}", "name": "Primary service"})
    record("order", "Insert order", "orders", "insert", values={"party_id": "{{steps.customer.output.id}}",
           "service_account_id": "{{steps.service.output.id}}", "monthly_price": "{{entities.monthly_price}}"})
    record("line", "Insert order item", "order_items", "insert", values={"order_id": "{{steps.order.output.id}}",
           "product_offering_id": "{{steps.product.output.id}}", "quantity": "{{entities.quantity}}"})
    nodes.append({"id": "end", "type": "end", "label": "End", "config": {}})
    return {"nodes": nodes, "edges": [{"id": f"e{i}", "source": a["id"], "target": b["id"], "label": ""}
            for i, (a, b) in enumerate(zip(nodes, nodes[1:]))],
            "notes": "Drafted order and one line item using record operations, without a quote or predefined intent. "
                     "Supply numeric party_id and product_offering_id, quantity and total monthly_price in the trigger inputs or simulation. "
                     "Billing and service accounts are upserted; the service account name defaults to Primary service. "
                     "Review these mappings before activation. Each execution inserts a new order; simulation rolls back all changes."}


def heuristic_generate(prompt: str, positions: list[str]) -> dict[str, Any]:
    recipe = _order_record_draft(prompt)
    if recipe:
        return recipe
    nodes: list[dict[str, Any]] = [{"id": "n1", "type": "start", "label": "Start", "config": {}}]
    edges: list[dict[str, Any]] = []
    counter = 1

    def add(node: dict[str, Any]) -> str:
        nonlocal counter
        counter += 1
        node["id"] = f"n{counter}"
        nodes.append(node)
        return node["id"]

    def link(source: str, target: str, label: str = "") -> None:
        edges.append({"id": f"e{len(edges) + 1}", "source": source, "target": target, "label": label})

    clauses = [c for c in re.split(r"(?:\.\s+|;\s*|\n+|,?\s+then\s+|,\s*and\s+)", prompt.strip()) if c and c.strip()]
    tail = "n1"
    tail_label = ""
    open_condition: str | None = None  # condition whose false branch is still unconnected
    false_tails: list[str] = []
    unparsed: list[str] = []

    for raw in clauses:
        clause = raw.strip().rstrip(".")
        lowered = clause.lower()
        else_match = re.match(r"^(?:otherwise|else|if not)[,:]?\s*(.*)$", clause, re.I)
        if else_match and open_condition:
            action = _parse_action(else_match.group(1), positions) if else_match.group(1) else None
            if action:
                node_id = add(action)
                link(open_condition, node_id, "false")
                if action["type"] != "reject":
                    false_tails.append(node_id)
            open_condition = None
            continue
        if lowered.startswith(("if ", "when ", "only if ")):
            parts = re.split(r",\s*", clause, maxsplit=1)
            body, remainder = parts[0], (parts[1] if len(parts) > 1 else "")
            condition = _parse_condition(body)
            if condition is None:
                unparsed.append(clause)
                continue
            if open_condition:
                false_tails.append(open_condition)
            node_id = add({"type": "condition", "label": f"{condition['field']} {condition['operator']} {condition['value']}".strip(), "config": condition})
            link(tail, node_id, tail_label)
            tail, tail_label, open_condition = node_id, "true", node_id
            action = _parse_action(remainder, positions) if remainder.strip() else None
            if action:
                action_id = add(action)
                link(tail, action_id, tail_label)
                tail, tail_label = action_id, ""
                if action["type"] == "reject":
                    tail, tail_label = node_id, "false"
                    open_condition = None
            continue
        # "... assigned to the Field Service Manager who will then dispatch it to the field engineers"
        follow = re.match(r"^(.*?)\s+who\s+(?:will|shall|should|must|can|is\s+to|has\s+to|then)\s+(?:then\s+|the\s+|in\s+turn\s+)?(.+)$", clause, re.I)
        action = _parse_action(follow.group(1) if follow else clause, positions)
        if action is None:
            unparsed.append(clause)
            continue
        node_id = add(action)
        link(tail, node_id, tail_label)
        if action["type"] == "reject":
            continue
        tail, tail_label = node_id, ""
        if follow and action["type"] == "assign":
            who = action["config"].get("to_position") or action["config"].get("to_user")
            what = {"installation_work_order": "the installation work order", "work_order": "the work order"}.get(
                action["config"].get("target", ""), "this record")
            todo = follow.group(2).strip().rstrip(".")
            notify_id = add({"type": "notify", "label": f"Tell {who}", "config": {
                "channel": "email", "audience": who,
                "message": f"Please {todo} - {what} for #{{{{record.id}}}} has been assigned to you."}})
            link(tail, notify_id)
            tail = notify_id

    end_id = add({"type": "end", "label": "End", "config": {}})
    if not any(n["id"] == tail and n["type"] == "reject" for n in nodes):
        link(tail, end_id, tail_label)
    if open_condition and open_condition != tail:
        link(open_condition, end_id, "false")
    for false_tail in false_tails:
        node = next(n for n in nodes if n["id"] == false_tail)
        label = "false" if node["type"] == "condition" else ""
        if not any(e["source"] == false_tail and (e.get("label") or "") == label for e in edges):
            link(false_tail, end_id, label)

    notes = "Drafted by the built-in rule parser (no AI key configured)."
    if unparsed:
        notes += f" Could not interpret: {' | '.join(unparsed)} - add those steps by hand."
    return {"nodes": nodes, "edges": edges, "notes": notes}


def layout(graph: dict[str, Any]) -> dict[str, Any]:
    """Assigns canvas positions (top-down by BFS depth) to any node
    without one, so an AI draft opens readable rather than stacked."""
    nodes = graph.get("nodes", [])
    if all("x" in n and "y" in n for n in nodes):
        return graph
    children: dict[str, list[str]] = {}
    for e in graph.get("edges", []):
        children.setdefault(e["source"], []).append(e["target"])
    start = next((n["id"] for n in nodes if n.get("type") == "start"), nodes[0]["id"] if nodes else None)
    depth: dict[str, int] = {}
    queue = [start] if start else []
    if start:
        depth[start] = 0
    while queue:
        current = queue.pop(0)
        for child in children.get(current, []):
            if child not in depth:
                depth[child] = depth[current] + 1
                queue.append(child)
    rows: dict[int, int] = {}
    for n in nodes:
        d = depth.get(n["id"], max(depth.values(), default=0) + 1)
        column = rows.get(d, 0)
        rows[d] = column + 1
        n["x"] = 80 + column * 240
        n["y"] = 40 + d * 120
    return graph


def _states(db: Session, tenant_id: int) -> dict[str, list[str]]:
    from app.crm_core import lifecycle_service

    return {et: list(lifecycle_service.resolve_state_model(db, tenant_id, et).states) for et in lifecycle_service.ENTITY_MODELS}


def _clean_trigger(raw: Any, states: dict[str, list[str]]) -> dict[str, Any] | None:
    """Only a trigger the Studio can save: a known record type and state, or a known intent."""
    if not isinstance(raw, dict):
        return None
    entity_type = raw.get("entity_type")
    if raw.get("trigger_type") == "state_enter" and entity_type in states and raw.get("trigger_state") in states[entity_type]:
        return {"trigger_type": "state_enter", "entity_type": entity_type, "trigger_state": raw["trigger_state"],
                "summary": raw.get("summary") or f"When a {entity_type} enters '{raw['trigger_state']}'"}
    if raw.get("trigger_type") == "intent" and re.match(r"^INT-\d+$", str(raw.get("trigger_intent_type") or "")):
        return {"trigger_type": "intent", "entity_type": entity_type if entity_type in states else None,
                "trigger_intent_type": raw["trigger_intent_type"], "timing": raw.get("timing") if raw.get("timing") in ("before", "after") else "after",
                "summary": raw.get("summary") or f"{raw.get('timing', 'after')} {raw['trigger_intent_type']}"}
    return None


def generate(db: Session, tenant_id: int, prompt: str, entity_type: str | None, catalog: dict[str, Any]) -> dict[str, Any]:
    """The draft graph - and, when the description says when it should run
    ("When the order gets approved ..."), the trigger to save it with."""
    from app.crm_core import llm

    info = _context_for_prompt(catalog)
    states = _states(db, tenant_id)
    from app.orchestration import process_journey
    segment = process_journey.scope({}, prompt)
    scope = {"segment": segment} if segment else {}
    trigger, rest = detect_trigger(prompt, states)
    if trigger is None:
        trigger, rest = detect_after_intent(prompt)
    rest = _LEAD_IN.sub("", rest)
    from app.orchestration import flow_records
    if _RECORD_WORDS.search(prompt):
        info["record_entities"] = _tables_for_prompt(flow_records.catalog(db, tenant_id), prompt, entity_type or (trigger or {}).get("entity_type"))
    target = llm.resolve(db, tenant_id)
    rejected = ""
    if target is not None:
        try:
            draft = _llm_generate(target, prompt, entity_type or (trigger or {}).get("entity_type"), {**info, "states_by_record": states})
            graph = {**scope, "nodes": draft.get("nodes") or [], "edges": draft.get("edges") or []}
            for i, e in enumerate(graph["edges"], start=1):
                e.setdefault("id", f"e{i}")
                e.setdefault("label", "")
            errors = flow_engine.validate_graph(graph)
            if not errors:
                return {"graph": layout(graph), "source": "llm", "notes": draft.get("notes", ""),
                        "trigger": _clean_trigger(draft.get("trigger"), states) or trigger}
            rejected = f"The {target.label} draft did not hold together ({'; '.join(map(str, errors[:2]))}) - the built-in rule parser drafted it instead."
        except Exception as exc:  # noqa: BLE001 - any AI failure falls back to the rule parser
            reason = str(exc) if isinstance(exc, llm.LlmUnavailable) else f"{target.label} failed ({type(exc).__name__})"
            rejected = f"AI not used - {reason}. The built-in rule parser drafted it instead."
    draft = heuristic_generate(rest, info.get("positions", []))  # (the description without its trigger and lead-in)
    notes = draft["notes"]
    if rejected:
        notes = notes.replace("Drafted by the built-in rule parser (no AI key configured).", rejected)
    if trigger:
        notes = f"Trigger: {trigger['summary']}. {notes}"
    return {"graph": layout({**scope, "nodes": draft["nodes"], "edges": draft["edges"]}), "source": "heuristic", "notes": notes, "trigger": trigger}

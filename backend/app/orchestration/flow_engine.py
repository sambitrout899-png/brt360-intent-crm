"""Flow engine (Phase 7, AI Flow Studio) - executes a FlowDefinition's
graph. See app/models/flows.py for trigger semantics.

Graph shape (what the Flow Studio canvas saves):
    {"nodes": [{"id", "type", "label", "x", "y", "config": {...}}],
     "edges": [{"id", "source", "target", "label"}]}

A whole flow run is ONE SagaStep: branching depends on runtime values,
so the walk happens inside the step and its per-node trace is the step's
detail. Because it is a SagaStep inside the triggering intent's saga, a
flow's writes commit or roll back together with the intent - a "before"
flow's reject node aborts the intent cleanly, and a failing "after" flow
never leaves the intent half-applied.

Like llm_planner.py, nothing here runs model-written code: nodes are a
closed set of pre-written operations, and the only AI step (ai_decision)
merely picks a true/false branch, with an admin-chosen default whenever
no AI is available.
"""

from __future__ import annotations

import json
import re
from typing import Any

from sqlalchemy.orm import Session

from app.crm_core import lifecycle_service, record_service
from app.data_intelligence import event_bus
from app.integration import notification_adapter
from app.models.flows import FlowDefinition
from app.orchestration import flow_errors
from app.orchestration.orchestrator import SagaStep

NODE_TYPES: dict[str, dict[str, Any]] = {
    # A step that can fail has an "error" output: link it and a failure of that
    # step goes there (its own writes undone, {{error.*}} describing it) instead
    # of failing the flow. "retries" re-runs a step whose error is retryable.
    "record": {"label": "Record operation", "outputs": ["", "error"], "config": ["entity", "operation", "match", "values", "retries"]},
    "map": {"label": "Map outputs", "outputs": ["", "error"], "config": ["values"]},
    "start": {"label": "Start", "outputs": [""], "config": []},
    "condition": {"label": "Condition", "outputs": ["true", "false", "error"], "config": ["field", "operator", "value"]},
    "ai_decision": {"label": "AI Decision", "outputs": ["true", "false", "error"], "config": ["question", "default"]},
    "set_field": {"label": "Set Field", "outputs": ["", "error"], "config": ["field", "value"]},
    "transition": {"label": "Change State", "outputs": ["", "error"], "config": ["to_state"]},
    "assign": {"label": "Assign", "outputs": ["", "error"], "config": ["to_user", "to_position", "to_department", "strategy", "target"]},
    # channel "popup" shows a message box on the screen (title, level: info | success | warning | urgent)
    "notify": {"label": "Notify", "outputs": ["", "error"], "config": ["channel", "audience", "message", "title", "level", "retries"]},
    "reject": {"label": "Reject", "outputs": [], "config": ["message"]},
    "end": {"label": "End", "outputs": [], "config": []},
    # Runs one of the tenant's actions (an intent: Send to Presales, Create a quote,
    # Approve a lead ...) on the flow's record or a record linked to it.
    "action": {"label": "Run an action", "outputs": ["", "error"], "config": ["intent", "entities", "on_error", "retries"]},
    # Error handling for the whole flow: a step that fails without its own "error"
    # output jumps here (the first Catch errors whose codes match). Nothing links into it.
    "catch": {"label": "Catch errors", "outputs": [""], "config": ["codes"]},
    # Throws an error of the flow's own - caught by a Catch errors step, else it fails the flow.
    "throw": {"label": "Raise error", "outputs": [], "config": ["code", "message", "category"]},
}
# steps that write - each runs under a savepoint, so a failure undoes only that step
_WRITES = {"record", "set_field", "transition", "assign", "notify", "action"}
MAX_RETRIES = 3

# How a record finds the records an action needs: (flow record type, id wanted) -> lookup.
# Parents come from the record's own column (opportunity.lead_id); children are the latest one.
_CHILDREN: dict[tuple[str, str], tuple[str, str]] = {
    ("lead", "opportunity_id"): ("opportunity", "lead_id"),
    ("opportunity", "quote_id"): ("quote", "opportunity_id"),
    ("quote", "order_id"): ("order", "quote_id"),
}
_PARENTS: dict[tuple[str, str], str] = {
    ("opportunity", "party_id"): "lead.party_id",
    ("quote", "lead_id"): "opportunity.lead_id",
    ("quote", "party_id"): "opportunity.lead.party_id",
    ("order", "opportunity_id"): "quote.opportunity_id",
}

OPERATORS = ("eq", "ne", "gt", "gte", "lt", "lte", "contains", "in", "empty", "not_empty")

MAX_NODE_VISITS = 50


class FlowRejected(ValueError):
    """Raised by a reject node - aborts the surrounding saga with the
    node's message as the visible failure reason."""


def load_graph(flow: FlowDefinition) -> dict[str, Any]:
    try:
        graph = json.loads(flow.graph_json or "{}")
    except json.JSONDecodeError:
        graph = {}
    graph.setdefault("nodes", [])
    graph.setdefault("edges", [])
    return graph


def validate_graph(graph: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    nodes = graph.get("nodes") or []
    edges = graph.get("edges") or []
    ids = [n.get("id") for n in nodes]
    if len(set(ids)) != len(ids):
        errors.append("Node ids must be unique")
    starts = [n for n in nodes if n.get("type") == "start"]
    if len(starts) != 1:
        errors.append(f"A flow needs exactly one Start node (found {len(starts)})")
    by_id = {n.get("id"): n for n in nodes}
    for n in nodes:
        node_type = n.get("type")
        if node_type not in NODE_TYPES:
            errors.append(f"Node {n.get('id')}: unknown type '{node_type}'")
            continue
        config = n.get("config") or {}
        if node_type == "record":
            from app.orchestration.flow_records import available
            if config.get("entity") not in available():
                errors.append(f"Record operation '{n.get('id')}' needs an application entity")
            if config.get("operation") not in ("read", "insert", "update", "upsert", "delete"):
                errors.append(f"Record operation '{n.get('id')}' needs an operation")
        if node_type in ("record", "map"):
            for key in (["values", "match"] if node_type == "record" else ["values"]):
                if key in config and not isinstance(config[key], dict):
                    errors.append(f"{n.get('id')}: {key} must be a JSON object")
        if node_type == "condition":
            if not config.get("field"):
                errors.append(f"Condition '{n.get('label') or n.get('id')}' has no field")
            if config.get("operator", "eq") not in OPERATORS:
                errors.append(f"Condition '{n.get('label') or n.get('id')}' has unknown operator '{config.get('operator')}'")
        if node_type == "set_field" and not config.get("field"):
            errors.append(f"Set Field '{n.get('label') or n.get('id')}' has no field")
        if node_type == "transition" and not config.get("to_state"):
            errors.append(f"Change State '{n.get('label') or n.get('id')}' has no target state")
        if node_type == "assign" and not (config.get("to_user") or config.get("to_position") or config.get("to_department")):
            errors.append(f"Assign '{n.get('label') or n.get('id')}' needs a user, a position or a department")
        if node_type == "ai_decision" and not config.get("question"):
            errors.append(f"AI Decision '{n.get('label') or n.get('id')}' has no question")
        if node_type == "action" and not re.match(r"^(?:INT|FLOW)-\d+$", str(config.get("intent") or "")):
            errors.append(f"Run an action '{n.get('label') or n.get('id')}' has no action (e.g. INT-38)")
        if node_type == "action" and isinstance(config.get("entities"), str):
            try:
                if not isinstance(json.loads(config["entities"]), dict):
                    raise ValueError()
            except (ValueError, TypeError):
                errors.append(f"{n.get('id')}: action values must be a valid JSON object")
        name = n.get("label") or n.get("id")
        if "retries" in NODE_TYPES[node_type]["config"] and config.get("retries") not in (None, ""):
            if not re.fullmatch(r"[0-9]", str(config["retries"]).strip()) or int(str(config["retries"]).strip()) > MAX_RETRIES:
                errors.append(f"'{name}': retries must be a whole number from 0 to {MAX_RETRIES}")
        if node_type == "throw":
            if not flow_errors.CUSTOM_CODE.match(str(config.get("code") or "")):
                errors.append(f"Raise error '{name}' needs a code in CAPITALS, e.g. CREDIT_CHECK_FAILED")
            if not str(config.get("message") or "").strip():
                errors.append(f"Raise error '{name}' needs a message")
            if config.get("category") not in (None, "", "business", "validation"):
                errors.append(f"Raise error '{name}': category must be business or validation")
        if node_type == "catch":
            wanted = [c.strip() for c in str(config.get("codes") or "").split(",") if c.strip()] if not isinstance(config.get("codes"), list) \
                else [str(c).strip() for c in config["codes"] if str(c).strip()]
            known = set(flow_errors.CODES) | set(flow_errors.CATEGORIES)
            unknown = [c for c in wanted if c not in known and c.lower() not in known and not flow_errors.CUSTOM_CODE.match(c)]
            if unknown:
                errors.append(f"Catch errors '{name}': unknown error code(s) {', '.join(unknown)}")
            if any(e.get("target") == n.get("id") for e in edges):
                errors.append(f"Catch errors '{name}' is where failures jump to - nothing can link into it")
    for e in edges:
        if e.get("source") not in by_id or e.get("target") not in by_id:
            errors.append(f"Edge {e.get('id')} references a missing node")
            continue
        source_type = by_id[e["source"]].get("type")
        outputs = NODE_TYPES.get(source_type, {}).get("outputs", [])
        if not outputs:
            errors.append(f"'{by_id[e['source']].get('label') or source_type}' is a final node and cannot have outgoing edges")
        elif outputs != [""] and (e.get("label") or "") not in outputs:
            errors.append(f"Edge out of '{by_id[e['source']].get('label') or source_type}' must be labeled {' or '.join(outputs)}")
    return errors


# ---------- value resolution ----------

_TEMPLATE = re.compile(r"\{\{\s*([a-zA-Z0-9_.]+)\s*\}\}")


def _lookup(ctx: dict[str, Any], path: str, default=None) -> Any:
    path = path.strip()
    # "error": the failure being handled - set on an error path or after a Catch errors step
    if path.split(".")[0] in ("steps", "entities", "result", "record", "customer", "trigger", "flow", "error"):
        value = ctx
        for part in path.split("."):
            if isinstance(value, dict):
                value = value.get(part, default)
            elif isinstance(value, list) and part.isdigit() and int(part) < len(value):
                value = value[int(part)]
            else:
                return default
        return value
    if "." in path:
        scope, _, key = path.partition(".")
        if scope in ("record", "entities", "result"):
            return (ctx.get(scope) or {}).get(key)
    for scope in ("record", "entities", "result"):
        scoped = ctx.get(scope) or {}
        if path in scoped:
            return scoped[path]
    return None


def render(template: Any, ctx: dict[str, Any]) -> Any:
    if isinstance(template, dict):
        return {k: render(v, ctx) for k, v in template.items()}
    if isinstance(template, list):
        return [render(v, ctx) for v in template]
    if not isinstance(template, str):
        return template
    whole = _TEMPLATE.fullmatch(template.strip())
    if whole:
        if whole.group(1).startswith(("steps.", "trigger.", "flow.")):
            missing = object()
            value = _lookup(ctx, whole.group(1), missing)
            if value is missing:
                raise ValueError(f"Reference '{whole.group(1)}' is unavailable; check the path, trigger timing and executed branch")
            return value
        return _lookup(ctx, whole.group(1))
    return _TEMPLATE.sub(lambda m: "" if (v := _lookup(ctx, m.group(1))) is None else str(v), template)


def _as_number(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def evaluate_condition(config: dict[str, Any], ctx: dict[str, Any]) -> tuple[bool, Any]:
    actual = _lookup(ctx, str(config.get("field", "")))
    operator = config.get("operator", "eq")
    expected = render(config.get("value"), ctx)
    if operator == "empty":
        return actual in (None, "", []), actual
    if operator == "not_empty":
        return actual not in (None, "", []), actual
    if operator in ("gt", "gte", "lt", "lte"):
        a, b = _as_number(actual), _as_number(expected)
        if a is None or b is None:
            return False, actual
        return {"gt": a > b, "gte": a >= b, "lt": a < b, "lte": a <= b}[operator], actual
    if operator == "contains":
        return str(expected or "").lower() in str(actual or "").lower(), actual
    if operator == "in":
        options = [o.strip().lower() for o in str(expected or "").split(",") if o.strip()]
        return str(actual).lower() in options, actual
    a, b = _as_number(actual), _as_number(expected)
    equal = (a == b) if a is not None and b is not None else str(actual).strip().lower() == str(expected).strip().lower()
    if isinstance(actual, bool):
        equal = str(actual).lower() == str(expected).strip().lower()
    return (equal if operator == "eq" else not equal), actual


def _ai_decide(db: Session, tenant_id: int, question: str, ctx: dict[str, Any], default: bool) -> tuple[bool, str]:
    from app.crm_core import llm

    target = llm.resolve(db, tenant_id)
    if target is None:
        return default, "no AI model configured - used the node's default branch"
    try:
        parsed = llm.chat_json(
            target,
            "You make one yes/no business decision inside a CRM workflow. Answer strictly as JSON "
            '{"answer": true|false, "reason": "one sentence"} using only the record data given.',
            f"Question: {question}\nRecord: {json.dumps(ctx.get('record') or {}, default=str)}\n"
            f"Intent entities: {json.dumps(ctx.get('entities') or {}, default=str)}",
        )
        answer = parsed.get("answer")
        if isinstance(answer, str):
            answer = answer.strip().lower() in ("true", "yes", "1")
        return bool(answer), str(parsed.get("reason", ""))
    except Exception as exc:  # noqa: BLE001 - any AI failure falls back to the admin-chosen default
        return default, f"AI unavailable ({type(exc).__name__}) - used the node's default branch"


# ---------- run an action ----------


def _linked_id(db: Session, tenant_id: int, entity_type: str | None, record: Any, key: str) -> Any:
    """The id an action needs, from the flow's record: its own id, a column
    it carries (lead.party_id), a parent (opportunity -> lead -> party) or its
    latest child (lead -> opportunity)."""
    if record is None or not entity_type:
        return None
    if key == f"{entity_type}_id":
        return record.id
    if key == "address":  # a site address: the customer's own address, from their page
        party = {"party": record, "lead": getattr(record, "party", None)}.get(entity_type)
        if party is None:
            chain = {"opportunity": "lead.party", "quote": "opportunity.lead.party", "order": "quote.opportunity.lead.party"}.get(entity_type, "")
            party = record
            for attr in chain.split(".") if chain else ["_none"]:
                party = getattr(party, attr, None) if party is not None else None
        if party is None:
            return None
        from app.crm_core import page_service

        values = page_service.record_values(db, tenant_id, "party", party)
        line = ", ".join(str(values[k]).strip() for k in ("address_line1", "address_line2") if values.get(k) not in (None, ""))
        return line or None
    if hasattr(record, key) and not callable(getattr(record, key)):
        return getattr(record, key)
    if (entity_type, key) in _PARENTS:
        value = record
        for attr in _PARENTS[(entity_type, key)].split("."):
            value = getattr(value, attr, None) if value is not None else None
        return value
    if (entity_type, key) in _CHILDREN:
        child_type, fk = _CHILDREN[(entity_type, key)]
        model = lifecycle_service.ENTITY_MODELS[child_type]
        child = db.query(model).filter(getattr(model, fk) == record.id).order_by(model.id.desc()).first()
        return child.id if child is not None else None
    return None


def run_action(db: Session, tenant_id: int, execution_id: int, flow: FlowDefinition, entity_type: str | None, record: Any,
               config: dict[str, Any], ctx: dict[str, Any]) -> dict[str, Any]:
    """Runs an intent's own steps inside the flow (same transaction, under a
    savepoint). The flow's record fills what the action needs; ids it makes
    are added to the flow's results for later nodes ({{result.quote_id}})."""
    from app import capabilities
    from app.intent_engine import intent_registry

    code = str(config.get("intent") or "")
    from app.licensing import require_module, intent_module
    require_module(db, tenant_id, intent_module(code))
    builder = capabilities.step_builder(code)
    if builder is None:
        raise ValueError(f"Flow '{flow.name}': '{code}' is not an action that can run in a flow")
    given = config.get("entities") or {}
    if isinstance(given, str):  # typed as text in the Studio
        try:
            given = json.loads(given)
        except json.JSONDecodeError as exc:
            raise ValueError("Action values must be a valid JSON object") from exc
    entities = {k: render(v, ctx) for k, v in (given if isinstance(given, dict) else {}).items()}
    wanted = set(intent_registry.requirements(db, tenant_id, code)[0]) | {f["key"] for f in (intent_registry.entry(db, tenant_id, code) or {}).get("fields", [])}
    filled = []
    for key in sorted(wanted):
        if entities.get(key) in (None, "", [], {}):
            value = _linked_id(db, tenant_id, entity_type, record, key)
            if value not in (None, ""):
                entities[key] = value
                filled.append(key)
    if "entity_type" in wanted and not entities.get("entity_type") and entity_type and record is not None:
        entities.update(entity_type=entity_type, entity_id=record.id)
    required = intent_registry.requirements(db, tenant_id, code)[0]
    # INT-07 also places a consumer order directly, without a quote.
    # Validate that branch's inputs before invoking the same action builder.
    if code == "INT-07" and not entities.get("quote_id"):
        required = [f for f in required if f != "quote_id"] + ["party_id", "offering_name"]
    missing = [f for f in required if " or " not in f and entities.get(f) in (None, "", [], {})]
    if missing:
        raise ValueError(f"needs {', '.join(missing)}, which this {entity_type or 'record'} doesn't give")
    out: dict[str, Any] = {}
    savepoint = db.begin_nested()
    try:
        for step in builder(db, tenant_id, execution_id, {**entities, "_actor_role": "admin", "_actor_user_id": None}):
            out.update(step.action() or {})
        db.flush()
        savepoint.commit()
    except Exception:
        savepoint.rollback()
        raise
    ids = {k: v for k, v in out.items() if k.endswith("_id") and isinstance(v, int) and not isinstance(v, bool)}
    return {"intent": code, "entities": {k: v for k, v in entities.items() if not str(k).startswith("_")}, "filled": filled,
            "result_ids": ids, "output": out, "touched": lifecycle_service.records_in_result(out)}


# ---------- target record ----------


def resolve_target(db: Session, tenant_id: int, flow: FlowDefinition, ctx: dict[str, Any]):
    entity_type = flow.entity_type
    if not entity_type:
        return None, None
    if ctx.get("entity_type") == entity_type and ctx.get("entity_id"):
        return entity_type, lifecycle_service.get_record(db, tenant_id, entity_type, ctx["entity_id"])
    for scope in ("result", "entities"):
        values = ctx.get(scope) or {}
        candidates = [f"{entity_type}_id"]
        if entity_type == "quote":
            candidates.insert(0, "new_quote_id")
        if lifecycle_service.normalize_entity_type(values.get("entity_type")) == entity_type:
            candidates.append("entity_id")
        for key in candidates:
            if values.get(key) not in (None, ""):
                return entity_type, lifecycle_service.get_record(db, tenant_id, entity_type, values[key])
    return entity_type, None


def customer_of(db: Session, entity_type: str | None, record: Any) -> Any:
    """The party a sales record belongs to: itself, or up the road order/quote -> opportunity -> lead -> party."""
    from app.models.crm import Lead, Opportunity, Party, Quote

    if record is None:
        return None
    if entity_type == "party":
        return record
    if getattr(record, "party_id", None):
        return db.get(Party, record.party_id)
    if getattr(record, "quote_id", None):
        record = db.get(Quote, record.quote_id)
    if record is not None and getattr(record, "opportunity_id", None):
        record = db.get(Opportunity, record.opportunity_id)
    if record is not None and getattr(record, "lead_id", None):
        lead = db.get(Lead, record.lead_id)
        return db.get(Party, lead.party_id) if lead is not None else None
    return None


# ---------- execution ----------


def run_flow(db: Session, tenant_id: int, execution_id: int, flow: FlowDefinition, ctx: dict[str, Any]) -> dict[str, Any]:
    from app.licensing import require_module
    if flow.entity_type in ("party", "lead", "opportunity", "quote", "order"):
        require_module(db, tenant_id, "telecom_crm")
    graph = load_graph(flow)
    errors = validate_graph(graph)
    if errors:
        raise ValueError(f"Flow '{flow.name}' is invalid: {'; '.join(errors)}")
    nodes = {n["id"]: n for n in graph["nodes"]}
    edges: dict[str, list[dict[str, Any]]] = {}
    for e in graph["edges"]:
        edges.setdefault(e["source"], []).append(e)

    entity_type, record = resolve_target(db, tenant_id, flow, ctx)
    from app.orchestration import process_journey
    required_segment = process_journey.scope(graph, flow.name)
    if required_segment and process_journey.record_segment(record) != required_segment:
        if flow.trigger_type in ("intent", "state_enter"):
            return {"flow_id": flow.id, "flow_name": flow.name, "skipped": True,
                    "reason": f"Applies only to {required_segment} records"}
        raise FlowRejected(f"Flow '{flow.name}' applies only to {required_segment} records. Check the selected record's segment.")
    ctx = dict(ctx)
    ctx.setdefault("trigger", {"input": dict(ctx.get("entities") or {}), "output": dict(ctx.get("result") or {})})
    from app.orchestration import flow_parameters
    ctx["flow"] = {"inputs": flow_parameters.contract(db, tenant_id, ctx["trigger"].get("input") or {}, ctx["trigger"].get("output") or {})}
    ctx["steps"] = {}
    # A validation flow (Page Designer) runs before the record exists - it
    # checks the values about to be saved, handed in as the record.
    incoming = dict(ctx.get("record") or {}) if flow.trigger_type == "validation" else {}

    def refresh_record() -> None:
        ctx["record"] = record_service.snapshot(db, tenant_id, entity_type, record) if record is not None else incoming
        # the customer behind the record - "only when the prospect's Company type is Government" on a quote's flow
        party = customer_of(db, entity_type, record)
        ctx["customer"] = record_service.snapshot(db, tenant_id, "party", party) if party is not None else {}

    refresh_record()
    if flow.trigger_type in ("intent", "state_enter"):
        from app.orchestration import conflicts

        # the admin's rule for flows that share this trigger: a condition, or "only the first that applies"
        aside = conflicts.gate(db, tenant_id, flow, ctx)
        if aside:
            return {"flow_id": flow.id, "flow_name": flow.name, "entity_type": entity_type, "entity_id": record.id if record is not None else None,
                    "trace": [{"node_id": "", "type": "skipped", "label": aside, "skipped": aside}], "skipped": aside}
    source = f"flow:{flow.id}"
    trace: list[dict[str, Any]] = []
    touched: list[tuple[str, int]] = []  # records an action node changed - their own state flows run next
    caught: list[dict[str, Any]] = []  # errors this run caught and carried on from
    catch_nodes = [n for n in graph["nodes"] if n["type"] == "catch"]
    current = next(n for n in graph["nodes"] if n["type"] == "start")
    visits = 0

    def need_record(node_label: str):
        if record is None:
            raise ValueError(f"needs a {entity_type or 'record'}, but none was resolved from the intent")
        return record

    def summary(outcome: str) -> dict[str, Any]:
        return {
            "flow_id": flow.id,
            "flow_name": flow.name,
            "entity_type": entity_type,
            "entity_id": record.id if record is not None else None,
            "trace": trace,
            "steps": ctx["steps"],
            "trigger": ctx["trigger"],
            "inputs": ctx["flow"]["inputs"],
            # completed | handled (an error was caught and the flow carried on) | failed | rejected
            "outcome": outcome,
            **({"errors": caught} if caught else {}),
            **({"touched": touched} if touched else {}),
        }

    def execute(current: dict[str, Any], entry: dict[str, Any]) -> str:
        """Runs one step; returns the output branch to follow. Raises on failure."""
        node_type = current["type"]
        config = current.get("config") or {}
        label = entry["label"]
        branch = ""
        entry["input"] = render(config, ctx)

        if node_type == "record":
            from app.orchestration import flow_records
            entry["output"] = flow_records.execute(db, tenant_id, entry["input"])
            event_bus.publish(db, execution_id, "flow.record_operation", {
                "flow_id": flow.id, "node_id": current["id"], "entity": config.get("entity"),
                "operation": config.get("operation"), "record_id": entry["output"]["id"]})
        elif node_type == "map":
            entry["output"] = render(config.get("values") or {}, ctx)
        elif node_type == "condition":
            outcome, actual = evaluate_condition(config, ctx)
            branch = "true" if outcome else "false"
            entry.update({"field": config.get("field"), "actual": actual, "outcome": outcome})
        elif node_type == "ai_decision":
            default = str(config.get("default", "false")).lower() == "true"
            outcome, reason = _ai_decide(db, tenant_id, render(config.get("question", ""), ctx), ctx, default)
            branch = "true" if outcome else "false"
            entry.update({"outcome": outcome, "reason": reason})
        elif node_type == "set_field":
            target = need_record(label)
            value = render(config.get("value"), ctx)
            # The Studio stores full paths ("record.review_tier"); the record
            # itself only knows the bare field name.
            field_name = str(config["field"]).removeprefix("record.")
            applied = record_service.update_fields(db, tenant_id, entity_type, target, {field_name: value}, enforce_editable=False)
            entry["applied"] = applied
            refresh_record()
        elif node_type == "transition":
            target = need_record(label)
            entry.update(lifecycle_service.set_state(db, tenant_id, entity_type, target, str(render(config["to_state"], ctx)), source))
            refresh_record()
        elif node_type == "assign" and config.get("target") in ("installation_work_order", "work_order"):
            # hand over a record that belongs to this one: the order's open (installation) work order
            from app.models.assurance import WorkOrder

            order = need_record(label)
            query = db.query(WorkOrder).filter(WorkOrder.tenant_id == tenant_id, WorkOrder.order_id == order.id, WorkOrder.status != "complete")
            if config["target"] == "installation_work_order":
                query = query.filter(WorkOrder.kind == "installation")
            work_order = query.order_by(WorkOrder.id.desc()).first() if entity_type == "order" else None
            if work_order is None:
                entry.update({"skipped": f"no open {config['target'].replace('_', ' ')} on this {entity_type}"})
            else:
                user = lifecycle_service.resolve_user(db, tenant_id, render(config.get("to_user"), ctx))
                position = lifecycle_service.resolve_position(db, tenant_id, render(config.get("to_position"), ctx))
                department = None if position or user else lifecycle_service.department_named(
                    db, tenant_id, render(config.get("to_department") or config.get("to_position"), ctx))
                if user is None and position is None and department is None:
                    raise ValueError(f"Assign node '{label}': '{config.get('to_position') or config.get('to_user') or config.get('to_department')}' "
                                     "is not a user, position or department")
                user_id = user.id if user else None
                if config.get("strategy") == "round_robin" and position is not None and user is None:
                    members = sorted(m.user_id for m in position.members)
                    user_id = members[work_order.id % len(members)] if members else None
                entry.update(lifecycle_service.assign(
                    db, tenant_id, "work_order", work_order.id, user_id, position.id if position else None,
                    f"Flow '{flow.name}' - {work_order.kind.replace('_', ' ')} work order of order #{order.id}", source,
                    department.id if department is not None else None,
                ))
        elif node_type == "assign":
            target = need_record(label)
            user = lifecycle_service.resolve_user(db, tenant_id, render(config.get("to_user"), ctx))
            position = lifecycle_service.resolve_position(db, tenant_id, render(config.get("to_position"), ctx))
            if config.get("to_user") and user is None:
                raise ValueError(f"Assign node '{label}': user '{config.get('to_user')}' not found")
            department = None
            if config.get("to_position") and position is None:
                department = lifecycle_service.department_named(db, tenant_id, render(config.get("to_position"), ctx))
                if department is None:
                    raise ValueError(f"Assign node '{label}': position '{config.get('to_position')}' not found")
            if config.get("to_department"):
                department = lifecycle_service.department_named(db, tenant_id, render(config.get("to_department"), ctx))
                if department is None:
                    raise ValueError(f"Assign node '{label}': department '{config.get('to_department')}' not found")
            if config.get("strategy") == "round_robin" and position is not None and user is None:
                members = sorted(m.user_id for m in position.members)
                user_id = members[(target.id) % len(members)] if members else None
            else:
                user_id = user.id if user else None
            entry.update(
                lifecycle_service.assign(
                    db, tenant_id, entity_type, target.id, user_id, position.id if position else None,
                    f"Flow '{flow.name}'", source, department.id if department is not None and position is None else None,
                )
            )
            refresh_record()
        elif node_type == "notify" and config.get("channel") == "popup":
            from app.crm_core import notice_service

            message = str(render(config.get("message", ""), ctx))
            audience = str(render(config.get("audience") or "me", ctx))
            title = str(render(config.get("title") or flow.name, ctx))
            level = config.get("level") if config.get("level") in notice_service.LEVELS else "info"
            user_ids = notice_service.popup_recipients(db, tenant_id, audience, entity_type, record)
            notification_adapter.send(db, execution_id, "popup", audience, message)
            event_bus.publish(db, execution_id, notice_service.POPUP_EVENT, {
                "tenant_id": tenant_id, "user_ids": user_ids, "title": title, "message": message, "level": level, "audience": audience,
                "entity_type": entity_type, "entity_id": record.id if record is not None else None, "flow": flow.name})
            entry.update({"audience": audience, "message": message, "popup": {"title": title, "level": level, "to": len(user_ids)}})
        elif node_type == "notify":
            message = str(render(config.get("message", ""), ctx))
            audience = str(render(config.get("audience", "owner"), ctx))
            notification_adapter.send(db, execution_id, config.get("channel", "email"), audience, message)
            entry.update({"audience": audience, "message": message})
        elif node_type == "action":
            done = run_action(db, tenant_id, execution_id, flow, entity_type, record, config, ctx)
            entry.update({"intent": done["intent"], "entities": done["entities"], "filled": done["filled"], "result_ids": done["result_ids"]})
            entry["input"] = done["entities"]
            entry["output"] = done["output"]
            ctx["result"] = {**(ctx.get("result") or {}), **done["output"]}
            touched.extend(t for t in done["touched"] if t not in touched)
            refresh_record()
        elif node_type == "catch":
            # the error being handled is this step's output too: {{steps.<id>.output.message}}
            entry["output"] = dict(ctx.get("error") or {})
        elif node_type == "throw":
            raise flow_errors.raised(str(config.get("code") or "").strip(), str(render(config.get("message") or "", ctx)),
                                     config.get("category") or None)
        elif node_type == "reject":
            message = str(render(config.get("message") or f"Rejected by flow '{flow.name}'", ctx))
            trace.append({**entry, "status": "rejected", "message": message})
            raise FlowRejected(f"Flow '{flow.name}': {message}")
        return branch

    def retries_of(node: dict[str, Any]) -> int:
        raw = str((node.get("config") or {}).get("retries") or "0").strip()
        return min(int(raw), MAX_RETRIES) if raw.isdigit() and "retries" in NODE_TYPES[node["type"]]["config"] else 0

    in_handler = False  # inside a Catch errors path: a failure there is not caught again by Catch errors
    try:
        while current is not None:
            visits += 1
            if visits > MAX_NODE_VISITS:
                raise flow_errors.FlowError("LOOP_LIMIT", f"exceeded {MAX_NODE_VISITS} steps - check it for a loop").at(flow)
            node_type = current["type"]
            config = current.get("config") or {}
            label = current.get("label") or NODE_TYPES[node_type]["label"]
            outgoing = edges.get(current["id"], [])
            error_edge = next((e for e in outgoing if e.get("label") == "error"), None)
            attempts = retries_of(current) + 1
            err: flow_errors.FlowError | None = None
            branch = ""
            for attempt in range(1, attempts + 1):
                entry: dict[str, Any] = {"node_id": current["id"], "type": node_type, "label": label, "status": "ok"}
                savepoint = db.begin_nested() if node_type in _WRITES else None
                try:
                    branch = execute(current, entry)
                    if savepoint is not None:
                        savepoint.commit()
                    err = None
                    break
                except FlowRejected:
                    if savepoint is not None and savepoint.is_active:
                        savepoint.rollback()
                    raise
                except Exception as exc:  # noqa: BLE001 - every failure is classified and routed below
                    if savepoint is not None and savepoint.is_active:
                        savepoint.rollback()  # undoes this step's own writes - nothing before it
                    refresh_record()
                    err = flow_errors.classify(exc, flow, current, label, intent=config.get("intent") if node_type == "action" else None)
                    if not (err.retryable and attempt < attempts):
                        break
            if attempts > 1 and (attempt > 1 or err is not None):
                entry["attempts"] = attempt

            if err is not None:
                entry.update(status="failed", error=err.as_dict())
                if err.catchable and error_edge is not None:
                    # the step's own "error" output: carry on down it
                    entry["status"] = "caught"
                    caught.append(err.as_dict())
                    ctx["error"] = err.as_dict()
                    ctx["steps"][current["id"]] = {"input": entry.get("input"), "output": {"error": err.as_dict()}}
                    trace.append(entry)
                    event_bus.publish(db, execution_id, "flow.error_caught", {"flow_id": flow.id, "node_id": current["id"], "code": err.code})
                    current = nodes.get(error_edge["target"])
                    continue
                on_error = str(config.get("on_error") or "")
                if node_type == "action" and err.catchable and (on_error == "skip" or (on_error == "" and not catch_nodes)):
                    # an action that can't run is skipped and the flow carries on - the long-standing default,
                    # unless the flow has a Catch errors step to take it
                    entry.update(status="skipped", intent=config.get("intent"), skipped=err.message)
                else:
                    handler = None if in_handler else next(
                        (n for n in catch_nodes if flow_errors.matches(err, (n.get("config") or {}).get("codes"))), None)
                    trace.append(entry)
                    if handler is None:
                        raise err
                    entry.update(status="caught", caught_by=handler["id"])
                    caught.append(err.as_dict())
                    ctx["error"] = err.as_dict()
                    ctx["steps"][current["id"]] = {"input": entry.get("input"), "output": {"error": err.as_dict()}}
                    event_bus.publish(db, execution_id, "flow.error_caught", {"flow_id": flow.id, "node_id": current["id"], "code": err.code,
                                                                              "handler": handler["id"]})
                    in_handler = True
                    current = handler
                    continue

            entry.setdefault("output", {k: v for k, v in entry.items() if k not in ("input", "node_id", "type", "label", "status", "attempts")})
            ctx["steps"][current["id"]] = {"input": entry.get("input"), "output": entry["output"]}
            trace.append(entry)
            # Interactive ordering hands control to the user; no order exists yet.
            if isinstance(entry["output"], dict) and entry["output"].get("configuration_request"):
                break
            if node_type == "end":
                break
            next_edge = next((e for e in outgoing if (e.get("label") or "") == branch), None)
            if next_edge is None and branch == "":
                next_edge = next((e for e in outgoing if (e.get("label") or "") != "error"), None)
            current = nodes.get(next_edge["target"]) if next_edge else None
    except FlowRejected as exc:
        exc.run = summary("rejected")  # type: ignore[attr-defined]
        raise
    except flow_errors.FlowError as exc:
        exc.run = summary("failed")
        raise

    outcome = "handled" if caught else "completed"
    event_bus.publish(
        db, execution_id, "flow.executed",
        {"flow_id": flow.id, "flow_name": flow.name, "entity_type": entity_type, "entity_id": record.id if record else None, "outcome": outcome},
    )
    return summary(outcome)


def run_flow_guarded(db: Session, tenant_id: int, execution_id: int, flow: FlowDefinition, ctx: dict[str, Any]) -> dict[str, Any]:
    """run_flow under the flow's policy for an error it doesn't catch itself:
    "fail" (the default) fails it - and with it the intent that triggered it;
    "continue" undoes only this flow's changes, records the failure
    ({"failed": True, "error": {...}}) and lets the intent complete.
    A Reject step is a decision, not an error - it always turns the intent down."""
    if (getattr(flow, "on_error", None) or "fail") != "continue":
        return run_flow(db, tenant_id, execution_id, flow, ctx)
    savepoint = db.begin_nested()
    try:
        run = run_flow(db, tenant_id, execution_id, flow, ctx)
        savepoint.commit()
        return run
    except FlowRejected:
        if savepoint.is_active:
            savepoint.rollback()
        raise
    except Exception as exc:  # noqa: BLE001 - the flow's policy says carry on; the failure is reported, not raised
        if savepoint.is_active:
            savepoint.rollback()
        err = flow_errors.classify(exc, flow)
        run = dict(err.run or {"flow_id": flow.id, "flow_name": flow.name, "trace": []})
        run.update(outcome="failed", failed=True, error=err.as_dict())
        event_bus.publish(db, execution_id, "flow.failed", {"flow_id": flow.id, "flow_name": flow.name, "code": err.code,
                                                             "message": err.message, "node_id": err.node_id})
        return run


def flow_step(db: Session, tenant_id: int, execution_id: int, flow: FlowDefinition, ctx_provider) -> SagaStep:
    """ctx_provider is called when the step runs, so an "after" flow sees
    the results of every intent step that ran before it."""

    def action() -> dict[str, Any]:
        run = run_flow_guarded(db, tenant_id, execution_id, flow, ctx_provider())
        if run.get("failed"):
            return {"flows_failed": [run]}
        if run.get("skipped") and run.get("reason"):
            return {"flows_skipped": [run]}
        return {"flows_run": [run]}

    return SagaStep(f"flow:{flow.name}", action)


# ---------- lookups ----------


def _active(db: Session, tenant_id: int):
    return db.query(FlowDefinition).filter(FlowDefinition.tenant_id == tenant_id, FlowDefinition.is_active.is_(True))


def flows_for_intent(db: Session, tenant_id: int, intent_type: str, timing: str) -> list[FlowDefinition]:
    from app.orchestration import conflicts

    flows = (
        _active(db, tenant_id)
        .filter(
            FlowDefinition.trigger_type == "intent",
            FlowDefinition.trigger_intent_type == intent_type,
            FlowDefinition.timing == timing,
        )
        .order_by(FlowDefinition.id)
        .all()
    )
    # several flows on one trigger run in the order the admin set (Admin -> Conflicts)
    return conflicts.ordered(db, tenant_id, f"intent:{intent_type}:{timing}", flows)


def flows_for_state(db: Session, tenant_id: int, entity_type: str, state: str) -> list[FlowDefinition]:
    from app.orchestration import conflicts

    flows = (
        _active(db, tenant_id)
        .filter(
            FlowDefinition.trigger_type == "state_enter",
            FlowDefinition.entity_type == entity_type,
            FlowDefinition.trigger_state == state,
        )
        .order_by(FlowDefinition.id)
        .all()
    )
    return conflicts.ordered(db, tenant_id, f"state:{entity_type}:{state}", flows)


def custom_intent_flows(db: Session, tenant_id: int) -> list[FlowDefinition]:
    return _active(db, tenant_id).filter(FlowDefinition.trigger_type == "custom_intent").order_by(FlowDefinition.id).all()


def custom_intent_code(flow: FlowDefinition) -> str:
    return f"FLOW-{flow.id}"


def flow_from_intent_code(db: Session, tenant_id: int, intent_type: str | None) -> FlowDefinition | None:
    if not intent_type or not intent_type.startswith("FLOW-"):
        return None
    try:
        flow_id = int(intent_type.split("-", 1)[1])
    except ValueError:
        return None
    flow = db.get(FlowDefinition, flow_id)
    if flow is None or flow.tenant_id != tenant_id or not flow.is_active or flow.trigger_type != "custom_intent":
        return None
    return flow


def required_fields(flow: FlowDefinition) -> list[str]:
    return [f.strip() for f in (flow.required_fields or "").split(",") if f.strip()]

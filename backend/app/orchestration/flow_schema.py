"""What a flow can reference, and an auto-corrector for what it does reference.

field_catalog() is the Flow Studio's source of truth for autocomplete:
every field a condition / set_field / template can address for the flow's
record type and trigger, with its type, picklist values and whether a
set_field node may write it:

- record.<field>  - the record's core fields (types and enum values read
  from the ORM model), its tenant custom fields (data type + picklist
  values), and lifecycle info (state / owner, with their own value lists);
- entities.<field> - what the triggering intent supplies (built-in intents'
  known inputs; a custom intent's required fields);
- result.<field> - what a built-in intent returns, for "after" flows;
plus the option lists for the other node settings (states, users,
positions, channels, round-robin strategy) and the values this tenant has
already used in other flows ("recent" - the designer remembers).

review_graph() checks a graph against that catalog. Unambiguous fixes are
applied automatically (wrong case/spacing, a label instead of a key, a
clear typo of a record field or picklist value, a state/position/user
written loosely); anything less certain comes back as a suggestion; and
references it cannot place at all come back as warnings - a typo'd field
would otherwise just make a condition silently false at run time.
"""

from __future__ import annotations

import difflib
import json
import re
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import Boolean, DateTime, Enum, Float, Integer, Numeric, inspect
from sqlalchemy.orm import Session

from app.crm_core import custom_field_service, lifecycle_service
from app.intent_engine import reasoning
from app.models.auth import User
from app.models.crm import TerritoryRule
from app.models.flows import FlowDefinition
from app.models.lifecycle import Position
from app.orchestration import flow_engine
from app.crm_core.record_service import _SNAPSHOT_FIELDS

CHANNELS = ["email", "sms", "whatsapp", "in_app", "popup"]  # popup: a message box on the person's screen
STRATEGIES = ["position", "round_robin"]

# Inputs each built-in intent may carry beyond its required fields (see
# classifier._SYSTEM_PROMPT) - what an intent-triggered flow can read as
# entities.<name>. Value lists: fixed vocabularies; "@offerings"/"@regions"
# are filled from the tenant's own data.
_INTENT_INPUTS: dict[str, list[str]] = {
    "INT-01": ["name", "composition", "segment", "channel", "region", "list_price", "requested_discount_pct"],
    "INT-02": ["offering_name", "new_list_price", "new_segment", "new_channel"],
    "INT-03": ["offering_name", "replacement_offering_name"],
    "INT-04": ["company_name", "source", "region", "offering_name", "quantity"],
    "INT-05": ["opportunity_id", "offering_name", "quantity", "requested_discount_pct", "discount_justification"],
    "INT-06": ["quote_id", "decision", "reasoning"],
    "INT-07": ["quote_id", "service_account_name", "sites"],
    "INT-106": ["party_id", "service_account_id", "offering_name", "order_mode", "quantity", "pricing_attributes", "technical_attributes", "requested_discount_pct", "submit_for_provisioning"],
    "INT-107": ["asset_id", "asset_operation", "quantity", "pricing_attributes", "technical_attributes", "requested_discount_pct", "target_service_account_id", "target_site", "port_number", "donor_operator"],
    "INT-108": ["order_id"],
    "INT-110": ["order_id", "asset_id", "reason"],
    "INT-111": ["order_id", "asset_id", "asset_operation", "quantity", "pricing_attributes", "technical_attributes", "target_site", "promotion", "due_date", "reason"],
    "INT-112": ["asset_ids", "party_id", "current_offering", "asset_operation", "quantity", "pricing_attributes", "promotion", "due_date"],
    "INT-113": ["asset_id", "as_of"],
    "INT-114": ["asset_id", "order_id", "asset_operation", "quantity", "pricing_attributes", "technical_attributes", "target_site", "promotion", "due_date"],
    "INT-115": ["quote_number"],
    "INT-116": ["quote_id", "override_price", "reason", "remove_override"],
    "INT-117": ["order_id", "fulfillment_pattern"],
    "INT-118": ["order_id", "fallout_action", "step", "note"],
    "INT-119": ["process_description", "pattern_name"],
    "INT-120": ["question"],
    "INT-121": ["location"],
    "INT-122": ["item_type", "quantity", "location", "vendor", "model"],
    "INT-123": ["opportunity_id", "order_id", "resources"],
    "INT-124": ["opportunity_id", "order_id", "note"],
    "INT-125": ["item", "lifecycle", "oper_status", "note"],
    "INT-126": ["item"],
    "INT-127": ["pool_type_code", "range", "block_size", "location", "pool_name"],
    "INT-128": ["type_description"],
    "INT-08": ["order_id", "new_monthly_price"],
    "INT-09": ["order_id", "action", "decision"],
    "INT-10": ["description", "company_name", "sla_class"],
    "INT-11": ["ticket_id"],
    "INT-12": ["ticket_id", "order_id", "required_skill", "region", "scheduled_window"],
    "INT-13": ["party_id", "id_number", "id_type", "provider"],
    "INT-14": ["order_id", "site_label"],
    "INT-15": ["name", "email", "phone"],
    "INT-16": ["lead_id", "offering_name"],
    "INT-17": ["lead_id", "decision", "reasoning"],
    "INT-18": ["opportunity_id", "address", "technical_attributes"],
    "INT-19": ["quote_id", "requested_discount_pct", "quantity"],
    "INT-20": ["quote_id", "reasoning"],
    "INT-21": ["order_id", "new_monthly_price", "new_quantity"],
    "INT-22": ["quote_id"],
    "INT-23": ["proposal_id", "decision"],
    "INT-24": ["quote_id"],
    "INT-25": ["order_id", "decision", "reasoning"],
    "INT-26": ["work_order_id", "technical_parameters"],
    "INT-27": ["work_order_id", "signature"],
    "INT-28": ["work_order_id", "document_ref"],
    "INT-29": ["entity_type", "entity_id"],
    "INT-30": ["quote_id", "requested_discount_pct", "quantity"],
    "INT-31": ["entity_type", "entity_id", "action", "to_state", "reasoning"],
    "INT-32": ["entity_type", "entity_id", "to_user", "to_position", "to_department", "include_related", "reason"],
    "INT-33": ["instruction"],
    "INT-35": ["party_id", "price_list", "industry"],
    "INT-36": ["entity_type", "entity_id", "decision", "reasoning"],
    "INT-37": ["opportunity_id", "offering_name", "quantity", "pricing_attributes"],
    "INT-38": ["opportunity_id", "address", "technical_parameters", "to_user"],
    "INT-39": ["quote_id", "po_number", "po_amount", "document_ref"],
    "INT-40": ["quote_id"],
    "INT-41": ["name", "email", "phone"],
    "INT-42": ["offering_name", "design", "instruction"],
    "INT-43": ["price_list", "offering_name", "price"],
    "INT-44": ["entity_type", "entity_id", "price_list"],
    "INT-45": ["order_id", "technical_attributes"],
    "INT-46": ["party_id", "products", "source", "region"],
    "INT-47": ["opportunity_id", "offering_name", "need", "max_monthly"],
    "INT-48": ["offering_name", "valid_from", "notes"],
    "INT-49": ["party_id", "offering_name"],
    "INT-50": ["order_id", "pricing_attributes"],
    "INT-109": ["quote_id", "notes"],
}
from app.intent_engine.om_intents import FLOW_FIELDS as _OM_FLOW_FIELDS  # noqa: E402

_INTENT_INPUTS.update(_OM_FLOW_FIELDS)
from app.intent_engine.comm_intents import FLOW_FIELDS as _CM_FLOW_FIELDS  # noqa: E402

_INTENT_INPUTS.update(_CM_FLOW_FIELDS)
_INTENT_INPUTS.update({
    'TRN-01': ['name', 'description', 'curriculum', 'details'],
    'TRN-02': ['program_id', 'curriculum'],
    'TRN-03': ['title', 'revision_id', 'facilitator_id', 'location', 'starts_at', 'ends_at', 'capacity'],
    'TRN-04': ['session_id', 'user_id'],
    'TRN-05': ['registration_id', 'attendance'],
    'TRN-06': [],
})

_INPUT_VALUES: dict[str, list[str] | str] = {
    "decision": ["approve", "reject", "conditional"],
    "segment": ["B2C", "B2B", "B2B-SMB", "B2B-Enterprise"],
    "new_segment": ["B2C", "B2B", "B2B-SMB", "B2B-Enterprise"],
    "channel": ["B2C", "B2B"],
    "new_channel": ["B2C", "B2B"],
    "sla_class": ["standard", "premium"],
    "id_type": ["national_id", "passport", "iqama", "commercial_registration"],
    "entity_type": ["party", "lead", "opportunity", "quote", "order"],
    "offering_name": "@offerings",
    "replacement_offering_name": "@offerings",
    "region": "@regions",
}
_NUMBER_INPUT = re.compile(r"(_id$|^quantity$|^new_quantity$|price$|_pct$)")

# What a built-in intent returns that an "after" flow branches on - read as
# result.<name>. Value list = picklist; empty = typed by name like inputs.
_INTENT_RESULTS: dict[str, dict[str, list[str]]] = {
    "INT-49": {"party_id": [], "eligible": [], "not_eligible": [], "needs_first": [], "owns": []},
    "INT-07": {"order_id": [], "party_id": [], "service_account_id": [], "monthly_price": [], "status": []},
    "INT-106": {"order_id": [], "order_item_id": [], "asset_id": [], "party_id": [], "product_offering_id": [], "offering_name": [], "status": [], "unit_price": [], "discount_pct": [], "net_unit_price": [], "configuration_request": []},
    "INT-107": {"order_id": [], "order_item_id": [], "asset_id": [], "operation": [], "status": [], "orchestration_ref": []},
    "INT-108": {"order_id": [], "status": [], "asset_ids": []},
    "INT-110": {"order_id": [], "asset_id": [], "operation": [], "status": ["cancelled"]},
    "INT-111": {"order_id": [], "asset_id": [], "operation": [], "status": [], "revision": [], "revision_of_order_id": []},
    "INT-112": {"operation": [], "requested": [], "submitted": [], "failed": [], "order_ids": []},
    "INT-113": {"asset_id": [], "status": [], "version": []},
    "INT-114": {"quote_id": [], "quote_number": [], "asset_id": [], "operation": [], "new_monthly": [], "valid_until": []},
    "INT-115": {"quote_number": [], "order_id": [], "asset_id": [], "operation": [], "status": []},
    "INT-117": {"order_id": [], "fulfillment_order": [], "fulfillment_status": ["in_progress", "waiting", "fallout", "completed"], "planned_by": [],
                "fulfillment_pattern": []},
    "INT-118": {"order_id": [], "fulfillment_order": [], "step": [], "action": ["retry", "skip", "complete"], "step_status": [], "fulfillment_status": []},
    "INT-119": {"fulfillment_pattern_id": [], "fulfillment_pattern": [], "pattern_status": ["draft"], "products": [], "started_by": [], "drafted_by": []},
    "INT-120": {"count": [], "read_as": [], "answered_by": []},
    "INT-121": {"location": [], "summary": []},
    "INT-122": {"received": [], "item_type": [], "location": []},
    "INT-123": {"opportunity_id": [], "order_id": [], "location": [], "summary": []},
    "INT-124": {"opportunity_id": [], "order_id": [], "count": []},
    "INT-125": {"item": [], "item_id": [], "lifecycle": ["available", "reserved", "in_use", "maintenance", "retired", "planned"],
                "oper_status": ["up", "down", "degraded", "unknown"], "services_affected": [], "customers_affected": []},
    "INT-126": {"item": [], "summary": [], "affected_items": []},
    "INT-127": {"pool": [], "pool_id": [], "capacity": [], "location": []},
    "INT-128": {"item_type": [], "code": [], "kind": ["location", "physical", "logical", "service"], "drafted_by": []},
    "INT-116": {"quote_id": [], "total_price": [],"computed_price": [], "list_price": [], "override_price": [], "adjustment": [],
                "effective_discount_pct": [], "round": [], "override_removed": ["true", "false"]},
    "INT-41": {"party_id": [], "name": [], "segment": []},
    "INT-18": {"opportunity_id": [], "assessment_id": [], "outcome": ["passed", "failed", "pending"]},
    "INT-40": {"quote_id": [], "order_id": [], "agreement_document_id": [], "agreement_number": []},
    "INT-109": {"quote_id": [], "document_id": [], "version": []},
    "INT-22": {"quote_id": [], "proposal_id": [], "document_id": [], "status": ["sent"]},
    "INT-39": {"quote_id": [], "po_number": [], "po_amount": [], "proposal_accepted": ["true", "false"]},
    "INT-36": {"decision": ["approve", "reject"], "chain_status": ["approved", "rejected", "pending"]},
}


@dataclass
class FieldDef:
    path: str
    key: str
    label: str
    data_type: str  # text | number | boolean | date | picklist
    source: str  # core | custom | lifecycle | intent
    values: list[str] = field(default_factory=list)
    settable: bool = False
    recent: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()


def _label(key: str) -> str:
    return key.replace("_", " ").strip().capitalize().replace(" pct", " %").replace(" id", " #")


def _observed_outputs(db, tenant_id, intent_type):
    """Discover field paths, never example values, from this tenant's executions."""
    from app.models.intent_log import IntentExecutionLog
    found = {}
    def visit(value, path="", depth=0):
        if depth > 5 or len(found) >= 200:
            return
        if path:
            found[path] = "boolean" if isinstance(value, bool) else "number" if isinstance(value, (int, float)) else "text"
        if isinstance(value, dict):
            for key, child in value.items():
                if re.fullmatch(r"[a-zA-Z_][a-zA-Z0-9_]*", key) and not key.startswith("_"):
                    visit(child, f"{path}.{key}" if path else key, depth + 1)
        elif isinstance(value, list) and value:
            visit(value[0], f"{path}.0", depth + 1)
    if not intent_type:
        return found
    logs = db.query(IntentExecutionLog.plan_json).filter(IntentExecutionLog.tenant_id == tenant_id,
        IntentExecutionLog.intent_type == intent_type, IntentExecutionLog.status == "executed").order_by(IntentExecutionLog.id.desc()).limit(5).all()
    for (raw,) in logs:
        try:
            plan = json.loads(raw or "[]")
        except (ValueError, TypeError):
            continue
        if not isinstance(plan, list):
            continue
        for step in plan:
            if isinstance(step, dict) and not str(step.get("action", "")).startswith(("flow:", "lifecycle_sync")):
                detail = step.get("detail")
                if isinstance(detail, dict):
                    visit({k: v for k, v in detail.items() if k != "flows_run"})
    return found


def _column_type(model, key: str) -> tuple[str, list[str]]:
    column = inspect(model).columns.get(key)
    if column is None:
        return "text", []
    col_type = column.type
    if isinstance(col_type, Enum):
        return "picklist", [str(v) for v in col_type.enums]
    if isinstance(col_type, Boolean):
        return "boolean", ["true", "false"]
    if isinstance(col_type, (Integer, Float, Numeric)):
        return "number", []
    if isinstance(col_type, DateTime):
        return "date", []
    return "text", []


def _recent_values(db: Session, tenant_id: int) -> dict[str, list[str]]:
    """field path -> values already used for it in this tenant's flows."""
    seen: dict[str, list[str]] = {}
    from app.licensing import flow_available
    for flow in db.query(FlowDefinition).filter(FlowDefinition.tenant_id == tenant_id).all():
        if not flow_available(db, tenant_id, flow):
            continue
        try:
            nodes = json.loads(flow.graph_json or "{}").get("nodes") or []
        except json.JSONDecodeError:
            continue
        for node in nodes:
            config = node.get("config") or {}
            path, value = str(config.get("field") or "").strip(), str(config.get("value") or "").strip()
            if path and value and "{{" not in value:
                bucket = seen.setdefault(path, [])
                if value not in bucket:
                    bucket.append(value)
    return seen


def field_catalog(
    db: Session,
    tenant_id: int,
    entity_type: str | None,
    trigger_intent_type: str | None = None,
    custom_required_fields: str | None = None,
) -> dict[str, Any]:
    from app.licensing import require_entity, intent_available
    from fastapi import HTTPException
    require_entity(db, tenant_id, entity_type)
    if trigger_intent_type and not intent_available(db, tenant_id, trigger_intent_type):
        raise HTTPException(403, "Intent is not available in the selected modules")
    users = db.query(User).filter(User.tenant_id == tenant_id, User.is_active.is_(True)).order_by(User.name).all()
    positions = [p.name for p in db.query(Position).filter(Position.tenant_id == tenant_id, Position.is_active.is_(True)).order_by(Position.name)]
    from app.integration import tmf620_adapter

    offerings = tmf620_adapter.active_offering_names(db, tenant_id)  # on sale now, once each - not every version
    regions = sorted({r.region for r in db.query(TerritoryRule).filter(TerritoryRule.tenant_id == tenant_id)})
    recent = _recent_values(db, tenant_id)
    fields: list[FieldDef] = []
    states: list[dict[str, str]] = []

    if entity_type in lifecycle_service.ENTITY_MODELS:
        model = lifecycle_service.ENTITY_MODELS[entity_type]
        settable = lifecycle_service.UPDATABLE_CORE_FIELDS.get(entity_type, {})
        for key in _SNAPSHOT_FIELDS[entity_type]:
            data_type, values = _column_type(model, key)
            fields.append(FieldDef(f"record.{key}", key, _label(key), data_type, "core", values, key in settable))
        state_model = lifecycle_service.resolve_state_model(db, tenant_id, entity_type)
        states = [{"code": code, "label": s.label} for code, s in state_model.states.items()]
        fields += [
            FieldDef("record.state", "state", "State", "picklist", "lifecycle", [s["code"] for s in states]),
            FieldDef("record.built_in_status", "built_in_status", "Built-in status", "picklist", "lifecycle",
                     list(lifecycle_service.BUILT_IN_STATES.get(entity_type, []))),
            FieldDef("record.owner_user", "owner_user", "Owner (user email)", "picklist", "lifecycle", [u.email for u in users]),
            FieldDef("record.owner_position", "owner_position", "Owner (position)", "picklist", "lifecycle", positions),
        ]
        for d in custom_field_service.list_definitions(db, tenant_id, entity_type):
            values = custom_field_service.allowed_values(d) if d.data_type == "picklist" else (["true", "false"] if d.data_type == "boolean" else [])
            fields.append(FieldDef(f"record.{d.key}", d.key, d.label, d.data_type, "custom", values, True))
    if entity_type in ("lead", "opportunity", "quote", "order"):
        # the customer behind the record (flow_engine.customer_of) - read-only from this flow
        party = lifecycle_service.ENTITY_MODELS["party"]
        for key in _SNAPSHOT_FIELDS["party"]:
            data_type, values = _column_type(party, key)
            fields.append(FieldDef(f"customer.{key}", key, f"Customer: {_label(key)}", data_type, "customer", values))
        for d in custom_field_service.list_definitions(db, tenant_id, "party"):
            values = custom_field_service.allowed_values(d) if d.data_type == "picklist" else (["true", "false"] if d.data_type == "boolean" else [])
            fields.append(FieldDef(f"customer.{d.key}", d.key, f"Customer: {d.label}", d.data_type, "customer", values))

    inputs: list[str] = []
    if trigger_intent_type in _INTENT_INPUTS:
        inputs = list(dict.fromkeys(reasoning.required_fields_for(trigger_intent_type) + _INTENT_INPUTS[trigger_intent_type]))
    elif custom_required_fields:
        inputs = [f.strip() for f in custom_required_fields.split(",") if f.strip()]
    for key in inputs:
        values = _INPUT_VALUES.get(key, [])
        values = offerings if values == "@offerings" else regions if values == "@regions" else list(values)
        data_type = "picklist" if values else ("number" if _NUMBER_INPUT.search(key) else "text")
        fields.append(FieldDef(f"entities.{key}", key, f"Intent input: {_label(key)}", data_type, "intent", values))
    for key, values in _INTENT_RESULTS.get(trigger_intent_type or "", {}).items():
        data_type = "picklist" if values else ("number" if _NUMBER_INPUT.search(key) else "text")
        fields.append(FieldDef(f"result.{key}", key, f"Intent result: {_label(key)}", data_type, "result", list(values)))
    for key, data_type in _observed_outputs(db, tenant_id, trigger_intent_type).items():
        if not any(f.path == f"result.{key}" for f in fields):
            fields.append(FieldDef(f"result.{key}", key, f"Intent result: {_label(key)}", data_type, "result", []))

    for f in fields:
        f.recent = recent.get(f.path, []) or recent.get(f.key, [])

    aliases = []
    for f in fields:
        if f.path.startswith(("entities.", "result.")):
            scope = "input" if f.path.startswith("entities.") else "output"
            aliases.append(FieldDef(f"trigger.{scope}.{f.key}", f.key, f"Trigger {scope}: {_label(f.key)}", f.data_type, f.source, f.values))
    fields.extend(aliases)
    fields.extend([FieldDef("trigger.input", "input", "Trigger inputs (all fields)", "text", "intent", []),
                   FieldDef("trigger.output", "output", "Trigger outputs (all fields)", "text", "result", [])])
    # the failure being handled - on a step's "error" path, after a Catch errors step, or in an "on error" flow
    from app.orchestration import flow_errors

    fields.extend([
        FieldDef("error.code", "code", "Error: code", "picklist", "error", [c for c in flow_errors.CODES if c not in flow_errors.UNCATCHABLE]),
        FieldDef("error.category", "category", "Error: category", "picklist", "error", list(flow_errors.CATEGORIES)),
        FieldDef("error.message", "message", "Error: message", "text", "error", []),
        FieldDef("error.node", "node", "Error: step that failed", "text", "error", []),
        FieldDef("error.intent", "intent", "Error: action (intent) that failed", "text", "error", []),
        FieldDef("error.flow", "flow", "Error: flow it happened in", "text", "error", []),
        # the answer to a Wait for approval step - on the steps after it (flow_runs.resume)
        FieldDef("approval.decision", "decision", "Approval: decision", "picklist", "approval", ["approved", "rejected", "expired"]),
        FieldDef("approval.note", "note", "Approval: approver's note", "text", "approval", []),
        FieldDef("approval.by", "by", "Approval: who decided", "text", "approval", []),
    ])

    from app.orchestration import flow_parameters
    parameters = {}
    for f in fields:
        if f.path.startswith(("entities.", "result.")):
            parameters[f.key] = {**f.as_dict(), "path": f"flow.inputs.parameters.{f.key}",
                                 "label": f"Intent → {_label(f.key)}", "settable": False}
    handoff = list(parameters.values()) + flow_parameters.record_fields(set(parameters))

    return {
        "fields": [f.as_dict() for f in fields] + handoff,
        "handoff": handoff,
        "states": states,
        "users": [{"email": u.email, "name": u.name} for u in users],
        "positions": positions,
        "channels": CHANNELS,
        "strategies": STRATEGIES,
    }


# ---------------------------------------------------------------- review

_TEMPLATE = re.compile(r"\{\{\s*([^}]+?)\s*\}\}")
_NUMERIC_OPS = {"gt", "gte", "lt", "lte"}
_TYPED_RESULT = re.compile(r"^(?:intent\s+)?result\s*:\s*(\S.*)$", re.IGNORECASE)


def _norm(text: str) -> str:
    """"Requested discount %" / "requested-discount pct" -> requested_discount_pct"""
    text = text.strip().lower().replace("%", " pct").replace("#", " id")
    return re.sub(r"[^a-z0-9@.]+", "_", text).strip("_")


def _best(raw: str, options: dict[str, str], cutoff: float = 0.72) -> tuple[str | None, float]:
    """options: normalized alias -> canonical value. Returns (canonical, score)."""
    key = _norm(raw)
    if key in options:
        return options[key], 1.0
    matches = difflib.get_close_matches(key, list(options), n=2, cutoff=cutoff)
    if not matches:
        return None, 0.0
    score = difflib.SequenceMatcher(None, key, matches[0]).ratio()
    if len(matches) > 1 and options[matches[0]] != options[matches[1]]:
        second = difflib.SequenceMatcher(None, key, matches[1]).ratio()
        if second >= score - 0.05:
            return options[matches[0]], min(score, 0.8)  # ambiguous - suggest, don't auto-fix
    return options[matches[0]], score


class _Reviewer:
    def __init__(self, catalog: dict[str, Any], custom_intent: bool):
        self.fields = {f["path"]: f for f in catalog["fields"]}
        self.custom_intent = custom_intent
        self.catalog = catalog
        self.corrections: list[dict[str, Any]] = []
        self.issues: list[dict[str, Any]] = []
        aliases: dict[str, str] = {}
        for f in catalog["fields"]:  # record first: a bare key resolves to record at run time
            short_label = f["label"].removeprefix("Intent input: ").removeprefix("Intent result: ")
            # error.* and approval.* are reached only by their full path - a bare "message", "code"
            # or "note" is never the error's or the approval's
            names = (f["path"], f["path"].replace(".", "_")) if f["path"].startswith(("error.", "approval.")) else \
                (f["path"], f["key"], f["label"], short_label, f["path"].replace(".", "_"))
            for alias in names:
                aliases.setdefault(_norm(alias), f["path"])
        self.aliases = aliases

    def _fix(self, node: dict[str, Any], key: str, before: Any, after: Any, reason: str) -> None:
        node.setdefault("config", {})[key] = after
        self.corrections.append({"node_id": node.get("id"), "node": node.get("label") or node.get("type"), "key": key,
                                 "before": before, "after": after, "reason": reason})

    def _issue(self, node: dict[str, Any], key: str, message: str, suggestion: Any = None) -> None:
        self.issues.append({"node_id": node.get("id"), "node": node.get("label") or node.get("type"), "key": key,
                            "message": message, "suggestion": suggestion})

    # -- fields
    def resolve_field(self, raw: str) -> tuple[str | None, bool, str]:
        """(path, auto, reason) - path None when nothing plausible."""
        text = raw.strip().strip("{}").strip()
        if text in self.fields:
            return text, True, ""
        typed_result = _TYPED_RESULT.match(text)
        if typed_result and _norm(text) not in self.aliases:
            # "Intent result: Status" for an intent whose results aren't catalogued
            return f"result.{_norm(typed_result.group(1))}", True, "intent results are read as result.<name>"
        scope, _, rest = text.partition(".")
        if scope in ("entities", "result") and rest:
            normalized = f"{scope}.{_norm(rest)}"
            if normalized in self.fields:
                return normalized, True, "normalized the name"
            if scope == "result":
                return text, True, ""  # intent results are open-ended
            guess, score = _best(rest, {_norm(f["key"]): p for p, f in self.fields.items() if p.startswith("entities.")})
            return (guess, False, "closest intent input") if guess else (None, False, "")
        if scope == "record" and rest:
            text = rest
        exact = self.aliases.get(_norm(text))
        if exact:
            return exact, True, "matched the field name/label"
        guess, score = _best(text, {a: p for a, p in self.aliases.items() if p.startswith("record.")})
        if guess:
            return guess, score >= 0.85, f"looks like a typo of {self.fields[guess]['label']}"
        guess, score = _best(text, {a: p for a, p in self.aliases.items()})
        return (guess, False, "closest field") if guess else (None, False, "")

    def check_field(self, node: dict[str, Any], key: str, numeric: bool = False, settable: bool = False) -> dict[str, Any] | None:
        raw = str((node.get("config") or {}).get(key) or "")
        if not raw:
            return None
        path, auto, reason = self.resolve_field(raw)
        if path is None:
            if raw.startswith(("steps.", "trigger.", "flow.", "error.", "approval.")) or (raw.startswith("entities.") and self.custom_intent):
                return None  # a custom intent may carry any input its phrases capture
            self._issue(node, key, f"'{raw}' is not a field this flow can see - the condition would never match")
            return None
        if path != raw:
            if auto:
                self._fix(node, key, raw, path, reason)
            else:
                self._issue(node, key, f"Unknown field '{raw}' - did you mean {self.fields.get(path, {}).get('label', path)}?", path)
                return None
        definition = self.fields.get(path)
        if definition and settable and not definition["settable"]:
            self._issue(node, key, f"{definition['label']} cannot be set by a flow")
        if definition and numeric and definition["data_type"] not in ("number", "date"):
            self._issue(node, "operator", f"{definition['label']} is {definition['data_type']} - greater/less-than needs a number")
        return definition

    # -- values
    def check_value(self, node: dict[str, Any], key: str, definition: dict[str, Any] | None, multiple: bool = False) -> None:
        config = node.get("config") or {}
        raw = config.get(key)
        if definition is None or raw in (None, "") or "{{" in str(raw):
            return
        data_type, values = definition["data_type"], definition["values"]
        if data_type == "number":
            if re.fullmatch(r"\s*-?\d+(\.\d+)?\s*%?\s*", str(raw)) is None:
                self._issue(node, key, f"{definition['label']} is a number - '{raw}' is not")
            elif "%" in str(raw) or str(raw) != str(raw).strip():
                self._fix(node, key, raw, str(raw).strip().rstrip("%").strip(), "kept just the number")
            return
        if data_type == "boolean":
            mapped = {"true": "true", "yes": "true", "y": "true", "1": "true", "false": "false", "no": "false", "n": "false", "0": "false"}.get(str(raw).strip().lower())
            if mapped is None:
                self._issue(node, key, f"{definition['label']} is yes/no - '{raw}' is neither", "true")
            elif mapped != raw:
                self._fix(node, key, raw, mapped, "yes/no value")
            return
        if not values:
            return
        options = {_norm(v): v for v in values}
        parts = [p.strip() for p in str(raw).split(",")] if multiple else [str(raw)]
        fixed, changed = [], False
        for part in parts:
            if part in values:
                fixed.append(part)
                continue
            guess, score = _best(part, options, cutoff=0.6)
            if guess and (score >= 0.85 or _norm(part) == _norm(guess)):
                fixed.append(guess)
                changed = True
            else:
                fixed.append(part)
                allowed = ", ".join(values[:12]) + ("…" if len(values) > 12 else "")
                self._issue(node, key, f"'{part}' is not a value of {definition['label']} ({allowed})", guess)
        if changed:
            self._fix(node, key, raw, ", ".join(fixed) if multiple else fixed[0], f"matched a {definition['label']} value")

    def check_choice(self, node: dict[str, Any], key: str, choices: dict[str, str], what: str) -> None:
        raw = (node.get("config") or {}).get(key)
        if raw in (None, "") or "{{" in str(raw) or raw in choices.values():
            return
        guess, score = _best(str(raw), choices, cutoff=0.6)
        if guess and score >= 0.85:
            self._fix(node, key, raw, guess, f"matched an existing {what}")
        else:
            self._issue(node, key, f"No {what} called '{raw}'", guess)

    def check_templates(self, node: dict[str, Any], key: str) -> None:
        raw = (node.get("config") or {}).get(key)
        if not isinstance(raw, str) or "{{" not in raw:
            return
        replaced = raw
        for path in _TEMPLATE.findall(raw):
            resolved, auto, _ = self.resolve_field(path)
            if resolved and resolved != path and auto:
                replaced = replaced.replace("{{" + path + "}}", "{{" + resolved + "}}").replace("{{ " + path + " }}", "{{" + resolved + "}}")
            elif resolved is None and not path.startswith(("entities.", "result.", "steps.", "trigger.", "flow.", "error.", "approval.")):
                self._issue(node, key, f"'{{{{{path}}}}}' does not refer to a known field")
        if replaced != raw:
            self._fix(node, key, raw, replaced, "fixed field references")


def review_graph(graph: dict[str, Any], catalog: dict[str, Any], custom_intent: bool = False) -> dict[str, Any]:
    graph = json.loads(json.dumps(graph))  # never mutate the caller's copy
    reviewer = _Reviewer(catalog, custom_intent)
    states = {}
    for s in catalog["states"]:
        states[_norm(s["code"])] = s["code"]
        states[_norm(s["label"])] = s["code"]
    users = {}
    for u in catalog["users"]:
        users[_norm(u["email"])] = u["email"]
        users[_norm(u["name"])] = u["email"]
    positions = {_norm(p): p for p in catalog["positions"]}
    channels = {_norm(c): c for c in catalog["channels"]}

    for node in graph.get("nodes") or []:
        node_type = node.get("type")
        config = node.setdefault("config", {})
        if node_type == "condition":
            operator = str(config.get("operator") or "eq")
            if operator not in flow_engine.OPERATORS:
                guess, _ = _best(operator, {o: o for o in flow_engine.OPERATORS}, cutoff=0.5)
                symbol = {">": "gt", ">=": "gte", "<": "lt", "<=": "lte", "=": "eq", "==": "eq", "!=": "ne", "is": "eq"}.get(operator.strip())
                if symbol or guess:
                    reviewer._fix(node, "operator", operator, symbol or guess, "known operator")
                    operator = symbol or guess
            definition = reviewer.check_field(node, "field", numeric=operator in _NUMERIC_OPS)
            if operator not in ("empty", "not_empty"):
                reviewer.check_value(node, "value", definition, multiple=operator == "in")
        elif node_type == "set_field":
            definition = reviewer.check_field(node, "field", settable=True)
            reviewer.check_value(node, "value", definition)
        elif node_type == "transition" and states:
            reviewer.check_choice(node, "to_state", states, "state")
        elif node_type == "assign":
            if config.get("to_position") and positions:
                reviewer.check_choice(node, "to_position", positions, "position")
            if config.get("to_user") and users:
                reviewer.check_choice(node, "to_user", users, "user")
        elif node_type == "notify":
            if config.get("channel"):
                reviewer.check_choice(node, "channel", channels, "channel")
            reviewer.check_templates(node, "message")
        elif node_type == "ai_decision":
            reviewer.check_templates(node, "question")
            if str(config.get("default", "")).lower() not in ("", "true", "false"):
                reviewer._issue(node, "default", "Default must be true or false", "false")
        elif node_type == "reject":
            reviewer.check_templates(node, "message")

    return {"graph": graph, "corrections": reviewer.corrections, "issues": reviewer.issues}


def catalog_for_flow(db: Session, tenant_id: int, flow_like) -> dict[str, Any]:
    return field_catalog(
        db, tenant_id, flow_like.entity_type,
        flow_like.trigger_intent_type if flow_like.trigger_type == "intent" else None,
        flow_like.required_fields if flow_like.trigger_type == "custom_intent" else None,
    )

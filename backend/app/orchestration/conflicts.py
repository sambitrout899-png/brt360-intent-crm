"""Conflicts - automations, actions and plan steps that pull against each other.

Scanned during a rehearsal and before a run (pre-run), and on demand in
Admin -> Conflicts:

  automations  two or more switched-on flows on the same trigger (the same
               action, before or after it; a record entering the same stage)
               that run the same action twice, set one field to different
               values, move the record to different stages, hand it to
               different people, or where one rejects what another acts on;
  actions      custom actions that answer to the same phrase in the intent
               bar - which one a request reaches is a coin toss;
  plan steps   the same step twice, approve and reject of the same thing, or
               a step an automation of an earlier step already does.

An administrator settles an automation conflict with a rule:
  order       the order the flows run in (the last one to set a field wins);
  mode        "all" - every flow runs, in that order; "first" - only the
              first whose condition holds runs (the others stand aside);
  conditions  per flow, run only when record.<field> <operator> <value>
              (the Flow Studio's condition operators).
The flow engine keeps to the rule from then on (flow_engine.flows_for_* and
run_flow -> gate). A phrase conflict is settled by naming the action that
keeps the phrase; the others stop answering to it. Plan-step conflicts are
settled in the plan itself (drop a step).

Rules live in the tenant's settings row (cm_settings.data_json, key
"conflict_rules", one indexed read); every save is also written to the audit
log, so earlier rules are never lost. A trigger with a single flow never
reads them - nothing changes for a tenant without conflicts.
"""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy.orm import Session

from app.models.comms import CmSetting
from app.models.flows import FlowDefinition
from app.timeutils import utcnow

KEY = "conflict_rules"
KIND = "CONFLICT-RULES"
MODES = ("all", "first")
_RANK = {"high": 3, "medium": 2, "low": 1}
# opposite decisions on the same kind of step
_DECISION_CODES = {"INT-17", "INT-06", "INT-36", "INT-23", "INT-18", "INT-25"}
_YES = {"approve", "approved", "feasible", "accept", "accepted", "yes"}
_NO = {"reject", "rejected", "not_feasible", "infeasible", "decline", "declined", "no"}


# ---------------------------------------------------------------- the rules


def _row(db: Session, tenant_id: int) -> CmSetting | None:
    return db.query(CmSetting).filter(CmSetting.tenant_id == tenant_id).first()


def rules(db: Session, tenant_id: int) -> dict[str, dict[str, Any]]:
    row = _row(db, tenant_id)
    try:
        stored = json.loads(row.data_json or "{}").get(KEY) if row else None
    except ValueError:
        stored = None
    return stored if isinstance(stored, dict) else {}


def _store(db: Session, tenant_id: int, user, all_rules: dict[str, Any], note: str) -> None:
    from app.data_intelligence import audit

    row = _row(db, tenant_id)
    if row is None:
        row = CmSetting(tenant_id=tenant_id, data_json="{}")
        db.add(row)
    try:
        current = json.loads(row.data_json or "{}")
    except ValueError:
        current = {}
    current[KEY] = all_rules
    row.data_json = json.dumps(current)
    log = audit.start_execution(db, tenant_id, f"[conflicts] {note}"[:4000], channel="conflicts")
    log.intent_type = KIND
    log.user_id = getattr(user, "id", None)
    audit.record_context(log, {"version": 1, "rules": all_rules})
    audit.complete(log, "executed")
    db.flush()


def group_key(flow: FlowDefinition) -> str | None:
    if flow.trigger_type == "intent" and flow.trigger_intent_type:
        return f"intent:{flow.trigger_intent_type}:{flow.timing or 'after'}"
    if flow.trigger_type == "state_enter" and flow.entity_type and flow.trigger_state:
        return f"state:{flow.entity_type}:{flow.trigger_state}"
    return None


def ordered(db: Session, tenant_id: int, key: str, flows: list[FlowDefinition]) -> list[FlowDefinition]:
    """The flows of one trigger in the order the admin set (id order otherwise).
    One flow - nothing to order, and the rules are not even read."""
    if len(flows) < 2:
        return flows
    rule = rules(db, tenant_id).get(key)
    if not rule or not isinstance(rule.get("order"), list):
        return flows
    rank = {int(i): n for n, i in enumerate(rule["order"]) if str(i).isdigit()}
    return sorted(flows, key=lambda f: (rank.get(f.id, len(rank)), f.id))


def _flow_ctx(db: Session, tenant_id: int, flow: FlowDefinition, ctx: dict[str, Any]) -> dict[str, Any]:
    """The context a flow's condition is checked against: its own record."""
    from app.crm_core import record_service
    from app.orchestration import flow_engine

    entity_type, record = flow_engine.resolve_target(db, tenant_id, flow, ctx)
    out = dict(ctx)
    if record is not None:
        out["record"] = record_service.snapshot(db, tenant_id, entity_type, record)
    return out


def _holds(condition: Any, ctx: dict[str, Any]) -> bool:
    from app.orchestration import flow_engine

    if not isinstance(condition, dict) or not condition.get("field"):
        return True
    return flow_engine.evaluate_condition(condition, ctx)[0]


def gate(db: Session, tenant_id: int, flow: FlowDefinition, ctx: dict[str, Any]) -> str | None:
    """Why this flow stands aside this time (the admin's rule), or None to run."""
    key = group_key(flow)
    if key is None:
        return None
    rule = rules(db, tenant_id).get(key)
    if not rule:
        return None
    conditions = rule.get("conditions") or {}
    own = conditions.get(str(flow.id))
    if own and not _holds(own, ctx):
        return f"Skipped - its condition ({_say(own)}) doesn't hold (Admin → Conflicts)"
    if rule.get("mode") == "first":
        order = [int(i) for i in rule.get("order") or [] if str(i).isdigit()]
        if flow.id in order:
            ahead = order[: order.index(flow.id)]
            if ahead:
                for other in db.query(FlowDefinition).filter(FlowDefinition.id.in_(ahead), FlowDefinition.tenant_id == tenant_id,
                                                              FlowDefinition.is_active.is_(True)).all():
                    if _holds(conditions.get(str(other.id)), _flow_ctx(db, tenant_id, other, ctx)):
                        return f"Skipped - “{other.name}” comes first and only one of them runs (Admin → Conflicts)"
    return None


def _say(condition: dict[str, Any]) -> str:
    words = {"eq": "is", "ne": "is not", "gt": ">", "gte": "≥", "lt": "<", "lte": "≤", "contains": "contains", "in": "is one of",
             "empty": "is empty", "not_empty": "is filled"}
    field = str(condition.get("field") or "").removeprefix("record.").replace("_", " ")
    op = words.get(str(condition.get("operator") or "eq"), str(condition.get("operator")))
    value = "" if condition.get("operator") in ("empty", "not_empty") else f" {condition.get('value')}"
    return f"{field} {op}{value}"


# ---------------------------------------------------------------- scanning automations


def _effects(flow: FlowDefinition) -> dict[str, Any]:
    from app.orchestration import flow_engine

    graph = flow_engine.load_graph(flow)
    out: dict[str, Any] = {"fields": {}, "states": set(), "actions": set(), "assigns": set(), "rejects": False, "conditional": False}
    for node in graph.get("nodes", []):
        config = node.get("config") or {}
        kind = node.get("type")
        if kind in ("condition", "ai_decision"):
            out["conditional"] = True
        elif kind == "set_field" and config.get("field"):
            out["fields"][str(config["field"]).removeprefix("record.")] = str(config.get("value"))
        elif kind == "transition" and config.get("to_state"):
            out["states"].add(str(config["to_state"]))
        elif kind == "action" and config.get("intent"):
            out["actions"].add(str(config["intent"]))
        elif kind == "assign":
            out["assigns"].add(str(config.get("to_user") or config.get("to_position") or config.get("to_department") or "?"))
        elif kind == "reject":
            out["rejects"] = True
    return out


def _trigger_words(db: Session, tenant_id: int, key: str) -> str:
    from app.intent_engine import intent_registry

    kind, a, b = key.split(":", 2)
    if kind == "intent":
        label = intent_registry.label_of(db, tenant_id, a) or a
        return f"{'Before' if b == 'before' else 'After'} “{label}”"
    return f"When a {a.replace('_', ' ')} becomes {b.replace('_', ' ')}"


def _clashes(db: Session, tenant_id: int, a: FlowDefinition, ea: dict, b: FlowDefinition, eb: dict) -> list[tuple[str, str, str]]:
    from app.intent_engine import intent_registry

    out = []
    for code in sorted(ea["actions"] & eb["actions"]):
        out.append(("high", "duplicate", f"both run “{intent_registry.label_of(db, tenant_id, code) or code}” - it would happen twice"))
    for field in sorted(set(ea["fields"]) & set(eb["fields"])):
        if ea["fields"][field] != eb["fields"][field]:
            out.append(("high", "field", f"they set {field.replace('_', ' ')} to “{ea['fields'][field]}” and “{eb['fields'][field]}” - the last one wins"))
    if ea["states"] and eb["states"] and ea["states"] != eb["states"]:
        out.append(("high", "state", f"they move the record to {', '.join(sorted(ea['states']))} and {', '.join(sorted(eb['states']))}"))
    if ea["assigns"] and eb["assigns"] and ea["assigns"] != eb["assigns"]:
        out.append(("medium", "assign", f"they hand the record to {', '.join(sorted(ea['assigns']))} and {', '.join(sorted(eb['assigns']))} - "
                                        "it ends up with whichever runs last"))
    acts = lambda e: e["fields"] or e["states"] or e["actions"] or e["assigns"]  # noqa: E731
    if ea["rejects"] != eb["rejects"] and (acts(eb) if ea["rejects"] else acts(ea)):
        stopper, doer = (a, b) if ea["rejects"] else (b, a)
        out.append(("medium", "reject", f"“{stopper.name}” can reject what “{doer.name}” acts on"))
    if out and (ea["conditional"] or eb["conditional"]):  # a branch may keep them apart - still worth a decision
        out = [("medium" if sev == "high" else sev, kind, text + " (on some branches)") for sev, kind, text in out]
    return out


def _automation_conflicts(db: Session, tenant_id: int, saved: dict[str, Any]) -> list[dict[str, Any]]:
    flows = (db.query(FlowDefinition)
             .filter(FlowDefinition.tenant_id == tenant_id, FlowDefinition.is_active.is_(True),
                     FlowDefinition.trigger_type.in_(("intent", "state_enter")))
             .order_by(FlowDefinition.id).all())
    groups: dict[str, list[FlowDefinition]] = {}
    for f in flows:
        key = group_key(f)
        if key:
            groups.setdefault(key, []).append(f)
    out = []
    for key, members in groups.items():
        if len(members) < 2:
            continue
        effects = {f.id: _effects(f) for f in members}
        reasons: list[tuple[str, str, str]] = []
        for i, a in enumerate(members):
            for b in members[i + 1:]:
                for sev, kind, text in _clashes(db, tenant_id, a, effects[a.id], b, effects[b.id]):
                    reasons.append((sev, kind, f"“{a.name}” and “{b.name}”: {text}"))
        if not reasons:
            continue
        members = ordered(db, tenant_id, key, members)
        rule = saved.get(key)
        resolved = bool(rule) and {f.id for f in members} <= {int(i) for i in rule.get("order") or [] if str(i).isdigit()}
        severity = max((r[0] for r in reasons), key=lambda s: _RANK[s])
        out.append({
            "key": key, "type": "automations", "severity": severity, "resolved": resolved,
            "title": f"{_trigger_words(db, tenant_id, key)}: {len(members)} automations pull against each other",
            "trigger": _trigger_words(db, tenant_id, key),
            "reasons": [r[2] for r in reasons], "kinds": sorted({r[1] for r in reasons}),
            "members": [{"type": "flow", "id": f.id, "name": f.name, "entity_type": f.entity_type,
                         "condition": (rule or {}).get("conditions", {}).get(str(f.id))} for f in members],
            "rule": rule,
            "ask": "Set which runs first, whether all of them run or only the first that applies, and when each one should run.",
        })
    return out


def _phrase_conflicts(db: Session, tenant_id: int) -> list[dict[str, Any]]:
    from app.orchestration import flow_engine

    by_phrase: dict[str, list[FlowDefinition]] = {}
    for f in flow_engine.custom_intent_flows(db, tenant_id):
        for p in {p.strip().lower() for p in (f.trigger_phrases or "").split(",") if p.strip()}:
            by_phrase.setdefault(p, []).append(f)
    out = []
    for phrase, members in sorted(by_phrase.items()):
        if len(members) < 2:
            continue
        out.append({
            "key": f"phrase:{phrase}", "type": "actions", "severity": "medium", "resolved": False,
            "title": f"{len(members)} actions answer to “{phrase}”",
            "trigger": f"Asked as “{phrase}” in the intent bar",
            "reasons": [f"“{phrase}” could reach {' or '.join(f'“{m.name}”' for m in members)} - which one is a coin toss"],
            "kinds": ["phrase"],
            "members": [{"type": "action", "id": m.id, "code": flow_engine.custom_intent_code(m), "name": m.name} for m in members],
            "rule": None,
            "ask": "Pick the action that keeps this phrase - the others stop answering to it.",
        })
    return out


def scan(db: Session, tenant_id: int) -> list[dict[str, Any]]:
    """Every conflict in the tenant's set-up, the most serious first."""
    saved = rules(db, tenant_id)
    found = _automation_conflicts(db, tenant_id, saved) + _phrase_conflicts(db, tenant_id)
    return sorted(found, key=lambda c: (c["resolved"], -_RANK[c["severity"]], c["key"]))


# ---------------------------------------------------------------- scanning a plan


def _decision(entities: dict[str, Any]) -> str | None:
    value = str(entities.get("decision") or "").strip().lower()
    return "yes" if value in _YES else "no" if value in _NO else None


def _same_record(a: dict[str, Any], b: dict[str, Any]) -> bool:
    ids_a = {k: v for k, v in a.items() if (k.endswith("_id") or k in ("entity_id", "entity_type")) and v not in (None, "")}
    ids_b = {k: v for k, v in b.items() if (k.endswith("_id") or k in ("entity_id", "entity_type")) and v not in (None, "")}
    shared = set(ids_a) & set(ids_b)
    # no ids given: both act on what the plan passes along - the same record
    return all(str(ids_a[k]) == str(ids_b[k]) for k in shared)


def scan_steps(db: Session, tenant_id: int, steps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Steps of one plan that clash with each other or with the automations."""
    from app.intent_engine import intent_registry
    from app.orchestration import flow_engine

    out: list[dict[str, Any]] = []
    numbered = [(int(s.get("n") or i), s) for i, s in enumerate(steps, 1) if s.get("intent_type")]
    label = lambda code: intent_registry.label_of(db, tenant_id, code) or code  # noqa: E731
    for i, (n, a) in enumerate(numbered):
        ea = a.get("entities") or {}
        for m, b in numbered[i + 1:]:
            if a["intent_type"] != b["intent_type"]:
                continue
            eb = b.get("entities") or {}
            if not _same_record(ea, eb):
                continue
            da, dbb = _decision(ea), _decision(eb)
            if a["intent_type"] in _DECISION_CODES and da and dbb and da != dbb:
                out.append({"key": f"steps:{n}:{m}:opposite", "type": "steps", "severity": "high", "resolved": False,
                            "title": f"Steps {n} and {m} decide “{label(a['intent_type'])}” both ways",
                            "reasons": [f"Step {n} says {ea.get('decision')}, step {m} says {eb.get('decision')} - on the same record"],
                            "kinds": ["opposite"], "steps": [n, m], "drop": m,
                            "ask": f"Keep one of them - drop step {m}, or step {n}."})
            elif {k: v for k, v in ea.items() if not k.endswith("_id")} == {k: v for k, v in eb.items() if not k.endswith("_id")}:
                out.append({"key": f"steps:{n}:{m}:twice", "type": "steps", "severity": "medium", "resolved": False,
                            "title": f"Steps {n} and {m} do the same thing",
                            "reasons": [f"“{label(a['intent_type'])}” twice with the same details"],
                            "kinds": ["twice"], "steps": [n, m], "drop": m, "ask": f"Drop step {m} unless you mean it twice."})
    # an automation that already runs a later step after an earlier one
    for i, (n, a) in enumerate(numbered):
        for flow in flow_engine.flows_for_intent(db, tenant_id, a["intent_type"], "after"):
            does = _effects(flow)["actions"]
            for m, b in numbered[i + 1:]:
                if b["intent_type"] in does:
                    out.append({"key": f"steps:{n}:{m}:auto:{flow.id}", "type": "steps", "severity": "low", "resolved": False,
                                "title": f"Step {m} is already done by an automation",
                                "reasons": [f"After step {n} (“{label(a['intent_type'])}”), the automation “{flow.name}” runs "
                                            f"“{label(b['intent_type'])}” by itself - step {m} would find it done or do it again"],
                                "kinds": ["automated"], "steps": [n, m], "drop": m, "flow_id": flow.id,
                                "ask": f"Drop step {m} and let the automation do it, or keep it - I skip it when it's already done."})
    return out


def relevant(db: Session, tenant_id: int, codes: list[str], flow_ids: list[int], steps: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    """The conflicts a rehearsal or a run touches: the automations of its
    actions (and any flow that ran), the actions it uses, and its own steps."""
    wanted_codes = {c for c in codes if c}
    wanted_flows = {int(f) for f in flow_ids if f}
    out = []
    for c in scan(db, tenant_id):
        kind, a, _b = (c["key"].split(":", 2) + ["", ""])[:3]
        if (kind == "intent" and a in wanted_codes) or any(m["id"] in wanted_flows for m in c["members"] if m["type"] == "flow") \
                or (kind == "phrase" and any(m.get("code") in wanted_codes for m in c["members"])):
            out.append(c)
    if steps:
        out.extend(scan_steps(db, tenant_id, steps))
    return out


# ---------------------------------------------------------------- settling


_OPERATORS = ("eq", "ne", "gt", "gte", "lt", "lte", "contains", "in", "empty", "not_empty")


def resolve(db: Session, tenant_id: int, user, key: str, order: list[int] | None = None, mode: str = "all",
            conditions: dict[str, Any] | None = None, winner: int | None = None) -> dict[str, Any]:
    """Saves the admin's decision on one conflict (and applies a phrase winner)."""
    if key.startswith("phrase:"):
        return _keep_phrase(db, tenant_id, user, key, winner)
    found = next((c for c in _automation_conflicts(db, tenant_id, rules(db, tenant_id)) if c["key"] == key), None)
    current = rules(db, tenant_id)
    members = [m["id"] for m in found["members"]] if found else [int(i) for i in (current.get(key) or {}).get("order") or []]
    if not members:
        raise ValueError("No conflict with that key - it may have been settled or its flows switched off")
    if mode not in MODES:
        raise ValueError(f"mode must be one of: {', '.join(MODES)}")
    order = [int(i) for i in (order or members)]
    if sorted(order) != sorted(members):
        raise ValueError("The order must list each automation of this conflict exactly once")
    clean: dict[str, Any] = {}
    for fid, cond in (conditions or {}).items():
        if int(fid) not in members:
            raise ValueError(f"Flow #{fid} is not part of this conflict")
        if not cond or not isinstance(cond, dict) or not str(cond.get("field") or "").strip():
            continue
        op = str(cond.get("operator") or "eq")
        if op not in _OPERATORS:
            raise ValueError(f"operator must be one of: {', '.join(_OPERATORS)}")
        field = str(cond["field"]).strip()
        clean[str(int(fid))] = {"field": field if "." in field else f"record.{field}", "operator": op, "value": cond.get("value")}
    rule = {"order": order, "mode": mode, "conditions": clean, "by": getattr(user, "name", None) or getattr(user, "email", None),
            "at": utcnow().replace(microsecond=0).isoformat()}
    current[key] = rule
    names = {f.id: f.name for f in db.query(FlowDefinition).filter(FlowDefinition.id.in_(order))}
    _store(db, tenant_id, user, current, f"{key}: " + " → ".join(names.get(i, f"#{i}") for i in order) + f" ({mode})")
    db.commit()
    return rule


def clear(db: Session, tenant_id: int, user, key: str) -> None:
    current = rules(db, tenant_id)
    if key not in current:
        raise ValueError("No rule with that key")
    current.pop(key)
    _store(db, tenant_id, user, current, f"{key}: rule cleared")
    db.commit()


def _keep_phrase(db: Session, tenant_id: int, user, key: str, winner: int | None) -> dict[str, Any]:
    phrase = key.split(":", 1)[1]
    found = next((c for c in _phrase_conflicts(db, tenant_id) if c["key"] == key), None)
    if found is None:
        raise ValueError("No action conflict on that phrase any more")
    ids = [m["id"] for m in found["members"]]
    if winner not in ids:
        raise ValueError("Pick one of the actions in this conflict")
    for flow in db.query(FlowDefinition).filter(FlowDefinition.id.in_(ids)):
        if flow.id == winner:
            continue
        kept = [p for p in (flow.trigger_phrases or "").split(",") if p.strip() and p.strip().lower() != phrase]
        fallback = flow.name.strip().lower()
        fallback = f"{fallback} {flow.id}" if fallback == phrase else fallback
        flow.trigger_phrases = ",".join(kept) or fallback  # an action always keeps a way to be asked for
    current = rules(db, tenant_id)
    _store(db, tenant_id, user, current, f"{key}: kept by flow #{winner}")
    db.commit()
    return {"winner": winner, "phrase": phrase}

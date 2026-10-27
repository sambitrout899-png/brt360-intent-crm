"""The Functional Map: a picture of every function point (intent) of the
platform, in lanes by business area, joined by the hand-offs that make up
the business processes - and, for a tenant, the levers behind it.

Two maps:
- The vanilla map is BRT's own. It is built from code only (the intent
  registry and the copilot's "What's next" graph) - never stored, never
  edited, the same for every tenant and always available.
- A tenant's map is the vanilla map as that tenant runs it: read live from
  the engines (which intents are switched on, their names and areas, the
  Flow Studio automations between them) plus what the tenant's administrators
  arranged on the picture (positions, the suggested next steps). Every save
  is a new row in the audit store (FUNCTION-MAP) - nothing is overwritten.

Re-orchestration: when an administrator changes the picture, the change is
turned into the engines' own settings, shown first and applied on consent:
- a function point taken off / put back  -> the intent is switched off / on
- renamed                                 -> the intent's label
- moved to another lane                   -> the intent's area (category)
- a "next" link drawn / removed           -> the "What's next" steps offered
                                             after an action runs
- an "auto" link drawn / removed          -> a Flow Studio automation (after A,
                                             run B on the same record) made /
                                             switched off (kept, never deleted)
- a condition on a link                   -> the next step is offered (or the
                                             automation runs) only when it holds
Automations made elsewhere in Flow Studio are shown, and changed there.

Conditions: a link may carry "when" = {field, operator, value} - field is
entities.<input>, result.<output> or record.<field> of the step it leaves,
operator one of the Flow Studio operators - or {"operator": "otherwise"}: offered
only when none of the step's conditional links holds. Once any link out of a
step has a condition, the map alone decides that step's next steps.
"""
from __future__ import annotations

import json
import re
from typing import Any

from sqlalchemy.orm import Session

KIND = "FUNCTION-MAP"
MARK = "[functional map]"  # description prefix of the automations the map makes
COL, ROW = 240, 74  # layout grid: one step to the next, one row in a lane

# The vanilla business processes - which function points take part in each.
PROCESSES: list[dict[str, Any]] = [
    {"key": "b2b_sto", "label": "B2B Sales to Order",
     "steps": ["INT-15", "INT-46", "INT-17", "INT-37", "INT-38", "INT-18", "INT-05", "INT-06", "INT-109", "INT-22", "INT-23", "INT-39", "INT-40",
               "INT-25", "INT-26", "INT-27", "INT-28"]},
    # B2C goes straight from the prospect to the order - no lead, opportunity, quote or Presales; the Order Module checks
    # feasibility; any channel (IVR, retail point, store, WhatsApp, SMS, USSD, web, app) - see b2c_order_service.py
    {"key": "b2c", "label": "B2C New Connect", "steps": ["INT-15", "INT-41", "INT-13", "INT-49", "INT-104", "INT-105", "INT-57", "INT-25",
                                                         "INT-26", "INT-27", "INT-28"]},
    {"key": "l2q", "label": "Lead to Quote", "steps": ["INT-35", "INT-04", "INT-16", "INT-47", "INT-05", "INT-19", "INT-116", "INT-30", "INT-06", "INT-20", "INT-44"]},
    {"key": "order_capture", "label": "Order Capture",
     "steps": ["INT-65", "INT-66", "INT-51", "INT-52", "INT-69", "INT-53", "INT-54", "INT-55", "INT-56", "INT-57", "INT-58", "INT-59",
               "INT-60", "INT-61", "INT-62", "INT-63", "INT-64", "INT-67", "INT-68", "INT-71"]},
    {"key": "fulfilment", "label": "Order Fulfilment", "steps": ["INT-24", "INT-25", "INT-117", "INT-118", "INT-45", "INT-26", "INT-27", "INT-28", "INT-07", "INT-14", "INT-102", "INT-98"]},
    {"key": "inlife", "label": "In-life Changes", "steps": ["INT-08", "INT-50", "INT-09", "INT-70", "INT-21"]},
    {"key": "assurance", "label": "Service Assurance", "steps": ["INT-10", "INT-11", "INT-99", "INT-12", "INT-95", "INT-96", "INT-97", "INT-78", "INT-80", "INT-81"]},
    {"key": "accounts", "label": "Account Setup", "steps": ["INT-72", "INT-73", "INT-75", "INT-77", "INT-79", "INT-74", "INT-82", "INT-83", "INT-100", "INT-101"]},
    {"key": "billing", "label": "Bill to Cash", "steps": ["INT-84", "INT-90", "INT-85", "INT-86", "INT-87", "INT-88", "INT-89", "INT-91", "INT-92"]},
    {"key": "fraud", "label": "Fraud", "steps": ["INT-93", "INT-94", "INT-76"]},
    {"key": "product", "label": "Product Lifecycle", "steps": ["INT-01", "INT-42", "INT-43", "INT-48", "INT-02", "INT-03"]},
    {"key": "workspace", "label": "Workspace & Records", "steps": ["INT-33", "INT-34", "INT-103", "INT-29", "INT-31", "INT-32", "INT-36"]},
]
# what an automatic step decides when it is a decision (the map's "auto" link means "and pass it")
_AUTO_DECISION: dict[str, str | None] = {"INT-18": "feasible", "INT-09": None}  # INT-09's "decision" is only a retention offer
RECORD_WORDS = {"party": "a customer", "lead": "a lead", "opportunity": "an opportunity", "quote": "a quote", "order": "an order",
                "ticket": "a ticket", "work_order": "a work order"}


# ---------------------------------------------------------------- vanilla


def _lanes() -> list[dict[str, str]]:
    from app.intent_engine.intent_registry import CATEGORIES

    return [{"key": k, "label": label} for k, label in CATEGORIES]


def _vanilla_edges() -> list[dict[str, Any]]:
    from app.intent_engine.copilot import _NEXT
    from app.intent_engine.intent_registry import BUILTIN

    return [{"source": a, "target": b, "kind": "next", "why": why}
            for a, steps in _NEXT.items() for b, why in steps if a != b and a in BUILTIN and b in BUILTIN]


def layout(nodes: list[dict[str, Any]], edges: list[dict[str, Any]]) -> dict[str, dict[str, int]]:
    """{code: {x, dy}} - steps left to right along the hand-offs (longest
    path), stacked in rows inside their lane."""
    codes = [n["code"] for n in nodes]
    rank = {c: 0 for c in codes}
    links = [(e["source"], e["target"]) for e in edges if e["source"] in rank and e["target"] in rank and e["source"] != e["target"]]
    for _ in range(len(codes)):  # relax; a loop stops growing after len(codes) rounds
        changed = False
        for a, b in links:
            if rank[b] < rank[a] + 1 and rank[a] + 1 < len(codes):
                rank[b] = rank[a] + 1
                changed = True
        if not changed:
            break
    linked = {c for pair in links for c in pair}
    out: dict[str, dict[str, int]] = {}
    lane_of = {n["code"]: n["lane"] for n in nodes}
    taken: dict[tuple[str, int], int] = {}
    for code in sorted(codes, key=lambda c: (rank[c], c not in linked, _num(c))):
        lane = lane_of[code]
        col = rank[code]
        if code not in linked:  # a step on its own: the least crowded column of its lane
            col = min(range(0, max(6, max(rank.values()) + 1)), key=lambda k: (taken.get((lane, k), 0), k))
        row = taken.get((lane, col), 0)
        taken[(lane, col)] = row + 1
        out[code] = {"x": 20 + col * COL, "dy": 12 + row * ROW}
    return out


def _num(code: str) -> int:
    try:
        return int(code.split("-", 1)[1])
    except (IndexError, ValueError):
        return 10_000


def module_view(db, tenant_id, graph):
    """Remove unrelated nodes AND their edges, process steps and empty lanes."""
    from app.licensing import intent_available
    nodes = [n for n in graph['nodes'] if intent_available(db, tenant_id, n['code'])]
    codes = {n['code'] for n in nodes}
    lanes = {n['lane'] for n in nodes}
    processes = [{**p, 'steps': [c for c in p['steps'] if c in codes]} for p in graph['processes']]
    return {**graph, 'nodes': nodes,
            'edges': [e for e in graph['edges'] if e['source'] in codes and e['target'] in codes],
            'lanes': [lane for lane in graph['lanes'] if lane['key'] in lanes],
            'processes': [p for p in processes if p['steps']]}


def vanilla(db=None, tenant_id=None) -> dict[str, Any]:
    """BRT's map - from code only, identical for every tenant."""
    from app.intent_engine.intent_registry import BUILTIN

    nodes = [{"code": m.code, "label": m.label, "default_label": m.label, "icon": m.icon, "lane": m.category, "default_lane": m.category,
              "description": m.description, "target": m.target, "segment": m.segment, "active": True, "kind": "builtin", "in_vanilla": True,
              "phrases": [], "examples": m.examples}
             for m in sorted(BUILTIN.values(), key=lambda m: _num(m.code))]
    edges = _vanilla_edges()
    places = layout(nodes, edges)
    for n in nodes:
        n.update(places[n["code"]])
    graph = {"lanes": _lanes(), "nodes": nodes, "edges": edges, "processes": PROCESSES, "vanilla": True}
    return module_view(db, tenant_id, graph) if db is not None else graph


# ---------------------------------------------------------------- the tenant's map


def saved(db: Session, tenant_id: int) -> dict[str, Any] | None:
    from app.models.intent_log import IntentExecutionLog

    row = (db.query(IntentExecutionLog)
           .filter(IntentExecutionLog.tenant_id == tenant_id, IntentExecutionLog.intent_type == KIND, IntentExecutionLog.status == "executed")
           .order_by(IntentExecutionLog.id.desc()).first())
    if row is None:
        return None
    try:
        value = json.loads(row.context_json)
    except (TypeError, ValueError):
        return None
    if not isinstance(value, dict) or value.get("version") != 1:
        return None
    return {**value, "id": row.id, "at": row.created_at.isoformat(timespec="seconds"), "user_id": row.user_id}


_FIELD = re.compile(r"^(entities|result|record)\.[A-Za-z_][A-Za-z0-9_.]{0,80}$")
OPERATOR_WORDS = {"eq": "is", "ne": "is not", "gt": "is more than", "gte": "is at least", "lt": "is less than", "lte": "is at most",
                  "contains": "contains", "in": "is one of", "empty": "is empty", "not_empty": "is filled in"}


def clean_when(raw: Any) -> dict[str, str] | None:
    """A link's condition in its stored shape, or None (always)."""
    from app.orchestration.flow_engine import OPERATORS

    if not isinstance(raw, dict):
        return None
    operator = str(raw.get("operator") or "eq")
    if operator == "otherwise":
        return {"operator": "otherwise"}
    field = str(raw.get("field") or "").strip()
    if operator not in OPERATORS or not _FIELD.match(field):
        return None
    value = "" if operator in ("empty", "not_empty") else str(raw.get("value") if raw.get("value") is not None else "").strip()[:200]
    return {"field": field, "operator": operator, "value": value}


def describe_when(when: dict[str, str] | None) -> str:
    """'when the decision is reject' - for the plan and the differences."""
    if not when:
        return ""
    if when.get("operator") == "otherwise":
        return "otherwise (when none of the other conditions holds)"
    scope, _, key = when["field"].partition(".")
    what = {"entities": "the input", "result": "the result", "record": "the record's"}[scope]
    words = OPERATOR_WORDS.get(when["operator"], when["operator"])
    tail = "" if when["operator"] in ("empty", "not_empty") else f" {when['value']}"
    return f"when {what} {key.replace('_', ' ')} {words}{tail}"


def _saved_from(db: Session, tenant_id: int, code: str) -> list[dict[str, Any]] | None:
    mine = saved(db, tenant_id)
    if mine is None or not isinstance(mine.get("edges"), list):
        return None
    return [e for e in mine["edges"] if isinstance(e, dict) and e.get("source") == code and e.get("target")]


def decides(db: Session, tenant_id: int, code: str) -> bool:
    """Whether the tenant's map has conditions on this step's next steps -
    then the map alone decides them (the built-in outcome rules step aside)."""
    return any(clean_when(e.get("when")) for e in _saved_from(db, tenant_id, code) or [])


def next_steps(db: Session, tenant_id: int, code: str, default: list[tuple[str, str]],
               facts: dict[str, Any] | None = None) -> list[tuple[str, str]]:
    """The "What's next" steps after an action: the tenant's map when it
    has one, else the vanilla ones. With facts ({entities, result, record}
    of the step that ran) a link with a condition is offered only when the
    condition holds; an "otherwise" link only when none of them does."""
    from app.orchestration.flow_engine import evaluate_condition

    links = _saved_from(db, tenant_id, code)
    if links is None:
        return list(default)
    if facts is None:
        return [(e["target"], e.get("why") or "") for e in links]
    held: dict[int, bool] = {}
    for i, e in enumerate(links):
        when = clean_when(e.get("when"))
        if when and when["operator"] != "otherwise":
            try:
                held[i] = evaluate_condition(when, facts)[0]
            except Exception:  # a broken condition never offers its step
                held[i] = False
    any_held = any(held.values())
    out = []
    for i, e in enumerate(links):
        when = clean_when(e.get("when"))
        if when is None or (when["operator"] == "otherwise" and not any_held) or held.get(i):
            out.append((e["target"], e.get("why") or ""))
    return out


def _graph_actions(flow) -> list[str]:
    try:
        graph = json.loads(flow.graph_json or "{}")
    except ValueError:
        return []
    return [str((n.get("config") or {}).get("intent")) for n in graph.get("nodes", [])
            if n.get("type") == "action" and (n.get("config") or {}).get("intent")]


def _graph_condition(flow) -> dict[str, str] | None:
    """The condition of a map-made automation (start -> condition -> run)."""
    try:
        graph = json.loads(flow.graph_json or "{}")
    except ValueError:
        return None
    for n in graph.get("nodes", []):
        if n.get("type") == "condition":
            config = n.get("config") or {}
            return clean_when({"field": config.get("field"), "operator": config.get("operator", "eq"), "value": config.get("value")})
    return None


def automations(db: Session, tenant_id: int) -> list[dict[str, Any]]:
    """Every "after A, run B" automation in Flow Studio, as map links."""
    from app.models.flows import FlowDefinition

    flows = (db.query(FlowDefinition)
             .filter(FlowDefinition.tenant_id == tenant_id, FlowDefinition.trigger_type == "intent", FlowDefinition.timing == "after")
             .order_by(FlowDefinition.id).all())
    out = []
    for flow in flows:
        by_map = (flow.description or "").startswith(MARK)
        when = _graph_condition(flow) if by_map else None
        for target in _graph_actions(flow):
            out.append({"source": flow.trigger_intent_type, "target": target, "kind": "auto", "flow_id": flow.id, "flow_name": flow.name,
                        "active": bool(flow.is_active), "by_map": by_map, **({"when": when} if when else {})})
    return out


def current(db: Session, tenant_id: int) -> dict[str, Any]:
    from app.intent_engine import intent_registry
    from app.models.auth import User

    served = intent_registry.served_segments(db, tenant_id)
    base = {n["code"]: n for n in vanilla()["nodes"]}
    nodes = []
    for e in intent_registry.all_intents(db, tenant_id):
        v = base.get(e["code"])
        nodes.append({"code": e["code"], "label": e["label"], "default_label": e["default_label"], "icon": e["icon"], "lane": e["category"],
                      "default_lane": v["lane"] if v else "custom", "description": e["description"], "target": e["target"],
                      "segment": e.get("segment"), "served": not e.get("segment") or e["segment"] in served, "active": bool(e["is_active"]),
                      "kind": e["kind"], "flow_id": e.get("flow_id"), "in_vanilla": v is not None,
                      "phrases": e.get("phrases", []), "examples": e.get("examples", [])})
    codes = {n["code"] for n in nodes}
    mine = saved(db, tenant_id)
    edges = mine["edges"] if mine and isinstance(mine.get("edges"), list) else _vanilla_edges()
    edges = [{"source": e["source"], "target": e["target"], "kind": "next", "why": e.get("why") or "",
              **({"when": clean_when(e.get("when"))} if clean_when(e.get("when")) else {})}
             for e in edges if isinstance(e, dict) and e.get("source") in codes and e.get("target") in codes]
    autos = [a for a in automations(db, tenant_id) if a["source"] in codes and a["target"] in codes]
    places = layout(nodes, edges + [a for a in autos if a["active"]])
    kept = (mine or {}).get("positions") or {}
    for n in nodes:
        spot = kept.get(n["code"]) if isinstance(kept.get(n["code"]), dict) else None
        n.update({"x": int(spot["x"]), "dy": int(spot["dy"])} if spot and "x" in spot and "dy" in spot else places[n["code"]])
    by = None
    if mine and mine.get("user_id"):
        user = db.get(User, mine["user_id"])
        by = user.name if user is not None else None
    out = {"lanes": _lanes(), "nodes": nodes, "edges": edges + autos, "processes": PROCESSES, "vanilla": False,
           "saved_at": mine["at"] if mine else None, "saved_by": by, "customized": mine is not None}
    out = module_view(db, tenant_id, out)
    out["differences"] = differences(out)
    return out


def differences(tenant_map: dict[str, Any]) -> list[dict[str, str]]:
    """How this tenant's map differs from BRT's vanilla map, in words."""
    van = vanilla()
    vnodes = {n["code"]: n for n in van["nodes"]}
    out: list[dict[str, str]] = []
    lanes = {lane["key"]: lane["label"] for lane in van["lanes"]}
    for n in tenant_map["nodes"]:
        v = vnodes.get(n["code"])
        if v is None:
            out.append({"kind": "added", "code": n["code"], "text": f"“{n['label']}” is your own function point ({n['code']})"})
            continue
        if not n["active"]:
            out.append({"kind": "off", "code": n["code"], "text": f"“{v['label']}” is switched off"})
        if n["label"] != v["label"]:
            out.append({"kind": "renamed", "code": n["code"], "text": f"“{v['label']}” is called “{n['label']}”"})
        if n["lane"] != v["lane"]:
            out.append({"kind": "moved", "code": n["code"], "text": f"“{n['label']}” is under {lanes.get(n['lane'], n['lane'])}, not {lanes.get(v['lane'], v['lane'])}"})
    names = {n["code"]: n["label"] for n in tenant_map["nodes"]}
    vlinks = {(e["source"], e["target"]) for e in van["edges"]}
    links = {(e["source"], e["target"]) for e in tenant_map["edges"] if e["kind"] == "next"}
    for a, b in sorted(links - vlinks):
        out.append({"kind": "link_added", "code": a, "text": f"After “{names.get(a, a)}”, “{names.get(b, b)}” is suggested next"})
    for a, b in sorted(vlinks - links):
        if a in names and b in names:
            out.append({"kind": "link_removed", "code": a, "text": f"“{names[b]}” is no longer suggested after “{names[a]}”"})
    for e in tenant_map["edges"]:
        if e["kind"] == "next" and e.get("when"):
            out.append({"kind": "conditioned", "code": e["source"],
                        "text": f"After “{names.get(e['source'], e['source'])}”, “{names.get(e['target'], e['target'])}” is suggested only {describe_when(e['when'])}"})
    for e in tenant_map["edges"]:
        if e["kind"] == "auto" and e["active"]:
            out.append({"kind": "automated", "code": e["source"],
                        "text": f"After “{names.get(e['source'], e['source'])}”, “{names.get(e['target'], e['target'])}” runs automatically ({e['flow_name']})"})
    return out


# ---------------------------------------------------------------- re-orchestration


def _auto_check(db: Session, tenant_id: int, a: dict[str, Any], b: dict[str, Any]) -> tuple[str | None, str | None, dict[str, Any]]:
    """(problem, warning, what B is given) for "after A, run B automatically"."""
    from app.crm_core.lifecycle_service import ENTITY_MODELS
    from app.experience.routes_intent import _STEP_BUILDERS, _required_fields
    from app.orchestration.flow_engine import _CHILDREN, _PARENTS

    ta, tb = a.get("target"), b.get("target")
    if a["kind"] != "builtin":
        return f"“{a['label']}” is one of your own actions - automate what follows it inside its flow in Flow Studio", None, {}
    if ta not in ENTITY_MODELS:
        return (f"“{a['label']}” doesn't work on one customer, lead, opportunity, quote or order, so nothing can run automatically after it"
                f" - keep it as a suggested next step"), None, {}
    if b["code"] not in _STEP_BUILDERS:
        return f"“{b['label']}” can't run inside an automation", None, {}
    model = ENTITY_MODELS[ta]

    def derivable(key: str) -> bool:
        return key == f"{ta}_id" or key == "address" or (ta, key) in _PARENTS or (ta, key) in _CHILDREN or hasattr(model, key)

    if tb is None:
        return f"“{b['label']}” isn't about one record, so it can't follow “{a['label']}” automatically", None, {}
    if tb != "@entity_type" and tb != ta and not derivable(f"{tb}_id"):
        return f"“{b['label']}” needs {RECORD_WORDS.get(tb, tb)}, which {RECORD_WORDS.get(ta, ta)} from “{a['label']}” doesn't lead to", None, {}
    entities: dict[str, Any] = {}
    if "decision" in {f["key"] for f in b.get("fields", [])} and _AUTO_DECISION.get(b["code"], "approve"):
        entities["decision"] = _AUTO_DECISION.get(b["code"], "approve")
    given = {"entity_type", "entity_id", *entities}
    required = [f for f in _required_fields(db, tenant_id, b["code"])[0] if " or " not in f]
    unknown = [f.replace("_", " ") for f in required if f not in given and not derivable(f)]
    notes = []
    if unknown:
        notes.append(f"“{b['label']}” also needs {', '.join(unknown)}, which the record doesn't give - when it is missing the automatic step is "
                     f"skipped and “{a['label']}” still completes")
    if entities.get("decision"):
        notes.append(f"It decides “{entities['decision']}” on its own - whoever runs “{a['label']}” also passes this step")
    return None, ". ".join(notes) or None, entities


def _desired_nodes(desired: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {str(n["code"]): n for n in desired.get("nodes", []) if isinstance(n, dict) and n.get("code")}


def plan(db: Session, tenant_id: int, desired: dict[str, Any]) -> dict[str, Any]:
    """What saving this picture would change in the engines - nothing is changed."""
    from app.intent_engine import intent_registry

    now = current(db, tenant_id)
    lanes = {lane["key"]: lane["label"] for lane in now["lanes"]}
    entries = {e["code"]: e for e in intent_registry.all_intents(db, tenant_id)}
    nodes = {n["code"]: n for n in now["nodes"]}
    want = _desired_nodes(desired)
    changes: list[dict[str, Any]] = []
    problems: list[dict[str, Any]] = []
    for code, n in nodes.items():
        d = want.get(code)
        active = bool(d.get("active", True)) if d is not None else False
        if active != n["active"]:
            if active:
                changes.append({"kind": "on", "code": code, "engine": "Intents", "text": f"Switch “{n['label']}” back on - it returns to the intent bar"})
            else:
                fed = [e for e in now["edges"] if e["kind"] == "auto" and e["active"] and e["target"] == code]
                tail = f"; {len(fed)} automation(s) that run it will skip it" if fed else ""
                changes.append({"kind": "off", "code": code, "engine": "Intents",
                                "text": f"Switch off “{n['label']}” - nobody can run it; it stays on the map, greyed, to switch back on{tail}"})
        if d is None:
            continue
        label = str(d.get("label") or "").strip()[:150]
        if label and label != n["label"]:
            changes.append({"kind": "rename", "code": code, "engine": "Intents", "label": label,
                            "text": f"Rename “{n['label']}” to “{label}” - the intent bar, forms and lists use the new name"})
        lane = str(d.get("lane") or n["lane"])
        if lane != n["lane"]:
            if lane not in lanes:
                problems.append({"code": code, "text": f"“{n['label']}”: there is no area called '{lane}'"})
            else:
                changes.append({"kind": "move", "code": code, "engine": "Intents", "lane": lane,
                                "text": f"Move “{n['label']}” from {lanes.get(n['lane'], n['lane'])} to {lanes[lane]} - it is listed there in the intent bar"})
    for code in want:
        if code not in nodes:
            problems.append({"code": code, "text": f"{code} is not a function point of this platform"})

    def name(code: str) -> str:
        return (want.get(code) or {}).get("label") or nodes[code]["label"]

    links = [e for e in desired.get("edges", []) if isinstance(e, dict) and e.get("source") in nodes and e.get("target") in nodes
             and e.get("source") != e.get("target")]
    old_next = {(e["source"], e["target"]): e for e in now["edges"] if e["kind"] == "next"}
    new_next = {(e["source"], e["target"]): e for e in links if e.get("kind", "next") == "next"}
    for pair in new_next.keys() - old_next.keys():
        changes.append({"kind": "link", "source": pair[0], "target": pair[1], "engine": "What's next",
                        "text": f"After “{name(pair[0])}”, offer “{name(pair[1])}” as the next step"})
    for pair in old_next.keys() - new_next.keys():
        changes.append({"kind": "unlink", "source": pair[0], "target": pair[1], "engine": "What's next",
                        "text": f"Stop offering “{name(pair[1])}” after “{name(pair[0])}”"})
    for pair in new_next.keys() & old_next.keys():
        if (new_next[pair].get("why") or "") != (old_next[pair].get("why") or ""):
            changes.append({"kind": "reword", "source": pair[0], "target": pair[1], "engine": "What's next",
                            "text": f"Reword the suggestion “{name(pair[1])}” after “{name(pair[0])}”: “{new_next[pair].get('why') or ''}”"})
    for pair, e in new_next.items():
        when, was = clean_when(e.get("when")), clean_when((old_next.get(pair) or {}).get("when"))
        if when != was:
            changes.append({"kind": "condition", "source": pair[0], "target": pair[1], "engine": "What's next",
                            "text": (f"After “{name(pair[0])}”, offer “{name(pair[1])}” only {describe_when(when)}" if when
                                     else f"After “{name(pair[0])}”, always offer “{name(pair[1])}” (no condition)")})
        if e.get("when") and when is None:
            problems.append({"source": pair[0], "target": pair[1],
                             "text": f"The condition on “{name(pair[0])}” → “{name(pair[1])}” is incomplete - pick a field and a test; it is saved without one"})
    if [(e["source"], e["target"]) for e in links if e.get("kind", "next") == "next"] != [(e["source"], e["target"]) for e in now["edges"] if e["kind"] == "next"] \
            and not any(c["engine"] == "What's next" for c in changes):
        changes.append({"kind": "reorder", "engine": "What's next", "text": "Change the order the next steps are offered in"})
    live_autos = [e for e in now["edges"] if e["kind"] == "auto"]
    new_auto = {(e["source"], e["target"]) for e in links if e.get("kind") == "auto"}
    auto_when: dict[tuple[str, str], dict[str, str] | None] = {}
    for e in links:
        if e.get("kind") == "auto":
            when = clean_when(e.get("when"))
            if when and when["operator"] == "otherwise":
                problems.append({"source": e["source"], "target": e["target"],
                                 "text": f"“{name(e['source'])}” → “{name(e['target'])}”: an automation needs a real condition, not “otherwise” - it runs always"})
                when = None
            auto_when[(e["source"], e["target"])] = when
    on_auto = {(e["source"], e["target"]) for e in live_autos if e["active"]}
    for pair in sorted(new_auto - on_auto):
        problem, warning, given = _auto_check(db, tenant_id, {**nodes[pair[0]], **entries.get(pair[0], {}), "label": name(pair[0])},
                                       {**nodes[pair[1]], **entries.get(pair[1], {}), "label": name(pair[1])})
        if problem:
            problems.append({"source": pair[0], "target": pair[1], "text": problem})
            continue
        again = next((e for e in live_autos if (e["source"], e["target"]) == pair and e["by_map"]), None)
        when = auto_when.get(pair)
        changes.append({"kind": "automate", "source": pair[0], "target": pair[1], "engine": "Flow Studio", "warning": warning,
                        "entities": given, "flow_id": again["flow_id"] if again else None, "when": when,
                        "text": (f"Automate: whenever “{name(pair[0])}” runs, “{name(pair[1])}” runs straight after on the same record"
                                 + (f" - only {describe_when(when)}" if when else "")
                                 + (f" (switch “{again['flow_name']}” back on)" if again else " (a new Flow Studio automation)"))})
    for e in live_autos:
        pair = (e["source"], e["target"])
        if e["active"] and e["by_map"] and pair in new_auto and auto_when.get(pair) != clean_when(e.get("when")):
            when = auto_when.get(pair)
            changes.append({"kind": "reautomate", "source": pair[0], "target": pair[1], "engine": "Flow Studio", "flow_id": e["flow_id"], "when": when,
                            "text": (f"“{e['flow_name']}”: run “{name(pair[1])}” only {describe_when(when)}" if when
                                     else f"“{e['flow_name']}”: run “{name(pair[1])}” every time (no condition)")})
        elif e["active"] and not e["by_map"] and pair in new_auto and pair in auto_when and auto_when[pair] is not None:
            problems.append({"source": pair[0], "target": pair[1],
                             "text": f"“{e['flow_name']}” was made in Flow Studio - set its condition there"})
    for e in live_autos:
        pair = (e["source"], e["target"])
        if e["active"] and pair not in new_auto:
            if e["by_map"]:
                changes.append({"kind": "unautomate", "source": pair[0], "target": pair[1], "engine": "Flow Studio", "flow_id": e["flow_id"],
                                "text": f"Stop running “{name(pair[1])}” automatically after “{name(pair[0])}” - “{e['flow_name']}” is switched off (kept)"})
            else:
                problems.append({"source": pair[0], "target": pair[1],
                                 "text": f"“{e['flow_name']}” was made in Flow Studio - change or switch it off there"})
    return {"changes": changes, "problems": problems, "layout_only": not changes}


def _setting(db: Session, tenant_id: int, code: str):
    from app.models.intent_catalog import IntentSetting

    row = db.query(IntentSetting).filter(IntentSetting.tenant_id == tenant_id, IntentSetting.code == code).first()
    if row is None:
        row = IntentSetting(tenant_id=tenant_id, code=code)
        db.add(row)
        db.flush()  # a second change to the same intent finds it
    return row


def _automation_graph(label: str, target: str, entities: dict[str, Any], when: dict[str, str] | None) -> str:
    """start -> run [-> end], with a condition in front of the run when the link has one."""
    from app.orchestration import flow_ai

    nodes = [{"id": "start", "type": "start", "label": "Start", "config": {}},
             {"id": "run", "type": "action", "label": label, "config": {"intent": target, "entities": entities, "on_error": "skip"}},
             {"id": "end", "type": "end", "label": "End", "config": {}}]
    edges = [{"id": "e1", "source": "start", "target": "run", "label": ""}, {"id": "e2", "source": "run", "target": "end", "label": ""}]
    if when:
        nodes.insert(1, {"id": "check", "type": "condition", "label": describe_when(when).capitalize()[:120],
                         "config": {"field": when["field"], "operator": when["operator"], "value": when["value"]}})
        edges = [{"id": "e1", "source": "start", "target": "check", "label": ""},
                 {"id": "e3", "source": "check", "target": "run", "label": "true"},
                 {"id": "e4", "source": "check", "target": "end", "label": "false"},
                 {"id": "e2", "source": "run", "target": "end", "label": ""}]
    return json.dumps(flow_ai.layout({"nodes": nodes, "edges": edges}))


def _recondition(flow, when: dict[str, str] | None) -> None:
    """Rewrite a map-made automation's condition, keeping what it runs."""
    try:
        graph = json.loads(flow.graph_json or "{}")
    except ValueError:
        graph = {}
    run = next((n for n in graph.get("nodes", []) if n.get("type") == "action"), None)
    if run is None:
        return
    config = run.get("config") or {}
    flow.graph_json = _automation_graph(run.get("label") or "", config.get("intent"), dict(config.get("entities") or {}), when)


def _automate(db: Session, tenant_id: int, change: dict[str, Any], names: dict[str, str], targets: dict[str, Any]) -> int:
    from app.models.flows import FlowDefinition

    when = clean_when(change.get("when"))
    if change.get("flow_id"):
        flow = db.get(FlowDefinition, change["flow_id"])
        flow.is_active = True
        if _graph_condition(flow) != when:
            _recondition(flow, when)
        return flow.id
    a, b = change["source"], change["target"]
    entities = dict(change.get("entities") or {})
    flow = FlowDefinition(tenant_id=tenant_id, name=f"Map: after {names[a]} → {names[b]}"[:150],
                          description=f"{MARK} Drawn on the Functional Map: whenever “{names[a]}” runs, “{names[b]}” runs straight after"
                                      + (f" - only {describe_when(when)}." if when else "."),
                          entity_type=targets[a], trigger_type="intent", trigger_intent_type=a, timing="after",
                          graph_json=_automation_graph(names[b], b, entities, when), is_active=True)
    db.add(flow)
    db.flush()
    return flow.id


def apply(db: Session, tenant_id: int, user_id: int | None, desired: dict[str, Any]) -> dict[str, Any]:
    """Applies the plan (all together), then records the picture."""
    from app.data_intelligence import audit
    from app.intent_engine import intent_registry
    from app.models.flows import FlowDefinition

    from app.licensing import intent_available
    from fastapi import HTTPException
    supplied = {n.get("code") for n in desired.get("nodes", [])} | {e.get(k) for e in desired.get("edges", []) for k in ("source", "target")}
    if any(code and not intent_available(db, tenant_id, code) for code in supplied):
        raise HTTPException(403, "The map contains functions outside the selected modules; refresh it before saving")
    found = plan(db, tenant_id, desired)
    entries = {e["code"]: e for e in intent_registry.all_intents(db, tenant_id)}
    want = _desired_nodes(desired)
    names = {c: (want.get(c) or {}).get("label") or e["label"] for c, e in entries.items()}
    targets = {c: e["target"] for c, e in entries.items()}
    done: list[dict[str, Any]] = []
    try:
        for c in found["changes"]:
            if c["kind"] in ("on", "off"):
                _setting(db, tenant_id, c["code"]).is_active = c["kind"] == "on"
            elif c["kind"] == "rename":
                default = entries[c["code"]]["default_label"]
                _setting(db, tenant_id, c["code"]).label = None if c["label"] == default else c["label"]
            elif c["kind"] == "move":
                _setting(db, tenant_id, c["code"]).category = c["lane"]
            elif c["kind"] == "automate":
                c["flow_id"] = _automate(db, tenant_id, c, names, targets)
            elif c["kind"] == "reautomate":
                flow = db.get(FlowDefinition, c["flow_id"])
                if flow is not None and flow.tenant_id == tenant_id:
                    _recondition(flow, clean_when(c.get("when")))
            elif c["kind"] == "unautomate":
                flow = db.get(FlowDefinition, c["flow_id"])
                if flow is not None and flow.tenant_id == tenant_id:
                    flow.is_active = False
            done.append(c)
        db.flush()
        nodes = {n["code"] for n in current(db, tenant_id)["nodes"]}
        edges = [{"source": e["source"], "target": e["target"], "why": str(e.get("why") or "")[:200],
                  **({"when": clean_when(e.get("when"))} if clean_when(e.get("when")) else {})}
                 for e in desired.get("edges", []) if isinstance(e, dict) and e.get("kind", "next") == "next"
                 and e.get("source") in nodes and e.get("target") in nodes and e.get("source") != e.get("target")]
        positions = {code: {"x": int(n.get("x", 0)), "dy": max(0, int(n.get("dy", 0)))} for code, n in want.items()
                     if code in nodes and isinstance(n.get("x"), (int, float)) and isinstance(n.get("dy"), (int, float))}
        # Saving the visible module must not erase another module's saved layout.
        previous = saved(db, tenant_id) or {"edges": _vanilla_edges(), "positions": {}}
        edges += [e for e in previous.get('edges', [])
                  if e.get('source') not in nodes or e.get('target') not in nodes]
        positions = {**{k: v for k, v in previous.get('positions', {}).items() if k not in nodes}, **positions}
        log = audit.start_execution(db, tenant_id, f"[functional map] {len(done)} change(s)", channel="function_map")
        log.intent_type = KIND
        log.user_id = user_id
        audit.record_context(log, {"version": 1, "edges": edges, "positions": positions})
        audit.record_plan(log, done)
        audit.complete(log, "executed")
        db.commit()
    except Exception:
        db.rollback()
        raise
    return {"applied": done, "problems": found["problems"], "map": current(db, tenant_id)}


def restore_draft(db: Session, tenant_id: int) -> dict[str, Any]:
    """The vanilla map as a draft of this tenant's map: BRT's names, areas,
    steps and next steps; the map's own automations off. The tenant's own
    function points and Flow Studio automations stay as they are. Nothing is
    changed until it is saved."""
    van = vanilla(db, tenant_id)
    now = current(db, tenant_id)
    nodes = [{k: n[k] for k in ("code", "label", "lane", "active", "x", "dy")} for n in van["nodes"]]
    vcodes = {n["code"] for n in nodes}
    own = [n for n in now["nodes"] if n["code"] not in vcodes]
    bottom = 12 + max((n["dy"] for n in nodes if n["lane"] == "custom"), default=-ROW) + ROW
    for i, n in enumerate(own):  # your own function points keep their place in their area
        nodes.append({"code": n["code"], "label": n["label"], "lane": n["lane"], "active": n["active"], "x": 20 + (i % 8) * COL,
                      "dy": n["dy"] if n["lane"] != "custom" else bottom + (i // 8) * ROW})
    edges = [*van["edges"], *[e for e in now["edges"] if e["kind"] == "auto" and e["active"] and not e["by_map"]]]
    return {**now, "nodes": [{**next(x for x in now["nodes"] if x["code"] == n["code"]), **n} for n in nodes], "edges": edges}

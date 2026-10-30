"""The Functional Map: a picture of every function point (intent) of the
platform, in lanes by business area, joined by the hand-offs that make up
the business processes - and, for a tenant, the levers behind it.

The maps:
- The vanilla (original) map is BRT's own. It is built from code only (the
  intent registry and the copilot's "What's next" graph) - never stored,
  never edited by a tenant, the same for every tenant and always available.
  A BRT release that changes it reaches every tenant.
- A tenant's map is the vanilla map as that tenant runs it: read live from
  the engines (which intents are switched on, their names and areas, the
  Flow Studio automations between them) plus what the tenant's administrators
  arranged (positions, the suggested next steps). It is read-only.
- Versions (models/function_map.py): a tenant customizes by copying the
  original, the map in use or another version into a draft, working on it
  and activating it. Each version remembers the release it was reconciled
  with ("base"), so BRT's later hand-offs merge in without undoing the
  tenant's own changes (release_merge). Every activation is a new row in the
  audit store (FUNCTION-MAP) - nothing is overwritten.

Re-orchestration: activating a version turns its picture into the engines'
own settings, shown first and applied on consent:
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

from app.db import cached, forget

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
            'processes': [p for p in processes if p['steps'] or p.get('tenant')]}  # a tenant's own process shows even before it has steps


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


def merged_processes(overrides: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    """BRT's processes with a tenant's own on top: an override with a BRT key
    changes that process's steps (a process appended to), any other key is a
    process of the tenant's own ("tenant": true)."""
    out = [dict(p) for p in PROCESSES]
    keys = {p["key"]: p for p in out}
    for o in overrides or []:
        if not isinstance(o, dict) or not o.get("key") or not isinstance(o.get("steps"), list):
            continue
        steps = [str(s) for s in o["steps"]]
        if o["key"] in keys:
            keys[o["key"]]["steps"] = steps
        else:
            out.append({"key": str(o["key"]), "label": str(o.get("label") or o["key"]), "steps": steps, "tenant": True})
    return out


# ---------------------------------------------------------------- the tenant's map


def saved(db: Session, tenant_id: int) -> dict[str, Any] | None:
    """The tenant's saved map - read once per request (it is asked for every
    action's next steps); saving a new one forgets it (save_map)."""
    return cached(db, ("function_map", tenant_id), (), lambda: _load_saved(db, tenant_id))


def _load_saved(db: Session, tenant_id: int) -> dict[str, Any] | None:
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
    if not isinstance(value, dict) or value.get("version") not in (1, 2):
        return None
    if isinstance(value.get("edges"), list) and value.get("base") is not None:
        value["edges"] = release_merge(value["edges"], value["base"])[0]  # BRT's releases since it was activated
    return {**value, "id": row.id, "at": row.created_at.isoformat(timespec="seconds"), "user_id": row.user_id}


def original_pairs() -> list[list[str]]:
    """BRT's original hand-offs as they are in this release - what a tenant's
    picture is reconciled with (its "base")."""
    return [[e["source"], e["target"]] for e in _vanilla_edges()]


def release_merge(edges: list[dict[str, Any]], base: list | None) -> tuple[list[dict[str, Any]], list[str]]:
    """A tenant's links brought up to BRT's current release, keeping the
    tenant's own changes: a hand-off BRT added since `base` is added, one BRT
    retired since is dropped; links the tenant added or removed stay as the
    tenant left them. (links, what changed in words)."""
    if base is None:
        return edges, []
    from app.intent_engine.intent_registry import BUILTIN

    now = {(e["source"], e["target"]): e for e in _vanilla_edges()}
    was = {(p[0], p[1]) for p in base if isinstance(p, (list, tuple)) and len(p) == 2}
    gone = was - now.keys()
    label = lambda c: BUILTIN[c].label if c in BUILTIN else c  # noqa: E731
    notes = [f"BRT no longer suggests “{label(b)}” after “{label(a)}”" for a, b in sorted(gone)
             if any(e.get("kind", "next") == "next" and (e["source"], e["target"]) == (a, b) for e in edges)]
    out = [e for e in edges if not (e.get("kind", "next") == "next" and (e["source"], e["target"]) in gone)]
    have = {(e["source"], e["target"]) for e in out if e.get("kind", "next") == "next"}
    for pair in sorted(now.keys() - was - have):
        out.append({"source": pair[0], "target": pair[1], "kind": "next", "why": now[pair].get("why") or ""})
        notes.append(f"New from BRT: “{label(pair[1])}” is suggested after “{label(pair[0])}”")
    return out, notes


# what a condition tests: the step's input, its result, its record, the customer behind it, or a record above it
_FIELD = re.compile(r"^(entities|result|record|customer|parent\.(?:lead|opportunity|quote))\.[A-Za-z_][A-Za-z0-9_.]{0,80}$")
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
    if scope == "parent":
        parent, _, key = key.partition(".")
        what = f"the {parent}'s"
    else:
        what = {"entities": "the input", "result": "the result", "record": "the record's", "customer": "the customer's"}[scope]
    words = OPERATOR_WORDS.get(when["operator"], when["operator"])
    tail = "" if when["operator"] in ("empty", "not_empty") else f" {when['value']}"
    return f"when {what} {key.replace('_', ' ')} {words}{tail}"


def _saved_from(db: Session, tenant_id: int, code: str) -> list[dict[str, Any]] | None:
    mine = saved(db, tenant_id)
    if mine is None or not isinstance(mine.get("edges"), list):
        return None
    return [e for e in mine["edges"] if isinstance(e, dict) and e.get("source") == code and e.get("target")]


def rewired(db: Session, tenant_id: int, code: str) -> bool:
    """Whether the tenant's map in use changed this step's next steps from
    BRT's original - then the map, not the built-in outcome rules, says where
    a successful outcome goes (a failure still takes its built-in path)."""
    mine = _saved_from(db, tenant_id, code)
    if mine is None:
        return False
    original = {e["target"] for e in _vanilla_edges() if e["source"] == code}
    return {e["target"] for e in mine} != original


def rewired_steps(db: Session, tenant_id: int, code: str) -> list[tuple[str, str, str]] | None:
    """The map in use's next steps after `code` when it re-wired them (no
    condition decides - those need the step's facts), as (code, why, the
    step's name); None = BRT's built-in suggestions stand. Customer 360 and a
    record's buttons follow these."""
    from app.intent_engine import intent_registry

    if not rewired(db, tenant_id, code):
        return None
    return [(e["target"], e.get("why") or "", intent_registry.label_of(db, tenant_id, e["target"]) or e["target"])
            for e in _saved_from(db, tenant_id, code) or [] if not clean_when(e.get("when"))]


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
    overrides = (mine or {}).get("processes") or []
    out = {"lanes": _lanes(), "nodes": nodes, "edges": edges + autos, "processes": merged_processes(overrides), "process_overrides": overrides,
           "vanilla": False, "saved_at": mine["at"] if mine else None, "saved_by": by, "customized": mine is not None}
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
        if e["kind"] == "next" and clean_when(e.get("when")):
            out.append({"kind": "conditioned", "code": e["source"],
                        "text": f"After “{names.get(e['source'], e['source'])}”, “{names.get(e['target'], e['target'])}” is suggested only {describe_when(clean_when(e['when']))}"})
    for e in tenant_map["edges"]:
        if e["kind"] == "auto" and e["active"]:
            out.append({"kind": "automated", "code": e["source"],
                        "text": f"After “{names.get(e['source'], e['source'])}”, “{names.get(e['target'], e['target'])}” runs automatically ({e['flow_name']})"})
    return out


# ---------------------------------------------------------------- re-orchestration


def _auto_check(db: Session, tenant_id: int, a: dict[str, Any], b: dict[str, Any]) -> tuple[str | None, str | None, dict[str, Any]]:
    """(problem, warning, what B is given) for "after A, run B automatically"."""
    from app.crm_core.lifecycle_service import ENTITY_MODELS
    from app import capabilities
    from app.intent_engine.intent_registry import requirements
    from app.orchestration.flow_engine import _CHILDREN, _PARENTS

    ta, tb = a.get("target"), b.get("target")
    if a["kind"] != "builtin":
        return f"“{a['label']}” is one of your own actions - automate what follows it inside its flow in Flow Studio", None, {}
    if ta not in ENTITY_MODELS:
        return (f"“{a['label']}” doesn't work on one customer, lead, opportunity, quote or order, so nothing can run automatically after it"
                f" - keep it as a suggested next step"), None, {}
    if not capabilities.has_steps(b["code"]):
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
    required = [f for f in requirements(db, tenant_id, b["code"])[0] if " or " not in f]
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
        # base: the release these links were reconciled with - later releases merge in (release_merge)
        processes = desired["processes"] if isinstance(desired.get("processes"), list) else previous.get("processes") or []
        audit.record_context(log, {"version": 2, "edges": edges, "positions": positions, "base": original_pairs(), "processes": processes})
        audit.record_plan(log, done)
        audit.complete(log, "executed")
        forget(db, ("function_map", tenant_id))
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
    return {**now, "nodes": [{**next(x for x in now["nodes"] if x["code"] == n["code"]), **n} for n in nodes], "edges": edges,
            "processes": van["processes"], "process_overrides": []}


# ---------------------------------------------------------------- versions
#
# A tenant never edits the map it runs (current) - it makes versions, works on
# drafts and activates one; activating is plan + apply of that version's picture.


class VersionError(ValueError):
    pass


def _picture_of(m: dict[str, Any]) -> dict[str, Any]:
    """A map as stored in a version: what the editor changes, nothing more."""
    # a condition is kept as drawn - an incomplete one is reported when the version is activated (plan)
    return {"nodes": [{k: n[k] for k in ("code", "label", "lane", "active", "x", "dy")} for n in m["nodes"]],
            "edges": [{"source": e["source"], "target": e["target"], "kind": e.get("kind", "next"), "why": e.get("why") or "",
                       **({"when": e["when"]} if isinstance(e.get("when"), dict) else {})}
                      for e in m["edges"] if e.get("kind", "next") == "next" or e.get("active")],
            "processes": list(m.get("process_overrides") or []),
            "base": original_pairs()}


def _clean_picture(draft: dict[str, Any]) -> dict[str, Any]:
    nodes = []
    for n in draft.get("nodes", []):
        if isinstance(n, dict) and n.get("code"):
            nodes.append({"code": str(n["code"]), "label": str(n.get("label") or "").strip()[:150], "lane": str(n.get("lane") or ""),
                          "active": bool(n.get("active", True)), "x": int(n.get("x") or 0), "dy": max(0, int(n.get("dy") or 0))})
    edges = [{"source": str(e["source"]), "target": str(e["target"]), "kind": "auto" if e.get("kind") == "auto" else "next",
              "why": str(e.get("why") or "")[:200], **({"when": e["when"]} if isinstance(e.get("when"), dict) else {})}
             for e in draft.get("edges", []) if isinstance(e, dict) and e.get("source") and e.get("target") and e["source"] != e["target"]]
    return {"nodes": nodes, "edges": edges, "base": original_pairs()}


def _read(v) -> dict[str, Any]:
    try:
        pic = json.loads(v.picture_json or "{}")
    except ValueError:
        pic = {}
    return pic if isinstance(pic, dict) else {}


def _get(db: Session, tenant_id: int, version_id: int):
    from app.models.function_map import FunctionMapVersion

    v = db.get(FunctionMapVersion, version_id)
    if v is None or v.tenant_id != tenant_id:
        raise LookupError("No such version of the map")
    return v


def _summary(db: Session, v) -> dict[str, Any]:
    from app.models.auth import User

    def who(uid):
        user = db.get(User, uid) if uid else None
        return user.name if user is not None else None

    from app.orchestration import change_control

    from app.models.change_control import Workspace

    iso = lambda t: t.isoformat(timespec="seconds") if t else None  # noqa: E731
    revision = v.revision or 0
    ws = db.get(Workspace, v.workspace_id) if v.workspace_id else None
    return {"id": v.id, "number": v.number, "revision": revision, "label": f"v{v.number}" + (f".{revision}" if revision else ""),
            "workspace_id": v.workspace_id, "workspace": ws.name if ws else None, "workspace_status": ws.status if ws else None,
            "parent_id": v.parent_id, "locked_by": change_control.lock_out(db, change_control.lock_of(db, v.tenant_id, "map_version", str(v.id))),
            "name": v.name, "notes": v.notes or "", "status": v.status, "based_on": v.based_on,
            "created_at": iso(v.created_at), "created_by": who(v.created_by_id), "updated_at": iso(v.updated_at),
            "activated_at": iso(v.activated_at), "activated_by": who(v.activated_by_id),
            "builds": [{"name": b.get("name"), "status": b.get("status")} for b in _read(v).get("builds") or [] if isinstance(b, dict)]}


def _new(db: Session, tenant_id: int, user_id: int | None, name: str, based_on: str, picture: dict[str, Any], status: str = "draft",
         workspace=None):
    """A new version; made in a workspace, it belongs to it and is locked to it."""
    from sqlalchemy import func

    from app.models.function_map import FunctionMapVersion
    from app.timeutils import utcnow

    number = (db.query(func.max(FunctionMapVersion.number)).filter(FunctionMapVersion.tenant_id == tenant_id).scalar() or 0) + 1
    now = utcnow()
    v = FunctionMapVersion(tenant_id=tenant_id, number=number, name=(name.strip() or f"Version {number}")[:150], notes="",
                           status=status, based_on=based_on, picture_json=json.dumps(picture), created_by_id=user_id,
                           created_at=now, updated_at=now, activated_at=now if status == "active" else None,
                           activated_by_id=user_id if status == "active" else None, workspace_id=workspace.id if workspace else None)
    db.add(v)
    db.flush()
    if workspace is not None:
        _claim(db, tenant_id, v, user_id, workspace)
    return v


def _hidden(db: Session, v, viewer) -> bool:
    """A draft in someone else's workspace is theirs - like a Flow Studio draft, not shown to others."""
    from app.models.change_control import Workspace

    if viewer is None or v.status != "draft" or not v.workspace_id:
        return False
    ws = db.get(Workspace, v.workspace_id)
    return ws is not None and ws.status in ("open", "submitted") and ws.owner_id != viewer.id


def versions(db: Session, tenant_id: int, viewer=None) -> dict[str, Any]:
    """Every version of this tenant's map, newest first, and which one runs
    (none = BRT's original). A map arranged before versions existed becomes
    version 1, active. With a viewer, drafts in other administrators'
    workspaces are left out."""
    from app.models.function_map import FunctionMapVersion

    rows = (db.query(FunctionMapVersion).filter(FunctionMapVersion.tenant_id == tenant_id)
            .order_by(FunctionMapVersion.number.desc(), FunctionMapVersion.revision.desc()).all())
    if not rows and saved(db, tenant_id) is not None:
        mine = saved(db, tenant_id)
        rows = [_new(db, tenant_id, mine.get("user_id"), "Version 1 - the map in use before versions", "active map",
                     _picture_of(current(db, tenant_id)), status="active")]
        db.commit()
    active = next((v for v in rows if v.status == "active"), None)
    return {"versions": [_summary(db, v) for v in rows if not _hidden(db, v, viewer)], "active_id": active.id if active else None,
            "on_original": active is None}


def create_version(db: Session, tenant_id: int, user_id: int | None, name: str, source: str | int, workspace=None) -> dict[str, Any]:
    """A new draft: a copy of BRT's original ("original"), of the map in use
    ("active") or of another version (its id)."""
    # the processes a version built go with its copies - activating a copy keeps them running
    if source == "original":
        picture, based_on = _picture_of(restore_draft(db, tenant_id)), "BRT original"
    elif source == "active":
        picture, based_on = _picture_of(current(db, tenant_id)), "active map"
        active = _active(db, tenant_id)
        picture["builds"] = list(_read(active).get("builds") or []) if active is not None else []
    elif str(source).isdigit():
        src = _get(db, tenant_id, int(source))
        pic = _read(src)
        picture, based_on = _picture_of(_view(db, tenant_id, pic)[0]), f"version {src.number}"
        picture["builds"] = list(pic.get("builds") or [])
    else:
        raise VersionError("Copy from 'original', 'active' or a version")
    v = _new(db, tenant_id, user_id, name, based_on, picture, workspace=workspace)
    db.commit()
    return version_map(db, tenant_id, v.id)


def _active(db: Session, tenant_id: int):
    from app.models.function_map import FunctionMapVersion

    return db.query(FunctionMapVersion).filter(FunctionMapVersion.tenant_id == tenant_id, FunctionMapVersion.status == "active").first()


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")[:40] or "process"


def stage(db: Session, tenant_id: int, user_id: int | None, spec: dict[str, Any], placement: dict[str, Any] | None = None,
          version_id: int | None = None, workspace=None) -> dict[str, Any]:
    """A process designed in the Process Builder, put into a draft version of
    the map - nothing is built until that version is activated. spec: the
    builder's {request, steps, answers, graphs, name}. placement: {"mode":
    "new", "label"} (a process of its own) or {"mode": "append",
    "process_key"} (added to one of the map's processes). Without a version
    id, a new version is made from the map in use."""
    from app.orchestration import process_builder

    blueprint = process_builder.design(db, tenant_id, spec["request"], spec.get("answers"), steps=spec.get("steps"),
                                       graphs=spec.get("graphs"), name=spec.get("name"))
    if blueprint.get("mode") == "actions":
        raise VersionError("That is a sequence of actions to run, not a process to set up - run it from the command bar")
    if blueprint["questions"]:
        raise VersionError(f"“{blueprint['name']}” still has {len(blueprint['questions'])} open question(s) - answer them first")
    if not blueprint["components"]:
        raise VersionError("Nothing here can be built with the engines" + (" - see the engineering changes" if blueprint["engineering"] else ""))
    if version_id is not None:
        v = _get(db, tenant_id, version_id)
        if v.status != "draft":
            raise VersionError(f"Version {v.number} is {v.status} - put the process into a draft version")
        _claim(db, tenant_id, v, user_id, workspace)
    else:
        made = create_version(db, tenant_id, user_id, f"{blueprint['name']} - new process"[:150], "active", workspace)
        v = _get(db, tenant_id, made["version"]["id"])
    pic = _read(v)
    builds = pic.setdefault("builds", [])
    overrides = pic.setdefault("processes", [])
    placement = dict(placement or {})
    codes = process_builder.process_steps(blueprint)
    if placement.get("mode") == "append":
        key = str(placement.get("process_key") or "")
        current_steps = next((p["steps"] for p in merged_processes(overrides) if p["key"] == key), None)
        if current_steps is None:
            raise VersionError(f"There is no process '{key}' on the map to add it to")
        steps = [*current_steps, *[c for c in codes if c not in current_steps]]
        overrides[:] = [o for o in overrides if o.get("key") != key] + [{"key": key, "steps": steps}]
    else:
        label = str(placement.get("label") or blueprint["name"]).strip()[:120]
        taken = {p["key"] for p in merged_processes(overrides)}
        key = f"t_{_slug(label)}"
        while key in taken:
            key += "_2"
        overrides.append({"key": key, "label": label, "steps": codes})
        placement = {"mode": "new", "label": label}
    placement["process_key"] = key
    builds.append({"id": f"b{len(builds) + 1}", "name": blueprint["name"], "request": blueprint["request"], "steps": blueprint["steps"],
                   "answers": blueprint.get("answers") or {}, "status": "pending", "placement": placement, "made": [],
                   "graphs": {c["id"]: c["spec"]["graph"] for c in blueprint["components"] if c["kind"] == "flow"},
                   "pieces": [{"engine": c["engine"], "title": c["title"]} for c in blueprint["components"]]})
    v.picture_json = json.dumps(pic)
    db.commit()
    return version_map(db, tenant_id, v.id)


def _redesign(db: Session, tenant_id: int, b: dict[str, Any]) -> dict[str, Any]:
    """A staged process designed again against the setup as it is now."""
    from app.orchestration import process_builder

    return process_builder.design(db, tenant_id, b["request"], b.get("answers"), steps=b.get("steps"), graphs=b.get("graphs"), name=b.get("name"))


def _made_keys(builds: list[dict[str, Any]]) -> dict[tuple[str, int], dict[str, Any]]:
    return {(m["kind"], m["id"]): m for b in builds if b.get("status") == "built" for m in b.get("made") or [] if isinstance(m.get("id"), int)}


def _view(db: Session, tenant_id: int, picture: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """A version's picture laid over the map as the engines run it now: every
    function point of the selected modules is there (one BRT released after
    the version was made keeps its current state), links are brought up to
    BRT's current release. (map, what the release brought in)."""
    now = current(db, tenant_id)
    pic_nodes = {n["code"]: n for n in picture.get("nodes", []) if isinstance(n, dict) and n.get("code")}
    nodes = []
    for n in now["nodes"]:
        p = pic_nodes.get(n["code"])
        nodes.append({**n, "label": p.get("label") or n["label"], "lane": p.get("lane") or n["lane"], "active": bool(p.get("active", True)),
                      "x": int(p.get("x", n["x"])), "dy": int(p.get("dy", n["dy"]))} if p else n)
    codes = {n["code"] for n in nodes}
    edges, notes = release_merge([e for e in picture.get("edges", []) if isinstance(e, dict)], picture.get("base"))
    live = {(a["source"], a["target"]): a for a in now["edges"] if a["kind"] == "auto"}
    out_edges = []
    for e in edges:
        if e.get("source") not in codes or e.get("target") not in codes:
            continue
        when = clean_when(e.get("when")) or (e["when"] if isinstance(e.get("when"), dict) else None)  # an incomplete one stays to finish
        edge = {"source": e["source"], "target": e["target"], "kind": e.get("kind", "next"), "why": e.get("why") or "", **({"when": when} if when else {})}
        if edge["kind"] == "auto":
            a = live.get((edge["source"], edge["target"]))
            edge.update({"active": True, "by_map": a["by_map"] if a else True,
                         **({"flow_id": a["flow_id"], "flow_name": a["flow_name"]} if a else {"flow_name": "made when this version is activated"})})
        out_edges.append(edge)
    drawn = {(e["source"], e["target"]) for e in out_edges if e["kind"] == "auto"}
    # automations made in Flow Studio are Flow Studio's - a version shows them as they run (a process it built included)
    out_edges += [a for pair, a in live.items() if a["active"] and not a["by_map"] and pair not in drawn]
    overrides = [o for o in picture.get("processes") or [] if isinstance(o, dict)]
    processes = [{**p, "steps": [c for c in p["steps"] if c in codes]} for p in merged_processes(overrides)]
    view = {**now, "nodes": nodes, "edges": out_edges, "processes": [p for p in processes if p["steps"] or p.get("tenant")],
            "process_overrides": overrides, "builds": [_build_out(b) for b in picture.get("builds") or [] if isinstance(b, dict)]}
    view["differences"] = differences(view)
    return view, notes


def _build_out(b: dict[str, Any]) -> dict[str, Any]:
    """A process staged into a version, as the screen shows it."""
    return {"id": b.get("id"), "name": b.get("name"), "request": b.get("request"), "status": b.get("status"), "placement": b.get("placement"),
            "pieces": b.get("pieces") or [], "made": b.get("made") or []}


def version_map(db: Session, tenant_id: int, version_id: int) -> dict[str, Any]:
    v = _get(db, tenant_id, version_id)
    view, notes = _view(db, tenant_id, _read(v))
    return {**view, "version": _summary(db, v), "release_notes": notes}


def _claim(db: Session, tenant_id: int, v, user_id: int | None, workspace=None) -> None:
    """A draft version is locked to the first administrator who changes it -
    and to their workspace, when they work in one; another one works on their
    own revision or copy (change_control). A version made in a workspace is
    changed only there."""
    from app.models.auth import User
    from app.models.change_control import Workspace
    from app.orchestration import change_control

    user = db.get(User, user_id) if user_id else None
    if user is None:
        return
    if v.workspace_id and (workspace is None or workspace.id != v.workspace_id):
        home = db.get(Workspace, v.workspace_id)
        if home is not None and home.status in ("open", "submitted"):
            whose = "your" if home.owner_id == user.id else f"{change_control._name(db, home.owner_id)}'s"
            raise VersionError(f"Map version {v.number} belongs to {whose} workspace “{home.name}” - "
                               + ("select it to work on the version." if home.owner_id == user.id else "make a revision or a copy of it to work on."))
    try:
        change_control.claim(db, tenant_id, "map_version", str(v.id), user, workspace, f"Map version {v.number}")
    except change_control.ChangeError as exc:
        raise VersionError(f"{exc} Make a revision or a copy of it to work on.") from exc


def revise_version(db: Session, tenant_id: int, version_id: int, user_id: int | None, workspace=None) -> dict[str, Any]:
    """The next revision of a version (v3 -> v3.1 -> v3.2): a draft with its
    picture and processes, keeping its number - activating it supersedes the
    revision in use."""
    from sqlalchemy import func

    from app.models.function_map import FunctionMapVersion

    src = _get(db, tenant_id, version_id)
    revision = (db.query(func.max(FunctionMapVersion.revision))
                .filter(FunctionMapVersion.tenant_id == tenant_id, FunctionMapVersion.number == src.number).scalar() or 0) + 1
    v = _new(db, tenant_id, user_id, src.name, f"revision of v{src.number}" + (f".{src.revision}" if src.revision else ""), _read(src),
             workspace=workspace)
    v.number, v.revision, v.parent_id = src.number, revision, src.id
    db.commit()
    return version_map(db, tenant_id, v.id)


def save_version(db: Session, tenant_id: int, version_id: int, user_id: int | None, payload: dict[str, Any], workspace=None) -> dict[str, Any]:
    """A draft's picture, name or notes - nothing changes in the engines."""
    from app.timeutils import utcnow

    v = _get(db, tenant_id, version_id)
    if v.status != "draft":
        raise VersionError(f"Version {v.number} is {v.status} - it can't be changed. Make a revision of it to work on.")
    _claim(db, tenant_id, v, user_id, workspace)
    if payload.get("name") is not None:
        v.name = str(payload["name"]).strip()[:150] or v.name
    if payload.get("notes") is not None:
        v.notes = str(payload["notes"])[:2000]
    if payload.get("nodes") is not None:
        old = _read(v)  # the editor sends the picture; the version's processes and pending builds stay
        v.picture_json = json.dumps({**_clean_picture({"nodes": payload["nodes"], "edges": payload.get("edges") or []}),
                                     "processes": old.get("processes") or [], "builds": old.get("builds") or []})
    v.updated_at = utcnow()
    db.commit()
    return version_map(db, tenant_id, v.id)


def delete_version(db: Session, tenant_id: int, version_id: int, user_id: int | None = None, workspace=None) -> None:
    from app.orchestration import change_control

    v = _get(db, tenant_id, version_id)
    if v.status == "active":
        raise VersionError("The active version can't be deleted - activate another version (or BRT's original) first.")
    lock = change_control.lock_of(db, tenant_id, "map_version", str(v.id))
    if lock is not None and user_id is not None and (lock.user_id != user_id or (lock.workspace_id and (workspace is None or workspace.id != lock.workspace_id))):
        raise VersionError(f"Map version {v.number} is being worked on by {change_control.lock_out(db, lock)['user']}"
                           + (f" in workspace “{change_control.lock_out(db, lock)['workspace']}”" if lock.workspace_id else "") + " - it can't be deleted from here")
    change_control.unlock(db, tenant_id, "map_version", str(v.id))
    db.delete(v)
    db.commit()


def _activation(db: Session, tenant_id: int, version_id: int | None) -> dict[str, Any]:
    """What activating asks the engines for: the version's picture over every
    function point (or BRT's original)."""
    if version_id is None:
        return _picture_of(restore_draft(db, tenant_id))
    v = _get(db, tenant_id, version_id)
    if v.status == "active":
        raise VersionError(f"Version {v.number} is already active.")
    return _picture_of(_view(db, tenant_id, _read(v))[0])


def _builds_of(db: Session, tenant_id: int, version_id: int | None) -> list[dict[str, Any]]:
    return [b for b in _read(_get(db, tenant_id, version_id)).get("builds") or [] if isinstance(b, dict)] if version_id is not None else []


def plan_activation(db: Session, tenant_id: int, version_id: int | None) -> dict[str, Any]:
    """What activating this version (None: BRT's original) would change -
    in the map, and the processes staged into it - nothing is changed."""
    out = plan(db, tenant_id, _activation(db, tenant_id, version_id))
    builds = _builds_of(db, tenant_id, version_id)
    active = _active(db, tenant_id)
    keep = _made_keys(builds)
    off = [m for k, m in _made_keys(_read(active).get("builds") or [] if active is not None else []).items() if k not in keep]
    for b in builds:
        if b.get("status") == "built":
            out["changes"].append({"kind": "build_on", "engine": "Process Builder",
                                   "text": f"Switch “{b['name']}” back on - its flows, approvals and assignments run again"})
            continue
        bp = _redesign(db, tenant_id, b)
        if bp["questions"] or not bp["components"]:
            out["problems"].append({"text": f"“{b['name']}” needs another look in the Process Builder - "
                                            + (f"{len(bp['questions'])} open question(s)" if bp["questions"] else "nothing in it can be built now")})
            continue
        where = b.get("placement") or {}
        out["changes"].append({"kind": "build", "engine": "Process Builder",
                               "text": f"Build “{b['name']}” ({len(bp['components'])} piece(s)) "
                                       + (f"as a new process “{where.get('label')}”" if where.get("mode") == "new" else "into an existing process"),
                               "pieces": [f"{c['engine']}: {c['title']}" for c in bp["components"]]})
    if off:
        out["changes"].append({"kind": "build_off", "engine": "Process Builder",
                               "text": f"Switch off {len(off)} flow(s), approval(s) and assignment(s) the version in use built - "
                                       "kept, and back on if it is activated again; page and field changes stay"})
    out["layout_only"] = not out["changes"]
    return out


def activate(db: Session, tenant_id: int, version_id: int | None, user_id: int | None) -> dict[str, Any]:
    """Re-orchestrates the engines to this version (None: BRT's original), all
    together: the processes staged into it are built, the ones it already
    built are switched back on, those of the version it replaces are switched
    off, and the map is applied - one transaction, or nothing. The version it
    replaces is retired - kept, and can be activated again."""
    from app.models.function_map import FunctionMapVersion
    from app.orchestration import process_builder, rehearsal
    from app.timeutils import utcnow

    v = _get(db, tenant_id, version_id) if version_id is not None else None
    if v is not None and v.status == "active":
        raise VersionError(f"Version {v.number} is already active.")
    olds = db.query(FunctionMapVersion).filter(FunctionMapVersion.tenant_id == tenant_id, FunctionMapVersion.status == "active").all()
    built: list[dict[str, Any]] = []
    try:
        pic = _read(v) if v is not None else {}
        keep = _made_keys(pic.get("builds") or [])
        for old in olds:
            gone = [m for k, m in _made_keys(_read(old).get("builds") or []).items() if k not in keep]
            process_builder.switch(db, tenant_id, gone, False)
            old.status = "retired"
        for b in pic.get("builds") or []:
            if b.get("status") == "built":
                process_builder.switch(db, tenant_id, b.get("made") or [], True)
                continue
            bp = _redesign(db, tenant_id, b)
            if bp["questions"] or not bp["components"]:
                raise VersionError(f"“{b['name']}” needs another look in the Process Builder before this version can be activated")
            with rehearsal.capture(db) as rows:
                made = process_builder.create_all(db, tenant_id, bp)
            impact = rehearsal.summarize(db, rows, {})
            process_builder.record_build(db, tenant_id, bp, made)
            b.update(status="built", made=made, built_at=utcnow().isoformat(timespec="seconds"))
            key = (b.get("placement") or {}).get("process_key")
            for o in pic.get("processes") or []:  # the process's own flows join its steps now that they have numbers
                if o.get("key") == key:
                    o["steps"] = [*o["steps"], *[c for c in process_builder.process_steps(bp, made) if c not in o["steps"]]]
            built.append({"build": b, "blueprint": bp, "made": made, "impact": impact})
        if v is not None:
            from app.orchestration import change_control

            v.picture_json = json.dumps(pic)
            v.status, v.activated_at, v.activated_by_id = "active", utcnow(), user_id
            change_control.unlock(db, tenant_id, "map_version", str(v.id))  # active is read-only - nobody holds it
            desired = _picture_of(_view(db, tenant_id, pic)[0])
        else:
            desired = _picture_of(restore_draft(db, tenant_id))
        out = apply(db, tenant_id, user_id, desired)  # commits all of it with the engines' changes, or rolls all back
    except Exception:
        db.rollback()
        raise
    results = []
    for item in built:  # after the commit: each piece proved live
        checks, memory_id = process_builder.after_build(db, tenant_id, item["blueprint"], item["made"])
        results.append({"name": item["build"]["name"], "blueprint": item["blueprint"], "made": item["made"], "checks": checks,
                        "impact": item["impact"], "memory_id": memory_id})
    return {"applied": out["applied"], "problems": out["problems"], "map": out["map"], "built": results, **versions(db, tenant_id)}


def simulate(db: Session, tenant_id: int, version_id: int) -> dict[str, Any]:
    """The version played through with nothing kept: the processes staged into
    it are built and each flow is run on a recent record of its type, then
    everything is rolled back. What activating would change in the map comes
    with it."""
    from app.orchestration import process_builder, rehearsal

    planned = plan_activation(db, tenant_id, version_id)
    results = []
    try:
        with rehearsal.capture(db) as rows:
            for b in _builds_of(db, tenant_id, version_id):
                if b.get("status") == "built":
                    continue
                bp = _redesign(db, tenant_id, b)
                if bp["questions"] or not bp["components"]:
                    results.append({"name": b["name"], "ok": False, "checks": [], "message": "needs another look in the Process Builder"})
                    continue
                made = process_builder.create_all(db, tenant_id, bp)
                checks = process_builder._verify(db, tenant_id, bp, made, keep_open=True)
                titles = {c["id"]: c["title"] for c in bp["components"]}
                results.append({"name": b["name"], "ok": all(c["ok"] for c in checks), "made": made,
                                "checks": [{**c, "title": titles.get(c["component"], "")} for c in checks]})
            db.flush()
        impact = rehearsal.summarize(db, rows, {})
    finally:
        db.rollback()
    return {"builds": results, "impact": impact, "plan": planned, "ok": all(r["ok"] for r in results) and not planned["problems"]}

"""Build it from scratch - for a request nothing in the tenant's set-up covers.

When the assistant can't match a request to an action, a chain of actions or
a process the builder can set up (its flows, intents, custom fields, rules
and assignments), an administrator is offered a new action built for it:

  draft()  what would be made - nothing is saved:
           - a new action in the intent bar ("Our processes"), named from the
             request, answering to its words;
           - its form: the details the request names ("... with the survey
             date, engineer and notes"), typed from their words, plus the
             record it acts on;
           - its automation (a custom_intent flow), drafted from the request
             the way the Flow Studio drafts one, checked against the real
             fields - or, when nothing can be drafted, one that confirms on
             screen that it was done, ready to extend in the Flow Studio.
  build()  makes exactly the draft the admin consented to (they may have
           edited it) and switches it on. Nothing is built without consent.

The new action is an ordinary custom intent - Admin -> Intents and the Flow
Studio change it like any other, and switching it off keeps everything.
"""

from __future__ import annotations

import json
import re
from typing import Any

from sqlalchemy.orm import Session

_INTRO = re.compile(r"^\s*(?:please\s+|kindly\s+|can\s+you\s+|could\s+you\s+|would\s+you\s+|i\s+(?:want|need|would\s+like)\s+(?:you\s+)?to\s+|"
                    r"we\s+(?:want|need)\s+to\s+|let'?s\s+|how\s+(?:do|can)\s+(?:i|we)\s+|help\s+me\s+(?:to\s+)?|"
                    r"(?:so\s+)?in\s+order\s+to\s+(?:make\s+(?:this|it|that)\s+(?:happen|work)|do\s+(?:this|it|that))\s*,?\s*)+", re.I)
_WITH = re.compile(r"\b(?:with|capturing|capture|recording|including|asking\s+for|that\s+takes|taking|fields?:?)\s+(?:the\s+|a\s+|an\s+|its\s+)?(.+)$", re.I)
_ITEM_SPLIT = re.compile(r"\s*(?:,|;|\band\b|&|\+)\s*", re.I)
_REQUIRED = re.compile(r"\b(?:mandatory|required|must\s+have)\b\s*", re.I)
# (pattern on the field's words, type, placeholder)
_TYPES: list[tuple[re.Pattern, str, str]] = [
    (re.compile(r"\b(?:date|day|deadline|due)\b", re.I), "text", "YYYY-MM-DD"),
    (re.compile(r"\b(?:time|slot)\b", re.I), "text", "e.g. 10:30"),
    (re.compile(r"\b(?:amount|price|cost|fee|charge|value|budget|revenue)\b", re.I), "money", ""),
    (re.compile(r"\b(?:percent|percentage|%|rate)\b", re.I), "percent", ""),
    (re.compile(r"\b(?:count|number\s+of|qty|quantity|how\s+many|units?)\b", re.I), "number", ""),
    (re.compile(r"\b(?:notes?|comments?|description|reason|details|summary|remarks?)\b", re.I), "textarea", ""),
    (re.compile(r"^(?:is|has|needs?)\b|\b(?:yes\s*/\s*no|flag|confirmed|approved\?)\b", re.I), "boolean", ""),
]
_CATEGORY = {"lead": "sales", "opportunity": "sales", "quote": "sales", "party": "customers", "order": "orders"}
_ICONS = [(r"\bsurvey|visit|site\b", "📍"), (r"\bcall|phone\b", "📞"), (r"\bmail|email|letter\b", "✉️"), (r"\bmeeting|demo|book\b", "📅"),
          (r"\bpay|invoice|bill|refund\b", "💳"), (r"\bcomplain|issue|fault|ticket\b", "🛠️"), (r"\bcontract|agreement|sign\b", "✍️")]
MAX_FIELDS = 10


def _core(request: str) -> str:
    text = " ".join((request or "").split()).strip(" ?!.")
    return _INTRO.sub("", text).strip(" ,.") or text


def _name(core: str) -> str:
    head = _WITH.split(core, maxsplit=1)[0] if _WITH.search(core) else core
    words = re.sub(r"\b(?:for\s+(?:the|a|an|this|that|my|our)\s+)?(?:customer|prospect|record)s?\b$", "", head.strip(), flags=re.I).split()[:7]
    name = " ".join(words).strip(" ,.-") or core[:60]
    return name[:1].upper() + name[1:]


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", text.strip().lower()).strip("_")[:60] or "detail"


def _fields(core: str) -> list[dict[str, Any]]:
    """The details the request names, each typed from its words."""
    from app.intent_engine import intent_registry

    m = _WITH.search(core)
    if not m:
        return []
    out: list[dict[str, Any]] = []
    for raw in _ITEM_SPLIT.split(m.group(1)):
        required = bool(_REQUIRED.search(raw))
        words = _REQUIRED.sub("", raw).strip(" .:-")
        words = re.sub(r"^(?:the|a|an|its|their|his|her)\s+", "", words, flags=re.I)
        if not words or len(words.split()) > 5 or not re.search(r"[a-z]", words, re.I):
            continue
        key = _slug(words)
        if any(f["key"] == key for f in out):
            continue
        library = intent_registry._LIB.get(key)
        if library and library.get("type", "text") not in ("updates", "transition", "attributes", "design", "page", "products"):
            spec = intent_registry.field_spec(key, required).as_dict()
            out.append({"key": key, "label": spec["label"], "type": spec["type"], "required": required, "values": spec.get("values") or [],
                        "source": spec.get("source") or "", "placeholder": spec.get("placeholder") or "", "help": spec.get("help") or ""})
        else:
            kind, placeholder = next(((t, p) for pattern, t, p in _TYPES if pattern.search(words)), ("text", ""))
            out.append({"key": key, "label": words[:1].upper() + words[1:], "type": kind, "required": required, "values": [], "source": "",
                        "placeholder": placeholder, "help": ""})
        if len(out) >= MAX_FIELDS:
            break
    return out


def _confirm_graph(label: str, entity_type: str | None) -> dict[str, Any]:
    from app.orchestration import flow_ai

    about = " for {{record.name}}" if entity_type == "party" else (f" on {entity_type.replace('_', ' ')} #{{{{record.id}}}}" if entity_type else "")
    nodes = [{"id": "n1", "type": "start", "label": "Start", "config": {}},
             {"id": "n2", "type": "notify", "label": "Confirm it on screen",
              "config": {"channel": "popup", "audience": "me", "title": label, "level": "success", "message": f"“{label}” recorded{about}."}},
             {"id": "n3", "type": "end", "label": "End", "config": {}}]
    edges = [{"id": "e1", "source": "n1", "target": "n2", "label": ""}, {"id": "e2", "source": "n2", "target": "n3", "label": ""}]
    return flow_ai.layout({"nodes": nodes, "edges": edges})


def draft(db: Session, tenant_id: int, request: str) -> dict[str, Any]:
    """What a new action for this request would be - nothing is saved."""
    from app.orchestration import flow_ai, flow_engine, flow_schema, process_builder

    core = _core(request)
    label = _name(core)
    entity_type = process_builder._entity_of(core)
    fields = _fields(core)
    warnings: list[str] = []
    graph, source = None, "confirm"
    try:
        catalog = flow_schema.field_catalog(db, tenant_id, entity_type, None, None)
        made = flow_ai.generate(db, tenant_id, core, entity_type, catalog)
        reviewed = flow_schema.review_graph(made["graph"], catalog, True)["graph"]
        steps = [n for n in reviewed.get("nodes", []) if n.get("type") not in ("start", "end")]
        if steps and not flow_engine.validate_graph(reviewed):
            graph, source = reviewed, made.get("source") or "rules"
    except Exception as exc:  # noqa: BLE001 - a draft that can't be made falls back to the confirming one
        warnings.append(f"I couldn't draft what it should do ({type(exc).__name__}) - it confirms on screen for now; extend it in the Flow Studio.")
    if graph is None:
        graph = _confirm_graph(label, entity_type)
        if not warnings:
            warnings.append("I couldn't tell what it should do by itself - for now it records the request and confirms it on screen. "
                            "Add the steps in the Flow Studio whenever you like.")
    phrases = list(dict.fromkeys(p for p in (label.lower(), core.lower()[:80]) if p))
    taken = {p.strip().lower(): f.name for f in flow_engine.custom_intent_flows(db, tenant_id) for p in (f.trigger_phrases or "").split(",") if p.strip()}
    for p in phrases:
        if p in taken:
            warnings.append(f"“{taken[p]}” already answers to “{p}” - Admin → Conflicts will ask which one keeps it.")
    icon = next((i for pattern, i in _ICONS if re.search(pattern, core, re.I)), "✨")
    steps = [n.get("label") or n["type"] for n in graph.get("nodes", []) if n.get("type") not in ("start", "end")]
    record_word = entity_type.replace("_", " ") if entity_type else None
    return {
        "request": request,
        "label": label,
        "description": f"Built from the request: “{core}”",
        "icon": icon,
        "category": _CATEGORY.get(entity_type or "", "custom"),
        "entity_type": entity_type,
        "phrases": phrases,
        "fields": fields,
        "graph": graph,
        "source": source,
        "steps": steps,
        "warnings": warnings,
        "creates": [
            f"A new action “{label}” in the intent bar ({dict(_category_labels()).get(_CATEGORY.get(entity_type or '', 'custom'), 'Our processes')}), "
            f"answering to “{phrases[0]}”",
            "Its form: " + (", ".join(([f"the {record_word}"] if record_word else []) + [f["label"] for f in fields]) or "no details to ask for"),
            "What it does: " + (" → ".join(steps) or "nothing yet"),
            "Switched on for everyone who may run actions - change it in Admin → Intents or the Flow Studio",
        ],
    }


def _category_labels() -> list[tuple[str, str]]:
    from app.intent_engine import intent_registry

    return intent_registry.CATEGORIES


def build(db: Session, tenant_id: int, user, spec: dict[str, Any]) -> dict[str, Any]:
    """Makes the action the admin consented to, and switches it on."""
    from app.experience import routes_intent_admin as admin
    from app.intent_engine import intent_registry
    from app.models.flows import FlowDefinition
    from app.orchestration import flow_engine, flow_schema
    from app.schemas.intent_catalog import IntentFieldIn

    label = str(spec.get("label") or "").strip()
    if len(label) < 2:
        raise ValueError("Give the new action a name")
    category = spec.get("category") or "custom"
    if category not in intent_registry.CATEGORY_KEYS:
        category = "custom"
    from app.crm_core import lifecycle_service

    entity_type = lifecycle_service.normalize_entity_type(spec.get("entity_type")) if spec.get("entity_type") else None
    try:
        fields = admin._clean_fields([IntentFieldIn(**{k: v for k, v in f.items() if k in IntentFieldIn.model_fields})
                                      for f in spec.get("fields") or []], entity_type)
    except Exception as exc:  # HTTPException from the admin's own checks, or a bad shape
        raise ValueError(getattr(exc, "detail", None) or str(exc)) from exc
    phrases = admin._phrases([str(p) for p in spec.get("phrases") or []]) or label.lower()
    graph = spec.get("graph") if isinstance(spec.get("graph"), dict) and spec["graph"].get("nodes") else None
    flow = FlowDefinition(tenant_id=tenant_id, name=label[:150], description=str(spec.get("description") or "")[:2000], entity_type=entity_type,
                          trigger_type="custom_intent", timing="after", trigger_phrases=phrases, required_fields=admin._required(fields),
                          graph_json=json.dumps({"nodes": [], "edges": []}), is_active=False)
    db.add(flow)
    db.flush()
    if graph is not None:
        graph = flow_schema.review_graph(graph, flow_schema.catalog_for_flow(db, tenant_id, flow), True)["graph"]
    if graph is None or flow_engine.validate_graph(graph):
        graph = _confirm_graph(label, entity_type)
    flow.graph_json = json.dumps(graph)
    flow.is_active = True
    code = flow_engine.custom_intent_code(flow)
    setting = admin._setting(db, tenant_id, code)
    setting.label = label
    setting.description = str(spec.get("description") or "")
    setting.category = category
    setting.icon = str(spec.get("icon") or "✨").strip()[:16] or "✨"
    setting.is_active = True
    setting.role_ids = ""
    setting.fields_json = json.dumps(fields)
    db.commit()
    return {"intent": intent_registry.entry(db, tenant_id, code), "code": code, "flow_id": flow.id}

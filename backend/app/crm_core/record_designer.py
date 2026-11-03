"""Design a record type in plain English (Admin -> Record Types -> "Describe it", and INT-158).

    "Site Survey for a customer (required): survey date (date, required), floors (number), outcome (Pending,
     Feasible, Not feasible), site (lookup to Site), report (long text). States: scheduled -> in progress ->
     completed, cancelled. Rules: if state is completed then report is mandatory; floors must be at least 1.
     Related to Contacts as 'Survey contacts'."

draft() turns that into a plan - the type, its fields (lookups included), its state model, its many-to-many
relationships and its validation rules - by the tenant's AI model when one is set up (its answer is checked
against the closed plan form, never run), else by the built-in reader below. Nothing is saved: the admin
reviews and adjusts the plan, then build() creates it all through the same services the admin screens use
(custom_record_service, the state model, page_service.save_rule). An existing type named in the text is
extended - fields, states and rules it lacks are added, nothing it has is changed.
"""

from __future__ import annotations

import json
import re
from typing import Any

from sqlalchemy.orm import Session

from app.crm_core import custom_record_service as records
from app.crm_core import lifecycle_service
from app.models.custom_fields import CUSTOM_FIELD_DATA_TYPES, CustomFieldDefinition

TERMINAL = {"cancelled", "canceled", "rejected", "lost", "closed", "archived", "void", "withdrawn", "expired", "terminated", "abandoned"}
LOCKED = TERMINAL | {"completed", "complete", "done", "approved", "signed", "won", "finished", "delivered", "resolved"}
ACTION_OF = {"in_progress": "start", "started": "start", "completed": "complete", "complete": "complete", "done": "finish",
             "cancelled": "cancel", "canceled": "cancel", "approved": "approve", "rejected": "reject", "closed": "close",
             "scheduled": "schedule", "submitted": "submit", "on_hold": "hold", "signed": "sign", "active": "activate",
             "expired": "expire", "archived": "archive", "open": "reopen", "draft": "back_to_draft", "review": "send_for_review",
             "in_review": "send_for_review", "pending_approval": "submit"}
_SECTION = r"(?:fields?|attributes?|states?|stages?|status(?:es)?|lifecycle|workflow|rules?|validations?|checks?|relationships?|related\s+to|linked\s+to)"


def _key(text: str, size: int = 60) -> str:
    key = re.sub(r"[^a-z0-9]+", "_", str(text or "").lower()).strip("_")[:size].strip("_")
    return key if key and key[0].isalpha() else (f"f_{key}" if key else "")


def _targets(db: Session, tenant_id: int) -> dict[str, str]:
    """Names a lookup / parent / relationship may point to -> the entity type."""
    out: dict[str, str] = {}
    for key, label in records.PARENT_TYPES.items():
        out[label.lower()] = key
        out[key] = key
    for alias, key in lifecycle_service.ENTITY_TYPE_ALIASES.items():
        out[alias] = key
    for rt in records.list_types(db, tenant_id):
        for name in (rt.label, rt.plural_label, rt.key, rt.key.replace("_", " ")):
            out[name.lower()] = rt.entity_type
    return out


def _target_of(name: str, targets: dict[str, str], own: tuple[str, str] | None = None) -> str | None:
    """own = (the new type's name in lower case, its entity type) - a type may point to its own records."""
    text = re.sub(r"^(?:a|an|the|each|every|one|its|their)\s+", "", str(name or "").strip().lower()).strip(" '\"")
    if not text:
        return None
    candidates = [text, text.rstrip("s"), re.sub(r"ies$", "y", text)]
    if own and any(c in (own[0], own[0].rstrip("s")) for c in candidates):
        return own[1]
    return next((targets[c] for c in candidates if c in targets), None)


# ---------------------------------------------------------------- the built-in reader


def _split_items(text: str) -> list[str]:
    """Commas / semicolons / ' and ' outside parentheses."""
    items, depth, current, i = [], 0, "", 0
    while i < len(text):
        ch = text[i]
        depth += ch == "("
        depth -= ch == ")"
        if depth <= 0 and ch in ",;\n":
            items.append(current)
            current = ""
        elif depth <= 0 and re.match(r"\s+and\s+", text[i:]) and current.strip():
            items.append(current)
            current = ""
            i += len(re.match(r"\s+and\s+", text[i:]).group(0))
            continue
        else:
            current += ch
        i += 1
    items.append(current)
    return [x.strip(" .") for x in items if x.strip(" .")]


# where a section of the description ends: the next "Heading:" or "related to ..."
_END = rf"(?=(?:\.\s+|\n|;\s*|\s)\b(?:{_SECTION}\s*(?:are\s*)?:|(?:related|linked)\s+to\b)|$)"


def _section(text: str, names: str, loose: bool = False) -> str:
    """The text of a section - "States: a -> b". loose: no colon needed ("with a, b and c")."""
    sep = r"\s*(?:are|is|:|-)?\s*" if loose else r"\s*(?:are\s*)?:\s*"
    m = re.search(rf"\b(?:{names})\b{sep}(.+?){_END}", text, re.I | re.S)
    return m.group(1).strip(" .") if m else ""


def _field(item: str, targets: dict[str, str], own: tuple[str, str]) -> dict[str, Any] | None:
    m = re.match(r"^(.*?)\s*\((.*)\)\s*$", item)
    label, spec = (m.group(1), m.group(2)) if m else (item, "")
    label = re.sub(r"^(?:a|an|the|its|their)\s+", "", label.strip(), flags=re.I).strip(" :'\"")
    if not label or len(label) > 60:
        return None
    words = f"{label} {spec}".lower()
    required = bool(re.search(r"\b(required|mandatory|must)\b", spec, re.I))
    spec_clean = re.sub(r"\b(required|mandatory|optional|must)\b", "", spec, flags=re.I).strip(" ,;")
    data_type, values, target = "text", [], None
    lookup = re.search(r"(?:lookup|link|links|linked|points?|reference|ref)\s*(?:to|of)?\s+(?:an?\s+|the\s+)?([\w ]+)", spec_clean, re.I) \
        or re.search(r"^\s*(?:→|->)\s*([\w ]+)$", spec_clean)
    if lookup:
        target = _target_of(lookup.group(1), targets, own)
        data_type = "lookup" if target else "text"
    elif re.search(r"\bone\s+of\b", spec_clean, re.I) or (spec_clean.count(",") >= 1 and not re.search(
            r"\b(date|number|count|yes|no|boolean|email|phone|text|long)\b", spec_clean.split(",")[0], re.I)):
        values = [v.strip(" '\"") for v in re.split(r",|\bor\b|/", re.sub(r"^\s*(?:picklist|choice|one\s+of)\s*[:\-]?\s*", "", spec_clean, flags=re.I)) if v.strip(" '\"")]
        data_type = "picklist" if len(values) >= 2 else "text"
    elif re.search(r"\b(date|day|deadline|when|due|dob|birthday)\b", words):
        data_type = "date"
    elif re.search(r"\b(yes\s*/\s*no|yes or no|boolean|checkbox|flag|tick)\b", words) or re.match(r"^(is|has|needs|requires)\b", label, re.I):
        data_type = "boolean"
    elif re.search(r"\b(number|count|qty|quantity|amount|price|cost|total|floors?|area|size|score|age|percent|%|integer|decimal|rate|budget|value)\b", words):
        data_type = "number"
    elif re.search(r"\be-?mail\b", words):
        data_type = "email"
    elif re.search(r"\b(phone|mobile|telephone|contact\s+number)\b", words):
        data_type = "phone"
    elif re.search(r"\b(notes?|description|comments?|report|summary|long\s+text|details|remarks|findings)\b", words):
        data_type = "textarea"
    else:
        # "site" / "customer" with nothing more: a record of that type
        target = _target_of(label, targets, own) if not spec_clean else None
        if target and target != own[1]:
            data_type = "lookup"
    return {"key": _key(label), "label": label[:1].upper() + label[1:], "data_type": data_type, "values": values, "target": target,
            "required": required}


def _states(text: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    groups = [g.strip() for g in re.split(r",|;|\bor\b", text) if g.strip(" .")]
    chains = [[s.strip(" .'\"") for s in re.split(r"\s*(?:->|→|=>|>|\bthen\b|\bto\b)\s*", g) if s.strip(" .'\"")] for g in groups]
    states: list[dict[str, Any]] = []
    transitions: list[dict[str, Any]] = []

    def add(label: str) -> str:
        code = _key(label, 50)
        if code and not any(s["code"] == code for s in states):
            states.append({"code": code, "label": label[:1].upper() + label[1:], "editable": code not in LOCKED, "is_terminal": code in TERMINAL})
        return code

    def move(a: str, b: str) -> None:
        action = ACTION_OF.get(b, f"to_{b}")[:50]
        if a and b and a != b and not any(t["from_state"] == a and t["to_state"] == b for t in transitions):
            transitions.append({"from_state": a, "to_state": b, "action": action, "allowed_roles": ""})

    for i, chain in enumerate(chains):
        codes = [add(s) for s in chain]
        for a, b in zip(codes, codes[1:]):
            move(a, b)
        if i and len(codes) == 1:  # "..., cancelled" - reachable from wherever a record is still going
            move("*", codes[0])
    if states and len(chains[0]) > 1 and not states[[s["code"] for s in states].index(_key(chains[0][-1], 50))]["is_terminal"]:
        last = _key(chains[0][-1], 50)
        states[[s["code"] for s in states].index(last)]["editable"] = False
    for s in states:  # a closed-down record can be picked up again where that makes sense
        first = states[0]["code"]
        if s["is_terminal"] and s["code"] not in ("archived", first) and not any(
                t["from_state"] == s["code"] and t["to_state"] == first for t in transitions):
            transitions.append({"from_state": s["code"], "to_state": first, "action": "reopen", "allowed_roles": ""})
    return states, transitions


def read(text: str, targets: dict[str, str]) -> dict[str, Any]:
    """The built-in reader: name, parent, fields, states, rules and relationships from the wording."""
    body = re.sub(r"^\s*(?:please\s+)?(?:design|define|create|build|set\s+up|make|add)?\s*(?:me\s+)?(?:a\s+|an\s+|the\s+)?(?:new\s+)?"
                  r"(?:custom\s+)?(?:record\s+type|object|entity|record)?\s*(?:called|named|for\s+tracking)?\s*[:\-]?\s*", "", text.strip(), flags=re.I)
    m = re.match(r"^['\"]?([A-Za-z][\w &/'-]{0,60}?)['\"]?\s*(?=:|\s-\s|\(|,|\.|\bfor\b|\bwith\b|\bthat\b|\bwhich\b|\bbelong|\bunder\b|\bper\b|\bhas\b|\bhaving\b|$)", body, re.I)
    label = (m.group(1).strip() if m else body.split()[0] if body.split() else "Record").strip(" '\"")
    label = " ".join(w if w.isupper() else w[:1].upper() + w[1:] for w in label.split())
    own = (label.lower(), f"x_{records.slug(label)}")
    head = re.split(rf"\b{_SECTION}\s*(?:are\s*)?:", body, maxsplit=1, flags=re.I)[0]
    parent, parent_required = None, False
    pm = re.search(r"\b(?:for|belongs?\s+to|belonging\s+to|hangs?\s+off|under|child\s+of|per|attached\s+to|of)\s+(?:(?:an|a|each|every|the|one)\s+)?"
                   r"([A-Za-z][\w ]{1,40}?)\s*(\((?:required|mandatory|optional)\))?(?=[,.;:()]|\s+(?:with|and|that|which|has|having)\b|$)", head, re.I)
    if pm:
        parent = _target_of(pm.group(1), targets)
        parent_required = bool(pm.group(2) and re.search(r"required|mandatory", pm.group(2), re.I)) or bool(re.search(r"\b(must|always)\s+belong", head, re.I))
    fields_text = _section(body, r"fields?|attributes?")
    if not fields_text and ":" in head:
        fields_text = head.split(":", 1)[1]
    if not fields_text:
        fields_text = _section(head, r"with|has|having|captures?|tracks?", loose=True)
    fields = [f for f in (_field(i, targets, own) for i in _split_items(fields_text)) if f and f["key"]]
    states, transitions = _states(_section(body, r"states?|stages?|status(?:es)?|lifecycle|workflow"))
    rules_text = _section(body, r"rules?|validations?|checks?")
    rules = [r.strip(" .") for r in re.split(r";|\n|\.\s+(?=[A-Z])", rules_text) if r.strip(" .")]
    relationships = []
    rel_text = _section(body, r"relationships?|related\s+to|linked\s+to") or (
        re.search(r"\b(?:related|linked)\s+to\s+(.+?)(?:[.;]|$)", body, re.I).group(1) if re.search(r"\b(?:related|linked)\s+to\s+", body, re.I) else "")
    for item in _split_items(rel_text):
        rm = re.match(r"^(?:many\s+)?([\w ]+?)(?:\s+as\s+['\"]?([^'\"]+)['\"]?)?$", item.strip(), re.I)
        if rm and (target := _target_of(rm.group(1), targets, own)):
            relationships.append({"to": target, "label": rm.group(2).strip() if rm.group(2) else rm.group(1).strip().title(), "reverse_label": ""})
    return {"type": {"label": label, "parent_entity_type": parent, "parent_required": parent_required},
            "fields": fields, "states": states, "transitions": transitions, "relationships": relationships, "rules": rules}


_PROMPT = """You design one record type for a CRM from an administrator's plain-English description. Answer strictly as JSON:
{"type": {"label": str (singular, Title Case), "plural_label": str, "icon": one emoji, "number_prefix": 1-6 capital letters,
          "parent_entity_type": one of TARGETS or null, "parent_required": bool, "name_label": what its name is called or "",
          "description": one sentence},
 "fields": [{"key": snake_case, "label": str, "data_type": text|textarea|number|boolean|date|picklist|email|phone|lookup,
             "values": [str] (picklist only), "target": one of TARGETS (lookup only), "required": bool}],
 "states": [{"code": snake_case, "label": str, "editable": bool, "is_terminal": bool}] (first = where a new record starts; [] if none said),
 "transitions": [{"from_state": code or "*", "to_state": code, "action": snake_case verb}],
 "relationships": [{"to": one of TARGETS, "label": what this type calls the linked records, "reverse_label": what they call it}],
 "rules": [one plain-English validation rule per item, using the field labels - e.g. "if state is completed then report is mandatory"]}
Only what the description says or clearly implies; never a field for the name, number, state or parent (every record has them).
TARGETS (what lookups, parents and relationships may point to; "SELF" = the type being designed): """


def draft(db: Session, tenant_id: int, text: str) -> dict[str, Any]:
    """Plain English -> a checked plan. Nothing is saved."""
    from app.crm_core import llm

    text = str(text or "").strip()
    if len(text) < 3:
        raise ValueError("Describe the record type - its name, what it belongs to, its fields, states and rules")
    targets = _targets(db, tenant_id)
    raw, engine, notes = None, "rules", []
    target = llm.resolve(db, tenant_id)
    if target is not None:
        try:
            listing = ", ".join(sorted({f"{v}" for v in targets.values()})) + ", SELF"
            labels = "; ".join(f"{o['value']} = {o['label']}" for o in records.entity_options(db, tenant_id))
            raw = llm.chat_json(target, _PROMPT + listing + f"\n({labels})", text)
            engine = "ai"
        except Exception as exc:  # noqa: BLE001 - any AI failure falls back to the built-in reader
            notes.append(f"AI unavailable ({type(exc).__name__}) - read with the built-in rules")
    if not isinstance(raw, dict) or not (raw.get("type") or {}).get("label"):
        raw, engine = read(text, targets), "rules"
    plan = check(db, tenant_id, raw)
    plan["engine"], plan["source_text"] = engine, text
    plan["notes"] = notes + plan["notes"]
    return plan


def check(db: Session, tenant_id: int, raw: dict[str, Any]) -> dict[str, Any]:
    """Any plan (the AI's, the reader's, or one the admin edited) -> the closed form, with notes on what was
    dropped or changed. An existing type of that name is extended, not duplicated."""
    notes: list[str] = []
    t = dict(raw.get("type") or {})
    label = str(t.get("label") or "").strip()[:100]
    if not label:
        raise ValueError("The plan needs the record type's name")
    existing = records.type_named(db, tenant_id, label)
    own_type = existing.entity_type if existing else f"x_{records.slug(t.get('key') or label)}"
    targets = _targets(db, tenant_id)

    def target_of(value: Any) -> str | None:
        text = str(value or "").strip()
        if not text:
            return None
        if text.upper() == "SELF" or text == own_type or text.lower() in (label.lower(), f"{label.lower()}s"):
            return own_type
        if text in records.PARENT_TYPES or (text.startswith("x_") and records.get_type(db, tenant_id, text)):
            return text
        return _target_of(text, targets)

    parent = target_of(t.get("parent_entity_type"))
    if t.get("parent_entity_type") and parent is None:
        notes.append(f"'{t['parent_entity_type']}' isn't a record type - the type stands alone")
    if parent == own_type:
        parent = None
    out_type = {
        "label": label, "plural_label": str(t.get("plural_label") or "").strip()[:100] or (existing.plural_label if existing else f"{label}s"),
        "icon": str(t.get("icon") or (existing.icon if existing else "📄"))[:10], "key": existing.key if existing else records.slug(t.get("key") or label),
        "number_prefix": re.sub(r"[^A-Z0-9]", "", str(t.get("number_prefix") or "").upper())[:10] or None,
        "parent_entity_type": existing.parent_entity_type if existing else parent, "parent_required": bool(t.get("parent_required") and parent),
        "name_label": str(t.get("name_label") or "").strip()[:100], "description": str(t.get("description") or "").strip()[:500],
        "existing": existing is not None, "entity_type": own_type,
    }
    have = {d.key for d in db.query(CustomFieldDefinition).filter(CustomFieldDefinition.tenant_id == tenant_id,
                                                                    CustomFieldDefinition.entity_type == own_type)} if existing else set()
    fields, seen = [], set()
    for f in raw.get("fields") or []:
        key = _key(f.get("key") or f.get("label") or "")
        if not key or key in seen or key in records.RESERVED_FIELD_KEYS:
            if key in records.RESERVED_FIELD_KEYS:
                notes.append(f"'{f.get('label') or key}' is something every record already has - left out")
            continue
        seen.add(key)
        data_type = str(f.get("data_type") or "text").lower()
        data_type = {"long_text": "textarea", "longtext": "textarea", "int": "number", "integer": "number", "decimal": "number", "bool": "boolean",
                     "choice": "picklist", "select": "picklist", "enum": "picklist", "reference": "lookup", "link": "lookup"}.get(data_type, data_type)
        if data_type not in CUSTOM_FIELD_DATA_TYPES:
            notes.append(f"{f.get('label') or key}: '{data_type}' isn't a field type - made it text")
            data_type = "text"
        values = [str(v).strip() for v in (f.get("values") or []) if str(v).strip()] if data_type == "picklist" else []
        if data_type == "picklist" and len(values) < 2:
            notes.append(f"{f.get('label') or key}: a choice needs at least two values - made it text")
            data_type = "text"
        target = target_of(f.get("target")) if data_type == "lookup" else None
        if data_type == "lookup" and target is None:
            notes.append(f"{f.get('label') or key}: '{f.get('target')}' isn't a record type - made it text")
            data_type = "text"
        fields.append({"key": key, "label": str(f.get("label") or key.replace("_", " ").title())[:150], "data_type": data_type, "values": values,
                       "target": target, "required": bool(f.get("required")), "exists": key in have})
    states, codes = [], []
    for s in raw.get("states") or []:
        code = _key(s.get("code") or s.get("label") or "", 50)
        if code and code not in codes:
            codes.append(code)
            states.append({"code": code, "label": str(s.get("label") or code.replace("_", " ").title())[:100],
                           "editable": bool(s.get("editable", True)), "is_terminal": bool(s.get("is_terminal", False))})
    transitions = []
    for tr in raw.get("transitions") or []:
        a, b = str(tr.get("from_state") or "").strip(), _key(tr.get("to_state") or "", 50)
        a = "*" if a == "*" else _key(a, 50)
        if b in codes and (a == "*" or a in codes) and a != b:
            transitions.append({"from_state": a, "to_state": b, "action": _key(tr.get("action") or f"to_{b}", 50) or f"to_{b}",
                                "allowed_roles": str(tr.get("allowed_roles") or "")})
    if states and not transitions and len(states) > 1:
        notes.append("No moves between the states were given - each state leads to the next")
        transitions = [{"from_state": a["code"], "to_state": b["code"], "action": ACTION_OF.get(b["code"], f"to_{b['code']}"), "allowed_roles": ""}
                       for a, b in zip(states, states[1:])]
    relationships = []
    for r in raw.get("relationships") or []:
        target = target_of(r.get("to"))
        if target is None:
            notes.append(f"Relationship to '{r.get('to')}': not a record type - left out")
            continue
        relationships.append({"to": target, "to_label": records.label_of(db, tenant_id, target) if target != own_type else label,
                              "label": str(r.get("label") or "").strip()[:100] or f"{records.label_of(db, tenant_id, target)}s",
                              "reverse_label": str(r.get("reverse_label") or "").strip()[:100] or out_type["plural_label"]})
    rules = [str(r).strip()[:500] for r in raw.get("rules") or [] if str(r).strip()]
    return {"type": out_type, "fields": fields, "states": states, "transitions": transitions, "relationships": relationships,
            "rules": rules, "notes": notes}


# ---------------------------------------------------------------- build


def build(db: Session, tenant_id: int, plan: dict[str, Any]) -> dict[str, Any]:
    """Creates (or extends) the record type from a plan - fields, state model, relationships, rules. A rule the
    rule compiler can't read is reported, not fatal; everything else is all-or-nothing with the caller's commit."""
    from app.crm_core import page_service
    from app.models.lifecycle import StateModel, StateModelState, StateModelTransition

    plan = check(db, tenant_id, plan)
    t = plan["type"]
    report: dict[str, Any] = {"created_type": False, "fields": [], "states": [], "relationships": [], "rules": [], "notes": list(plan["notes"])}
    rt = records.type_named(db, tenant_id, t["label"])
    if rt is None:
        rt = records.create_type(db, tenant_id, label=t["label"], plural_label=t["plural_label"], key=t["key"], description=t["description"],
                                 icon=t["icon"], number_prefix=t["number_prefix"], parent_entity_type=t["parent_entity_type"],
                                 parent_required=t["parent_required"], name_label=t["name_label"])
        report["created_type"] = True
    position = db.query(CustomFieldDefinition).filter(CustomFieldDefinition.tenant_id == tenant_id,
                                                      CustomFieldDefinition.entity_type == rt.entity_type).count()
    for f in plan["fields"]:
        if records.get_type(db, tenant_id, rt.entity_type) and db.query(CustomFieldDefinition.id).filter(
                CustomFieldDefinition.tenant_id == tenant_id, CustomFieldDefinition.entity_type == rt.entity_type,
                CustomFieldDefinition.key == f["key"]).first():
            continue
        target = rt.entity_type if f["target"] and f["target"] == t["entity_type"] else f["target"]
        db.add(CustomFieldDefinition(tenant_id=tenant_id, entity_type=rt.entity_type, key=f["key"], label=f["label"], data_type=f["data_type"],
                                     allowed_values=",".join(f["values"]) if f["data_type"] == "picklist" else target,
                                     required=f["required"], is_system=False, section="Details", position=position))
        position += 1
        report["fields"].append(f["key"])
    db.flush()
    if plan["states"]:
        current = lifecycle_service.resolve_state_model(db, tenant_id, rt.entity_type)
        states = list(current.states.values()) if current.configured else []
        codes = [s.code for s in states]
        new_states = [s for s in plan["states"] if s["code"] not in codes]
        keep = [{"code": s.code, "label": s.label, "editable": s.editable, "is_terminal": s.is_terminal} for s in states] + new_states
        transitions = [{"from_state": x.from_state, "to_state": x.to_state, "action": x.action, "allowed_roles": x.allowed_roles}
                       for x in (current.transitions if current.configured else [])]
        transitions += [x for x in plan["transitions"] if not any(y["from_state"] == x["from_state"] and y["to_state"] == x["to_state"] for y in transitions)]
        for old in db.query(StateModel).filter(StateModel.tenant_id == tenant_id, StateModel.entity_type == rt.entity_type):
            db.delete(old)
        db.flush()
        model = StateModel(tenant_id=tenant_id, entity_type=rt.entity_type, name=f"{rt.label} lifecycle")
        db.add(model)
        db.flush()
        for i, s in enumerate(keep):
            db.add(StateModelState(state_model_id=model.id, code=s["code"], label=s["label"], sequence=i, editable=s["editable"],
                                   is_terminal=s["is_terminal"], built_in=False))
        for x in transitions:
            db.add(StateModelTransition(state_model_id=model.id, from_state=x["from_state"], to_state=x["to_state"], action=x["action"],
                                        intent_type=None, allowed_roles=x["allowed_roles"]))
        db.flush()
        report["states"] = [s["code"] for s in new_states]
        if not current.configured and records.record_count(db, rt):
            first = keep[0]["code"]
            from app.models.custom_records import CustomRecord

            moved = db.query(CustomRecord).filter(CustomRecord.type_id == rt.id, CustomRecord.state.notin_([s["code"] for s in keep]))
            for rec in moved:
                rec.state = first
            report["notes"].append(f"Records in states the new model doesn't have start in '{first}'")
    for r in plan["relationships"]:
        to = rt.entity_type if r["to"] == t["entity_type"] else r["to"]
        exists = any(x.to_entity_type == to and x.label.lower() == r["label"].lower() for x in records.list_relationships(db, tenant_id, rt.entity_type))
        if exists:
            continue
        try:
            rel = records.create_relationship(db, tenant_id, from_entity_type=rt.entity_type, to_entity_type=to, label=r["label"],
                                              reverse_label=r["reverse_label"])
            report["relationships"].append(rel.key)
        except ValueError as exc:
            report["notes"].append(f"Relationship {r['label']}: {exc}")
    for text in plan["rules"]:
        savepoint = db.begin_nested()
        try:
            rule = page_service.save_rule(db, tenant_id, rt.entity_type, text)
            savepoint.commit()
            report["rules"].append({"id": rule.id, "text": text, "explanation": rule.name})
        except ValueError as exc:
            savepoint.rollback()
            report["notes"].append(f"Rule \"{text}\" wasn't added: {exc}")
    db.flush()
    report["type"] = records.type_out(db, tenant_id, rt)
    return report


def summary(plan: dict[str, Any]) -> str:
    t = plan["type"]
    parts = [f"{'Extend' if t.get('existing') else 'New'} record type {t['label']}"]
    if t.get("parent_entity_type"):
        parts.append(f"hangs off {t['parent_entity_type']}{' (required)' if t.get('parent_required') else ''}")
    if plan["fields"]:
        parts.append(f"{len(plan['fields'])} field(s): " + ", ".join(f"{f['label']} ({f['data_type']}{' → ' + f['target'] if f['target'] else ''})" for f in plan["fields"]))
    if plan["states"]:
        parts.append("states: " + " → ".join(s["label"] for s in plan["states"]))
    if plan["relationships"]:
        parts.append("related to: " + ", ".join(r["label"] for r in plan["relationships"]))
    if plan["rules"]:
        parts.append(f"{len(plan['rules'])} rule(s)")
    return "; ".join(parts)


def plan_json(plan: dict[str, Any]) -> str:
    return json.dumps(plan, default=str)

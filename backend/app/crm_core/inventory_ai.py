"""Inventory in plain English - the same AI framework as Flow Studio and Fulfillment:
the tenant's model (its own on-server model or an external API, Admin -> AI) answers
within a closed schema, its answer is checked against the tenant's real types,
locations and values, and the built-in reader answers whenever there is no model or
the model's answer is unusable.

  interpret(text)   "free GPON ports at BLR-EX-01", "ONTs in stock in the Bangalore
                    store", "what does order 12 use", "devices that are down"
                    -> a search filter (inventory_service.search) and how it was read
  draft_type(text)  "A DSLAM is a network device in an exchange or cabinet with a
                    management IP, a port count and firmware (required)"
                    -> an item type to review and save
"""

from __future__ import annotations

import json
import re
from typing import Any

from sqlalchemy.orm import Session

from app.crm_core import inventory_service as inv
from app.models.inventory import ITEM_KINDS, LIFECYCLE, OPER_STATUSES, InvItem, InvItemType

_LIFECYCLE_WORDS = [
    (r"\b(?:free|available|spare|unused|unassigned|in\s+stock|idle)\b", "available"),
    (r"\b(?:reserved|held|on\s+hold|booked)\b", "reserved"),
    (r"\b(?:in\s+use|in\s+service|used|assigned|live|allocated)\b", "in_use"),
    (r"\b(?:in\s+maintenance|under\s+maintenance|maintenance)\b", "maintenance"),
    (r"\b(?:retired|decommissioned|scrapped)\b", "retired"),
    (r"\b(?:planned|future|not\s+yet\s+built)\b", "planned"),
]
_OPER_WORDS = [(r"\b(?:down|faulty|failed|failing|offline|dead)\b", "down"), (r"\b(?:degraded|flapping|slow)\b", "degraded")]


def _model(db: Session, tenant_id: int):
    from app.crm_core import llm

    return llm.resolve(db, tenant_id)


def _types(db: Session, tenant_id: int) -> list[InvItemType]:
    inv.ensure_types(db, tenant_id)
    return db.query(InvItemType).filter(InvItemType.tenant_id == tenant_id, InvItemType.is_active.is_(True)).all()


def _names(t: InvItemType) -> list[str]:
    base = {t.code.replace("_", " "), re.sub(r"\s*\(.*\)", "", t.name.lower()).strip(), t.name.lower()}
    if m := re.search(r"\(([^)]+)\)", t.name):
        base.add(m.group(1).lower())
    for part in re.split(r"\s*/\s*", re.sub(r"\s*\(.*\)", "", t.name.lower())):
        base.add(part.strip())
    return sorted({b for b in base if b}, key=len, reverse=True)


# ================================================================== search

def rule_filter(db: Session, tenant_id: int, text: str) -> tuple[dict[str, Any], list[str]]:
    """The filter the built-in reader takes from the words, and how it read them."""
    f: dict[str, Any] = {}
    said: list[str] = []
    lowered = f" {text.lower()} "
    for pattern, value in _LIFECYCLE_WORDS:
        if re.search(pattern, lowered):
            f["lifecycle"] = value
            said.append(f"lifecycle {value.replace('_', ' ')}")
            break
    for pattern, value in _OPER_WORDS:
        if re.search(pattern, lowered):
            f["oper_status"] = value
            said.append(f"operational status {value}")
            break
    best: tuple[int, int, InvItemType] | None = None
    for t in _types(db, tenant_id):
        for name in _names(t):
            # "ONTs in the Bangalore store" asks for ONTs at a place: a thing beats a kind of place, then the longer name wins
            rank = (0 if t.kind == "location" else 1, len(name))
            if re.search(rf"\b{re.escape(name)}(?:e?s)?\b", lowered) and (best is None or rank > best[:2]):
                best = (*rank, t)
    best = (best[1], best[2]) if best else None
    if re.search(r"\b(?:devices?|equipment|kit|hardware)\b", lowered) and best is None:
        f["kind"] = "physical"
        said.append("equipment")
    if re.search(r"\b(?:services?)\b", lowered) and best is None:
        f["kind"] = "service"
        said.append("services")
    if best:
        f["type"] = best[1].code
        said.append(best[1].name)
    if m := re.search(r"\border\s*#?\s*(\d+)\b", lowered):
        f["order_id"] = int(m.group(1))
        said.append(f"order {m.group(1)}")
    if m := re.search(r"\b(?:customer|for)\s+#?(\d+)\b", lowered):
        f["party_id"] = int(m.group(1))
        said.append(f"customer {m.group(1)}")
    locations = db.query(InvItem).filter(InvItem.tenant_id == tenant_id, InvItem.kind == "location", InvItem.lifecycle != "retired").all()
    found_loc = None
    for loc in sorted(locations, key=lambda l: -len(l.identifier)):
        words = [loc.identifier.lower(), loc.name.lower()] + [c.strip().lower() for c in str(json.loads(loc.attributes_json or "{}").get("coverage") or "").split(",")]
        hit = next((w for w in words if len(w) >= 3 and re.search(rf"(?<![\w-]){re.escape(w)}(?![\w-])", lowered)), None)
        if hit:
            # the named place itself, else the one whose name says it most exactly
            found_loc = loc if hit in (loc.identifier.lower(), loc.name.lower()) else (found_loc or loc)
            if hit in (loc.identifier.lower(), loc.name.lower()):
                break
    if found_loc is None and (m := re.search(r"\b(?:at|in|near|around)\s+(?:the\s+)?([a-z][\w -]{2,40}?)(?:\s+(?:that|which|with|for|and)\b|[?.!,]|$)", lowered)):
        place = inv.serving_location(db, tenant_id, m.group(1))
        found_loc = place
    if found_loc is not None:
        f["location"] = found_loc.identifier
        said.append(f"at {found_loc.identifier}")
    if best is not None:
        for a in inv.attributes(db, best[1]):
            for v in a.get("values") or []:
                if re.search(rf"(?<![\w-]){re.escape(v.lower())}(?![\w-])", lowered):
                    f.setdefault("attributes", {})[a["key"]] = v
                    said.append(f"{a['label'].lower()} {v}")
    if m := re.search(r"['\"“]([^'\"”]{2,60})['\"”]", text):
        f["text"] = m.group(1)
        said.append(f"matching '{m.group(1)}'")
    elif not f and text.strip():
        f["text"] = text.strip()[:60]
        said.append(f"matching '{f['text']}'")
    return f, said


def _clean_filter(db: Session, tenant_id: int, raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        return {}
    f: dict[str, Any] = {}
    t = inv.type_by_code(db, tenant_id, raw.get("type") or "") or inv.find_type(db, tenant_id, raw.get("type") or "") if raw.get("type") else None
    if t is not None:
        f["type"] = t.code
    if raw.get("kind") in ITEM_KINDS:
        f["kind"] = raw["kind"]
    if raw.get("lifecycle") in LIFECYCLE:
        f["lifecycle"] = raw["lifecycle"]
    if raw.get("oper_status") in OPER_STATUSES:
        f["oper_status"] = raw["oper_status"]
    if raw.get("location"):
        try:
            f["location"] = inv.resolve_item(db, tenant_id, raw["location"]).identifier
        except ValueError:
            pass
    for key in ("order_id", "party_id"):
        if str(raw.get(key) or "").isdigit():
            f[key] = int(raw[key])
    if raw.get("text"):
        f["text"] = str(raw["text"])[:60]
    if isinstance(raw.get("attributes"), dict) and t is not None:
        known = {a["key"] for a in inv.attributes(db, t)}
        f["attributes"] = {k: v for k, v in raw["attributes"].items() if k in known}
        if not f["attributes"]:
            f.pop("attributes")
    return f


def interpret(db: Session, tenant_id: int, text: str) -> dict[str, Any]:
    """{"filter", "read_as", "source": "llm" | "rules", "model", "results"}."""
    text = str(text or "").strip()
    if not text:
        raise ValueError("What are you looking for?")
    target = _model(db, tenant_id)
    if target is not None:
        try:
            from app.crm_core import llm

            types = [{"code": t.code, "name": t.name, "kind": t.kind,
                      "attributes": [{"key": a["key"], **({"values": a["values"]} if a.get("values") else {})} for a in inv.attributes(db, t)]}
                     for t in _types(db, tenant_id)]
            locations = [{"identifier": l.identifier, "name": l.name} for l in
                         db.query(InvItem).filter(InvItem.tenant_id == tenant_id, InvItem.kind == "location", InvItem.lifecycle != "retired").limit(150)]
            answer = llm.chat_json(target,
                                   "You turn a telecom network-inventory question into a search filter. Output strict JSON "
                                   '{"type": "", "kind": "", "lifecycle": "", "oper_status": "", "location": "", "order_id": null, '
                                   '"party_id": null, "text": "", "attributes": {}, "read_as": "one short sentence"} - leave out what the '
                                   f"question doesn't say. type: one of these codes {json.dumps(types)}. kind: {list(ITEM_KINDS)}. lifecycle: "
                                   f"{list(LIFECYCLE)} (free / in stock = available). oper_status: {list(OPER_STATUSES)}. location: an identifier "
                                   f"from {json.dumps(locations)}. Never invent codes, locations or values.", text)
            f = _clean_filter(db, tenant_id, answer)
            if f:
                return {"filter": f, "read_as": str(answer.get("read_as") or "")[:200], "source": "llm", "model": target.label,
                        "results": inv.search(db, tenant_id, f)}
        except Exception:  # noqa: BLE001 - the reader answers instead
            pass
    f, said = rule_filter(db, tenant_id, text)
    return {"filter": f, "read_as": ("Looking for " + ", ".join(said)) if said else "", "source": "rules", "model": None,
            "results": inv.search(db, tenant_id, f)}


# ================================================================== item types

def _attr_from_phrase(phrase: str) -> dict[str, Any] | None:
    p = re.sub(r"^\s*(?:an?|the|its|their)\s+", "", phrase.strip(" .;:"), flags=re.I)
    required = bool(re.search(r"\((?:required|mandatory|must)\)|\b(?:required|mandatory)\b", p, re.I))
    unique = bool(re.search(r"\(unique\)|\bunique\b", p, re.I))
    values = None
    if m := re.search(r"\((?:one of\s+)?([^)]*[,/|][^)]*)\)|\bone of\s+(.+)$", p, re.I):
        raw = m.group(1) or m.group(2)
        if not re.fullmatch(r"\s*(?:required|mandatory|unique)(?:\s*,\s*(?:required|mandatory|unique))*\s*", raw, re.I):
            values = [v.strip() for v in re.split(r"\s*(?:,|/|\||\bor\b)\s*", raw) if v.strip() and v.strip().lower() not in ("required", "mandatory", "unique")]
    label = re.sub(r"\(.*?\)|\bone of\b.*$|\b(?:required|mandatory|unique)\b", "", p, flags=re.I).strip(" ,-")
    label = re.sub(r"\s+", " ", label)
    if not label or len(label) > 60:
        return None
    lowered = label.lower()
    kind = ("picklist" if values else "ip" if re.search(r"\bip\b|\baddress\s+ip\b|\bip\s+address", lowered)
            else "date" if re.search(r"\bdate\b|\bwhen\b|\binstalled\s+on\b|\bexpiry\b|\bwarranty\s+end", lowered)
            else "boolean" if re.search(r"^(?:is|has)\s|\bwhether\b|\byes/no\b|\bflag\b", lowered)
            else "number" if re.search(r"\b(?:number|count|slots?|ports?|capacity|channels?|strands?|size|speed\s+in|mbps|gbps|kg|watts?|height|length)\b", lowered)
            else "text")
    key = re.sub(r"[^a-z0-9]+", "_", re.sub(r"^(?:number of|count of)\s+", "", lowered)).strip("_")[:40]
    return {"key": key, "label": label[:1].upper() + label[1:], "type": kind, "required": required, "unique": unique, **({"values": values} if values else {})}


def rule_type(db: Session, tenant_id: int, text: str) -> dict[str, Any]:
    t = text.strip()
    name = ""
    if m := re.search(r"\b(?:called|named)\s+['\"“]?([\w /-]{2,60}?)['\"”]?(?:[,.:]|\s+(?:is|has|with|that|which)\b|$)", t, re.I):
        name = m.group(1)
    elif m := re.match(r"\s*(?:an?\s+|the\s+)?([\w /()-]{2,60}?)\s+(?:is|are|has|have|with|sits|lives|:)\b", t, re.I):
        name = m.group(1)
    name = re.sub(r"^(?:new\s+)?(?:item\s+)?(?:type\s+)?(?:for\s+)?", "", name.strip(), flags=re.I) or "New item type"
    lowered = t.lower()
    kind = ("location" if re.search(r"\b(?:a|is\s+a|kind\s+of)\s+(?:location|site|building|room|place|area|warehouse|store)\b", lowered)
            else "service" if re.search(r"\b(?:a|is\s+a)\s+(?:customer\s+)?service\b", lowered)
            else "logical" if re.search(r"\b(?:logical|an?\s+(?:id|identifier|number|range|address)\b|allocated\s+from\s+a\s+pool)", lowered)
            else "physical")
    parent = ""
    if m := re.search(r"\b(?:is\s+a|kind\s+of|type\s+of)\s+([\w ]{3,40}?)(?:\s+(?:that|which|with|in|inside|and|,)|[.,]|$)", lowered):
        found = inv.find_type(db, tenant_id, m.group(1))
        if found is not None and found.kind == kind:
            parent = found.code
    contained: list[str] = []
    if m := re.search(r"\b(?:sits?|lives?|goes|installed|mounted|placed|kept|fitted)\s+(?:in|inside|into|at)\s+(.+?)(?:[.;]|\s+with\b|\s+and\s+(?:has|holds|tracks)\b|$)", lowered):
        for chunk in re.split(r"\s*(?:,|\bor\b|\band\b)\s*", m.group(1)):
            found = inv.find_type(db, tenant_id, re.sub(r"^(?:an?|the)\s+", "", chunk.strip()))
            if found is not None and found.code not in contained:
                contained.append(found.code)
    attrs: list[dict[str, Any]] = []
    if m := re.search(r"\b(?:with|has|have|tracks?|records?|attributes?|fields?|keeps?)\b[:\s]+(.+)$", t, re.I | re.S):
        body = re.split(r"\.\s+(?=[A-Z])", m.group(1))[0]
        body = re.sub(r"\b(?:sits?|lives?|installed|mounted)\s+(?:in|inside|at)\s+.+$", "", body, flags=re.I)
        depth, chunk, parts = 0, "", []
        for ch in body:  # split on commas / "and" outside brackets
            depth += ch == "("
            depth -= ch == ")"
            if ch == "," and depth == 0:
                parts.append(chunk)
                chunk = ""
            else:
                chunk += ch
        parts.append(chunk)
        for part in parts:
            for piece in re.split(r"\s+and\s+(?![^()]*\))", part):
                a = _attr_from_phrase(piece)
                if a and a["key"] and a["key"] not in {x["key"] for x in attrs}:
                    attrs.append(a)
    resource = ""
    if m := re.search(r"\b(?:handed\s+out|allocated|reserved|used)\s+as\s+(?:an?\s+)?([a-z_ ]{2,30}?)(?:[.,]|$)", lowered):
        resource = re.sub(r"[^a-z0-9]+", "_", m.group(1)).strip("_")
    naming = m.group(1) if (m := re.search(r"\bnamed\s+like\s+['\"“]?([^'\"”\s]+)", t, re.I)) else ""
    return {"code": re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")[:60], "name": name[:1].upper() + name[1:], "kind": kind,
            "parent_code": parent, "attributes": attrs, "contained_in": contained, "naming": naming, "resource_type": resource, "description": t[:500]}


def draft_type(db: Session, tenant_id: int, text: str) -> dict[str, Any]:
    """{"type": the draft (not saved), "source", "model", "notes"}."""
    text = str(text or "").strip()
    if len(text) < 8:
        raise ValueError("Describe the item type - what it is, where it sits and what it records")
    target = _model(db, tenant_id)
    if target is not None:
        try:
            from app.crm_core import llm

            types = [{"code": t.code, "name": t.name, "kind": t.kind} for t in _types(db, tenant_id)]
            answer = llm.chat_json(target,
                                   "You design an item type for a telecom network inventory. Output strict JSON "
                                   '{"code": "snake_case", "name": "...", "kind": "location|physical|logical|service", "parent_code": "", '
                                   '"attributes": [{"key": "snake_case", "label": "...", "type": "text|number|boolean|picklist|date|ip", '
                                   '"required": false, "unique": false, "values": []}], "contained_in": ["type codes"], "naming": "", '
                                   '"resource_type": "", "description": "..."}. parent_code (a type it inherits attributes from) and '
                                   f"contained_in only from these existing types: {json.dumps(types)}. Picklists need their values. "
                                   "resource_type only if orders are given items of this type (e.g. port, ont, vlan).", text)
            draft = _clean_type(db, tenant_id, answer)
            if draft["code"]:
                return {"type": draft, "source": "llm", "model": target.label, "notes": ""}
        except Exception:  # noqa: BLE001
            pass
    draft = _clean_type(db, tenant_id, rule_type(db, tenant_id, text))
    return {"type": draft, "source": "rules", "model": None,
            "notes": "Drafted by the built-in reader - check the attributes and where it may sit before saving."}


def _clean_type(db: Session, tenant_id: int, raw: Any) -> dict[str, Any]:
    raw = raw if isinstance(raw, dict) else {}
    known = {t.code for t in _types(db, tenant_id)}
    attrs = []
    for a in raw.get("attributes") or []:
        if not isinstance(a, dict) or not (a.get("key") or a.get("label")):
            continue
        kind = a.get("type") if a.get("type") in inv.ATTRIBUTE_TYPES else "text"
        values = [str(v) for v in a.get("values") or [] if str(v).strip()]
        if kind == "picklist" and not values:
            kind = "text"
        attrs.append({"key": re.sub(r"[^a-z0-9_]", "_", str(a.get("key") or a.get("label")).lower()).strip("_")[:40],
                      "label": str(a.get("label") or a.get("key"))[:80], "type": kind, "required": bool(a.get("required")),
                      "unique": bool(a.get("unique")), **({"values": values} if values else {})})
    return {"code": re.sub(r"[^a-z0-9_]", "_", str(raw.get("code") or raw.get("name") or "").lower()).strip("_")[:60],
            "name": str(raw.get("name") or raw.get("code") or "")[:120], "kind": raw.get("kind") if raw.get("kind") in ITEM_KINDS else "physical",
            "parent_code": raw.get("parent_code") if raw.get("parent_code") in known else "", "attributes": attrs,
            "contained_in": [c for c in raw.get("contained_in") or [] if c in known], "naming": str(raw.get("naming") or "")[:150],
            "resource_type": re.sub(r"[^a-z0-9_]", "_", str(raw.get("resource_type") or "").lower())[:60],
            "description": str(raw.get("description") or "")[:500]}

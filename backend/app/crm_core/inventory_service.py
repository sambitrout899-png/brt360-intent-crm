"""Inventory - the network as recorded: types, items, containment, relationships,
pools, allocation, impact, capacity, data health and reconciliation (models/inventory.py).

  schema         ensure_types (the built-in catalog every tenant starts from), save_type,
                 attributes (with what a type inherits), check_attributes
  items          create_item / update_item / set_lifecycle / set_oper_status / relate,
                 receive_stock (many at once, named by the type's naming rule)
  allocation     technical_action - Fulfillment's SOM actions: design a service record,
                 assign logical values from pools, reserve physical items at the location
                 serving the order (nearest first), activate, release. Idempotent per
                 order: a retried step gets what it already holds.
  insight        impact (what an item takes down with it), capacity, health
  reconciliation reconcile - a discovery source's report against the record: identified
                 by type + identifier, else serial; attributes kept by source priority;
                 what a full scan no longer sees is flagged, never deleted
  housekeeping   expire_reservations - reservations nobody is using any more go back

Everything that changes an item writes its history.
"""

from __future__ import annotations

import datetime as dt
import ipaddress
import json
import re
import uuid
from typing import Any

from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.models.inventory import (
    ITEM_KINDS, LIFECYCLE, OPER_STATUSES, POOL_TYPES, RELATIONSHIPS, SOURCE_PRIORITY, TRANSITIONS,
    InvHistory, InvItem, InvItemType, InvPool, InvReconcileRun, InvRelationship,
)
from app.timeutils import utcnow

ATTRIBUTE_TYPES = ("text", "number", "boolean", "picklist", "date", "ip")
RESERVATION_DAYS = 30  # a reservation no open order holds goes back after this long
STALE_DAYS = 90  # physical items / locations not verified against the network for this long
ACTION_STATUS = {"design": "designed", "assign": "assigned", "reserve": "reserved", "activate": "active", "release": "released"}


def _j(text: str | None, default: Any) -> Any:
    try:
        value = json.loads(text) if text else default
    except ValueError:
        return default
    return default if value is None else value


def _dumps(value: Any) -> str:
    return json.dumps(value, default=str)


# ================================================================== the built-in catalog

def _a(key: str, label: str, type_: str = "text", required: bool = False, unique: bool = False, values: list[str] | None = None) -> dict[str, Any]:
    return {"key": key, "label": label, "type": type_, "required": required, "unique": unique, **({"values": values} if values else {})}


_COVERAGE = _a("coverage", "Areas served (comma-separated: localities, PIN codes)")
_ADDRESS = _a("address", "Address")
# code -> (name, kind, parent_code, attributes, contained_in, naming, resource_type, description)
BUILTIN_TYPES: dict[str, tuple] = {
    # locations
    "region": ("Region", "location", "", [_COVERAGE], "", "", "", "A city or area - its exchanges and stores sit inside it"),
    "exchange": ("Exchange / central office", "location", "", [_COVERAGE, _ADDRESS], "region", "", "", "Where the network equipment serving an area lives"),
    "cabinet": ("Street cabinet", "location", "", [_COVERAGE, _ADDRESS], "exchange,region", "", "", "Outside plant: splitters and fibre joints near the customer"),
    "store": ("Store / warehouse", "location", "", [_ADDRESS], "region,exchange", "", "", "Equipment in stock, waiting to go to a customer"),
    "premises": ("Customer premises", "location", "", [_ADDRESS], "", "", "", "Where a customer's equipment is installed"),
    # physical
    "network_device": ("Network device", "physical", "", [_a("management_ip", "Management IP", "ip"), _a("software", "Software version")],
                       "exchange,cabinet,region", "", "", "Any powered network element"),
    "olt": ("Optical line terminal (OLT)", "physical", "network_device", [_a("slots", "Card slots", "number")], "exchange",
            "{location}-OLT-{n:02d}", "", "Fibre access head-end; holds line cards"),
    "switch": ("Switch", "physical", "network_device", [], "exchange,cabinet", "{location}-SW-{n:02d}", "", "Aggregation / access switch"),
    "card": ("Line card", "physical", "", [_a("slot", "Slot", "number")], "olt,switch", "", "", "A card in a device slot; holds ports"),
    "port": ("Port", "physical", "", [_a("speed", "Speed", "picklist", values=["1G", "10G", "GPON", "XGS-PON"])], "card,olt,switch,router", "",
             "port", "A customer-facing or uplink port"),
    "splitter": ("Optical splitter", "physical", "", [_a("ratio", "Split ratio", "picklist", values=["1:8", "1:16", "1:32", "1:64"])],
                 "cabinet,exchange", "{location}-SPL-{n:02d}", "", "Splits one fibre to many customers"),
    "splitter_port": ("Splitter port", "physical", "", [], "splitter", "", "splitter_port", "One customer leg of a splitter"),
    "fibre_cable": ("Fibre cable", "physical", "", [_a("strands", "Strands", "number")], "exchange,cabinet", "", "", "A cable of fibre strands"),
    "fibre_strand": ("Fibre strand", "physical", "", [], "fibre_cable", "", "fibre_strand", "One strand of a fibre cable"),
    "ont": ("ONT", "physical", "", [_a("mac", "MAC address", unique=True)], "store,premises", "", "ont", "Optical network terminal for the customer"),
    "cpe": ("CPE", "physical", "", [_a("mac", "MAC address", unique=True)], "store,premises", "", "cpe", "Customer premises equipment"),
    "router": ("Router", "physical", "", [_a("mac", "MAC address", unique=True)], "store,premises,exchange", "", "router", "Managed router"),
    "sim": ("SIM", "physical", "", [_a("imsi", "IMSI", unique=True)], "store,premises", "", "sim", "SIM card"),
    # logical
    "vlan": ("VLAN", "logical", "", [], "", "", "vlan", "A VLAN id"),
    "ip_block": ("IPv4 block", "logical", "", [], "", "", "ip_block", "An IPv4 subnet"),
    "ipv6_prefix": ("IPv6 prefix", "logical", "", [], "", "", "ipv6_prefix", "An IPv6 delegated prefix"),
    "phone_number": ("Phone number", "logical", "", [], "", "", "phone_number", "A telephone number"),
    "sip_trunk": ("SIP trunk", "logical", "", [_a("channels", "Channels", "number")], "", "", "sip_trunk", "A SIP trunk id"),
    "circuit_id": ("Circuit id", "logical", "", [], "", "", "circuit_id", "A circuit identifier"),
    "service_id": ("Service id", "logical", "", [], "", "", "service_id", "A service identifier"),
    # service
    "service": ("Customer service", "service", "", [_a("product", "Product")], "", "", "", "A customer's service as delivered - it uses its resources"),
}


def ensure_types(db: Session, tenant_id: int) -> None:
    """The built-in catalog, added once per tenant (the tenant can change or extend it)."""
    have = {c for (c,) in db.query(InvItemType.code).filter(InvItemType.tenant_id == tenant_id)}
    for code, (name, kind, parent, attrs, contained, naming, resource, desc) in BUILTIN_TYPES.items():
        if code not in have:
            db.add(InvItemType(tenant_id=tenant_id, code=code, name=name, kind=kind, parent_code=parent, attributes_json=_dumps(attrs),
                               contained_in=contained, naming=naming, resource_type=resource, description=desc, builtin=True))
    db.flush()


def type_by_code(db: Session, tenant_id: int, code: str) -> InvItemType | None:
    ensure_types(db, tenant_id)
    return db.query(InvItemType).filter(InvItemType.tenant_id == tenant_id, InvItemType.code == str(code).strip().lower()).first()


def find_type(db: Session, tenant_id: int, words: str) -> InvItemType | None:
    """A type by its code, name or plural ("ONTs", "optical splitter")."""
    w = re.sub(r"\s+", " ", str(words or "").strip().lower())
    if not w:
        return None
    exact = type_by_code(db, tenant_id, w.replace(" ", "_"))
    if exact:
        return exact
    singular = re.sub(r"(?<=[a-z])e?s$", "", w)
    for t in db.query(InvItemType).filter(InvItemType.tenant_id == tenant_id, InvItemType.is_active.is_(True)):
        names = {t.code, t.code.replace("_", " "), t.name.lower(), re.sub(r"\s*\(.*\)", "", t.name.lower())}
        abbrev = re.search(r"\(([^)]+)\)", t.name)
        if abbrev:
            names.add(abbrev.group(1).lower())
        if w in names or singular in names or singular.replace(" ", "_") == t.code:
            return t
    return None


def attributes(db: Session, t: InvItemType) -> list[dict[str, Any]]:
    """Its attributes, with the ones it inherits first."""
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    chain, current = [], t
    while current is not None and current.code not in seen:
        seen.add(current.code)
        chain.append(current)
        current = (db.query(InvItemType).filter(InvItemType.tenant_id == t.tenant_id, InvItemType.code == current.parent_code).first()
                   if current.parent_code else None)
    keys: set[str] = set()
    for ty in reversed(chain):
        for a in _j(ty.attributes_json, []):
            if a.get("key") and a["key"] not in keys:
                keys.add(a["key"])
                out.append(a)
    return out


def type_out(db: Session, t: InvItemType) -> dict[str, Any]:
    count = db.query(InvItem.id).filter(InvItem.type_id == t.id, InvItem.lifecycle != "retired").count()
    return {"id": t.id, "code": t.code, "name": t.name, "kind": t.kind, "description": t.description, "parent_code": t.parent_code,
            "attributes": _j(t.attributes_json, []), "all_attributes": attributes(db, t),
            "contained_in": [c for c in t.contained_in.split(",") if c.strip()], "naming": t.naming, "resource_type": t.resource_type,
            "is_active": t.is_active, "builtin": t.builtin, "items": count}


def save_type(db: Session, tenant_id: int, data: dict[str, Any], type_id: int | None = None) -> InvItemType:
    ensure_types(db, tenant_id)
    code = re.sub(r"[^a-z0-9_]", "_", str(data.get("code") or data.get("name") or "").strip().lower()).strip("_")[:60]
    if not code:
        raise ValueError("A type needs a code (e.g. dslam)")
    if data.get("kind") not in ITEM_KINDS:
        raise ValueError(f"kind must be one of {', '.join(ITEM_KINDS)}")
    t = db.get(InvItemType, type_id) if type_id else None
    if type_id and (t is None or t.tenant_id != tenant_id):
        raise ValueError("Item type not found")
    clash = db.query(InvItemType).filter(InvItemType.tenant_id == tenant_id, InvItemType.code == code).first()
    if clash is not None and clash is not t:
        raise ValueError(f"There is already an item type '{code}'")
    if t is not None and t.code != code and db.query(InvItem.id).filter(InvItem.type_id == t.id).first():
        raise ValueError("Items of this type exist - its code can't change")
    attrs = []
    for a in data.get("attributes") or []:
        key = re.sub(r"[^a-z0-9_]", "_", str(a.get("key") or a.get("label") or "").strip().lower()).strip("_")
        if not key:
            continue
        kind = a.get("type") or "text"
        if kind not in ATTRIBUTE_TYPES:
            raise ValueError(f"Attribute '{key}': type must be one of {', '.join(ATTRIBUTE_TYPES)}")
        values = [str(v).strip() for v in (a.get("values") or []) if str(v).strip()]
        if kind == "picklist" and not values:
            raise ValueError(f"Attribute '{key}' is a picklist - give it its values")
        attrs.append(_a(key, str(a.get("label") or key.replace("_", " ").capitalize()), kind, bool(a.get("required")), bool(a.get("unique")), values or None))
    parent = str(data.get("parent_code") or "").strip().lower()
    known = {c for (c,) in db.query(InvItemType.code).filter(InvItemType.tenant_id == tenant_id)} | {code}
    if parent and parent not in known:
        raise ValueError(f"Unknown parent type '{parent}'")
    contained = [c.strip().lower() for c in (data.get("contained_in") or []) if str(c).strip()] if isinstance(data.get("contained_in"), list) \
        else [c.strip().lower() for c in str(data.get("contained_in") or "").split(",") if c.strip()]
    unknown = [c for c in contained if c not in known]
    if unknown:
        raise ValueError(f"Unknown type(s) it would sit in: {', '.join(unknown)}")
    if t is None:
        t = InvItemType(tenant_id=tenant_id)
        db.add(t)
    t.code, t.name, t.kind = code, str(data.get("name") or code.replace("_", " ").capitalize())[:120], data["kind"]
    t.description, t.parent_code, t.attributes_json = str(data.get("description") or ""), parent, _dumps(attrs)
    t.contained_in, t.naming = ",".join(contained), str(data.get("naming") or "")[:150]
    t.resource_type = re.sub(r"[^a-z0-9_]", "_", str(data.get("resource_type") or "").strip().lower())[:60]
    t.is_active = bool(data.get("is_active", True))
    db.flush()
    return t


def check_attributes(db: Session, t: InvItemType, values: dict[str, Any], item_id: int | None = None, partial: bool = False) -> dict[str, Any]:
    """The values, coerced to their types - or a ValueError naming every problem."""
    specs = {a["key"]: a for a in attributes(db, t)}
    out: dict[str, Any] = {}
    problems: list[str] = []
    for key, value in (values or {}).items():
        spec = specs.get(key)
        if spec is None:
            out[key] = value  # extra attributes are kept (discovery reports more than the schema knows)
            continue
        if value in (None, ""):
            out[key] = ""
            continue
        kind = spec.get("type", "text")
        try:
            if kind == "number":
                value = float(value) if "." in str(value) else int(value)
            elif kind == "boolean":
                value = str(value).strip().lower() in ("1", "true", "yes", "y", "on")
            elif kind == "picklist" and str(value) not in spec.get("values", []):
                problems.append(f"{spec['label']} must be one of {', '.join(spec['values'])}")
            elif kind == "ip":
                value = str(ipaddress.ip_interface(str(value).strip()).ip) if "/" not in str(value) else str(ipaddress.ip_network(str(value).strip(), strict=False))
            elif kind == "date":
                value = dt.date.fromisoformat(str(value)[:10]).isoformat()
        except ValueError:
            problems.append(f"{spec['label']}: '{value}' is not a valid {kind}")
            continue
        out[key] = value
    if not partial:
        for key, spec in specs.items():
            if spec.get("required") and out.get(key) in (None, ""):
                problems.append(f"{spec['label']} is required")
    for key, spec in specs.items():
        if spec.get("unique") and out.get(key) not in (None, ""):
            needle = f'"{key}": {json.dumps(out[key])}'
            q = db.query(InvItem).filter(InvItem.tenant_id == t.tenant_id, InvItem.type_id == t.id, InvItem.lifecycle != "retired",
                                         InvItem.attributes_json.contains(needle))
            if item_id:
                q = q.filter(InvItem.id != item_id)
            if q.first() is not None:
                problems.append(f"{spec['label']} {out[key]} is already recorded on another {t.name}")
    if problems:
        raise ValueError("; ".join(problems))
    return out


# ================================================================== items

def _history(db: Session, item: InvItem, action: str, changes: dict[str, Any], source: str = "manual", reference: str = "", actor_id: int | None = None) -> None:
    db.add(InvHistory(tenant_id=item.tenant_id, item_id=item.id, action=action, changes_json=_dumps(changes), source=source,
                      reference=reference[:120], actor_id=actor_id))


def _owned(db: Session, tenant_id: int, item_id: Any, what: str = "Item") -> InvItem:
    try:
        item = db.get(InvItem, int(item_id))
    except (TypeError, ValueError):
        item = None
    if item is None or item.tenant_id != tenant_id:
        raise ValueError(f"{what} {item_id} not found")
    return item


def resolve_item(db: Session, tenant_id: int, ref: Any) -> InvItem:
    """An item by id, BRT_ID, identifier or name."""
    text = str(ref or "").strip()
    if not text:
        raise ValueError("Which item?")
    if text.isdigit():
        item = db.get(InvItem, int(text))
        if item is not None and item.tenant_id == tenant_id:
            return item
    q = db.query(InvItem).filter(InvItem.tenant_id == tenant_id, InvItem.lifecycle != "retired")
    item = (q.filter(or_(InvItem.brt_id == text, InvItem.identifier == text)).first()
            or q.filter(or_(InvItem.identifier.ilike(text), InvItem.name.ilike(text))).first())
    if item is None:
        raise ValueError(f"No inventory item '{text}'")
    return item


def _location_of(db: Session, item: InvItem | None) -> int | None:
    seen: set[int] = set()
    while item is not None and item.id not in seen:
        seen.add(item.id)
        if item.kind == "location":
            return item.id
        item = db.get(InvItem, item.parent_id) if item.parent_id else None
    return None


def _check_identifier(db: Session, t: InvItemType, identifier: str, item_id: int | None = None) -> None:
    q = db.query(InvItem.id).filter(InvItem.tenant_id == t.tenant_id, InvItem.type_id == t.id, InvItem.identifier == identifier,
                                     InvItem.lifecycle != "retired")
    if item_id:
        q = q.filter(InvItem.id != item_id)
    if q.first() is not None:
        raise ValueError(f"{t.name} '{identifier}' is already recorded")


def _check_containment(db: Session, t: InvItemType, parent: InvItem | None) -> None:
    allowed = [c for c in t.contained_in.split(",") if c.strip()]
    if parent is None or not allowed:
        return
    parent_type = db.get(InvItemType, parent.type_id)
    # a type allowed inside "network_device" may sit in anything that inherits from it
    chain, current, seen = [], parent_type, set()
    while current is not None and current.code not in seen:
        seen.add(current.code)
        chain.append(current.code)
        current = (db.query(InvItemType).filter(InvItemType.tenant_id == t.tenant_id, InvItemType.code == current.parent_code).first()
                   if current.parent_code else None)
    if not set(chain) & set(allowed):
        raise ValueError(f"A {t.name} can't sit in a {parent_type.name} - only in: {', '.join(allowed)}")


def _name_for(db: Session, t: InvItemType, parent: InvItem | None) -> str:
    location = db.get(InvItem, _location_of(db, parent)) if parent is not None else None
    # numbered within its location when the name carries the location (BLR-EX-02-OLT-01), else across the tenant
    same = db.query(InvItem.id).filter(InvItem.tenant_id == t.tenant_id, InvItem.type_id == t.id)
    if location is not None and "{location}" in (t.naming or ""):
        same = same.filter(InvItem.location_id == location.id)
    n = same.count() + 1
    template = t.naming or "{type}-{n:05d}"
    for _ in range(1000):
        try:
            name = template.format(location=(location.identifier if location else "X"), type=t.code.upper(), n=n)
        except (KeyError, ValueError, IndexError):
            name = f"{t.code.upper()}-{n:05d}"
        if not db.query(InvItem.id).filter(InvItem.tenant_id == t.tenant_id, InvItem.type_id == t.id, InvItem.identifier == name,
                                           InvItem.lifecycle != "retired").first():
            return name
        n += 1
    return f"{t.code.upper()}-{uuid.uuid4().hex[:8].upper()}"


def create_item(db: Session, tenant_id: int, type_code: str, identifier: str = "", *, name: str = "", parent: Any = None,
                attributes_: dict[str, Any] | None = None, lifecycle: str = "available", oper_status: str = "unknown", serial: str = "",
                vendor: str = "", model: str = "", source: str = "manual", reference: str = "", actor_id: int | None = None,
                verified: bool = False, notes: str = "") -> InvItem:
    t = type_by_code(db, tenant_id, type_code) if not isinstance(type_code, InvItemType) else type_code
    if t is None or not t.is_active:
        raise ValueError(f"Unknown item type '{type_code}'")
    if lifecycle not in LIFECYCLE or lifecycle == "retired":
        raise ValueError(f"A new item's lifecycle must be one of {', '.join(x for x in LIFECYCLE if x != 'retired')}")
    if oper_status not in OPER_STATUSES:
        raise ValueError(f"Operational status must be one of {', '.join(OPER_STATUSES)}")
    parent_item = resolve_item(db, tenant_id, parent) if parent not in (None, "") else None
    if parent_item is not None and parent_item.lifecycle == "retired":
        raise ValueError(f"{parent_item.identifier} is retired")
    _check_containment(db, t, parent_item)
    identifier = str(identifier or "").strip() or _name_for(db, t, parent_item)
    _check_identifier(db, t, identifier)
    values = check_attributes(db, t, attributes_ or {})
    if serial and db.query(InvItem.id).filter(InvItem.tenant_id == tenant_id, InvItem.serial == serial, InvItem.lifecycle != "retired").first():
        raise ValueError(f"Serial number {serial} is already recorded")
    item = InvItem(tenant_id=tenant_id, type_id=t.id, kind=t.kind, identifier=identifier[:200], name=(name or identifier)[:200],
                   lifecycle=lifecycle, oper_status=oper_status, parent_id=parent_item.id if parent_item else None, serial=serial[:120],
                   vendor=vendor[:120], model=model[:120], attributes_json=_dumps(values), sources_json=_dumps({k: source for k in values}),
                   source=source, reference=reference[:120], notes=notes, last_verified_at=utcnow() if verified else None)
    db.add(item)
    db.flush()
    item.location_id = item.id if t.kind == "location" else _location_of(db, parent_item)
    _history(db, item, "created", {"type": t.code, "identifier": identifier, "parent": parent_item.identifier if parent_item else None,
                                   "lifecycle": lifecycle}, source, reference, actor_id)
    db.flush()
    return item


def update_item(db: Session, tenant_id: int, item_id: Any, changes: dict[str, Any], source: str = "manual", reference: str = "",
                actor_id: int | None = None) -> InvItem:
    """Changes its name, identifier, parent, serial, vendor, model, notes and attributes. An attribute a
    higher-priority source set is not overwritten by a lower one (discovery never undoes a manual fix)."""
    item = _owned(db, tenant_id, item_id)
    if item.lifecycle == "retired":
        raise ValueError(f"{item.identifier} is retired")
    t = db.get(InvItemType, item.type_id)
    before: dict[str, Any] = {}
    after: dict[str, Any] = {}
    for key in ("name", "serial", "vendor", "model", "notes"):
        if key in changes and str(changes[key] or "") != getattr(item, key):
            if key == "serial" and changes[key] and db.query(InvItem.id).filter(InvItem.tenant_id == tenant_id, InvItem.serial == changes[key],
                                                                                  InvItem.id != item.id, InvItem.lifecycle != "retired").first():
                raise ValueError(f"Serial number {changes[key]} is already recorded")
            before[key], after[key] = getattr(item, key), str(changes[key] or "")
            setattr(item, key, str(changes[key] or ""))
    if "identifier" in changes and str(changes["identifier"]).strip() and changes["identifier"] != item.identifier:
        _check_identifier(db, t, str(changes["identifier"]).strip(), item.id)
        before["identifier"], after["identifier"] = item.identifier, str(changes["identifier"]).strip()
        item.identifier = after["identifier"]
    if "parent" in changes:
        parent = resolve_item(db, tenant_id, changes["parent"]) if changes["parent"] not in (None, "") else None
        if parent is not None and (parent.id == item.id or item.id in _ancestors(db, parent)):
            raise ValueError("An item can't sit inside itself")
        _check_containment(db, t, parent)
        before["parent"] = item.parent_id
        item.parent_id = parent.id if parent else None
        after["parent"] = item.parent_id
        _relocate(db, item)
    if isinstance(changes.get("attributes"), dict):
        current = _j(item.attributes_json, {})
        sources = _j(item.sources_json, {})
        wanted = {k: v for k, v in changes["attributes"].items()
                  if SOURCE_PRIORITY.get(source, 0) >= SOURCE_PRIORITY.get(sources.get(k, "import"), 0)}
        values = check_attributes(db, t, {**current, **wanted}, item.id)
        for k, v in values.items():
            if current.get(k) != v and k in wanted:
                before[k], after[k] = current.get(k), v
                sources[k] = source
        item.attributes_json, item.sources_json = _dumps(values), _dumps(sources)
    if after:
        _history(db, item, "updated", {"before": before, "after": after}, source, reference, actor_id)
    if source == "discovery":
        item.last_verified_at = utcnow()
    db.flush()
    return item


def _ancestors(db: Session, item: InvItem) -> list[int]:
    out, current, seen = [], item, set()
    while current is not None and current.parent_id and current.parent_id not in seen:
        seen.add(current.parent_id)
        out.append(current.parent_id)
        current = db.get(InvItem, current.parent_id)
    return out


def descendants(db: Session, item_id: int) -> list[int]:
    out, frontier = [], [item_id]
    while frontier:
        children = [i for (i,) in db.query(InvItem.id).filter(InvItem.parent_id.in_(frontier))]
        children = [c for c in children if c not in out and c != item_id]
        out.extend(children)
        frontier = children
    return out


def _relocate(db: Session, item: InvItem) -> None:
    """Its location (and its contents') after a move."""
    for i in [item.id, *descendants(db, item.id)]:
        row = db.get(InvItem, i)
        row.location_id = row.id if row.kind == "location" else _location_of(db, db.get(InvItem, row.parent_id) if row.parent_id else None)


def set_lifecycle(db: Session, tenant_id: int, item_id: Any, to: str, source: str = "manual", note: str = "", reference: str = "",
                  actor_id: int | None = None) -> InvItem:
    item = _owned(db, tenant_id, item_id) if not isinstance(item_id, InvItem) else item_id
    if to not in LIFECYCLE:
        raise ValueError(f"Lifecycle must be one of {', '.join(LIFECYCLE)}")
    if to == item.lifecycle:
        return item
    if to not in TRANSITIONS[item.lifecycle]:
        allowed = ", ".join(TRANSITIONS[item.lifecycle]) or "nothing - it is retired"
        raise ValueError(f"{item.identifier} is {item.lifecycle.replace('_', ' ')} - it can move to {allowed}, not {to.replace('_', ' ')}")
    if to == "retired":
        live = [db.get(InvItem, i) for i in descendants(db, item.id)]
        live = [c for c in live if c.lifecycle != "retired"]
        if live:
            raise ValueError(f"{item.identifier} still holds {len(live)} item(s) (e.g. {live[0].identifier}) - move or retire them first")
        if item.order_id and item.lifecycle in ("reserved", "in_use"):
            raise ValueError(f"{item.identifier} is {item.lifecycle.replace('_', ' ')} for order {item.order_id} - release it first")
    if to == "maintenance":
        users = services_using(db, tenant_id, [item.id, *descendants(db, item.id)])
        if users and not note:
            raise ValueError(f"Putting {item.identifier} into maintenance affects {len(users)} service(s) - say why (a note)")
    before = item.lifecycle
    item.lifecycle = to
    if to == "available":
        item.order_id = item.party_id = item.service_item_id = None
        item.reserved_until, item.reference = None, ""
    _history(db, item, "lifecycle", {"from": before, "to": to, "note": note}, source, reference, actor_id)
    db.flush()
    return item


def set_oper_status(db: Session, tenant_id: int, item_id: Any, status: str, source: str = "manual", note: str = "", actor_id: int | None = None) -> InvItem:
    item = _owned(db, tenant_id, item_id) if not isinstance(item_id, InvItem) else item_id
    if status not in OPER_STATUSES:
        raise ValueError(f"Operational status must be one of {', '.join(OPER_STATUSES)}")
    if status != item.oper_status:
        _history(db, item, "oper_status", {"from": item.oper_status, "to": status, "note": note}, source, "", actor_id)
        item.oper_status = status
        db.flush()
    return item


def relate(db: Session, tenant_id: int, source_ref: Any, target_ref: Any, rel_type: str, note: str = "", source: str = "manual",
           actor_id: int | None = None) -> InvRelationship:
    if rel_type not in RELATIONSHIPS:
        raise ValueError(f"Relationship must be one of {', '.join(RELATIONSHIPS)}")
    a, b = resolve_item(db, tenant_id, source_ref), resolve_item(db, tenant_id, target_ref)
    if a.id == b.id:
        raise ValueError("An item can't relate to itself")
    existing = (db.query(InvRelationship).filter(InvRelationship.tenant_id == tenant_id, InvRelationship.source_id == a.id,
                                                 InvRelationship.target_id == b.id, InvRelationship.rel_type == rel_type).first())
    if existing is not None:
        return existing
    if rel_type == "depends_on" and a.id in _dependency_closure(db, tenant_id, b.id):
        raise ValueError(f"{b.identifier} already depends on {a.identifier} - that would be a loop")
    rel = InvRelationship(tenant_id=tenant_id, source_id=a.id, target_id=b.id, rel_type=rel_type, note=note[:300])
    db.add(rel)
    _history(db, a, "related", {"relationship": RELATIONSHIPS[rel_type][0], "target": b.identifier}, source, "", actor_id)
    db.flush()
    return rel


def unrelate(db: Session, tenant_id: int, rel_id: int, actor_id: int | None = None) -> None:
    rel = db.get(InvRelationship, rel_id)
    if rel is None or rel.tenant_id != tenant_id:
        raise ValueError("Relationship not found")
    a, b = db.get(InvItem, rel.source_id), db.get(InvItem, rel.target_id)
    _history(db, a, "unrelated", {"relationship": RELATIONSHIPS.get(rel.rel_type, (rel.rel_type,))[0], "target": b.identifier if b else rel.target_id},
             "manual", "", actor_id)
    db.delete(rel)
    db.flush()


def _dependency_closure(db: Session, tenant_id: int, item_id: int) -> set[int]:
    out, frontier = set(), {item_id}
    while frontier:
        nxt = {t for (t,) in db.query(InvRelationship.target_id).filter(InvRelationship.tenant_id == tenant_id, InvRelationship.rel_type == "depends_on",
                                                                         InvRelationship.source_id.in_(frontier))} - out
        out |= nxt
        frontier = nxt
    return out


def receive_stock(db: Session, tenant_id: int, type_code: str, quantity: int, location: Any, *, vendor: str = "", model: str = "",
                  serial_prefix: str = "", source: str = "manual", reference: str = "", actor_id: int | None = None) -> list[InvItem]:
    """Many items of one type into a location (a store, an exchange) at once - named by the type's naming rule."""
    if not 1 <= int(quantity) <= 500:
        raise ValueError("Receive between 1 and 500 items at a time")
    t = type_by_code(db, tenant_id, type_code) or find_type(db, tenant_id, type_code)
    if t is None:
        raise ValueError(f"Unknown item type '{type_code}'")
    if t.kind not in ("physical",):
        raise ValueError(f"{t.name} is {t.kind} - stock is physical equipment; give logical values a pool")
    where = resolve_item(db, tenant_id, location)
    made = []
    for i in range(int(quantity)):
        serial = f"{serial_prefix}{uuid.uuid4().hex[:8].upper()}" if serial_prefix else ""
        made.append(create_item(db, tenant_id, t, parent=where.id, vendor=vendor, model=model, serial=serial, source=source,
                                reference=reference, actor_id=actor_id))
    return made


# ================================================================== pools

def _pool_capacity(pool: InvPool) -> int:
    if pool.pool_type == "ip":
        net = ipaddress.ip_network(pool.range_start, strict=False)
        return 2 ** max(0, pool.block_size - net.prefixlen) if pool.block_size >= net.prefixlen else 0
    return max(0, int(pool.range_end) - int(pool.range_start) + 1)


def _pool_used(db: Session, pool: InvPool) -> set[str]:
    q = db.query(InvItem.identifier).filter(InvItem.tenant_id == pool.tenant_id, InvItem.lifecycle != "retired",
                                            or_(InvItem.pool_id == pool.id, (InvItem.type_id == pool.type_id) & (InvItem.location_id == pool.location_id)))
    return {i for (i,) in q}


def _format(pool: InvPool, n: int) -> str:
    try:
        return pool.format.format(n=n) if pool.format else str(n)
    except (KeyError, ValueError, IndexError):
        return str(n)


def _next_value(db: Session, pool: InvPool) -> str | None:
    used = _pool_used(db, pool)
    if pool.pool_type == "ip":
        net = ipaddress.ip_network(pool.range_start, strict=False)
        for sub in net.subnets(new_prefix=pool.block_size):
            if str(sub) not in used:
                return str(sub)
        return None
    for n in range(int(pool.range_start), int(pool.range_end) + 1):
        value = _format(pool, n)
        if value not in used:
            return value
    return None


def save_pool(db: Session, tenant_id: int, data: dict[str, Any], pool_id: int | None = None) -> InvPool:
    t = type_by_code(db, tenant_id, data.get("type_code") or "") or find_type(db, tenant_id, data.get("type_code") or "")
    if t is None or t.kind not in ("logical", "service"):
        raise ValueError("A pool hands out a logical type (vlan, ip_block, phone_number, circuit_id, service_id ...)")
    pool_type = data.get("pool_type") or ("ip" if t.code in ("ip_block", "ipv6_prefix") else "sequence" if "{" in str(data.get("format") or "") else "range")
    if pool_type not in POOL_TYPES:
        raise ValueError(f"pool_type must be one of {', '.join(POOL_TYPES)}")
    start, end = str(data.get("range_start") or "").strip(), str(data.get("range_end") or "").strip()
    block = int(data.get("block_size") or 0)
    if pool_type == "ip":
        try:
            net = ipaddress.ip_network(start, strict=False)
        except ValueError as exc:
            raise ValueError(f"'{start}' is not a network (e.g. 10.20.0.0/16)") from exc
        block = block or (net.max_prefixlen - 3 if net.version == 4 else 56)
        if not net.prefixlen <= block <= net.max_prefixlen:
            raise ValueError(f"Blocks of /{block} don't fit in {net}")
        start, end = str(net), ""
    else:
        if not (start.isdigit() and end.isdigit()) or int(start) > int(end):
            raise ValueError("Give a numeric range: start and end (e.g. 100 - 999)")
        if t.code == "vlan" and not (1 <= int(start) and int(end) <= 4094):
            raise ValueError("VLAN ids run from 1 to 4094")
    location = resolve_item(db, tenant_id, data["location"]) if data.get("location") not in (None, "") else None
    if location is not None and location.kind != "location":
        raise ValueError(f"{location.identifier} is not a location")
    pool = db.get(InvPool, pool_id) if pool_id else InvPool(tenant_id=tenant_id)
    if pool is None or pool.tenant_id != tenant_id:
        raise ValueError("Pool not found")
    if pool_id is None:
        db.add(pool)
    pool.name = str(data.get("name") or f"{t.name} {start}{'-' + end if end else ''}{' @ ' + location.identifier if location else ''}")[:150]
    pool.type_id, pool.pool_type, pool.location_id = t.id, pool_type, location.id if location else None
    pool.range_start, pool.range_end, pool.block_size = start, end, block
    pool.format = str(data.get("format") or "{n}")[:80]
    pool.threshold_pct = int(data.get("threshold_pct") or 80)
    pool.is_active = bool(data.get("is_active", True))
    db.flush()
    if _pool_capacity(pool) <= 0:
        raise ValueError("That pool has no room")
    return pool


def pool_out(db: Session, pool: InvPool) -> dict[str, Any]:
    t = db.get(InvItemType, pool.type_id)
    capacity = _pool_capacity(pool)
    used = db.query(InvItem.id).filter(InvItem.pool_id == pool.id, InvItem.lifecycle != "retired").count()
    location = db.get(InvItem, pool.location_id) if pool.location_id else None
    pct = round(100 * used / capacity, 1) if capacity else 100.0
    return {"id": pool.id, "name": pool.name, "type_code": t.code if t else "", "type_name": t.name if t else "", "pool_type": pool.pool_type,
            "location": location.identifier if location else None, "location_id": pool.location_id, "range_start": pool.range_start,
            "range_end": pool.range_end, "block_size": pool.block_size, "format": pool.format, "threshold_pct": pool.threshold_pct,
            "is_active": pool.is_active, "capacity": capacity, "used": used, "free": max(0, capacity - used), "used_pct": pct,
            "over_threshold": pct >= pool.threshold_pct}


# ================================================================== where an order is served from

def _location_chain(db: Session, location_id: int | None) -> list[int]:
    """The location, then the ones above it - nearest first."""
    if not location_id:
        return []
    item = db.get(InvItem, location_id)
    return [location_id, *[i for i in _ancestors(db, item) if db.get(InvItem, i).kind == "location"]] if item else []


def serving_location(db: Session, tenant_id: int, address: str) -> InvItem | None:
    """The most specific location whose served areas the address names."""
    text = f" {re.sub(r'[^a-z0-9]+', ' ', (address or '').lower())} "
    if not text.strip():
        return None
    best: tuple[int, int, InvItem] | None = None
    for loc in db.query(InvItem).filter(InvItem.tenant_id == tenant_id, InvItem.kind == "location", InvItem.lifecycle != "retired"):
        tokens = [re.sub(r"[^a-z0-9]+", " ", t.lower()).strip() for t in str(_j(loc.attributes_json, {}).get("coverage") or "").split(",")]
        hits = [t for t in tokens if t and f" {t} " in text]
        if hits:
            score = (len(_ancestors(db, loc)), max(len(h) for h in hits))
            if best is None or score > best[:2]:
                best = (*score, loc)
    return best[2] if best else None


def _service_area(db: Session, location_id: int) -> list[int]:
    """Where a location's own network is: the location and the locations inside it that don't serve areas of
    their own. A region whose exchanges each serve their localities doesn't serve an address by itself - an
    address matching only the region's coverage gets no exchange's ports."""
    out, frontier = [location_id], [location_id]
    while frontier:
        children = db.query(InvItem).filter(InvItem.parent_id.in_(frontier), InvItem.kind == "location", InvItem.lifecycle != "retired").all()
        frontier = [c.id for c in children if not str(_j(c.attributes_json, {}).get("coverage") or "").strip()]
        out += frontier
    return out


def _locations_under(db: Session, location_id: int) -> list[int]:
    return [location_id, *[i for i in descendants(db, location_id) if db.get(InvItem, i).kind == "location"]]


def _types_for(db: Session, tenant_id: int, resource_type: str) -> list[int]:
    ensure_types(db, tenant_id)
    return [i for (i,) in db.query(InvItemType.id).filter(InvItemType.tenant_id == tenant_id, InvItemType.resource_type == resource_type,
                                                          InvItemType.is_active.is_(True))]


def in_use(db: Session, tenant_id: int) -> bool:
    """Does the tenant keep its inventory here? (No locations, equipment or pools yet = the stand-in answers.)"""
    return bool(db.query(InvItem.id).filter(InvItem.tenant_id == tenant_id, InvItem.kind.in_(("location", "physical"))).first()
                or db.query(InvPool.id).filter(InvPool.tenant_id == tenant_id, InvPool.is_active.is_(True)).first())


def managed(db: Session, tenant_id: int, resource_type: str) -> bool:
    """Is this resource type kept here - equipment of it recorded, or a pool handing it out?"""
    types = _types_for(db, tenant_id, resource_type)
    if not types:
        return False
    return bool(db.query(InvItem.id).filter(InvItem.tenant_id == tenant_id, InvItem.type_id.in_(types), InvItem.kind == "physical").first()
                or db.query(InvPool.id).filter(InvPool.tenant_id == tenant_id, InvPool.type_id.in_(types), InvPool.is_active.is_(True)).first())


def resource_types(db: Session, tenant_id: int) -> list[str]:
    ensure_types(db, tenant_id)
    return sorted({r for (r,) in db.query(InvItemType.resource_type).filter(InvItemType.tenant_id == tenant_id, InvItemType.is_active.is_(True),
                                                                            InvItemType.resource_type != "")})


# ================================================================== allocation (Fulfillment SOM)

def _held(db: Session, tenant_id: int, ctx: dict[str, Any], types: list[int]) -> InvItem | None:
    """What the order - or the opportunity it came from - already holds of these types (a retried step, or
    Presales' feasibility hold, gets exactly that back)."""
    if not ctx.get("order_id") and not ctx.get("opportunity_id"):
        return None
    q = db.query(InvItem).filter(InvItem.tenant_id == tenant_id, InvItem.type_id.in_(types), InvItem.lifecycle.in_(("reserved", "in_use")))
    q = q.filter(InvItem.order_id == ctx["order_id"]) if ctx.get("order_id") else q.filter(InvItem.opportunity_id == ctx["opportunity_id"],
                                                                                         InvItem.order_id.is_(None))
    return q.order_by(InvItem.id).first()


def _hold(db: Session, item: InvItem, ctx: dict[str, Any], source: str) -> None:
    before = item.lifecycle
    item.lifecycle, item.order_id = "reserved", ctx.get("order_id")
    item.opportunity_id = ctx.get("opportunity_id") or item.opportunity_id
    item.party_id = ctx.get("party_id") or item.party_id
    item.reference = str(ctx.get("reference") or "")[:120]
    item.reserved_until = utcnow() + dt.timedelta(days=RESERVATION_DAYS)
    _history(db, item, "reserved", {"from": before, "order_id": item.order_id, "opportunity_id": item.opportunity_id}, source,
             item.reference, ctx.get("actor_id"))


def _pools_for(db: Session, tenant_id: int, rtype: str, location: InvItem | None) -> list[InvPool]:
    """The pools that may serve this location, in the order they're used: the location's own, then those above
    it, then shared ones. An address no location serves may use any pool (shared first)."""
    types = _types_for(db, tenant_id, rtype)
    chain = _location_chain(db, location.id if location else None)
    pools = db.query(InvPool).filter(InvPool.tenant_id == tenant_id, InvPool.type_id.in_(types or [0]), InvPool.is_active.is_(True)).all()
    ranked = sorted(pools, key=lambda p: (chain.index(p.location_id) if p.location_id in chain else len(chain) if p.location_id is None else 10_000, p.id))
    return [p for p in ranked if not (chain and p.location_id is not None and p.location_id not in chain)]  # never another area's pool


def _physical_candidates(db: Session, tenant_id: int, rtype: str, location: InvItem | None, limit: int = 1) -> tuple[list[InvItem], bool]:
    """Free items that may be given for this location, best first - and whether the type is stock.
    Stock (what is kept in a store - ONTs, CPEs, SIMs) comes from the nearest store up the location chain;
    network resources (ports, splitter legs, strands) only from the location serving the address. Never one
    on a card / device that is in maintenance, retired or down."""
    types = _types_for(db, tenant_id, rtype)
    base = (db.query(InvItem).filter(InvItem.tenant_id == tenant_id, InvItem.type_id.in_(types or [0]), InvItem.lifecycle == "available",
                                     InvItem.oper_status != "down"))
    stock = any("store" in (db.get(InvItemType, t).contained_in or "").split(",") for t in types)
    chain = _location_chain(db, location.id if location else None)
    found: list[InvItem] = []
    for loc_id in (chain if stock else chain[:1]) or [None]:
        area = _locations_under(db, loc_id) if stock or loc_id is None else _service_area(db, loc_id)
        q = base if loc_id is None else base.filter(InvItem.location_id.in_(area))
        for item in q.order_by(InvItem.identifier, InvItem.id).limit(max(limit, 1) * 20 + 200):
            if item not in found and not _out_of_service_above(db, item):
                found.append(item)
                if len(found) >= limit:
                    return found, stock
        if found:  # nearest first: a nearer level that has some is the answer
            return found, stock
    return found, stock


def _allocate_logical(db: Session, tenant_id: int, rtype: str, location: InvItem | None, ctx: dict[str, Any], source: str) -> InvItem:
    types = _types_for(db, tenant_id, rtype)
    held = _held(db, tenant_id, ctx, types)
    if held is not None:
        return held
    for pool in _pools_for(db, tenant_id, rtype, location):
        value = _next_value(db, pool)
        if value is None:
            continue
        t = db.get(InvItemType, pool.type_id)
        item = InvItem(tenant_id=tenant_id, type_id=t.id, kind=t.kind, identifier=value, name=value, lifecycle="reserved", oper_status="unknown",
                       pool_id=pool.id, location_id=pool.location_id, source=source, attributes_json="{}", sources_json="{}")
        db.add(item)
        db.flush()
        _history(db, item, "created", {"type": t.code, "identifier": value, "pool": pool.name}, source, str(ctx.get("reference") or ""), ctx.get("actor_id"))
        _hold(db, item, ctx, source)
        return item
    where = f" for {location.identifier}" if location else ""
    raise ValueError(f"Inventory: no free {rtype.replace('_', ' ')}{where} - every pool that serves it is used up")


def _reserve_physical(db: Session, tenant_id: int, rtype: str, location: InvItem | None, ctx: dict[str, Any], source: str) -> InvItem:
    types = _types_for(db, tenant_id, rtype)
    held = _held(db, tenant_id, ctx, types)
    if held is not None:
        return held
    found, stock = _physical_candidates(db, tenant_id, rtype, location)
    if found:
        _hold(db, found[0], ctx, source)
        return found[0]
    where = (f" at {location.identifier}" + (" or the stores above it" if stock else "")) if location else ""
    raise ValueError(f"Inventory: no free {rtype.replace('_', ' ')}{where}")


def _out_of_service_above(db: Session, item: InvItem) -> bool:
    for a in _ancestors(db, item):
        above = db.get(InvItem, a)
        if above.kind == "location":
            return False
        if above.lifecycle in ("maintenance", "retired") or above.oper_status == "down":
            return True
    return False


def _service_record(db: Session, tenant_id: int, ctx: dict[str, Any], location: InvItem | None, source: str) -> InvItem:
    t = type_by_code(db, tenant_id, "service")
    order_id = ctx.get("order_id")
    existing = (db.query(InvItem).filter(InvItem.tenant_id == tenant_id, InvItem.type_id == t.id, InvItem.order_id == order_id,
                                         InvItem.lifecycle != "retired").first() if order_id else None)
    if existing is not None:
        return existing
    pools = db.query(InvPool).filter(InvPool.tenant_id == tenant_id, InvPool.type_id.in_(_types_for(db, tenant_id, "service_id")),
                                     InvPool.is_active.is_(True)).order_by(InvPool.id).all()
    value, pool_id = None, None
    for pool in pools:
        value = _next_value(db, pool)
        if value:
            pool_id = pool.id
            break
    value = value or f"SVC-{int(order_id or 0):06d}-{uuid.uuid4().hex[:4].upper()}"
    products = ", ".join(ctx.get("products") or [])
    item = InvItem(tenant_id=tenant_id, type_id=t.id, kind="service", identifier=value, name=f"{products or 'Service'} - order {order_id}"[:200],
                   lifecycle="planned", oper_status="unknown", pool_id=pool_id, location_id=location.id if location else None, order_id=order_id,
                   party_id=ctx.get("party_id"), reference=str(ctx.get("reference") or "")[:120], source=source,
                   attributes_json=_dumps({"product": products, "address": ctx.get("address") or ""}), sources_json="{}")
    db.add(item)
    db.flush()
    _history(db, item, "created", {"type": "service", "identifier": value, "order_id": order_id}, source, item.reference, ctx.get("actor_id"))
    return item


def _resource_out(item: InvItem, rtype: str, status: str, db: Session) -> dict[str, Any]:
    location = db.get(InvItem, item.location_id) if item.location_id else None
    kind = "physical" if item.kind == "physical" else "logical"
    return {"type": rtype, "kind": kind, "identifier": item.identifier, "status": status, "item_id": item.id, "brt_id": item.brt_id,
            "location": location.identifier if location else None}


def technical_action(db: Session, tenant_id: int, action: str, resources: list[str], ctx: dict[str, Any], source: str = "fulfillment") -> dict[str, Any]:
    """A SOM technical action against this inventory. Returns {"resources": [...], "unmanaged": [types it doesn't keep],
    "location": the serving location}. Raises ValueError (-> Fulfillment fallout) when it can't be done."""
    if action not in ACTION_STATUS:
        raise ValueError(f"Unknown technical action '{action}'")
    location = serving_location(db, tenant_id, ctx.get("address") or "")
    order_id = ctx.get("order_id")
    out: list[dict[str, Any]] = []
    unmanaged: list[str] = []
    if action == "design":
        if "NOCOVERAGE" in str(ctx.get("address") or "").upper():
            raise ValueError(f"Inventory: no network route can serve '{ctx.get('address')}'")
        service = _service_record(db, tenant_id, ctx, location, source)
        for rtype in resources:
            if rtype == "service_id":
                out.append(_resource_out(service, rtype, "designed", db))
            elif managed(db, tenant_id, rtype):
                item = _allocate_logical(db, tenant_id, rtype, location, ctx, source)
                item.service_item_id = service.id
                out.append(_resource_out(item, rtype, "designed", db))
            else:
                unmanaged.append(rtype)
    elif action in ("assign", "reserve"):
        for rtype in resources:
            if not managed(db, tenant_id, rtype):
                unmanaged.append(rtype)
                continue
            types = _types_for(db, tenant_id, rtype)
            kinds = {k for (k,) in db.query(InvItemType.kind).filter(InvItemType.id.in_(types))}
            item = (_reserve_physical if "physical" in kinds and not db.query(InvPool.id).filter(InvPool.type_id.in_(types)).first()
                    else _allocate_logical)(db, tenant_id, rtype, location, ctx, source)
            out.append(_resource_out(item, rtype, ACTION_STATUS[action], db))
    elif action == "activate":
        if not order_id:
            raise ValueError("Activation needs the order")
        service = _service_record(db, tenant_id, ctx, location, source)
        held = (db.query(InvItem).filter(InvItem.tenant_id == tenant_id, InvItem.order_id == order_id, InvItem.id != service.id,
                                         InvItem.lifecycle.in_(("reserved", "in_use"))).order_by(InvItem.id).all())
        for item in held:
            if item.lifecycle != "in_use":
                before = item.lifecycle
                item.lifecycle, item.reserved_until = "in_use", None
                _history(db, item, "lifecycle", {"from": before, "to": "in_use", "service": service.identifier}, source, str(ctx.get("reference") or ""))
            item.oper_status, item.service_item_id = "up", service.id
            if not db.query(InvRelationship.id).filter(InvRelationship.source_id == service.id, InvRelationship.target_id == item.id,
                                                       InvRelationship.rel_type == "uses").first():
                db.add(InvRelationship(tenant_id=tenant_id, source_id=service.id, target_id=item.id, rel_type="uses"))
            t = db.get(InvItemType, item.type_id)
            out.append(_resource_out(item, t.resource_type or t.code, "active", db))
        if service.lifecycle != "in_use":
            _history(db, service, "lifecycle", {"from": service.lifecycle, "to": "in_use"}, source, str(ctx.get("reference") or ""))
            service.lifecycle = "in_use"
        service.oper_status = "up"
        service.last_verified_at = utcnow()
        listed = {r["type"] for r in out}
        unmanaged = [r for r in resources if r not in listed and not managed(db, tenant_id, r)]
    else:  # release
        out = release_order(db, tenant_id, order_id, source=source, reference=str(ctx.get("reference") or ""))
    db.flush()
    return {"resources": out, "unmanaged": unmanaged, "location": location.identifier if location else None}


def release_order(db: Session, tenant_id: int, order_id: Any, source: str = "manual", reference: str = "", actor_id: int | None = None,
                  note: str = "") -> list[dict[str, Any]]:
    """Everything an order holds goes back: equipment and ports become available, pool values return to their
    pool, the service record is retired."""
    if order_id in (None, ""):
        raise ValueError("Which order?")
    out = []
    held = (db.query(InvItem).filter(InvItem.tenant_id == tenant_id, InvItem.order_id == int(order_id),
                                     InvItem.lifecycle.in_(("planned", "reserved", "in_use"))).order_by(InvItem.id).all())
    for item in held:
        t = db.get(InvItemType, item.type_id)
        before = item.lifecycle
        if item.kind == "physical":
            item.lifecycle, item.oper_status = "available", "unknown"
        else:
            item.lifecycle = "retired"  # a pool value goes back to its pool; a service record ends
        item.order_id = item.party_id = item.service_item_id = item.opportunity_id = None
        item.reserved_until, item.reference = None, ""
        _history(db, item, "released", {"from": before, "order_id": int(order_id), "note": note}, source, reference, actor_id)
        out.append(_resource_out(item, t.resource_type or t.code, "released", db))
    db.query(InvRelationship).filter(InvRelationship.tenant_id == tenant_id, InvRelationship.rel_type == "uses",
                                     InvRelationship.source_id.in_([i.id for i in held if i.kind == "service"] or [0])).delete(synchronize_session=False)
    db.flush()
    return out


def register_answer(db: Session, tenant_id: int, resources: list[dict[str, Any]], ctx: dict[str, Any]) -> None:
    """An outside inventory system answered (asynchronously): what it handed out is recorded here too, held for the order."""
    if not in_use(db, tenant_id):
        return
    for r in resources:
        types = _types_for(db, tenant_id, str(r.get("type") or ""))
        if not types:
            continue
        t = db.get(InvItemType, types[0])
        item = (db.query(InvItem).filter(InvItem.tenant_id == tenant_id, InvItem.type_id.in_(types), InvItem.identifier == str(r["identifier"]),
                                         InvItem.lifecycle != "retired").first())
        if item is None:
            item = InvItem(tenant_id=tenant_id, type_id=t.id, kind=t.kind, identifier=str(r["identifier"])[:200], name=str(r["identifier"])[:200],
                           lifecycle="available", source="import", attributes_json="{}", sources_json="{}")
            db.add(item)
            db.flush()
            _history(db, item, "created", {"type": t.code, "identifier": item.identifier, "from": "inventory answer"}, "import", str(ctx.get("reference") or ""))
        if item.order_id in (None, ctx.get("order_id")) and item.lifecycle in ("available", "planned", "reserved"):
            if item.lifecycle != "reserved":
                _hold(db, item, ctx, "import")
    db.flush()


def items_for_order(db: Session, tenant_id: int, order_id: int) -> list[dict[str, Any]]:
    return [item_out(db, i) for i in db.query(InvItem).filter(InvItem.tenant_id == tenant_id, InvItem.order_id == order_id).order_by(InvItem.kind, InvItem.id)]


# ================================================================== Presales: feasibility against the network, held for the deal

_DEFAULT_RESOURCES = ("port", "splitter_port", "ont", "cpe", "router", "vlan", "ip_block")


def resources_for_product(db: Session, tenant_id: int, products: list[str], segment: str = "") -> tuple[list[str], str]:
    """The resource types a product needs, and where that came from: the SOM steps (assign / reserve) of the
    active fulfillment pattern that fulfils it - else the usual access resources Inventory keeps."""
    from app.models.fulfillment_orders import FulfillmentPattern
    from app.orchestration import fulfillment_engine
    from app.orchestration import fulfillment_functions as fx

    names = {p.lower() for p in products if p}
    data = {"products": list(products), "segment": segment, "type": "new"}
    for pattern in (db.query(FulfillmentPattern).filter(FulfillmentPattern.tenant_id == tenant_id, FulfillmentPattern.is_active.is_(True))
                    .order_by(FulfillmentPattern.priority.desc(), FulfillmentPattern.id)):
        wanted = {p.strip().lower() for p in (pattern.products or "").split(",") if p.strip()}
        if (wanted and not wanted & names) or not fulfillment_engine.fits(pattern, data):
            continue
        out: list[str] = []
        for node in fulfillment_engine.load_graph(pattern)["nodes"]:
            spec = fx.FUNCTIONS.get(node.get("function"), {})
            if spec.get("inventory") in ("assign", "reserve"):
                raw = (node.get("config") or {}).get("resources") or ", ".join(spec.get("default_resources") or [])
                out += [r.strip() for r in str(raw).split(",") if r.strip()]
        if out:
            return list(dict.fromkeys(out)), f"the '{pattern.name}' fulfillment pattern"
    return [r for r in _DEFAULT_RESOURCES if managed(db, tenant_id, r)], "the resources Inventory keeps"


def check_availability(db: Session, tenant_id: int, address: str, resources: list[str]) -> dict[str, Any]:
    """Read-only: the location serving the address and, per resource type, whether one can be given there -
    by the same rules a reservation uses (nearest first, network resources only at the serving location,
    nothing on equipment out of service)."""
    location = serving_location(db, tenant_id, address)
    checks = []
    for rtype in resources:
        if not managed(db, tenant_id, rtype):
            checks.append({"resource_type": rtype, "kept": False, "available": None, "free": None, "candidate": None,
                           "detail": f"{rtype.replace('_', ' ')} isn't kept in BRT 360 Inventory - the integrated system confirms it at fulfillment"})
            continue
        pools = _pools_for(db, tenant_id, rtype, location)
        if pools:
            free = sum(pool_out(db, p)["free"] for p in pools)
            candidate = next((v for v in (_next_value(db, p) for p in pools) if v), None)
            where = location.identifier if location else "any area"
            checks.append({"resource_type": rtype, "kept": True, "available": bool(candidate), "free": free, "candidate": candidate,
                           "detail": f"{free} free in the pools serving {where}" if candidate else f"every pool serving {where} is used up"})
            continue
        found, stock = _physical_candidates(db, tenant_id, rtype, location, limit=50)
        where = location.identifier if location else "anywhere"
        if found:
            at = db.get(InvItem, found[0].location_id).identifier if found[0].location_id else where
            checks.append({"resource_type": rtype, "kept": True, "available": True, "free": len(found), "candidate": found[0].identifier,
                           "detail": f"{len(found)}{'+' if len(found) >= 50 else ''} free at {at}"})
        else:
            checks.append({"resource_type": rtype, "kept": True, "available": False, "free": 0, "candidate": None,
                           "detail": f"none free at {where}" + (" or the stores above it" if stock and location else "")})
    return {"location": location.identifier if location else None, "location_name": location.name if location else None,
            "address": address, "checks": checks,
            "feasible": location is not None and all(c["available"] is not False for c in checks)}


def _opportunity_ctx(db: Session, tenant_id: int, opportunity_id: int, reference: str = "", actor_id: int | None = None) -> dict[str, Any]:
    from app.models.crm import Opportunity

    opp = db.get(Opportunity, int(opportunity_id))
    if opp is None or opp.tenant_id != tenant_id:
        raise ValueError(f"Opportunity {opportunity_id} not found")
    party_id = opp.lead.party_id if opp.lead is not None else None
    return {"opportunity_id": opp.id, "order_id": None, "party_id": party_id, "products": [opp.offering_name] if opp.offering_name else [],
            "address": (opp.config.site_address if opp.config is not None else "") or "", "reference": reference or f"opportunity {opp.id}",
            "actor_id": actor_id, "opportunity": opp}


def opportunity_view(db: Session, tenant_id: int, opportunity_id: int, address: str = "") -> dict[str, Any]:
    """What Presales sees: the site, the location serving it, each resource the product needs and whether it's
    available there, and what is already held for the deal."""
    ctx = _opportunity_ctx(db, tenant_id, opportunity_id)
    opp = ctx["opportunity"]
    if not in_use(db, tenant_id):
        return {"in_use": False, "opportunity_id": opp.id, "stage": opp.stage.value, "address": ctx["address"], "checks": [], "holds": []}
    segment = ""
    if opp.lead is not None and opp.lead.party_id:
        from app.crm_core import process_service
        from app.models.crm import Party

        party = db.get(Party, opp.lead.party_id)
        segment = process_service.party_segment(party) or "" if party else ""
    resources, basis = resources_for_product(db, tenant_id, ctx["products"], segment)
    check = check_availability(db, tenant_id, address or ctx["address"], resources)
    holds = [item_out(db, i) for i in db.query(InvItem).filter(InvItem.tenant_id == tenant_id, InvItem.opportunity_id == opp.id,
                                                               InvItem.lifecycle.in_(("reserved", "in_use"))).order_by(InvItem.id)]
    return {"in_use": True, "opportunity_id": opp.id, "opportunity": opp.name, "stage": opp.stage.value, "product": opp.offering_name,
            "resources": resources, "resources_from": basis, "holds": holds, **check}


def hold_for_opportunity(db: Session, tenant_id: int, opportunity_id: int, resources: list[str] | None = None, address: str = "",
                         source: str = "presales", actor_id: int | None = None) -> dict[str, Any]:
    """Presales reserves what the deal needs at the location serving its site - all of it or nothing. Already-held
    types are kept (no double hold). The hold moves to the order created from the deal."""
    ctx = _opportunity_ctx(db, tenant_id, opportunity_id, actor_id=actor_id)
    if not in_use(db, tenant_id):
        raise ValueError("No network is recorded in BRT 360 Inventory yet - nothing to reserve")
    address = address or ctx["address"]
    if not address.strip():
        raise ValueError("The opportunity has no site address - add it before checking the network")
    location = serving_location(db, tenant_id, address)
    if location is None:
        raise ValueError(f"No location in Inventory serves '{address}' - not feasible on the recorded network")
    if resources is None:
        resources, _ = resources_for_product(db, tenant_id, ctx["products"])
    held = []
    for rtype in resources:
        if not managed(db, tenant_id, rtype):
            continue
        types = _types_for(db, tenant_id, rtype)
        kinds = {k for (k,) in db.query(InvItemType.kind).filter(InvItemType.id.in_(types))}
        pick = (_reserve_physical if "physical" in kinds and not db.query(InvPool.id).filter(InvPool.type_id.in_(types)).first() else _allocate_logical)
        item = pick(db, tenant_id, rtype, location, ctx, source)  # raises (and the caller's transaction undoes the rest) if none is free
        item.reserved_until = utcnow() + dt.timedelta(days=RESERVATION_DAYS)
        held.append(_resource_out(item, rtype, "reserved", db))
    db.flush()
    return {"opportunity_id": ctx["opportunity_id"], "location": location.identifier, "held": held}


def release_opportunity(db: Session, tenant_id: int, opportunity_id: int, source: str = "presales", note: str = "",
                        actor_id: int | None = None) -> list[dict[str, Any]]:
    """What a deal holds (not yet moved to an order) goes back."""
    out = []
    for item in db.query(InvItem).filter(InvItem.tenant_id == tenant_id, InvItem.opportunity_id == int(opportunity_id),
                                         InvItem.order_id.is_(None), InvItem.lifecycle == "reserved").order_by(InvItem.id).all():
        t = db.get(InvItemType, item.type_id)
        item.lifecycle = "available" if item.kind == "physical" else "retired"
        item.opportunity_id = item.party_id = None
        item.reserved_until, item.reference = None, ""
        _history(db, item, "released", {"from": "reserved", "opportunity_id": int(opportunity_id), "note": note}, source, "", actor_id)
        out.append(_resource_out(item, t.resource_type or t.code, "released", db))
    db.flush()
    return out


def transfer_to_order(db: Session, tenant_id: int, opportunity_id: int | None, order_id: int) -> int:
    """The order created from a deal takes over what Presales held for it - SOM then uses exactly those items."""
    if not opportunity_id:
        return 0
    moved = 0
    for item in db.query(InvItem).filter(InvItem.tenant_id == tenant_id, InvItem.opportunity_id == opportunity_id,
                                         InvItem.order_id.is_(None), InvItem.lifecycle == "reserved").all():
        item.order_id = order_id
        item.reserved_until = utcnow() + dt.timedelta(days=RESERVATION_DAYS)
        item.reference = f"order {order_id}"
        _history(db, item, "transferred", {"opportunity_id": opportunity_id, "order_id": order_id}, "fulfillment", f"order {order_id}")
        moved += 1
    db.flush()
    return moved


def link_assets(db: Session, tenant_id: int, order_id: int, asset_ids: list[Any]) -> None:
    """The CRM's installed assets for an order, noted on its service record."""
    service = (db.query(InvItem).filter(InvItem.tenant_id == tenant_id, InvItem.order_id == order_id, InvItem.kind == "service",
                                        InvItem.lifecycle != "retired").first())
    if service is None:
        return
    attrs = _j(service.attributes_json, {})
    attrs["crm_assets"] = ", ".join(str(a) for a in asset_ids if a)
    service.attributes_json = _dumps(attrs)
    _history(db, service, "updated", {"after": {"crm_assets": attrs["crm_assets"]}}, "fulfillment")
    db.flush()


def expire_reservations(db: Session, now: dt.datetime | None = None) -> int:
    """Reservations past their time that nothing open still needs go back: an order no longer being fulfilled,
    or a deal that was lost / found not feasible. Returns how many."""
    from app.models.crm import Opportunity, OpportunityStage
    from app.models.fulfillment_orders import FulfillmentOrder

    now = now or utcnow()
    count = 0
    for item in db.query(InvItem).filter(InvItem.lifecycle == "reserved", InvItem.reserved_until.isnot(None), InvItem.reserved_until < now).limit(500):
        open_order = item.order_id and db.query(FulfillmentOrder.id).filter(
            FulfillmentOrder.tenant_id == item.tenant_id, FulfillmentOrder.crm_order_id == item.order_id,
            FulfillmentOrder.status.in_(("in_progress", "waiting", "fallout"))).first()
        opportunity = db.get(Opportunity, item.opportunity_id) if item.opportunity_id and not item.order_id else None
        open_deal = opportunity is not None and opportunity.stage not in (OpportunityStage.lost, OpportunityStage.feasibility_failed)
        if open_order or open_deal:
            item.reserved_until = now + dt.timedelta(days=RESERVATION_DAYS)  # still being fulfilled / still being sold - keep holding it
            continue
        order_id, opportunity_id = item.order_id, item.opportunity_id
        item.lifecycle = "available" if item.kind == "physical" else "retired"
        item.order_id = item.party_id = item.service_item_id = item.opportunity_id = None
        item.reserved_until, item.reference = None, ""
        _history(db, item, "released", {"from": "reserved", "order_id": order_id, "opportunity_id": opportunity_id,
                                        "note": "reservation expired"}, "system")
        count += 1
    db.flush()
    return count


def run_due(session_factory) -> None:
    db = session_factory()
    try:
        expire_reservations(db)
        db.commit()
    finally:
        db.close()


# ================================================================== insight

def services_using(db: Session, tenant_id: int, item_ids: list[int]) -> list[int]:
    ids = set(item_ids)
    through_links = {s for (s,) in db.query(InvRelationship.source_id).filter(InvRelationship.tenant_id == tenant_id, InvRelationship.rel_type == "uses",
                                                                              InvRelationship.target_id.in_(ids or {0}))}
    direct = {s for (s,) in db.query(InvItem.service_item_id).filter(InvItem.id.in_(ids or {0}), InvItem.service_item_id.isnot(None))}
    own = {i for (i,) in db.query(InvItem.id).filter(InvItem.id.in_(ids or {0}), InvItem.kind == "service")}
    return sorted(through_links | direct | own)


def impact(db: Session, tenant_id: int, item_ref: Any) -> dict[str, Any]:
    """What goes down with an item: what it holds, what depends on or connects through any of that, and the
    services using any of it - with their customers and orders."""
    from app.models.crm import Party

    item = resolve_item(db, tenant_id, item_ref) if not isinstance(item_ref, InvItem) else item_ref
    affected = {item.id, *descendants(db, item.id)}
    for _ in range(10):  # bounded: dependency chains are short; loops can't recurse forever
        grown = {s for (s,) in db.query(InvRelationship.source_id).filter(InvRelationship.tenant_id == tenant_id,
                                                                          InvRelationship.rel_type.in_(("depends_on", "connects_to", "member_of")),
                                                                          InvRelationship.target_id.in_(affected))} - affected
        for g in list(grown):
            grown |= set(descendants(db, g))
        if not grown:
            break
        affected |= grown
    services = [db.get(InvItem, s) for s in services_using(db, tenant_id, sorted(affected))]
    services = [s for s in services if s is not None and s.lifecycle != "retired"]
    parties = sorted({s.party_id for s in services if s.party_id})
    customers = [{"id": p, "name": (db.get(Party, p).name if db.get(Party, p) else f"#{p}")} for p in parties]
    protected = {t for (t,) in db.query(InvRelationship.target_id).filter(InvRelationship.tenant_id == tenant_id, InvRelationship.rel_type == "protects",
                                                                          InvRelationship.target_id.in_(affected))}
    return {"item": item_out(db, item), "affected_items": len(affected),
            "affected": [item_out(db, db.get(InvItem, i), brief=True) for i in sorted(affected)[:200]],
            "services": [item_out(db, s, brief=True) for s in services], "customers": customers,
            "orders": sorted({s.order_id for s in services if s.order_id}),
            "protected": sorted(db.get(InvItem, p).identifier for p in protected),
            "summary": (f"{item.identifier} going down affects {len(affected) - 1} item(s) it holds or that rely on it, "
                        f"{len(services)} service(s) and {len(customers)} customer(s)"
                        + (f"; {len(protected)} of them have protection" if protected else "") + ".")}


def capacity(db: Session, tenant_id: int, location_ref: Any = None) -> dict[str, Any]:
    location = resolve_item(db, tenant_id, location_ref) if location_ref not in (None, "") else None
    scope = _locations_under(db, location.id) if location else None
    rows = []
    for rtype in resource_types(db, tenant_id):
        types = _types_for(db, tenant_id, rtype)
        q = db.query(InvItem.lifecycle).filter(InvItem.tenant_id == tenant_id, InvItem.type_id.in_(types), InvItem.kind == "physical")
        if scope:
            q = q.filter(InvItem.location_id.in_(scope))
        counts: dict[str, int] = {}
        for (life,) in q:
            counts[life] = counts.get(life, 0) + 1
        total = sum(v for k, v in counts.items() if k != "retired")
        if total:
            rows.append({"resource_type": rtype, "total": total, "available": counts.get("available", 0), "reserved": counts.get("reserved", 0),
                         "in_use": counts.get("in_use", 0), "maintenance": counts.get("maintenance", 0),
                         "used_pct": round(100 * (counts.get("reserved", 0) + counts.get("in_use", 0)) / total, 1)})
    pools = [pool_out(db, p) for p in db.query(InvPool).filter(InvPool.tenant_id == tenant_id, InvPool.is_active.is_(True)).order_by(InvPool.name)
             if scope is None or p.location_id is None or p.location_id in scope]
    return {"location": location.identifier if location else None, "equipment": rows, "pools": pools}


def health(db: Session, tenant_id: int) -> dict[str, Any]:
    """How good the record is: completeness, staleness, orphans, duplicates, expired reservations, full pools,
    services on equipment that is down."""
    ensure_types(db, tenant_id)
    items = db.query(InvItem).filter(InvItem.tenant_id == tenant_id, InvItem.lifecycle != "retired").all()
    by_kind: dict[str, int] = {}
    by_life: dict[str, int] = {}
    for i in items:
        by_kind[i.kind] = by_kind.get(i.kind, 0) + 1
        by_life[i.lifecycle] = by_life.get(i.lifecycle, 0) + 1
    types = {t.id: t for t in db.query(InvItemType).filter(InvItemType.tenant_id == tenant_id)}
    required = {tid: [a["key"] for a in attributes(db, t) if a.get("required")] for tid, t in types.items()}
    incomplete = [i for i in items if any(_j(i.attributes_json, {}).get(k) in (None, "") for k in required.get(i.type_id, []))]
    stale_before = utcnow() - dt.timedelta(days=STALE_DAYS)
    stale = [i for i in items if i.kind in ("physical", "location") and (i.last_verified_at is None or i.last_verified_at < stale_before)]
    orphans = [i for i in items if i.kind == "physical" and i.parent_id is None]
    serials: dict[str, list[InvItem]] = {}
    for i in items:
        if i.serial:
            serials.setdefault(i.serial, []).append(i)
    duplicates = [v for v in serials.values() if len(v) > 1]
    expired = [i for i in items if i.lifecycle == "reserved" and i.reserved_until and i.reserved_until < utcnow()]
    down = [i for i in items if i.oper_status == "down" and i.kind != "service"]
    at_risk = services_using(db, tenant_id, [i.id for i in down] + [d for i in down for d in descendants(db, i.id)]) if down else []
    at_risk = [s for s in at_risk if db.get(InvItem, s).lifecycle == "in_use"]
    pools = [pool_out(db, p) for p in db.query(InvPool).filter(InvPool.tenant_id == tenant_id, InvPool.is_active.is_(True))]
    total = len(items) or 1
    checks = [
        {"key": "completeness", "label": "Required attributes filled", "bad": len(incomplete), "examples": [i.identifier for i in incomplete[:5]]},
        {"key": "staleness", "label": f"Equipment / locations verified in the last {STALE_DAYS} days", "bad": len(stale), "examples": [i.identifier for i in stale[:5]]},
        {"key": "orphans", "label": "Equipment placed somewhere", "bad": len(orphans), "examples": [i.identifier for i in orphans[:5]]},
        {"key": "duplicates", "label": "Serial numbers unique", "bad": sum(len(v) for v in duplicates), "examples": [v[0].serial for v in duplicates[:5]]},
        {"key": "reservations", "label": "Reservations within their time", "bad": len(expired), "examples": [i.identifier for i in expired[:5]]},
        {"key": "pools", "label": "Pools below their threshold", "bad": sum(1 for p in pools if p["over_threshold"]),
         "examples": [p["name"] for p in pools if p["over_threshold"]][:5]},
        {"key": "at_risk", "label": "Services on equipment that is up", "bad": len(at_risk), "examples": [db.get(InvItem, s).identifier for s in at_risk[:5]]},
    ]
    scored = [c for c in checks if c["key"] in ("completeness", "staleness", "orphans", "duplicates")]
    score = round(100 * sum(max(0, 1 - c["bad"] / total) for c in scored) / len(scored)) if items else 100
    return {"items": len(items), "by_kind": by_kind, "by_lifecycle": by_life, "checks": checks, "score": score,
            "types": len(types), "pools": len(pools), "in_use": in_use(db, tenant_id)}


# ================================================================== one customer's network (Customer 360)

# availability as the element's state says it is right now (no monitoring history is kept here)
_STATE_AVAILABILITY = {"up": 100.0, "degraded": 99.0, "down": 0.0}
_RISKY_LIFECYCLE = ("maintenance", "retired")


def _element_trouble(item: InvItem) -> str | None:
    if item.oper_status in ("down", "degraded"):
        return item.oper_status
    if item.lifecycle in _RISKY_LIFECYCLE:
        return f"in {item.lifecycle}" if item.lifecycle == "maintenance" else item.lifecycle
    return None


def _load_pct(db: Session, item: InvItem) -> float | None:
    """How full a device / card / splitter is: its contents that are reserved or in use."""
    rows = [life for (life,) in db.query(InvItem.lifecycle).filter(InvItem.parent_id == item.id, InvItem.lifecycle != "retired")]
    if not rows:
        return None
    return round(100 * sum(1 for r in rows if r in ("reserved", "in_use")) / len(rows), 1)


def customer_network(db: Session, tenant_id: int, party_id: int, order_ids: list[int] | None = None) -> dict[str, Any]:
    """What a customer's services run on: each service record with its resources and the path of each
    (exchange > OLT > card > port, cabinet > splitter > leg), the network elements serving the customer with
    their state and load, the customer's devices, and what puts any of it at risk."""
    order_ids = [int(o) for o in order_ids or []]
    if not in_use(db, tenant_id):
        return {"in_use": False, "services": [], "resources": [], "elements": [], "devices": [], "at_risk": []}
    q = db.query(InvItem).filter(InvItem.tenant_id == tenant_id, InvItem.lifecycle != "retired")
    mine = q.filter(or_(InvItem.party_id == party_id, InvItem.order_id.in_(order_ids or [0]))).order_by(InvItem.id).all()
    services = [i for i in mine if i.kind == "service"]
    held = [i for i in mine if i.kind != "service"]
    for s in services:  # what a service uses, even once its links are the only trace
        for (target,) in db.query(InvRelationship.target_id).filter(InvRelationship.source_id == s.id, InvRelationship.rel_type == "uses"):
            if target not in {h.id for h in held}:
                extra = db.get(InvItem, target)
                if extra is not None and extra.lifecycle != "retired":
                    held.append(extra)
    elements: dict[int, InvItem] = {}
    path_of: dict[int, list[InvItem]] = {}
    for item in held:
        chain = [db.get(InvItem, a) for a in reversed(_ancestors(db, item))]
        path_of[item.id] = [c for c in chain if c is not None]
        for c in path_of[item.id]:
            if c.kind != "location":
                elements[c.id] = c
        if item.kind == "physical" and db.get(InvItemType, item.type_id).resource_type in ("port", "splitter_port", "fibre_strand"):
            elements[item.id] = item

    def resource_row(item: InvItem) -> dict[str, Any]:
        t = db.get(InvItemType, item.type_id)
        location = db.get(InvItem, item.location_id) if item.location_id and item.location_id != item.id else None
        trouble = [f"{p.identifier} is {_element_trouble(p)}" for p in [*path_of.get(item.id, []), item] if _element_trouble(p)]
        return {"id": item.id, "item_id": item.id, "brt_id": item.brt_id, "resource_type": t.resource_type or t.code, "type_name": t.name,
                "kind": item.kind, "identifier": item.identifier, "status": item.lifecycle, "oper_status": item.oper_status,
                "site": location.identifier if location else None, "path": " › ".join(p.identifier for p in path_of.get(item.id, []) if p.kind != "location"),
                "location_path": " › ".join(p.identifier for p in path_of.get(item.id, []) if p.kind == "location"),
                "order_id": item.order_id, "service_item_id": item.service_item_id, "model": item.model, "vendor": item.vendor,
                "serial": item.serial, "attributes": _j(item.attributes_json, {}), "trouble": trouble}

    resources = [resource_row(i) for i in held]
    by_service: dict[int, list[dict[str, Any]]] = {}
    for r in resources:
        by_service.setdefault(r["service_item_id"] or 0, []).append(r)
    service_rows, at_risk = [], []
    for s in services:
        rows = by_service.get(s.id, []) or [r for r in resources if r["order_id"] and r["order_id"] == s.order_id]
        attrs = _j(s.attributes_json, {})
        trouble = sorted({t for r in rows for t in r["trouble"]} | ({f"{s.identifier} is {_element_trouble(s)}"} if _element_trouble(s) else set()))
        location = db.get(InvItem, s.location_id) if s.location_id else None
        oper = "down" if any("is down" in t for t in trouble) else "degraded" if trouble else s.oper_status
        row = {"id": s.id, "item_id": s.id, "brt_id": s.brt_id, "service": s.identifier, "product": attrs.get("product") or s.name,
               "status": s.lifecycle, "oper_status": oper, "location": location.identifier if location else None,
               "path": next((r["path"] for r in rows if r["kind"] == "physical" and r["path"]), ""),
               "resources": len(rows), "order_id": s.order_id, "at_risk": "yes" if trouble else "no", "trouble": trouble,
               "crm_assets": [int(a) for a in str(attrs.get("crm_assets") or "").split(",") if a.strip().isdigit()],
               "address": attrs.get("address") or "", "uses": rows}
        service_rows.append(row)
        if trouble:
            at_risk.append({"service": s.identifier, "item_id": s.id, "order_id": s.order_id, "reasons": trouble})
    element_rows = []
    for e in sorted(elements.values(), key=lambda x: x.identifier):
        t = db.get(InvItemType, e.type_id)
        location = db.get(InvItem, e.location_id) if e.location_id else None
        attrs = _j(e.attributes_json, {})
        element_rows.append({"id": e.id, "item_id": e.id, "element": e.identifier, "type_name": t.name, "site": location.identifier if location else None,
                             "status": e.oper_status if e.lifecycle not in _RISKY_LIFECYCLE else e.lifecycle,
                             "lifecycle": e.lifecycle, "utilization_pct": _load_pct(db, e), "latency_ms": None,
                             # planned maintenance is not an outage - it shows as a risk, not as lost availability
                             "availability_pct": None if e.lifecycle in _RISKY_LIFECYCLE else _STATE_AVAILABILITY.get(e.oper_status),
                             "firmware": attrs.get("software") or attrs.get("firmware") or ""})
    devices = [{"device_id": r["identifier"], "item_id": r["item_id"], "type_name": r["type_name"], "model": " ".join(x for x in (r["vendor"], r["model"]) if x),
                "site": r["site"], "status": r["oper_status"] if r["status"] == "in_use" else r["status"], "serial": r["serial"],
                "firmware": str(r["attributes"].get("software") or r["attributes"].get("firmware") or ""), "order_id": r["order_id"]}
               for r in resources if r["kind"] == "physical" and r["resource_type"] not in ("port", "splitter_port", "fibre_strand")]
    return {"in_use": True, "services": service_rows, "resources": resources, "elements": element_rows, "devices": devices, "at_risk": at_risk}


# ================================================================== reading

def item_out(db: Session, item: InvItem, brief: bool = False) -> dict[str, Any]:
    t = db.get(InvItemType, item.type_id)
    location = db.get(InvItem, item.location_id) if item.location_id and item.location_id != item.id else None
    parent = db.get(InvItem, item.parent_id) if item.parent_id else None
    out = {"id": item.id, "brt_id": item.brt_id, "type_code": t.code if t else "", "type_name": t.name if t else "", "kind": item.kind,
           "identifier": item.identifier, "name": item.name, "lifecycle": item.lifecycle, "oper_status": item.oper_status,
           "location": location.identifier if location else None, "parent": parent.identifier if parent else None, "order_id": item.order_id}
    if brief:
        return out
    return {**out, "parent_id": item.parent_id, "location_id": item.location_id, "pool_id": item.pool_id, "serial": item.serial,
            "vendor": item.vendor, "model": item.model, "attributes": _j(item.attributes_json, {}), "sources": _j(item.sources_json, {}),
            "party_id": item.party_id, "service_item_id": item.service_item_id, "reference": item.reference, "reserved_until": item.reserved_until,
            "source": item.source, "last_verified_at": item.last_verified_at, "notes": item.notes, "created_at": item.created_at,
            "updated_at": item.updated_at, "next_lifecycle": list(TRANSITIONS.get(item.lifecycle, ()))}


def detail(db: Session, tenant_id: int, item_id: Any) -> dict[str, Any]:
    item = _owned(db, tenant_id, item_id)
    children = db.query(InvItem).filter(InvItem.parent_id == item.id).order_by(InvItem.identifier).limit(300).all()
    rels = []
    for r in db.query(InvRelationship).filter(InvRelationship.tenant_id == tenant_id, or_(InvRelationship.source_id == item.id,
                                                                                         InvRelationship.target_id == item.id)):
        forward = r.source_id == item.id
        other = db.get(InvItem, r.target_id if forward else r.source_id)
        words = RELATIONSHIPS.get(r.rel_type, (r.rel_type, r.rel_type))
        rels.append({"id": r.id, "rel_type": r.rel_type, "wording": words[0 if forward else 1], "direction": "out" if forward else "in",
                     "other": item_out(db, other, brief=True) if other else None, "note": r.note})
    history = [{"action": h.action, "changes": _j(h.changes_json, {}), "source": h.source, "reference": h.reference, "actor_id": h.actor_id, "at": h.at}
               for h in db.query(InvHistory).filter(InvHistory.tenant_id == tenant_id, InvHistory.item_id == item.id).order_by(InvHistory.id.desc()).limit(100)]
    path = [item_out(db, db.get(InvItem, a), brief=True) for a in reversed(_ancestors(db, item))]
    t = db.get(InvItemType, item.type_id)
    return {"item": item_out(db, item), "type": type_out(db, t), "path": path, "children": [item_out(db, c, brief=True) for c in children],
            "children_by_lifecycle": _count_by(children, "lifecycle"), "relationships": rels, "history": history,
            "services": [item_out(db, db.get(InvItem, s), brief=True) for s in services_using(db, tenant_id, [item.id, *descendants(db, item.id)])[:50]]}


def _count_by(rows: list[InvItem], attr: str) -> dict[str, int]:
    out: dict[str, int] = {}
    for r in rows:
        out[getattr(r, attr)] = out.get(getattr(r, attr), 0) + 1
    return out


def search(db: Session, tenant_id: int, f: dict[str, Any], limit: int = 200) -> list[dict[str, Any]]:
    """Items matching a structured filter: type, kind, lifecycle, oper_status, location (and inside it), parent,
    order, customer, text (identifier / name / serial), attribute values."""
    ensure_types(db, tenant_id)
    q = db.query(InvItem).filter(InvItem.tenant_id == tenant_id)
    if f.get("type"):
        t = type_by_code(db, tenant_id, f["type"]) or find_type(db, tenant_id, f["type"])
        if t is None:
            return []
        sub = {t.code} | {c for (c,) in db.query(InvItemType.code).filter(InvItemType.tenant_id == tenant_id, InvItemType.parent_code == t.code)}
        q = q.filter(InvItem.type_id.in_([i for (i,) in db.query(InvItemType.id).filter(InvItemType.tenant_id == tenant_id, InvItemType.code.in_(sub))]))
    if f.get("resource_type"):
        q = q.filter(InvItem.type_id.in_(_types_for(db, tenant_id, f["resource_type"]) or [0]))
    if f.get("kind") in ITEM_KINDS:
        q = q.filter(InvItem.kind == f["kind"])
    if f.get("lifecycle") in LIFECYCLE:
        q = q.filter(InvItem.lifecycle == f["lifecycle"])
    elif not f.get("include_retired"):
        q = q.filter(InvItem.lifecycle != "retired")
    if f.get("oper_status") in OPER_STATUSES:
        q = q.filter(InvItem.oper_status == f["oper_status"])
    if f.get("location"):
        loc = resolve_item(db, tenant_id, f["location"])
        q = q.filter(InvItem.location_id.in_(_locations_under(db, loc.id)))
    if f.get("parent"):
        q = q.filter(InvItem.parent_id == resolve_item(db, tenant_id, f["parent"]).id)
    if f.get("order_id"):
        q = q.filter(InvItem.order_id == int(f["order_id"]))
    if f.get("party_id"):
        q = q.filter(InvItem.party_id == int(f["party_id"]))
    if f.get("text"):
        like = f"%{str(f['text']).strip()}%"
        q = q.filter(or_(InvItem.identifier.ilike(like), InvItem.name.ilike(like), InvItem.serial.ilike(like), InvItem.brt_id.ilike(like)))
    for key, value in (f.get("attributes") or {}).items():
        q = q.filter(InvItem.attributes_json.contains(f'"{key}": {json.dumps(value)}'))
    return [item_out(db, i, brief=True) for i in q.order_by(InvItem.kind, InvItem.identifier).limit(limit)]


def locations_tree(db: Session, tenant_id: int) -> list[dict[str, Any]]:
    locs = db.query(InvItem).filter(InvItem.tenant_id == tenant_id, InvItem.kind == "location", InvItem.lifecycle != "retired").order_by(InvItem.identifier).all()
    counts: dict[int, int] = {}
    for (loc_id,) in db.query(InvItem.location_id).filter(InvItem.tenant_id == tenant_id, InvItem.kind == "physical", InvItem.lifecycle != "retired"):
        if loc_id:
            counts[loc_id] = counts.get(loc_id, 0) + 1
    return [{**item_out(db, l, brief=True), "parent_id": l.parent_id, "equipment": counts.get(l.id, 0)} for l in locs]


# ================================================================== reconciliation

def reconcile(db: Session, tenant_id: int, payload: dict[str, Any], dry_run: bool = True, actor_id: int | None = None) -> dict[str, Any]:
    """{"source": "nms-scan", "full_scan_of": <location>?, "items": [{"type", "identifier", "name"?, "serial"?, "parent"?,
    "vendor"?, "model"?, "attributes"?, "oper_status"?}]} - each reported item is found (type + identifier, else serial),
    created, or updated where the source may; with full_scan_of, what is recorded there but not reported is flagged.
    dry_run (the default) reports what would change and changes nothing."""
    ensure_types(db, tenant_id)
    source_name = str(payload.get("source") or "discovery")[:120]
    reported = payload.get("items")
    if not isinstance(reported, list) or not reported:
        raise ValueError("Nothing reported - give items: [{type, identifier, ...}]")
    savepoint = db.begin_nested()
    details: list[dict[str, Any]] = []
    seen: set[int] = set()
    counts = {"created": 0, "updated": 0, "unchanged": 0, "conflicts": 0, "missing": 0, "rejected": 0}
    try:
        # parents first, so a card reported before its OLT still finds it
        pending = list(reported)
        for _ in range(4):
            if not pending:
                break
            later = []
            for raw in pending:
                outcome = _reconcile_one(db, tenant_id, raw, source_name, actor_id, final=False)
                if outcome is None:
                    later.append(raw)
                    continue
                details.append(outcome)
                counts[outcome["result"]] += 1
                if outcome.get("item_id"):
                    seen.add(outcome["item_id"])
            pending = later
        for raw in pending:  # parents that never arrived
            outcome = _reconcile_one(db, tenant_id, raw, source_name, actor_id, final=True)
            details.append(outcome)
            counts[outcome["result"]] += 1
        if payload.get("full_scan_of"):
            scope = resolve_item(db, tenant_id, payload["full_scan_of"])
            reported_types = {str(r.get("type") or "").lower() for r in reported if isinstance(r, dict)}
            type_ids = [i for (i,) in db.query(InvItemType.id).filter(InvItemType.tenant_id == tenant_id, InvItemType.code.in_(reported_types))]
            for item in db.query(InvItem).filter(InvItem.tenant_id == tenant_id, InvItem.type_id.in_(type_ids or [0]), InvItem.lifecycle != "retired",
                                                 InvItem.location_id.in_(_locations_under(db, scope.location_id or scope.id))):
                if item.id not in seen:
                    counts["missing"] += 1
                    details.append({"result": "missing", "identifier": item.identifier, "item_id": item.id,
                                    "note": f"Recorded at {scope.identifier} but {source_name} no longer sees it - check it on site"})
                    if item.oper_status != "unknown":
                        _history(db, item, "reconciled", {"note": f"not seen by {source_name}", "oper_status": {"from": item.oper_status, "to": "unknown"}},
                                 "discovery", source_name, actor_id)
                        item.oper_status = "unknown"
        if dry_run:
            savepoint.rollback()
        else:
            savepoint.commit()
    except Exception:
        if savepoint.is_active:
            savepoint.rollback()
        raise
    run = InvReconcileRun(tenant_id=tenant_id, source_name=source_name, dry_run=dry_run, summary_json=_dumps(counts), details_json=_dumps(details[:500]))
    db.add(run)
    db.flush()
    return {"run_id": run.id, "dry_run": dry_run, "source": source_name, "summary": counts, "details": details}


def _reconcile_one(db: Session, tenant_id: int, raw: Any, source_name: str, actor_id: int | None, final: bool) -> dict[str, Any] | None:
    if not isinstance(raw, dict) or not raw.get("type") or not (raw.get("identifier") or raw.get("serial")):
        return {"result": "rejected", "identifier": str(raw)[:80] if not isinstance(raw, dict) else raw.get("identifier"), "note": "needs a type and an identifier or serial"}
    t = type_by_code(db, tenant_id, raw["type"]) or find_type(db, tenant_id, raw["type"])
    if t is None:
        return {"result": "rejected", "identifier": raw.get("identifier"), "note": f"unknown type '{raw['type']}' - add the item type first"}
    parent = None
    if raw.get("parent"):
        try:
            parent = resolve_item(db, tenant_id, raw["parent"])
        except ValueError:
            if not final:
                return None
            return {"result": "rejected", "identifier": raw.get("identifier"), "note": f"its parent '{raw['parent']}' is neither recorded nor reported"}
    identifier = str(raw.get("identifier") or "").strip()
    q = db.query(InvItem).filter(InvItem.tenant_id == tenant_id, InvItem.lifecycle != "retired")
    item = q.filter(InvItem.type_id == t.id, InvItem.identifier == identifier).first() if identifier else None
    matched_by = "identifier"
    if item is None and raw.get("serial"):
        item, matched_by = q.filter(InvItem.serial == str(raw["serial"])).first(), "serial"
    try:
        if item is None:
            made = create_item(db, tenant_id, t, identifier or str(raw["serial"]), name=str(raw.get("name") or ""), parent=parent.id if parent else None,
                               attributes_=raw.get("attributes") or {}, oper_status=raw.get("oper_status") if raw.get("oper_status") in OPER_STATUSES else "up",
                               serial=str(raw.get("serial") or ""), vendor=str(raw.get("vendor") or ""), model=str(raw.get("model") or ""),
                               source="discovery", reference=source_name, actor_id=actor_id, verified=True)
            return {"result": "created", "identifier": made.identifier, "item_id": made.id, "note": f"new {t.name}"}
        if item.type_id != t.id:
            return {"result": "conflicts", "identifier": identifier, "item_id": item.id,
                    "note": f"serial {raw.get('serial')} is recorded on {item.identifier} ({db.get(InvItemType, item.type_id).name}), reported as {t.name}"}
        changes: dict[str, Any] = {k: raw[k] for k in ("name", "vendor", "model") if raw.get(k) and raw[k] != getattr(item, k)}
        if raw.get("serial") and raw["serial"] != item.serial:
            if item.serial and SOURCE_PRIORITY["discovery"] < SOURCE_PRIORITY.get(item.source, 0):
                return {"result": "conflicts", "identifier": item.identifier, "item_id": item.id,
                        "note": f"serial recorded as {item.serial}, {source_name} sees {raw['serial']} - check which is right"}
            changes["serial"] = raw["serial"]
        if parent is not None and item.parent_id != parent.id:
            changes["parent"] = parent.id
        if isinstance(raw.get("attributes"), dict):
            current = _j(item.attributes_json, {})
            diff = {k: v for k, v in raw["attributes"].items() if current.get(k) != v}
            if diff:
                changes["attributes"] = diff
        before_attrs = item.attributes_json
        if changes:
            update_item(db, tenant_id, item.id, changes, source="discovery", reference=source_name, actor_id=actor_id)
        if raw.get("oper_status") in OPER_STATUSES:
            set_oper_status(db, tenant_id, item, raw["oper_status"], source="discovery")
        item.last_verified_at = utcnow()
        kept = "attributes" in changes and item.attributes_json == before_attrs
        if changes and not kept:
            return {"result": "updated", "identifier": item.identifier, "item_id": item.id, "note": f"matched by {matched_by}: {', '.join(changes)}"}
        if kept:
            return {"result": "unchanged", "identifier": item.identifier, "item_id": item.id,
                    "note": "values set by hand are kept - discovery doesn't overwrite them"}
        return {"result": "unchanged", "identifier": item.identifier, "item_id": item.id, "note": f"matched by {matched_by}"}
    except ValueError as exc:
        return {"result": "rejected", "identifier": identifier, "note": str(exc)}


# ================================================================== a sample network to start from

def load_sample(db: Session, tenant_id: int, actor_id: int | None = None) -> dict[str, Any]:
    """A small fibre network: a region with an exchange (OLT, cards, ports), a street cabinet (splitter), a store
    of ONTs / CPEs / routers, and pools for VLANs, IP blocks, phone numbers, circuit and service ids."""
    ensure_types(db, tenant_id)
    if db.query(InvItem.id).filter(InvItem.tenant_id == tenant_id, InvItem.identifier == "BLR").first():
        raise ValueError("The sample network is already loaded")
    kw = {"source": "manual", "reference": "sample network", "actor_id": actor_id, "verified": True}
    region = create_item(db, tenant_id, "region", "BLR", name="Bangalore", attributes_={"coverage": "bangalore, bengaluru"}, **kw)
    exchange = create_item(db, tenant_id, "exchange", "BLR-EX-01", name="Bangalore Koramangala exchange", parent=region.id,
                           attributes_={"coverage": "koramangala, hsr layout, btm layout, 560034, 560102", "address": "80 Feet Road, Koramangala"}, **kw)
    exchange2 = create_item(db, tenant_id, "exchange", "BLR-EX-02", name="Bangalore Indiranagar exchange", parent=region.id,
                            attributes_={"coverage": "indiranagar, domlur, 560038", "address": "100 Feet Road, Indiranagar"}, **kw)
    store = create_item(db, tenant_id, "store", "BLR-STORE", name="Bangalore store", parent=region.id, attributes_={"address": "Peenya Industrial Area"}, **kw)
    cabinet = create_item(db, tenant_id, "cabinet", "BLR-CAB-17", name="Koramangala 5th block cabinet", parent=exchange.id,
                          attributes_={"coverage": "5th block"}, **kw)
    for ex, olt_count in ((exchange, 1), (exchange2, 1)):
        for o in range(olt_count):
            olt = create_item(db, tenant_id, "olt", parent=ex.id, vendor="Huawei", model="MA5800-X7", serial=f"OLT{ex.id:03d}{o:02d}",
                              attributes_={"slots": 2, "management_ip": f"10.255.{ex.id % 250}.{o + 1}"}, oper_status="up", **kw)
            for c in range(2):
                card = create_item(db, tenant_id, "card", f"{olt.identifier}/{c + 1}", parent=olt.id, attributes_={"slot": c + 1}, model="GPHF",
                                   oper_status="up", **kw)
                for p in range(8):
                    create_item(db, tenant_id, "port", f"{card.identifier}/{p + 1}", parent=card.id, attributes_={"speed": "GPON"}, oper_status="up", **kw)
    splitter = create_item(db, tenant_id, "splitter", parent=cabinet.id, attributes_={"ratio": "1:8"}, oper_status="up", **kw)
    for p in range(8):
        create_item(db, tenant_id, "splitter_port", f"{splitter.identifier}:{p + 1}", parent=splitter.id, **kw)
    for code, count, vendor, model in (("ont", 12, "Huawei", "HG8245H"), ("cpe", 6, "TP-Link", "Archer C6"), ("router", 4, "Cisco", "ISR 1111")):
        for n in range(count):
            create_item(db, tenant_id, code, f"{code.upper()}-BLR-{n + 1:04d}", parent=store.id, vendor=vendor, model=model,
                        serial=f"{code.upper()}SN{tenant_id:03d}{n + 1:05d}", attributes_={"mac": f"02:{tenant_id % 256:02x}:{['ont', 'cpe', 'router'].index(code):02x}:00:{n // 256:02x}:{n % 256:02x}"},
                        **kw)
    pools = [
        save_pool(db, tenant_id, {"type_code": "vlan", "range_start": "100", "range_end": "999", "location": exchange.id, "name": "VLANs BLR-EX-01"}),
        save_pool(db, tenant_id, {"type_code": "vlan", "range_start": "100", "range_end": "999", "location": exchange2.id, "name": "VLANs BLR-EX-02"}),
        save_pool(db, tenant_id, {"type_code": "ip_block", "pool_type": "ip", "range_start": "10.20.0.0/16", "block_size": 29, "name": "Customer IPv4 /29s"}),
        save_pool(db, tenant_id, {"type_code": "ipv6_prefix", "pool_type": "ip", "range_start": "2001:db8:100::/40", "block_size": 56, "name": "Customer IPv6 /56s"}),
        save_pool(db, tenant_id, {"type_code": "phone_number", "pool_type": "sequence", "range_start": "1", "range_end": "9999",
                                  "format": "+91 80 4100 {n:04d}", "name": "Bangalore numbers", "location": region.id}),
        save_pool(db, tenant_id, {"type_code": "circuit_id", "pool_type": "sequence", "range_start": "1", "range_end": "99999", "format": "CKT/BLR/{n:05d}",
                                  "name": "Circuit ids"}),
        save_pool(db, tenant_id, {"type_code": "service_id", "pool_type": "sequence", "range_start": "1", "range_end": "999999", "format": "SVC-{n:06d}",
                                  "name": "Service ids"}),
    ]
    relate(db, tenant_id, cabinet.identifier, exchange.identifier, "depends_on", "fed from the exchange")
    return {"locations": 5, "pools": len(pools), "items": db.query(InvItem.id).filter(InvItem.tenant_id == tenant_id).count()}

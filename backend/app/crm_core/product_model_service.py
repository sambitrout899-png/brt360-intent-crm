"""Product modelling (models/product_model.py): product hierarchies, their
import / export, and Siebel-style eligibility, compatibility and
constraint rules.

The spreadsheet format is the one product managers already keep:

    Product Element | Type | Parent | Description | Mandatory/Optional (M/O) | Pricing Impact | Provisioning Impact

Type is Root Product, Child Product or Product Attribute. For an attribute,
Description holds its domain: a list (WiMax, Microwave, Fiber ...), a
range ("512 Kbps to 4Mbps"), or an inventory reference (IP Address, Place,
Logical Device, Device Interface) that provisioning fills in. Optional
extra columns round-trip everything else: Values, Default, Unit, Min, Max,
Price, Charge Type, Min Qty, Max Qty, Price Impacts, Segment, Status.

Imports are additive - elements are matched by name under their root and
updated; nothing missing from the file is removed. A dry run reports what
would change and every row it can't take.
"""

from __future__ import annotations

import csv
import io
import json
import re
import zipfile
from typing import Any
from xml.etree import ElementTree as ET
from xml.sax.saxutils import escape

from sqlalchemy.orm import Session

from app.crm_core import product_config_service as config
from app.models.catalog import OfferingStatus, ProductOffering
from app.models.crm import Lead, Party
from app.models.product_config import OfferingAttribute
from app.models.product_model import CHARGE_TYPES, PRODUCT_RULE_TYPES, ProductComponent, ProductRule

_key = config._key
REFERENCE_TYPES = ("IP Address", "Place", "Logical Device", "Physical Device", "Device Interface", "Port", "VLAN", "Location",
                   "Site", "Circuit", "Network Element", "Service Point")
COLUMNS = ["Product Element", "Type", "Parent", "Description", "Mandatory/Optional(M/O)", "Pricing Impact", "Provisioning Impact",
           "Values", "Default", "Unit", "Min", "Max", "Price", "Charge Type", "Min Qty", "Max Qty", "Price Impacts", "Segment", "Status",
           "Slot Products", "Slot Class", "Slot Default"]
RULE_COLUMNS = ["Product", "Rule Type", "Rule", "Message", "Active"]
CLASS_COLUMNS = ["Class", "Parent Class", "Description", "Members"]


# ---------------------------------------------------------------- reading a product


def offering_of(db: Session, tenant_id: int, name: str) -> ProductOffering | None:
    rows = db.query(ProductOffering).filter(ProductOffering.tenant_id == tenant_id, ProductOffering.name.ilike(name or "")).all()
    active = [r for r in rows if r.status == OfferingStatus.active]
    return max(active or rows, key=lambda r: r.version, default=None)


def products(db: Session, tenant_id: int) -> list[dict[str, Any]]:
    """Every product (latest version per name) with how much of it is modelled."""
    latest: dict[str, ProductOffering] = {}
    for o in db.query(ProductOffering).filter(ProductOffering.tenant_id == tenant_id).order_by(ProductOffering.version):
        if o.status != OfferingStatus.retired or o.name not in latest:
            latest[o.name] = o
    counts: dict[str, list[int]] = {}
    for (name,) in db.query(OfferingAttribute.offering_name).filter(OfferingAttribute.tenant_id == tenant_id, OfferingAttribute.is_active.is_(True)):
        counts.setdefault(name.lower(), [0, 0, 0])[0] += 1
    for (name,) in db.query(ProductComponent.offering_name).filter(ProductComponent.tenant_id == tenant_id, ProductComponent.is_active.is_(True)):
        counts.setdefault(name.lower(), [0, 0, 0])[1] += 1
    for (name,) in db.query(ProductRule.offering_name).filter(ProductRule.tenant_id == tenant_id, ProductRule.is_active.is_(True)):
        counts.setdefault(name.lower(), [0, 0, 0])[2] += 1
    return [
        {"name": o.name, "segment": o.segment, "status": o.status.value, "price": o.price, "version": o.version, "brt_id": o.brt_id,
         "attributes": counts.get(o.name.lower(), [0, 0, 0])[0], "children": counts.get(o.name.lower(), [0, 0, 0])[1],
         "rules": counts.get(o.name.lower(), [0, 0, 0])[2]}
        for o in sorted(latest.values(), key=lambda o: o.name.lower())
    ]


def tree(db: Session, tenant_id: int, name: str) -> dict[str, Any]:
    offering = offering_of(db, tenant_id, name)
    if offering is None:
        raise ValueError(f"No product named '{name}'")
    design = config.schema(db, tenant_id, offering.name, workspace=True)
    nodes = {c["id"]: {**c, "attributes": [], "children": []} for c in design["components"]}
    root = {"name": offering.name, "segment": offering.segment, "status": offering.status.value, "price": offering.price,
            "brt_id": offering.brt_id, "attributes": [], "children": []}
    for a in design["attributes"]:
        (nodes[a["component_id"]]["attributes"] if a["component_id"] in nodes else root["attributes"]).append(a)
    for c in design["components"]:
        (nodes[c["parent_id"]]["children"] if c["parent_id"] in nodes else root["children"]).append(nodes[c["id"]])
    root["rules"] = [rule_out(r) for r in rules_of(db, tenant_id, offering.name, active_only=False)]
    root["attribute_rules"] = design["rules"]
    return root


# ---------------------------------------------------------------- editing the hierarchy


def _component_row(db: Session, tenant_id: int, offering_name: str, name: str) -> ProductComponent | None:
    rows = db.query(ProductComponent).filter(ProductComponent.tenant_id == tenant_id, ProductComponent.offering_name.ilike(offering_name)).all()
    return next((r for r in rows if _key(r.name) == _key(name)), None)


def upsert_component(db: Session, tenant_id: int, offering_name: str, data: dict[str, Any]) -> str:
    name = str(data.get("name") or "").strip()
    if not name:
        raise ValueError("A child product needs a name")
    if _key(name) == _key(offering_name):
        raise ValueError(f"'{name}' is the root product itself")
    parent = None
    if data.get("parent") and _key(data["parent"]) != _key(offering_name):
        parent = _component_row(db, tenant_id, offering_name, data["parent"])
        if parent is None:
            raise ValueError(f"{name}: no child product '{data['parent']}' to put it under")
    row = _component_row(db, tenant_id, offering_name, name)
    created = row is None
    if row is None:
        count = db.query(ProductComponent).filter(ProductComponent.tenant_id == tenant_id, ProductComponent.offering_name.ilike(offering_name)).count()
        row = ProductComponent(tenant_id=tenant_id, offering_name=offering_name, name=name, sequence=count)
        db.add(row)
        db.flush()
    if parent is not None:
        p, seen = parent, set()
        while p is not None and p.id not in seen:  # never a loop
            if p.id == row.id:
                raise ValueError(f"{name} can't sit under its own child")
            seen.add(p.id)
            p = db.get(ProductComponent, p.parent_id) if p.parent_id else None
    if "parent" in data:
        row.parent_id = parent.id if parent else None
    row.is_active = True
    if "description" in data:
        row.description = data.get("description") or ""
    if data.get("mandatory") is not None and data.get("min_qty") is None:
        row.min_qty = 1 if data["mandatory"] else 0
    for field in ("min_qty", "max_qty", "default_qty"):
        if data.get(field) not in (None, ""):
            setattr(row, field, max(0, int(float(data[field]))))
    row.max_qty = max(row.max_qty or 1, row.min_qty or 0, 1)
    if "charge_type" in data:
        charge = (data.get("charge_type") or None) and str(data["charge_type"]).lower().replace("-", "_").replace(" ", "_")
        charge = {"mrc": "recurring", "monthly": "recurring", "nrc": "one_time", "onetime": "one_time", "setup": "one_time"}.get(charge or "", charge)
        if charge is not None and charge not in CHARGE_TYPES:
            raise ValueError(f"{name}: charge type is recurring (monthly) or one_time")
        row.charge_type = charge
    if data.get("price") not in (None, ""):
        row.price = float(str(data["price"]).replace(",", "").replace("₹", ""))
    if data.get("affects_provisioning") is not None:
        row.affects_provisioning = bool(data["affects_provisioning"])
    db.flush()
    return f"{'Added' if created else 'Updated'} child product {name}" + (f" under {parent.name}" if parent else "")


def remove_component(db: Session, tenant_id: int, offering_name: str, name: str) -> str:
    row = _component_row(db, tenant_id, offering_name, name)
    if row is None or not row.is_active:
        raise ValueError(f"{offering_name} has no child product '{name}'")
    stack, removed = [row], []
    while stack:
        current = stack.pop()
        current.is_active = False
        removed.append(current.name)
        for a in db.query(OfferingAttribute).filter(OfferingAttribute.component_id == current.id):
            a.is_active = False
        stack += db.query(ProductComponent).filter(ProductComponent.parent_id == current.id, ProductComponent.is_active.is_(True)).all()
    db.flush()
    return f"Removed {', '.join(removed)}"


def ensure_offering(db: Session, tenant_id: int, name: str, segment: str | None = None, price: float | None = None,
                    status: str | None = None) -> tuple[ProductOffering, bool]:
    offering = offering_of(db, tenant_id, name)
    if offering is not None:
        return offering, False
    offering = ProductOffering(tenant_id=tenant_id, name=name, segment=segment or "B2B", price=float(price or 0), price_floor=0.0,
                               composition="", status=OfferingStatus.active if (status or "").lower() == "active" else OfferingStatus.draft)
    db.add(offering)
    db.flush()
    return offering, True


def publish(db: Session, tenant_id: int, name: str) -> ProductOffering:
    offering = offering_of(db, tenant_id, name)
    if offering is None:
        raise ValueError(f"No product named '{name}'")
    if offering.status == OfferingStatus.retired:
        raise ValueError(f"{name} is retired")
    offering.status = OfferingStatus.active
    db.flush()
    return offering


# ---------------------------------------------------------------- spreadsheet rows


def _norm_header(h: str) -> str:
    k = re.sub(r"[^a-z]", "", str(h or "").lower())
    for prefix, name in (("slotproducts", "slot_products"), ("slotclass", "slot_class"), ("slotdefault", "slot_default"),
                         ("parentclass", "parent_class"), ("class", "class"), ("members", "members"),
                         ("productelement", "element"), ("element", "element"), ("mandatory", "mandatory"), ("pricingimpa", "pricing"),
                         ("provisioningimpa", "provisioning"), ("priceimpact", "price_impacts"), ("chargetype", "charge_type"),
                         ("minqty", "min_qty"), ("maxqty", "max_qty"), ("defaultqty", "default_qty"), ("description", "description"),
                         ("parent", "parent"), ("allowedvalues", "values"), ("values", "values"), ("default", "default"),
                         ("unit", "unit"), ("segment", "segment"), ("status", "status"), ("price", "price"), ("type", "type"),
                         ("minimum", "min"), ("maximum", "max"), ("min", "min"), ("max", "max"),
                         ("ruletype", "rule_type"), ("rule", "rule"), ("product", "product"), ("message", "message"), ("active", "active")):
        if k == prefix or (len(prefix) > 6 and k.startswith(prefix)):
            return name
    return k


def _yes(v: Any) -> bool:
    return str(v or "").strip().lower() in ("y", "yes", "true", "1", "x", "m", "mandatory")


def _kind(raw: str) -> str | None:
    k = _key(raw)
    if k in ("rootproduct", "root", "product", "rootofferring", "offering"):
        return "root"
    if k in ("childproduct", "child", "component", "subproduct", "childcomponent"):
        return "child"
    if k in ("productattribute", "attribute", "attr"):
        return "attribute"
    return None


def _name_and_unit(element: str) -> tuple[str, str | None]:
    m = re.match(r"^(.*?)\s*\(\s*([A-Za-z/]+)\s*\)\s*$", element)
    if m and m.group(2).lower() in config._RATE or (m and m.group(2).lower() in ("months", "days", "users", "ips", "gb", "mb", "sites")):
        unit = m.group(2)
        unit = {"kbps": "Kbps", "mbps": "Mbps", "gbps": "Gbps"}.get(unit.lower(), unit)
        return m.group(1).strip(), unit
    return element.strip(), None


_RANGE = re.compile(r"^\s*(\d[\d.,]*)\s*([a-zA-Z/]*)\s*(?:to|-|–|until|upto|up to)\s*(\d[\d.,]*)\s*([a-zA-Z/]*)\s*$", re.I)


def domain(name: str, description: str, unit: str | None) -> dict[str, Any]:
    """What an attribute's Description says about its values."""
    text = (description or "").strip()
    if not text:
        return {"data_type": "boolean", "values": ["Yes", "No"]} if re.match(r"^(use|is|has|with|enable|include)\b", name, re.I) else {}
    if m := _RANGE.match(text):
        lo_unit = m.group(2) or m.group(4) or unit
        hi_unit = m.group(4) or m.group(2) or unit
        target = unit or lo_unit or None
        lo = config.in_unit(f"{m.group(1)} {lo_unit or ''}", target)
        hi = config.in_unit(f"{m.group(3)} {hi_unit or ''}", target)
        if lo is not None and hi is not None:
            nice = {"kbps": "Kbps", "mbps": "Mbps", "gbps": "Gbps"}
            return {"data_type": "number", "min": lo, "max": hi, "unit": nice.get((target or "").lower(), target)}
    ref = next((r for r in REFERENCE_TYPES if _key(r) == _key(text)), None)
    if ref:
        return {"reference_type": ref}
    if "," in text or ";" in text:
        parts = [p.strip() for p in re.split(r"[,;]", text) if p.strip()]
        if "ratio" in name.lower() and all(p.isdigit() for p in parts):
            parts = [f"1:{p}" for p in parts]
        return {"data_type": "enum", "values": parts}
    return {"description": text}


_IMPACTS = re.compile(r"^\s*(?P<value>[^:=]+?)\s*[:=]\s*(?P<sign>[+-])?\s*(?P<num>\d[\d,]*(?:\.\d+)?)\s*(?P<pct>%)?\s*(?P<each>each|per\s+\w+)?\s*$", re.I)


def parse_impacts(raw: str) -> list[dict[str, Any]]:
    out = []
    # ";" or a new line between impacts - or a comma when a new "value:" follows it ("1,499" stays one number)
    for part in re.split(r";|\n|,\s*(?=[^,;:=\n]+[:=])", raw or ""):
        if not part.strip():
            continue
        m = _IMPACTS.match(part)
        if not m:
            raise ValueError(f"price impact '{part.strip()}' - write it like 'Fiber: +500', 'DSL: -10%' or '*: 2 each'")
        amount = float(m.group("num").replace(",", "")) * (-1 if m.group("sign") == "-" else 1)
        kind = "per_unit" if m.group("each") else "percent" if m.group("pct") else "flat"
        out.append({"value": m.group("value").strip(), "type": kind, "amount": amount})
    return out


def impacts_text(impacts: list[dict[str, Any]]) -> str:
    def one(i: dict[str, Any]) -> str:
        amount = f"{i['amount']:+g}" if i["type"] != "per_unit" else f"{i['amount']:g} each"
        return f"{i['value']}: {amount}{'%' if i['type'] == 'percent' else ''}"
    return "; ".join(one(i) for i in impacts)


def read_rows(content: bytes, file_name: str) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    """(product rows, rule rows) from a CSV or an Excel workbook."""
    sheets = read_sheets(content, file_name)
    return sheets["products"], sheets["rules"]


def read_sheets(content: bytes, file_name: str) -> dict[str, list[dict[str, str]]]:
    """Product, rule and class rows from a CSV or an Excel workbook."""
    out: dict[str, list[dict[str, str]]] = {"products": [], "rules": [], "classes": []}
    if file_name.lower().endswith((".xlsx", ".xlsm")) or content[:2] == b"PK":
        for title, rows in read_xlsx(content):
            records = _records(rows)
            if not records:
                continue
            if "element" in records[0]:
                out["products"] += records
            elif "class" in records[0] and "members" in records[0]:
                out["classes"] += records
            elif "rule" in records[0] or "rule_type" in records[0]:
                out["rules"] += records
        return out
    products, rules = _read_csv(content)
    out["products"], out["rules"] = products, rules
    return out


def _read_csv(content: bytes) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    text = content.decode("utf-8-sig", errors="replace")
    sample = text[:2000]
    dialect = csv.Sniffer().sniff(sample, delimiters=",;\t") if sample.strip() else csv.excel
    rows = list(csv.reader(io.StringIO(text), dialect))
    records = _records(rows)
    if records and "element" not in records[0] and ("rule" in records[0] or "rule_type" in records[0]):
        return [], records
    return records, []


def _records(rows: list[list[str]]) -> list[dict[str, str]]:
    rows = [r for r in rows if any(str(c).strip() for c in r)]
    if not rows:
        return []
    header = [_norm_header(h) for h in rows[0]]
    out = []
    for r in rows[1:]:
        record = {h: str(r[i]).strip() if i < len(r) and r[i] is not None else "" for i, h in enumerate(header) if h}
        record["_row"] = str(rows.index(r) + 1)
        out.append(record)
    return out


def plan_import(db: Session, tenant_id: int, product_rows: list[dict[str, str]], rule_rows: list[dict[str, str]],
                class_rows: list[dict[str, str]] | None = None) -> dict[str, Any]:
    """What an import would do, as operations - and the rows it can't take."""
    errors: list[dict[str, Any]] = []
    ops: list[dict[str, Any]] = [
        {"op": "class", "name": r["class"].strip(), "parent": (r.get("parent_class") or "").strip() or None,
         "description": r.get("description", ""), "members": [m.strip() for m in (r.get("members") or "").split(",") if m.strip()], "row": r["_row"]}
        for r in class_rows or [] if (r.get("class") or "").strip()
    ]
    roots: dict[str, str] = {}  # key -> root name
    owner: dict[str, str] = {}  # element key -> its root name
    children: dict[str, dict[str, str]] = {}
    for r in product_rows:
        kind = _kind(r.get("type", ""))
        name = r.get("element", "").strip()
        if not name:
            continue
        if kind is None:
            errors.append({"row": r["_row"], "element": name, "error": f"Type '{r.get('type')}' - use Root Product, Child Product or Product Attribute"})
        elif kind == "root":
            roots[_key(name)] = name
            owner[_key(name)] = name
            ops.append({"op": "root", "name": name, "segment": r.get("segment") or None, "price": r.get("price") or None,
                        "status": r.get("status") or None, "row": r["_row"]})
        elif kind == "child":
            children[_key(name)] = r
    # children in parent order (any depth)
    pending = dict(children)
    while pending:
        progressed = False
        for k, r in list(pending.items()):
            parent = r.get("parent", "")
            if _key(parent) in owner:
                root = owner[_key(parent)]
                owner[k] = root
                text = f"{r.get('element')} {r.get('description')}".lower()
                charge = r.get("charge_type") or ("recurring" if re.search(r"\bmrc\b|monthly|recurring", text) else
                                                  "one_time" if re.search(r"\bnrc\b|one[- ]?time|setup|installation charge", text) else None)
                mandatory = _yes(r.get("mandatory"))
                ops.append({"op": "child", "root": root, "name": r["element"].strip(), "parent": parent if _key(parent) != _key(root) else None,
                            "description": r.get("description", ""), "mandatory": mandatory,
                            "min_qty": r.get("min_qty") or (1 if mandatory else 0), "max_qty": r.get("max_qty") or None,
                            "default_qty": r.get("min_qty") or (1 if mandatory else 0), "charge_type": charge, "price": r.get("price") or None,
                            "affects_provisioning": _yes(r.get("provisioning")), "row": r["_row"],
                            "slot_products": r.get("slot_products") or "", "slot_class": r.get("slot_class") or "",
                            "slot_default": r.get("slot_default") or ""})
                del pending[k]
                progressed = True
        if not progressed:
            for k, r in pending.items():
                errors.append({"row": r["_row"], "element": r.get("element"), "error": f"Parent '{r.get('parent')}' is not a product in this file or the catalog"})
            break
    for r in product_rows:
        if _kind(r.get("type", "")) != "attribute" or not r.get("element", "").strip():
            continue
        parent = r.get("parent", "")
        root = owner.get(_key(parent))
        if root is None and offering_of(db, tenant_id, parent) is not None:
            root = offering_of(db, tenant_id, parent).name
            owner[_key(parent)] = root
        if root is None:
            existing = db.query(ProductComponent).filter(ProductComponent.tenant_id == tenant_id, ProductComponent.is_active.is_(True)).all()
            match = next((c for c in existing if _key(c.name) == _key(parent)), None)
            root = match.offering_name if match else None
        if root is None:
            errors.append({"row": r["_row"], "element": r.get("element"), "error": f"Parent '{parent}' is not a product in this file or the catalog"})
            continue
        name, unit = _name_and_unit(r["element"])
        unit = r.get("unit") or unit
        found = domain(name, r.get("description", ""), unit)
        op: dict[str, Any] = {"op": "upsert_attribute", "root": root, "name": name, "component": parent if _key(parent) != _key(root) else None,
                              "required": _yes(r.get("mandatory")), "affects_price": _yes(r.get("pricing")),
                              "affects_provisioning": _yes(r.get("provisioning")), "description": r.get("description") or None, "row": r["_row"]}
        op["kind"] = "pricing" if op["affects_price"] else "technical"
        if found.get("unit") or unit:
            op["unit"] = found.get("unit") or unit
        if r.get("values"):
            op["values"], op["data_type"] = [v.strip() for v in re.split(r"[,;]", r["values"]) if v.strip()], "enum"
        elif found.get("values"):
            op["values"], op["data_type"] = found["values"], found.get("data_type", "enum")
        if found.get("min") is not None or r.get("min"):
            op["min"] = float(r["min"]) if r.get("min") else found["min"]
            op["max"] = float(r["max"]) if r.get("max") else found.get("max")
            op["data_type"] = "number"
        op["reference_type"] = found.get("reference_type")
        if r.get("default"):
            op["default"] = r["default"]
        if r.get("price_impacts"):
            try:
                op["price_impacts"] = parse_impacts(r["price_impacts"])
            except ValueError as exc:
                errors.append({"row": r["_row"], "element": r.get("element"), "error": str(exc)})
                continue
        ops.append(op)
    for r in rule_rows:
        if not r.get("rule"):
            continue
        rule_type = (r.get("rule_type") or "").strip().lower()
        if rule_type not in PRODUCT_RULE_TYPES:
            errors.append({"row": r["_row"], "element": r.get("product"), "error": f"Rule Type is one of {', '.join(PRODUCT_RULE_TYPES)}"})
            continue
        ops.append({"op": "rule", "root": r.get("product", "").strip(), "rule_type": rule_type, "text": r["rule"],
                    "message": r.get("message") or "", "active": r.get("active", "") == "" or _yes(r.get("active")), "row": r["_row"]})
    return {"ops": ops, "errors": errors, "preview": describe_import(ops)}


def describe_import(ops: list[dict[str, Any]]) -> list[str]:
    out = []
    for o in ops:
        if o["op"] == "root":
            out.append(f"Product {o['name']}")
        elif o["op"] == "child":
            charge = f", {o['charge_type'].replace('_', '-')} charge" if o.get("charge_type") else ""
            out.append(f"  {o['root']} › {o.get('parent') + ' › ' if o.get('parent') else ''}{o['name']} ({'mandatory' if o['mandatory'] else 'optional'}{charge})")
        elif o["op"] == "upsert_attribute":
            where = f"{o['root']} › {o['component']}" if o.get("component") else o["root"]
            flags = ", ".join(x for x in ("price" if o["affects_price"] else "", "provisioning" if o["affects_provisioning"] else "") if x)
            dom = (f"{o['min']:g}-{o['max']:g} {o.get('unit') or ''}".strip() if o.get("min") is not None else
                   ", ".join(o["values"]) if o.get("values") else o.get("reference_type") or "free text")
            out.append(f"  {where}: attribute {o['name']} [{dom}]{' mandatory' if o['required'] else ''}{f' - affects {flags}' if flags else ''}")
        elif o["op"] == "rule":
            out.append(f"  {o['root']}: {o['rule_type']} rule \"{o['text']}\"")
        elif o["op"] == "class":
            out.append(f"Class {o['name']}{' under ' + o['parent'] if o.get('parent') else ''}: {', '.join(o['members']) or 'no members yet'}")
        if o["op"] == "child" and (o.get("slot_products") or o.get("slot_class")):
            out[-1] += f" - filled by {('any ' + o['slot_class']) if o.get('slot_class') else o['slot_products']}"
    return out


def apply_import(db: Session, tenant_id: int, plan: dict[str, Any]) -> dict[str, Any]:
    changes: list[str] = []
    errors = list(plan.get("errors") or [])
    created = []
    for o in plan["ops"]:
        savepoint = db.begin_nested()
        try:
            if o["op"] == "root":
                offering, new = ensure_offering(db, tenant_id, o["name"], o.get("segment"),
                                                float(str(o["price"]).replace(",", "")) if o.get("price") else None, o.get("status"))
                if new:
                    created.append(offering.name)
                    changes.append(f"Created product {offering.name} ({offering.status.value})")
            elif o["op"] == "child":
                if offering_of(db, tenant_id, o["root"]) is None:
                    ensure_offering(db, tenant_id, o["root"])
                root_name = offering_of(db, tenant_id, o["root"]).name
                changes.append(upsert_component(db, tenant_id, root_name, o))
                if o.get("slot_products") or o.get("slot_class"):
                    from app.crm_core import model_lifecycle

                    changes.append(model_lifecycle.set_slot(
                        db, tenant_id, root_name, o["name"], "class" if o.get("slot_class") else "products",
                        [p.strip() for p in (o.get("slot_products") or "").split(",") if p.strip()], o.get("slot_class") or None,
                        o.get("slot_default") or None))
            elif o["op"] == "class":
                from app.crm_core import model_lifecycle

                model_lifecycle.save_class(db, tenant_id, o["name"], o.get("parent"), o.get("description") or "", o["members"])
                changes.append(f"Class {o['name']}")
            elif o["op"] == "upsert_attribute":
                root = offering_of(db, tenant_id, o["root"])
                if root is None:
                    root, _ = ensure_offering(db, tenant_id, o["root"])
                changes.append(f"{root.name}: " + config._upsert_attribute(db, tenant_id, root.name, {k: v for k, v in o.items() if k not in ("root", "row")}))
            elif o["op"] == "rule":
                root = offering_of(db, tenant_id, o["root"])
                if root is None:
                    raise ValueError(f"No product named '{o['root']}'")
                row = save_rule(db, tenant_id, root.name, o["rule_type"], o["text"], o.get("message", ""), prefer_rules=True)
                row.is_active = bool(o.get("active", True))
                changes.append(f"{root.name}: {o['rule_type']} rule - {rule_out(row)['explanation']}")
            savepoint.commit()
        except (ValueError, TypeError) as exc:
            savepoint.rollback()
            errors.append({"row": o.get("row"), "element": o.get("name") or o.get("text"), "error": str(exc)})
    db.flush()
    return {"changes": changes, "errors": errors, "created": created}


def export_rows(db: Session, tenant_id: int, names: list[str]) -> tuple[list[list[str]], list[list[str]]]:
    rows: list[list[str]] = [COLUMNS]
    rules: list[list[str]] = [RULE_COLUMNS]

    def fmt(v: Any) -> str:
        if v is None:
            return ""
        if isinstance(v, float):
            return f"{v:g}"
        return str(v)

    def attribute_row(a: dict[str, Any], parent: str) -> list[str]:
        if a.get("min") is not None or a.get("max") is not None:
            description = f"{fmt(a['min'])} {a.get('unit') or ''} to {fmt(a['max'])} {a.get('unit') or ''}".replace("  ", " ").strip()
        elif a.get("reference_type"):
            description = a["reference_type"]
        elif a["values"]:
            description = ",".join(a["values"])
        else:
            description = a.get("description") or ""
        element = f"{a['name']} ({a['unit']})" if a.get("unit") else a["name"]
        return [element, "Product Attribute", parent, description, "M" if a["required"] else "O", "Y" if a["affects_price"] else "",
                "Y" if a["affects_provisioning"] else "", ",".join(a["values"]), fmt(a.get("default")), a.get("unit") or "", fmt(a.get("min")),
                fmt(a.get("max")), "", "", "", "", impacts_text(a["price_impacts"]), "", "", "", "", ""]

    for name in names:
        t = tree(db, tenant_id, name)
        rows.append([t["name"], "Root Product", "", "", "M", "", "", "", "", "", "", "", fmt(t["price"]), "", "", "", "", t["segment"], t["status"], "", "", ""])
        rows += [attribute_row(a, t["name"]) for a in t["attributes"]]

        def walk(node: dict[str, Any], parent: str) -> None:
            rows.append([node["name"], "Child Product", parent, node.get("description") or "", "M" if node["mandatory"] else "O", "",
                         "Y" if node["affects_provisioning"] else "", "", "", "", "", "", fmt(node["price"]) if node["price"] else "",
                         node["charge_type"] or "", fmt(node["min_qty"]), fmt(node["max_qty"]), "", "", "",
                         ",".join((node.get("slot") or {}).get("listed") or []) if (node.get("slot") or {}).get("type") == "products" else "",
                         (node.get("slot") or {}).get("class") or "", (node.get("slot") or {}).get("default") or ""])
            rows.extend(attribute_row(a, node["name"]) for a in node["attributes"])
            for child in node["children"]:
                walk(child, node["name"])

        for child in t["children"]:
            walk(child, t["name"])
        for r in t["rules"]:
            rules.append([t["name"], r["rule_type"], r["source_text"] or r["explanation"], r["message"], "Y" if r["is_active"] else "N"])
    return rows, rules


def to_csv(rows: list[list[str]]) -> str:
    out = io.StringIO()
    csv.writer(out).writerows(rows)
    return out.getvalue()


# ---------------------------------------------------------------- xlsx (standard library only)

_NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
       "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships"}


def _col_index(ref: str) -> int:
    letters = re.match(r"[A-Z]+", ref).group(0)
    n = 0
    for ch in letters:
        n = n * 26 + ord(ch) - 64
    return n - 1


def read_xlsx(content: bytes) -> list[tuple[str, list[list[str]]]]:
    z = zipfile.ZipFile(io.BytesIO(content))
    shared: list[str] = []
    if "xl/sharedStrings.xml" in z.namelist():
        for si in ET.fromstring(z.read("xl/sharedStrings.xml")).findall("m:si", _NS):
            shared.append("".join(t.text or "" for t in si.iter(f"{{{_NS['m']}}}t")))
    workbook = ET.fromstring(z.read("xl/workbook.xml"))
    rels = ET.fromstring(z.read("xl/_rels/workbook.xml.rels"))
    targets = {r.get("Id"): r.get("Target") for r in rels}
    out = []
    for sheet in workbook.find("m:sheets", _NS):
        target = targets.get(sheet.get(f"{{{_NS['r']}}}id"), "")
        path = target.lstrip("/") if target.startswith("/") else f"xl/{target}"
        if path not in z.namelist():
            continue
        rows: list[list[str]] = []
        for row in ET.fromstring(z.read(path)).iter(f"{{{_NS['m']}}}row"):
            cells: dict[int, str] = {}
            for c in row.findall("m:c", _NS):
                kind, v = c.get("t"), c.find("m:v", _NS)
                if kind == "s" and v is not None:
                    value = shared[int(v.text)]
                elif kind == "inlineStr":
                    value = "".join(t.text or "" for t in c.iter(f"{{{_NS['m']}}}t"))
                else:
                    value = v.text if v is not None and v.text is not None else ""
                    if re.fullmatch(r"-?\d+\.0", value or ""):
                        value = value[:-2]
                cells[_col_index(c.get("r", "A1"))] = value
            if cells:
                rows.append([cells.get(i, "") for i in range(max(cells) + 1)])
        out.append((sheet.get("name") or "", rows))
    return out


def write_xlsx(sheets: list[tuple[str, list[list[str]]]]) -> bytes:
    def col(i: int) -> str:
        s = ""
        i += 1
        while i:
            i, r = divmod(i - 1, 26)
            s = chr(65 + r) + s
        return s

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        names = [n for n, _ in sheets]
        z.writestr("[Content_Types].xml",
                   '<?xml version="1.0" encoding="UTF-8"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                   '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
                   '<Default Extension="xml" ContentType="application/xml"/>'
                   '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
                   '<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>'
                   + "".join(f'<Override PartName="/xl/worksheets/sheet{i + 1}.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
                             for i in range(len(sheets))) + "</Types>")
        z.writestr("_rels/.rels", '<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                   '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>')
        z.writestr("xl/workbook.xml", '<?xml version="1.0" encoding="UTF-8"?><workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
                   'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets>'
                   + "".join(f'<sheet name="{escape(n[:31])}" sheetId="{i + 1}" r:id="rId{i + 1}"/>' for i, n in enumerate(names)) + "</sheets></workbook>")
        z.writestr("xl/_rels/workbook.xml.rels", '<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                   + "".join(f'<Relationship Id="rId{i + 1}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet{i + 1}.xml"/>'
                             for i in range(len(sheets)))
                   + f'<Relationship Id="rId{len(sheets) + 1}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/></Relationships>')
        z.writestr("xl/styles.xml", '<?xml version="1.0" encoding="UTF-8"?><styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
                   '<fonts count="2"><font><sz val="11"/><name val="Calibri"/></font><font><b/><sz val="11"/><name val="Calibri"/></font></fonts>'
                   '<fills count="3"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill>'
                   '<fill><patternFill patternType="solid"><fgColor rgb="FFBDD7EE"/></patternFill></fill></fills>'
                   '<borders count="1"><border/></borders><cellStyleXfs count="1"><xf/></cellStyleXfs>'
                   '<cellXfs count="2"><xf/><xf fontId="1" fillId="2" applyFont="1" applyFill="1"/></cellXfs></styleSheet>')
        for i, (_, rows) in enumerate(sheets):
            body = []
            for r, row in enumerate(rows):
                style = ' s="1"' if r == 0 else ""
                cells = "".join(
                    f'<c r="{col(c)}{r + 1}" t="inlineStr"{style}><is><t xml:space="preserve">{escape(str(v))}</t></is></c>'
                    for c, v in enumerate(row) if str(v) != "")
                body.append(f'<row r="{r + 1}">{cells}</row>')
            widths = "".join(f'<col min="{c + 1}" max="{c + 1}" width="{min(60, max(10, max((len(str(row[c])) for row in rows if c < len(row)), default=10) + 2))}" customWidth="1"/>'
                             for c in range(max((len(r) for r in rows), default=0)))
            z.writestr(f"xl/worksheets/sheet{i + 1}.xml", '<?xml version="1.0" encoding="UTF-8"?><worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
                       + (f"<cols>{widths}</cols>" if widths else "") + f"<sheetData>{''.join(body)}</sheetData></worksheet>")
    return buf.getvalue()


# ---------------------------------------------------------------- rules

CUSTOMER_FIELDS = {"segment": "Segment", "region": "Region", "party_type": "Customer type", "account_status": "Account status",
                   "industry": "Industry", "owns": "Products they have"}
_OPS = ("eq", "ne", "in", "not_in", "gt", "gte", "lt", "lte", "empty", "not_empty", "contains")


def rules_of(db: Session, tenant_id: int, offering_name: str, rule_type: str | None = None, active_only: bool = True) -> list[ProductRule]:
    q = db.query(ProductRule).filter(ProductRule.tenant_id == tenant_id, ProductRule.offering_name.ilike(offering_name or ""))
    if rule_type:
        q = q.filter(ProductRule.rule_type == rule_type)
    if active_only:
        q = q.filter(ProductRule.is_active.is_(True))
    return q.order_by(ProductRule.id).all()


def _show(v: Any) -> str:
    if isinstance(v, list):
        return ", ".join(map(_show, v))
    return f"{v:g}" if isinstance(v, float) else str(v)


_WORDS = {"eq": "is", "ne": "is not", "in": "is one of", "not_in": "is not one of", "gt": "is more than", "gte": "is at least",
          "lt": "is less than", "lte": "is at most", "empty": "is empty", "not_empty": "is set", "contains": "includes"}


def _cond_text(c: dict[str, Any], labels: dict[str, str] | None = None) -> str:
    label = (labels or {}).get(c.get("field"), c.get("field"))
    if c.get("op") in ("empty", "not_empty"):
        return f"{label} {_WORDS[c['op']]}"
    return f"{label} {_WORDS.get(c.get('op'), c.get('op'))} {_show(c.get('value'))}"


def explain(rule_type: str, d: dict[str, Any], product: str) -> str:
    if rule_type == "price":
        from app.crm_core import price_formula

        return price_formula.explain_definition(d)
    if rule_type == "eligibility":
        conds = " and ".join(_cond_text(c, CUSTOMER_FIELDS) for c in d.get("when") or [])
        return f"{product} is only for customers whose {conds}" if d.get("mode", "only") == "only" else f"{product} is not for customers whose {conds}"
    if rule_type == "compatibility":
        where = {"quote": "on the same quote/order", "account": "among what the customer has", "either": "on the quote or among what the customer has"}[d.get("scope", "either")]
        return f"{product} {'requires' if d.get('effect') == 'requires' else 'cannot be combined with'} {d.get('other')} ({where})"
    when = " and ".join(_cond_text(c) for c in d.get("when") or [])
    prefix = f"If {when}: " if when else ""
    if d.get("resource"):
        who = d.get("by") or product
        if d.get("provide") is not None:
            return f"{prefix}{who} provides {d['provide']:g} {d['resource']}"
        return f"{prefix}each {who} uses {d['consume']:g} {d['resource']}"
    if d.get("recommend"):
        return f"{prefix}recommend {d['recommend']}"
    if d.get("say"):
        return f"{prefix}tell the user \"{d['say']}\""
    then = []
    for t in d.get("then") or []:
        if "require" in t:
            then.append(f"{t['require']} is required")
        elif "exclude" in t:
            then.append(f"{t['exclude']} is not allowed")
        else:
            text = _cond_text(t)
            then.append(text.replace(" is not ", " must not be ", 1) if " is not " in text else text.replace(" is ", " must be ", 1))
    when = " and ".join(_cond_text(c) for c in d.get("when") or [])
    return (f"If {when}: " if when else "") + "; ".join(then)


def rule_out(r: ProductRule) -> dict[str, Any]:
    d = config.loads(r.definition_json)
    return {"id": r.id, "brt_id": r.brt_id, "offering_name": r.offering_name, "rule_type": r.rule_type, "source_text": r.source_text,
            "definition": d, "message": r.message, "is_active": r.is_active, "explanation": explain(r.rule_type, d, r.offering_name)}


def _check(rule_type: str, d: dict[str, Any], names: set[str]) -> dict[str, Any]:
    """Only the closed rule shapes get through - whatever an AI returned."""
    def cond(c: Any, fields: set[str] | None) -> dict[str, Any]:
        if not isinstance(c, dict) or c.get("op") not in _OPS or not c.get("field"):
            raise ValueError(f"not a valid condition: {c}")
        if fields is not None and c["field"] not in fields:
            match = next((f for f in fields if _key(f) == _key(c["field"])), None)
            if match is None:
                raise ValueError(f"'{c['field']}' is not one of: {', '.join(sorted(fields))}")
            c = {**c, "field": match}
        out = {"field": c["field"], "op": c["op"]}
        if c["op"] not in ("empty", "not_empty"):
            out["value"] = c.get("value")
        return out

    if rule_type == "eligibility":
        when = [cond(c, set(CUSTOMER_FIELDS) | names) for c in d.get("when") or []]
        if not when:
            raise ValueError("an eligibility rule needs at least one condition on the customer")
        return {"mode": "not" if d.get("mode") == "not" else "only", "when": when}
    if rule_type == "compatibility":
        if d.get("effect") not in ("requires", "excludes") or not d.get("other"):
            raise ValueError("a compatibility rule says the product requires or excludes another product")
        return {"effect": d["effect"], "other": str(d["other"]).strip(), "scope": d.get("scope") if d.get("scope") in ("quote", "account", "either") else "either"}
    when = [cond(c, names) for c in d.get("when") or []]
    if d.get("resource"):
        by = d.get("by")
        if by and not any(_key(n) == _key(by) for n in names):
            raise ValueError(f"'{by}' is not a child product of this product")
        amount = d.get("provide") if d.get("provide") is not None else d.get("consume")
        if amount is None:
            raise ValueError("a resource rule says how much is provided or used")
        resource = re.sub(r"(?<=[^s])s$", "", str(d["resource"]).strip())  # "LAN ports" and "LAN port" are one pool
        out = {"resource": resource, "when": when,
               "by": next((n for n in names if _key(n) == _key(by)), None) if by else None}
        out["provide" if d.get("provide") is not None else "consume"] = float(amount)
        return out
    if d.get("recommend"):
        match = next((n for n in names if _key(n) == _key(d["recommend"])), None)
        if match is None:
            raise ValueError(f"'{d['recommend']}' is not an attribute or child product of this product")
        return {"when": when, "recommend": match}
    if d.get("say"):
        return {"when": when, "say": str(d["say"])[:300]}
    then = []
    for t in d.get("then") or []:
        if isinstance(t, dict) and (t.get("require") or t.get("exclude")):
            target = t.get("require") or t.get("exclude")
            match = next((n for n in names if _key(n) == _key(target)), None)
            if match is None:
                raise ValueError(f"'{target}' is not an attribute or child product of this product")
            then.append({"require" if t.get("require") else "exclude": match})
        else:
            then.append(cond(t, names))
    if not then:
        raise ValueError("a constraint says what is then required, not allowed, or limited")
    return {"when": when, "then": then}


_RULE_PROMPT = """You compile one plain-English product rule (telecom product catalog: eligibility, compatibility and constraint rules) into JSON.
Rule types and shapes:
- eligibility: {"mode": "only"|"not", "when": [cond]} - conditions on the CUSTOMER; fields: segment, region, party_type,
  account_status, industry, owns (products the customer already has), or a customer field listed below.
- compatibility: {"effect": "requires"|"excludes", "other": "<product name>", "scope": "quote"|"account"|"either"}.
- constraint: {"when": [cond], "then": [{"require": name} | {"exclude": name} | cond]} - on the product's own attributes and
  child products (a child product's value is its quantity; 0 = not chosen; a slot's value is the product filling it).
  Also: recommends (soft) {"when": [cond], "recommend": name}; a message {"when": [cond], "say": "text"};
  a resource {"resource": "LAN ports", "by": child product or null for the product itself, "provide": n | "consume": n, "when": [cond]}.
cond = {"field": str, "op": eq|ne|in|not_in|gt|gte|lt|lte|empty|not_empty|contains, "value": ...}.
Answer {"definition": {...}} or {"error": "why"}. Use only names listed."""


def _rules_compile(rule_type: str, text: str, names: list[str], products: list[str]) -> dict[str, Any] | None:
    t = text.strip().rstrip(".")
    low = t.lower()
    lists = lambda s: [p.strip(" .'\"") for p in re.split(r",|\s+or\s+|\s+and\s+|/", s) if p.strip(" .'\"")]  # noqa: E731
    if rule_type == "eligibility":
        when: list[dict[str, Any]] = []
        mode = "not" if re.search(r"\b(not (?:for|available)|never for|excluded? for|except)\b", low) else "only"
        if m := re.search(r"\b(b2b|b2c|business|consumer|enterprise|residential)\b", low):
            seg = {"business": "B2B", "enterprise": "B2B", "consumer": "B2C", "residential": "B2C"}.get(m.group(1), m.group(1).upper())
            when.append({"field": "segment", "op": "eq", "value": seg})
        if m := re.search(r"\b(?:in|from)\s+(?:the\s+)?([A-Z][\w-]*(?:(?:,\s*|\s+or\s+|\s+and\s+)[A-Z][\w-]*)*)(?:\s+regions?)?\b", t):
            regions = [r for r in lists(m.group(1)) if r.lower() not in ("customers", "accounts")]
            if regions:
                when.append({"field": "region", "op": "in", "value": regions})
        if re.search(r"\b(existing )?accounts? only\b|\bonly (?:for )?(?:existing )?accounts\b|\bfor accounts\b", low):
            when.append({"field": "account_status", "op": "eq", "value": "account"})
        if re.search(r"\bprospects?\b", low) and mode == "not":
            when.append({"field": "account_status", "op": "ne", "value": "account"})
        if m := re.search(r"\b(?:who (?:already )?(?:have|has|own)|that (?:already )?(?:have|has|own)|owning|with an? (?:active )?)\s+(.+?)$", t, re.I):
            owned = [p for p in products if _key(p) in _key(m.group(1))]
            if owned:
                when.append({"field": "owns", "op": "contains", "value": owned[0]})
        if m := re.search(r"\b(?:industry|sector) (?:is |of )?(.+)$", t, re.I):
            when.append({"field": "industry", "op": "in", "value": lists(m.group(1))})
        return {"mode": mode, "when": when} if when else None
    if rule_type == "compatibility":
        other = next((p for p in sorted(products, key=len, reverse=True) if _key(p) in _key(re.split(r"\b(requires?|needs?|excludes?|cannot|can't|not with|incompatible)\b", t, flags=re.I)[-1])), None)
        if other is None:
            return None
        effect = "excludes" if re.search(r"\b(exclud|cannot|can't|not with|incompatible|never with|conflicts)", low) else "requires"
        scope = "quote" if re.search(r"same (quote|order)|on the quote|together", low) else "account" if re.search(r"already (has|have|own)|installed|existing", low) else "either"
        return {"effect": effect, "other": other, "scope": scope}
    # constraint
    sorted_names = sorted(names, key=len, reverse=True)

    def find(s: str) -> tuple[str, str] | None:
        s = s.strip()
        for n in sorted_names:
            if _key(s).startswith(_key(n)) and s.lower().startswith(n.lower()[: max(3, len(n) // 2)].lower()):
                return n, s[len(n):].strip() if s.lower().startswith(n.lower()) else re.sub(r"^\S+\s*", "", s)
        return None

    def cond(s: str) -> dict[str, Any] | None:
        found = find(s)
        if not found:
            return None
        name, rest = found
        rest = rest.strip(" ,.")
        for pattern, op in ((r"^(?:is\s+)?(?:chosen|selected|included|present|set)$", "not_empty"), (r"^(?:is\s+)?(?:not chosen|empty|not set|excluded)$", "empty"),
                            (r"^(?:is\s+)?(?:more than|above|over|>)\s*(.+)$", "gt"), (r"^(?:is\s+)?(?:at least|>=|minimum)\s*(.+)$", "gte"),
                            (r"^(?:is\s+)?(?:less than|below|under|<)\s*(.+)$", "lt"), (r"^(?:is\s+)?(?:at most|<=|maximum|up to)\s*(.+)$", "lte"),
                            (r"^(?:is\s+)?not one of\s+(.+)$", "not_in"), (r"^(?:is\s+)?(?:one of|any of|in)\s+(.+)$", "in"),
                            (r"^(?:is not|isn't|!=)\s+(.+)$", "ne"), (r"^(?:is|=|equals)\s+(.+)$", "eq")):
            if m := re.match(pattern, rest, re.I):
                if op in ("empty", "not_empty"):
                    return {"field": name, "op": op}
                value = m.group(1).strip()
                return {"field": name, "op": op, "value": lists(value) if op in ("in", "not_in") else value}
        if not rest:
            return {"field": name, "op": "not_empty"}
        return None

    def then(s: str) -> dict[str, Any] | None:
        s = s.strip(" ,.")
        if m := re.match(r"^(.+?)\s+(?:is|are)\s+(?:required|mandatory|needed|compulsory)$", s, re.I) or re.match(r"^(?:require|include|add)\s+(.+)$", s, re.I):
            f = find(m.group(1))
            return {"require": f[0]} if f else None
        if m := re.match(r"^(.+?)\s+(?:is|are)\s+(?:not allowed|excluded|not available|forbidden)$", s, re.I) or re.match(r"^(?:exclude|no|without)\s+(.+)$", s, re.I):
            f = find(m.group(1))
            return {"exclude": f[0]} if f else None
        # "must not be X" = is not X (before the positive form, or "be" ends up in the value)
        s = re.sub(r"\b(?:must|should|has\s+to|can)\s+not\s+be\b|\b(?:mustn't|shouldn't|cannot|can't)\s+be\b", "is not", s, flags=re.I)
        s = re.sub(r"\b(must|should|has to|can only)\s+(be\s+)?", "is ", s, flags=re.I)
        return cond(s)

    # resources: "CPE provides 4 LAN ports", "each Static IP uses 1 LAN port"
    if m := re.match(r"^(?:each\s+|every\s+)?(.+?)\s+(provides?|gives?|offers?|adds?|uses?|consumes?|takes?|needs?)\s+(\d+(?:\.\d+)?)\s+(.+)$", t, re.I):
        who = m.group(1).strip()
        found = find(who)
        is_root = _key(who) in (_key("the product"), _key("product"), _key("it")) or found is None and _key(who) not in {_key(n) for n in names}
        if found or is_root:
            key = "provide" if re.match(r"provide|give|offer|add", m.group(2), re.I) else "consume"
            resource = re.sub(r"^(?:x\s+)?", "", m.group(4)).strip(" .")
            return {"resource": resource, "by": None if is_root and not found else found[0], key: float(m.group(3)), "when": []}
    if m := re.match(r"^(.+?)\s+recommends?\s+(.+)$", t, re.I):
        a, b = find(m.group(1)), find(m.group(2))
        if a and b:
            return {"when": [{"field": a[0], "op": "not_empty"}], "recommend": b[0]}
    if m := re.match(r"^(?:if|when)\s+(.+?)(?:,\s*|\s+then\s+)(?:recommend|suggest)\s+(.+)$", t, re.I):
        whens = [cond(c) for c in re.split(r"\s+and\s+", m.group(1))]
        target = find(m.group(2))
        if all(whens) and target:
            return {"when": whens, "recommend": target[0]}
    if m := re.match(r"^(?:if|when)\s+(.+?)(?:,\s*|\s+then\s+)(?:say|show|warn|tell(?: them| the user)?)\s+[\'\"]?(.+?)[\'\"]?$", t, re.I):
        whens = [cond(c) for c in re.split(r"\s+and\s+", m.group(1))]
        if all(whens):
            return {"when": whens, "say": m.group(2)}
    if m := re.match(r"^(?:if|when)\s+(.+?)(?:,\s*|\s+then\s+)(.+)$", t, re.I):
        whens = [cond(c) for c in re.split(r"\s+and\s+", m.group(1))]
        thens = [then(c) for c in re.split(r"\s+and\s+|;", m.group(2))]
        if all(whens) and all(thens):
            return {"when": whens, "then": thens}
        return None
    if m := re.match(r"^(.+?)\s+(requires?|needs?|excludes?|cannot be (?:chosen )?with|is not allowed with)\s+(.+)$", t, re.I):
        a, b = find(m.group(1)), find(m.group(3))
        if a and b:
            key = "exclude" if re.match(r"exclud|cannot|is not", m.group(2), re.I) else "require"
            return {"when": [{"field": a[0], "op": "not_empty"}], "then": [{key: b[0]}]}
    found = then(t)
    return {"when": [], "then": [found]} if found else None


def compile_rule(db: Session, tenant_id: int, offering_name: str, rule_type: str, text: str, prefer_rules: bool = False) -> dict[str, Any]:
    """prefer_rules (bulk imports): the built-in reader first, the AI model only for what it can't read -
    a sheet of rules must not spend a model call (seconds each on a CPU-hosted model) per row."""
    from app.crm_core import llm, page_service

    if rule_type not in PRODUCT_RULE_TYPES:
        raise ValueError(f"Rule type is one of {', '.join(PRODUCT_RULE_TYPES)}")
    if rule_type == "price":
        from app.crm_core import price_formula

        out = price_formula.compile_text(db, tenant_id, offering_name, text, use_ai=not prefer_rules)
        return {"definition": {k: out[k] for k in ("expression", "label", "charge")}, "engine": out["engine"], "explanation": out["explanation"]}
    design = config.schema(db, tenant_id, offering_name, workspace=True)
    names = [a["name"] for a in design["attributes"]] + [c["name"] for c in design["components"]]
    customer_fields = [f["key"] for f in page_service.catalog(db, tenant_id, "party") if f["key"] not in CUSTOMER_FIELDS]
    all_products = sorted({o.name for o in db.query(ProductOffering).filter(ProductOffering.tenant_id == tenant_id)})
    allowed = set(names) if rule_type == "constraint" else set(customer_fields)
    definition, engine, problem = None, "rules", ""
    if prefer_rules:
        parsed = _rules_compile(rule_type, text, names, [p for p in all_products if _key(p) != _key(offering_name)])
        if parsed is not None:
            try:
                definition = _check(rule_type, parsed, allowed)
            except ValueError as exc:
                problem = str(exc)
    target = llm.resolve(db, tenant_id) if definition is None else None
    if target is not None:
        try:
            listing = (f"Product: {offering_name}\nAttributes / child products: {', '.join(names) or 'none'}\n"
                       f"Customer fields: {', '.join(list(CUSTOMER_FIELDS) + customer_fields)}\nProducts in the catalog: {', '.join(all_products)}")
            reply = llm.chat_json(target, _RULE_PROMPT, f"{listing}\n\nRule type: {rule_type}\nRule: {text}")
            if reply.get("error"):
                problem = str(reply["error"])
            else:
                definition, engine = _check(rule_type, reply.get("definition") or {}, allowed), "ai"
        except Exception as exc:  # noqa: BLE001 - fall back to the built-in reader
            problem = problem or f"AI unavailable ({type(exc).__name__})"
    if definition is None:
        parsed = _rules_compile(rule_type, text, names, [p for p in all_products if _key(p) != _key(offering_name)])
        if parsed is not None:
            try:
                definition, engine = _check(rule_type, parsed, allowed), "rules"
            except ValueError as exc:
                problem = str(exc)
    if definition is None:
        examples = {
            "eligibility": "\"only for B2B customers in North or South\", \"not for prospects\", \"only for customers who already have Fiber 800\"",
            "compatibility": "\"requires Static IP Add-on\", \"cannot be combined with Home Fiber 100\"",
            "constraint": "\"if Access Medium is DSL then Bandwidth must be at most 2048\", \"CPE requires CPE Type\", \"CPE recommends BBI-NRC\", "
                          "\"CPE provides 4 LAN ports\", \"each Static IP uses 1 LAN port\", \"if Access Medium is DSL then say 'DSL speeds depend on line length'\"",
        }[rule_type]
        raise ValueError("I couldn't turn that into a rule" + (f" ({problem})" if problem else "") + f". Try e.g. {examples}")
    return {"definition": definition, "engine": engine, "explanation": explain(rule_type, definition, offering_name)}


def save_rule(db: Session, tenant_id: int, offering_name: str, rule_type: str, text: str, message: str = "", rule_id: int | None = None,
              prefer_rules: bool = False) -> ProductRule:
    compiled = compile_rule(db, tenant_id, offering_name, rule_type, text, prefer_rules)
    if rule_type == "price":
        from app.crm_core import price_formula

        d = compiled["definition"]
        return price_formula.save(db, tenant_id, offering_name, d["expression"], message or d["label"], d["charge"], text, rule_id)
    row = db.get(ProductRule, rule_id) if rule_id else None
    if row is None or row.tenant_id != tenant_id:
        row = ProductRule(tenant_id=tenant_id, offering_name=offering_name, rule_type=rule_type)
        db.add(row)
    row.rule_type, row.source_text, row.message = rule_type, text, (message or "")[:300]
    row.definition_json = json.dumps(compiled["definition"])
    db.flush()
    return row


# ---------------------------------------------------------------- checking a customer against the rules


def owned_products(db: Session, tenant_id: int, party: Party) -> list[str]:
    from app.models.orders import Order, OrderStatus

    names: list[str] = []
    from app.crm_core import abo_service
    from app.models.fulfillment import AssetStatus
    names.extend(dict.fromkeys(a.asset_type for a in abo_service.customer_assets(db, tenant_id, party.id) if a.status != AssetStatus.decommissioned))
    for order in db.query(Order).filter(Order.tenant_id == tenant_id, Order.party_id == party.id,
                                        Order.status.in_([OrderStatus.active, OrderStatus.changed, OrderStatus.activating])):
        for item in order.items:
            offering = db.get(ProductOffering, item.product_offering_id)
            if offering is not None and offering.name not in names:
                names.append(offering.name)
    return names


def customer_facts(db: Session, tenant_id: int, party: Party) -> dict[str, Any]:
    from app.crm_core import page_service, process_service

    facts = page_service.record_values(db, tenant_id, "party", party)
    lead = db.query(Lead).filter(Lead.party_id == party.id).order_by(Lead.id.desc()).first()
    facts.update({
        "segment": process_service.party_segment(party), "region": lead.region if lead else None, "party_type": party.party_type,
        "account_status": process_service.account_status(party) or ("account" if party.party_type != "prospect" else "prospect"),
        "industry": facts.get("industry"), "owns": owned_products(db, tenant_id, party),
    })
    return facts


def _test(c: dict[str, Any], facts: dict[str, Any]) -> bool:
    actual = facts.get(c["field"])
    op, expected = c["op"], c.get("value")
    if op == "empty":
        return actual in (None, "", [])
    if op == "not_empty":
        return actual not in (None, "", [])
    if isinstance(actual, list):
        hit = any(_key(a) == _key(e) for a in actual for e in (expected if isinstance(expected, list) else [expected]))
        return hit if op in ("contains", "eq", "in") else not hit
    if actual in (None, ""):
        return op in ("ne", "not_in")
    if op in ("in", "not_in"):
        inside = _key(actual) in {_key(o) for o in (expected if isinstance(expected, list) else str(expected).split(","))}
        return inside == (op == "in")
    if op in ("gt", "gte", "lt", "lte"):
        a, b = config._num(actual), config._num(expected)
        return a is not None and b is not None and {"gt": a > b, "gte": a >= b, "lt": a < b, "lte": a <= b}[op]
    if op == "contains":
        return _key(expected) in _key(actual)
    same = _key(actual) == _key(expected) or (c["field"] == "segment" and str(actual).upper().startswith(str(expected).upper()))
    return same if op == "eq" else not same


def eligibility_errors(db: Session, tenant_id: int, offering_name: str, party: Party, facts: dict[str, Any] | None = None) -> list[str]:
    rules = rules_of(db, tenant_id, offering_name, "eligibility")
    if not rules:
        return []
    facts = facts or customer_facts(db, tenant_id, party)
    errors = []
    for r in rules:
        d = config.loads(r.definition_json)
        matches = all(_test(c, facts) for c in d.get("when") or [])
        if (d.get("mode", "only") == "only" and not matches) or (d.get("mode") == "not" and matches):
            errors.append(r.message or f"{party.name} is not eligible for {offering_name}: {explain('eligibility', d, offering_name)}")
    return errors


def compatibility_errors(db: Session, tenant_id: int, offering_name: str, party: Party | None, others: list[str]) -> list[str]:
    rules = rules_of(db, tenant_id, offering_name, "compatibility")
    # rules of the other products that exclude this one count too (excludes is mutual)
    for other in others + (owned_products(db, tenant_id, party) if party is not None else []):
        rules += [r for r in rules_of(db, tenant_id, other, "compatibility")
                  if config.loads(r.definition_json).get("effect") == "excludes" and _key(config.loads(r.definition_json).get("other")) == _key(offering_name)]
    if not rules:
        return []
    owned = owned_products(db, tenant_id, party) if party is not None else []
    errors = []
    for r in rules:
        d = config.loads(r.definition_json)
        mine = _key(r.offering_name) == _key(offering_name)
        other = d.get("other") if mine else r.offering_name
        scope = d.get("scope", "either")
        pool = (others if scope in ("quote", "either") else []) + (owned if scope in ("account", "either") else [])
        present = any(_key(p) == _key(other) for p in pool)
        if d.get("effect") == "requires" and mine and not present:
            errors.append(r.message or f"{offering_name} needs {other} ({'on the same quote' if scope == 'quote' else 'already installed or on the quote' if scope == 'either' else 'already installed'})")
        elif d.get("effect") == "excludes" and present:
            errors.append(r.message or f"{offering_name} can't be combined with {other}")
    return list(dict.fromkeys(errors))


def customer_rule_errors(db: Session, tenant_id: int, offering_name: str, party: Party, others: list[str] | None = None) -> list[str]:
    return eligibility_errors(db, tenant_id, offering_name, party) + compatibility_errors(db, tenant_id, offering_name, party, others or [])


def eligible(db: Session, tenant_id: int, names: list[str], party: Party | None) -> tuple[list[str], dict[str, str]]:
    """(the products this customer may buy, why the others are out)."""
    if party is None:
        return names, {}
    facts = None
    ok, why = [], {}
    for name in names:
        if not rules_of(db, tenant_id, name, "eligibility"):
            ok.append(name)
            continue
        facts = facts or customer_facts(db, tenant_id, party)
        problems = eligibility_errors(db, tenant_id, name, party, facts)
        if problems:
            why[name] = problems[0]
        else:
            ok.append(name)
    return ok, why

"""BIM - BRT Integration Manager (models/bim.py): loading data into BRT 360
in batches (CSV / JSON) or record by record over the API (EAI).

The rules every load follows:
- Every record carries a BRT_ID that BRT 360 issued for that entity
  (brt_id.issued: right prefix, BRT 360's check character, and a number
  the register actually handed out). Loaders get them in advance from
  reserve_ids - no BRT_ID, no load.
- A known BRT_ID updates that record (interface mode "upsert") or is
  refused ("insert"); a BRT_ID of another tenant's record is refused.
- References are by BRT_ID too (a lead's party_brt_id), and may point at
  records loaded earlier in the same batch.
- Each row is loaded on its own (a savepoint): a bad row is reported and
  skipped, never half-written; a dry run checks everything and keeps
  nothing.
- Prospects and leads also pass their Page Designer page (mandatory
  fields, field types, validation rules) unless the load says otherwise.
"""

from __future__ import annotations

import csv
import io
import json
from dataclasses import dataclass, field
from typing import Any, Callable

from sqlalchemy.orm import Session

from app import brt_id
from app.crm_core import custom_field_service, page_service, process_service
from app.models.auth import User
from app.models.bim import BimInterface, BimJob
from app.models.catalog import OfferingStatus, ProductOffering
from app.models.crm import Lead, LeadStatus, Opportunity, OpportunityStage, Party
from app.models.pages import LeadProduct
from app.timeutils import utcnow

MAX_ROWS = 20000
MAX_RESERVE = 10000


def _str(v: Any) -> str | None:
    return None if v is None or str(v).strip() == "" else str(v).strip()


def _float(v: Any) -> float | None:
    s = _str(v)
    return None if s is None else float(s.replace(",", "").replace("₹", ""))


def _int(v: Any) -> int | None:
    f = _float(v)
    return None if f is None else int(f)


@dataclass
class Spec:
    label: str
    model: Any
    entity_type: str | None  # for custom fields / pages
    fields: dict[str, tuple[Callable[[Any], Any], bool, str]]  # name -> (caster, required, help)
    refs: dict[str, tuple[Any, str]] = field(default_factory=dict)  # "<x>_brt_id" -> (model, attribute it sets)
    extra: dict[str, str] = field(default_factory=dict)  # loaded by a hook, not a column

    @property
    def table(self) -> str:
        return self.model.__tablename__


def _enum(enum_cls) -> Callable[[Any], Any]:
    def cast(v: Any):
        s = _str(v)
        if s is None:
            return None
        try:
            return enum_cls(s.lower().replace(" ", "_"))
        except ValueError:
            raise ValueError(f"must be one of {', '.join(e.value for e in enum_cls)}")
    return cast


ENTITIES: dict[str, Spec] = {
    "prospect": Spec(
        "Prospect / customer", Party, "party",
        {"name": (_str, True, "Company or person name"), "email": (_str, False, ""), "phone": (_str, False, ""),
         "party_type": (_str, False, "prospect (default) / organization / individual")},
        extra={"segment": "B2B or B2C", "industry": "Industry (B2B)", "account_status": "prospect / account"},
    ),
    "lead": Spec(
        "Lead", Lead, "lead",
        {"source": (_str, True, "referral, website, event ..."), "region": (_str, False, "default ALL"),
         "owner_name": (_str, False, "default: the region's owner"), "propensity_score": (_float, False, "0-1, default 0.5"),
         "status": (_enum(LeadStatus), False, "pending_qualification (default) / qualified / rejected"),
         "interested_offering_name": (_str, False, "main product of interest")},
        refs={"party_brt_id": (Party, "party_id")},
        extra={"products": "Interested products, e.g. 'Fiber 800 x 2, VOIP'"},
    ),
    "opportunity": Spec(
        "Opportunity", Opportunity, "opportunity",
        {"name": (_str, True, ""), "stage": (_enum(OpportunityStage), False, "draft (default) ..."),
         "offering_name": (_str, False, ""), "quantity": (_int, False, "default 1")},
        refs={"lead_brt_id": (Lead, "lead_id")},
    ),
    "product": Spec(
        "Product offering", ProductOffering, None,
        {"name": (_str, True, ""), "segment": (_str, True, "B2B, B2C, B2B-SMB, B2B-Enterprise"), "price": (_float, True, "monthly price"),
         "price_floor": (_float, False, "default 0"), "status": (_enum(OfferingStatus), False, "draft (default) / active / retired")},
    ),
}


def entities() -> list[dict[str, Any]]:
    out = []
    for key, spec in ENTITIES.items():
        columns = [{"name": "brt_id", "required": True, "help": f"BRT_ID issued for {spec.label.lower()} ({brt_id.prefix_for(spec.table)}-...)"}]
        columns += [{"name": name, "required": req, "help": help_} for name, (_, req, help_) in spec.fields.items()]
        columns += [{"name": ref, "required": True, "help": f"BRT_ID of the {ref.removesuffix('_brt_id')}"} for ref in spec.refs]
        columns += [{"name": name, "required": False, "help": help_} for name, help_ in spec.extra.items()]
        columns.append({"name": "owner_brt_id", "required": False, "help": "BRT_ID of the user who owns the record (visibility)"})
        out.append({"key": key, "label": spec.label, "prefix": brt_id.prefix_for(spec.table), "columns": columns,
                    "custom_fields": bool(spec.entity_type)})
    return out


def spec_of(entity: str) -> Spec:
    if entity not in ENTITIES:
        raise ValueError(f"BIM loads: {', '.join(ENTITIES)}")
    return ENTITIES[entity]


def template_columns(db: Session, tenant_id: int, entity: str) -> list[str]:
    spec = spec_of(entity)
    columns = ["brt_id", *spec.fields, *spec.refs, *spec.extra, "owner_brt_id"]
    if spec.entity_type:
        columns += [d.key for d in custom_field_service.list_definitions(db, tenant_id, spec.entity_type) if d.key not in columns]
    return columns


def reserve_ids(db: Session, entity: str, count: int) -> list[str]:
    if not 0 < count <= MAX_RESERVE:
        raise ValueError(f"Reserve between 1 and {MAX_RESERVE} BRT_IDs at a time")
    return brt_id.mint(db.connection(), spec_of(entity).table, count)


def parse(content: str, fmt: str) -> list[dict[str, Any]]:
    fmt = (fmt or "csv").lower()
    if fmt == "json":
        data = json.loads(content)
        rows = data.get("records") if isinstance(data, dict) else data
        if not isinstance(rows, list) or not all(isinstance(r, dict) for r in rows):
            raise ValueError("JSON must be a list of records (or {\"records\": [...]})")
        return rows
    reader = csv.DictReader(io.StringIO(content.lstrip("﻿")))
    return [{(k or "").strip(): v for k, v in row.items() if k} for row in reader]


def _mapped(row: dict[str, Any], mapping: dict[str, str]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k, v in row.items():
        target = mapping.get(k) or mapping.get(str(k).strip()) or str(k).strip()
        out[target.strip().lower().replace(" ", "_")] = v
    return out


def _find(db: Session, model, code: str):
    return db.query(model).filter(model.brt_id == code).first()


def _products(raw: Any) -> list[tuple[str, int]]:
    out = []
    for part in str(raw or "").split(","):
        part = part.strip()
        if not part:
            continue
        name, _, qty = part.rpartition(" x ")
        if name and qty.strip().isdigit():
            out.append((name.strip(), int(qty)))
        else:
            out.append((part, 1))
    return out


def load_row(db: Session, tenant_id: int, spec: Spec, row: dict[str, Any], mode: str, apply_pages: bool, actor_id: int | None) -> tuple[str, list[str]]:
    """'inserted' / 'updated' and no errors - or the errors."""
    errors: list[str] = []
    code = _str(row.get("brt_id"))
    if code is None:
        return "failed", ["brt_id is missing - every record loaded into BRT 360 needs its BRT_ID"]
    code = code.upper()
    if not brt_id.issued(db.connection(), code, spec.table):
        return "failed", [f"{code} is not a BRT_ID BRT 360 issued for {spec.label.lower()} records "
                          f"({brt_id.prefix_for(spec.table)}-...) - reserve them in BIM first"]
    obj = _find(db, spec.model, code)
    if obj is not None and getattr(obj, "tenant_id", tenant_id) != tenant_id:
        return "failed", [f"{code} belongs to another organization"]
    if obj is not None and mode == "insert":
        return "failed", [f"{code} is already loaded (this interface only inserts)"]

    values: dict[str, Any] = {}
    for name, (caster, required, _) in spec.fields.items():
        if name not in row and obj is not None:
            continue
        try:
            value = caster(row.get(name))
        except (ValueError, TypeError) as exc:
            errors.append(f"{name}: {exc if str(exc).startswith('must') else 'is not valid'} ('{row.get(name)}')")
            continue
        if value is None and required and (obj is None or name in row):
            errors.append(f"{name} is required")
        elif value is not None or name in row:
            values[name] = value
    for ref, (model, attr) in spec.refs.items():
        ref_code = _str(row.get(ref))
        if ref_code is None:
            if obj is None:
                errors.append(f"{ref} is required")
            continue
        target = _find(db, model, ref_code.upper())
        if target is None or getattr(target, "tenant_id", tenant_id) != tenant_id:
            errors.append(f"{ref} {ref_code} is not a {model.__tablename__.rstrip('s')} of this organization")
        else:
            values[attr] = target.id
    owner_id = actor_id
    if _str(row.get("owner_brt_id")):
        owner = _find(db, User, _str(row["owner_brt_id"]).upper())
        if owner is None or owner.tenant_id != tenant_id:
            errors.append(f"owner_brt_id {row['owner_brt_id']} is not a user of this organization")
        else:
            owner_id = owner.id
    known_columns = {"brt_id", "owner_brt_id", *spec.fields, *spec.refs, *spec.extra}
    custom = {k: v for k, v in row.items() if k not in known_columns}
    if spec.entity_type:
        defs = {d.key: d for d in custom_field_service.list_definitions(db, tenant_id, spec.entity_type)}
        unknown = [k for k in custom if k not in defs]
        if unknown:
            errors.append(f"unknown column(s): {', '.join(unknown)} - add them as fields in the Page Designer or map them")
        segment = page_service.segment_of(row.get("segment")) if spec.entity_type == "party" else None
        if spec.entity_type == "lead" and "party_id" in values:
            segment = process_service.party_segment(db.get(Party, values["party_id"]))
        if apply_pages and spec.entity_type in ("party", "lead") and not errors:
            page_values = {**{k: v for k, v in row.items() if k in defs or k in ("segment", "industry")}, **values}
            if obj is not None:
                page_values = {**page_service.record_values(db, tenant_id, spec.entity_type, obj), **page_values}
            errors += page_service.validate(db, tenant_id, spec.entity_type, segment, page_values)
    else:
        unknown = [k for k in custom if _str(custom[k]) is not None]
        if unknown:
            errors.append(f"unknown column(s): {', '.join(unknown)}")
    if errors:
        return "failed", errors

    status = "updated" if obj is not None else "inserted"
    if obj is None:
        defaults: dict[str, Any] = {"tenant_id": tenant_id}
        if spec.model is Party:
            defaults["party_type"] = "prospect"
        if spec.model is Lead:
            defaults.update({"region": "ALL", "propensity_score": 0.5, "owner_name": "Unassigned"})
        if spec.model is ProductOffering:
            defaults.update({"status": OfferingStatus.draft, "price_floor": 0.0})
        obj = spec.model(**{**defaults, **{k: v for k, v in values.items() if v is not None}}, brt_id=code)
        obj.sys_created_by = owner_id
        db.add(obj)
    else:
        for k, v in values.items():
            if v is not None or k not in ("name", "source", "segment", "price"):
                setattr(obj, k, v)
    db.flush()
    if spec.model is Party:
        page_service.save_values(db, tenant_id, "party", obj, {k: row[k] for k in ("segment", "industry") if k in row})
        if _str(row.get("account_status")):
            process_service.party_profile(obj, create=True).account_status = _str(row["account_status"]).lower()
    if spec.model is Lead and _str(row.get("products")):
        db.query(LeadProduct).filter(LeadProduct.lead_id == obj.id).delete()
        for position, (name, qty) in enumerate(_products(row["products"])):
            db.add(LeadProduct(lead_id=obj.id, offering_name=name, quantity=qty, position=position))
        if not obj.interested_offering_name:
            obj.interested_offering_name = _products(row["products"])[0][0]
    if spec.entity_type:
        defs = {d.key: d for d in custom_field_service.list_definitions(db, tenant_id, spec.entity_type)}
        for k, v in custom.items():
            if k in defs and (_str(v) is not None or status == "updated"):
                custom_field_service.set_value(db, tenant_id, spec.entity_type, obj.id, defs[k], _str(v))
    if spec.entity_type:
        _record_owner(db, tenant_id, spec.entity_type, obj.id, owner_id)
    db.flush()
    return status, []


def _record_owner(db: Session, tenant_id: int, entity_type: str, entity_id: int, user_id: int | None) -> None:
    """The record's owner for visibility (same data intents keep)."""
    from app.crm_core import lifecycle_service

    if user_id is None or entity_type not in ("party", "lead", "opportunity", "quote", "order"):
        return
    meta = lifecycle_service.get_meta(db, tenant_id, entity_type, entity_id, create=True)
    if meta.created_by_user_id is None:
        meta.created_by_user_id = user_id
    if meta.owner_user_id is None:
        meta.owner_user_id = user_id


def run(db: Session, tenant_id: int, entity: str, rows: list[dict[str, Any]], channel: str = "batch", interface: BimInterface | None = None,
        file_name: str | None = None, dry_run: bool = False, apply_pages: bool = True, actor_id: int | None = None) -> tuple[BimJob, list[dict[str, Any]]]:
    spec = spec_of(entity)
    if len(rows) > MAX_ROWS:
        raise ValueError(f"At most {MAX_ROWS} records per load - split the file")
    mapping = json.loads(interface.mapping_json or "{}") if interface else {}
    mode = interface.mode if interface else "upsert"
    job = BimJob(tenant_id=tenant_id, interface_id=interface.id if interface else None, entity=entity, channel=channel,
                 file_name=file_name, dry_run=dry_run, total=len(rows))
    db.add(job)
    db.flush()
    results: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    counts = {"inserted": 0, "updated": 0, "failed": 0}
    outer = db.begin_nested() if dry_run else None
    for n, raw in enumerate(rows, start=1):
        row = _mapped(raw, mapping)
        savepoint = db.begin_nested()
        try:
            status, problems = load_row(db, tenant_id, spec, row, mode, apply_pages, actor_id)
        except Exception as exc:  # noqa: BLE001 - one bad row never stops the load
            status, problems = "failed", [f"could not be loaded: {exc}"]
        if status == "failed":
            savepoint.rollback()
            errors.append({"row": n, "brt_id": _str(row.get("brt_id")), "errors": problems})
        else:
            savepoint.commit()
        counts[status] += 1
        results.append({"row": n, "brt_id": _str(row.get("brt_id")), "status": status, "errors": problems})
    if outer is not None:
        outer.rollback()  # a dry run keeps only the job's report
    job.inserted, job.updated, job.failed = counts["inserted"], counts["updated"], counts["failed"]
    job.errors_json = json.dumps(errors[:1000])
    job.status = "completed" if not errors else ("failed" if len(errors) == len(rows) else "completed_with_errors")
    job.finished_at = utcnow()
    db.flush()
    return job, results


def job_out(job: BimJob) -> dict[str, Any]:
    return {
        "id": job.id, "brt_id": job.brt_id, "entity": job.entity, "channel": job.channel, "file_name": job.file_name,
        "status": job.status, "dry_run": job.dry_run, "total": job.total, "inserted": job.inserted, "updated": job.updated,
        "failed": job.failed, "errors": json.loads(job.errors_json or "[]"), "started_at": job.started_at, "finished_at": job.finished_at,
        "interface_id": job.interface_id, "started_by": job.sys_created_by,
    }


def interface_out(row: BimInterface) -> dict[str, Any]:
    return {"id": row.id, "brt_id": row.brt_id, "code": row.code, "name": row.name, "entity": row.entity, "mode": row.mode,
            "mapping": json.loads(row.mapping_json or "{}"), "description": row.description, "is_active": row.is_active}


def record_by_brt_id(db: Session, tenant_id: int, code: str) -> dict[str, Any] | None:
    """Any loadable record, by its BRT_ID (EAI read-back)."""
    code = code.strip().upper()
    for key, spec in ENTITIES.items():
        if not code.startswith(brt_id.prefix_for(spec.table) + "-"):
            continue
        obj = _find(db, spec.model, code)
        if obj is None or getattr(obj, "tenant_id", tenant_id) != tenant_id:
            return None
        data: dict[str, Any] = {"brt_id": obj.brt_id, "entity": key}
        for name in spec.fields:
            value = getattr(obj, name, None)
            data[name] = getattr(value, "value", value)
        for ref, (model, attr) in spec.refs.items():
            target = db.get(model, getattr(obj, attr))
            data[ref] = target.brt_id if target else None
        if spec.entity_type:
            data.update(page_service.record_values(db, tenant_id, spec.entity_type, obj) if spec.entity_type in page_service.CORE_FIELDS
                        else custom_field_service.get_values(db, tenant_id, spec.entity_type, obj.id))
        if spec.model is Lead:
            data["products"] = ", ".join(f"{p.offering_name} x {p.quantity}" for p in
                                         db.query(LeadProduct).filter(LeadProduct.lead_id == obj.id).order_by(LeadProduct.position))
        data["sys_created_at"] = obj.sys_created_at
        data["sys_updated_at"] = obj.sys_updated_at
        return data
    return None
